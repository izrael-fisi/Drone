"""Synthetic end-to-end timing provenance through capture, index and model data."""

from copy import deepcopy
import json
import shutil

import pytest

from vision_nav.training_capture import record_training_session, write_session_checksums, sha256_file, _frame_camera_timing
from vision_nav.neural.dataset_release import create_dataset_release
from vision_nav.neural.training_dataset import build_training_index, EXPOSURE_TIMING, LEGACY_TIMING
from vision_nav.neural.training_readiness import evaluate_training_readiness
from vision_nav.neural.temporal_training_windows import _frame_context
from vision_nav.neural.crossview_dataset import build_crossview_dataset, load_crossview_dataset, MapConditionedDataset
from vision_nav.neural.training_timing import checked_exposure_frame

from test_map_conditioned_student import _windows
from test_capture_timing import HOST, camera_metadata, exchanges, remote


def _prepared(tmp_path):
    windows, registry, _ = _windows(tmp_path)
    packages = {}
    manifest = json.loads((windows / "manifest.json").read_text())
    for split in ("train", "validation"):
        window_path = windows / manifest["splits"][split]["path"]
        window = json.loads(window_path.read_text())
        inputs = tmp_path / f"inputs-{split}"
        inputs.mkdir()
        for i, frame in enumerate(window["frames"]):
            shutil.copyfile(frame["frame_path"], inputs / f"frame_{i}.png")
        session = tmp_path / split
        capture = record_training_session(session, calibration_id="cam@1", map_release_id="area@1",
            session_id=split, capture_count=2, interval_s=0, input_frames_dir=inputs)
        source = capture["manifest"]
        records = []
        for i, frame in enumerate(source["frames"]):
            host = HOST + 10_000_000_000 + i * 100_000_000
            frame.update(host_monotonic_ns=host + 700_000_000,
                         camera_backend="picamera2", camera_metadata=camera_metadata(host))
            records.append({"index": i, "host_monotonic_ns": host + 300_000_000,
                "sample": {"message_type": "ATTITUDE", "source_system": 1, "source_component": 1,
                    "vehicle_time_domain": "px4_boot_us", "vehicle_timestamp_us": remote(host) // 1000,
                    "roll_rad": 0.123456789, "pitch_rad": -0.234567891, "yaw_rad": 3.123456789}})
        rows = exchanges()
        source["camera"]["frame_timing"] = _frame_camera_timing(source["frames"])
        source["mavlink"].update(attitude_sample_count=2, timesync={
            "pre_capture": {"exchanges": rows[:3]}, "post_capture": {"exchanges": rows[3:]}})
        source["camera_to_body"] = {"status": "measured", "extrinsic_id": "synthetic-mount", "frame_id": "body_frd",
            "child_frame_id": "down_camera_optical", "transform_direction": "body_frd_T_camera_optical",
            "translation_m": {"x": 0, "y": 0, "z": 0}, "rotation_quat_xyzw": {"x": 0, "y": 0, "z": 0, "w": 1}}
        (session / "manifest.json").write_text(json.dumps(source))
        (session / source["telemetry_path"]).write_text("".join(json.dumps(row) + "\n" for row in records))
        write_session_checksums(session)
        release = create_dataset_release([session], release_id=f"{split}@1", version="1", source_revision="test",
            license_summary="synthetic", require_offline_gnss_reference=False,
            require_mavlink_timesync=False)
        release_path = tmp_path / f"{split}-release.json"
        release_path.write_text(json.dumps(release))
        index_dir = tmp_path / f"{split}-index"
        index = build_training_index(release_path, index_dir)
        samples = [json.loads(line) for line in (index_dir / "samples.jsonl").read_text().splitlines()]
        for i, sample in enumerate(samples):
            context, error = _frame_context({"sample": sample})
            assert error is None
            window["frames"][i] = context
        window["anchor"]["sample_id"] = samples[-1]["sample_id"]
        window_path.write_text(json.dumps(window) + "\n")
        manifest["splits"][split]["sha256"] = sha256_file(window_path)
        packages[split] = {"index": index, "samples": samples, "session": session, "release": release_path,
                           "index_dir": index_dir, "window": window}
    (windows / "manifest.json").write_text(json.dumps(manifest))
    return windows, registry, packages


def test_default_index_uses_native_attitude_despite_large_receive_delay(tmp_path):
    _, _, packages = _prepared(tmp_path)
    data = packages["train"]
    assert data["index"]["manifest"]["timing_policy"] == EXPOSURE_TIMING
    sample = data["samples"][0]
    assert sample["timestamp_ns"] == HOST + 10_000_000_000
    assert sample["attitude_alignment"]["native_delta_ms"] < 0.001
    assert sample["timestamp_reference"] == "first_row_exposure_start"
    assert sample["attitude"]["yaw_rad"] == 3.123456789
    report = evaluate_training_readiness(data["index_dir"], min_samples=2, min_capture_sessions=1,
        require_gps_labels=False, require_quality_metrics=False)
    assert report["status"] == "ready", report["errors"]
    assert report["flight_ready"] is False
    assert report["timing_policies"] == [EXPOSURE_TIMING]


def test_strict_map_conditioned_dataset_is_portable_and_matches_audits(tmp_path):
    pytest.importorskip("torch")
    windows, registry, _ = _prepared(tmp_path)
    root = tmp_path / "crossview"
    manifest = build_crossview_dataset([windows], registry, root)
    assert manifest["timing_policies"] == [EXPOSURE_TIMING]
    assert manifest["allow_legacy_timing"] is False
    moved = tmp_path / "portable"
    root.rename(moved)
    shutil.rmtree(tmp_path / "train-index")
    shutil.rmtree(tmp_path / "validation-index")
    dataset = MapConditionedDataset(moved, split="train", image_size=64, grid_size=4, heading_bins=8)
    item = dataset[0]
    assert item["inputs"]["time_offsets_s"][0].tolist() == pytest.approx([-0.1, 0])
    assert item["inputs"]["roll_pitch"][0, 0].tolist() == pytest.approx([0.123456789, -0.234567891])


@pytest.mark.parametrize("fault", ["timestamp", "sensor_timestamp", "attitude", "session", "checksum", "sequence", "image"])
def test_frame_cannot_silently_diverge_from_exposure_evidence(tmp_path, fault):
    _, _, packages = _prepared(tmp_path)
    frame = deepcopy(packages["train"]["window"]["frames"][0])
    if fault == "timestamp":
        frame["timestamp_ns"] += 1
    elif fault == "sensor_timestamp":
        frame["sensor_timestamp_ns"] += 1
    elif fault == "attitude":
        frame["attitude_rpy_rad"][0] += 0.1
    elif fault == "session":
        frame["capture_session_id"] = "another-session"
    elif fault == "checksum":
        frame["timing_audit"]["sha256"] = "0" * 64
    elif fault == "image":
        frame["frame_sha256"] = "0" * 64
    else:
        frame["timing_audit"]["sequence"] = 555
    with pytest.raises(ValueError):
        checked_exposure_frame(frame, {})


def test_old_windows_require_explicit_research_override(tmp_path):
    windows, registry, _ = _windows(tmp_path)
    with pytest.raises(ValueError, match="exposure-native"):
        build_crossview_dataset([windows], registry, tmp_path / "strict")
    manifest = build_crossview_dataset([windows], registry, tmp_path / "legacy", allow_legacy_timing=True)
    assert manifest["timing_policies"] == [LEGACY_TIMING]
    assert manifest["sensor_delay_calibrated"] is False


def test_dataset_release_binds_original_capture_manifest(tmp_path):
    _, _, packages = _prepared(tmp_path)
    session = packages["train"]["session"]
    path = session / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["frames"][0]["camera_metadata"]["sensor_timestamp_ns"] += 1
    path.write_text(json.dumps(manifest))
    write_session_checksums(session)
    with pytest.raises(ValueError, match="changed since DatasetRelease"):
        build_training_index(packages["train"]["release"], tmp_path / "changed-index")


def test_no_clock_evidence_yields_rejections_not_legacy_fallback(tmp_path):
    _, _, packages = _prepared(tmp_path)
    data = packages["train"]
    session = data["session"]
    path = session / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["mavlink"]["timesync"] = {}
    path.write_text(json.dumps(manifest))
    write_session_checksums(session)
    release = create_dataset_release([session], release_id="incomplete@1", version="1", source_revision="test",
        license_summary="synthetic", require_attitude=False, require_offline_gnss_reference=False, require_mavlink_timesync=False)
    data["release"].write_text(json.dumps(release))
    result = build_training_index(data["release"], tmp_path / "rejected-index")
    assert result["manifest"]["status"] == "no_eligible_samples"
    assert result["manifest"]["skipped_count"] == 2
    assert all(row["reason"] == "px4_clock_fit_unavailable" for row in result["manifest"]["skipped"])


@pytest.mark.parametrize("field", ["roll_pitch_rad", "time_offsets_s"])
def test_portable_inputs_still_match_audit_after_resigning_split(tmp_path, field):
    windows, registry, _ = _prepared(tmp_path)
    root = tmp_path / "crossview"
    build_crossview_dataset([windows], registry, root)
    path = root / "train.jsonl"
    row = json.loads(path.read_text())
    if field == "roll_pitch_rad":
        row[field][0][0] += 0.1
    else:
        row[field][0] -= 0.1
    path.write_text(json.dumps(row) + "\n")
    manifest = json.loads((root / "manifest.json").read_text())
    manifest["splits"]["train"]["sha256"] = sha256_file(path)
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="differ"):
        load_crossview_dataset(root)
