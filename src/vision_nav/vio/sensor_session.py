"""Finite prop-off camera/MAVLink collection feeding native local VIO only."""

import argparse
from collections import deque
from copy import deepcopy
import json
import math
from pathlib import Path
import threading
import time

from ..camera_ingress import Picamera2Ingress
from ..mavlink_bridge import MavlinkVisionBridge
from .live_worker import NativeVioLiveWorker
from .openvins_replay import digest
from .sensor_buffer import LiveSensorBuffer


class LiveSensorSession:
    """One camera owner, one link owner, one native-input consumer.

    The caller may consume latest complete learned windows independently. No
    model, map fusion, PX4 parameter change or navigation transmission occurs.
    """

    def __init__(self, binary, checkout, profile_dir, output, *, endpoint=None,
                 camera_factory=None, link_factory=None, frame_count=120, interval_s=.05,
                 window_size=2, warmup_timeout_s=10., duration_s=60., frame_capacity=16,
                 frame_bytes=64*1024*1024, wait_ms=1000., max_clones=None,
                 record_images=False, max_image_bytes=1024**3, anchor_capacity=0):
        if type(frame_count) is not int or not 2 <= frame_count <= 20000:
            raise ValueError("frame_count must be in [2,20000]")
        if type(window_size) is not int or not 2 <= window_size <= 16:
            raise ValueError("window_size must be in [2,16]")
        if window_size > frame_count:
            raise ValueError("window_size cannot exceed frame_count")
        for name, value, low, high in (("interval_s", interval_s, .025, .2),
                ("warmup_timeout_s", warmup_timeout_s, 1., 30.), ("duration_s", duration_s, 1., 1800.)):
            if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"{name} must be in [{low},{high}]")
        if link_factory is None and (not isinstance(endpoint, str) or not endpoint.strip()):
            raise ValueError("Explicit MAVLink endpoint or owned test link required")
        self.root = Path(output).resolve()
        if self.root.exists():
            raise FileExistsError("Live sensor session output must be new")
        self.native = NativeVioLiveWorker(Path(binary), Path(checkout), Path(profile_dir), self.root / "vio",
            max_frames=frame_count, max_duration_s=duration_s, max_clones=max_clones,
            record_images=record_images, max_image_bytes=max_image_bytes, anchor_capacity=anchor_capacity)
        self.buffer = LiveSensorBuffer(self.native._profile, self.native._camera,
            frame_capacity=frame_capacity, frame_bytes=frame_bytes, wait_ms=wait_ms)
        width, height = self.native._camera["resolution"]
        self._camera_factory = camera_factory or (lambda: Picamera2Ingress(width=width, height=height))
        self._link_factory = link_factory or (lambda: MavlinkVisionBridge(endpoint))
        self._frame_count, self._interval = frame_count, interval_s
        self._warmup_timeout, self._duration = warmup_timeout_s, duration_s
        self._window_size = window_size
        self._window, self._pending = deque(maxlen=window_size), None
        self._contexts = deque(maxlen=window_size)
        self._lock = threading.Lock()
        self._stop, self._stop_inputs = threading.Event(), threading.Event()
        self._thread = self._camera_thread = self._link_thread = None
        self._state, self._error = "not_started", None
        self._camera_state = self._link_state = "not_started"
        self._captured = self._delivered = self._selected = self._superseded = 0
        self._last_camera_ns = None
        self._source_hashes = {name: digest(Path(__file__).resolve().parents[1] / name) for name in
            ("vio/sensor_session.py", "vio/sensor_buffer.py", "camera_ingress.py", "mavlink_bridge.py")}
        self._camera_done = threading.Event()

    def start(self):
        with self._lock:
            if self._thread is not None or self._stop.is_set():
                raise RuntimeError("Live sensor session cannot restart")
            self._state = "warming_sensors"
            self._thread = threading.Thread(target=self._run, name="vision-nav-sensor-session", daemon=True)
            self._thread.start()

    def _abort(self, reason):
        with self._lock:
            self._error = self._error or str(reason)[:1000]
            self._window.clear()
            self._contexts.clear()
            self._pending = None
        self.buffer.fail(reason)
        self._stop.set()
        self._stop_inputs.set()

    def _check(self):
        if self._stop.is_set():
            raise InterruptedError(self._error or "operator_stopped")
        self.buffer.check_deadlines()

    def take_latest_window(self):
        native_state = self.native.status()["status"]
        with self._lock:
            if self._error or self._stop.is_set() or native_state in {"failed", "stopped", "shutdown_incomplete"}:
                self._window.clear()
                self._contexts.clear()
                self._pending = None
                return None
            if self._pending is None:
                return None
            tickets, contexts = self._pending
            result = {"worker_run_id": self.native.worker_run_id, "tickets": tickets,
                "contexts": deepcopy(contexts),
                "source_session_state": self._state, "native_state": native_state, "position_authority": False,
                "native_and_global_fusion_connected": False}
            self._pending = None
            self._selected += 1
            return result

    def status(self):
        with self._lock:
            result = {"schema_version": "vision_nav_live_sensor_session_v1", "state": self._state, "error": self._error,
                "camera_state": self._camera_state, "link_state": self._link_state,
                "camera_frames": self._captured, "delivered_frames": self._delivered,
                "selected_learned_windows": self._selected, "superseded_learned_windows": self._superseded,
                "pending_learned_window": self._pending is not None,
                "thread_alive": bool(self._thread and self._thread.is_alive()),
                "camera_thread_alive": bool(self._camera_thread and self._camera_thread.is_alive()),
                "link_thread_alive": bool(self._link_thread and self._link_thread.is_alive()),
                "navigation_mode": "NO_GLOBAL_FIX", "position_authority": False, "px4_external_vision": "disabled"}
        result.update(buffer=self.buffer.status(), native=self.native.status())
        return result

    def join(self, timeout_s=0.):
        if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or timeout_s < 0:
            raise ValueError("Join timeout must be finite and nonnegative")
        if self._thread:
            self._thread.join(timeout_s)
        return not any(thread and thread.is_alive() for thread in (self._thread, self._camera_thread, self._link_thread))

    def stop(self, timeout_s=5.):
        self._stop.set()
        self._stop_inputs.set()
        with self._lock:
            self._window.clear()
            self._contexts.clear()
            self._pending = None
        return self.join(timeout_s)

    @staticmethod
    def _write(stream, row):
        line = json.dumps(row, sort_keys=True, allow_nan=False)+"\n"
        if len(line.encode()) > 65536 or stream.tell()+len(line.encode()) > 64*1024*1024:
            raise ValueError("Sensor journal exceeds bounded evidence limit")
        stream.write(line)
        stream.flush()

    def _read_link(self):
        link, cursor = None, 0
        try:
            link = self._link_factory()
            link.connect()
            link.start_timesync_recording(interval_s=.2, timeout_s=.1, max_exchanges=10000)
            with self._lock:
                self._link_state = "receiving"
            with (self.root / "telemetry.jsonl").open("x") as stream:
                while not self._stop_inputs.is_set():
                    sample = link.try_read_telemetry(timeout_s=.02)
                    if sample is not None:
                        host = time.monotonic_ns()
                        row = sample.to_dict()
                        self._write(stream, {"record_type": "telemetry", "host_monotonic_ns": host, "sample": row})
                        self.buffer.push_telemetry(row, host_ns=host)
                    update = link.read_timesync_recording(cursor)
                    cursor = update["next_cursor"]
                    for exchange in update["exchanges"]:
                        self._write(stream, {"record_type": "timesync", "exchange": exchange})
                        self.buffer.push_exchange(exchange)
                    if update["capacity_reached"]:
                        raise ValueError("TIMESYNC recorder capacity reached")
                    if sample is None:
                        self._stop_inputs.wait(.001)
        except Exception as exc:
            self._abort(f"sensor_link_failed:{type(exc).__name__}:{exc}")
        finally:
            if link is not None:
                try:
                    evidence = link.stop_timesync_recording()
                    (self.root / "timesync.json").write_text(json.dumps(evidence, indent=2)+"\n")
                except Exception as exc:
                    self._abort(f"sensor_clock_close_failed:{type(exc).__name__}:{exc}")
                try:
                    link.close()
                except Exception as exc:
                    self._abort(f"sensor_link_close_failed:{type(exc).__name__}:{exc}")
            with self._lock:
                self._link_state = "stopped"

    def _capture_camera(self):
        camera = None
        try:
            camera = self._camera_factory()
            if not callable(getattr(camera, "capture_memory", None)):
                raise ValueError("Ordered VIO capture requires the in-memory camera API")
            with self._lock:
                self._camera_state = "capturing"
            with (self.root / "camera.jsonl").open("x") as stream:
                for index in range(self._frame_count):
                    if self._stop_inputs.is_set():
                        break
                    started = time.monotonic()
                    path = self.root / f"reserved-camera-{index:06d}.png"
                    frame = camera.capture_memory(path)
                    if frame.path.resolve() != path or path.exists():
                        raise ValueError("Camera must return requested reserved path without writing a file")
                    self.buffer.push_frame(frame)
                    self._write(stream, {"record_type": "camera", "sequence": index, "camera_metadata": frame.to_dict()})
                    with self._lock:
                        self._captured += 1
                        self._last_camera_ns = time.monotonic_ns()
                    self._stop_inputs.wait(max(0., self._interval-(time.monotonic()-started)))
        except Exception as exc:
            self._abort(f"camera_owner_failed:{type(exc).__name__}:{exc}")
        finally:
            if camera is not None:
                try:
                    camera.close()
                except Exception as exc:
                    self._abort(f"camera_close_failed:{type(exc).__name__}:{exc}")
            with self._lock:
                self._camera_state = "stopped"
            self._camera_done.set()

    def _run(self):
        native_started = False
        try:
            self._link_thread = threading.Thread(target=self._read_link, name="vision-nav-raw-imu", daemon=True)
            self._link_thread.start()
            deadline = time.monotonic()+self._warmup_timeout
            while not self.buffer.ready():
                self._check()
                if time.monotonic() > deadline:
                    raise TimeoutError("Sensor warmup lacked fresh raw IMU or bracketed clock fit")
                self._stop.wait(.01)
            self.buffer.arm()
            self.native.start()
            native_started = True
            with self._lock:
                self._state = "collecting"
            camera_started_ns = time.monotonic_ns()
            self._camera_thread = threading.Thread(target=self._capture_camera, name="vision-nav-ordered-camera", daemon=True)
            self._camera_thread.start()
            with (self.root / "dispatch.jsonl").open("x") as stream:
                while self._delivered < self._frame_count:
                    self._check()
                    native = self.native.status()
                    if native["status"] in {"failed", "stopped", "shutdown_incomplete", "complete", "no_local_estimate"}:
                        raise RuntimeError("Native VIO terminated before all sensor frames: "+str(native["error"]))
                    if (not self._camera_done.is_set() and time.monotonic_ns()-(self._last_camera_ns or camera_started_ns) > 5_000_000_000):
                        raise TimeoutError("Camera owner stalled")
                    # Clock evidence often releases several camera frames at
                    # once. Keep them in the bounded sensor buffer until this
                    # sole native producer has a slot; never drop/retry IMU.
                    if native["pending_batch_count"] >= native["pending_capacity"]:
                        self._stop.wait(.005)
                        continue
                    batch = self.buffer.take_batch()
                    if batch is None:
                        self._stop.wait(.005)
                        continue
                    ticket = self.native.submit_frame(batch["frame"], batch["imu_samples"],
                        calibration_id=self.native._profile["camera_calibration_id"], clock_fit=batch["clock_fit"])
                    self._write(stream, {"record_type": "dispatch", "sequence": ticket.sequence,
                        "worker_run_id": ticket.worker_run_id, "timestamp_ns": ticket.timestamp_ns,
                        "pixel_sha256": ticket.pixel_sha256, "clock_fit": batch["clock_report"],
                        "learned_context": batch["learned_context"],
                        "bracketing_wait_ms": batch["wait_ms"], "position_authority": False})
                    with self._lock:
                        self._delivered += 1
                        self._window.append(ticket)
                        self._contexts.append({**batch["learned_context"], "sequence": ticket.sequence,
                            "worker_run_id": ticket.worker_run_id, "pixel_sha256": ticket.pixel_sha256})
                        if len(self._window) == self._window_size:
                            self._superseded += self._pending is not None
                            self._pending = (tuple(self._window), tuple(self._contexts))
            self._stop_inputs.set()
            self.native.finish()
            while not self.native.join(.05):
                # All final-frame IMU coverage is already submitted. Intentional
                # input closure is not a fresh-telemetry requirement during drain.
                if self._stop.is_set():
                    raise InterruptedError(self._error or "operator_stopped")
            native = self.native.status()
            if native["status"] not in {"complete", "no_local_estimate"}:
                raise RuntimeError("Native drain failed: "+str(native["error"]))
            with self._lock:
                self._state = "complete" if native["status"] == "complete" else "completed_without_local_estimate"
        except InterruptedError:
            with self._lock:
                self._state = "failed" if self._error else "stopped"
        except Exception as exc:
            self._abort(f"sensor_session_failed:{type(exc).__name__}:{exc}")
            with self._lock:
                self._state = "failed"
        finally:
            self._stop_inputs.set()
            for thread in (self._camera_thread, self._link_thread):
                if thread:
                    thread.join(3.)
            closed = all(not thread or not thread.is_alive() for thread in (self._camera_thread, self._link_thread))
            if native_started:
                closed = self.native.stop() and closed
            try:
                for name, expected in self._source_hashes.items():
                    if digest(Path(__file__).resolve().parents[1] / name) != expected:
                        self._abort("sensor_adapter_source_changed")
            except OSError as exc:
                self._abort(f"sensor_adapter_verification_failed:{exc}")
            with self._lock:
                if not closed:
                    self._state, self._error = "shutdown_incomplete", self._error or "sensor_owner_did_not_stop"
                elif self._error:
                    self._state = "failed"
                if self._state not in {"complete", "completed_without_local_estimate"}:
                    self._window.clear()
                    self._contexts.clear()
                    self._pending = None
            try:
                report = self.status()
                report["python_adapter_sha256"] = self._source_hashes
                report["report_scope"] = "terminal_snapshot_before_coordinator_thread_returns"
                report["evidence_sha256"] = {p.name: digest(p) for p in self.root.glob("*.jsonl")}
                if (self.root / "timesync.json").exists():
                    report["evidence_sha256"]["timesync.json"] = digest(self.root / "timesync.json")
                if (self.root / "vio/live_report.json").exists():
                    report["evidence_sha256"]["vio/live_report.json"] = digest(self.root / "vio/live_report.json")
                (self.root / "session_report.json").write_text(json.dumps(report, indent=2)+"\n")
            except Exception as exc:
                self._abort(f"session_report_write_failed:{type(exc).__name__}:{exc}")
                with self._lock:
                    self._state = "failed"


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("binary", "checkout", "sensor-profile", "output"):
        parser.add_argument("--"+name, required=True, type=Path)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--frames", type=int, default=120)
    parser.add_argument("--interval-s", type=float, default=.05)
    parser.add_argument("--max-clones", type=int)
    parser.add_argument("--record-images", action="store_true", help="Retain exact native input PNGs for offline replay")
    parser.add_argument("--max-image-bytes", type=int, default=1024**3, help="Recording image budget; default 1 GiB")
    parser.add_argument("--propellers-removed", action="store_true")
    args = parser.parse_args(argv)
    if not args.propellers_removed:
        parser.error("This finite bench command requires --propellers-removed")
    session = LiveSensorSession(args.binary, args.checkout, args.sensor_profile, args.output,
        endpoint=args.endpoint, frame_count=args.frames, interval_s=args.interval_s, max_clones=args.max_clones,
        record_images=args.record_images, max_image_bytes=args.max_image_bytes)
    session.start()
    try:
        while not session.join(1.):
            pass
    except KeyboardInterrupt:
        session.stop()
    status = session.status()
    print(json.dumps(status, indent=2))
    return 0 if status["state"] == "complete" and not status["thread_alive"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
