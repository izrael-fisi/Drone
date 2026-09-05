"""Offline PX4 boundary checks and NED pose conversion; no wire transport."""

from __future__ import annotations

from dataclasses import dataclass
import time

import numpy as np

from ..contracts import CompatibilityReport, validate_nav_estimate
from ..types import NavEstimate, NavigationMode


@dataclass(frozen=True)
class Px4VisionOdometry:
    """Internal mathematical record, NOT a MAVLink ODOMETRY packet.

    Orientation maps body FRD into NED. Covariance is a full 6x6 matrix for
    [delta_position_ned, delta_rotation_ned], with left rotation perturbations.
    It is not Euler-angle covariance or MAVLink's packed 21-element array.
    Timestamp remains host-monotonic; no PX4 clock synchronization is implied.
    """
    timestamp_us: int
    position_ned_m: np.ndarray
    quaternion_wxyz: np.ndarray
    pose_covariance: np.ndarray
    frame_id: str = "NED"
    body_frame: str = "FRD"
    covariance_convention: str = "position_then_left_rotation_world"
    clock_domain: str = "host_monotonic"


@dataclass(frozen=True)
class Px4PublicationPolicy:
    max_estimate_age_s: float = 0.15
    max_covariance_trace_m2: float = 100.0

    def __post_init__(self) -> None:
        if not all(np.isfinite(value) and value > 0 for value in (
            self.max_estimate_age_s, self.max_covariance_trace_m2
        )):
            raise ValueError("Publication limits must be finite and positive.")


@dataclass(frozen=True)
class Px4PublicationDecision:
    publish: bool
    reason: str
    estimate_age_s: float | None = None
    covariance_trace_m2: float | None = None


class Px4VisionBridge:
    """Translator for MAVLink ODOMETRY / PX4 External Vision.

    This layer has no arm, mode, mission, trajectory, or actuator API. PX4 owns those
    responsibilities. The generic bridge deliberately exposes a typed message only;
    vehicle code binds the pinned MAVSDK/pymavlink transport after this gate passes.
    """

    def __init__(self, policy: Px4PublicationPolicy = Px4PublicationPolicy()) -> None:
        self.policy = policy

    @staticmethod
    def _convention_rejection(estimate: NavEstimate) -> str | None:
        if estimate.body_frame != "FRD":
            return "unsupported_body_frame"
        if estimate.covariance_convention != "position_then_left_rotation_world":
            return "unsupported_covariance_convention"
        if estimate.clock_domain != "host_monotonic":
            return "unsupported_clock_domain"
        if (estimate.diagnostics.get("publication_withheld")
                or estimate.diagnostics.get("position_authority") is False):
            return "estimator_publication_withheld"
        return None

    def publication_decision(
        self,
        estimate: NavEstimate,
        *,
        compatibility: CompatibilityReport,
        transport_healthy: bool,
        now_s: float | None = None,
    ) -> Px4PublicationDecision:
        if estimate.mode != NavigationMode.TRACKING:
            return Px4PublicationDecision(False, "navigation_not_tracking")
        valid, reason = validate_nav_estimate(estimate)
        if not valid:
            return Px4PublicationDecision(False, reason or "invalid_estimate")
        convention_reason = self._convention_rejection(estimate)
        if convention_reason:
            return Px4PublicationDecision(False, convention_reason)
        if not compatibility.compatible:
            return Px4PublicationDecision(False, "incompatible_map_camera_or_model")
        versions = (
            (estimate.calibration_id, compatibility.camera_calibration_id),
            (estimate.map_version, compatibility.map_version),
            (estimate.model_version, compatibility.model_version),
        )
        if any(not actual or not expected or actual != expected for actual, expected in versions):
            return Px4PublicationDecision(False, "estimate_compatibility_binding_mismatch")
        if not transport_healthy:
            return Px4PublicationDecision(False, "px4_transport_unhealthy")
        now = time.monotonic() if now_s is None else now_s
        if not np.isfinite(now) or now <= 0:
            return Px4PublicationDecision(False, "invalid_current_timestamp")
        age = now - estimate.timestamp_s
        if age < 0:
            return Px4PublicationDecision(False, "estimate_in_future", age)
        if age > self.policy.max_estimate_age_s:
            return Px4PublicationDecision(False, "estimate_stale", age)
        trace = float(np.trace(np.asarray(estimate.covariance, dtype=float)[:3, :3]))
        if trace > self.policy.max_covariance_trace_m2:
            return Px4PublicationDecision(False, "covariance_exceeds_policy", age, trace)
        return Px4PublicationDecision(True, "tracking_estimate_ready", age, trace)

    def to_message(self, estimate: NavEstimate) -> Px4VisionOdometry | None:
        """Convert a declared pose contract for offline inspection, never send it."""

        if (estimate.mode != NavigationMode.TRACKING
                or not validate_nav_estimate(estimate)[0]
                or self._convention_rejection(estimate)):
            return None
        rotation = np.array([[0., 1., 0.], [1., 0., 0.], [0., 0., -1.]])
        qx, qy, qz, qw = np.asarray(estimate.pose_map_body.quaternion_xyzw, dtype=float)
        # Hamilton product q_NED_ENU * q_ENU_FRD, q_NED_ENU = (0,s,s,0).
        s = np.sqrt(.5)
        quaternion = np.array([-s * (qx + qy), s * (qw + qz),
                               s * (qw - qz), s * (qy - qx)])
        jacobian = np.zeros((6, 6))
        jacobian[:3, :3] = rotation
        jacobian[3:, 3:] = rotation
        return Px4VisionOdometry(
            int(estimate.timestamp_s * 1_000_000),
            rotation @ np.asarray(estimate.pose_map_body.position_enu_m, dtype=float),
            quaternion,
            jacobian @ np.asarray(estimate.covariance, dtype=float) @ jacobian.T,
        )

    async def publish_mavsdk(
        self,
        estimate: NavEstimate,
        system: object,
        *,
        compatibility: CompatibilityReport,
        transport_healthy: bool,
        now_s: float | None = None,
    ) -> bool:
        """Vehicle integration hook for the pinned MAVSDK/PX4 ODOMETRY protocol."""

        decision = self.publication_decision(
            estimate,
            compatibility=compatibility,
            transport_healthy=transport_healthy,
            now_s=now_s,
        )
        if not decision.publish:
            return False
        raise NotImplementedError(
            "Bind the pinned MAVSDK/PX4 ODOMETRY transport in vehicle integration code."
        )
