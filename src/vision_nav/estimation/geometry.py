"""Geometry gate: learned matches are accepted only after robust model fitting."""

from __future__ import annotations

from dataclasses import dataclass
import numpy as np

from ..types import CorrespondenceSet, FramePacket, GeometricEstimate, Pose3


@dataclass(frozen=True)
class GeometryPolicy:
    min_inliers: int = 24
    min_inlier_ratio: float = 0.30
    max_rmse_px: float = 4.0
    min_grid_coverage: float = 0.25
    # OpenCV's USAC_MAGSAC is selected where cv2 is available.
    usac_max_iterations: int = 10_000


class RobustGeometry:
    """PnP first if a tile exposes 3D; otherwise homography for locally planar nadir maps.

    This is deliberately a *gate*, not a learned-pose replacement. A production adapter
    supplies target 3D map points and calls solvePnPRansac(USAC_MAGSAC) where applicable.
    """
    def __init__(self, policy: GeometryPolicy = GeometryPolicy()) -> None:
        self.policy = policy

    def estimate(self, frame: FramePacket, tile_id: str, matches: CorrespondenceSet) -> GeometricEstimate:
        n = len(matches.confidence)
        if n < 4:
            return self._invalid(tile_id, n, "homography")
        try:
            import cv2  # optional accelerated production dependency
            h, mask = cv2.findHomography(
                matches.source_uv.astype(np.float32), matches.target_uv.astype(np.float32),
                method=cv2.USAC_MAGSAC, ransacReprojThreshold=self.policy.max_rmse_px,
                maxIters=self.policy.usac_max_iterations, confidence=0.999,
            )
            mask = mask.reshape(-1).astype(bool) if mask is not None else np.zeros(n, dtype=bool)
            if h is None:
                return self._invalid(tile_id, n, "homography")
            residual = self._homography_residual(h, matches.source_uv, matches.target_uv, mask)
            return self._make(tile_id, mask, residual, "homography", coverage=self._grid_coverage(matches.source_uv, mask))
        except ImportError:
            # Minimal deterministic fallback for test/simulation. Never use this as a flight gate.
            mask = matches.confidence >= np.quantile(matches.confidence, 0.4)
            return self._make(tile_id, mask, 1.0, "homography", coverage=self._grid_coverage(matches.source_uv, mask))

    def estimate_pnp(self, camera_uv: np.ndarray, map_xyz: np.ndarray, camera: FramePacket,
                     tile_id: str) -> GeometricEstimate:
        """3D-aware path. map_xyz must be in ENU map coordinates."""
        if len(camera_uv) < 6:
            return self._invalid(tile_id, len(camera_uv), "pnp")
        try:
            import cv2
            ok, rvec, tvec, mask = cv2.solvePnPRansac(
                map_xyz.astype(np.float32), camera_uv.astype(np.float32), camera.camera.matrix,
                None, iterationsCount=self.policy.usac_max_iterations,
                reprojectionError=self.policy.max_rmse_px, confidence=0.999,
                flags=cv2.SOLVEPNP_AP3P,
            )
            if not ok or mask is None:
                return self._invalid(tile_id, len(camera_uv), "pnp")
            inliers = np.zeros(len(camera_uv), dtype=bool)
            inliers[mask.reshape(-1)] = True
            rotation, _ = cv2.Rodrigues(rvec)
            # tvec is map-origin in camera frame; invert to map-camera pose.
            cam_position = -rotation.T @ tvec.reshape(3)
            return self._make(tile_id, inliers, 1.0, "pnp", cam_position, self._grid_coverage(camera_uv, inliers))
        except ImportError:
            return self._invalid(tile_id, len(camera_uv), "pnp")

    def _make(self, tile_id: str, mask: np.ndarray, rmse: float, model: str,
              position: np.ndarray | None = None, coverage: float = 0.0) -> GeometricEstimate:
        count, ratio = int(mask.sum()), float(mask.mean()) if len(mask) else 0.0
        valid = (count >= self.policy.min_inliers and ratio >= self.policy.min_inlier_ratio
                 and rmse <= self.policy.max_rmse_px and coverage >= self.policy.min_grid_coverage)
        pose = Pose3(position if position is not None else np.zeros(3), np.array([0., 0., 0., 1.])) if valid else None
        return GeometricEstimate(tile_id, pose, model, mask, rmse,
                                 np.eye(6) * max(rmse, 0.1) ** 2, valid, coverage)

    def _invalid(self, tile_id: str, n: int, model: str) -> GeometricEstimate:
        return GeometricEstimate(tile_id, None, model, np.zeros(n, dtype=bool), float("inf"), np.eye(6) * 1e6, False, 0.0)

    @staticmethod
    def _grid_coverage(points: np.ndarray, mask: np.ndarray, rows: int = 4, cols: int = 4) -> float:
        selected = np.asarray(points)[np.asarray(mask, dtype=bool)]
        if len(selected) == 0:
            return 0.0
        lower = selected.min(axis=0)
        upper = selected.max(axis=0)
        span = upper - lower
        if np.any(span <= 1e-6):
            return 0.0
        normalized = (selected - lower) / span
        cells = {
            (min(rows - 1, int(point[1] * rows)), min(cols - 1, int(point[0] * cols)))
            for point in normalized
        }
        return len(cells) / float(rows * cols)

    @staticmethod
    def _homography_residual(h: np.ndarray, source: np.ndarray, target: np.ndarray, mask: np.ndarray) -> float:
        homogeneous = np.c_[source, np.ones(len(source))]
        projected = (h @ homogeneous.T).T
        projected = projected[:, :2] / projected[:, 2:3]
        error = np.linalg.norm(projected - target, axis=1)
        return float(np.sqrt(np.mean(error[mask] ** 2))) if mask.any() else float("inf")
