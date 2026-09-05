import json

import cv2
import numpy as np
import pytest
import yaml

from vision_nav.training_capture import sha256_file
from vision_nav.neural.map_pixel_geometry import reference_enu_to_tile_uv, tile_metric_axes
from vision_nav.neural.crossview_dataset import build_crossview_dataset, load_crossview_dataset, MapConditionedDataset
from vision_nav.neural.map_conditioned_student import (MapConditionedStudentConfig, build_map_conditioned_student,
    train_map_conditioned_student, validate_inputs, summarize_outputs)
from vision_nav.neural.map_conditioned_export import export_map_conditioned_student, MapConditionedOnnxProbe


def _windows(tmp_path):
    Transformer = pytest.importorskip("pyproj").Transformer
    lon, lat = Transformer.from_crs(32610, 4326, always_xy=True).transform(500000, 4200000)
    geometry = {"schema_version": "vision_nav_tile_pixel_geometry_v1", "crs": "EPSG:32610",
                "affine": [2, 0, 499936, 0, -2, 4200064], "image_size": [64, 64],
                "geodetic_origin_wgs84": {"lat_deg": lat, "lon_deg": lon, "altitude_m": 100}}
    calibration = {"calibration_id": "cam@1", "calibration_status": "measured", "camera_name": "test",
                   "image_width": 64, "image_height": 64, "distortion_model": "plumb_bob",
                   "camera_matrix": {"data": [50, 0, 31.5, 0, 50, 31.5, 0, 0, 1]},
                   "distortion_coefficients": {"rows": 1, "cols": 5, "data": [0, 0, 0, 0, 0]}}
    (tmp_path / "camera.yaml").write_text(yaml.safe_dump(calibration))
    registry = tmp_path / "calibrations.json"
    registry.write_text(json.dumps({"cam@1": "camera.yaml"}))
    source = tmp_path / "windows"
    source.mkdir()
    rng = np.random.default_rng(42)
    splits = {}
    for split in ("train", "validation"):
        frame_rows = []
        for index in range(2):
            image_path = tmp_path / f"{split}_{index}.png"
            assert cv2.imwrite(str(image_path), rng.integers(0, 256, (64, 64, 3), dtype=np.uint8))
            frame_rows.append({"sample_id": f"{split}-{index}", "capture_session_id": split, "frame_path": str(image_path),
                               "timestamp_ns": 1000000000 + index * 100000000, "sensor_timestamp_ns": 900000000 + index * 100000000,
                               "map_release_id": "area@1", "calibration_id": "cam@1", "attitude_rpy_rad": [0.1, -0.2, 2.5],
                               "attitude_valid": True, "camera_extrinsics_valid": True,
                               "camera_translation_body_frd_m": [0, 0, 0], "camera_rotation_camera_optical_to_body_frd_xyzw": [0, 0, 0, 1]})
        maps = []
        for index in range(2):
            image_path = tmp_path / f"map_{split}_{index}.png"
            assert cv2.imwrite(str(image_path), rng.integers(0, 256, (64, 64, 3), dtype=np.uint8))
            geo = {**geometry, "affine": [2, 0, 499936 + index * 500, 0, -2, 4200064]}
            maps.append({"tile_id": f"tile-{index}", "image_path": str(image_path), "pixel_geometry": geo})
        window = {"schema_version": "vision_nav_temporal_training_windows_v1", "window_id": split + "-window",
                  "capture_session_id": split, "frame_count": 2, "anchor_index": 1, "map_release_id": "area@1", "calibration_id": "cam@1",
                  "frames": frame_rows, "anchor": {"sample_id": split + "-1", "positive": maps[0], "spatial_hard_negatives": [maps[1]],
                  "supervision": {"position_enu_m": {"east": 0, "north": 0, "up": 0}, "heading_rad": 2.5,
                                  "heading_source": "synthetic_reference", "position_reference": "synthetic_camera_center"}}}
        path = source / f"{split}.jsonl"
        path.write_text(json.dumps(window) + "\n")
        splits[split] = {"path": path.name, "sha256": sha256_file(path), "window_count": 1}
    manifest = {"schema_version": "vision_nav_temporal_training_windows_v1", "policy": "offline_training_only",
                "temporal_policy": {"max_frame_gap_ms": 1000}, "splits": splits}
    (source / "manifest.json").write_text(json.dumps(manifest))
    return source, registry, geometry


def test_pixel_projection_and_metric_axes_are_not_bounds_approximations(tmp_path):
    _, _, geometry = _windows(tmp_path)
    np.testing.assert_allclose(reference_enu_to_tile_uv([0, 0, 0], geometry), [0.5, 0.5], atol=1e-7)
    axes = np.asarray(tile_metric_axes(geometry))
    assert axes[0] > 127 and axes[3] < -127
    rotated = {**geometry, "affine": [0, -2, 500064, -2, 0, 4200064]}
    np.testing.assert_allclose(reference_enu_to_tile_uv([0, 0, 0], rotated), [0.5, 0.5], atol=1e-7)
    axes = np.asarray(tile_metric_axes(rotated))
    assert axes[1] < -127 and axes[2] < -127
    with pytest.raises(ValueError):
        reference_enu_to_tile_uv([0, 0, 0], {**geometry, "affine": [0, 0, 0, 0, 0, 0]})


def test_portable_dataset_has_map_pixels_no_yaw_or_absolute_position_input(tmp_path):
    torch = pytest.importorskip("torch")
    source, registry, _ = _windows(tmp_path)
    root = tmp_path / "dataset"
    manifest = build_crossview_dataset([source], registry, root, allow_legacy_timing=True)
    dataset = MapConditionedDataset(root, split="train", image_size=64, grid_size=4, heading_bins=8)
    item = dataset[0]
    assert item["inputs"]["candidate_map"].shape == (2, 3, 64, 64)
    assert item["inputs"]["roll_pitch"].shape == (2, 2, 2)
    np.testing.assert_allclose(item["inputs"]["roll_pitch"][0, 0], [0.1, -0.2])
    assert item["location_target"].tolist()[-1] == 16
    assert 0 <= item["location_target"][0] < 16
    assert item["heading_target"].tolist() == [3, -1]
    assert manifest["geographic_holdout_verified"] is False
    # Moving the package must not require the original camera/map source paths.
    moved = tmp_path / "moved"
    root.rename(moved)
    assert MapConditionedDataset(moved, split="validation", image_size=64, grid_size=4, heading_bins=8)[0]["inputs"]["frame_window"].shape == (2, 2, 3, 64, 64)
    name = next(iter(manifest["assets_sha256"]))
    (moved / name).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum"):
        load_crossview_dataset(moved)


def test_model_changes_with_map_and_backpropagates_both_image_branches(tmp_path):
    torch = pytest.importorskip("torch")
    torch.set_num_threads(1)
    torch.manual_seed(3)
    source, registry, _ = _windows(tmp_path)
    root = tmp_path / "dataset"
    build_crossview_dataset([source], registry, root, allow_legacy_timing=True)
    config = MapConditionedStudentConfig(image_size=64, grid_size=4, feature_dim=16, window_size=2, heading_bins=8)
    item = MapConditionedDataset(root, split="train", image_size=64, grid_size=4, heading_bins=8)[0]
    model = build_map_conditioned_student(config)
    out = model(**item["inputs"])
    assert out[0].shape == (2, 17)
    assert not torch.allclose(out[0][0], out[0][1])
    loss = torch.nn.functional.cross_entropy(out[0], item["location_target"])
    loss.backward()
    assert model.map_encoder[0].weight.grad.abs().sum() > 0
    assert model.camera_encoder[0].weight.grad.abs().sum() > 0
    inputs = {k: v.numpy() for k, v in item["inputs"].items()}
    validate_inputs(inputs, config)
    inputs["time_offsets_s"][:] = 0
    with pytest.raises(ValueError, match="strictly increase"):
        validate_inputs(inputs, config)


def test_real_training_export_and_shadow_probe(tmp_path):
    torch = pytest.importorskip("torch")
    pytest.importorskip("onnx")
    pytest.importorskip("onnxruntime")
    torch.set_num_threads(1)
    source, registry, _ = _windows(tmp_path)
    dataset = tmp_path / "dataset"
    build_crossview_dataset([source], registry, dataset, allow_legacy_timing=True)
    config = MapConditionedStudentConfig(image_size=64, grid_size=4, feature_dim=16, window_size=2, heading_bins=8)
    training = tmp_path / "training"
    report = train_map_conditioned_student(dataset, training, config=config, epochs=2, device="cpu")
    assert len(report["history"]) == 2
    assert np.isfinite(report["history"][-1]["train"]["mean_loss"])
    assert report["teacher_distillation"] == "not_performed"
    export = tmp_path / "export"
    result = export_map_conditioned_student(training / "map_conditioned_student.pt", dataset, export)
    assert result["status"] == "parity_passed"
    assert len(result["parity"]["cases"]) == 2
    probe = MapConditionedOnnxProbe(export)
    with np.load(export / "sample_inputs.npz") as archive:
        inputs = {k: archive[k] for k in archive.files}
    # Unlike the absolute-coordinate baseline, a new map ID is allowed.
    evidence = probe.run(inputs, tile_id="unseen-tile", map_release_id="new-area@1", calibration_id="cam@1")
    assert evidence["position_authority"] is False and evidence["covariance"] is None
    assert evidence["px4_external_vision"] == "disabled"
    assert len(evidence["location_probability_grid"]) == 4
    with pytest.raises(ValueError, match="compatible"):
        probe.run(inputs, tile_id="tile", map_release_id="map", calibration_id="wrong-camera")
    (export / "map_conditioned_student.onnx").write_bytes(b"bad")
    with pytest.raises(ValueError, match="checksum"):
        MapConditionedOnnxProbe(export)


def test_explicit_null_output_is_not_a_position():
    config = MapConditionedStudentConfig()
    location = np.zeros((1, 65), dtype=np.float32)
    location[0, -1] = 100
    evidence = summarize_outputs({"location_logits": location, "heading_logits": np.zeros((1, 36))}, config)
    assert evidence["status"] == "no_match_evidence"
    assert evidence["most_likely_uv"] is None
    assert evidence["position_authority"] is False


@pytest.mark.parametrize("fault", ["negative_contains_target", "missing_geometry", "wrong_calibration", "same_camera_bytes"])
def test_builder_rejects_unusable_training_evidence(tmp_path, fault):
    source, registry, _ = _windows(tmp_path)
    path = source / "train.jsonl"
    row = json.loads(path.read_text())
    if fault == "negative_contains_target":
        row["anchor"]["spatial_hard_negatives"][0]["pixel_geometry"] = row["anchor"]["positive"]["pixel_geometry"]
    elif fault == "missing_geometry":
        row["anchor"]["positive"].pop("pixel_geometry")
    elif fault == "wrong_calibration":
        calibration = yaml.safe_load((tmp_path / "camera.yaml").read_text())
        calibration["calibration_id"] = "wrong"
        (tmp_path / "camera.yaml").write_text(yaml.safe_dump(calibration))
    else:
        validation = json.loads((source / "validation.jsonl").read_text())
        row["frames"][0]["frame_path"] = validation["frames"][0]["frame_path"]
    path.write_text(json.dumps(row) + "\n")
    manifest = json.loads((source / "manifest.json").read_text())
    manifest["splits"]["train"]["sha256"] = sha256_file(path)
    (source / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        build_crossview_dataset([source], registry, tmp_path / "dataset", allow_legacy_timing=True)


@pytest.mark.parametrize("config", [MapConditionedStudentConfig(image_size=63), MapConditionedStudentConfig(feature_dim=7),
                                    MapConditionedStudentConfig(heading_loss_weight=float("nan"))])
def test_invalid_model_config(config):
    with pytest.raises(ValueError):
        config.validate()
