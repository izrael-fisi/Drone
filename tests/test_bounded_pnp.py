"""Explicit learned-fit budgets with synthetic geometry, not field accuracy."""

import copy
from unittest.mock import patch

import cv2
import numpy as np
import pytest

from vision_nav.neural.bounded_pnp import (
    FIT_POLICY, digest, select_indices, validate_bound_attempt, verify_bounded_pnp,
)
from vision_nav.neural.shadow_geometry import verify_pnp_correspondences


def inputs(n=192):
    rng = np.random.default_rng(721)
    pixels = rng.uniform([5., 5.], [635., 379.], (n, 2))
    matrix = np.array([[400., 0., 319.5], [0., 410., 191.5], [0., 0., 1.]])
    depths = rng.uniform(20, 35, n)
    points = np.c_[(pixels[:, 0]-matrix[0, 2])*depths/matrix[0, 0],
                   (pixels[:, 1]-matrix[1, 2])*depths/matrix[1, 1], depths]
    return dict(query_keypoints=pixels, map_points_enu_m=points,
        match_pairs=np.c_[np.arange(n), np.arange(n)], scores=np.ones(n), camera_matrix=matrix,
        query_width=640, query_height=384, minimum_score=.2, min_inliers=12,
        min_inlier_ratio=.4, max_reprojection_error_px=3., min_grid_coverage=.25)


def attempt_fixture():
    args = inputs()
    geometry, selected = verify_bounded_pnp(**args)
    assert geometry["status"] == "accepted", geometry
    pairs = args["match_pairs"][selected]
    return {"geometry": geometry, "position_authority": False,
        "matched_query_pixels": args["query_keypoints"][pairs[:, 0]].tolist(),
        "matched_map_points_enu_m": args["map_points_enu_m"][pairs[:, 1]].tolist(),
        "matched_query_feature_indices": pairs[:, 0].tolist(),
        "matched_map_feature_indices": pairs[:, 1].tolist(),
        "matcher_scores": args["scores"][selected].tolist(),
        "feature_counts": {"query": len(args["query_keypoints"]), "map": len(args["map_points_enu_m"])},
        "calibrated_camera_matrix": args["camera_matrix"].tolist()}


def test_full_consensus_precedes_bounded_refit_and_retains_source():
    args = inputs()
    with patch("vision_nav.neural.bounded_pnp.verify_pnp_correspondences", wraps=verify_pnp_correspondences) as solver:
        geometry, selected = verify_bounded_pnp(**args)
    assert [len(c.kwargs["match_pairs"]) for c in solver.call_args_list] == [192, 24]
    assert geometry["status"] == "accepted", geometry
    audit = geometry["fit_selection"]
    assert audit["original_consensus"]["inlier_count"] == 192
    assert audit["source_sha256"] == digest(audit["source_correspondences"])
    assert audit["selected_source_match_indices"] == selected.tolist()
    assert geometry["inlier_grid_coverage"] == 1.
    assert audit["original_consensus_refit_rmse_px"] < 1e-5
    assert geometry["pose_covariance"] is None and not geometry["position_authority"]
    assert 8*(5*len(selected)+4) <= 1024


def test_selection_is_repeatable_spatial_and_stable_by_feature_identity():
    attempt = attempt_fixture()
    audit = attempt["geometry"]["fit_selection"]
    source = audit["source_correspondences"]
    first = select_indices(source, audit["original_consensus"]["inlier_match_indices"])
    assert first == select_indices(source, list(reversed(audit["original_consensus"]["inlier_match_indices"])))
    permutation = np.random.default_rng(4).permutation(192).tolist()
    shuffled = copy.deepcopy(source)
    for key in ("query_pixels", "map_points_enu_m", "query_feature_indices", "map_feature_indices", "scores"):
        shuffled[key] = [source[key][i] for i in permutation]
    second = select_indices(shuffled, list(range(192)))
    assert sorted(source["query_feature_indices"][i] for i in first) == sorted(shuffled["query_feature_indices"][i] for i in second)


def test_failed_full_consensus_is_not_rescued_by_subset():
    args = inputs()
    args["query_keypoints"] = np.random.default_rng(82).uniform([0., 0.], [639., 383.], (192, 2))
    with patch("vision_nav.neural.bounded_pnp.verify_pnp_correspondences", wraps=verify_pnp_correspondences) as solver:
        geometry, selected = verify_bounded_pnp(**args)
    assert geometry["status"] != "accepted"
    assert solver.call_count == 1 and len(selected) == 192
    assert "fit_selection" not in geometry


def test_refit_must_explain_original_consensus():
    args = inputs()
    original = verify_pnp_correspondences(**args)
    invalid_refit = copy.deepcopy(original)
    invalid_refit["camera_position_enu_m"][0] += 4
    with patch("vision_nav.neural.bounded_pnp.verify_pnp_correspondences", side_effect=[original, invalid_refit]):
        geometry, _ = verify_bounded_pnp(**args)
    assert geometry["status"] == "rejected"
    assert geometry["reason"] == "bounded_refit_disagrees_with_original_consensus"


def test_small_sets_keep_existing_fit_and_inlier_identity():
    args = inputs(24)
    original = verify_pnp_correspondences(**args)
    result, indices = verify_bounded_pnp(**args)
    assert result == original and indices.tolist() == list(range(24))
    assert "fit_selection" not in result


def test_explicit_budget_does_not_silently_weaken_minimum_inliers():
    args = inputs()
    args["min_inliers"] = 25
    result, _ = verify_bounded_pnp(**args)
    assert result["status"] == "unavailable"
    assert result["reason"] == "bounded_fit_budget_below_minimum_inliers"


@pytest.mark.parametrize("change", ["source_hash", "selected", "pixel", "score", "camera", "policy",
    "short_source", "invalid_shape", "duplicate_feature", "source_feature_range", "inlier_shape",
    "inlier_count", "thresholds", "full_pose", "final_pose", "saved_residual"])
def test_changed_or_malformed_selection_cannot_enter_estimator(change):
    attempt = attempt_fixture()
    validate_bound_attempt(attempt)
    audit = attempt["geometry"]["fit_selection"]
    source = audit["source_correspondences"]
    if change == "source_hash": audit["source_sha256"] = "0"*64
    elif change == "selected": audit["selected_source_match_indices"][0] = 191
    elif change == "pixel": attempt["matched_query_pixels"][0][0] += 1
    elif change == "score": attempt["matcher_scores"][0] = .1
    elif change == "camera": attempt["calibrated_camera_matrix"][0][0] += 1
    elif change == "policy": audit["policy"]["max_correspondences"] = 25
    elif change == "short_source": source["scores"] = []
    elif change == "invalid_shape": source["query_pixels"][0] = [1.]
    elif change == "duplicate_feature": source["query_feature_indices"][0] = source["query_feature_indices"][1]
    elif change == "source_feature_range": attempt["feature_counts"]["query"] = 2
    elif change == "inlier_shape": audit["original_consensus"]["inlier_match_indices"][0] = []
    elif change == "inlier_count": audit["original_consensus"]["inlier_count"] = 1
    elif change == "thresholds": source["thresholds"]["min_grid_coverage"] = 0.
    elif change == "full_pose": audit["original_consensus"]["camera_position_enu_m"][0] += 10
    elif change == "final_pose": attempt["geometry"]["camera_position_enu_m"][0] += 10
    elif change == "saved_residual": audit["original_consensus_refit_rmse_px"] = 10.
    if change != "source_hash": audit["source_sha256"] = digest(source)
    with pytest.raises(ValueError):
        validate_bound_attempt(attempt)


def test_bounded_fit_binds_and_propagates_explicit_synthetic_uncertainty():
    from test_learned_pnp_binding import envelope
    from test_reprojection_uncertainty import fixture
    from vision_nav.estimation.correlated_window import payload_digest
    from vision_nav.estimation.learned_pnp_binding import prepare, bind_uncertainty

    request = fixture()[0]
    row, kwargs, noise = envelope(request)
    attempt = attempt_fixture()
    previous = row["local_verification"]["attempts"][0]
    for key in ("status", "tile_id", "image_sha256"):
        attempt[key] = previous[key]
    row["local_verification"]["attempts"] = [attempt]
    from vision_nav.neural.candidate_groups import select_candidate_groups
    row["local_verification"]["candidate_selection"] = select_candidate_groups([attempt], complete=True)
    draft = prepare(row, **kwargs)
    assert draft["status"] == "awaiting_joint_noise", draft
    n = len(attempt["matched_query_pixels"])
    cov = np.zeros((5*n+4, 5*n+4))
    cov[:2*n, :2*n] = np.eye(2*n)*.04
    noise.update(attempt_sha256=payload_digest(attempt), joint_measurement_covariance=cov.tolist())
    bound = bind_uncertainty(draft, noise)
    assert bound["status"] == "conditional_pnp_request", bound
    assert bound["assessment"]["publication_covariance"] is None
    assert not bound["covariance_calibrated"] and not bound["position_authority"]
