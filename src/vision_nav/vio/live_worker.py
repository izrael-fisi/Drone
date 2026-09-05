"""Bounded arriving-image adapter for the research-only native VIO process.

One upstream sensor owner supplies ordered raw IMU and exposure-clock evidence.
This adapter does not open cameras/MAVLink, extrapolate clocks, or publish poses.
"""

from copy import deepcopy
from dataclasses import dataclass
import hashlib
import json
import math
from pathlib import Path
import shutil
import threading
import time

import cv2
import numpy as np

from ..camera_ingress import CapturedFrame
from ..capture_timing import Px4ClockFit, TimingPolicy, camera_exposure_time
from .openvins_replay import checked_max_clones, digest, prepare_config, read_yaml, write_yaml
from .prepare_capture import load_sensor_profile, normalize_imu_sample
from .stream_worker import NativeVioStreamWorker


@dataclass(frozen=True)
class LiveFrameTicket:
    worker_run_id: str
    sequence: int
    timestamp_ns: int
    pixel_sha256: str
    image_sha256: str
    # Bytes-backed immutable pixels may be shared with a learned consumer after
    # the native worker acknowledges and removes its temporary PNG.
    pixels: np.ndarray


class NativeVioLiveWorker(NativeVioStreamWorker):
    """Finite bench session with immutable calibration and fail-stop overload.

    Only submit_frame is public input here. Unlike sealed replay, a full queue
    ends this epoch; callers cannot drop a physical sensor batch and continue.
    """

    def __init__(self, binary: Path, checkout: Path, profile_dir: Path, output: Path, *,
                 capacity=2, batch_timeout_s=5., max_clones=None, max_frames=600,
                 max_duration_s=60., max_evidence_bytes=128*1024*1024, max_frame_bytes=16*1024*1024,
                 record_images=False, max_image_bytes=1024**3, anchor_capacity=0):
        checked_max_clones(max_clones)
        if type(max_frames) is not int or not 2 <= max_frames <= 20000:
            raise ValueError("max_frames must be an integer in [2,20000]")
        if (type(max_duration_s) not in (int, float) or not math.isfinite(max_duration_s)
                or not 1 <= max_duration_s <= 1800):
            raise ValueError("max_duration_s must be in [1,1800]")
        if type(max_evidence_bytes) is not int or not 1024*1024 <= max_evidence_bytes <= 1024**3:
            raise ValueError("max_evidence_bytes must be in [1 MiB,1 GiB]")
        if type(max_frame_bytes) is not int or not 1024 <= max_frame_bytes <= 64*1024*1024:
            raise ValueError("max_frame_bytes must be in [1 KiB,64 MiB]")
        if type(record_images) is not bool:
            raise ValueError("record_images must be boolean")
        if type(max_image_bytes) is not int or not 1024 <= max_image_bytes <= 32*1024**3:
            raise ValueError("max_image_bytes must be in [1 KiB,32 GiB]")
        root, profile_dir = output.resolve(), profile_dir.resolve()
        if root.is_relative_to(profile_dir) or root.is_relative_to(checkout.resolve()):
            raise ValueError("Live output must not modify source calibration or native checkout")
        profile, camera, imu = load_sensor_profile(profile_dir)
        width, height = camera["cam0"]["resolution"]
        if width*height*3 > max_frame_bytes:
            raise ValueError("Calibrated BGR frame exceeds selected memory bound")
        source_hash = digest(profile_dir / "sensor_profile.json")
        self._initialize(binary, root / "input", root / "native", capacity=capacity,
                         batch_timeout_s=batch_timeout_s, anchor_capacity=anchor_capacity)
        self.root = root
        self._adapter_root = Path(__file__).resolve().parents[1]
        self._adapter_hashes = {name: digest(self._adapter_root / name) for name in
            ("vio/live_worker.py", "vio/stream_worker.py", "vio/anchor_control.py", "vio/prepare_capture.py",
             "vio/openvins_replay.py", "capture_timing.py")}
        self._profile, self._camera = deepcopy(profile), deepcopy(camera["cam0"])
        self._max_frames, self._max_duration, self._max_evidence = max_frames, max_duration_s, max_evidence_bytes
        self._input_lock, self._offer_lock = threading.Lock(), threading.Lock()
        self._spool, self._input_failure = {}, None
        self._frame_identities = {}
        self._record_images, self._max_image_bytes = record_images, max_image_bytes
        self._recorded_hashes, self._recorded_bytes = {}, 0
        self._events_digest = hashlib.sha256()
        self._recording = root / "recording"
        self._recording_events = self._recording / "events.jsonl"
        self._offered_frames = self._acknowledged_frames = self._peak_spool = 0
        self._imu_first = self._imu_last = self._camera_last = 0
        self._last_host_offset = self._started_ns = None
        root.mkdir(parents=True, exist_ok=False)
        self.capture.mkdir()
        prepare_config(checkout, self.capture, max_clones=max_clones)
        config = read_yaml(self.capture / "estimator_config.yaml")
        config.update(use_mask=False, gravity_mag=profile["gravity_m_s2"])
        write_yaml(self.capture / "estimator_config.yaml", config)
        write_yaml(self.capture / "kalibr_imucam_chain.yaml", camera)
        write_yaml(self.capture / "kalibr_imu_chain.yaml", imu)
        saved_profile = self.capture / "sensor_profile"
        saved_profile.mkdir()
        for name, expected in profile["checksums"].items():
            destination = saved_profile / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(profile_dir / name, destination)
            if digest(destination) != expected:
                raise ValueError("Sensor evidence changed during live setup")
        shutil.copy2(profile_dir / "sensor_profile.json", saved_profile / "sensor_profile.json")
        if digest(saved_profile / "sensor_profile.json") != source_hash or digest(profile_dir / "sensor_profile.json") != source_hash:
            raise ValueError("Sensor profile changed during live setup")
        self._fixed_hashes = {str(p.relative_to(self.capture)): digest(p)
                              for p in self.capture.rglob("*") if p.is_file()}
        self._manifest = {"schema_version": "vision_nav_native_live_input_v1",
            "worker_run_id": self.worker_run_id, "native_build": self.native_build,
            "binary_sha256": self.binary_sha256, "sensor_profile_sha256": source_hash,
            "python_adapter_sha256": self._adapter_hashes,
            "configuration_checksums": self._fixed_hashes, "max_clones": config["max_clones"],
            "bounds": {"max_frames": max_frames, "max_duration_s": max_duration_s,
                       "max_evidence_bytes": max_evidence_bytes, "max_spool_images": capacity+2,
                       "max_frame_bytes": max_frame_bytes, "max_image_bytes": max_image_bytes},
            "input_policy": "single_sensor_owner_ordered_batches_fail_stop_on_overflow",
            "anchor_control_capacity": anchor_capacity,
            "clock_policy": "bracketed_fit_only_no_extrapolation",
            "image_storage": ("retained_png_hardlinks_and_exact_native_events" if record_images else
                              "temporary_png_until_native_ack_external_capture_needed_for_replay"),
            "sensor_qualification": "operator_supplied_evidence_not_independently_verified",
            "position_authority": False, "px4_external_vision": "disabled"}
        (self.capture / "live_input.json").write_text(json.dumps(self._manifest, indent=2)+"\n")
        self._manifest_hash = digest(self.capture / "live_input.json")
        (self.capture / "images").mkdir()
        self._journal = self.capture / "live_frames.jsonl"
        if record_images:
            (self._recording / "images").mkdir(parents=True)
            self._recording_events.touch(exist_ok=False)

    def start(self):
        if self._started_ns is not None:
            raise RuntimeError("Live VIO worker cannot be restarted")
        self._verify_fixed(include_binary=True)
        self._started_ns = time.monotonic_ns()
        super().start()

    def submit(self, events):
        raise RuntimeError("Live input requires submit_frame with calibration, exposure and raw IMU evidence")

    def request_anchor(self, *args, **kwargs):
        raise RuntimeError("Live anchors require retain_frame/release_frame with an owned frame ticket")

    def _frame_anchor(self, ticket, operation):
        if (not isinstance(ticket, LiveFrameTicket) or ticket.worker_run_id != self.worker_run_id
                or type(ticket.timestamp_ns) is not int or type(ticket.sequence) is not int):
            raise ValueError("Anchor ticket belongs to a different live worker epoch")
        with self._input_lock:
            identity = self._frame_identities.get(ticket.timestamp_ns)
        if identity != (ticket.sequence, ticket.pixel_sha256, ticket.image_sha256):
            raise ValueError("Anchor ticket was not issued for this captured frame")
        return super().request_anchor(operation, ticket.timestamp_ns, worker_run_id=ticket.worker_run_id)

    def retain_frame(self, ticket):
        """Request native retention of an issued frame, not a copied pose."""
        return self._frame_anchor(ticket, "retain")

    def release_frame(self, ticket):
        """Release exactly the issued frame's anchor; require its acknowledgement."""
        return self._frame_anchor(ticket, "release")

    def _fail_input(self, error):
        with self._input_lock:
            self._input_failure = self._input_failure or f"{type(error).__name__}:{error}"[:1000]
        self._stop.set()
        with self._condition:
            self._condition.notify_all()

    def submit_frame(self, frame: CapturedFrame, imu_samples: list[dict], *,
                     calibration_id: str, clock_fit: Px4ClockFit) -> LiveFrameTicket:
        """Copy one immutable frame and offer exactly the new bracketing IMU samples.

        The owner must buffer until a subsequent TIMESYNC exchange brackets this
        exposure. No unbounded waiting, clock extrapolation or hidden sensor
        drop is performed here. A validation/overrun failure ends the epoch.
        """
        if not self._offer_lock.acquire(blocking=False):
            error = RuntimeError("Concurrent sensor producers are not allowed")
            self._fail_input(error)
            raise error
        try:
            state = super().status()
            if self._started_ns is None or state["input_closed"] or not state["thread_alive"] or self._stop.is_set():
                raise RuntimeError("Live sensor input is not accepting frames")
            self._check_input()
            if self._offered_frames >= self._max_frames:
                raise ValueError("Live frame limit reached; finish this session before new input")
            if (not isinstance(frame, CapturedFrame) or frame.backend != "picamera2"
                    or calibration_id != self._profile["camera_calibration_id"]):
                raise ValueError("Live frame requires the bound Picamera2 calibration identity")
            if (not isinstance(clock_fit, Px4ClockFit)
                    or clock_fit.source != (self._profile["source_system"], self._profile["source_component"])):
                raise ValueError("Live exposure clock fit must belong to selected IMU sender")
            observation = camera_exposure_time(frame.to_dict(), TimingPolicy())
            if observation["status"] != "available":
                raise ValueError("Live exposure rejected: " + observation["reason"])
            converted = clock_fit.convert(observation["host_monotonic_ns"], observation["clock_uncertainty_ns"])
            if converted["status"] != "available":
                raise ValueError("Live clock conversion rejected: " + converted["reason"])
            if converted["clock_uncertainty_ns"] > TimingPolicy().max_conversion_uncertainty_ms*1e6:
                raise ValueError("Live clock uncertainty exceeds native policy")
            offset = observation["boottime_minus_monotonic_ns"]
            if (self._last_host_offset is not None and abs(offset-self._last_host_offset) >
                    TimingPolicy().max_host_offset_step_ms*1e6+observation["clock_uncertainty_ns"]):
                raise ValueError("Live host suspend/clock step requires a new epoch")
            stamp = converted["px4_timestamp_ns"]
            if self._camera_last and not 0 < stamp-self._camera_last <= 250_000_000:
                raise ValueError("Live camera reordered or gap exceeds 250 ms")
            if not isinstance(imu_samples, list) or not 1 <= len(imu_samples) <= 1023:
                raise ValueError("Live batch requires 1..1023 new raw IMU samples")
            events, last, first = [], self._imu_last, self._imu_first
            for sample in imu_samples:
                event = normalize_imu_sample(sample, self._profile)
                if event is None:
                    raise ValueError("Live batch contains a different sensor or non-IMU message")
                ns = event["timestamp_ns"]
                if last and not 0 < ns-last <= 50_000_000:
                    raise ValueError("Live raw IMU reordered or gap exceeds 50 ms")
                first, last = first or ns, ns
                events.append(event)
            target = stamp+round(self._camera["timeshift_cam_imu"]*1e9)
            uncertainty = converted["clock_uncertainty_ns"]
            if first > target-uncertainty or not target+uncertainty+5_000_000 <= last <= target+150_000_000:
                raise ValueError("Live camera lacks bounded bracketing raw IMU coverage")
            image = frame.bgr
            width, height = self._camera["resolution"]
            if (not isinstance(image, np.ndarray) or image.dtype != np.uint8
                    or image.shape not in {(height, width), (height, width, 3)}):
                raise ValueError("Live pixels differ from calibrated uint8 gray/BGR dimensions")
            pixels = np.frombuffer(image.tobytes(), dtype=np.uint8).reshape(image.shape)
            pixel_hash = hashlib.sha256(pixels.tobytes()).hexdigest()
            ok, encoded = cv2.imencode(".png", pixels)
            if not ok:
                raise RuntimeError("Native temporary image encoding failed")
            payload = encoded.tobytes()
            image_hash = hashlib.sha256(payload).hexdigest()
            sequence = self._offered_frames
            relative = f"images/{sequence:06d}.png"
            events.append({"type": "camera", "timestamp_ns": stamp, "image_path": relative})
            receipt = {"record_type": "offered", "worker_run_id": self.worker_run_id, "sequence": sequence,
                "timestamp_ns": stamp, "image_path": relative, "image_sha256": image_hash,
                "pixel_sha256": pixel_hash, "pixel_shape": list(pixels.shape), "pixel_format": "gray" if pixels.ndim == 2 else "BGR",
                "camera_clock": observation, "px4_clock": converted,
                "clock_fit": {"host_origin_ns": clock_fit.host_origin_ns, "offset_at_origin_ns": clock_fit.offset_at_origin_ns,
                    "offset_slope": clock_fit.offset_slope, "envelope_ns": clock_fit.envelope_ns,
                    "host_interval_ns": [clock_fit.host_samples_ns[0], clock_fit.host_samples_ns[-1]]},
                "imu_count": len(events)-1, "imu_first_timestamp_ns": events[0]["timestamp_ns"],
                "imu_last_timestamp_ns": last, "offered_host_monotonic_ns": time.monotonic_ns(),
                "batch_sha256": hashlib.sha256(json.dumps(events, sort_keys=True, allow_nan=False).encode()).hexdigest(),
                "position_authority": False}
            with self._input_lock:
                if len(self._spool) >= self.capacity+2:
                    raise RuntimeError("Live temporary image capacity exceeded")
                if self._record_images and self._recorded_bytes+len(payload) > self._max_image_bytes:
                    raise ValueError("Live recording image byte limit exceeded")
                path = self.capture / relative
                with path.open("xb") as stream:
                    stream.write(payload)
                self._spool[relative] = receipt
                self._peak_spool = max(self._peak_spool, len(self._spool))
                if self._record_images:
                    # Same-filesystem hard link: retain the exact native PNG,
                    # without another encoding or second copy of its pixels.
                    (self._recording / relative).hardlink_to(path)
                    self._recorded_hashes[relative] = image_hash
                    self._recorded_bytes += len(payload)
                    encoded_events = "".join(json.dumps(e, sort_keys=True, allow_nan=False)+"\n" for e in events).encode()
                    with self._recording_events.open("ab") as stream:
                        stream.write(encoded_events)
                    self._events_digest.update(encoded_events)
                self._append(receipt)
            if not super().submit(events):
                raise RuntimeError("Live native queue overflow: epoch stopped, no raw IMU retry/drop")
            if self.anchor_capacity:
                with self._input_lock:
                    # Metadata only, bounded by this finite session's max_frames.
                    self._frame_identities[stamp] = (sequence, pixel_hash, image_hash)
            self._offered_frames += 1
            self._imu_first, self._imu_last, self._camera_last = first, last, stamp
            self._last_host_offset = offset
            return LiveFrameTicket(self.worker_run_id, sequence, stamp, pixel_hash, image_hash, pixels)
        except Exception as exc:
            self._fail_input(exc)
            raise
        finally:
            self._offer_lock.release()

    def _append(self, row):
        with self._journal.open("a") as stream:
            stream.write(json.dumps(row, sort_keys=True, allow_nan=False)+"\n")

    def _validate_image_reference(self, event):
        with self._input_lock:
            receipt = self._spool.get(event.get("image_path"))
            if receipt is None or receipt["timestamp_ns"] != event["timestamp_ns"]:
                raise ValueError("Camera is not owned by the live image spool")

    def _verify_fixed(self, *, include_binary=False):
        if ((include_binary and digest(self.binary) != self.binary_sha256)
                or digest(self.capture / "live_input.json") != self._manifest_hash):
            raise ValueError("Live native binary or input manifest changed")
        if include_binary:
            for name, expected in self._adapter_hashes.items():
                if digest(self._adapter_root / name) != expected:
                    raise ValueError("Live adapter source changed during this session")
        for relative, expected in self._fixed_hashes.items():
            if digest(self.capture / relative) != expected:
                raise ValueError("Live sensor configuration changed; a new epoch is required")

    def _check_input(self):
        if self._started_ns is not None and (time.monotonic_ns()-self._started_ns)/1e9 > self._max_duration:
            raise TimeoutError("Live session duration exceeded")
        total = sum(p.stat().st_size for p in (self._journal, self.output / "local_states.jsonl",
                    self.output / "native.log", self._recording_events, self.output / "owned_stream.jsonl",
                    self.output / "anchor_acknowledgements.jsonl") if p.exists())
        if total > self._max_evidence:
            raise ValueError("Live evidence byte limit exceeded")

    def _check_wait(self, deadline):
        self._check_input()
        super()._check_wait(deadline)

    def _before_batch(self, payload):
        self._verify_fixed()
        event = json.loads(payload.splitlines()[-1])
        self._validate_image_reference(event)
        with self._input_lock:
            if digest(self.capture / event["image_path"]) != self._spool[event["image_path"]]["image_sha256"]:
                raise ValueError("Live frame changed before native consumption")

    def _after_batch(self, payload, record):
        self._check_input()
        event = json.loads(payload.splitlines()[-1])
        with self._input_lock:
            receipt = self._spool[event["image_path"]]
            path = self.capture / event["image_path"]
            if digest(path) != receipt["image_sha256"]:
                raise ValueError("Live frame changed during native consumption")
            self._append({"record_type": "acknowledged", "sequence": receipt["sequence"],
                "timestamp_ns": record["timestamp_ns"], "worker_run_id": self.worker_run_id,
                "image_sha256": receipt["image_sha256"], "pixel_sha256": receipt["pixel_sha256"],
                "acknowledged_host_monotonic_ns": time.monotonic_ns(), "position_authority": False})
            path.unlink()
            del self._spool[event["image_path"]]
            self._acknowledged_frames += 1

    def status(self):
        result = super().status()
        with self._input_lock:
            result["live_input"] = {"schema_version": "vision_nav_native_live_input_status_v1",
                "offered_frames": self._offered_frames, "acknowledged_frames": self._acknowledged_frames,
                "spool_images": len(self._spool), "peak_spool_images": self._peak_spool,
                "record_images": self._record_images, "recorded_images": len(self._recorded_hashes),
                "recorded_image_bytes": self._recorded_bytes, "max_image_bytes": self._max_image_bytes,
                "input_failure": self._input_failure, "position_authority": False}
        return result

    def _verify_recording(self):
        if not self._record_images:
            return
        paths = list(self._recording.rglob("*"))
        if self._recording.is_symlink() or any(p.is_symlink() for p in paths):
            raise ValueError("Live recording contains symlinks")
        expected = {**self._recorded_hashes, "events.jsonl": self._events_digest.hexdigest()}
        actual = {str(p.relative_to(self._recording)) for p in paths if p.is_file()}
        if actual != set(expected):
            raise ValueError("Live recording file inventory changed")
        for name, sha in expected.items():
            if digest(self._recording / name) != sha:
                raise ValueError("Live recording checksum changed: " + name)

    def _finalize_input(self):
        # An interrupted offer may still be encoding its owned image. Complete
        # that handoff before removing spool files or sealing terminal evidence.
        with self._offer_lock, self._input_lock:
            unacknowledged = list(self._spool)
            for name in unacknowledged:
                (self.capture / name).unlink(missing_ok=True)
            self._spool.clear()
            failure = self._input_failure
        try:
            self._verify_fixed(include_binary=True)
            self._verify_recording()
        except Exception as exc:
            failure = failure or f"final_input_verification_failed:{type(exc).__name__}:{exc}"
        if failure:
            with self._condition:
                self._state, self._error = "failed", failure
        status = self.status()
        recording = {"enabled": self._record_images, "status": "disabled", "complete": False}
        if self._record_images:
            complete = (not failure and not unacknowledged and status["input_closed"]
                and status["status"] in {"complete", "no_local_estimate"}
                and 2 <= self._acknowledged_frames == self._offered_frames == len(self._recorded_hashes)
                and self._acknowledged_frames == status["completed_batch_count"])
            recording.update(status="complete" if complete else "partial_or_invalid", complete=complete,
                image_count=len(self._recorded_hashes), image_bytes=self._recorded_bytes,
                max_image_bytes=self._max_image_bytes,
                checksums={**self._recorded_hashes, "events.jsonl": self._events_digest.hexdigest()},
                durability="closed_files_with_hashes_not_power_loss_certified",
                training_qualification="not_established")
        report = {"schema_version": "vision_nav_native_live_report_v1", "worker": status,
            "batch_timings": self.timing_snapshot(),
            "recording": recording,
            "report_scope": "terminal_status_written_before_worker_thread_returns",
            "input_manifest_sha256": self._manifest_hash, "unacknowledged_images": unacknowledged,
            "frames_journal_sha256": digest(self._journal) if self._journal.exists() else None,
            "native_states_sha256": digest(self.output / "local_states.jsonl") if (self.output / "local_states.jsonl").exists() else None,
            "anchor_control_report_sha256": digest(self.output / "anchor_control_report.json") if self.anchor_capacity else None,
            "position_authority": False, "px4_external_vision": "disabled"}
        (self.root / "live_report.json").write_text(json.dumps(report, indent=2)+"\n")
