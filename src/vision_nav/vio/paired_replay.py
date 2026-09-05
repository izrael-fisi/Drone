"""Independent sensor replay and latest-window handoff for learned inference.

Recorded sensors may wait on VIO backpressure. This is not a live ingress policy,
an estimator, or a conversion from local VIO into global position authority.
"""

import json
import math
from pathlib import Path
import threading
import time

import numpy as np

from .openvins_replay import digest, read_yaml, validate_capture
from .stream_replay import sensor_batches
from .stream_worker import NativeVioStreamWorker


class PairedSensorReplay:
    def __init__(self, binary, capture, output, *, session, manifest, calibration,
                 camera_context, window_size, playback_speed=1., max_duration_s=1800.):
        if type(window_size) is not int or not 2 <= window_size <= 32:
            raise ValueError("Paired replay needs a window of 2..32 frames")
        for name, value, maximum in (("playback_speed", playback_speed, 100.),
                                     ("max_duration_s", max_duration_s, 3600.)):
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 < value <= maximum:
                raise ValueError(f"Invalid paired replay {name}")
        self.capture, self.session = Path(capture).resolve(), Path(session).resolve()
        self.worker = NativeVioStreamWorker(Path(binary), self.capture, Path(output))
        native = self.worker.manifest
        source = native.get("source", {})
        self._session_hash = digest(self.session / "manifest.json")
        self._telemetry = (self.session / manifest["telemetry_path"]).resolve()
        if not self._telemetry.is_relative_to(self.session):
            raise ValueError("Escaped paired telemetry path")
        self._telemetry_hash = digest(self._telemetry)
        if (native.get("scope") != "pi_capture_local_vio_research_not_flight_qualification"
                or native.get("sensor_frame") != "body_frd"
                or source.get("session_id") != manifest["session_id"]
                or source.get("capture_manifest_sha256") != self._session_hash
                or source.get("telemetry_sha256") != self._telemetry_hash):
            raise ValueError("Native input is not bound to this exact camera/IMU capture")
        mapping = json.loads((self.capture / "capture_timing.json").read_text())["frames"]
        if not window_size <= len(mapping) == len(manifest["frames"]) <= 20000:
            raise ValueError("Paired replay frame count differs or is insufficient")
        for index, (mapped, frame) in enumerate(zip(mapping, manifest["frames"], strict=True)):
            path = (self.session / frame["path"]).resolve()
            if (frame["sequence"] != index or not path.is_relative_to(self.session)
                    or mapped["source_path"] != frame["path"] or digest(path) != mapped["sha256"]
                    or native["checksums"].get(mapped["image_path"]) != mapped["sha256"]):
                raise ValueError("Native/learned camera image identity mismatch")
        # Compare actual parameters, not just a reused human-readable ID. The
        # converter's body-FRD IMU frame makes T_imu_cam the same physical mount.
        camera = read_yaml(self.capture / "kalibr_imucam_chain.yaml")["cam0"]
        k = calibration.camera_matrix
        distortion = calibration.distortion_coefficients
        expected_k = np.array([[k[0, 0], 0., k[0, 2]], [0., k[1, 1], k[1, 2]], [0., 0., 1.]])
        if (camera["camera_model"] != "pinhole" or camera["distortion_model"] != "radtan"
                or camera["resolution"] != list(calibration.image_size)
                or not np.allclose(k, expected_k, atol=1e-10, rtol=0)
                or not np.allclose(camera["intrinsics"], [k[0, 0], k[1, 1], k[0, 2], k[1, 2]], atol=1e-8, rtol=0)
                or len(distortion) < 4 or np.any(distortion[4:] != 0)
                or not np.allclose(camera["distortion_coeffs"], distortion[:4], atol=1e-10, rtol=0)):
            raise ValueError("Native/learned camera calibration mismatch")
        x, y, z, w = camera_context[4:8]
        rotation = np.array([[1-2*(y*y+z*z), 2*(x*y-z*w), 2*(x*z+y*w)],
                             [2*(x*y+z*w), 1-2*(x*x+z*z), 2*(y*z-x*w)],
                             [2*(x*z-y*w), 2*(y*z+x*w), 1-2*(x*x+y*y)]])
        transform = np.eye(4)
        transform[:3, :3], transform[:3, 3] = rotation, camera_context[8:11]
        if not np.allclose(camera["T_imu_cam"], transform, atol=1e-6, rtol=0):
            raise ValueError("Native/learned camera mount mismatch")
        # Validate camera order before starting any process, not midway through
        # a run after a mismatched sequence has already entered the estimator.
        count = 0
        for count, batch in enumerate(sensor_batches(self.capture / "events.jsonl"), start=1):
            event = batch[-1]
            if count > len(mapping) or any(event[key] != mapping[count-1][key] for key in ("timestamp_ns", "image_path")):
                raise ValueError("Native event stream differs from paired frame mapping")
        if count != len(mapping):
            raise ValueError("Native event stream frame count mismatch")
        self._mapping = mapping
        self.size, self.speed, self.limit = window_size, playback_speed, max_duration_s
        self._condition, self._stop = threading.Condition(), threading.Event()
        self._thread, self._pending, self._error = None, None, None
        self._closed = False
        self._offered = self._selected = self._superseded = 0
        self._max_lateness_ms = 0.
        self._max_backpressure_ms = 0.
        self._started_ns = None

    def start(self):
        if self._thread is not None or self._stop.is_set():
            raise RuntimeError("Paired replay cannot be restarted")
        self.worker.start()
        self._started_ns = time.monotonic_ns()
        self._thread = threading.Thread(target=self._produce, name="vision-nav-paired-sensors", daemon=True)
        self._thread.start()

    def verify_frame(self, index, path):
        if digest(path) != self._mapping[index]["sha256"]:
            raise ValueError("Paired camera image changed during learned processing")

    def _check(self):
        if self._stop.is_set():
            raise InterruptedError("Paired replay stopped")
        if time.monotonic_ns() - self._started_ns > self.limit * 1e9:
            raise TimeoutError("Paired sensor replay exceeded duration limit")

    def _produce(self):
        try:
            origin = self._mapping[0]["timestamp_ns"]
            for index, batch in enumerate(sensor_batches(self.capture / "events.jsonl")):
                target = self._started_ns + int((batch[-1]["timestamp_ns"] - origin) / self.speed)
                while time.monotonic_ns() < target:
                    self._check()
                    self._stop.wait(max(0., min(.02, (target - time.monotonic_ns()) / 1e9)))
                self._check()
                began = time.monotonic_ns()
                while not self.worker.submit(batch):
                    self._check()
                    self._stop.wait(.005)
                offered_ns = time.monotonic_ns()
                with self._condition:
                    self._max_lateness_ms = max(self._max_lateness_ms, (offered_ns - target) / 1e6)
                    self._max_backpressure_ms = max(self._max_backpressure_ms, (offered_ns - began) / 1e6)
                    if index + 1 >= self.size:
                        self._superseded += self._pending is not None
                        self._pending = {"indices": list(range(index + 1 - self.size, index + 1)),
                            "anchor_vio_timestamp_ns": batch[-1]["timestamp_ns"],
                            "anchor_frame_sha256": self._mapping[index]["sha256"],
                            "scheduled_host_monotonic_ns": target, "offered_host_monotonic_ns": offered_ns}
                        self._offered += 1
                        self._condition.notify_all()
            self.worker.finish()
            while not self.worker.join(.02):
                self._check()
            status = self.worker.status()
            if status["status"] not in {"complete", "no_local_estimate"}:
                raise RuntimeError(f"Native replay failed: {status['error']}")
            validate_capture(self.capture)
            if (digest(self.capture / "input_manifest.json") != self.worker.manifest_sha256
                    or digest(self.worker.binary) != self.worker.binary_sha256
                    or digest(self.session / "manifest.json") != self._session_hash
                    or digest(self._telemetry) != self._telemetry_hash):
                raise ValueError("Paired replay inputs changed during execution")
            for index, frame in enumerate(self._mapping):
                self.verify_frame(index, self.session / frame["source_path"])
        except Exception as exc:
            with self._condition:
                self._error = f"{type(exc).__name__}:{exc}"
                self._pending = None
            self.worker.stop()
        finally:
            with self._condition:
                self._closed = True
                self._condition.notify_all()

    def windows(self):
        if self._thread is None:
            raise RuntimeError("Paired replay has not started")
        while True:
            with self._condition:
                while self._pending is None and not self._closed:
                    self._condition.wait(.1)
                if self._error:
                    raise RuntimeError(self._error)
                if self._pending is None:
                    break
                value, self._pending = self._pending, None
                self._selected += 1
            yield value
        self._thread.join(4.)
        if self._thread.is_alive():
            raise RuntimeError("Paired sensor producer did not finish")

    def stop(self):
        self._stop.set()
        with self._condition:
            self._condition.notify_all()
        if self._thread:
            self._thread.join(4.)
        self.worker.stop()
        if self.worker.output.exists():
            (self.worker.output / "paired_report.json").write_text(json.dumps(self.status(), indent=2) + "\n")

    def status(self):
        native = self.worker.status()
        states = self.worker.output / "local_states.jsonl"
        with self._condition:
            return {"schema_version": "vision_nav_paired_sensor_replay_v1",
                "worker_run_id": self.worker.worker_run_id, "native": native,
                "sensor_producer_alive": bool(self._thread and self._thread.is_alive()),
                "complete": (self._closed and self._error is None and native["status"] == "complete"
                             and not native["thread_alive"] and not bool(self._thread and self._thread.is_alive())),
                "error": self._error, "window_size": self.size, "playback_speed": self.speed,
                "offered_windows": self._offered, "selected_windows": self._selected,
                "superseded_windows": self._superseded, "pending_windows": int(self._pending is not None),
                "max_sensor_schedule_lateness_ms": self._max_lateness_ms,
                "max_vio_submit_wait_ms": self._max_backpressure_ms,
                "input_manifest_sha256": self.worker.manifest_sha256,
                "binary_sha256": self.worker.binary_sha256, "adapter_sha256": digest(__file__),
                "states_sha256": digest(states) if self._closed and not native["thread_alive"] and states.is_file() else None,
                "timing_scope": "paced_recording_not_physical_live_capture_latency",
                "fusion": "not_connected", "position_authority": False, "px4_external_vision": "disabled"}
