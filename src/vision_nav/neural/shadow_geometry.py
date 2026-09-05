"""Robust planar verification for learned local matches without pose output."""

from __future__ import annotations

from typing import Any

import cv2
import numpy as np


def _grid_coverage(points: np.ndarray, *, width: int, height: int, rows: int = 4, cols: int = 4) -> float:
    if len(points) == 0 or width <= 0 or height <= 0:
        return 0.0
    cells = {
        (
            min(rows - 1, max(0, int(point[1] / height * rows))),
            min(cols - 1, max(0, int(point[0] / width * cols))),
        )
        for point in points
        if np.isfinite(point).all()
    }
    return len(cells) / float(rows * cols)


def verify_planar_correspondences(
    *,
    query_keypoints: np.ndarray,
    map_keypoints: np.ndarray,
    match_pairs: np.ndarray,
    scores: np.ndarray,
    query_width: int,
    query_height: int,
    minimum_score: float,
    min_inliers: int,
    min_inlier_ratio: float,
    max_reprojection_error_px: float,
    min_grid_coverage: float,
) -> dict[str, Any]:
    """Run USAC/MAGSAC map-pixel to image-pixel consistency verification.

    A valid homography means only that a candidate tile is geometrically
    consistent with the frame. It does not establish metric camera position.
    """

    query = np.asarray(query_keypoints, dtype=np.float32).reshape(-1, 2)
    map_points = np.asarray(map_keypoints, dtype=np.float32).reshape(-1, 2)
    pairs = np.asarray(match_pairs)
    values = np.asarray(scores, dtype=np.float32).reshape(-1)
    if pairs.ndim < 2 or pairs.shape[-1] != 2:
        return {"status": "unavailable", "reason": "invalid_match_pair_shape"}
    pairs = pairs.reshape(-1, 2)
    if values.size != len(pairs) or not np.all(np.isfinite(values)):
        return {"status": "unavailable", "reason": "match_score_count_or_values_invalid"}
    if not np.all(np.isfinite(query)) or not np.all(np.isfinite(map_points)):
        return {"status": "unavailable", "reason": "nonfinite_keypoints"}
    rounded = np.rint(pairs)
    if not np.allclose(pairs, rounded):
        return {"status": "unavailable", "reason": "match_pairs_are_not_integer_indices"}
    pairs = rounded.astype(np.int64)
    valid = (
        (pairs[:, 0] >= 0)
        & (pairs[:, 1] >= 0)
        & (pairs[:, 0] < len(query))
        & (pairs[:, 1] < len(map_points))
        & (values >= minimum_score)
    )
    filtered = pairs[valid]
    if len(filtered) < 4:
        return {"status": "rejected", "reason": "insufficient_scored_correspondences", "candidate_count": int(len(filtered))}
    source = map_points[filtered[:, 1]]
    target = query[filtered[:, 0]]
    method = getattr(cv2, "USAC_MAGSAC", cv2.RANSAC)
    method_name = "USAC_MAGSAC" if method == getattr(cv2, "USAC_MAGSAC", None) else "RANSAC"
    homography, mask = cv2.findHomography(
        source,
        target,
        method=method,
        ransacReprojThreshold=max_reprojection_error_px,
        maxIters=10_000,
        confidence=0.999,
    )
    if homography is None or mask is None:
        return {"status": "rejected", "reason": "homography_fit_failed", "candidate_count": int(len(filtered)), "algorithm": method_name}
    inlier_mask = mask.reshape(-1).astype(bool)
    if not np.any(inlier_mask):
        return {"status": "rejected", "reason": "homography_has_no_inliers", "candidate_count": int(len(filtered)), "algorithm": method_name}
    homogeneous = np.c_[source, np.ones(len(source), dtype=np.float32)]
    projected = (homography @ homogeneous.T).T
    projected = projected[:, :2] / projected[:, 2:3]
    residual = np.linalg.norm(projected - target, axis=1)
    rmse = float(np.sqrt(np.mean(residual[inlier_mask] ** 2)))
    inlier_count = int(np.count_nonzero(inlier_mask))
    inlier_ratio = float(inlier_count / len(filtered))
    coverage = _grid_coverage(target[inlier_mask], width=query_width, height=query_height)
    accepted = (
        inlier_count >= min_inliers
        and inlier_ratio >= min_inlier_ratio
        and rmse <= max_reprojection_error_px
        and coverage >= min_grid_coverage
    )
    return {
        "status": "accepted" if accepted else "rejected",
        "reason": "planar_geometry_gate_passed" if accepted else "planar_geometry_gate_threshold_not_met",
        "algorithm": method_name,
        "candidate_count": int(len(filtered)),
        "inlier_count": inlier_count,
        "inlier_ratio": inlier_ratio,
        "reprojection_rmse_px": rmse,
        "inlier_grid_coverage": coverage,
        "thresholds": {
            "minimum_score": float(minimum_score),
            "min_inliers": min_inliers,
            "min_inlier_ratio": min_inlier_ratio,
            "max_reprojection_error_px": max_reprojection_error_px,
            "min_grid_coverage": min_grid_coverage,
        },
    }


def verify_pnp_correspondences(
    *,
    query_keypoints: np.ndarray,
    map_points_enu_m: np.ndarray,
    match_pairs: np.ndarray,
    scores: np.ndarray,
    camera_matrix: np.ndarray,
    query_width: int,
    query_height: int,
    minimum_score: float,
    min_inliers: int,
    min_inlier_ratio: float,
    max_reprojection_error_px: float,
    min_grid_coverage: float,
) -> dict[str, Any]:
    """Produce a shadow-only calibrated camera pose candidate from 3D landmarks."""

    query = np.asarray(query_keypoints, dtype=np.float64).reshape(-1, 2)
    map_points = np.asarray(map_points_enu_m, dtype=np.float64)
    if map_points.ndim < 2 or map_points.shape[-1] != 3:
        return {"status": "unavailable", "reason": "invalid_map_points_enu_shape"}
    map_points = map_points.reshape(-1, 3)
    pairs = np.asarray(match_pairs)
    values = np.asarray(scores, dtype=np.float32).reshape(-1)
    matrix = np.asarray(camera_matrix, dtype=np.float64)
    if matrix.shape != (3, 3) or not np.all(np.isfinite(matrix)):
        return {"status": "unavailable", "reason": "invalid_calibrated_camera_matrix"}
    if (matrix[0, 0] <= 0 or matrix[1, 1] <= 0 or not np.allclose(matrix[2], [0, 0, 1])
            or not np.isfinite(query).all() or not np.isfinite(map_points).all()):
        return {"status": "unavailable", "reason": "invalid_camera_or_landmark_values"}
    if pairs.ndim < 2 or pairs.shape[-1] != 2:
        return {"status": "unavailable", "reason": "invalid_match_pair_shape"}
    pairs = pairs.reshape(-1, 2)
    if values.size != len(pairs) or not np.all(np.isfinite(values)):
        return {"status": "unavailable", "reason": "match_score_count_or_values_invalid"}
    rounded = np.rint(pairs)
    if not np.allclose(pairs, rounded):
        return {"status": "unavailable", "reason": "match_pairs_are_not_integer_indices"}
    pairs = rounded.astype(np.int64)
    valid = (
        (pairs[:, 0] >= 0)
        & (pairs[:, 1] >= 0)
        & (pairs[:, 0] < len(query))
        & (pairs[:, 1] < len(map_points))
        & (values >= minimum_score)
    )
    filtered = pairs[valid]
    if len(filtered) < 6:
        return {"status": "rejected", "reason": "insufficient_scored_3d_correspondences", "candidate_count": int(len(filtered))}
    object_points = map_points[filtered[:, 1]]
    image_points = query[filtered[:, 0]]
    if (np.linalg.matrix_rank(object_points - object_points.mean(axis=0)) < 2
            or np.linalg.matrix_rank(image_points - image_points.mean(axis=0)) < 2):
        return {"status": "rejected", "reason": "degenerate_pnp_spatial_support", "candidate_count": int(len(filtered))}
    try:
        ok, rvec, tvec, inliers = cv2.solvePnPRansac(
            object_points,
            image_points,
            matrix,
            None,
            iterationsCount=10_000,
            reprojectionError=max_reprojection_error_px,
            confidence=0.999,
            flags=cv2.SOLVEPNP_AP3P,
        )
    except cv2.error as exc:
        return {"status": "rejected", "reason": f"pnp_solver_error:{exc}", "candidate_count": int(len(filtered))}
    if not ok or inliers is None or len(inliers) == 0:
        return {"status": "rejected", "reason": "pnp_fit_failed", "candidate_count": int(len(filtered))}
    inlier_indices = inliers.reshape(-1)
    inlier_mask = np.zeros(len(filtered), dtype=bool)
    inlier_mask[inlier_indices] = True
    try:
        rvec, tvec = cv2.solvePnPRefineLM(object_points[inlier_indices], image_points[inlier_indices], matrix, None, rvec, tvec)
    except cv2.error as exc:
        return {"status": "rejected", "reason": f"pnp_refinement_error:{exc}", "candidate_count": int(len(filtered))}
    if not np.isfinite(rvec).all() or not np.isfinite(tvec).all():
        return {"status": "rejected", "reason": "pnp_nonfinite_pose"}
    projected, _ = cv2.projectPoints(object_points, rvec, tvec, matrix, None)
    residual = np.linalg.norm(projected.reshape(-1, 2) - image_points, axis=1)
    rmse = float(np.sqrt(np.mean(residual[inlier_mask] ** 2)))
    inlier_count = int(np.count_nonzero(inlier_mask))
    inlier_ratio = float(inlier_count / len(filtered))
    coverage = _grid_coverage(image_points[inlier_mask], width=query_width, height=query_height)
    rotation, _ = cv2.Rodrigues(rvec)
    camera_position = (-rotation.T @ tvec.reshape(3)).astype(float)
    depths = (rotation @ object_points[inlier_mask].T + tvec.reshape(3, 1))[2]
    cheirality = bool(np.all(depths > 0))
    accepted = (
        inlier_count >= min_inliers
        and inlier_ratio >= min_inlier_ratio
        and rmse <= max_reprojection_error_px
        and coverage >= min_grid_coverage
        and cheirality
        and np.isfinite(camera_position).all()
    )
    return {
        "status": "accepted" if accepted else "rejected",
        "reason": "pnp_geometry_gate_passed" if accepted else "pnp_geometry_gate_threshold_not_met",
        "candidate_count": int(len(filtered)),
        "inlier_count": inlier_count,
        "inlier_ratio": inlier_ratio,
        "reprojection_rmse_px": rmse,
        "inlier_grid_coverage": coverage,
        "camera_position_enu_m": [float(value) for value in camera_position],
        "camera_rotation_enu_to_optical": rotation.tolist(),
        "pose_solver": "opencv_AP3P_RANSAC_RefineLM_unweighted",
        "inlier_match_indices": np.flatnonzero(valid)[inlier_indices].tolist(),
        "inliers_in_front_of_camera": cheirality,
        "position_authority": False,
        "pose_covariance": None,
        "thresholds": {
            "minimum_score": float(minimum_score),
            "min_inliers": min_inliers,
            "min_inlier_ratio": min_inlier_ratio,
            "max_reprojection_error_px": max_reprojection_error_px,
            "min_grid_coverage": min_grid_coverage,
        },
    }
