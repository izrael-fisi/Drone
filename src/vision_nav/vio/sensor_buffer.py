"""Bounded exposure/clock/raw-IMU rendezvous for one live sensor epoch."""

from bisect import bisect_left
from collections import deque
from copy import deepcopy
from dataclasses import replace
import math
import threading
import time

import numpy as np

from ..camera_ingress import CapturedFrame
from ..capture_timing import TimingPolicy, camera_exposure_time, fit_px4_clock
from .prepare_capture import normalize_imu_sample


class LiveSensorBuffer:
    """Two resource producers, one batch consumer; faults latch until replaced.

    Warmup IMU eviction is counted before camera arming. Once armed, no IMU or
    camera queue overflow is hidden. Only pre-first-exposure IMU is trimmed
    explicitly when the initial native batch is formed.
    """

    def __init__(self, profile, camera, *, imu_capacity=4096, frame_capacity=16,
                 frame_bytes=64*1024*1024, clock_capacity=32, wait_ms=1000.,
                 clock_ns=time.monotonic_ns):
        for key, value, minimum, maximum in (("imu_capacity", imu_capacity, 32, 16384),
                ("frame_capacity", frame_capacity, 1, 64), ("frame_bytes", frame_bytes, 1024, 256*1024*1024),
                ("clock_capacity", clock_capacity, 6, 256)):
            if type(value) is not int or not minimum <= value <= maximum:
                raise ValueError(f"{key} must be an integer in [{minimum},{maximum}]")
        if type(wait_ms) not in (int, float) or not math.isfinite(wait_ms) or not 50 <= wait_ms <= 2000:
            raise ValueError("wait_ms must be in [50,2000]")
        self.profile, self.camera = deepcopy(profile), deepcopy(camera)
        self.policy = TimingPolicy()
        self._clock, self._wait_ns = clock_ns, round(wait_ms*1e6)
        self._imu_cap, self._frame_cap, self._bytes_cap, self._clock_cap = imu_capacity, frame_capacity, frame_bytes, clock_capacity
        self._imu, self._frames, self._exchanges = deque(), deque(), deque(maxlen=clock_capacity)
        self._attitudes = deque(maxlen=256)
        self._attitude_error = None
        self._lock = threading.RLock()
        self._error = None
        self._armed = False
        self._fit = None
        self._fit_report = {"status": "waiting", "errors": ["insufficient_clock_samples"]}
        self._last_imu = self._last_imu_host = self._last_frame_host = self._last_sensor = None
        self._last_offset = None
        self._last_clock_host = None
        self._previous_clock = None
        self._native_started = False
        self._bytes = self._accepted_imu = self._filtered = self._accepted_frames = self._dispatched = 0
        self._warmup_discarded = self._initial_discarded = self._invalid_exchanges = self._peak_frames = self._peak_bytes = 0

    def fail(self, reason):
        with self._lock:
            self._error = self._error or str(reason)[:1000]
            self._frames.clear()
            self._imu.clear()
            self._attitudes.clear()
            self._bytes = 0

    def _raise(self, reason):
        self.fail(reason)
        raise ValueError(reason)

    def _healthy(self):
        if self._error:
            raise RuntimeError(self._error)

    def push_telemetry(self, sample, *, host_ns=None):
        with self._lock:
            self._healthy()
            try:
                if sample.get("message_type") == "ATTITUDE":
                    self._push_attitude(sample)
                event = normalize_imu_sample(sample, self.profile)
                if event is None:
                    self._filtered += 1
                    return False
                host = self._clock() if host_ns is None else host_ns
                if type(host) is not int or host <= 0 or self._last_imu_host is not None and host < self._last_imu_host:
                    self._raise("raw_imu_receive_clock_reversed")
                stamp = event["timestamp_ns"]
                if self._last_imu is not None and not 0 < stamp-self._last_imu <= 50_000_000:
                    self._raise("raw_imu_reordered_or_gapped")
                if len(self._imu) >= self._imu_cap:
                    if self._armed:
                        self._raise("raw_imu_buffer_overflow")
                    self._imu.popleft()
                    self._warmup_discarded += 1
                self._imu.append((event, deepcopy(sample)))
                self._last_imu, self._last_imu_host = stamp, host
                self._accepted_imu += 1
                return True
            except Exception as exc:
                self.fail(exc)
                raise

    def _push_attitude(self, sample):
        source = (sample.get("source_system"), sample.get("source_component"))
        if source != (self.profile["source_system"], self.profile["source_component"]):
            return
        stamp = sample.get("vehicle_timestamp_us")
        angles = [sample.get(k) for k in ("roll_rad", "pitch_rad", "yaw_rad")]
        if (any(type(v) is not int for v in source) or type(stamp) is not int or stamp <= 0
                or sample.get("vehicle_time_domain") != "px4_boot_us"
                or not all(type(v) in (float, int) and math.isfinite(v) for v in angles)):
            self._attitude_error = "invalid_selected_attitude"
        elif self._attitudes and stamp*1000 <= self._attitudes[-1][0]:
            self._attitude_error = "attitude_clock_reset_duplicate_or_reordering"
        elif not self._attitude_error:
            self._attitudes.append((stamp*1000, angles))

    def _learned_context(self, observation, converted):
        # Auxiliary attitude never substitutes for raw IMU or blocks VIO.
        # Match training's exposure-clock nearest ATTITUDE convention, not
        # receipt time or the estimator's separately calibrated IMU delay.
        result = {"status": "rejected", "reason": self._attitude_error,
            "camera": deepcopy(observation), "conversion": deepcopy(converted),
            "calibration_id": self.profile.get("camera_calibration_id"),
            "source_system": self.profile["source_system"], "source_component": self.profile["source_component"],
            "native_camera_imu_delay_s": self.camera["timeshift_cam_imu"],
            "policy": "exposure_native_attitude_v1", "position_authority": False}
        if self._attitude_error:
            return result
        if not self._attitudes:
            return {**result, "reason": "compatible_native_attitude_missing"}
        target = converted["px4_timestamp_ns"]
        stamp, angles = min(self._attitudes, key=lambda item: abs(item[0]-target))
        envelope = abs(stamp-target)+converted["clock_uncertainty_ns"]+1_000_000
        if envelope > self.policy.max_attitude_delta_ms*1e6:
            return {**result, "reason": "native_attitude_age_exceeds_policy"}
        return {**result, "status": "paired", "reason": None,
            "attitude_timestamp_us": stamp//1000, "attitude_rpy_rad": list(angles),
            "total_timing_envelope_ms": envelope/1e6, "interpolation": "none_nearest_native_sample"}

    def push_exchange(self, exchange):
        with self._lock:
            self._healthy()
            source = (exchange.get("source_system"), exchange.get("source_component"))
            target = (self.profile["source_system"], self.profile["source_component"])
            if source != target or any(type(v) is not int for v in source):
                self._invalid_exchanges += 1
                return
            values = [exchange.get(k) for k in ("host_sent_monotonic_ns", "host_received_monotonic_ns", "px4_timestamp_ns")]
            if any(type(v) is not int or v <= 0 for v in values) or values[1] < values[0]:
                self._raise("malformed_selected_clock_exchange")
            sent, received, remote = values
            if received-sent > self.policy.max_round_trip_ms*1e6:
                self._invalid_exchanges += 1
                return
            midpoint = (sent+received)//2
            if self._previous_clock and (midpoint <= self._previous_clock[0] or remote <= self._previous_clock[1]):
                self._raise("selected_clock_reboot_or_reordering")
            self._previous_clock = (midpoint, remote)
            self._exchanges.append(deepcopy(exchange))
            fit, report = fit_px4_clock(list(self._exchanges), self.policy)
            self._fit_report = report
            # Only insufficient initial history is an expected warmup condition.
            if fit is None and set(report["errors"]) - {"insufficient_clock_samples", "clock_fit_span_too_short"}:
                self._raise("clock_fit_rejected:"+",".join(report["errors"]))
            self._fit = fit
            self._last_clock_host = self._clock()

    def ready(self):
        with self._lock:
            self._healthy()
            now = self._clock()
            return (self._fit is not None and bool(self._imu) and self._last_imu_host is not None
                    and 0 <= now-self._last_imu_host <= self._wait_ns
                    and self._last_clock_host is not None and 0 <= now-self._last_clock_host <= self._wait_ns)

    def arm(self):
        with self._lock:
            if self._armed or not self.ready():
                raise RuntimeError("Camera arming requires fresh raw IMU and a valid clock fit")
            self._armed = True

    def push_frame(self, frame):
        # Copy before entering the buffer lock so raw IMU reads do not wait for
        # a large image copy. Input comes only from the camera-owner thread.
        try:
            if not isinstance(frame, CapturedFrame) or frame.backend != "picamera2":
                raise ValueError("ordered_capture_requires_picamera2")
            width, height = self.camera["resolution"]
            image = frame.bgr
            if (not isinstance(image, np.ndarray) or image.dtype != np.uint8
                    or image.shape not in {(height, width), (height, width, 3)} or image.nbytes > self._bytes_cap):
                raise ValueError("ordered_capture_dimensions_or_memory_invalid")
            image = np.frombuffer(image.tobytes(), dtype=np.uint8).reshape(image.shape)
            owned = replace(frame, bgr=image, host_clock_samples=deepcopy(frame.host_clock_samples), quality=deepcopy(frame.quality))
            observation = camera_exposure_time(owned.to_dict(), self.policy)
            if observation["status"] != "available":
                raise ValueError("ordered_capture_clock:"+observation["reason"])
            with self._lock:
                self._healthy()
                if not self._armed:
                    self._raise("camera_started_before_sensor_arming")
                sensor = observation["sensor_timestamp_ns"]
                if self._last_sensor is not None and not 0 < sensor-self._last_sensor <= 250_000_000:
                    self._raise("camera_reordered_or_gapped")
                offset = observation["boottime_minus_monotonic_ns"]
                if self._last_offset is not None and abs(offset-self._last_offset) > self.policy.max_host_offset_step_ms*1e6+observation["clock_uncertainty_ns"]:
                    self._raise("camera_host_clock_step")
                if len(self._frames) >= self._frame_cap or self._bytes+image.nbytes > self._bytes_cap:
                    self._raise("ordered_camera_buffer_overflow")
                now = self._clock()
                self._frames.append((owned, observation, now))
                self._bytes += image.nbytes
                self._peak_frames, self._peak_bytes = max(self._peak_frames, len(self._frames)), max(self._peak_bytes, self._bytes)
                self._last_frame_host, self._last_sensor, self._last_offset = now, sensor, offset
                self._accepted_frames += 1
        except Exception as exc:
            self.fail(exc)
            raise

    def take_batch(self):
        """Consume exactly one ready frame and new IMU; None means bounded wait."""
        with self._lock:
            self.check_deadlines()
            now = self._clock()
            if not self._frames:
                return None
            frame, observation, arrival = self._frames[0]
            if not 0 <= now-arrival <= self._wait_ns:
                self._raise("camera_bracketing_wait_exceeded")
            if self._fit is None:
                return None
            converted = self._fit.convert(observation["host_monotonic_ns"], observation["clock_uncertainty_ns"])
            if converted["status"] != "available":
                if (converted["reason"] == "outside_clock_fit_interval"
                        and observation["host_monotonic_ns"]+observation["clock_uncertainty_ns"] > self._fit.host_samples_ns[-1]):
                    return None
                self._raise("camera_clock_conversion:"+converted["reason"])
            target = converted["px4_timestamp_ns"]+round(self.camera["timeshift_cam_imu"]*1e9)
            uncertainty = converted["clock_uncertainty_ns"]
            times = [row[0]["timestamp_ns"] for row in self._imu]
            end = bisect_left(times, target+uncertainty+5_000_000)
            if end >= len(times):
                return None
            if not self._native_started:
                # The native epoch starts here. Discard only warmup samples well
                # before the first exposure, retaining a bracketing predecessor.
                start = max(0, bisect_left(times, target-uncertainty-20_000_000)-1)
                if times[start] > target-uncertainty:
                    self._raise("initial_exposure_missing_preceding_imu")
                for _ in range(start):
                    self._imu.popleft()
                self._initial_discarded += start
                end -= start
            if end+1 > 1023 or times[-1] < target or self._imu[end][0]["timestamp_ns"] > target+150_000_000:
                self._raise("native_batch_imu_envelope_invalid")
            samples = [self._imu.popleft()[1] for _ in range(end+1)]
            self._frames.popleft()
            self._bytes -= frame.bgr.nbytes
            self._native_started = True
            self._dispatched += 1
            return {"frame": frame, "imu_samples": samples, "clock_fit": self._fit,
                    "learned_context": self._learned_context(observation, converted),
                    "clock_report": deepcopy(self._fit_report), "wait_ms": (now-arrival)/1e6,
                    "position_authority": False}

    def check_deadlines(self):
        with self._lock:
            self._healthy()
            now = self._clock()
            if self._armed and self._last_imu_host is not None and not 0 <= now-self._last_imu_host <= self._wait_ns:
                self._raise("raw_imu_reception_stale")
            if self._frames and not 0 <= now-self._frames[0][2] <= self._wait_ns:
                self._raise("camera_bracketing_wait_exceeded")

    def status(self):
        with self._lock:
            return {"schema_version": "vision_nav_live_sensor_buffer_v1", "armed": self._armed, "error": self._error,
                "pending_imu": len(self._imu), "pending_frames": len(self._frames), "pending_frame_bytes": self._bytes,
                "imu_capacity": self._imu_cap, "frame_capacity": self._frame_cap, "frame_bytes_limit": self._bytes_cap,
                "clock_samples": len(self._exchanges), "clock_capacity": self._clock_cap,
                "attitude_samples": len(self._attitudes), "attitude_capacity": 256,
                "learned_attitude_error": self._attitude_error,
                "clock_status": self._fit_report["status"], "clock_errors": self._fit_report.get("errors", []),
                "accepted_imu": self._accepted_imu, "filtered_telemetry": self._filtered, "accepted_frames": self._accepted_frames,
                "dispatched_frames": self._dispatched, "warmup_imu_discarded": self._warmup_discarded,
                "initial_imu_discarded": self._initial_discarded, "invalid_clock_exchanges": self._invalid_exchanges,
                "peak_frames": self._peak_frames, "peak_frame_bytes": self._peak_bytes,
                "wait_limit_ms": self._wait_ns/1e6, "position_authority": False}
