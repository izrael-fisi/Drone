"""Continuous local BEV-to-satellite feature-map correlation port."""

from __future__ import annotations

from dataclasses import dataclass
import numpy as np

from ..types import GeometricEstimate, GlobalObservation, Pose3


@dataclass(frozen=True)
class FeatureMapPatch:
    tile_id: str
    center_enu_m: np.ndarray
    features: np.ndarray  # C,H,W unit-normalized satellite feature map
    metres_per_cell: float


class NumpyFeatureMapCorrelator:
    """Reference implementation for a production CUDA/TensorRT correlation kernel.

    The BEV builder must emit a C,H,W feature tensor in the same learned feature space.
    Search is restricted to active-tile neighbourhoods. The peak and peak separation are
    translated into an observation covariance, then still pass the hypothesis gate.
    """
    def __init__(self, patches: dict[str, FeatureMapPatch], min_peak: float = .65) -> None:
        self.patches, self.min_peak = patches, min_peak

    def correlate(self, bev: np.ndarray, active_tile_id: str | None) -> list[GlobalObservation]:
        candidates = [self.patches[active_tile_id]] if active_tile_id in self.patches else self.patches.values()
        result: list[GlobalObservation] = []
        q = self._normalize(bev).reshape(-1)
        for patch in candidates:
            # Placeholder assumes pre-pooled aligned map patch; production performs FFT/conv
            # over translation, yaw, and scale cells and returns peak coordinates.
            score = float(np.dot(q, self._normalize(patch.features).reshape(-1)) /
                          max(1, q.size))
            valid = score >= self.min_peak
            sigma_m = max(.5, (1 - score) * 25.)
            estimate = GeometricEstimate(
                patch.tile_id,
                Pose3(patch.center_enu_m, np.array([0., 0., 0., 1.])) if valid else None,
                "bev_correlation", np.ones(32, dtype=bool) if valid else np.zeros(32, dtype=bool),
                max(.1, (1 - score) * 3.), np.eye(6) * sigma_m ** 2, valid)
            result.append(GlobalObservation(0., estimate, score, score))
        return result

    @staticmethod
    def _normalize(array: np.ndarray) -> np.ndarray:
        return array / max(float(np.linalg.norm(array)), 1e-12)
