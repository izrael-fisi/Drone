"""Temporal consistency gate for trace-only camera-pose candidates."""

from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Any

import numpy as np


@dataclass(frozen=True)
class ShadowCandidatePolicy:
    required_consecutive: int = 3
    max_gap_s: float = 1.5
    max_position_jump_m: float = 8.0
    history_size: int = 8


class ShadowCameraCandidateBank:
    """Require repeated PnP evidence before reporting a tracking candidate.

    This is intentionally not an estimator and does not produce a NavEstimate.
    It identifies whether the same tile has repeatedly produced a plausible
    camera pose candidate for subsequent offline review.
    """

    def __init__(self, policy: ShadowCandidatePolicy = ShadowCandidatePolicy()) -> None:
        if policy.required_consecutive < 2 or policy.max_gap_s <= 0.0 or policy.max_position_jump_m <= 0.0:
            raise ValueError("Invalid shadow candidate policy.")
        self.policy = policy
        self._tile_id: str | None = None
        self._history: deque[tuple[int, np.ndarray]] = deque(maxlen=policy.history_size)

    def update(self, *, tile_id: str, camera_position_enu_m: Any, timestamp_monotonic_ns: int) -> dict[str, Any]:
        position = np.asarray(camera_position_enu_m, dtype=np.float64).reshape(-1)
        if not tile_id or position.shape != (3,) or not np.all(np.isfinite(position)) or timestamp_monotonic_ns <= 0:
            return {"status": "rejected", "reason": "invalid_camera_candidate"}
        reason = "candidate_continues"
        if self._tile_id != tile_id:
            self._reset(tile_id)
            reason = "candidate_tile_changed"
        elif self._history:
            previous_time, previous_position = self._history[-1]
            gap_s = (timestamp_monotonic_ns - previous_time) / 1_000_000_000.0
            if gap_s <= 0.0:
                return {"status": "rejected", "reason": "nonmonotonic_candidate_timestamp"}
            if gap_s > self.policy.max_gap_s:
                self._reset(tile_id)
                reason = "candidate_gap_reset"
            elif float(np.linalg.norm(position - previous_position)) > self.policy.max_position_jump_m:
                self._reset(tile_id)
                reason = "candidate_position_jump_reset"
        self._history.append((timestamp_monotonic_ns, position))
        positions = np.stack([item[1] for item in self._history])
        count = len(self._history)
        ready = count >= self.policy.required_consecutive
        scatter = np.cov(positions.T) if count > 1 else np.full((3, 3), np.nan)
        return {
            "status": "tracking_candidate" if ready else "warming_up",
            "reason": reason,
            "tile_id": tile_id,
            "consecutive_count": count,
            "required_consecutive": self.policy.required_consecutive,
            "camera_position_enu_m": [float(value) for value in position],
            "position_authority": False,
            "position_covariance_m2": None,
            "position_sample_scatter_m2": (
                [[float(value) for value in row] for row in scatter]
                if count > 1
                else None
            ),
        }

    def _reset(self, tile_id: str) -> None:
        self._tile_id = tile_id
        self._history.clear()

    def invalidate(self) -> None:
        """A missing/ambiguous frame breaks consecutive support."""
        self._tile_id = None
        self._history.clear()
