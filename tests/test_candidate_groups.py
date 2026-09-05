"""Overlap must not fabricate confidence or hide distinct pose hypotheses."""

import copy
import itertools

import cv2
import numpy as np
import pytest

from vision_nav.neural.candidate_groups import CandidateGroupingPolicy, landmark_bounds, select_candidate_groups


def candidate(tile, x=0., angle=0., bounds=(-5., -5., 5., 5.)):
    rotation, _ = cv2.Rodrigues(np.array([0., 0., np.radians(angle)]))
    return {"tile_id": tile, "landmark_support_bounds_enu_m": list(bounds),
            "geometry": {"status": "accepted", "camera_position_enu_m": [x, 0., 10.],
                         "camera_rotation_enu_to_optical": rotation.tolist(), "inlier_count": 20,
                         "reprojection_rmse_px": 1., "pose_covariance": None}}


def test_overlap_preserves_original_pose_without_double_counting():
    attempts = [candidate("b", .2), candidate("a")]
    attempts[0]["geometry"]["inlier_count"] = 30
    original = copy.deepcopy(attempts)
    result = select_candidate_groups(attempts, complete=True)
    assert result["status"] == "unique"
    assert result["groups"][0]["tile_ids"] == ["a", "b"]
    assert result["selected_tile_id"] == "b"
    assert result["confidence"] is None and result["covariance"] is None
    assert not result["groups"][0]["pose_fused"]
    assert not result["position_authority"] and not result["policy_calibrated"]
    assert attempts == original


@pytest.mark.parametrize("alternative", [candidate("b", 10.), candidate("b", angle=20.),
                                         candidate("b", bounds=(20., 20., 30., 30.))])
def test_different_pose_or_disjoint_map_stays_ambiguous(alternative):
    result = select_candidate_groups([candidate("a"), alternative], complete=True)
    assert result["status"] == "withheld"
    assert len(result["groups"]) == 2
    assert result["selected_tile_id"] is None


def test_chain_does_not_merge_distant_endpoints_and_is_order_independent():
    attempts = [candidate("a", 0.), candidate("b", .8), candidate("c", 1.6)]
    results = [select_candidate_groups(list(order), complete=True) for order in itertools.permutations(attempts)]
    assert all(r == results[0] for r in results)
    assert results[0]["nonclique_overlap"]
    assert len(results[0]["groups"]) == 3
    assert results[0]["status"] == "withheld"


def test_overlap_cannot_hide_incomplete_candidate_search():
    result = select_candidate_groups([candidate("a"), candidate("b", .1)], complete=False)
    assert len(result["groups"]) == 1
    assert result["reason"] == "candidate_verification_incomplete"
    assert result["selected_tile_id"] is None


def test_planar_matches_are_not_metric_duplicates():
    attempts = [{"tile_id": t, "geometry": {"status": "accepted"}} for t in ["a", "b"]]
    assert len(select_candidate_groups(attempts, complete=True)["groups"]) == 2


@pytest.mark.parametrize("failure", ["reflection", "nan_position", "bad_bounds"])
def test_invalid_accepted_metric_pose_is_withheld(failure):
    attempt = candidate("a")
    if failure == "reflection":
        attempt["geometry"]["camera_rotation_enu_to_optical"][0][0] = -1
    elif failure == "nan_position":
        attempt["geometry"]["camera_position_enu_m"][0] = float("nan")
    else:
        attempt["landmark_support_bounds_enu_m"] = [5, 5, 0, 0]
    result = select_candidate_groups([attempt], complete=True)
    assert result["status"] == "withheld" and result["validation_errors"]


@pytest.mark.parametrize("policy", [CandidateGroupingPolicy(max_position_separation_m=float("nan")),
                                  CandidateGroupingPolicy(max_rotation_separation_deg=181),
                                  CandidateGroupingPolicy(min_support_overlap_fraction=2),
                                  CandidateGroupingPolicy(max_position_separation_m=True)])
def test_invalid_policy_rejected(policy):
    with pytest.raises(ValueError):
        select_candidate_groups([], complete=True, policy=policy)


def test_duplicate_ids_rejected_and_support_box_validated():
    with pytest.raises(ValueError):
        select_candidate_groups([candidate("a"), candidate("a")], complete=True)
    assert landmark_bounds(np.zeros((6, 3))) is None
    assert landmark_bounds(np.zeros((2, 3))) is None
    assert landmark_bounds(None) is None
    points = np.array([[x, y, 3.] for x in [-2., 4.] for y in [-1., 3., 5.]])
    assert landmark_bounds(points[None]) == [-2., -1., 4., 5.]
