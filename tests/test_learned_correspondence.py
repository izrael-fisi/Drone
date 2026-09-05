import json
import os
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from vision_nav.camera import load_camera_calibration
from vision_nav.neural.learned_correspondence import (
    LocalMatchingPolicy, LocalFeatures, PinnedLocalMatcher, resized_camera_matrix,
    matcher_checkpoint, validate_matches, match_image_pair,
)
from vision_nav.neural.replay_local_geometry import ReplayLocalVerifier
from vision_nav.neural.shadow_geometry import verify_pnp_correspondences
from test_map_conditioned_replay import artifacts, run
from test_metric_map_landmarks import _fixture as surface_fixture
from test_replay_retrieval import retrieval_artifacts, automatic


class FixtureMatcher:
    """Geometry/interface fixture only; actual checkpoints have separate tests."""
    def __init__(self, config):
        self.policy = LocalMatchingPolicy(image_width=64, image_height=64, max_features=32)
        self.provenance = {"backend": "synthetic_test_fixture"}
    def extract(self, image):
        points = np.array([[x, y] for y in (8, 24, 40, 56) for x in (8, 24, 40, 56)], np.float32)
        return LocalFeatures(points, np.eye(16, 64, dtype=np.float32), np.ones(16, np.float32))
    def match(self, query, tile):
        # The synthetic descriptors encode correspondence after DSM filtering.
        q = query.descriptors.argmax(axis=1)
        t = tile.descriptors.argmax(axis=1)
        pairs = np.array([(i, j) for i, a in enumerate(q) for j, b in enumerate(t) if a == b], np.int64).reshape(-1, 2)
        return pairs, np.ones(len(pairs), np.float32)


@pytest.fixture
def local_fixture(monkeypatch, tmp_path):
    monkeypatch.setattr("vision_nav.neural.replay_local_geometry.load_local_matcher", FixtureMatcher)
    path = tmp_path / "local.json"
    path.write_text('{}')
    return path


def test_replay_records_ambiguous_local_geometry_without_position_authority(artifacts, local_fixture, tmp_path):
    result = run(artifacts, tmp_path / "out", local_verification_config=local_fixture)
    assert result["counts"]["model_windows"] == 1
    rows = [json.loads(line) for line in (tmp_path / "out" / "trace.jsonl").read_text().splitlines()]
    local = rows[-1]["local_verification"]
    assert local["reason"] == "ambiguous_multiple_geometric_candidates"
    assert len(local["accepted_tile_ids"]) == 2
    assert local["position_authority"] is False
    assert all(a["covariance"] is None and a["geometry"]["status"] == "accepted" for a in local["attempts"])
    assert rows[-1]["navigation_mode"] == "NO_GLOBAL_FIX"


def test_one_candidate_is_not_a_global_fix(artifacts, local_fixture, tmp_path):
    run(artifacts, tmp_path / "out", local_verification_config=local_fixture, candidate_tile_ids=["tile_000000"])
    row = json.loads((tmp_path / "out" / "trace.jsonl").read_text().splitlines()[-1])
    assert row["local_verification"]["reason"] == "single_consistent_candidate_not_global_authority"
    assert row["position_authority"] is False
    assert row["local_verification"]["candidate_selection"]["status"] == "unique"


def test_provider_failure_keeps_each_attempt_and_clears_window(artifacts, local_fixture, tmp_path, monkeypatch):
    def fail(*args):
        raise RuntimeError("matcher_test_failure")
    monkeypatch.setattr(FixtureMatcher, "match", fail)
    result = run(artifacts, tmp_path / "out", local_verification_config=local_fixture)
    row = json.loads((tmp_path / "out" / "trace.jsonl").read_text().splitlines()[-1])
    assert result["counts"]["window_resets"] == 1
    assert row["local_verification"]["complete"] is False
    assert all(a["reason"] == "matcher_test_failure" for a in row["local_verification"]["attempts"])


def test_surface_backed_reference_preserves_metric_points(local_fixture, tmp_path):
    result, spec_path, _ = surface_fixture(tmp_path)
    local_fixture.write_text(json.dumps({"elevation_spec": str(spec_path)}))
    calibration = SimpleNamespace(camera_matrix=np.array([[40, 0, 31.5], [0, 40, 31.5], [0, 0, 1]]),
                                  distortion_coefficients=np.zeros(5), image_size=(64, 64))
    verifier = ReplayLocalVerifier(local_fixture, Path(result["layout_path"]), calibration)
    image = tmp_path / "query.png"
    cv2.imwrite(str(image), np.full((64, 64, 3), 120, np.uint8))
    evidence = verifier.verify(image, ["tile_000000"])
    attempt = evidence["attempts"][0]
    assert evidence["geometry_mode"] == "surface_backed_pnp_camera_candidate"
    assert attempt["status"] == "verified"
    points = np.array(attempt["matched_map_points_enu_m"])
    assert points.shape == (16, 3)
    np.testing.assert_allclose(points[:, 2], 5, atol=0.01)
    np.testing.assert_allclose(attempt["matched_map_variance_enu_m2"], np.tile([0.25, 0.25, 1], (16, 1)))
    assert attempt["covariance"] is None
    assert len(attempt["matched_query_feature_indices"]) == len(points)
    assert len(attempt["matched_map_feature_indices"]) == len(points)


def test_surface_all_nodata_does_not_fall_back_to_a_plane(local_fixture, tmp_path):
    rasterio = pytest.importorskip("rasterio")
    from vision_nav.training_capture import sha256_file
    result, spec_path, spec = surface_fixture(tmp_path)
    with rasterio.open(tmp_path / "surface.tif", "r+") as surface:
        surface.write(np.full((1, 16, 16), -9999, np.float32))
    spec["sha256"] = sha256_file(tmp_path / "surface.tif")
    spec_path.write_text(json.dumps(spec))
    local_fixture.write_text(json.dumps({"elevation_spec": str(spec_path)}))
    calibration = SimpleNamespace(camera_matrix=np.eye(3), distortion_coefficients=np.zeros(5), image_size=(64, 64))
    verifier = ReplayLocalVerifier(local_fixture, Path(result["layout_path"]), calibration)
    image = tmp_path / "query.png"
    cv2.imwrite(str(image), np.full((64, 64, 3), 120, np.uint8))
    evidence = verifier.verify(image, ["tile_000000"])
    assert evidence["attempts"][0]["geometry"]["reason"] == "insufficient_scored_3d_correspondences"
    assert evidence["accepted_tile_ids"] == []


def test_local_feature_cache_is_bounded(artifacts, local_fixture):
    verifier = ReplayLocalVerifier(local_fixture, artifacts / "tiles" / "tile_layout.json",
                                   load_camera_calibration(artifacts / "camera.yaml"))
    tile = verifier.tiles["tile_000000"]
    for index in range(8):
        verifier.tiles[str(index)] = {**tile, "tile_id": str(index)}
        verifier._map_features(str(index))
        assert len(verifier.cache) <= 4
    assert list(verifier.cache) == ["4", "5", "6", "7"]


def test_pixel_center_intrinsics_resize_matches_projected_pixels():
    matrix = np.array([[400, 0, 319.5], [0, 410, 239.5], [0, 0, 1]])
    updated = resized_camera_matrix(matrix, (640, 480), (320, 384))
    point = np.array([0.2, -0.1, 2])
    original = matrix @ point
    original = original[:2] / original[2]
    projected = updated @ point
    np.testing.assert_allclose(projected[:2] / projected[2], (original + 0.5) * [0.5, 0.8] - 0.5)


def test_matcher_weights_allow_only_computed_threshold_buffer():
    wanted = {"w": 1, "confidence_thresholds": 2}
    assert matcher_checkpoint({"matcher.w": 1, "extractor.model.net.w": 9}, wanted, 6) == wanted
    with pytest.raises(ValueError, match="parameter set"):
        matcher_checkpoint({}, wanted, 6)
    with pytest.raises(ValueError, match="Duplicate"):
        matcher_checkpoint({"w": 1, "matcher.w": 1}, wanted, 6)


@pytest.mark.parametrize("policy", [{"image_width": 65}, {"max_features": 1}, {"max_features": 4096},
                                    {"min_inlier_ratio": float("nan")}, {"min_inliers": 4}, {"minimum_score": 0}])
def test_reference_policy_rejects_invalid_limits(policy):
    with pytest.raises(ValueError):
        LocalMatchingPolicy(**policy).validate()


def test_correspondence_duplicates_or_out_of_range_rejected():
    features = FixtureMatcher(None).extract(None)
    for pairs in (np.array([[0, 0], [0, 1]]), np.array([[0, 99]]), np.array([[0.5, 1.0]])):
        with pytest.raises(ValueError):
            validate_matches(features, features, pairs, np.ones(len(pairs), np.float32))


def pnp_case():
    rng = np.random.default_rng(14)
    points = rng.uniform([-8, -5, 0], [8, 5, 3], (60, 3)).astype(np.float32)
    matrix = np.array([[400, 0, 320], [0, 400, 192], [0, 0, 1]], dtype=float)
    pixels, _ = cv2.projectPoints(points, np.zeros(3), np.array([0.0, 0.0, 20.0]), matrix, None)
    return dict(query_keypoints=pixels.reshape(-1, 2), map_points_enu_m=points,
                match_pairs=np.c_[np.arange(60), np.arange(60)], scores=np.ones(60), camera_matrix=matrix,
                query_width=640, query_height=384, **LocalMatchingPolicy().geometry_kwargs())


def test_pnp_refits_pose_and_keeps_covariance_unset():
    result = verify_pnp_correspondences(**pnp_case())
    assert result["status"] == "accepted"
    np.testing.assert_allclose(result["camera_position_enu_m"], [0, 0, -20], atol=1e-4)
    np.testing.assert_allclose(result["camera_rotation_enu_to_optical"], np.eye(3), atol=1e-5)
    assert result["pose_covariance"] is None and result["position_authority"] is False
    assert result["inliers_in_front_of_camera"] is True


@pytest.mark.parametrize("failure", ["collinear", "nonfinite", "bad_focal"])
def test_pnp_rejects_invalid_geometry(failure):
    case = pnp_case()
    if failure == "collinear":
        case["map_points_enu_m"] = np.c_[np.arange(60), np.zeros((60, 2))]
    elif failure == "nonfinite":
        case["map_points_enu_m"][0, 0] = np.nan
    else:
        case["camera_matrix"][0, 0] = -1
    assert verify_pnp_correspondences(**case)["status"] != "accepted"


@pytest.fixture(scope="module")
def official_config():
    path = os.environ.get("VISION_NAV_LOCAL_REFERENCE_CONFIG")
    if not path:
        pytest.skip("Set VISION_NAV_LOCAL_REFERENCE_CONFIG for explicitly provisioned pretrained checkpoint tests")
    return Path(path)


def test_official_pretrained_image_pair(official_config):
    config = json.loads(official_config.read_text())
    checkout = (official_config.parent / config["checkout"]).resolve()
    result = match_image_pair(official_config, checkout / "assets" / "ref.png", checkout / "assets" / "tgt.png")
    assert result["match_count"] >= 20
    assert result["geometry"]["status"] == "accepted"
    assert result["provenance"]["backend"] == "pytorch_reference"
    assert result["position_authority"] is False


def test_official_pretrained_blank_image_has_no_matches(official_config):
    matcher = PinnedLocalMatcher(official_config)
    features = matcher.extract(np.zeros((384, 640, 3), np.uint8))
    pairs, scores = matcher.match(features, features)
    assert len(pairs) == len(scores) == 0


def test_automatic_replay_reaches_real_pretrained_local_matcher(official_config, artifacts, retrieval_artifacts, tmp_path):
    result = automatic(artifacts, retrieval_artifacts, tmp_path / "out", local_verification_config=official_config)
    assert result["counts"]["retrieval_calls"] == result["counts"]["model_windows"] == 1
    row = json.loads((tmp_path / "out" / "trace.jsonl").read_text().splitlines()[-1])
    local = row["local_verification"]
    assert local["complete"] is True
    assert len(local["attempts"]) == 2
    assert all(a["status"] == "verified" for a in local["attempts"])
    assert row["position_authority"] is False
    assert result["provenance"]["local_verification"]["backend"] == "pytorch_reference"
