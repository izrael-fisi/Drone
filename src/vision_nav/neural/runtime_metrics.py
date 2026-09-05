"""Opt-in, bounded live-load evidence from the actual shadow runtime process."""

from copy import deepcopy
from datetime import UTC, datetime
import json
import math
import os
from pathlib import Path
import threading
import time
from typing import Callable
from uuid import UUID

from .hardware_resources import hardware_identity, process_io_counters, resource_snapshot


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


class RuntimeMetricsRecorder:
    """Measure in a separate thread; retain one sample in memory and bounded JSONL.

    File writes and vcgencmd may block this recorder, never the camera/attitude
    readers or heartbeat. Sample age and recorder errors remain visible. There
    are no control actions or automatic performance acceptance decisions.
    """

    def __init__(self, output_dir: Path, *, run_id: str, activity: Callable[[], dict],
                 interval_s: float = 1.0, max_samples: int = 3600,
                 hardware: dict | None = None, probe: Callable[[], dict] | None = None,
                 native_vio_probe: Callable[[], dict] | None = None,
                 io_probe: Callable[[], dict] = process_io_counters,
                 clock_ns: Callable[[], int] = time.monotonic_ns,
                 cpu_clock_ns: Callable[[], int] = time.process_time_ns):
        if str(UUID(run_id)) != run_id:
            raise ValueError("Metrics run ID must be a canonical UUID")
        if type(interval_s) not in (int, float) or not math.isfinite(interval_s) or interval_s <= 0:
            raise ValueError("Metrics interval must be finite and positive")
        if type(max_samples) is not int or not 1 <= max_samples <= 36000:
            raise ValueError("Metrics sample count must be from 1 to 36000")
        self.run_id = run_id
        self.path = output_dir / f"runtime_metrics-{run_id}.jsonl"
        self._activity, self._interval, self._max_samples = activity, interval_s, max_samples
        self._hardware = deepcopy(hardware if hardware is not None else hardware_identity())
        self._probe = probe or (lambda: resource_snapshot(bool(self._hardware.get("is_raspberry_pi"))))
        self._io_probe, self._clock, self._cpu_clock = io_probe, clock_ns, cpu_clock_ns
        self._native_vio_probe = native_vio_probe
        self._lock, self._stop = threading.Lock(), threading.Event()
        self._thread = None
        self._state, self._error, self._latest = "not_started", None, None
        self._count = 0
        self._native_available_count = 0
        self._previous = None

    def start(self) -> None:
        with self._lock:
            if self._thread is not None or self._stop.is_set():
                raise RuntimeError("Metrics recorder cannot be restarted")
            self._state = "starting"
            self._thread = threading.Thread(target=self._run, name="vision-nav-metrics", daemon=True)
            self._thread.start()

    def status(self) -> dict:
        with self._lock:
            latest = deepcopy(self._latest)
            return {"status": self._state, "error": self._error, "sample_count": self._count,
                    "native_vio_available_sample_count": self._native_available_count,
                    "max_samples": self._max_samples, "interval_s": self._interval,
                    "path": str(self.path), "latest": latest,
                    "sample_age_ms": (self._clock() - latest["host_monotonic_ns"]) / 1e6 if latest else None,
                    "thread_alive": bool(self._thread and self._thread.is_alive()),
                    "position_authority": False, "hardware": deepcopy(self._hardware)}

    def stop(self, *, timeout_s: float = 2.0) -> bool:
        if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or timeout_s < 0:
            raise ValueError("Metrics shutdown timeout must be finite and nonnegative")
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout_s)
        with self._lock:
            alive = bool(self._thread and self._thread.is_alive())
            if alive:
                self._state = "shutdown_timeout"
            elif self._thread is None:
                self._state = "stopped"
            return not alive

    def _sample(self) -> dict:
        started = self._clock()
        resources = deepcopy(self._probe())
        io = deepcopy(self._io_probe())
        native_vio = (deepcopy(self._native_vio_probe()) if self._native_vio_probe else
                      {"status": "not_configured", "position_authority": False})
        cpu_ns, now = self._cpu_clock(), self._clock()
        cpu_percent = elapsed_ms = None
        io_delta = None
        interval_reason = "first_sample"
        if self._previous is not None:
            old_time, old_cpu, old_io = self._previous
            if now <= old_time or cpu_ns < old_cpu:
                interval_reason = "nonmonotonic_resource_counter"
            else:
                elapsed_ms = (now - old_time) / 1e6
                cpu_percent = 100 * (cpu_ns - old_cpu) / (now - old_time)
                interval_reason = None
                if io.get("status") == old_io.get("status") == "available":
                    keys = ("read_bytes", "write_bytes", "cancelled_write_bytes")
                    if all(type(io.get(k)) is int and type(old_io.get(k)) is int and io[k] >= old_io[k] for k in keys):
                        io_delta = {key: io[key] - old_io[key] for key in keys}
                    else:
                        interval_reason = "nonmonotonic_process_io_counter"
        self._previous = (now, cpu_ns, io)
        activity = deepcopy(self._activity())
        record = {"record_type": "sample", "run_id": self.run_id, "sample_index": self._count,
                  "updated_at_utc": utc_now(), "host_monotonic_ns": now,
                  "probe_duration_ms": (now - started) / 1e6, "sample_interval_ms": elapsed_ms,
                  "process_cpu_percent_one_core_scale": cpu_percent, "interval_reason": interval_reason,
                  "process_cpu_ns": cpu_ns, "resources": resources, "process_io": io, "process_io_delta": io_delta,
                  "native_vio_process": native_vio,
                  "activity": activity, "position_authority": False}
        return record

    @staticmethod
    def _write(handle, value: dict) -> None:
        line = json.dumps(value, sort_keys=True, allow_nan=False) + "\n"
        if len(line.encode("utf-8")) > 65_536:
            raise ValueError("Runtime metrics record exceeds 64 KiB bound")
        handle.write(line)
        handle.flush()

    def _run(self) -> None:
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            with self.path.open("x", encoding="utf-8") as handle:
                self._write(handle, {"record_type": "header", "schema_version": "vision_nav_runtime_metrics_v1",
                    "run_id": self.run_id, "process_id": os.getpid(), "hardware": self._hardware,
                    "interval_s": self._interval, "max_samples": self._max_samples, "created_at_utc": utc_now(),
                    "cpu_scope": "runtime_process_all_threads_excludes_child_processes",
                    "native_vio_measurement_requested": self._native_vio_probe is not None,
                    "io_scope": "kernel_process_storage_demand_not_physical_flash_writes",
                    "position_authority": False})
                with self._lock:
                    self._state = "running"
                while not self._stop.is_set() and self._count < self._max_samples:
                    started = time.monotonic()
                    record = self._sample()
                    self._write(handle, record)
                    # Heartbeats need resources only, not a recursively nested activity snapshot.
                    latest = {key: value for key, value in record.items() if key != "activity"}
                    with self._lock:
                        self._latest = latest
                        self._count += 1
                        self._native_available_count += record["native_vio_process"].get("status") == "available"
                    self._stop.wait(max(0.0, self._interval - (time.monotonic() - started)))
                final = "sample_limit_reached" if self._count >= self._max_samples else "stopped"
                self._write(handle, {"record_type": "footer", "run_id": self.run_id, "sample_count": self._count,
                                     "status": final, "host_monotonic_ns": self._clock(), "position_authority": False})
                with self._lock:
                    self._state = final
        except Exception as exc:
            with self._lock:
                self._error = f"{type(exc).__name__}:{exc}"[:1000]
                self._state = "failed"
        finally:
            self._stop.set()
