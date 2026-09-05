"""Stable, dependency-light messages shared across the navigation stack."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import Enum
from typing import Any
import numpy as np


Vec = np.ndarray


class NavigationMode(str, Enum):
    INITIALIZING = "INITIALIZING"
    TRACKING = "TRACKING"
    DEGRADED = "DEGRADED"
    RECOVERING = "RECOVERING"
    NO_GLOBAL_FIX = "NO_GLOBAL_FIX"


@dataclass(frozen=True)
class CameraModel:
    fx: float
    fy: float
    cx: float
    cy: float
    width: int
    height: int

    def __post_init__(self) -> None:
        if not all(np.isfinite(value) and value > 0.0 for value in (self.fx, self.fy)):
            raise ValueError("Camera focal lengths must be finite positive pixels.")
        if self.width <= 0 or self.height <= 0:
            raise ValueError("Camera image dimensions must be positive.")

    @property
    def matrix(self) -> Vec:
        return np.array([[self.fx, 0.0, self.cx], [0.0, self.fy, self.cy], [0.0, 0.0, 1.0]])


@dataclass(frozen=True)
class FramePacket:
    sequence: int
    timestamp_s: float
    image: Any
    camera: CameraModel
    # Optional: visual-first operation does not consume it for the nominal pose path.
    imu: Any | None = None
    calibration_id: str | None = None
    clock_domain: str | None = None


@dataclass(frozen=True)
class Pose3:
    """Position (ENU m), quaternion (xyzw). The map frame is always ENU."""
    position_enu_m: Vec
    quaternion_xyzw: Vec

    def __post_init__(self) -> None:
        position = np.asarray(self.position_enu_m, dtype=float)
        quaternion = np.asarray(self.quaternion_xyzw, dtype=float)
        if position.shape != (3,) or not np.all(np.isfinite(position)):
            raise ValueError("Pose position must be three finite ENU metres.")
        if quaternion.shape != (4,) or not np.all(np.isfinite(quaternion)):
            raise ValueError("Pose quaternion must be four finite xyzw values.")
        if not np.isclose(np.linalg.norm(quaternion), 1.0, atol=1e-3):
            raise ValueError("Pose quaternion must have unit norm.")


@dataclass(frozen=True)
class VisualOdometry:
    timestamp_s: float
    relative_pose: Pose3
    covariance: Vec
    tracked_features: int
    quality: float


@dataclass(frozen=True)
class RetrievalCandidate:
    tile_id: str
    score: float
    prior_position_enu_m: Vec
    scale_m_per_px: float


@dataclass(frozen=True)
class CorrespondenceSet:
    source_uv: Vec
    target_uv: Vec
    confidence: Vec
    kind: str  # sparse_lightglue or dense_roma


@dataclass(frozen=True)
class GeometricEstimate:
    tile_id: str
    pose_map_camera: Pose3 | None
    model: str  # pnp, homography, essential
    inlier_mask: Vec
    reprojection_rmse_px: float
    covariance: Vec
    valid: bool
    # Fraction of a regular image grid occupied by accepted correspondences.
    # Unknown coverage is unsafe for a global observation and therefore rejects.
    inlier_grid_coverage: float = 0.0
    map_version: str | None = None

    @property
    def inlier_count(self) -> int:
        return int(np.count_nonzero(self.inlier_mask))


@dataclass(frozen=True)
class GlobalObservation:
    timestamp_s: float
    estimate: GeometricEstimate
    retrieval_score: float
    dense_score: float
    semantic_score: float | None = None


@dataclass(frozen=True)
class NavEstimate:
    timestamp_s: float
    pose_map_body: Pose3
    velocity_map_mps: Vec
    covariance: Vec
    confidence: float
    mode: NavigationMode
    active_tile_id: str | None
    diagnostics: dict[str, Any] = field(default_factory=dict)
    map_version: str | None = None
    calibration_id: str | None = None
    model_version: str | None = None
    measurement_age_s: float | None = None
    # Explicit publication conventions; legacy/research producers remain unset.
    body_frame: str | None = None
    covariance_convention: str | None = None
    clock_domain: str | None = None
