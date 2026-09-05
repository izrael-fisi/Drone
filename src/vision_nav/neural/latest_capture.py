"""Single-owner camera capture with a one-frame, latest-only handoff."""

from __future__ import annotations

from dataclasses import replace
import math
import os
from pathlib import Path
import shutil
import tempfile
import threading
import time
from typing import Callable

import numpy as np

from ..camera_ingress import CameraIngress, CapturedFrame


class LatestCaptureWorker:
    """Capture independently of inference; retain only frames taken for evidence.

    The camera is created, used and closed by this worker's thread. Superseded
    buffers are released without disk writes when the camera supports memory
    capture. File-only backends use a private spool. Selected frames are saved
    for shadow evidence; this is not a high-rate raw image/IMU VIO frontend.
    """

    def __init__(self, factory: Callable[[], CameraIngress], output_dir: Path, *, interval_s: float = 0.5):
        if type(interval_s) not in (int, float) or not math.isfinite(interval_s) or interval_s <= 0:
            raise ValueError("Capture interval must be finite and positive")
        self._factory, self._output_dir, self._interval_s = factory, output_dir.resolve(), interval_s
        self._condition = threading.Condition()
        self._take_lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._spool: Path | None = None
        self._pending: CapturedFrame | None = None
        self._state = "not_started"
        self._error: str | None = None
        self._captured = self._superseded = self._delivered = 0
        self._in_progress = False
        self._memory_captured = self._file_captured = 0

    def start(self) -> None:
        with self._condition:
            if self._thread is not None or self._stop.is_set():
                raise RuntimeError("Capture worker cannot be restarted")
            self._output_dir.mkdir(parents=True, exist_ok=True)
            self._spool = Path(tempfile.mkdtemp(prefix=".pending-", dir=self._output_dir))
            self._state = "starting"
            self._thread = threading.Thread(target=self._run, name="vision-nav-capture", daemon=True)
            self._thread.start()

    def take(self, *, timeout_s: float = 1.0) -> CapturedFrame:
        # Only one consumer may own a frame being encoded at a time.
        with self._take_lock:
            return self._take(timeout_s=timeout_s)

    def _take(self, *, timeout_s: float) -> CapturedFrame:
        if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or timeout_s < 0:
            raise ValueError("Capture wait must be finite and nonnegative")
        with self._condition:
            if self._thread is None:
                raise RuntimeError("Capture worker has not started")
            self._condition.wait_for(lambda: self._pending is not None or self._error or self._stop.is_set(), timeout_s)
            if self._error:
                raise RuntimeError(f"Camera worker failed: {self._error}")
            if self._stop.is_set():
                raise RuntimeError("Camera worker stopped")
            if self._pending is None:
                raise TimeoutError("No captured frame available yet")
            frame = self._pending
            target = self._output_dir / f"{self._spool.name[1:]}-{frame.path.name}"
            if frame.bgr is None:
                # Exclusive linking preserves existing evidence on collision.
                os.link(frame.path, target)
                frame.path.unlink()
                self._pending = None
                self._delivered += 1
                return replace(frame, path=target)
            self._pending = None

        # JPEG encoding and writes must not hold the producer's handoff lock.
        # A failed write is surfaced to the caller, never returned as evidence.
        import cv2
        ok, encoded = cv2.imencode(".jpg", frame.bgr)
        if not ok:
            raise RuntimeError("Could not encode selected camera frame")
        created = False
        try:
            with target.open("xb") as handle:
                created = True
                data = memoryview(encoded).cast("B")
                if handle.write(data) != len(data):
                    raise OSError("Incomplete selected camera frame write")
                handle.flush()
        except BaseException:
            if created:
                target.unlink(missing_ok=True)
            raise
        with self._condition:
            self._delivered += 1
        return replace(frame, path=target, bgr=None)

    def status(self) -> dict:
        with self._condition:
            return {"schema_version": "vision_nav_capture_worker_v1", "state": self._state,
                    "error": self._error, "captured_count": self._captured,
                    "superseded_count": self._superseded, "delivered_count": self._delivered,
                    "memory_capture_count": self._memory_captured, "file_capture_count": self._file_captured,
                    "pending_memory_bytes": (int(self._pending.bgr.nbytes)
                        if self._pending is not None and self._pending.bgr is not None else 0),
                    "pending_count": int(self._pending is not None), "pending_capacity": 1,
                    "capture_in_progress": self._in_progress, "interval_s": self._interval_s,
                    "thread_alive": bool(self._thread and self._thread.is_alive()),
                    "policy": "latest_waiting_frame_only", "position_authority": False}

    def stop(self, *, timeout_s: float = 2.0) -> bool:
        if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or timeout_s < 0:
            raise ValueError("Shutdown wait must be finite and nonnegative")
        self._stop.set()
        with self._condition:
            self._condition.notify_all()
        if self._thread is None:
            self._state = "stopped"
            return True
        self._thread.join(timeout_s)
        if self._thread.is_alive():
            with self._condition:
                self._state = "shutdown_timeout"
            return False
        return True

    def _run(self) -> None:
        ingress = None
        try:
            ingress = self._factory()
            capture_memory = getattr(ingress, "capture_memory", None)
            capture = capture_memory if callable(capture_memory) else ingress.capture
            with self._condition:
                self._state = "running"
            sequence = 0
            while not self._stop.is_set():
                started = time.monotonic()
                path = self._spool / f"capture-{sequence:09d}.jpg"
                with self._condition:
                    self._in_progress = True
                frame = capture(path)
                if (not isinstance(frame, CapturedFrame) or frame.path.resolve() != path
                        or path.is_symlink()):
                    raise ValueError("Camera must return the requested contained capture path")
                if frame.bgr is not None:
                    pixels = frame.bgr
                    if (not isinstance(pixels, np.ndarray) or pixels.dtype != np.uint8
                            or pixels.ndim != 3 or pixels.shape[2] != 3 or pixels.size == 0
                            or not pixels.flags.owndata or not pixels.flags.c_contiguous
                            or pixels.flags.writeable or pixels.nbytes > 64 * 1024 * 1024
                            or path.exists()):
                        raise ValueError("Memory capture requires owned read-only uint8 BGR within 64 MiB and no file")
                elif not path.is_file():
                    raise ValueError("Camera must return a regular capture file or an owned memory frame")
                elif path.stat().st_size > 64 * 1024 * 1024:
                    raise ValueError("Capture exceeds 64 MiB shadow-frame bound")
                with self._condition:
                    self._in_progress = False
                    self._captured += 1
                    self._memory_captured += int(frame.bgr is not None)
                    self._file_captured += int(frame.bgr is None)
                    if self._pending is not None:
                        if self._pending.bgr is None:
                            self._pending.path.unlink()
                        self._superseded += 1
                    self._pending = frame
                    self._condition.notify_all()
                # Do not retain a second reference to a superseded array while
                # the next native camera call is blocked.
                del frame
                pixels = None
                sequence += 1
                self._stop.wait(max(0.0, self._interval_s - (time.monotonic() - started)))
        except Exception as exc:
            with self._condition:
                self._error = f"{type(exc).__name__}:{exc}"
                self._condition.notify_all()
        finally:
            if ingress is not None:
                try:
                    ingress.close()
                except Exception as exc:
                    with self._condition:
                        self._error = self._error or f"camera_close_failed:{type(exc).__name__}:{exc}"
            with self._condition:
                self._pending = None
                self._in_progress = False
                try:
                    shutil.rmtree(self._spool)
                except OSError as exc:
                    self._error = self._error or f"spool_cleanup_failed:{type(exc).__name__}:{exc}"
                self._state = "failed" if self._error else "stopped"
                self._stop.set()
                self._condition.notify_all()
