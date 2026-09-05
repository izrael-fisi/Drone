"""Explicit bounded refit after full learned-match consensus; never covariance."""

from copy import deepcopy
import hashlib
import json

import cv2
import numpy as np

from .shadow_geometry import verify_pnp_correspondences


FIT_POLICY = {"schema_version": "vision_nav_bounded_pnp_fit_v1",
    "method": "full_consensus_then_grid_balanced_refit", "max_correspondences": 24,
    "grid_rows": 4, "grid_columns": 4, "selection_uncertainty": "not_modeled_conditional_correct_inliers"}


def digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, allow_nan=False).encode()).hexdigest()


def _validate_source(source):
    n = len(source["query_pixels"])
    if not 1 <= n <= 2048:
        raise ValueError("Bounded PnP accepts at most 2048 source correspondences")
    for name, shape in (("query_pixels", (n, 2)), ("map_points_enu_m", (n, 3)),
                        ("scores", (n,)), ("camera_matrix", (3, 3))):
        values = np.asarray(source[name], dtype=float)
        if values.shape != shape or not np.isfinite(values).all():
            raise ValueError("Invalid bounded-fit source array: " + name)
    for name in ("query_feature_indices", "map_feature_indices"):
        ids = source[name]
        if (not isinstance(ids, list) or len(ids) != n
                or any(type(i) is not int or i < 0 for i in ids) or len(set(ids)) != n):
            raise ValueError("Source feature identities must be distinct nonnegative integers")
    size = source["image_size"]
    if not isinstance(size, list) or len(size) != 2 or any(type(v) is not int or v <= 0 for v in size):
        raise ValueError("Invalid calibrated image size")
    matrix = np.asarray(source["camera_matrix"])
    if (matrix[0, 0] <= 0 or matrix[1, 1] <= 0 or not np.allclose(matrix[2], [0, 0, 1])
            or matrix[0, 1] != 0 or matrix[1, 0] != 0):
        raise ValueError("Expected rectified zero-skew camera intrinsics")
    thresholds = source["thresholds"]
    if (type(thresholds["min_inliers"]) is not int or thresholds["min_inliers"] < 6
            or any(not np.isfinite(thresholds[k]) or not 0 <= thresholds[k] <= 1
                   for k in ("minimum_score", "min_inlier_ratio", "min_grid_coverage"))
            or not np.isfinite(thresholds["max_reprojection_error_px"])
            or thresholds["max_reprojection_error_px"] <= 0):
        raise ValueError("Invalid bounded-fit geometry thresholds")


def select_indices(source, inliers, policy=FIT_POLICY):
    """Round-robin occupied image cells, score then feature-ID tie breaking."""
    n = len(source["query_pixels"])
    if (not isinstance(inliers, list) or not inliers
            or any(type(i) is not int or not 0 <= i < n for i in inliers)
            or len(set(inliers)) != len(inliers)):
        raise ValueError("Invalid original consensus indices")
    width, height = source["image_size"]
    cells = {}
    for index in inliers:
        x, y = source["query_pixels"][index]
        if not 0 <= x < width or not 0 <= y < height:
            raise ValueError("Original consensus point is outside the calibrated image")
        cell = (min(policy["grid_rows"]-1, int(y/height*policy["grid_rows"])),
                min(policy["grid_columns"]-1, int(x/width*policy["grid_columns"])))
        cells.setdefault(cell, []).append(index)
    def key(i):
        return (-source["scores"][i], source["query_feature_indices"][i], source["map_feature_indices"][i])
    queues = {cell: sorted(values, key=key) for cell, values in cells.items()}
    selected, depth = [], 0
    while len(selected) < min(len(inliers), policy["max_correspondences"]):
        choices = [values[depth] for values in queues.values() if len(values) > depth]
        selected.extend(sorted(choices, key=key)[:policy["max_correspondences"]-len(selected)])
        depth += 1
    return sorted(selected)


def _consensus_projection(source, inliers, geometry):
    rotation = np.asarray(geometry["camera_rotation_enu_to_optical"], dtype=float)
    position = np.asarray(geometry["camera_position_enu_m"], dtype=float)
    if (rotation.shape != (3, 3) or position.shape != (3,) or not np.isfinite(rotation).all()
            or not np.isfinite(position).all() or not np.allclose(rotation.T @ rotation, np.eye(3), atol=1e-8)
            or not np.isclose(np.linalg.det(rotation), 1., atol=1e-8)):
        raise ValueError("Invalid bounded-fit pose")
    objects = np.asarray(source["map_points_enu_m"], dtype=float)[inliers]
    translation = -rotation @ position
    projected, _ = cv2.projectPoints(objects, cv2.Rodrigues(rotation)[0], translation,
                                     np.asarray(source["camera_matrix"], dtype=float), None)
    residual = projected.reshape(-1, 2)-np.asarray(source["query_pixels"])[inliers]
    rmse = float(np.sqrt(np.mean(np.sum(residual**2, axis=1))))
    positive = bool(np.all((rotation @ objects.T+translation[:, None])[2] > 0))
    return rmse, positive


def verify_bounded_pnp(*, query_keypoints, map_points_enu_m, match_pairs, scores, camera_matrix,
                       query_width, query_height, **thresholds):
    """Return final geometry plus raw match indices actually used for that fit.

    Full-set acceptance is mandatory before reducing an oversized observation.
    A reduced fit must pass its own gates and still explain the original consensus.
    """
    pairs = np.asarray(match_pairs).reshape(-1, 2)
    values = np.asarray(scores).reshape(-1)
    query = np.asarray(query_keypoints).reshape(-1, 2)
    points = np.asarray(map_points_enu_m).reshape(-1, 3)
    if (len(pairs) > 2048 or not np.issubdtype(pairs.dtype, np.integer) or len(values) != len(pairs)
            or not np.isfinite(values).all() or not np.isfinite(query).all() or not np.isfinite(points).all()
            or np.any(pairs < 0) or np.any(pairs[:, 0] >= len(query)) or np.any(pairs[:, 1] >= len(points))):
        raise ValueError("Bounded PnP requires checked finite correspondence arrays")
    if len(set(pairs[:, 0].tolist())) != len(pairs) or len(set(pairs[:, 1].tolist())) != len(pairs):
        raise ValueError("Bounded PnP requires one-to-one learned feature identities")
    if thresholds["min_inliers"] > FIT_POLICY["max_correspondences"]:
        return {"status": "unavailable", "reason": "bounded_fit_budget_below_minimum_inliers"}, np.arange(len(pairs))
    common = dict(query_keypoints=query, map_points_enu_m=points, camera_matrix=camera_matrix,
                  query_width=query_width, query_height=query_height, **thresholds)
    original = verify_pnp_correspondences(match_pairs=pairs, scores=values, **common)
    if original["status"] != "accepted" or len(pairs) <= FIT_POLICY["max_correspondences"]:
        return original, np.arange(len(pairs))
    source = {"query_pixels": query[pairs[:, 0]].tolist(), "map_points_enu_m": points[pairs[:, 1]].tolist(),
        "query_feature_indices": pairs[:, 0].tolist(), "map_feature_indices": pairs[:, 1].tolist(),
        "scores": values.tolist(), "image_size": [query_width, query_height],
        "camera_matrix": np.asarray(camera_matrix).tolist(), "thresholds": thresholds}
    _validate_source(source)
    selected = select_indices(source, original["inlier_match_indices"])
    refit = verify_pnp_correspondences(match_pairs=pairs[selected], scores=values[selected], **common)
    selection = {"policy": deepcopy(FIT_POLICY), "source_correspondences": source,
        "source_sha256": digest(source), "original_consensus": original,
        "selected_source_match_indices": selected, "original_match_count": len(pairs),
        "fit_match_count": len(selected), "position_authority": False}
    if refit["status"] == "accepted":
        # Re-check the full original consensus at the bounded-fit pose. A small
        # selected set must not rescue a fit that contradicts the wider evidence.
        rmse, positive = _consensus_projection(source, original["inlier_match_indices"], refit)
        selection.update(original_consensus_refit_rmse_px=rmse, original_consensus_in_front=positive)
        if not np.isfinite(rmse) or rmse > thresholds["max_reprojection_error_px"] or not positive:
            refit = {**refit, "status": "rejected", "reason": "bounded_refit_disagrees_with_original_consensus"}
    return {**refit, "fit_selection": selection}, np.asarray(selected, dtype=np.int64)


def validate_bound_attempt(attempt):
    """Reproduce selection and exact estimator inputs, without fitting another pose."""
    selection = attempt["geometry"].get("fit_selection")
    if selection is None:
        return
    if selection.get("policy") != FIT_POLICY or selection.get("position_authority") is not False:
        raise ValueError("Unknown or altered bounded-fit policy")
    source, original = selection["source_correspondences"], selection["original_consensus"]
    _validate_source(source)
    count = len(source["query_pixels"])
    if (selection["source_sha256"] != digest(source) or selection["original_match_count"] != count
            or original.get("status") != "accepted" or not count > FIT_POLICY["max_correspondences"]
            or len(source["map_points_enu_m"]) != count or len(source["scores"]) != count
            or len(source["query_feature_indices"]) != count or len(source["map_feature_indices"]) != count):
        raise ValueError("Bounded-fit original source/consensus is inconsistent")
    indices = select_indices(source, original["inlier_match_indices"])
    inliers = original["inlier_match_indices"]
    thresholds = source["thresholds"]
    scored = np.asarray(source["scores"], dtype=np.float32) >= thresholds["minimum_score"]
    cells = {(int(source["query_pixels"][i][0]/source["image_size"][0]*4),
              int(source["query_pixels"][i][1]/source["image_size"][1]*4)) for i in inliers}
    if (original.get("thresholds") != thresholds or attempt["geometry"].get("thresholds") != thresholds
            or original.get("position_authority") is not False or original.get("pose_covariance") is not None
            or original.get("inlier_count") != len(inliers) or original.get("candidate_count") != int(scored.sum())
            or not scored[inliers].all() or len(inliers) < thresholds["min_inliers"]
            or len(inliers)/int(scored.sum()) < thresholds["min_inlier_ratio"]
            or len(cells)/16 < thresholds["min_grid_coverage"]):
        raise ValueError("Original bounded-fit consensus does not satisfy its gates")
    original_rmse, original_positive = _consensus_projection(source, inliers, original)
    if (not original_positive or not np.isfinite(original_rmse)
            or original_rmse > thresholds["max_reprojection_error_px"]):
        raise ValueError("Original consensus pose contradicts its observations")
    if indices != selection["selected_source_match_indices"] or len(indices) != selection["fit_match_count"]:
        raise ValueError("Bounded-fit selection cannot be reproduced")
    for name, source_name in (("matched_query_pixels", "query_pixels"), ("matched_map_points_enu_m", "map_points_enu_m"),
                              ("matched_query_feature_indices", "query_feature_indices"),
                              ("matched_map_feature_indices", "map_feature_indices"), ("matcher_scores", "scores")):
        if attempt[name] != [source[source_name][i] for i in indices]:
            raise ValueError("Estimator inputs differ from bounded-fit selected correspondences")
    if attempt["calibrated_camera_matrix"] != source["camera_matrix"]:
        raise ValueError("Bounded-fit camera matrix differs")
    for name, feature in (("query_feature_indices", "query"), ("map_feature_indices", "map")):
        size = attempt["feature_counts"][feature]
        if type(size) is not int or any(i >= size for i in source[name]):
            raise ValueError("Original feature identities exceed source feature counts")
    rmse, positive = _consensus_projection(source, inliers, attempt["geometry"])
    if (not positive or selection.get("original_consensus_in_front") is not True or not np.isfinite(rmse)
            or rmse > thresholds["max_reprojection_error_px"]
            or not np.isclose(rmse, selection.get("original_consensus_refit_rmse_px", float("nan")), rtol=1e-7, atol=1e-7)):
        raise ValueError("Bounded fit did not retain original consensus support")
