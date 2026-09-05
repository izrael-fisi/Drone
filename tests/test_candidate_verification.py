import numpy as np
import pytest

from vision_nav.neural.releases import ModelRelease
from vision_nav.neural.shadow_inference import ShadowFrame, run_shadow_frame_probe
from vision_nav.neural.shadow_hypotheses import ShadowCameraCandidateBank


def _model():
    image = {"shadow_source": "frame_rgb", "preprocess": {"image_size": [80, 80], "color_space": "rgb", "layout": "nchw"}}
    matcher = {"inputs": {name: {"shadow_source": source} for name, source in (
        ("q", "query_local_keypoints"), ("m", "map_tile_keypoints"), ("qd", "query_local_descriptors"), ("md", "map_tile_descriptors"))},
        "outputs": {"pairs": {"shadow_output": "match_indices", "shadow_encoding": "pairs_last_dim_2", "pair_order": "query_then_map"},
                    "scores": {"shadow_output": "match_scores", "shadow_encoding": "per_correspondence", "minimum_confidence": 0.5}},
        "geometry": {"mode": "homography_restricted", "query_image_size": [80, 80], "min_inliers": 8,
                     "min_inlier_ratio": 0.8, "max_reprojection_error_px": 1.0, "min_grid_coverage": 0.5}}
    return ModelRelease(release_id="test@1", profile_id="pi5_crossview_nav_v1", semantic_version="1.0.0",
                        checkpoint_digest_sha256="a" * 64, onnx_digest_sha256="b" * 64,
                        training_dataset_releases=("test@1",), source_revision="test", license_summary="test-only",
                        redistribution_allowed=False, calibration_ids=("cam@1",), artifact_paths={}, artifact_interfaces={
                            "global_retriever": {"inputs": {"image": image}, "outputs": {"embedding": {"shadow_output": "retrieval_embedding"}}},
                            "local_features": {"inputs": {"image": image}, "outputs": {
                                "keypoints": {"shadow_output": "local_keypoints"}, "descriptors": {"shadow_output": "local_descriptors"}}},
                            "local_matcher": matcher})


def _features(marker=1):
    return {"keypoints": np.asarray([[[x, y] for y in [10, 30, 50, 70] for x in [10, 30, 50, 70]]], dtype=np.float32),
            "descriptors": np.full((1, 16, 4), marker, dtype=np.float32)}


class _Runner:
    def __init__(self):
        self.calls = []

    def run(self, role, inputs):
        self.calls.append(role)
        if role == "global_retriever":
            return {"embedding": np.asarray([[1.0, 0.0]])}
        if role == "local_features":
            return _features()
        if inputs["md"][0, 0, 0] == -2:
            raise RuntimeError("provider failed")
        count = 16 if inputs["md"][0, 0, 0] > 0 else 3
        return {"pairs": np.asarray([[[i, i] for i in range(count)]]), "scores": np.ones((1, count), dtype=np.float32)}


def _probe(tmp_path, candidates, *, limit=4, bank=None):
    runner = _Runner()
    def lookup(tile):
        if tile == "missing":
            raise ValueError("missing asset")
        return _features(-1 if tile == "wrong" else (-2 if tile == "failed" else 1))
    probe = run_shadow_frame_probe(runner, model_release=_model(),
        frame=ShadowFrame(tmp_path / "synthetic.png", np.zeros((80, 80, 3), dtype=np.uint8)),
        candidate_lookup=lambda _: [{"tile_id": tile, "score": 1 - i / 100} for i, tile in enumerate(candidates)],
        tile_feature_lookup=lookup, max_candidate_verifications=limit, candidate_bank=bank)
    return probe, runner


def test_wrong_first_candidate_does_not_hide_correct_second(tmp_path):
    probe, runner = _probe(tmp_path, ["wrong", "correct"])
    assert runner.calls.count("local_features") == 1
    assert runner.calls.count("local_matcher") == 2
    assert probe["candidate_selection"]["accepted_tile_ids"] == ["correct"]
    assert probe["candidate_selection"]["status"] == "unique"
    assert probe["stages"][2]["candidate_tile_id"] == "correct"
    assert probe["geometry_evidence"]["status"] == "accepted"
    assert probe["shadow_tracking_candidate"]["status"] != "tracking_candidate"


def test_compiled_feature_pnp_uses_same_explicit_bounded_policy():
    from test_bounded_pnp import inputs
    from vision_nav.neural.shadow_inference import _local_matcher_geometry_evidence

    args = inputs()
    contract = _model().artifact_interfaces["local_matcher"]
    contract["geometry"].update(mode="pnp_3d", query_image_size=[640, 384])
    result = _local_matcher_geometry_evidence(contract,
        {"q": args["query_keypoints"][None], "m": args["query_keypoints"][None]},
        {"pairs": args["match_pairs"][None], "scores": args["scores"][None]},
        {"map_points_enu_m": args["map_points_enu_m"][None]}, args["camera_matrix"], (640, 384))
    assert result["status"] == "accepted", result
    assert result["fit_selection"]["original_match_count"] == 192
    assert result["fit_selection"]["fit_match_count"] == 24
    assert result["pose_covariance"] is None and not result["position_authority"]


def test_competing_geometry_is_withheld_and_breaks_consecutive_history(tmp_path):
    bank = ShadowCameraCandidateBank()
    bank.update(tile_id="first", camera_position_enu_m=[0, 0, 1], timestamp_monotonic_ns=1_000_000_000)
    probe, _ = _probe(tmp_path, ["first", "second"], bank=bank)
    assert probe["candidate_selection"]["reason"] == "ambiguous_multiple_geometric_candidates"
    assert probe["geometry_evidence"]["status"] == "withheld"
    assert probe["shadow_tracking_candidate"]["status"] == "withheld"
    assert bank.update(tile_id="first", camera_position_enu_m=[0, 0, 1], timestamp_monotonic_ns=2_000_000_000)["consecutive_count"] == 1


@pytest.mark.parametrize("other", ["missing", "failed"])
def test_incomplete_verification_cannot_claim_unique_fix(tmp_path, other):
    probe, _ = _probe(tmp_path, [other, "correct"])
    assert probe["candidate_selection"]["reason"] == "candidate_verification_incomplete"
    assert probe["candidate_selection"]["accepted_tile_ids"] == ["correct"]
    assert probe["geometry_evidence"]["status"] == "withheld"


def test_verification_budget_deduplicates_and_reports_unchecked_candidates(tmp_path):
    probe, runner = _probe(tmp_path, ["wrong", "wrong", "correct", "other"], limit=2)
    assert runner.calls.count("local_matcher") == 2
    assert probe["candidate_selection"]["candidate_count"] == 3
    assert probe["candidate_selection"]["truncated"] is True
    assert probe["candidate_selection"]["status"] == "withheld"


def test_wrong_map_produces_no_candidate(tmp_path):
    probe, _ = _probe(tmp_path, ["wrong"])
    assert probe["candidate_selection"]["reason"] == "no_geometric_candidate"
    assert probe["geometry_evidence"]["status"] == "rejected"


@pytest.mark.parametrize("limit", [0, 25, True, 2.5])
def test_invalid_budget_rejected(tmp_path, limit):
    with pytest.raises(ValueError):
        _probe(tmp_path, ["correct"], limit=limit)


@pytest.mark.parametrize("offset, expected", [(.2, "unique"), (10., "withheld")])
def test_native_pnp_candidate_grouping_and_resize_contract(tmp_path, offset, expected):
    import cv2
    from vision_nav.neural.learned_correspondence import resized_camera_matrix

    model = _model()
    model.artifact_interfaces["local_matcher"]["geometry"]["mode"] = "pnp_3d"
    matrix = np.array([[160., 0., 79.5], [0., 150., 59.5], [0., 0., 1.]])
    scaled = resized_camera_matrix(matrix, (160, 120), (80, 80))
    points = np.array([[x, y, .1*x*y] for y in [-3., -1., 1., 3.] for x in [-3., -1., 1., 3.]])
    pixels, _ = cv2.projectPoints(points, np.zeros(3), np.array([0., 0., 10.]), scaled, None)

    class Runner(_Runner):
        def run(self, role, inputs):
            if role == "local_features":
                self.calls.append(role)
                return {**_features(), "keypoints": pixels.reshape(1, -1, 2).astype(np.float32)}
            return super().run(role, inputs)

    def lookup(tile):
        return {**_features(), "map_points_enu_m": (points+[offset if tile == "b" else 0., 0., 0.])[None]}

    result = run_shadow_frame_probe(Runner(), model_release=model,
        frame=ShadowFrame(tmp_path / "fixture.png", np.zeros((120, 160, 3), np.uint8)),
        candidate_lookup=lambda _: [{"tile_id": "a", "score": .9}, {"tile_id": "b", "score": .8}],
        tile_feature_lookup=lookup, camera_matrix=matrix, camera_image_size=(160, 120))
    selection = result["candidate_selection"]
    assert selection["status"] == expected
    assert selection["accepted_tile_ids"] == ["a", "b"]
    assert not selection["position_authority"]
    attempts = result["stages"][2]["candidate_attempts"]
    assert len(attempts) == 2
    np.testing.assert_allclose(attempts[0]["geometry_evidence"]["camera_position_enu_m"], [0., 0., -10.], atol=1e-5)
    if expected == "unique":
        assert selection["groups"][0]["tile_ids"] == ["a", "b"]
        assert result["geometry_evidence"]["pose_covariance"] is None
