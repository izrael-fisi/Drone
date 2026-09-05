"""Conditional PnP error propagation for research, never publication covariance."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from .correlated_window import payload_digest
from .pose_smoother import timestamp, transform
from vision_nav.vio.pose_window import joint_covariance
from vision_nav.neural.bounded_pnp import validate_bound_attempt


def projection_jacobians(pose, points, matrix):
    """Pinhole prediction and right-camera SE(3)/map/intrinsics Jacobians.

    T_map_camera * Exp([rotation, translation]); K order is fx, fy, cx, cy.
    Images must already be rectified, with zero skew and the matching K.
    """
    pose = transform(pose)
    points = np.asarray(points, dtype=float)
    matrix = np.asarray(matrix, dtype=float)
    if points.ndim != 2 or points.shape[1] != 3 or not np.isfinite(points).all():
        raise ValueError("Expected finite N x 3 map points")
    if (matrix.shape != (3, 3) or not np.isfinite(matrix).all()
            or matrix[0, 0] <= 0 or matrix[1, 1] <= 0
            or not np.allclose(matrix[2], [0, 0, 1], atol=1e-12, rtol=0)
            or abs(matrix[0, 1]) > 1e-12 or abs(matrix[1, 0]) > 1e-12):
        raise ValueError("Expected calibrated zero-skew rectified pinhole matrix")
    camera = (points-pose[:3, 3]) @ pose[:3, :3]
    if np.any(camera[:, 2] <= 1e-6):
        raise ValueError("All retained landmarks must be in front of the camera")
    fx, fy, cx, cy = matrix[0, 0], matrix[1, 1], matrix[0, 2], matrix[1, 2]
    prediction, hp, hm, hk = [], [], [], []
    for x, y, z in camera:
        d = np.array([[fx/z, 0, -fx*x/z**2], [0, fy/z, -fy*y/z**2]])
        skew = np.array([[0, -z, y], [z, 0, -x], [-y, x, 0]])
        prediction.append([fx*x/z+cx, fy*y/z+cy])
        hp.append(d @ np.hstack((skew, -np.eye(3))))
        hm.append(d @ pose[:3, :3].T)
        hk.append([[x/z, 0, 1, 0], [0, y/z, 0, 1]])
    return np.asarray(prediction), np.vstack(hp), np.asarray(hm), np.vstack(hk)


def assess(request):
    """Propagate declared joint input errors through an unweighted PnP optimum.

    Conditional on fixed correct inliers and a locally valid linearization.
    This is sandwich propagation for the existing unweighted fit, not the
    inverse weighted information of a different, hypothetical GLS estimator.
    """
    if request.get("schema_version") != "vision_nav_pnp_uncertainty_request_v1":
        raise ValueError("Unknown PnP uncertainty request")
    attempt, noise, context = request["attempt"], request["uncertainty"], request["context"]
    validate_bound_attempt(attempt)
    for field in ("map_release_id", "model_release_id", "calibration_id", "clock_id", "uncertainty_model_id"):
        if not isinstance(context.get(field), str) or not context[field].strip():
            raise ValueError(f"Missing uncertainty provenance: {field}")
    ns = timestamp(context["timestamp_ns"])
    if noise.get("attempt_sha256") != payload_digest(attempt):
        raise ValueError("Uncertainty does not refer to this exact geometry attempt")
    expected = {
        "measurement_order": "all_query_uv_then_all_map_xyz_then_fx_fy_cx_cy",
        "correspondences": "fixed_correct_inliers_conditional",
        "map_systematics": "included_in_joint_measurement_covariance",
        "rectification_error": "included_in_joint_measurement_covariance",
        "timing_error": "not_modeled",
        "camera_extrinsic_error": "not_modeled",
    }
    if any(noise.get(key) != value for key, value in expected.items()):
        raise ValueError("Explicit measurement order and uncertainty assumptions required")
    if (noise.get("scope") not in {"synthetic_contract_test", "unvalidated_research_model"}
            or not isinstance(noise.get("noise_model_description"), str) or not noise["noise_model_description"].strip()):
        raise ValueError("Research scope and noise-model description required")
    geometry = attempt.get("geometry", {})
    if (attempt.get("position_authority") is not False or geometry.get("position_authority") is not False
            or geometry.get("status") != "accepted" or geometry.get("reason") != "pnp_geometry_gate_passed"
            or geometry.get("pose_solver") != "opencv_AP3P_RANSAC_RefineLM_unweighted"):
        raise ValueError("An accepted shadow unweighted PnP attempt is required")
    pixels = np.asarray(attempt["matched_query_pixels"], dtype=float)
    points = np.asarray(attempt["matched_map_points_enu_m"], dtype=float)
    n = len(pixels)
    if (not 6 <= n <= 128 or pixels.shape != (n, 2) or points.shape != (n, 3)
            or not np.isfinite(pixels).all() or not np.isfinite(points).all()):
        raise ValueError("Research covariance requires 6-128 finite paired correspondences")
    indices = geometry.get("inlier_match_indices")
    if (not isinstance(indices, list) or not 6 <= len(indices) <= n
            or any(type(i) is not int or not 0 <= i < n for i in indices)
            or len(set(indices)) != len(indices) or geometry.get("inlier_count") != len(indices)):
        raise ValueError("Exact unique original-match PnP inlier indices required")
    cov = joint_covariance(noise["joint_measurement_covariance"], 5*n+4)
    pose = np.eye(4)
    pose[:3, :3] = np.asarray(geometry["camera_rotation_enu_to_optical"], dtype=float).T
    pose[:3, 3] = geometry["camera_position_enu_m"]
    pose = transform(pose)
    result = {
        "schema_version": "vision_nav_pnp_uncertainty_v1", "request_sha256": payload_digest(request),
        "implementation_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
        "attempt_sha256": payload_digest(attempt), "timestamp_ns": ns,
        **{k: context[k] for k in ("map_release_id", "model_release_id", "calibration_id", "clock_id", "uncertainty_model_id")},
        "navigation_mode": "NO_GLOBAL_FIX", "position_authority": False, "px4_external_vision": "disabled",
        "covariance_calibrated": False, "publication_covariance": None,
        "covariance_right_camera": None, "map_T_camera": pose.tolist(),
        "tangent_order": "rotation_radians_translation_metres", "inlier_match_indices": indices,
        "method": "gauss_newton_sandwich_unweighted_pnp", "assumptions": expected,
        "scope": noise["scope"], "vio_cross_covariance": None,
    }
    prediction, h, hm, hk = projection_jacobians(pose, points[indices], attempt["calibrated_camera_matrix"])
    residual = (prediction-pixels[indices]).reshape(-1)
    # Column scaling only conditions the solve; it does not change covariance units.
    scale = np.linalg.norm(h, axis=0)
    if np.any(scale <= 1e-12):
        return {**result, "status": "withheld", "reason": "unobservable_pose_direction"}
    u, singular, vt = np.linalg.svd(h/scale, full_matrices=False)
    condition = float(singular[0]/singular[-1]) if singular[-1] > 0 else None
    result.update(column_normalized_condition=condition, reprojection_rmse_px=float(np.linalg.norm(residual)/np.sqrt(len(indices))))
    if condition is None or condition > 1e8:
        return {**result, "status": "withheld", "reason": "rank_deficient_or_ill_conditioned_geometry"}
    stationarity = float(np.linalg.norm(u.T @ residual)/np.sqrt(len(residual)))
    result["stationarity_px"] = stationarity
    if stationarity > 1e-3:
        return {**result, "status": "withheld", "reason": "pose_not_stationary_for_declared_inliers"}
    # The full measurement covariance retains across-point and calibration cross terms.
    b = np.zeros((2*len(indices), 5*n+4))
    for j, i in enumerate(indices):
        rows = slice(2*j, 2*j+2)
        b[rows, 2*i:2*i+2] = -np.eye(2)
        b[rows, 2*n+3*i:2*n+3*i+3] = hm[j]
        b[rows, 5*n:] = hk[2*j:2*j+2]
    inverse_h = ((vt.T/singular) @ u.T)/scale[:, None]
    sensitivity = -inverse_h @ b
    propagated = joint_covariance(sensitivity @ cov @ sensitivity.T, 6)
    if np.linalg.eigvalsh(propagated).min() <= 0:
        return {**result, "status": "withheld", "reason": "noise_model_leaves_zero_pose_uncertainty"}
    return {**result, "status": "conditional_research_covariance", "reason": "fixed_inlier_small_error_model_not_navigation",
            "covariance_right_camera": propagated.tolist(), "pose_input_sensitivity": sensitivity.tolist(),
            "cross_fix_covariance_required_before_fusion": True,
            "shared_map_error_already_included": True}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    if args.request.stat().st_size > 16*1024*1024:
        raise ValueError("Research request exceeds 16 MiB")
    request = json.loads(args.request.read_text())
    result = assess(request)
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({k: result[k] for k in ("status", "reason", "position_authority")}, allow_nan=False))
    return 0 if result["status"] == "conditional_research_covariance" else 2


if __name__ == "__main__":
    raise SystemExit(main())
