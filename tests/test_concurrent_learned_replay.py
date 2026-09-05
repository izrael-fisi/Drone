"""Actual native process + ONNX models on synthetic, explicitly non-flight data."""

import json
import os
from pathlib import Path

import cv2
import numpy as np
import pytest

from vision_nav.training_capture import write_session_checksums, _frame_camera_timing
from vision_nav.vio.openvins_replay import digest, read_yaml, write_yaml
from vision_nav.vio.prepare_capture import prepare_capture
from vision_nav.neural.replay_retrieval import build_replay_retrieval_index
from vision_nav.neural.learned_correspondence import LOCAL_CONFIG_SCHEMA
from test_prepare_vio_capture import sources, CHECKOUT, HOST_OFFSET, BOOT_OFFSET
from test_map_conditioned_replay import artifacts, run
from test_replay_retrieval import retrieval_artifacts


def _capture_for_export(sources, artifacts, tmp_path, *, max_clones=None):
    session, profile, _ = sources
    manifest = json.loads((session / "manifest.json").read_text())
    manifest["calibration"]["calibration_id"] = "cam@1"
    manifest["map_release_id"] = "area@1"
    manifest["camera"] = {"frame_timing": _frame_camera_timing(manifest["frames"])}
    manifest["camera_to_body"] = json.loads((artifacts / "train" / "manifest.json").read_text())["camera_to_body"]
    telemetry_path = session / "telemetry.jsonl"
    with telemetry_path.open("a") as stream:
        for frame in manifest["frames"]:
            path = session / frame["path"]
            image = cv2.imread(str(path))
            # Re-render this synthetic pinhole camera at the export fixture's
            # intrinsics. This is not resizing or recalibrating real aircraft data.
            image = cv2.warpAffine(image, np.array([[.2, 0., -.5], [0., .2, 7.5]]), (64, 64))
            assert cv2.imwrite(str(path), image)
            host = frame["camera_metadata"]["sensor_timestamp_ns"] - BOOT_OFFSET
            stream.write(json.dumps({"host_monotonic_ns": host, "sample": {
                "message_type": "ATTITUDE", "source_system": 1, "source_component": 1,
                "vehicle_timestamp_us": (host-HOST_OFFSET)//1000, "vehicle_time_domain": "px4_boot_us",
                "roll_rad": 0., "pitch_rad": 0., "yaw_rad": 0.}}) + "\n")
    (session / "manifest.json").write_text(json.dumps(manifest))
    write_session_checksums(session)
    path = profile / "kalibr_imucam_chain.yaml"
    camera = read_yaml(path)
    camera["cam0"].update(resolution=[64, 64], intrinsics=[50., 50., 31.5, 31.5])
    write_yaml(path, camera)
    description_path = profile / "sensor_profile.json"
    description = json.loads(description_path.read_text())
    description["camera_calibration_id"] = "cam@1"
    description["checksums"][path.name] = digest(path)
    description_path.write_text(json.dumps(description))
    native = tmp_path / "native-input"
    prepare_capture(CHECKOUT, session, profile, native, max_clones=max_clones)
    return session, native


def _execute(artifacts, sources, retrieval, tmp_path, *, local_export=None, max_clones=None):
    location = os.environ.get("VISION_NAV_OPENVINS_BINARY")
    if not location:
        pytest.skip("Set native binary for concurrent ONNX/native integration")
    session, native = _capture_for_export(sources, artifacts, tmp_path, max_clones=max_clones)
    index = tmp_path / "retrieval-index"
    build_replay_retrieval_index(retrieval, artifacts / "reference.json",
                                artifacts / "tiles" / "tile_layout.json", index)
    config = None
    if local_export:
        config = tmp_path / "local.json"
        config.write_text(json.dumps({"schema_version": LOCAL_CONFIG_SCHEMA, "backend": "onnx_cpu",
                                     "export_dir": str(Path(local_export).resolve()), "px4_external_vision": "disabled"}))
    output = tmp_path / "concurrent"
    result = run(artifacts, output, session_dir=session, native_vio_binary=Path(location),
        native_vio_capture=native, playback_speed=2., metrics_interval_s=.05,
        candidate_tile_ids=None, retrieval_export=retrieval, retrieval_index=index,
        local_verification_config=config, min_sharpness=0.)
    assert result["counts"]["model_windows"] > 1, result
    assert result["counts"]["retrieval_calls"] == result["counts"]["model_windows"]
    state = result["concurrent_replay"]
    assert state["native"]["completed_batch_count"] == 160
    assert not state["sensor_producer_alive"] and not state["native"]["thread_alive"]
    assert state["error"] is None
    assert result["runtime_metrics"]["native_vio_available_sample_count"] > 2
    rows = [json.loads(line) for line in (output / "trace.jsonl").read_text().splitlines()]
    assert all(row["position_authority"] is False and row["navigation_mode"] == "NO_GLOBAL_FIX" for row in rows)
    assert all(row["frame_sha256"] == row["concurrent_replay"]["anchor_frame_sha256"] for row in rows)
    assert rows[-1]["sequence"] == 159
    assert sum(row["concurrent_replay"]["native_after"]["completed_batch_count"] -
               row["concurrent_replay"]["native_before"]["completed_batch_count"] for row in rows) > 0
    assert state["selected_windows"] + state["superseded_windows"] == state["offered_windows"]
    native_rows = [json.loads(line) for line in
                   (output / "native-vio/local_states.jsonl").read_text().splitlines()]
    counts = {"all": {}, "native_not_terminal": {}}
    for row in rows:
        observation = row["concurrent_replay"]["clone_retention"]
        assert not observation["fusion_eligible"] and not observation["position_authority"]
        assert observation["anchor_timestamp_ns"] == row["concurrent_replay"]["anchor_vio_timestamp_ns"]
        completed = observation["completed_batch_count"]
        if completed:
            posterior = native_rows[completed-1]
            assert observation["posterior_timestamp_ns"] == posterior["timestamp_ns"]
            if observation["status"] != "unavailable":
                times = posterior["pose_window"]["pose_timestamps_ns"]
                assert observation["anchor_listed"] == (observation["anchor_timestamp_ns"] in times)
                assert observation["clone_count"] <= (max_clones or 11)
        key = observation["status"] if observation["reason"] is None else observation["reason"]
        counts["all"][key] = counts["all"].get(key, 0) + 1
        if observation["native_worker_state"] in {"starting", "waiting_for_sensors", "processing"}:
            active = counts["native_not_terminal"]
            active[key] = active.get(key, 0) + 1
    assert result["clone_retention_counts"] == counts
    if local_export:
        assert all("local_verification" in row for row in rows if row["state"] == "MODEL_EVIDENCE")
        assert any(attempt.get("feature_counts", {}).get("query", 0) >= 6 and
                   attempt.get("feature_counts", {}).get("map", 0) >= 6
                   for row in rows for attempt in row.get("local_verification", {}).get("attempts", []))
    return result


def test_actual_onnx_student_and_native_process(artifacts, sources, retrieval_artifacts, tmp_path):
    _execute(artifacts, sources, retrieval_artifacts / "export", tmp_path)


@pytest.mark.parametrize("max_clones", [11, 24, 32])
def test_actual_megaloc_xfeat_lighterglue_student_and_native(artifacts, sources, tmp_path, max_clones):
    retrieval = os.environ.get("VISION_NAV_RETRIEVAL_EXPORT")
    local = os.environ.get("VISION_NAV_LOCAL_EXPORT")
    if not retrieval or not local:
        pytest.skip("Set actual MegaLoc and XFeat/LighterGlue exports for full CPU workload")
    _execute(artifacts, sources, Path(retrieval), tmp_path, local_export=local, max_clones=max_clones)
