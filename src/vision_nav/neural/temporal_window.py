"""Bounded, host-monotonic camera/PX4-attitude windows for temporal models."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any, Callable

import numpy as np


FRAME_WINDOW_RGB_SOURCE = "frame_window_rgb"
ATTITUDE_WINDOW_RPY_SOURCE = "attitude_window_rpy"


@dataclass(frozen=True)
class AttitudeRecord:
    host_monotonic_ns: int
    roll_rad: float
    pitch_rad: float
    yaw_rad: float


@dataclass(frozen=True)
class TemporalFrame:
    bgr: np.ndarray
    host_monotonic_ns: int
    attitude_rpy: np.ndarray
    attitude_delta_ms: float


class TemporalWindowBuilder:
    """Maintains only aligned evidence; it never interpolates or invents attitude."""

    def __init__(self, *, window_size: int, max_attitude_frame_delta_ms: float = 50.0,
                 max_frame_gap_ms: float = 1500.0) -> None:
        if type(window_size) is not int or not 1 <= window_size <= 64:
            raise ValueError("Temporal window_size must be an integer from 1 to 64.")
        for value in (max_attitude_frame_delta_ms, max_frame_gap_ms):
            if type(value) not in (int, float) or not np.isfinite(value) or value <= 0:
                raise ValueError("Temporal timing limits must be finite and positive.")
        self.window_size = window_size
        self.max_attitude_frame_delta_ms = max_attitude_frame_delta_ms
        self.max_frame_gap_ms = max_frame_gap_ms
        self._last_frame_ns: int | None = None
        self._attitudes: deque[AttitudeRecord] = deque(maxlen=window_size * 32)
        self._frames: deque[TemporalFrame] = deque(maxlen=window_size)
        self._last_observation: dict[str, Any] = {"status": "empty"}

    def push_attitude(self, *, host_monotonic_ns: int, roll_rad: float | None, pitch_rad: float | None, yaw_rad: float | None) -> None:
        values = (roll_rad, pitch_rad, yaw_rad)
        if (type(host_monotonic_ns) is not int or host_monotonic_ns <= 0
                or any(type(value) not in (int, float) or not np.isfinite(value) for value in values)):
            return
        record = AttitudeRecord(host_monotonic_ns, float(roll_rad), float(pitch_rad), float(yaw_rad))
        if self._attitudes and record.host_monotonic_ns <= self._attitudes[-1].host_monotonic_ns:
            return
        self._attitudes.append(record)

    def observe_frame(self, bgr: np.ndarray, *, host_monotonic_ns: int) -> dict[str, Any]:
        if type(host_monotonic_ns) is not int or host_monotonic_ns <= 0:
            return self.invalidate("invalid_frame_monotonic_timestamp")
        if self._last_frame_ns is not None and host_monotonic_ns <= self._last_frame_ns:
            return self.invalidate("nonmonotonic_frame_timestamp")
        gap_reset = (self._last_frame_ns is not None and
                     host_monotonic_ns - self._last_frame_ns > self.max_frame_gap_ms * 1e6)
        self._last_frame_ns = host_monotonic_ns
        if gap_reset:
            self._frames.clear()
        if (not isinstance(bgr, np.ndarray) or bgr.ndim != 3 or bgr.shape[2] != 3
                or min(bgr.shape[:2]) <= 0 or bgr.dtype != np.uint8):
            return self.invalidate("invalid_temporal_frame_image")
        if not self._attitudes:
            return self.invalidate("no_px4_attitude_samples")
        nearest = min(self._attitudes, key=lambda item: abs(item.host_monotonic_ns - host_monotonic_ns))
        delta_ms = abs(nearest.host_monotonic_ns - host_monotonic_ns) / 1_000_000.0
        if delta_ms > self.max_attitude_frame_delta_ms:
            self.invalidate("nearest_px4_attitude_is_stale")
            self._last_observation["attitude_delta_ms"] = round(delta_ms, 3)
            return dict(self._last_observation)
        self._frames.append(
            TemporalFrame(np.asarray(bgr).copy(), host_monotonic_ns, np.asarray([nearest.roll_rad, nearest.pitch_rad, nearest.yaw_rad], dtype=np.float32), delta_ms)
        )
        self._last_observation = {
            "status": "ready" if len(self._frames) == self.window_size else "warming_up",
            "frame_count": len(self._frames),
            "required_frame_count": self.window_size,
            "attitude_delta_ms": round(delta_ms, 3),
            "frame_timestamp_ns": host_monotonic_ns,
            "frame_timestamps_ns": [item.host_monotonic_ns for item in self._frames],
            "gap_reset": gap_reset,
            "attitude_timing_qualified": False,
        }
        return dict(self._last_observation)

    def invalidate(self, reason: str) -> dict[str, Any]:
        """Break temporal support without allowing old timestamps to restart it."""
        self._frames.clear()
        self._last_observation = {"status": "rejected", "reason": reason, "frame_count": 0}
        return dict(self._last_observation)

    def reset_attitude_context(self, reason: str) -> dict[str, Any]:
        """Discard old sensor context after loss/reset; retain the frame time watermark."""
        self._attitudes.clear()
        return self.invalidate(reason)

    def build_inputs(
        self,
        contract: dict[str, Any],
        *,
        image_preprocessor: Callable[[np.ndarray, dict[str, Any]], np.ndarray],
    ) -> tuple[dict[str, np.ndarray] | None, str | None]:
        if self._last_observation.get("status") == "rejected":
            return None, str(self._last_observation["reason"])
        if len(self._frames) < self.window_size:
            return None, "temporal_window_not_full"
        inputs: dict[str, np.ndarray] = {}
        for logical_name, spec in contract.get("inputs", {}).items():
            source = spec.get("shadow_source")
            configured_window = spec.get("window_size")
            if configured_window != self.window_size:
                return None, f"temporal_window_size_contract_mismatch:{logical_name}"
            if source == FRAME_WINDOW_RGB_SOURCE:
                frames = [image_preprocessor(item.bgr, spec) for item in self._frames]
                if any(value.shape[0] != 1 for value in frames):
                    return None, f"temporal_frame_preprocessor_batch_shape_invalid:{logical_name}"
                inputs[logical_name] = np.stack([value[0] for value in frames], axis=0)[None, ...]
            elif source == ATTITUDE_WINDOW_RPY_SOURCE:
                inputs[logical_name] = np.stack([item.attitude_rpy for item in self._frames], axis=0)[None, ...]
            else:
                return None, f"unsupported_temporal_input_source:{logical_name}"
        return inputs, None

    def evidence(self) -> dict[str, Any]:
        return dict(self._last_observation)
