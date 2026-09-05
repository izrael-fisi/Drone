"""Ports: replaceable implementations must conform to these production boundaries."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Protocol, Sequence
import numpy as np

from .types import (CorrespondenceSet, FramePacket, GeometricEstimate, GlobalObservation,
                    NavEstimate, RetrievalCandidate, VisualOdometry)


@dataclass(frozen=True)
class CompatibilityReport:
    """Version compatibility checked at startup and every map/model switch."""

    compatible: bool
    reasons: tuple[str, ...] = ()
    camera_calibration_id: str | None = None
    map_version: str | None = None
    model_version: str | None = None


def validate_frame_packet(frame: FramePacket, *, require_versioned_calibration: bool = True) -> tuple[bool, str | None]:
    """Validate ingress before a frame can create an estimator factor."""

    if frame.sequence < 0:
        return False, "negative_sequence"
    if not math.isfinite(frame.timestamp_s) or frame.timestamp_s <= 0.0:
        return False, "invalid_monotonic_timestamp"
    if require_versioned_calibration and not frame.calibration_id:
        return False, "missing_calibration_id"
    if require_versioned_calibration and not frame.clock_domain:
        return False, "missing_clock_domain"
    image_shape = getattr(frame.image, "shape", None)
    if image_shape is not None and tuple(image_shape[:2]) != (frame.camera.height, frame.camera.width):
        return False, "image_calibration_dimension_mismatch"
    return True, None


def validate_nav_estimate(estimate: NavEstimate) -> tuple[bool, str | None]:
    """Shared finite-value check for publication and logging boundaries."""

    try:
        covariance = np.asarray(estimate.covariance, dtype=float)
        position = np.asarray(estimate.pose_map_body.position_enu_m, dtype=float)
        quaternion = np.asarray(estimate.pose_map_body.quaternion_xyzw, dtype=float)
    except (TypeError, ValueError):
        return False, "invalid_numeric_pose_data"
    if covariance.shape != (6, 6) or not np.all(np.isfinite(covariance)):
        return False, "invalid_pose_covariance"
    if (isinstance(estimate.timestamp_s, bool)
            or not isinstance(estimate.timestamp_s, (int, float, np.integer, np.floating))
            or not math.isfinite(estimate.timestamp_s) or estimate.timestamp_s <= 0):
        return False, "invalid_monotonic_timestamp"
    if position.shape != (3,) or quaternion.shape != (4,):
        return False, "invalid_pose_shape"
    if not np.all(np.isfinite(position)) or not np.all(np.isfinite(quaternion)):
        return False, "nonfinite_pose"
    if not np.isclose(np.linalg.norm(quaternion), 1.0, atol=1e-6, rtol=0):
        return False, "nonunit_pose_quaternion"
    if np.any(np.diag(covariance) < 0.0):
        return False, "negative_pose_variance"
    if not np.allclose(covariance, covariance.T, atol=1e-12, rtol=1e-10):
        return False, "asymmetric_pose_covariance"
    eigenvalues = np.linalg.eigvalsh((covariance + covariance.T) * .5)
    if eigenvalues[0] < -1e-12 * max(1.0, float(np.max(np.abs(eigenvalues)))):
        return False, "indefinite_pose_covariance"
    return True, None


class GlobalRetriever(Protocol):
    def retrieve(self, frame: FramePacket, top_k: int) -> Sequence[RetrievalCandidate]: ...


class SparseMatcher(Protocol):
    def match(self, frame: FramePacket, tile_id: str) -> CorrespondenceSet: ...


class DenseVerifier(Protocol):
    def verify(self, frame: FramePacket, tile_id: str, seed: CorrespondenceSet) -> CorrespondenceSet: ...


class GeometryEstimator(Protocol):
    def estimate(self, frame: FramePacket, tile_id: str, matches: CorrespondenceSet) -> GeometricEstimate: ...


class VisualOdometryFrontend(Protocol):
    def track(self, frame: FramePacket) -> VisualOdometry | None: ...


class LocalBEVBuilder(Protocol):
    def update(self, frame: FramePacket, odometry: VisualOdometry | None) -> np.ndarray | None: ...


class ContinuousMapCorrelator(Protocol):
    def correlate(self, bev: np.ndarray, active_tile_id: str | None) -> Sequence[GlobalObservation]: ...


class NavigationBackend(Protocol):
    def add_odometry(self, odometry: VisualOdometry) -> None: ...
    def add_global_observation(self, observation: GlobalObservation) -> None: ...
    def optimize(self, timestamp_s: float): ...
