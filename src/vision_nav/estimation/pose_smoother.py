"""Bounded metric VIO/map smoothing with explicit frames and no flight authority.

GTSAM owns SE(3) optimization and marginalization. Inputs are already qualified
metric factors, not raw camera/IMU data or neural scores. No sensor frontend or
PX4 transport is implemented here.
"""

from __future__ import annotations

from dataclasses import dataclass
import importlib.metadata
from typing import Any

import numpy as np


def timestamp(value: Any) -> int:
    if type(value) is not int or value <= 0:
        raise ValueError("timestamp_ns must be a positive integer in the configured monotonic clock")
    return value


def transform(value: Any) -> np.ndarray:
    result = np.asarray(value, dtype=float)
    if (result.shape != (4, 4) or not np.isfinite(result).all()
            or not np.allclose(result[3], [0, 0, 0, 1], atol=1e-9, rtol=0)
            or not np.allclose(result[:3, :3].T @ result[:3, :3], np.eye(3), atol=1e-7, rtol=0)
            or not np.isclose(np.linalg.det(result[:3, :3]), 1, atol=1e-7, rtol=0)):
        raise ValueError("Expected a finite proper rigid 4x4 transform")
    return result.copy()


def covariance(value: Any, *, positive_definite: bool = True) -> np.ndarray:
    result = np.asarray(value, dtype=float)
    if (result.shape != (6, 6) or not np.isfinite(result).all()
            or not np.allclose(result, result.T, atol=1e-10, rtol=1e-8)):
        raise ValueError("Expected a finite symmetric 6x6 covariance")
    eigenvalues = np.linalg.eigvalsh(result)
    if eigenvalues.min() < -1e-10 or (positive_definite and eigenvalues.min() <= 0):
        raise ValueError("Covariance must be positive definite (no unknown axes filled with zeros)")
    return result.copy()


@dataclass(frozen=True)
class SmootherConfig:
    map_release_id: str
    model_release_id: str
    calibration_id: str
    vio_release_id: str
    clock_id: str
    covariance_model_id: str
    body_T_camera: Any
    # Common left-tangent map error [rotation radians, translation metres].
    # This retained floor is never divided by the number of fixes.
    map_systematic_covariance: Any
    lag_s: float = 5.0
    max_keyframes: int = 64
    max_odometry_gap_s: float = 1.0
    max_local_age_s: float = 0.3
    max_map_age_s: float = 2.0
    innovation_chi2_limit: float = 22.46
    huber_k: float = 2.5

    def __post_init__(self) -> None:
        for name in ("map_release_id", "model_release_id", "calibration_id", "vio_release_id", "clock_id", "covariance_model_id"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"Missing estimator provenance: {name}")
        for name in ("lag_s", "max_odometry_gap_s", "max_local_age_s", "max_map_age_s", "innovation_chi2_limit", "huber_k"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value) or value <= 0:
                raise ValueError(f"Invalid estimator limit: {name}")
        if type(self.max_keyframes) is not int or not 3 <= self.max_keyframes <= 256:
            raise ValueError("max_keyframes must be in [3,256]")
        if self.max_odometry_gap_s >= self.lag_s:
            raise ValueError("Odometry gap must be smaller than smoother lag")
        for name, checked in (("body_T_camera", transform(self.body_T_camera)),
                              ("map_systematic_covariance", covariance(self.map_systematic_covariance))):
            checked.setflags(write=False)
            object.__setattr__(self, name, checked)


class MetricPoseSmoother:
    """Single-hypothesis, fixed-lag backend for qualified metric pose factors.

    X(k) is local_T_body; A is local_T_map. A map-camera measurement becomes a
    Between(A, X(k)) map-body factor after the calibrated lever-arm transform.
    The local origin is a coordinate gauge, not a measured global origin.
    """

    def __init__(self, config: SmootherConfig):
        try:
            import gtsam
            import gtsam_unstable
        except ImportError as exc:
            raise RuntimeError("Metric smoothing requires the estimation extra in Python 3.10-3.12; no heuristic fallback") from exc
        self.g, self.u, self.config = gtsam, gtsam_unstable, config
        params = gtsam.ISAM2Params()
        params.setRelinearizeThreshold(0.01)
        params.relinearizeSkip = 1
        params.findUnusedFactorSlots = True
        params.setFactorization("QR")
        self.smoother = gtsam_unstable.IncrementalFixedLagSmoother(config.lag_s, params)
        self.alignment_key = gtsam.symbol("a", 0)
        self.body_T_camera = gtsam.Pose3(config.body_T_camera)
        self.keys: dict[int, int] = {}
        self.used_fixes: dict[int, str] = {}
        self.origin_ns: int | None = None
        self.latest_ns: int | None = None
        self.last_fix_ns: int | None = None
        self.next_key = 0
        self.raw_local_pose = gtsam.Pose3()
        self.aligned = False
        self.fault: str | None = None
        self.accepted_fixes = 0
        self.last_innovation_nis: float | None = None

    def _healthy(self) -> None:
        if self.fault:
            raise RuntimeError(f"Estimator fault is latched; construct a new explicit session: {self.fault}")

    def _seconds(self, ns: int) -> float:
        return (ns - self.origin_ns) / 1e9

    def _update(self, factors, values, times) -> None:
        self._healthy()
        try:
            self.smoother.update(factors, values, times)
            active = set(self.smoother.calculateEstimate().keys())
            self.keys = {ns: key for ns, key in self.keys.items() if key in active}
            self.used_fixes = {ns: name for ns, name in self.used_fixes.items() if ns in self.keys}
        except Exception as exc:
            self.fault = f"optimizer_failure:{type(exc).__name__}"
            raise RuntimeError(self.fault) from exc

    def _containers(self):
        return self.g.NonlinearFactorGraph(), self.g.Values(), self.u.FixedLagSmootherKeyTimestampMap()

    def start(self, timestamp_ns: int) -> None:
        self._healthy()
        timestamp_ns = timestamp(timestamp_ns)
        if self.origin_ns is not None:
            raise ValueError("Estimator session already started; no implicit reset")
        self.origin_ns = self.latest_ns = timestamp_ns
        key = self.g.symbol("x", self.next_key)
        self.next_key += 1
        factors, values, times = self._containers()
        values.insert(key, self.raw_local_pose)
        # Tight gauge only fixes the definition of the arbitrary local frame.
        factors.add(self.g.PriorFactorPose3(key, self.raw_local_pose,
                    self.g.noiseModel.Diagonal.Sigmas(np.full(6, 1e-6))))
        times.insert((key, 0.0))
        self.keys[timestamp_ns] = key
        self._update(factors, values, times)

    def _identity(self, event: dict, fields: tuple[str, ...]) -> None:
        for field in fields:
            if event.get(field) != getattr(self.config, field):
                raise ValueError(f"Estimator compatibility mismatch: {field}")
        if event.get("covariance_convention") != "right_tangent_rx_ry_rz_tx_ty_tz":
            raise ValueError("Metric factors require declared right-tangent rotation-first covariance")

    def add_odometry(self, event: dict) -> None:
        self._healthy()
        self._identity(event, ("vio_release_id", "calibration_id", "clock_id", "covariance_model_id"))
        start, end = timestamp(event["from_timestamp_ns"]), timestamp(event["timestamp_ns"])
        if self.latest_ns is None or start != self.latest_ns or end <= start:
            raise ValueError("Odometry must link the latest keyframe to a strictly newer keyframe")
        if event.get("metric_scale_verified") is not True:
            raise ValueError("Scale-unresolved odometry cannot create metric factors")
        if (end - start) / 1e9 > self.config.max_odometry_gap_s:
            raise ValueError("Odometry gap exceeds policy; explicit VIO recovery is required")
        retained = sum(ns >= end - self.config.lag_s * 1e9 for ns in self.keys)
        if retained + 1 > self.config.max_keyframes:
            raise ValueError("Keyframe capacity exceeded; do not silently drop relative motion")
        delta = self.g.Pose3(transform(event["previous_body_T_body"]))
        noise = self.g.noiseModel.Gaussian.Covariance(covariance(event["covariance"]))
        previous_key, key = self.keys[start], self.g.symbol("x", self.next_key)
        factors, values, times = self._containers()
        previous = self.smoother.calculateEstimate().atPose3(previous_key)
        values.insert(key, previous.compose(delta))
        factors.add(self.g.BetweenFactorPose3(previous_key, key, delta, noise))
        times.insert((key, self._seconds(end)))
        if self.aligned:
            times.insert((self.alignment_key, self._seconds(end)))
        self.keys[end] = key
        self._update(factors, values, times)
        self.latest_ns, self.next_key = end, self.next_key + 1
        self.raw_local_pose = self.raw_local_pose.compose(delta)

    def _relative_marginal(self, key: int):
        values = self.smoother.calculateEstimate()
        a, x = values.atPose3(self.alignment_key), values.atPose3(key)
        h1, h2 = np.zeros((6, 6), order="F"), np.zeros((6, 6), order="F")
        result = a.between(x, h1, h2)
        marginals = self.g.Marginals(self.smoother.getFactors(), values)
        joint = marginals.jointMarginalCovariance([self.alignment_key, key])
        c = np.block([[joint.at(self.alignment_key, self.alignment_key), joint.at(self.alignment_key, key)],
                      [joint.at(key, self.alignment_key), joint.at(key, key)]])
        jacobian = np.hstack((h1, h2))
        result_covariance = jacobian @ c @ jacobian.T
        return result, covariance((result_covariance + result_covariance.T) / 2), a

    def add_map_fix(self, event: dict) -> None:
        self._healthy()
        self._identity(event, ("map_release_id", "model_release_id", "calibration_id", "clock_id", "covariance_model_id"))
        ns = timestamp(event["timestamp_ns"])
        if self.latest_ns is None or ns > self.latest_ns or ns not in self.keys:
            raise ValueError("Map fix must reference an exact retained keyframe; no arrival-time reassignment")
        fix_id = event.get("fix_id")
        if not isinstance(fix_id, str) or not 1 <= len(fix_id) <= 128:
            raise ValueError("Missing or invalid map fix identity")
        if ns in self.used_fixes or fix_id in self.used_fixes.values():
            raise ValueError("Duplicate map evidence; only one accepted fix per keyframe")
        if (event.get("hypothesis_state") != "unique_temporal_support" or event.get("geometry_mode") != "pnp_3d"
                or type(event.get("temporal_support")) is not int or event["temporal_support"] < 2):
            raise ValueError("Map factors require temporally qualified, unambiguous 3D pose evidence")
        map_T_camera = self.g.Pose3(transform(event["map_T_camera"]))
        measurement = map_T_camera.compose(self.body_T_camera.inverse())
        adjoint = self.body_T_camera.AdjointMap()
        measurement_covariance = adjoint @ covariance(event["covariance"]) @ adjoint.T
        key = self.keys[ns]
        if self.aligned:
            try:
                predicted, predicted_covariance, _ = self._relative_marginal(key)
            except Exception as exc:
                self.fault = f"marginal_failure:{type(exc).__name__}"
                raise RuntimeError(self.fault) from exc
            hlog = np.zeros((6, 6), order="F")
            hm, hp = np.zeros((6, 6), order="F"), np.zeros((6, 6), order="F")
            residual = self.g.Pose3.Logmap(measurement.between(predicted, hm, hp), hlog)
            jm, jp = hlog @ hm, hlog @ hp
            innovation_covariance = jm @ measurement_covariance @ jm.T + jp @ predicted_covariance @ jp.T
            self.last_innovation_nis = float(residual @ np.linalg.solve(innovation_covariance, residual))
            if not np.isfinite(self.last_innovation_nis) or self.last_innovation_nis > self.config.innovation_chi2_limit:
                raise ValueError("Map fix innovation rejected; keep independent recovery hypotheses")
        factors, values, times = self._containers()
        if not self.aligned:
            local = self.smoother.calculateEstimate().atPose3(key)
            values.insert(self.alignment_key, local.compose(measurement.inverse()))
        gaussian = self.g.noiseModel.Gaussian.Covariance(measurement_covariance)
        noise = self.g.noiseModel.Robust.Create(self.g.noiseModel.mEstimator.Huber.Create(self.config.huber_k), gaussian)
        factors.add(self.g.BetweenFactorPose3(self.alignment_key, key, measurement, noise))
        times.insert((self.alignment_key, self._seconds(self.latest_ns)))
        self._update(factors, values, times)
        self.aligned = True
        self.used_fixes[ns] = fix_id
        self.last_fix_ns = max(ns, self.last_fix_ns or ns)
        self.accepted_fixes += 1

    def snapshot(self, now_ns: int) -> dict:
        now_ns = timestamp(now_ns)
        if self.latest_ns is not None and now_ns < self.latest_ns:
            raise ValueError("Estimator query clock precedes newest measurement")
        local_age = (now_ns - self.latest_ns) / 1e9 if self.latest_ns is not None else None
        map_age = (now_ns - self.last_fix_ns) / 1e9 if self.last_fix_ns is not None else None
        state = ("FAULT" if self.fault else "NOT_STARTED" if self.latest_ns is None else
                 "LOCAL_STALE" if local_age > self.config.max_local_age_s else
                 "LOCAL_ONLY" if not self.aligned else "MAP_STALE" if map_age > self.config.max_map_age_s else "SMOOTHED")
        result = {"schema_version": "vision_nav_metric_smoother_v1", "timestamp_ns": now_ns,
                  "estimate_timestamp_ns": self.latest_ns, "last_map_fix_timestamp_ns": self.last_fix_ns,
                  "local_age_s": local_age, "map_fix_age_s": map_age, "estimator_state": state,
                  "navigation_mode": "NO_GLOBAL_FIX", "position_authority": False, "px4_external_vision": "disabled",
                  "map_T_body": None, "local_T_map": None, "covariance": None,
                  "raw_local_T_body": self.raw_local_pose.matrix().tolist() if self.latest_ns else None,
                  "accepted_fix_count": self.accepted_fixes, "active_keyframes": len(self.keys),
                  "last_innovation_nis": self.last_innovation_nis, "fault": self.fault,
                  "provenance": {name: getattr(self.config, name) for name in
                                 ("map_release_id", "model_release_id", "calibration_id", "vio_release_id",
                                  "clock_id", "covariance_model_id")},
                  "covariance_calibration": "unvalidated_linearized_conditional_plus_declared_systematic_floor",
                  "backend": "gtsam_incremental_fixed_lag", "gtsam_version": importlib.metadata.version("gtsam")}
        if self.aligned and not self.fault:
            try:
                pose, conditional, alignment = self._relative_marginal(self.keys[self.latest_ns])
                # Shared map error is a left perturbation; do not average it as independent fixes.
                adjoint = pose.inverse().AdjointMap()
                total = conditional + adjoint @ self.config.map_systematic_covariance @ adjoint.T
                r = pose.rotation().matrix()
                axes = np.zeros((6, 6))
                axes[:3, 3:] = r
                axes[3:, :3] = r
                total = axes @ total @ axes.T
                total = covariance((total + total.T) / 2)
                result.update(map_T_body=pose.matrix().tolist(), local_T_map=alignment.matrix().tolist(),
                              covariance=total.tolist(), covariance_convention="map_enu_dx_dy_dz_drx_dry_drz")
            except Exception as exc:
                self.fault = f"marginal_failure:{type(exc).__name__}"
                result.update(estimator_state="FAULT", fault=self.fault)
        return result
