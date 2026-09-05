import hashlib
import json
from types import SimpleNamespace

import cv2
import numpy as np

from vision_nav.neural.map_release import validate_release
from vision_nav.neural.local_feature_cache import load_local_feature_cache
from vision_nav.neural.profiles import PI5_CROSSVIEW_NAV_V1
from vision_nav.neural.retrieval_index import FaissTileRetriever, load_retrieval_metadata
from vision_nav.neural.releases import ModelRelease
from vision_nav.neural.shadow_inference import load_shadow_frame, run_shadow_frame_probe
from vision_nav.neural.shadow_geometry import verify_planar_correspondences
from vision_nav.neural.shadow_geometry import verify_pnp_correspondences
from vision_nav.neural.shadow_hypotheses import ShadowCameraCandidateBank, ShadowCandidatePolicy
from vision_nav.neural.shadow_runtime import RuntimeCompatibility, _frame_calibration, check_runtime_compatibility, runtime_status_v3
from vision_nav.neural.temporal_window import TemporalWindowBuilder


def _model() -> ModelRelease:
    return ModelRelease(
        release_id="pi5-crossview-nav@1.0.0",
        profile_id="pi5_crossview_nav_v1",
        semantic_version="1.0.0",
        checkpoint_digest_sha256="a" * 64,
        onnx_digest_sha256="b" * 64,
        training_dataset_releases=("public@1",),
        source_revision="abc123",
        license_summary="test-only",
        redistribution_allowed=False,
        calibration_ids=("camera-v1",),
        artifact_paths={"temporal_student": "models/student.onnx"},
        teacher_lineage=("video2bev",),
    )


def _release(root):
    assets = {
        "cog_uri": "imagery/map.tif",
        "stac_item_uri": "stac/item.json",
        "tile_index_uri": "index/tiles.sqlite",
        "retrieval_index_uri": "index/megaloc.faiss",
        "xfeat_index_uri": "index/xfeat_tiles.sqlite",
    }
    for relative in assets.values():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"test")
    return {
        "schema_version": "vision_nav_map_release_v2",
        "release_id": "test-map@1",
        "map_id": "test-map",
        "version": "1",
        "profile_id": PI5_CROSSVIEW_NAV_V1.profile_id,
        "model_release_id": "pi5-crossview-nav@1.0.0",
        "crs": "EPSG:32610",
        "local_origin_enu": [0.0, 0.0, 0.0],
        "gsd_m_per_px": 0.2,
        **assets,
        "calibration_ids": ["camera-v1"],
        "geometry_modes": ["pnp_3d", "homography_restricted"],
        "checksums_sha256": {
            relative: hashlib.sha256((root / relative).read_bytes()).hexdigest()
            for relative in assets.values()
        },
    }


def test_learned_release_requires_matching_model_and_calibration(tmp_path):
    model = _model()
    release = _release(tmp_path)
    validate_release(release, root=tmp_path, model_release=model.to_dict())
    compatible = check_runtime_compatibility(
        release, model, calibration_id="camera-v1", provider="onnxruntime_cpu", root=tmp_path,
    )
    assert compatible.compatible
    wrong_calibration = check_runtime_compatibility(
        release, model, calibration_id="other-camera", provider="onnxruntime_cpu", root=tmp_path,
    )
    assert not wrong_calibration.compatible
    assert "camera_calibration_not_compatible_with_model" in wrong_calibration.reasons


def test_map_release_rejects_missing_or_tampered_learned_artifact_checksums(tmp_path):
    model = _model()
    release = _release(tmp_path)
    release["checksums_sha256"].pop("index/megaloc.faiss")
    try:
        validate_release(release, root=tmp_path, model_release=model.to_dict())
    except ValueError as exc:
        assert "SHA-256 checksum" in str(exc)
    else:
        raise AssertionError("Expected missing learned-artifact checksum to fail")

    release = _release(tmp_path)
    release["checksums_sha256"]["index/xfeat_tiles.sqlite"] = "0" * 64
    try:
        validate_release(release, root=tmp_path, model_release=model.to_dict())
    except ValueError as exc:
        assert "checksum mismatch" in str(exc)
    else:
        raise AssertionError("Expected tampered learned-artifact checksum to fail")


def test_shadow_status_never_claims_px4_publication(tmp_path):
    model = _model()
    release = _release(tmp_path)
    compatibility = check_runtime_compatibility(
        release, model, calibration_id="camera-v1", provider="onnxruntime_cpu", root=tmp_path,
    )
    status = runtime_status_v3(
        compatibility=compatibility, map_release=release, model_release=model,
        camera_health="ready", runtime_state="NO_GLOBAL_FIX", sequence=7,
    )
    assert status["schema_version"] == "vision_nav_runtime_status_v3"
    assert status["shadow_mode"] is True
    assert status["px4_publication"]["enabled"] is False
    assert json.loads(json.dumps(status))["active_model"]["release_id"] == model.release_id


def test_runtime_validation_rejects_missing_onnx_artifacts(tmp_path):
    model = _model()
    release = _release(tmp_path)
    compatibility = check_runtime_compatibility(
        release,
        model,
        calibration_id="camera-v1",
        provider="onnxruntime_cpu",
        root=tmp_path,
        model_root=tmp_path,
    )
    assert not compatibility.compatible
    assert "missing_model_artifact:temporal_student" in compatibility.reasons


def test_shadow_frame_probe_runs_only_release_defined_frame_stages(tmp_path):
    frame_path = tmp_path / "frame.jpg"
    assert cv2.imwrite(str(frame_path), np.full((12, 16, 3), (10, 20, 30), dtype=np.uint8))
    frame_contract = {
        "name": "image",
        "rank": 4,
        "shadow_source": "frame_rgb",
        "preprocess": {
            "image_size": [8, 6],
            "color_space": "rgb",
            "layout": "nchw",
            "dtype": "float32",
            "value_range": "zero_one",
        },
    }
    model = ModelRelease(
        release_id="shadow-probe@1",
        profile_id=PI5_CROSSVIEW_NAV_V1.profile_id,
        semantic_version="1.0.0",
        checkpoint_digest_sha256="a" * 64,
        onnx_digest_sha256="b" * 64,
        training_dataset_releases=("public@1",),
        source_revision="unit",
        license_summary="test-only",
        redistribution_allowed=False,
        calibration_ids=("camera-v1",),
        artifact_paths={},
        artifact_interfaces={
            "global_retriever": {
                "inputs": {"image": frame_contract},
                "outputs": {"embedding": {"name": "embedding", "rank": 2, "shadow_output": "retrieval_embedding"}},
            },
            "local_features": {
                "inputs": {"image": frame_contract},
                "outputs": {"features": {"name": "features", "rank": 3}},
            },
            "local_matcher": {
                "inputs": {"map_features": {"name": "map_features", "rank": 3}},
                "outputs": {"matches": {"name": "matches", "rank": 2}},
            },
            "temporal_student": {
                "inputs": {"frame_window": {"name": "frame_window", "rank": 5}},
                "outputs": {"state": {"name": "state", "rank": 2}},
            },
        },
    )

    class FakeRunner:
        def run(self, role, logical_inputs):
            assert logical_inputs["image"].shape == (1, 3, 6, 8)
            assert logical_inputs["image"].dtype == np.float32
            if role == "global_retriever":
                return {"embedding": np.asarray([[0.25, 0.75]], dtype=np.float32)}
            return {"features": np.zeros((1, 4, 2), dtype=np.float32)}

    probe = run_shadow_frame_probe(
        FakeRunner(),
        model_release=model,
        frame=load_shadow_frame(frame_path),
        candidate_lookup=lambda embedding: [{"tile_id": "tile-4", "score": float(embedding[0, 1])}],
    )
    assert probe["executed_stage_count"] == 2
    assert probe["failed_stage_count"] == 0
    assert probe["decision_reason"] == "learned_frame_probe_complete_shadow_only"
    assert [stage["status"] for stage in probe["stages"]] == ["executed", "executed", "skipped", "skipped"]
    assert probe["stages"][2]["reason"] == "local_feature_cache_unavailable"
    assert probe["top_k_candidates"] == [{"tile_id": "tile-4", "score": 0.75}]


def test_retrieval_metadata_binds_faiss_ids_to_immutable_tiles(tmp_path):
    metadata_path = tmp_path / "megaloc_tiles.json"
    metadata_path.write_text(json.dumps({
        "schema_version": "vision_nav_retrieval_index_v1",
        "map_release_id": "field-map@1",
        "model_release_id": "pi5-crossview-nav@1",
        "descriptor_dimension": 2,
        "metric": "inner_product_cosine",
        "normalize_query": True,
        "tiles": [
            {"tile_id": "tile-east", "faiss_id": 7, "center_enu_m": [2.0, 3.0, 0.0], "scale_m_per_px": 0.2},
            {"tile_id": "tile-west", "faiss_id": 12, "center_enu_m": [-2.0, 3.0, 0.0], "scale_m_per_px": 0.2},
        ],
    }), encoding="utf-8")
    metadata = load_retrieval_metadata(
        metadata_path,
        expected_map_release_id="field-map@1",
        expected_model_release_id="pi5-crossview-nav@1",
    )

    class FakeIndex:
        d = 2
        ntotal = 2

        def search(self, query, top_k):
            assert np.allclose(query, [[0.6, 0.8]])
            assert top_k == 2
            return np.asarray([[0.9, 0.7]], dtype=np.float32), np.asarray([[12, 7]], dtype=np.int64)

    candidates = FaissTileRetriever(FakeIndex(), metadata).search(np.asarray([3.0, 4.0]), top_k=3)
    assert [candidate["tile_id"] for candidate in candidates] == ["tile-west", "tile-east"]
    assert candidates[0]["center_enu_m"] == [-2.0, 3.0, 0.0]


def test_frame_inference_requires_matching_measured_camera_calibration(tmp_path):
    calibration_path = tmp_path / "camera.yaml"
    calibration_path.write_text("""
camera_name: unit-camera
calibration_id: camera-v1
calibration_status: measured
image_width: 16
image_height: 12
distortion_model: plumb_bob
camera_matrix:
  data: [10, 0, 8, 0, 10, 6, 0, 0, 1]
distortion_coefficients:
  rows: 1
  cols: 5
  data: [0, 0, 0, 0, 0]
""", encoding="utf-8")
    compatibility = RuntimeCompatibility(True, (), "onnxruntime_cpu", PI5_CROSSVIEW_NAV_V1)
    args = SimpleNamespace(frame=tmp_path / "frame.jpg", camera_backend="none", camera_calibration=calibration_path, calibration_id="camera-v1")
    calibration, checked = _frame_calibration(args, compatibility)
    assert checked.compatible
    assert calibration is not None
    args.calibration_id = "other-camera"
    calibration, checked = _frame_calibration(args, compatibility)
    assert calibration is None
    assert "camera_calibration_id_does_not_match_runtime" in checked.reasons


def test_shadow_probe_runs_local_matcher_against_checksum_verified_tile_features(tmp_path):
    frame_path = tmp_path / "frame.jpg"
    assert cv2.imwrite(str(frame_path), np.full((12, 16, 3), 50, dtype=np.uint8))
    feature_path = tmp_path / "features" / "tile-a.npz"
    feature_path.parent.mkdir()
    np.savez(feature_path, keypoints=np.zeros((1, 3, 2), dtype=np.float32), descriptors=np.ones((1, 3, 4), dtype=np.float32))
    feature_digest = hashlib.sha256(feature_path.read_bytes()).hexdigest()
    metadata_path = tmp_path / "xfeat_tiles.json"
    metadata_path.write_text(json.dumps({
        "schema_version": "vision_nav_local_feature_cache_v1",
        "map_release_id": "field-map@1",
        "model_release_id": "local-shadow@1",
        "tiles": [{"tile_id": "tile-a", "feature_uri": "features/tile-a.npz", "sha256": feature_digest}],
    }), encoding="utf-8")
    cache = load_local_feature_cache(metadata_path, expected_map_release_id="field-map@1", expected_model_release_id="local-shadow@1")
    frame_input = {
        "name": "image", "rank": 4, "shadow_source": "frame_rgb",
        "preprocess": {"image_size": [8, 6], "color_space": "rgb", "layout": "nchw", "dtype": "float32"},
    }
    model = ModelRelease(
        release_id="local-shadow@1", profile_id=PI5_CROSSVIEW_NAV_V1.profile_id, semantic_version="1.0.0",
        checkpoint_digest_sha256="a" * 64, onnx_digest_sha256="b" * 64,
        training_dataset_releases=("public@1",), source_revision="unit", license_summary="test-only",
        redistribution_allowed=False, calibration_ids=("camera-v1",), artifact_paths={},
        artifact_interfaces={
            "global_retriever": {
                "inputs": {"image": frame_input},
                "outputs": {"embedding": {"name": "embedding", "rank": 2, "shadow_output": "retrieval_embedding"}},
            },
            "local_features": {
                "inputs": {"image": frame_input},
                "outputs": {
                    "keypoints": {"name": "keypoints", "rank": 3, "shadow_output": "local_keypoints"},
                    "descriptors": {"name": "descriptors", "rank": 3, "shadow_output": "local_descriptors"},
                },
            },
            "local_matcher": {
                "inputs": {
                    "query_keypoints": {"name": "query_keypoints", "rank": 3, "shadow_source": "query_local_keypoints"},
                    "map_keypoints": {"name": "map_keypoints", "rank": 3, "shadow_source": "map_tile_keypoints"},
                    "query_descriptors": {"name": "query_descriptors", "rank": 3, "shadow_source": "query_local_descriptors"},
                    "map_descriptors": {"name": "map_descriptors", "rank": 3, "shadow_source": "map_tile_descriptors"},
                },
                "outputs": {
                    "matches": {
                        "name": "matches", "rank": 3, "shadow_output": "match_indices",
                        "shadow_encoding": "pairs_last_dim_2", "pair_order": "query_then_map",
                    },
                    "scores": {
                        "name": "scores", "rank": 2, "shadow_output": "match_scores",
                        "shadow_encoding": "per_correspondence", "minimum_confidence": 0.2,
                    },
                },
                "geometry": {
                    "mode": "homography_restricted", "query_image_size": [8, 6],
                    "min_inliers": 4, "min_inlier_ratio": 0.3,
                    "max_reprojection_error_px": 4.0, "min_grid_coverage": 0.25,
                },
            },
            "temporal_student": {
                "inputs": {"frame_window": {"name": "frame_window", "rank": 5}},
                "outputs": {"state": {"name": "state", "rank": 2}},
            },
        },
    )

    class FakeRunner:
        def run(self, role, logical_inputs):
            if role == "global_retriever":
                return {"embedding": np.asarray([[1.0, 0.0]], dtype=np.float32)}
            if role == "local_features":
                return {
                    "keypoints": np.full((1, 3, 2), 2.0, dtype=np.float32),
                    "descriptors": np.full((1, 3, 4), 3.0, dtype=np.float32),
                }
            assert logical_inputs["map_keypoints"].shape == (1, 3, 2)
            assert logical_inputs["map_descriptors"].shape == (1, 3, 4)
            return {
                "matches": np.asarray([[[0, 0], [1, 1], [-1, -1]]], dtype=np.int64),
                "scores": np.asarray([[0.95, 0.5, 0.99]], dtype=np.float32),
            }

    probe = run_shadow_frame_probe(
        FakeRunner(), model_release=model, frame=load_shadow_frame(frame_path),
        candidate_lookup=lambda _embedding: [{"tile_id": "tile-a", "score": 0.91}],
        tile_feature_lookup=cache.load,
    )
    assert [stage["status"] for stage in probe["stages"]] == ["executed", "executed", "executed", "skipped"]
    assert probe["stages"][2]["candidate_tile_id"] == "tile-a"
    assert probe["correspondence_evidence"]["valid_pair_count"] == 2
    assert probe["correspondence_evidence"]["accepted_score_count"] == 2
    assert probe["geometry_evidence"]["reason"] == "insufficient_scored_correspondences"
    assert probe["decision_reason"] == "learned_frame_probe_complete_shadow_only"


def test_temporal_student_requires_aligned_complete_camera_attitude_window(tmp_path):
    frame_path = tmp_path / "frame.jpg"
    assert cv2.imwrite(str(frame_path), np.full((12, 16, 3), 80, dtype=np.uint8))
    model = ModelRelease(
        release_id="temporal-shadow@1", profile_id=PI5_CROSSVIEW_NAV_V1.profile_id, semantic_version="1.0.0",
        checkpoint_digest_sha256="a" * 64, onnx_digest_sha256="b" * 64,
        training_dataset_releases=("public@1",), source_revision="unit", license_summary="test-only",
        redistribution_allowed=False, calibration_ids=("camera-v1",), artifact_paths={},
        artifact_interfaces={
            "temporal_student": {
                "inputs": {
                    "frame_window": {
                        "name": "frame_window", "rank": 5, "shadow_source": "frame_window_rgb", "window_size": 2,
                        "preprocess": {"image_size": [8, 6], "color_space": "rgb", "layout": "nchw", "dtype": "float32"},
                    },
                    "attitude_window": {
                        "name": "attitude_window", "rank": 3, "shadow_source": "attitude_window_rpy", "window_size": 2,
                    },
                },
                "outputs": {"state": {"name": "state", "rank": 2}},
            },
        },
    )
    window = TemporalWindowBuilder(window_size=2, max_attitude_frame_delta_ms=50.0)
    window.push_attitude(host_monotonic_ns=1_000_000_000, roll_rad=0.1, pitch_rad=0.2, yaw_rad=0.3)

    class FakeRunner:
        def run(self, role, logical_inputs):
            assert role == "temporal_student"
            assert logical_inputs["frame_window"].shape == (1, 2, 3, 6, 8)
            assert logical_inputs["attitude_window"].shape == (1, 2, 3)
            return {"state": np.asarray([[0.4, 0.6]], dtype=np.float32)}

    first = run_shadow_frame_probe(
        FakeRunner(), model_release=model, frame=load_shadow_frame(frame_path),
        temporal_window=window, frame_monotonic_ns=1_010_000_000,
    )
    assert first["temporal_context"]["status"] == "warming_up"
    assert first["stages"][3]["reason"] == "temporal_window_not_full"
    window.push_attitude(host_monotonic_ns=2_000_000_000, roll_rad=0.2, pitch_rad=0.3, yaw_rad=0.4)
    second = run_shadow_frame_probe(
        FakeRunner(), model_release=model, frame=load_shadow_frame(frame_path),
        temporal_window=window, frame_monotonic_ns=2_005_000_000,
    )
    assert second["temporal_context"]["status"] == "ready"
    assert second["stages"][3]["status"] == "executed"
    missing = run_shadow_frame_probe(
        FakeRunner(), model_release=model, frame=load_shadow_frame(frame_path),
        temporal_window=window, frame_monotonic_ns=None,
    )
    assert missing["stages"][3]["status"] == "skipped"
    assert missing["stages"][3]["reason"] == "frame_monotonic_timestamp_missing"
    rejected = window.observe_frame(np.zeros((4, 4, 3), dtype=np.uint8), host_monotonic_ns=3_000_000_000)
    assert rejected["reason"] == "nearest_px4_attitude_is_stale"


def test_temporal_student_records_uncalibrated_location_and_heading_evidence(tmp_path):
    frame_path = tmp_path / "frame.jpg"
    assert cv2.imwrite(str(frame_path), np.full((12, 16, 3), 80, dtype=np.uint8))
    model = ModelRelease(
        release_id="temporal-evidence@1", profile_id=PI5_CROSSVIEW_NAV_V1.profile_id, semantic_version="1.0.0",
        checkpoint_digest_sha256="a" * 64, onnx_digest_sha256="b" * 64,
        training_dataset_releases=("public@1",), source_revision="unit", license_summary="test-only",
        redistribution_allowed=False, calibration_ids=("camera-v1",), artifact_paths={},
        artifact_interfaces={
            "temporal_student": {
                "inputs": {
                    "frame_window": {
                        "name": "frame_window", "rank": 5, "shadow_source": "frame_window_rgb", "window_size": 2,
                        "preprocess": {"image_size": [8, 6], "color_space": "rgb", "layout": "nchw", "dtype": "float32"},
                    },
                    "attitude_window": {
                        "name": "attitude_window", "rank": 3, "shadow_source": "attitude_window_rpy", "window_size": 2,
                    },
                },
                "outputs": {
                    "location_distribution": {"name": "location_distribution", "rank": 2, "shadow_output": "temporal_location_evidence"},
                    "heading_distribution": {"name": "heading_distribution", "rank": 2, "shadow_output": "temporal_heading_evidence"},
                    "temporal_feature": {"name": "temporal_feature", "rank": 2, "shadow_output": "temporal_feature_evidence"},
                },
            },
        },
    )
    window = TemporalWindowBuilder(window_size=2, max_attitude_frame_delta_ms=50.0)

    class FakeRunner:
        def run(self, role, logical_inputs):
            assert role == "temporal_student"
            assert logical_inputs["frame_window"].shape == (1, 2, 3, 6, 8)
            return {
                "location_distribution": np.asarray([[1.0, 2.0, 3.0]], dtype=np.float32),
                "heading_distribution": np.asarray([[0.0, 2.0]], dtype=np.float32),
                "temporal_feature": np.ones((1, 4), dtype=np.float32),
            }

    window.push_attitude(host_monotonic_ns=1_000_000_000, roll_rad=0.0, pitch_rad=0.0, yaw_rad=0.0)
    run_shadow_frame_probe(
        FakeRunner(), model_release=model, frame=load_shadow_frame(frame_path), temporal_window=window,
        frame_monotonic_ns=1_005_000_000,
    )
    window.push_attitude(host_monotonic_ns=2_000_000_000, roll_rad=0.0, pitch_rad=0.0, yaw_rad=0.0)
    probe = run_shadow_frame_probe(
        FakeRunner(), model_release=model, frame=load_shadow_frame(frame_path), temporal_window=window,
        frame_monotonic_ns=2_005_000_000,
    )
    evidence = probe["temporal_student_evidence"]
    assert evidence["status"] == "available"
    assert evidence["position_authority"] is False
    assert evidence["location_evidence_enu_m"] == [1.0, 2.0, 3.0]
    assert abs(evidence["heading_evidence_rad"]) < 1e-6
    assert evidence["temporal_feature_dimension"] == 4


def test_planar_geometry_gate_accepts_spatially_distributed_learned_pairs():
    x, y = np.meshgrid(np.linspace(30.0, 610.0, 8), np.linspace(25.0, 355.0, 8))
    map_points = np.c_[x.ravel(), y.ravel()].astype(np.float32)
    # A small affine-like map-to-camera transformation remains valid for a local planar tile.
    query_points = (map_points @ np.asarray([[0.92, 0.03], [-0.02, 0.96]], dtype=np.float32)) + np.asarray([8.0, 6.0], dtype=np.float32)
    pairs = np.c_[np.arange(len(map_points)), np.arange(len(map_points))].reshape(1, -1, 2)
    evidence = verify_planar_correspondences(
        query_keypoints=query_points[None, ...],
        map_keypoints=map_points[None, ...],
        match_pairs=pairs,
        scores=np.full((1, len(map_points)), 0.9, dtype=np.float32),
        query_width=640,
        query_height=384,
        minimum_score=0.2,
        min_inliers=24,
        min_inlier_ratio=0.3,
        max_reprojection_error_px=4.0,
        min_grid_coverage=0.25,
    )
    assert evidence["status"] == "accepted"
    assert evidence["inlier_count"] == len(map_points)
    assert evidence["inlier_grid_coverage"] >= 0.25


def test_pnp_geometry_gate_produces_shadow_only_metric_camera_candidate():
    rng = np.random.default_rng(7)
    map_points = rng.uniform([-3.0, -2.0, 0.0], [3.0, 2.0, 2.0], size=(40, 3)).astype(np.float32)
    camera_matrix = np.asarray([[520.0, 0.0, 320.0], [0.0, 520.0, 240.0], [0.0, 0.0, 1.0]])
    rvec = np.asarray([[0.08], [-0.04], [0.03]], dtype=np.float64)
    tvec = np.asarray([[0.1], [0.2], [12.0]], dtype=np.float64)
    query_points, _ = cv2.projectPoints(map_points, rvec, tvec, camera_matrix, None)
    pairs = np.c_[np.arange(len(map_points)), np.arange(len(map_points))].reshape(1, -1, 2)
    evidence = verify_pnp_correspondences(
        query_keypoints=query_points.reshape(1, -1, 2),
        map_points_enu_m=map_points.reshape(1, -1, 3),
        match_pairs=pairs,
        scores=np.full((1, len(map_points)), 0.95, dtype=np.float32),
        camera_matrix=camera_matrix,
        query_width=640,
        query_height=480,
        minimum_score=0.2,
        min_inliers=24,
        min_inlier_ratio=0.3,
        max_reprojection_error_px=2.0,
        min_grid_coverage=0.1,
    )
    assert evidence["status"] == "accepted"
    assert evidence["inlier_count"] == len(map_points)
    assert np.allclose(evidence["camera_position_enu_m"], [-0.58, -1.15, -11.93], atol=0.15)


def test_shadow_camera_candidate_bank_requires_repeatable_nonjumping_pnp_candidates():
    bank = ShadowCameraCandidateBank(
        ShadowCandidatePolicy(required_consecutive=3, max_gap_s=1.0, max_position_jump_m=2.0, history_size=4)
    )
    first = bank.update(tile_id="tile-a", camera_position_enu_m=[1.0, 2.0, 3.0], timestamp_monotonic_ns=1_000_000_000)
    second = bank.update(tile_id="tile-a", camera_position_enu_m=[1.1, 2.0, 3.0], timestamp_monotonic_ns=1_100_000_000)
    third = bank.update(tile_id="tile-a", camera_position_enu_m=[1.2, 2.0, 3.0], timestamp_monotonic_ns=1_200_000_000)
    assert first["status"] == "warming_up"
    assert second["consecutive_count"] == 2
    assert third["status"] == "tracking_candidate"
    assert third["position_covariance_m2"] is None
    assert third["position_sample_scatter_m2"] is not None
    assert third["camera_position_enu_m"] == [1.2, 2.0, 3.0]
    assert third["position_authority"] is False
    jumped = bank.update(tile_id="tile-a", camera_position_enu_m=[20.0, 2.0, 3.0], timestamp_monotonic_ns=1_300_000_000)
    assert jumped["status"] == "warming_up"
    assert jumped["reason"] == "candidate_position_jump_reset"
    changed = bank.update(tile_id="tile-b", camera_position_enu_m=[0.0, 0.0, 0.0], timestamp_monotonic_ns=1_400_000_000)
    assert changed["reason"] == "candidate_tile_changed"
