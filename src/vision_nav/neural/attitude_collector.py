"""Bounded, source-bound attitude context; not synchronized inertial navigation."""

from collections import deque
import math
import threading
import time
from typing import Callable, Protocol

from ..mavlink_bridge import MavlinkTelemetrySample


class AttitudeLink(Protocol):
    def connect(self) -> None: ...
    def try_read_telemetry(self, timeout_s: float) -> MavlinkTelemetrySample | None: ...
    def close(self) -> None: ...


class MavlinkAttitudeCollector:
    """Own link creation/read/close on one thread and expose discontinuities.

    Only host receipt time is used for the provisional temporal-model context.
    The original PX4 boot time is retained for ordering diagnostics, not silently
    converted into exposure time. This worker never calls a transmit method.
    """

    def __init__(self, factory: Callable[[], AttitudeLink], *, target_system: int = 1,
                 target_component: int = 1, capacity: int = 512, stale_after_ms: float = 500.0,
                 clock_ns: Callable[[], int] = time.monotonic_ns):
        for value in (target_system, target_component):
            if type(value) is not int or not 1 <= value <= 255:
                raise ValueError("Expected MAVLink sender IDs must be integers from 1 to 255")
        if type(capacity) is not int or not 1 <= capacity <= 4096:
            raise ValueError("Attitude capacity must be an integer from 1 to 4096")
        if type(stale_after_ms) not in (int, float) or not math.isfinite(stale_after_ms) or stale_after_ms <= 0:
            raise ValueError("Attitude freshness limit must be finite and positive")
        self._factory, self._clock_ns = factory, clock_ns
        self._target = (target_system, target_component)
        self._capacity, self._stale_ms = capacity, stale_after_ms
        self._samples = deque(maxlen=capacity)
        self._condition = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._state, self._error = "not_started", None
        self._received = self._accepted = self._filtered = self._invalid = self._overflow = 0
        self._generation = 0
        self._last_reason = None
        self._last_host_ns = self._last_vehicle_us = None
        self._latest = None

    def start(self) -> None:
        with self._condition:
            if self._thread is not None or self._stop.is_set():
                raise RuntimeError("Attitude collector cannot be restarted")
            self._state = "starting"
            self._thread = threading.Thread(target=self._run, name="vision-nav-attitude", daemon=True)
            self._thread.start()

    def stop(self, *, timeout_s: float = 1.0) -> bool:
        if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or timeout_s < 0:
            raise ValueError("Shutdown wait must be finite and nonnegative")
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout_s)
        with self._condition:
            alive = bool(self._thread and self._thread.is_alive())
            if alive:
                self._state = "shutdown_timeout"
            elif self._thread is None:
                self._state = "stopped"
            return not alive

    def status(self) -> dict:
        with self._condition:
            return self._status_locked()

    def drain(self) -> dict:
        """Take a batch and its generation/health atomically; discard unhealthy data."""
        with self._condition:
            health = self._status_locked()
            samples = list(self._samples) if health["status"] == "receiving" else []
            self._samples.clear()
            return {"samples": samples, "health": health}

    def _status_locked(self) -> dict:
        now = self._clock_ns()
        age_ms = None if self._last_host_ns is None else (now - self._last_host_ns) / 1e6
        health = self._state
        if self._state == "running":
            if self._last_host_ns is None:
                health = "waiting"
            elif self._latest is None:
                health = "invalid"
            elif age_ms < 0 or age_ms > self._stale_ms:
                health = "stale"
            else:
                health = "receiving"
        return {"schema_version": "vision_nav_attitude_context_v1", "status": health,
                "worker_state": self._state, "error": self._error,
                "target_system": self._target[0], "target_component": self._target[1],
                "received_attitude_count": self._received, "accepted_count": self._accepted,
                "filtered_source_count": self._filtered, "invalid_count": self._invalid,
                "overflow_count": self._overflow, "generation": self._generation,
                "last_discontinuity_reason": self._last_reason,
                "pending_count": len(self._samples), "capacity": self._capacity,
                "last_attitude_age_ms": age_ms, "stale_after_ms": self._stale_ms,
                "latest_attitude": dict(self._latest) if self._latest is not None else None,
                "thread_alive": bool(self._thread and self._thread.is_alive()),
                "time_basis": "host_receive_unqualified", "sensor_timing_calibrated": False,
                "position_authority": False, "mavlink_transmission_enabled": False}

    def _break_context(self, reason: str) -> None:
        self._generation += 1
        self._last_reason = reason
        self._samples.clear()
        self._latest = None

    def _record(self, sample: MavlinkTelemetrySample, host_ns: int) -> None:
        with self._condition:
            self._received += 1
            if (type(sample.source_system) is not int or type(sample.source_component) is not int
                    or (sample.source_system, sample.source_component) != self._target):
                self._filtered += 1
                return
            values = (sample.roll_rad, sample.pitch_rad, sample.yaw_rad)
            if (any(type(value) not in (int, float) or not math.isfinite(value) for value in values)
                    or type(host_ns) is not int or host_ns <= 0
                    or type(sample.vehicle_timestamp_us) is not int or sample.vehicle_timestamp_us < 0
                    or sample.vehicle_time_domain != "px4_boot_us"):
                self._invalid += 1
                self._break_context("invalid_attitude_or_clock_metadata")
                return
            if (self._last_host_ns is not None and host_ns <= self._last_host_ns
                    or self._last_vehicle_us is not None and sample.vehicle_timestamp_us <= self._last_vehicle_us):
                self._invalid += 1
                self._break_context("nonmonotonic_attitude_timestamp_restart_required_if_vehicle_rebooted")
                return
            if self._last_host_ns is not None and host_ns - self._last_host_ns > self._stale_ms * 1e6:
                self._break_context("attitude_reception_gap")
            if len(self._samples) == self._capacity:
                self._overflow += 1
                # Evict one oldest sample, but explicitly break temporal support.
                self._generation += 1
                self._last_reason = "attitude_buffer_overflow"
            self._last_host_ns, self._last_vehicle_us = host_ns, sample.vehicle_timestamp_us
            record = {"host_monotonic_ns": host_ns, "roll_rad": float(sample.roll_rad),
                      "pitch_rad": float(sample.pitch_rad), "yaw_rad": float(sample.yaw_rad)}
            self._samples.append(record)
            self._latest = {**record, "vehicle_timestamp_us": sample.vehicle_timestamp_us,
                            "vehicle_time_domain": sample.vehicle_time_domain,
                            "source_system": sample.source_system, "source_component": sample.source_component}
            self._accepted += 1

    def _run(self) -> None:
        bridge = None
        try:
            bridge = self._factory()
            bridge.connect()
            with self._condition:
                self._state = "running"
            while not self._stop.is_set():
                sample = bridge.try_read_telemetry(timeout_s=0.05)
                if sample is None:
                    self._stop.wait(0.001)
                elif sample.message_type == "ATTITUDE":
                    self._record(sample, self._clock_ns())
        except Exception as exc:
            with self._condition:
                self._error = f"{type(exc).__name__}:{exc}"
                self._break_context("attitude_reader_failed")
        finally:
            if bridge is not None:
                try:
                    bridge.close()
                except Exception as exc:
                    with self._condition:
                        self._error = self._error or f"close_failed:{type(exc).__name__}:{exc}"
            with self._condition:
                self._samples.clear()
                self._state = "failed" if self._error else "stopped"
                self._stop.set()
