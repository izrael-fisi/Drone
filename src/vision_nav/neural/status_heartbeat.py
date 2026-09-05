"""Independent shadow liveness reporting, never a stream of navigation fixes."""

from copy import deepcopy
from datetime import UTC, datetime
import json
import math
import os
from pathlib import Path
import tempfile
import threading
import time
from typing import Callable
from uuid import uuid4


MAX_HEARTBEAT_BYTES = 60_000


def write_status_snapshot(path: Path, status: dict) -> None:
    """Replace a snapshot atomically so concurrent readers never see partial JSON."""
    payload = json.dumps(status, sort_keys=True, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(payload)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _short(value) -> str:
    return str(value)[:1000]


class ShadowStatusHeartbeat:
    """One status writer; inference changes only compact state under a lock.

    `emit` writes the snapshot and optionally sends it to the GCS. It may return
    a nonfatal transport error; an exception is a fatal publisher error. Callback
    health probes must be nonblocking. This is not a process/hardware watchdog.
    """

    PHASES = {"initializing", "waiting_for_frame", "frame_preprocessing", "inference", "recording_evidence", "idle",
              "stopping", "stopped", "shutdown_incomplete"}

    def __init__(self, base_status: dict, emit: Callable[[dict], str | None], *,
                 camera_status: Callable[[], dict], attitude_status: Callable[[], dict],
                 metrics_status: Callable[[], dict] | None = None,
                 interval_s: float = 0.5, inference_warning_ms: float = 2000.0,
                 max_frame_age_ms: float = 1000.0, clock_ns: Callable[[], int] = time.monotonic_ns):
        for value in (interval_s, inference_warning_ms, max_frame_age_ms):
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                raise ValueError("Heartbeat timing limits must be finite and positive")
        if base_status.get("schema_version") != "vision_nav_runtime_status_v3" or base_status.get("shadow_mode") is not True:
            raise ValueError("Heartbeat requires a shadow runtime v3 status")
        self._base = deepcopy(base_status)
        self._base["inference"]["frame_probe"] = {}
        self._emit, self._camera_status, self._attitude_status = emit, camera_status, attitude_status
        self._metrics_status = metrics_status
        self._interval_s, self._warning_ms, self._max_age_ms = interval_s, inference_warning_ms, max_frame_age_ms
        self._clock = clock_ns
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread = None
        self._run_id = str(uuid4())
        self._phase, self._phase_ns, self._iteration = "initializing", self._clock(), 0
        self._capture_ns = None
        self._last_result = None
        self._emitted = 0
        self._error = self._transport_error = None

    @property
    def run_id(self) -> str:
        return self._run_id

    def activity(self) -> dict:
        """Bounded phase context for the metrics recorder, without recursive metrics."""
        with self._lock:
            return {"phase": self._phase, "phase_started_host_monotonic_ns": self._phase_ns,
                    "result_iteration": self._iteration,
                    "map_release_id": self._base.get("active_map", {}).get("release_id"),
                    "model_release_id": self._base.get("active_model", {}).get("release_id")}

    def set_phase(self, phase: str, *, iteration: int, capture_ns: int | None = None) -> None:
        if phase not in self.PHASES or type(iteration) is not int or iteration < 0:
            raise ValueError("Valid heartbeat phase and nonnegative iteration required")
        if capture_ns is not None and (type(capture_ns) is not int or capture_ns <= 0):
            raise ValueError("Capture timestamp must be positive monotonic nanoseconds or absent")
        with self._lock:
            self._phase, self._phase_ns, self._iteration = phase, self._clock(), iteration
            self._capture_ns = capture_ns

    def completed(self, status: dict) -> None:
        """Summarize a completed decision; keep detailed evidence in the trace only."""
        probe = status.get("inference", {}).get("frame_probe", {})
        capture_ns = probe.get("capture_timing", {}).get("host_monotonic_ns")
        if type(capture_ns) is not int or capture_ns <= 0:
            capture_ns = None
        latency = {str(key)[:80]: value for key, value in list(status.get("inference", {}).get("latency_ms", {}).items())[:32]
                   if type(value) in (int, float) and math.isfinite(value) and value >= 0}
        summary = {"sequence": status.get("sequence"), "completed_at_utc": status.get("updated_at_utc"),
                   "completed_host_monotonic_ns": self._clock(), "capture_host_monotonic_ns": capture_ns,
                   "decision_reason": _short(status.get("publication_decision", {}).get("reason")),
                   "latency_ms": latency, "camera_status": _short(status.get("camera_health", {}).get("status")),
                   "position_authority": False, "evidence_path": "decision_trace.jsonl"}
        with self._lock:
            self._last_result = summary
            self._phase, self._phase_ns, self._capture_ns = "idle", self._clock(), None

    def snapshot(self) -> dict:
        with self._lock:
            now = self._clock()
            phase, phase_ns, iteration = self._phase, self._phase_ns, self._iteration
            capture_ns, last = self._capture_ns, deepcopy(self._last_result)
            emitted, error, transport = self._emitted, self._error, self._transport_error
        status = deepcopy(self._base)
        phase_age = (now - phase_ns) / 1e6
        if last is not None:
            last["completion_age_ms"] = (now - last["completed_host_monotonic_ns"]) / 1e6
            last["capture_age_ms"] = ((now - last["capture_host_monotonic_ns"]) / 1e6
                                      if last["capture_host_monotonic_ns"] is not None else None)
            last["capture_within_diagnostic_age_limit"] = (last["capture_age_ms"] is not None
                and 0 <= last["capture_age_ms"] <= self._max_age_ms)
        status.update({"updated_at_utc": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                       "sequence": emitted, "emission_kind": "heartbeat", "runtime_state": "NO_GLOBAL_FIX",
                       "shadow_mode": True})
        status["heartbeat"] = {"run_id": self._run_id, "host_monotonic_ns": now, "phase": phase,
            "phase_age_ms": phase_age, "result_iteration": iteration,
            "inference_overdue": phase == "inference" and phase_age > self._warning_ms,
            "inference_warning_ms": self._warning_ms, "interval_s": self._interval_s,
            "current_capture_age_ms": (now - capture_ns) / 1e6 if capture_ns is not None else None,
            "publisher_error": error, "previous_transport_error": transport,
            "not_a_hardware_watchdog": True}
        status["inference"]["frame_probe"] = {}
        status["inference"]["last_completed_result"] = last
        status["inference"]["latency_ms"] = last["latency_ms"] if last is not None else {}
        status["camera_health"] = self._probe_health(self._camera_status)
        status["attitude_health"] = self._probe_health(self._attitude_status)
        if self._metrics_status is not None:
            status["runtime_metrics"] = self._probe_health(self._metrics_status)
        status["position"] = {"source_state": "no_position", "navigation_mode": "NO_GLOBAL_FIX",
            "heading_confidence": 0.0, "location_confidence": 0.0, "top_k_candidates": [], "active_hypotheses": [],
            "shadow_tracking_candidate": {"status": "withheld", "reason": "heartbeat_is_not_navigation_evidence",
                                          "position_authority": False}}
        status["px4_publication"] = {"enabled": False, "reason": "shadow_release_no_odometry_output"}
        status["publication_decision"] = {"published": False, "reason": "heartbeat_is_not_navigation_evidence"}
        return status

    @staticmethod
    def _probe_health(callback: Callable[[], dict]) -> dict:
        try:
            return deepcopy(callback())
        except Exception as exc:
            return {"status": "probe_failed", "error": _short(f"{type(exc).__name__}:{exc}")}

    def start(self) -> None:
        with self._lock:
            if self._thread is not None or self._stop.is_set():
                raise RuntimeError("Heartbeat cannot be restarted")
            self._thread = threading.Thread(target=self._run, name="vision-nav-status", daemon=True)
            self._thread.start()

    def check(self) -> None:
        with self._lock:
            if self._error:
                raise RuntimeError(f"Status publisher failed: {self._error}")

    def stop(self, *, timeout_s: float = 2.0, final_phase: str = "stopped") -> bool:
        if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or timeout_s < 0:
            raise ValueError("Shutdown wait must be finite and nonnegative")
        if final_phase not in {"stopped", "shutdown_incomplete"}:
            raise ValueError("Invalid final heartbeat phase")
        with self._lock:
            self._phase, self._phase_ns = final_phase, self._clock()
            self._capture_ns = None
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout_s)
        return not bool(self._thread and self._thread.is_alive())

    def _publish(self) -> None:
        status = self.snapshot()
        if len(json.dumps(status, allow_nan=False).encode("utf-8")) > MAX_HEARTBEAT_BYTES:
            raise ValueError("Compact runtime heartbeat exceeds 60000-byte transport bound")
        error = self._emit(status)
        with self._lock:
            self._emitted += 1
            self._transport_error = _short(error) if error is not None else None

    def _run(self) -> None:
        try:
            while not self._stop.is_set():
                started = time.monotonic()
                self._publish()
                self._stop.wait(max(0.0, self._interval_s - (time.monotonic() - started)))
            self._publish()
        except Exception as exc:
            with self._lock:
                self._error = _short(f"{type(exc).__name__}:{exc}")
            self._stop.set()
