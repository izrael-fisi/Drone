import json
import shutil

import cv2
import numpy as np
import pytest

from vision_nav.training_capture import write_session_checksums
from vision_nav.neural.crossview_dataset import build_crossview_dataset, MapConditionedDataset
from vision_nav.neural.map_conditioned_student import MapConditionedStudentConfig, train_map_conditioned_student
from vision_nav.neural.map_conditioned_export import export_map_conditioned_student, MapConditionedOnnxProbe
from vision_nav.neural.map_conditioned_replay import replay_map_conditioned_capture, CameraWindow
from vision_nav.neural.map_tile_layout import build_map_tile_layout
from vision_nav.neural.reference_map import create_reference_map

from test_exposure_training import _prepared


@pytest.fixture(scope="module")
def artifacts(tmp_path_factory):
    torch = pytest.importorskip("torch")
    rasterio = pytest.importorskip("rasterio")
    pytest.importorskip("onnx")
    pytest.importorskip("onnxruntime")
    torch.set_num_threads(1)
    root = tmp_path_factory.mktemp("capture-replay")
    windows, registry, packages = _prepared(root)
    dataset = root / "dataset"
    build_crossview_dataset([windows], registry, dataset)
    config = MapConditionedStudentConfig(image_size=64, grid_size=4, feature_dim=16, window_size=2, heading_bins=8)
    train_map_conditioned_student(dataset, root / "run", config=config, epochs=1, device="cpu")
    export_map_conditioned_student(root / "run" / "map_conditioned_student.pt", dataset, root / "export")
    geo = packages["train"]["window"]["anchor"]["positive"]["pixel_geometry"]
    origin = geo["geodetic_origin_wgs84"]
    with rasterio.open(root / "map.tif", "w", driver="GTiff", height=64, width=128,
                       count=3, dtype="uint8", crs=geo["crs"], transform=rasterio.Affine(*geo["affine"])) as raster:
        raster.write(np.random.default_rng(40).integers(1, 254, (3, 64, 128), dtype=np.uint8))
    (root / "stac.json").write_text('{}')
    create_reference_map(root / "reference.json", map_id="area", version="1", crs=geo["crs"],
        local_origin_enu=(0, 0, 0), geodetic_origin_wgs84=(origin["lat_deg"], origin["lon_deg"], origin["altitude_m"]),
        gsd_m_per_px=2, imagery_uri="map.tif", stac_item_uri="stac.json", calibration_ids=["cam@1"])
    build_map_tile_layout(root / "map.tif", root / "tiles", origin_lat_deg=origin["lat_deg"],
        origin_lon_deg=origin["lon_deg"], origin_altitude_m=origin["altitude_m"], tile_size_px=64)
    return root


def run(root, output, **overrides):
    args = {"session_dir": root / "train", "export_dir": root / "export", "reference_map": root / "reference.json",
            "tile_layout": root / "tiles" / "tile_layout.json", "calibration_path": root / "camera.yaml",
            "output_dir": output, "candidate_tile_ids": ["tile_000000", "tile_000001"], **overrides}
    return replay_map_conditioned_capture(**args)


def test_raw_camera_replay_matches_training_preprocessing_and_is_deterministic(artifacts, tmp_path, monkeypatch):
    root = artifacts
    original = MapConditionedOnnxProbe.run
    inputs_seen = []
    def spy(self, inputs, **kwargs):
        inputs_seen.append(inputs)
        return original(self, inputs, **kwargs)
    monkeypatch.setattr(MapConditionedOnnxProbe, "run", spy)
    first = run(root, tmp_path / "first")
    second = run(root, tmp_path / "second")
    assert first["counts"] == {"frames": 2, "warmup": 1, "rejected": 0, "model_windows": 1, "candidate_calls": 2, "window_resets": 0,
                              "retrieval_calls": 0}
    assert first["decision_sha256_excluding_latency"] == second["decision_sha256_excluding_latency"]
    dataset = MapConditionedDataset(root / "dataset", split="train", image_size=64, grid_size=4, heading_bins=8)
    training = dataset[0]["inputs"]
    for key in ("frame_window", "roll_pitch", "time_offsets_s", "camera_context"):
        np.testing.assert_array_equal(inputs_seen[0][key], training[key][:1].numpy())
    trace = [json.loads(line) for line in (tmp_path / "first" / "trace.jsonl").read_text().splitlines()]
    assert [row["state"] for row in trace] == ["WINDOW_WARMUP", "MODEL_EVIDENCE"]
    assert all(row["navigation_mode"] == "NO_GLOBAL_FIX" and row["position_authority"] is False for row in trace)
    assert len(trace[1]["candidates"]) == 2
    assert first["provenance"]["training_timing_policies"] == ["exposure_native_attitude_v1"]


def test_gps_telemetry_never_changes_model_inputs_or_candidates(artifacts, tmp_path):
    copied = tmp_path / "capture"
    shutil.copytree(artifacts / "train", copied)
    manifest = json.loads((copied / "manifest.json").read_text())
    telemetry = copied / manifest["telemetry_path"]
    with telemetry.open("a") as stream:
        stream.write(json.dumps({"index": 999, "host_monotonic_ns": 1, "sample": {"message_type": "GPS_RAW_INT",
            "gps_lat": -80, "gps_lon": 170, "gps_alt_m": 999999, "gps_fix_type": 6}}) + "\n")
    manifest["telemetry_count"] += 1
    (copied / "manifest.json").write_text(json.dumps(manifest))
    write_session_checksums(copied)
    a = run(artifacts, tmp_path / "a")
    b = run(artifacts, tmp_path / "b", session_dir=copied)
    assert a["decision_sha256_excluding_latency"] == b["decision_sha256_excluding_latency"]
    assert b["provenance"]["gps_used_for_candidate_selection_or_model_inputs"] is False


def test_frame_gap_resets_window_instead_of_reusing_old_context(artifacts, tmp_path):
    result = run(artifacts, tmp_path / "gap", max_frame_gap_ms=50)
    assert result["counts"]["model_windows"] == 0
    assert result["counts"]["window_resets"] == 1
    trace = [json.loads(line) for line in (tmp_path / "gap" / "trace.jsonl").read_text().splitlines()]
    assert trace[-1]["window_reset_reason"] == "frame_clock_gap_or_reordering"


def test_bad_image_clears_context_and_is_retained_in_trace(artifacts, tmp_path):
    copied = tmp_path / "capture"
    shutil.copytree(artifacts / "train", copied)
    manifest = json.loads((copied / "manifest.json").read_text())
    cv2.imwrite(str(copied / manifest["frames"][0]["path"]), np.zeros((64, 64, 3), np.uint8))
    write_session_checksums(copied)
    result = run(artifacts, tmp_path / "bad-image", session_dir=copied)
    assert result["counts"]["rejected"] == 1
    assert result["counts"]["model_windows"] == 0
    trace = [json.loads(line) for line in (tmp_path / "bad-image" / "trace.jsonl").read_text().splitlines()]
    assert trace[0]["reason"] == "frame_quality_rejected"
    assert trace[1]["state"] == "WINDOW_WARMUP"


def test_same_calibration_id_with_changed_parameters_is_rejected(artifacts, tmp_path):
    import yaml
    calibration = yaml.safe_load((artifacts / "camera.yaml").read_text())
    calibration["camera_matrix"]["data"][0] += 1
    path = tmp_path / "camera.yaml"
    path.write_text(yaml.safe_dump(calibration))
    with pytest.raises(ValueError, match="calibration file checksum"):
        run(artifacts, tmp_path / "out", calibration_path=path)


def test_provider_failure_remains_rejected_shadow_evidence(artifacts, tmp_path, monkeypatch):
    def failed(*args, **kwargs):
        raise RuntimeError("test_provider_failure")
    monkeypatch.setattr(MapConditionedOnnxProbe, "run", failed)
    result = run(artifacts, tmp_path / "failed")
    assert result["counts"]["model_windows"] == 0
    trace = [json.loads(line) for line in (tmp_path / "failed" / "trace.jsonl").read_text().splitlines()]
    assert trace[-1]["reason"] == "test_provider_failure"
    assert trace[-1]["position_authority"] is False


def test_replay_does_not_import_pytorch(artifacts, tmp_path, monkeypatch):
    import builtins
    original = builtins.__import__
    def without_torch(name, *args, **kwargs):
        if name == "torch" or name.startswith("torch."):
            raise ImportError("PyTorch must not be needed by CPU ONNX replay")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", without_torch)
    assert run(artifacts, tmp_path / "torchless")["counts"]["model_windows"] == 1


@pytest.mark.parametrize("candidates", [[], ["tile_000000"] * 2, [str(i) for i in range(5)], ["missing"]])
def test_candidate_selection_is_bounded_and_explicit(artifacts, tmp_path, candidates):
    with pytest.raises(ValueError):
        run(artifacts, tmp_path / "out", candidate_tile_ids=candidates)


def test_window_retains_only_bounded_consecutive_images():
    window = CameraWindow(2, 1000)
    frame = {"image": np.zeros((3, 8, 8), np.float32), "roll_pitch": [0, 0]}
    for i in range(20):
        window.push({**frame, "sequence": i, "sensor_ns": i * 10_000_000, "host_ns": i * 10_000_000})
    assert len(window.frames) == 2
    assert window.frames[0]["sequence"] == 18
    assert window.push({**frame, "sequence": 22, "sensor_ns": 220_000_000, "host_ns": 220_000_000}) == "nonconsecutive_frame_sequence"
    assert len(window.frames) == 1
    with pytest.raises(ValueError, match="not ready"):
        window.inputs(np.zeros((3, 8, 8)), [1, 0, 0, 1], [1] * 11)
