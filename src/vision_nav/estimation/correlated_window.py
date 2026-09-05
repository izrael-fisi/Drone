"""Single-snapshot correlated VIO/map alignment for research, never flight output."""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

import numpy as np

from .pose_smoother import covariance, timestamp, transform
from vision_nav.vio.pose_window import JointPoseWindow, joint_covariance


def payload_digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _sqrt_information(cov):
    """Whiten without forming/inverting a normal-equation information matrix."""
    chol = np.linalg.cholesky(cov)
    _, root = np.linalg.qr(np.linalg.solve(chol, np.eye(len(cov))))
    root = np.where(np.diag(root) < 0, -1., 1.)[:, None]*root
    check = root @ chol
    if not np.isfinite(root).all() or np.max(np.abs(check @ check.T-np.eye(len(cov)))) > 1e-6:
        raise ValueError("Joint covariance cannot be stably whitened; no jitter or noise repair")
    return root


@dataclass(frozen=True)
class WindowAlignmentConfig:
    map_release_id: str
    model_release_id: str
    calibration_id: str
    clock_id: str
    uncertainty_model_id: str
    imu_T_camera: object
    map_common_left_covariance: object
    consistency_probability: float = .999
    max_iterations: int = 30

    def __post_init__(self):
        for key in ("map_release_id", "model_release_id", "calibration_id", "clock_id", "uncertainty_model_id"):
            if not isinstance(getattr(self, key), str) or not getattr(self, key).strip():
                raise ValueError(f"Missing alignment provenance: {key}")
        if (type(self.consistency_probability) not in (float, int) or not .5 < self.consistency_probability < 1
                or type(self.max_iterations) is not int or not 1 <= self.max_iterations <= 100):
            raise ValueError("Invalid optimizer/gate settings")
        for key, value in (("imu_T_camera", transform(self.imu_T_camera)),
                           ("map_common_left_covariance", covariance(self.map_common_left_covariance, positive_definite=False))):
            value.setflags(write=False)
            object.__setattr__(self, key, value)


class CorrelatedWindowAligner:
    """Fresh batch per posterior: no carry-over priors, map fixes or VIO feedback.

    X_i = local_T_imu_i, A = local_T_nominal_map. Map uncertainty is explicitly
    joint across fixes and with VIO; no independent-factor approximation is made.
    """

    def __init__(self, config: WindowAlignmentConfig):
        import gtsam
        self.g, self.config = gtsam, config

    def _prepare(self, record, batch):
        window = JointPoseWindow.from_record(record)
        return self._prepare_window(window, payload_digest(record), batch)

    def _prepare_window(self, window, snapshot_sha256, batch):
        """Validate a map batch against an already checked, owned native window."""
        if batch.get("schema_version") != "vision_nav_correlated_map_batch_v1":
            raise ValueError("Unknown correlated map batch schema")
        if batch.get("snapshot_sha256") != snapshot_sha256:
            raise ValueError("Map batch is bound to a different VIO snapshot")
        for key in ("map_release_id", "model_release_id", "calibration_id", "clock_id", "uncertainty_model_id"):
            if batch.get(key) != getattr(self.config, key):
                raise ValueError(f"Map batch provenance mismatch: {key}")
        if (batch.get("covariance_convention") != "joint_right_local_pose_residuals"
                or batch.get("common_map_error_independent_of_joint_residuals") is not True
                or batch.get("extrinsics_treated_as_fixed") is not True
                or batch.get("covariance_scope") not in {"synthetic_contract_test", "unvalidated_research_model"}
                or not isinstance(batch.get("cross_covariance_model"), str) or not batch["cross_covariance_model"].strip()):
            raise ValueError("Missing explicit residual uncertainty assumptions")
        fixes = batch.get("fixes", [])
        if not isinstance(fixes, list) or not 1 <= len(fixes) <= min(16, len(window.timestamps_ns)):
            raise ValueError("Expected 1-16 map fixes within retained clone window")
        ids, times, poses, indices = [], [], [], []
        for fix in fixes:
            ns = timestamp(fix["timestamp_ns"])
            identity = fix.get("fix_id")
            if not isinstance(identity, str) or not identity.strip() or identity in ids or ns in times:
                raise ValueError("Duplicate or missing map evidence identity/time")
            if ns not in window.timestamps_ns:
                raise ValueError("Map fix time must be an exact retained clone, not receipt time")
            if times and ns <= times[-1]:
                raise ValueError("Map fixes must be ordered like their joint covariance")
            ids.append(identity)
            times.append(ns)
            indices.append(window.timestamps_ns.index(ns))
            poses.append(self.g.Pose3(transform(fix["map_T_camera"])))
        n, m = len(window.timestamps_ns), len(fixes)
        q = joint_covariance(batch["map_joint_covariance"], 6*m)
        cross = np.asarray(batch["vio_map_cross_covariance"], dtype=float)
        if cross.shape != (6*n, 6*m) or not np.isfinite(cross).all():
            raise ValueError("Explicit finite VIO/map cross-covariance is required")
        cov = joint_covariance(np.block([[window.covariance_right_local, cross], [cross.T, q]]), 6*(n+m))
        try:
            np.linalg.cholesky(cov)
        except np.linalg.LinAlgError as exc:
            raise ValueError("Joint residual model must be positive definite; no jitter or guessed noise") from exc
        return window, indices, poses, cov

    def _graph(self, window, indices, measurements, cov):
        g = self.g
        alignment = g.symbol("a", 0)
        keys = [g.symbol("x", i) for i in range(len(window.timestamps_ns))]
        ordered = [alignment, *keys]
        means = [g.Pose3(p) for p in window.local_T_imu]
        extrinsic = g.Pose3(self.config.imu_T_camera)
        dimension = len(cov)

        def residual(_factor, values, jacobians):
            result = np.zeros(dimension)
            blocks = [np.zeros((dimension, 6), order="F") for _ in ordered] if jacobians is not None else None
            a = values.atPose3(alignment)
            xs = [values.atPose3(k) for k in keys]
            for i, (mean, x) in enumerate(zip(means, xs)):
                h0, hx, hl = (np.zeros((6, 6), order="F") for _ in range(3))
                result[6*i:6*i+6] = g.Pose3.Logmap(mean.between(x, h0, hx), hl)
                if blocks is not None:
                    blocks[i+1][6*i:6*i+6] = hl @ hx
            for j, (i, z) in enumerate(zip(indices, measurements)):
                ha, hx, hc, he, hm, hp, hl = (np.zeros((6, 6), order="F") for _ in range(7))
                prediction = a.between(xs[i], ha, hx).compose(extrinsic, hc, he)
                rows = slice(6*(len(keys)+j), 6*(len(keys)+j+1))
                result[rows] = g.Pose3.Logmap(z.between(prediction, hm, hp), hl)
                if blocks is not None:
                    chain = hl @ hp @ hc
                    blocks[0][rows] = chain @ ha
                    blocks[i+1][rows] = chain @ hx
            if blocks is not None:
                for i, block in enumerate(blocks):
                    jacobians[i] = block
            return result

        graph, initial = g.NonlinearFactorGraph(), g.Values()
        # GTSAM's covariance-to-information path can lose accuracy on native
        # near-correlated clone windows. Supply a checked square root directly.
        graph.add(g.CustomFactor(g.noiseModel.Gaussian.SqrtInformation(_sqrt_information(cov), False), ordered, residual))
        initial.insert(alignment, means[indices[0]].compose(extrinsic).compose(measurements[0].inverse()))
        for key, mean in zip(keys, means):
            initial.insert(key, mean)
        return graph, initial, alignment, keys, residual

    @staticmethod
    def _reduce(window, indices, cov, enabled):
        """Eliminate unobserved pose residuals, not their uncertainty.

        Unused poses occur only in their Gaussian residual block. Minimizing
        over those residuals leaves the PRINCIPAL covariance of retained
        residuals, not a Schur-complement covariance that conditions them to zero.
        Native validation and provenance always use the complete original model.
        """
        n = len(window.timestamps_ns)
        selected = sorted(set(indices) | {n-1}) if enabled else list(range(n))
        retained = [6*i+j for i in selected for j in range(6)] + list(range(6*n, len(cov)))
        omitted = [6*i+j for i in range(n) if i not in selected for j in range(6)]
        principal = cov[np.ix_(retained, retained)]
        pose_rows = retained[:6*len(selected)]
        local_cov = window.covariance_right_local[np.ix_(pose_rows, pose_rows)]
        local_cov.setflags(write=False)
        reduced = JointPoseWindow(window.posterior_ns,
            tuple(window.timestamps_ns[i] for i in selected),
            tuple(window.local_T_imu[i] for i in selected), local_cov)
        return reduced, [selected.index(i) for i in indices], principal, retained, omitted

    def _single_fix(self, window, index, measurement, cov, base):
        """Exact mean and first-order joint propagation for one free map anchor.

        Y = Z E^-1 X_anchor^-1 X_current. There is no redundant map constraint:
        every pose residual is zero, so an optimizer cannot improve the means.
        The complete validated covariance includes all VIO/map cross terms.
        """
        g = self.g
        anchor = g.Pose3(window.local_T_imu[index])
        current = g.Pose3(window.local_T_imu[-1])
        ext = g.Pose3(self.config.imu_T_camera)
        n = len(window.timestamps_ns)
        h = np.zeros((6, len(cov)))
        map_imu = measurement.compose(ext.inverse())
        if index == n-1:
            # These are the SAME stochastic pose, not two independent draws.
            pose = map_imu
            h[:, -6:] = ext.AdjointMap()
        else:
            ha, hx, hm, hr = (np.zeros((6, 6), order="F") for _ in range(4))
            relative = anchor.between(current, ha, hx)
            pose = map_imu.compose(relative, hm, hr)
            h[:, 6*index:6*index+6] = hr @ ha
            h[:, 6*(n-1):6*n] = hr @ hx
            h[:, -6:] = hm @ ext.AdjointMap()
        # Gram propagation avoids subtracting large correlated covariance terms.
        propagated = h @ np.linalg.cholesky(cov)
        conditional = covariance(propagated @ propagated.T)
        common = pose.inverse().AdjointMap()
        total = conditional + common @ self.config.map_common_left_covariance @ common.T
        total = covariance((total+total.T)/2)
        a = anchor.compose(ext).compose(measurement.inverse())
        if base["nuisance_elimination"]["marginalized_pose_count"]:
            base["nuisance_elimination"]["max_eliminated_rotation_rad"] = 0.
        return {**base, "status": "research_alignment", "reason": "conditional_gaussian_alignment_not_navigation",
            "solution_method": "single_fix_closed_form", "covariance_solver": "analytic_joint_se3_pushforward",
            "residual_chi2": 0., "residual_dof": 0, "consistency_limit": None,
            "optimizer_iterations": 0, "consistency_test": "untestable_single_map_fix",
            "map_T_imu": transform(pose.matrix()).tolist(), "covariance_right_imu": total.tolist(),
            "conditional_covariance_right_imu": conditional.tolist(), "local_T_nominal_map": a.matrix().tolist(),
            "raw_local_T_imu": window.local_T_imu[-1].tolist(),
            "map_common_left_covariance": self.config.map_common_left_covariance.tolist()}

    def solve(self, record, batch, *, marginalize_unused_poses=True):
        window = JointPoseWindow.from_record(record)
        return self._solve_window(window, payload_digest(record), batch,
                                  marginalize_unused_poses=marginalize_unused_poses)

    def _solve_window(self, window, snapshot_sha256, batch, *, marginalize_unused_poses=True):
        """Internal transaction path; external records always enter through solve."""
        from scipy.stats import chi2

        if type(marginalize_unused_poses) is not bool:
            raise ValueError("Nuisance elimination must be explicitly boolean")
        window, indices, measurements, cov = self._prepare_window(window, snapshot_sha256, batch)
        reduced, reduced_indices, principal, retained, omitted = self._reduce(
            window, indices, cov, marginalize_unused_poses)
        base = {"schema_version": "vision_nav_correlated_window_alignment_v1",
                "snapshot_sha256": snapshot_sha256, "map_batch_sha256": payload_digest(batch),
                "posterior_timestamp_ns": window.posterior_ns, "pose_timestamp_ns": window.timestamps_ns[-1],
                "fix_ids": [f["fix_id"] for f in batch["fixes"]], "map_release_id": self.config.map_release_id,
                "model_release_id": self.config.model_release_id, "calibration_id": self.config.calibration_id,
                "clock_id": self.config.clock_id, "body_frame": "imu",
                "covariance_convention": "right_se3_rotation_radians_translation_metres",
                "uncertainty_model_id": self.config.uncertainty_model_id, "position_authority": False,
                "navigation_mode": "NO_GLOBAL_FIX", "px4_external_vision": "disabled", "confidence": None,
                "covariance_calibrated": False, "snapshot_policy": "replace_do_not_accumulate",
                "covariance_solver": "whitened_jacobian_qr",
                "solution_method": "joint_nonlinear_graph",
                "map_T_imu": None, "covariance_right_imu": None,
                "nuisance_elimination": {
                    "method": "gaussian_residual_principal_covariance" if omitted else "none",
                    "native_pose_count": len(window.timestamps_ns),
                    "optimized_pose_count": len(reduced.timestamps_ns),
                    "optimized_pose_timestamps_ns": list(reduced.timestamps_ns),
                    "marginalized_pose_count": len(omitted)//6,
                    "original_residual_dimension": len(cov), "optimized_residual_dimension": len(principal),
                    "complete_joint_model_validated": True, "native_filter_changed": False}}
        try:
            if marginalize_unused_poses and len(measurements) == 1:
                return self._single_fix(window, indices[0], measurements[0], cov, base)
            graph, initial, alignment, keys, residual = self._graph(reduced, reduced_indices, measurements, principal)
            params = self.g.LevenbergMarquardtParams()
            params.setMaxIterations(self.config.max_iterations)
            params.setLinearSolverType("MULTIFRONTAL_QR")
            params.setRelativeErrorTol(1e-9)
            params.setAbsoluteErrorTol(1e-9)
            optimizer = self.g.LevenbergMarquardtOptimizer(graph, initial, params)
            result = optimizer.optimize()
            residual_chi2 = 2*graph.error(result)
            dof = 6*(len(measurements)-1)
            limit = float(chi2.ppf(self.config.consistency_probability, dof)) if dof else None
            base.update(residual_chi2=float(residual_chi2), residual_dof=dof, consistency_limit=limit,
                        optimizer_iterations=int(optimizer.iterations()),
                        consistency_test="gaussian_assumption_unvalidated" if dof else "untestable_single_map_fix")
            if not np.isfinite(residual_chi2) or (limit is not None and residual_chi2 > limit):
                return {**base, "status": "withheld", "reason": "inconsistent_joint_map_vio_batch"}
            if optimizer.iterations() >= self.config.max_iterations:
                return {**base, "status": "withheld", "reason": "optimizer_iteration_budget_reached"}
            if omitted:
                # The eliminated optimum must be representable in the same
                # principal SE(3) log chart as the original full factor.
                omitted_residual = cov[np.ix_(omitted, retained)] @ np.linalg.solve(
                    principal, residual(None, result, None))
                if not np.isfinite(omitted_residual).all():
                    return {**base, "status": "withheld", "reason": "nonfinite_eliminated_pose_residual"}
                angle = float(np.linalg.norm(omitted_residual.reshape(-1, 6)[:, :3], axis=1).max())
                base["nuisance_elimination"]["max_eliminated_rotation_rad"] = angle
                if angle >= np.pi-1e-6:
                    return {**base, "status": "withheld", "reason": "eliminated_pose_outside_principal_log_chart"}
            a, x = result.atPose3(alignment), result.atPose3(keys[-1])
            ha, hx = np.zeros((6, 6), order="F"), np.zeros((6, 6), order="F")
            pose = a.between(x, ha, hx)
            blocks = [None]*(len(keys)+1)
            residual(None, result, blocks)
            whitened = np.linalg.solve(np.linalg.cholesky(principal), np.hstack(blocks))
            _, root = np.linalg.qr(whitened, mode="reduced")
            singular = np.linalg.svd(root, compute_uv=False)
            if singular[-1] <= np.finfo(float).eps*max(whitened.shape)*singular[0]:
                return {**base, "status": "withheld", "reason": "rank_deficient_whitened_pose_jacobian"}
            base["linearized_jacobian_condition"] = float(singular[0]/singular[-1])
            h = np.zeros((6, 6*(len(keys)+1)))
            h[:, :6], h[:, -6:] = ha, hx
            # H (J^T C^-1 J)^-1 H^T = (R^-T H^T)^T (R^-T H^T).
            propagated = np.linalg.solve(root.T, h.T)
            conditional = covariance(propagated.T @ propagated)
            common_jacobian = pose.inverse().AdjointMap()
            total = conditional + common_jacobian @ self.config.map_common_left_covariance @ common_jacobian.T
            total = covariance((total+total.T)/2)
            return {**base, "status": "research_alignment", "reason": "conditional_gaussian_alignment_not_navigation",
                    "map_T_imu": transform(pose.matrix()).tolist(), "covariance_right_imu": total.tolist(),
                    "conditional_covariance_right_imu": conditional.tolist(), "local_T_nominal_map": a.matrix().tolist(),
                    "raw_local_T_imu": window.local_T_imu[-1].tolist(),
                    "map_common_left_covariance": self.config.map_common_left_covariance.tolist()}
        except (RuntimeError, ValueError, np.linalg.LinAlgError) as exc:
            return {**base, "status": "withheld", "reason": f"optimizer_or_marginal_failure:{type(exc).__name__}:{exc}"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.request.stat().st_size > 8*1024*1024:
        raise ValueError("Correlated window request exceeds research size bound")
    request = json.loads(args.request.read_text())
    if request.get("schema_version") != "vision_nav_correlated_alignment_request_v1":
        raise ValueError("Unsupported alignment request")
    result = CorrelatedWindowAligner(WindowAlignmentConfig(**request["config"])).solve(request["native_record"], request["map_batch"])
    result["request_sha256"] = payload_digest(request)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(result, indent=2, allow_nan=False))
    return 0 if result["status"] == "research_alignment" else 2


if __name__ == "__main__":
    raise SystemExit(main())
