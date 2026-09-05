"""Actual ONNX and native VIO on synthetic single-owner camera/IMU streams."""

from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import time

import cv2
import numpy as np
import pytest
import yaml

from vision_nav.camera import load_camera_calibration
from vision_nav.camera_extrinsics import create_extrinsics_document
from vision_nav.neural.crossview_images import image_tensor, pixels_tensor
from vision_nav.neural.live_sensor_inference import LiveWindowInference, run_live_learned_session, main
from vision_nav.neural.map_conditioned_export import MapConditionedOnnxProbe
from vision_nav.neural.replay_retrieval import build_replay_retrieval_index
from vision_nav.vio.sensor_session import LiveSensorSession
from vision_nav.vio.openvins_replay import digest, read_yaml, write_yaml
from test_live_sensor_buffer import warmed, sample, frame, exchange
from test_live_sensor_session import SyntheticDevices
from test_recorded_training_capture import AttitudeRig
from test_prepare_vio_capture import sources, CHECKOUT
from test_map_conditioned_replay import artifacts
from test_replay_retrieval import retrieval_artifacts


@pytest.fixture
def rig_factory(sources, artifacts, retrieval_artifacts, tmp_path):
    binary = os.environ.get("VISION_NAV_OPENVINS_BINARY")
    if not binary:
        pytest.skip("Actual native build required")
    camera = read_yaml(sources[1] / "kalibr_imucam_chain.yaml")
    camera["cam0"].update(resolution=[64, 64], intrinsics=[50., 50., 31.5, 31.5])
    write_yaml(sources[1] / "kalibr_imucam_chain.yaml", camera)
    profile_path = sources[1] / "sensor_profile.json"
    profile = json.loads(profile_path.read_text())
    profile["camera_calibration_id"] = "cam@1"
    profile["checksums"]["kalibr_imucam_chain.yaml"] = digest(sources[1] / "kalibr_imucam_chain.yaml")
    profile_path.write_text(json.dumps(profile))
    manifest = json.loads((sources[0] / "manifest.json").read_text())
    for row in manifest["frames"]:
        image_path = sources[0] / row["path"]
        image = cv2.imread(str(image_path))
        image = cv2.warpAffine(image, np.array([[.2, 0., -.5], [0., .2, 7.5]]), (64, 64))
        assert cv2.imwrite(str(image_path), image)
    ext = tmp_path / "extrinsics.yaml"
    ext.write_text(yaml.safe_dump(create_extrinsics_document(extrinsic_id="synthetic", camera_calibration_id="cam@1",
        translation_m=(0., 0., 0.), rotation_quat_xyzw=(0., 0., 0., 1.),
        measurement_method="synthetic fixture not measured aircraft", translation_sigma_m=.01, rotation_sigma_deg=.1)))
    index = tmp_path / "index"
    build_replay_retrieval_index(retrieval_artifacts / "export", artifacts / "reference.json",
                                artifacts / "tiles/tile_layout.json", index)
    def create(*, frames=40, attitude=True, actual_models=False, isolated=False, worker_options=None, session_options=None, **kwargs):
        retrieval, selected_index, local_config = retrieval_artifacts / "export", index, None
        if actual_models:
            actual_retrieval, actual_local = os.environ.get("VISION_NAV_RETRIEVAL_EXPORT"), os.environ.get("VISION_NAV_LOCAL_EXPORT")
            if not actual_retrieval or not actual_local:
                pytest.skip("Actual MegaLoc and XFeat/LighterGlue exports required")
            retrieval, selected_index = Path(actual_retrieval), tmp_path / "actual-index"
            build_replay_retrieval_index(retrieval, artifacts / "reference.json", artifacts / "tiles/tile_layout.json", selected_index)
            from vision_nav.neural.learned_correspondence import LOCAL_CONFIG_SCHEMA
            local_config = tmp_path / "local.json"
            local_config.write_text(json.dumps({"schema_version": LOCAL_CONFIG_SCHEMA, "backend": "onnx_cpu",
                "export_dir": str(Path(actual_local).resolve()), "px4_external_vision": "disabled"}))
        rig = (AttitudeRig if attitude else SyntheticDevices)(sources, **kwargs)
        session = LiveSensorSession(Path(binary), CHECKOUT, sources[1], tmp_path / "sensors",
            frame_count=frames, duration_s=30., camera_factory=rig.camera_factory, link_factory=rig.link_factory,
            **(session_options or {}))
        from vision_nav.neural.inference_worker import ProcessWindowInference
        implementation = ProcessWindowInference if isolated else LiveWindowInference
        inference = implementation(session, artifacts / "export", artifacts / "reference.json",
            artifacts / "tiles/tile_layout.json", artifacts / "camera.yaml", ext,
            retrieval_export=retrieval, retrieval_index=selected_index, local_verification_config=local_config,
            min_sharpness=0., max_saturated_fraction=1., **(worker_options or {}))
        return session, inference, rig
    return create


def test_actual_retrieval_local_and_student_with_native(rig_factory, tmp_path):
    session, inference, _ = rig_factory(frames=160, actual_models=True)
    report = run_live_learned_session(session, inference, tmp_path / "learned")
    assert report["status"] == "completed" and report["model_windows"] > 0, report
    assert report["source_session"]["native"]["completed_batch_count"] == 160
    rows = [json.loads(line) for line in (tmp_path / "learned/trace.jsonl").read_text().splitlines()]
    assert rows[-1]["sequence"] == 159
    attempts = [attempt for row in rows for attempt in row.get("local_verification", {}).get("attempts", [])]
    assert any(a.get("feature_counts", {}).get("query", 0) >= 6 and a.get("feature_counts", {}).get("map", 0) >= 6 for a in attempts)
    assert all(not row["position_authority"] for row in rows)


def test_real_model_session_can_run_alignment_audit_without_fabricating_noise(rig_factory, tmp_path):
    from vision_nav.estimation.live_alignment import LiveAlignmentObserver

    session, inference, rig = rig_factory(frames=160, actual_models=True, isolated=True)
    reference = json.loads(Path(inference._binding["reference_map"]).read_text())
    config = {"map_release_id": reference["release_id"], "model_release_id": "synthetic-lab-stack@1",
        "calibration_id": "cam@1", "clock_id": "synthetic-px4-clock", "uncertainty_model_id": "not-qualified",
        "imu_T_camera": session.native._camera["T_imu_cam"], "map_common_left_covariance": np.zeros((6, 6)).tolist()}
    observer = LiveAlignmentObserver(session, config)
    report = run_live_learned_session(session, inference, tmp_path / "learned", alignment_observer=observer)
    assert report["status"] == "completed", report
    assert report["source_session"]["native"]["completed_batch_count"] == 160
    status = report["research_alignment"]
    assert status["state"] == "stopped" and not status["thread_alive"]
    assert status["uncertainty_mode"] == "audit_only_no_noise"
    rows = [json.loads(line) for line in (tmp_path / "learned/trace.jsonl").read_text().splitlines()]
    assert rows and report["research_alignment_latency_ms"]
    assert all(row["research_alignment"]["current_estimate"] is None for row in rows)
    assert all(row["research_alignment_completed_host_monotonic_ns"] >= row["completed_host_monotonic_ns"] for row in rows)
    assert rig.camera_count == rig.link_count == 1 and rig.camera_closed and rig.link_closed
    assert not report["inference_worker"]["process_alive"]
    assert not report["global_fusion_connected"]


def test_real_models_are_dispatched_only_after_native_query_retention(rig_factory, tmp_path, monkeypatch):
    pytest.importorskip("gtsam")
    from vision_nav.estimation.live_alignment import LiveAlignmentObserver
    from vision_nav.estimation.alignment_worker import ProcessAlignmentEngine

    session, inference, rig = rig_factory(frames=160, actual_models=True, isolated=True,
        session_options={"anchor_capacity": 4, "record_images": True})
    reference = json.loads(Path(inference._binding["reference_map"]).read_text())
    config = {"map_release_id": reference["release_id"], "model_release_id": "synthetic-lab-stack@1",
        "calibration_id": "cam@1", "clock_id": "synthetic-px4-clock", "uncertainty_model_id": "not-qualified",
        "imu_T_camera": session.native._camera["T_imu_cam"], "map_common_left_covariance": np.zeros((6, 6)).tolist()}
    engine = ProcessAlignmentEngine(Path(__file__).parent/"fixtures/alignment_noise.py")
    observer = LiveAlignmentObserver(session, config, process_engine=engine,
        continuous_refresh_interval_s=.02, retain_query_anchors=True)
    real_process, admitted = inference.process, []
    def process(item):
        frame = item["tickets"][-1]
        query = observer.anchors.query_for({"timestamp_ns": frame.timestamp_ns, "frame_sha256": frame.pixel_sha256})
        assert query is not None
        assert frame.timestamp_ns in session.native.status()["anchor_control"]["active_anchor_timestamps_ns"]
        admitted.append(query)
        return real_process(item)
    monkeypatch.setattr(inference, "process", process)
    report = run_live_learned_session(session, inference, tmp_path/"learned", alignment_observer=observer)
    assert report["status"] == "completed" and admitted, report
    assert rig.camera_count == rig.link_count == 1 and rig.camera_closed and rig.link_closed
    assert report["source_session"]["native"]["completed_batch_count"] == 160
    ownership = report["research_alignment"]["query_anchors"]
    assert ownership["closed"] and ownership["owned_timestamps_ns"] == []
    assert ownership["evidence_sha256"] == digest(tmp_path/"learned/query-anchor-trace.jsonl")
    assert not report["inference_worker"]["process_alive"] and not engine.status()["process_alive"]
    rows = [json.loads(line) for line in (tmp_path/"learned/trace.jsonl").read_text().splitlines()]
    assert len(rows) == len(admitted)
    for row, query in zip(rows, admitted):
        assert row["query_anchor_admission"]["ticket"] == query
        assert row["anchor_vio_timestamp_ns"] == query["timestamp_ns"]
        assert row["query_anchor_admission"]["recorded_host_monotonic_ns"] <= row["completed_host_monotonic_ns"]
        assert not row["position_authority"] and row["px4_external_vision"] == "disabled"


def test_actual_live_onnx_inference_does_not_block_native_sensor_owners(rig_factory, tmp_path, monkeypatch):
    session, inference, rig = rig_factory(frames=160)
    original = inference.models.probe.run
    inputs = []
    def slow(inputs_arg, **kwargs):
        inputs.append(deepcopy(inputs_arg))
        time.sleep(.08)
        return original(inputs_arg, **kwargs)
    monkeypatch.setattr(inference.models.probe, "run", slow)
    result = run_live_learned_session(session, inference, tmp_path / "learned")
    assert result["status"] == "completed", result
    assert result["model_windows"] > 2, result
    assert result["source_session"]["delivered_frames"] == 160
    assert result["source_session"]["native"]["completed_batch_count"] == 160
    assert result["source_session"]["superseded_learned_windows"] > 0
    assert not result["source_session"]["thread_alive"]
    assert rig.camera_count == rig.link_count == 1 and rig.camera_closed and rig.link_closed
    rows = [json.loads(line) for line in (tmp_path / "learned/trace.jsonl").read_text().splitlines()]
    assert rows[-1]["sequence"] == 159
    assert all(not row["position_authority"] and row["navigation_mode"] == "NO_GLOBAL_FIX" for row in rows)
    assert sum(r["native_after"]["completed_batch_count"]-r["native_before"]["completed_batch_count"] for r in rows) > 0
    dispatch = [json.loads(line) for line in (session.root / "dispatch.jsonl").read_text().splitlines()]
    for row in rows:
        assert row["anchor_pixel_sha256"] == dispatch[row["sequence"]]["pixel_sha256"]
        assert row["anchor_vio_timestamp_ns"] == dispatch[row["sequence"]]["timestamp_ns"]
        assert row["clone_retention"]["anchor_timestamp_ns"] == row["anchor_vio_timestamp_ns"]
        assert not row["clone_retention"]["fusion_eligible"]
    for value in inputs:
        np.testing.assert_allclose(value["roll_pitch"], np.array([[[.01, -.02], [.01, -.02]]]), atol=1e-8)
    assert not list((session.native.capture / "images").iterdir())


def test_missing_attitude_rejects_only_learned_windows(rig_factory, tmp_path):
    session, inference, _ = rig_factory(attitude=False, frames=8)
    result = run_live_learned_session(session, inference, tmp_path / "learned")
    assert result["status"] == "completed" and result["model_windows"] == 0
    assert result["rejected_windows"] > 0
    assert result["source_session"]["native"]["completed_batch_count"] == 8
    rows = [json.loads(line) for line in (tmp_path / "learned/trace.jsonl").read_text().splitlines()]
    assert all(row["reason"] == "compatible_native_attitude_missing" for row in rows)


def test_provider_failure_remains_rejected_while_native_finishes(rig_factory, tmp_path, monkeypatch):
    session, inference, _ = rig_factory(frames=8)
    def fail(*args, **kwargs):
        raise RuntimeError("provider failure fixture")
    monkeypatch.setattr(inference.models.probe, "run", fail)
    result = run_live_learned_session(session, inference, tmp_path / "learned")
    assert result["status"] == "completed" and result["model_windows"] == 0
    assert result["source_session"]["native"]["completed_batch_count"] == 8
    assert result["rejected_windows"] > 0


def test_changed_release_during_shutdown_prevents_completed_report(rig_factory, tmp_path, monkeypatch):
    session, inference, _ = rig_factory(frames=8)
    original = session.stop
    def changed_on_stop(*args, **kwargs):
        closed = original(*args, **kwargs)
        path = tmp_path / "extrinsics.yaml"
        path.write_text(path.read_text()+"\n# changed fixture after inference\n")
        return closed
    monkeypatch.setattr(session, "stop", changed_on_stop)
    result = run_live_learned_session(session, inference, tmp_path / "learned")
    assert result["status"] == "incomplete"
    assert "final_release_verification_failed" in result["error"]
    assert not result["source_session"]["thread_alive"]


def test_late_sensor_failure_vetoes_running_model_result(rig_factory, tmp_path, monkeypatch):
    session, inference, _ = rig_factory(frames=12, camera_failure=6)
    original = inference.models.probe.run
    def delayed(*args, **kwargs):
        time.sleep(.3)
        return original(*args, **kwargs)
    monkeypatch.setattr(inference.models.probe, "run", delayed)
    result = run_live_learned_session(session, inference, tmp_path / "learned")
    assert result["status"] == "incomplete" and result["model_windows"] == 0, result
    assert result["source_session"]["state"] == "failed"


@pytest.mark.parametrize("fault", ["epoch", "sequence", "pixel_hash", "context", "duplicate", "attitude", "uncertainty"])
def test_wrong_ticket_or_context_cannot_produce_model_evidence(rig_factory, fault):
    session, inference, _ = rig_factory(frames=8)
    session.start()
    try:
        assert session.join(10)
        item = session.take_latest_window()
        assert item
        if fault == "epoch":
            item["worker_run_id"] = "different"
        elif fault == "sequence":
            item["tickets"] = (replace(item["tickets"][0], sequence=99), item["tickets"][1])
        elif fault == "pixel_hash":
            item["tickets"] = (item["tickets"][0], replace(item["tickets"][1], pixel_sha256="0"*64))
        elif fault == "context":
            item["contexts"][1]["conversion"]["px4_timestamp_ns"] += 1
        elif fault == "attitude":
            item["contexts"][1]["attitude_rpy_rad"][0] = float("nan")
        elif fault == "uncertainty":
            item["contexts"][1]["total_timing_envelope_ms"] = 50.
        else:
            assert inference.process(item)["state"] == "MODEL_EVIDENCE"
        assert inference.process(item)["state"] == "REJECTED"
    finally:
        session.stop()


@pytest.mark.parametrize("gray", [False, True])
def test_in_memory_preprocessing_exactly_matches_disk_path(artifacts, tmp_path, gray):
    image = np.random.default_rng(50).integers(0, 256, (64, 64, 3), dtype=np.uint8)
    if gray:
        image = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    path = tmp_path / "image.png"
    assert cv2.imwrite(str(path), image)
    camera = load_camera_calibration(artifacts / "camera.yaml")
    np.testing.assert_array_equal(image_tensor(path, 32, calibration=camera), pixels_tensor(image, 32, calibration=camera))


@pytest.mark.parametrize("fault", [None, "missing", "stale", "foreign", "reordered", "malformed"])
def test_auxiliary_attitude_is_bounded_and_never_substitutes_for_imu(fault):
    buffer, now = warmed()
    attitude = {"message_type": "ATTITUDE", "source_system": 1, "source_component": 1,
        "vehicle_time_domain": "px4_boot_us", "vehicle_timestamp_us": 3_000_000,
        "roll_rad": .1, "pitch_rad": .2, "yaw_rad": .3}
    if fault != "missing":
        if fault == "stale":
            attitude["vehicle_timestamp_us"] -= 40_000
        elif fault == "foreign":
            attitude["source_system"] = 2
        elif fault == "malformed":
            attitude["roll_rad"] = float("nan")
        buffer.push_telemetry(attitude)
        if fault == "reordered":
            buffer.push_telemetry(attitude)
    buffer.push_frame(frame(3_000_000_000))
    buffer.push_exchange(exchange(3_100_000_000))
    for ns in range(2_995_000_000, 3_020_000_000, 5_000_000):
        buffer.push_telemetry(sample(ns))
    batch = buffer.take_batch()
    assert batch and buffer.status()["error"] is None
    context = batch["learned_context"]
    assert context["status"] == ("paired" if fault is None else "rejected")
    assert len(batch["imu_samples"]) == 5
    if fault is None:
        assert context["attitude_rpy_rad"] == [.1, .2, .3]
        assert context["total_timing_envelope_ms"] <= 25


def test_live_cli_requires_prop_off_before_loading_files(tmp_path):
    paths = ["binary", "checkout", "sensor-profile", "output", "export-dir", "reference-map", "tile-layout",
             "calibration", "camera-to-body", "retrieval-export", "retrieval-index"]
    args = sum((["--"+name, str(tmp_path / name)] for name in paths), [])
    with pytest.raises(SystemExit) as error:
        main(args+["--endpoint", "serial:/dev/test:921600"])
    assert error.value.code == 2
