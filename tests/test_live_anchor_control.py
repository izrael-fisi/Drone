"""Actual native pipe controls; synthetic imagery/IMU, no aircraft authority."""

from copy import deepcopy
from dataclasses import replace
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time

import numpy as np
import pytest

from vision_nav.vio.anchor_control import AnchorCommand, SCHEMA, audit_control_stream
from vision_nav.vio.openvins_replay import digest, make_synthetic, run, seal_input
from vision_nav.vio.pose_window import JointPoseWindow, audit
from vision_nav.vio.stream_replay import sensor_batches
from vision_nav.vio.stream_worker import NativeVioStreamWorker
from test_vio_stream_worker import wait_for
from test_native_live_worker import arrival_inputs, make_worker, offer
from test_prepare_vio_capture import sources
from test_live_sensor_session import setup


@pytest.fixture(scope="module")
def controlled(tmp_path_factory):
    binary = os.environ.get("VISION_NAV_OPENVINS_BINARY")
    if not binary:
        pytest.skip("Actual pinned native binary required")
    binary = Path(binary).resolve()
    root = Path(__file__).resolve().parents[1]
    tmp = tmp_path_factory.mktemp("live-anchor")
    capture = tmp/"capture"
    make_synthetic(root/"research/vendor/open_vins", capture)
    baseline = tmp/"baseline"
    assert run(binary, capture, baseline, pose_window=True)["complete"]
    plain = [json.loads(line) for line in (baseline/"local_states.jsonl").read_text().splitlines()]
    first = next(i for i, row in enumerate(plain) if row["local_state"] == "LOCAL_ESTIMATE")
    anchor = plain[first]["timestamp_ns"]
    worker = NativeVioStreamWorker(binary, capture, tmp/"controlled", anchor_capacity=1)
    replies = []

    def command(op, stamp):
        receipt = worker.request_anchor(op, stamp, worker_run_id=worker.worker_run_id)
        assert receipt is not None
        ack = receipt.result(5.)
        replies.append(ack)
        return ack

    worker.start()
    try:
        assert command("retain", anchor)["reason"] == "no_initialized_posterior"
        for index, batch in enumerate(sensor_batches(capture/"events.jsonl")):
            wait_for(lambda: worker.submit(batch))
            wait_for(lambda: worker.status()["completed_batch_count"] == index+1)
            if index == first:
                before = worker.take_latest()
                assert command("retain", anchor+1)["reason"] == "camera_clone_not_retained"
                assert command("retain", anchor)["status"] == "applied"
                assert worker.take_latest() is None  # ACK must not manufacture a new sensor receipt.
                assert worker.status()["completed_batch_count"] == before["completed_batch_count"]
                assert command("retain", anchor)["reason"] == "already_retained"
                assert command("release", anchor-1)["reason"] == "anchor_not_retained"
            if index == len(plain)-2:
                retained = worker.take_latest()
                assert command("retain", plain[index]["timestamp_ns"])["reason"] == "anchor_capacity_reached"
                assert command("release", anchor)["status"] == "applied"
                assert worker.take_latest() is None
                assert command("retain", anchor)["reason"] == "camera_clone_not_retained"
        worker.finish()
        assert worker.join(10.) and worker.status()["status"] == "complete", worker.status()
        final = worker.take_latest()
    finally:
        assert worker.stop()
    return binary, capture, tmp, plain, worker, replies, retained, final, anchor


def test_pipe_retention_survives_marginalization_without_mutating_local_tracking(controlled):
    _, _, _, plain, worker, replies, retained, final, anchor = controlled
    window = JointPoseWindow.from_record(retained["record"])
    assert anchor not in plain[-2]["pose_window"]["pose_timestamps_ns"]
    assert anchor in window.timestamps_ns
    assert window.posterior_ns-anchor > 4_000_000_000
    assert np.linalg.norm(window.covariance_right_local[:6, -6:]) > 0
    assert anchor not in JointPoseWindow.from_record(final["record"]).timestamps_ns
    assert final["completed_batch_count"] == 160
    rows = [json.loads(line) for line in (worker.output/"local_states.jsonl").read_text().splitlines()]
    for expected, actual in zip(plain, rows):
        if expected["local_state"] == "LOCAL_ESTIMATE":
            for field in ("local_T_imu", "velocity_local_m_s", "gyro_bias_rad_s", "accel_bias_m_s2", "state_covariance"):
                np.testing.assert_allclose(actual[field], expected[field], rtol=1e-8, atol=1e-10)
        assert actual["position_authority"] is False and actual["px4_external_vision"] == "disabled"
    assert [ack["request_id"] for ack in replies] == list(range(1, 9))
    assert worker.status()["anchor_control"]["acknowledged"] == 8
    assert worker._process.poll() == 0 and not worker.status()["thread_alive"]


def test_ordered_control_stream_replays_byte_exact_and_audits_covariance(controlled):
    binary, capture, tmp, _, worker, _, _, _, _ = controlled
    checked = audit_control_stream(worker.output/"owned_stream.jsonl", worker.output/"anchor_acknowledgements.jsonl",
        worker.output/"local_states.jsonl", worker_run_id=worker.worker_run_id, capacity=1,
        sensor_events_path=capture/"events.jsonl")
    assert checked["control_count"] == 8 and checked["camera_count"] == 160
    exported = tmp/"exported"
    shutil.copytree(capture, exported)
    (exported/"input_manifest.json").unlink()
    shutil.copyfile(worker.output/"owned_stream.jsonl", exported/"owned_stream.jsonl")
    seal_input(exported, "synthetic_controlled_stream", {"anchor_control": {
        "capacity": 1, "worker_run_id": worker.worker_run_id, "stream_path": "owned_stream.jsonl"}})
    output = tmp/"replayed"
    assert run(binary, exported, output, pose_window=True)["complete"]
    for name in ("local_states.jsonl", "anchor_acknowledgements.jsonl"):
        assert (output/name).read_bytes() == (worker.output/name).read_bytes()
    assert audit(output)["anchor_control_verified"]["control_count"] == 8
    with pytest.raises(ValueError, match="Controlled replay"):
        run(binary, exported, tmp/"without-window")
    with pytest.raises(ValueError, match="discard"):
        NativeVioStreamWorker(binary, exported, tmp/"sensor-only")


def test_slow_map_fix_can_use_original_pose_retained_by_live_command(controlled):
    from vision_nav.estimation.correlated_window import payload_digest
    from vision_nav.estimation.pnp_window_alignment import align
    from test_pnp_window_alignment import fixture as map_fixture

    _, _, tmp, plain, _, _, retained, _, anchor = controlled
    initial = next(row for row in plain if row["timestamp_ns"] == anchor)
    request, _ = map_fixture(initial, shared=False, vio_cross=False)
    original = deepcopy(request["fixes"][-1])
    request["fixes"] = [original]
    request["native_record"] = retained["record"]
    d = len(original["request"]["uncertainty"]["joint_measurement_covariance"])
    request["joint_uncertainty"].update(snapshot_sha256=payload_digest(retained["record"]),
        pnp_request_sha256s=[payload_digest(original["request"])],
        joint_measurement_covariance=original["request"]["uncertainty"]["joint_measurement_covariance"],
        vio_input_cross_covariance=np.zeros((6*len(retained["record"]["pose_window"]["pose_timestamps_ns"]), d)).tolist())
    result = align(request)
    assert result["status"] == "research_alignment", result
    assert result["alignment"]["pose_timestamp_ns"] == retained["record"]["timestamp_ns"]
    assert request["fixes"][0] == original and not result["position_authority"]
    (tmp/"live-anchor-alignment.json").write_text(json.dumps({"scope": "native_stream_with_synthetic_map_pixels_and_noise",
        "request": request, "result": result}, allow_nan=False)+"\n")


@pytest.mark.parametrize("fault", ["ack_epoch", "ack_order", "ack_active", "window_active", "sensor", "missing_ack", "extra_ack", "null_event"])
def test_control_audit_rejects_corrupted_or_missing_evidence(controlled, tmp_path, fault):
    _, capture, _, _, worker, _, _, _, _ = controlled
    for name in ("owned_stream.jsonl", "anchor_acknowledgements.jsonl", "local_states.jsonl"):
        shutil.copyfile(worker.output/name, tmp_path/name)
    name = "anchor_acknowledgements.jsonl"
    if fault == "window_active": name = "local_states.jsonl"
    elif fault in {"sensor", "null_event"}: name = "owned_stream.jsonl"
    rows = [json.loads(line) for line in (tmp_path/name).read_text().splitlines()]
    if fault == "ack_epoch": rows[0]["worker_run_id"] = "0"*36
    elif fault == "ack_order": rows[0]["input_line"] += 1
    elif fault == "ack_active": rows[2]["active_anchor_timestamps_ns"] = []
    elif fault == "window_active":
        next(row for row in rows if row.get("pose_window"))["pose_window"]["passive_anchor_timestamps_ns"] = [1]
    elif fault == "sensor": next(row for row in rows if row.get("type") == "imu")["specific_force_m_s2"][0] += 1
    elif fault == "missing_ack": rows.pop()
    elif fault == "extra_ack": rows.append(rows[-1])
    elif fault == "null_event": rows.append(None)
    (tmp_path/name).write_text("".join(json.dumps(row)+"\n" for row in rows))
    with pytest.raises(ValueError):
        audit_control_stream(tmp_path/"owned_stream.jsonl", tmp_path/"anchor_acknowledgements.jsonl",
            tmp_path/"local_states.jsonl", worker_run_id=worker.worker_run_id, capacity=1,
            sensor_events_path=capture/"events.jsonl")


def test_control_backpressure_reserves_sensor_slots_and_queue_timeout_is_not_sent(controlled, tmp_path, monkeypatch):
    binary, capture, *_ = controlled
    worker = NativeVioStreamWorker(binary, capture, tmp_path/"queue", capacity=1, anchor_capacity=1)
    entered, release = threading.Event(), threading.Event()
    original = worker._read_result
    def hold(*args):
        entered.set()
        assert release.wait(5.)
        return original(*args)
    monkeypatch.setattr(worker, "_read_result", hold)
    batches = list(sensor_batches(capture/"events.jsonl"))[:3]
    worker.start()
    try:
        assert worker.submit(batches[0]) and entered.wait(5.)
        commands = [worker.request_anchor("retain", 1, worker_run_id=worker.worker_run_id) for _ in range(4)]
        assert all(commands)
        assert worker.request_anchor("retain", 1, worker_run_id=worker.worker_run_id) is None
        assert worker.submit(batches[1])  # Controls cannot consume the reserved sensor slot.
        assert not worker.submit(batches[2])
        commands[0]._deadline = time.monotonic()-1
        release.set()
        worker.finish()
        assert worker.join(10.)
        assert commands[0].result()["reason"] == "queue_deadline_exceeded"
        assert all(command.result()["status"] == "rejected" for command in commands[1:])
        assert worker.status()["completed_batch_count"] == 2
        report = json.loads((worker.output/"anchor_control_report.json").read_text())
        assert report["unapplied_or_indeterminate"][0]["request"]["request_id"] == 1
        acks = [json.loads(line) for line in (worker.output/"anchor_acknowledgements.jsonl").read_text().splitlines()]
        assert [ack["request_id"] for ack in acks] == [2, 3, 4]
    finally:
        release.set()
        assert worker.stop()


@pytest.mark.parametrize("fault", ["identity", "timeout"])
def test_sent_control_failure_invalidates_epoch_and_settles_receipts(controlled, tmp_path, monkeypatch, fault):
    binary, capture, *_ = controlled
    worker = NativeVioStreamWorker(binary, capture, tmp_path/fault, anchor_capacity=1)
    original = worker._read_line
    def bad_ack(reader, deadline, limit):
        if limit == 4096:
            if fault == "timeout":
                raise TimeoutError("injected ACK deadline exceeded")
            result = original(reader, deadline, limit)
            result["request_id"] += 1
            return result
        return original(reader, deadline, limit)
    monkeypatch.setattr(worker, "_read_line", bad_ack)
    worker.start()
    try:
        command = worker.request_anchor("retain", 1, worker_run_id=worker.worker_run_id)
        assert worker.join(5.) and worker.status()["status"] == "failed"
        assert command.result()["status"] == "indeterminate"
        assert worker._process.poll() is not None
        assert worker.take_latest() is None
        with pytest.raises(RuntimeError):
            worker.request_anchor("release", 1, worker_run_id=worker.worker_run_id)
    finally:
        assert worker.stop()


def test_live_controls_require_original_frame_ticket_and_preserve_recorded_input(arrival_inputs, tmp_path):
    worker = make_worker(arrival_inputs, tmp_path, anchor_capacity=2, record_images=True)
    worker.start()
    anchor = None
    try:
        for index in range(len(arrival_inputs[-1])):
            ticket = offer(worker, arrival_inputs, index)
            wait_for(lambda: worker.status()["completed_batch_count"] == index+1)
            if worker.status()["last_local_state"] == "LOCAL_ESTIMATE" and anchor is None:
                with pytest.raises(RuntimeError, match="owned frame ticket"):
                    worker.request_anchor("retain", ticket.timestamp_ns, worker_run_id=ticket.worker_run_id)
                for bad in (replace(ticket, worker_run_id="different"), replace(ticket, sequence=ticket.sequence+1),
                            replace(ticket, pixel_sha256="f"*64), replace(ticket, image_sha256="a"*64)):
                    with pytest.raises(ValueError): worker.retain_frame(bad)
                assert worker.retain_frame(ticket).result(5.)["status"] == "applied"
                anchor = ticket
        assert anchor is not None
        final = worker.take_latest()
        assert anchor.timestamp_ns in JointPoseWindow.from_record(final["record"]).timestamps_ns
        assert not list((worker.capture/"images").iterdir())
        assert worker.release_frame(anchor).result(5.)["status"] == "applied"
        worker.finish()
        assert worker.join(10.) and worker.status()["status"] == "complete", worker.status()
        report = json.loads((worker.root/"live_report.json").read_text())
        assert report["recording"]["complete"]
        assert report["anchor_control_report_sha256"] == digest(worker.output/"anchor_control_report.json")
        checked = audit_control_stream(worker.output/"owned_stream.jsonl", worker.output/"anchor_acknowledgements.jsonl",
            worker.output/"local_states.jsonl", worker_run_id=worker.worker_run_id, capacity=2,
            sensor_events_path=worker.root/"recording/events.jsonl")
        assert checked["control_count"] == 2 and checked["camera_count"] == 160
    finally:
        assert worker.stop()


def test_owned_sensor_session_advances_while_anchor_held_and_exports_exact_replay(setup, tmp_path):
    from vision_nav.vio.export_sensor_replay import export_sensor_replay, validate_recorded_session

    session, rig = setup(frame_count=160, duration_s=30., record_images=True, anchor_capacity=2)
    session.start()
    try:
        wait_for(lambda: session.native.status()["last_local_state"] == "LOCAL_ESTIMATE", timeout=15.)
        item = session.take_latest_window()
        assert item is not None
        anchor = item["tickets"][-1]
        ack = session.native.retain_frame(anchor).result(5.)
        assert ack["status"] == "applied", ack
        began = session.native.status()["completed_batch_count"]
        # No sensor service calls or new learned queries while this old pose is held.
        wait_for(lambda: session.native.status()["completed_batch_count"] >= began+25, timeout=10.)
        retained = session.native.take_latest()["record"]
        assert anchor.timestamp_ns not in retained["pose_window"]["normal_clone_timestamps_ns"]
        assert anchor.timestamp_ns in JointPoseWindow.from_record(retained).timestamps_ns
        assert session.native.release_frame(anchor).result(5.)["status"] == "applied"
        assert session.join(20.) and session.status()["state"] == "complete", session.status()
        assert rig.camera_count == rig.link_count == 1
        checked = validate_recorded_session(session.root)
        assert checked["anchor_control"]["capacity"] == 2
        exported = tmp_path/"controlled-export"
        manifest = export_sensor_replay(session.root, exported)
        assert manifest["source"]["anchor_control"]["worker_run_id"] == session.native.worker_run_id
        output = tmp_path/"control-export-replay"
        assert run(session.native.binary, exported, output, pose_window=True)["complete"]
        for name in ("local_states.jsonl", "anchor_acknowledgements.jsonl"):
            assert (output/name).read_bytes() == (session.native.output/name).read_bytes()
        assert audit(output)["anchor_control_verified"]["control_count"] == 2
        # The complete session cannot bless changes to the control stream.
        with (session.native.output/"owned_stream.jsonl").open("a") as stream:
            stream.write("{}\n")
        with pytest.raises(ValueError, match="checksum"):
            validate_recorded_session(session.root)
    finally:
        assert session.stop()


def test_command_wait_timeout_is_not_cancellation_and_owned_response_isolated():
    command = AnchorCommand({"request_id": 1}, time.monotonic()+1)
    with pytest.raises(TimeoutError, match="same receipt"): command.result()
    command._settle({"status": "applied", "items": [1]})
    response = command.result()
    response["items"].append(2)
    assert command.result()["items"] == [1]
    with pytest.raises(RuntimeError): command._settle({})
    for value in (True, float("nan"), -1, 61):
        with pytest.raises(ValueError): command.result(value)


@pytest.mark.parametrize("fault", ["epoch", "duplicate", "operation", "extra", "timestamp", "budget"])
def test_native_rejects_malformed_control_protocol(controlled, tmp_path, fault):
    binary, capture, _, _, worker, *_ = controlled
    event = {"type": "anchor_control", "schema_version": SCHEMA, "worker_run_id": worker.worker_run_id,
        "request_id": 1, "operation": "retain", "anchor_timestamp_ns": 1}
    if fault == "epoch": event["worker_run_id"] = "wrong"
    elif fault == "operation": event["operation"] = "reset"
    elif fault == "extra": event["pose"] = []
    elif fault == "timestamp": event["anchor_timestamp_ns"] = 1.5
    elif fault == "budget": event["request_id"] = 1025
    payload = json.dumps(event)+"\n"
    if fault == "duplicate": payload *= 2
    result = subprocess.run([str(binary), str(capture/"estimator_config.yaml"), str(capture/"events.jsonl"),
        str(tmp_path/"states.jsonl"), "--pose-window", "--stream-input", "--anchor-control", "1", worker.worker_run_id,
        str(tmp_path/"acks.jsonl")], input=payload, text=True, capture_output=True, timeout=10.)
    assert result.returncode == 2, result.stderr
    assert "anchor" in result.stderr.lower() or "timestamp" in result.stderr.lower()
    assert not (tmp_path/"states.jsonl").read_text()


def test_source_change_prevents_controlled_epoch_from_being_marked_complete(controlled, tmp_path):
    binary, capture, *_ = controlled
    worker = NativeVioStreamWorker(binary, capture, tmp_path/"source-change", anchor_capacity=1)
    worker.start()
    try:
        wait_for(lambda: worker.status()["status"] == "waiting_for_sensors")
        # Do not edit the shared repository while native tests are running.
        worker._control_source_hashes["anchor_control.py"] = "0"*64
        worker.finish()
        assert worker.join(5.) and worker.status()["status"] == "failed"
        assert "source" in worker.status()["error"]
        assert worker.take_latest() is None
        assert json.loads((worker.output/"anchor_control_report.json").read_text())["status"] == "failed"
    finally:
        assert worker.stop()


def test_anchor_api_rejects_bad_inputs_and_commands_after_close(controlled, tmp_path):
    binary, capture, *_ = controlled
    for capacity in (True, -1, 5):
        with pytest.raises(ValueError): NativeVioStreamWorker(binary, capture, tmp_path/"bad", anchor_capacity=capacity)
    worker = NativeVioStreamWorker(binary, capture, tmp_path/"validation", anchor_capacity=1)
    worker.start()
    try:
        for operation, stamp in (("reset", 1), ("retain", True), ("retain", 0), ("release", 2**63)):
            with pytest.raises(ValueError): worker.request_anchor(operation, stamp, worker_run_id=worker.worker_run_id)
        with pytest.raises(ValueError, match="epoch"):
            worker.request_anchor("retain", 1, worker_run_id="different")
        worker.finish()
        with pytest.raises(RuntimeError): worker.request_anchor("retain", 1, worker_run_id=worker.worker_run_id)
        assert worker.join(5.)
    finally:
        assert worker.stop()


def test_stop_settles_both_sent_and_queued_commands(controlled, tmp_path, monkeypatch):
    binary, capture, *_ = controlled
    worker = NativeVioStreamWorker(binary, capture, tmp_path/"stop-pending", anchor_capacity=1)
    entered, release = threading.Event(), threading.Event()
    original = worker._read_anchor_ack
    def hold(*args):
        entered.set()
        assert release.wait(5.)
        return original(*args)
    monkeypatch.setattr(worker, "_read_anchor_ack", hold)
    worker.start()
    try:
        sent = worker.request_anchor("retain", 1, worker_run_id=worker.worker_run_id)
        assert entered.wait(5.)
        queued = worker.request_anchor("retain", 2, worker_run_id=worker.worker_run_id)
        assert not worker.stop(.001)
        release.set()
        assert worker.join(5.)
        assert sent.result()["status"] == "indeterminate"
        assert queued.result()["status"] == "not_applied"
        assert worker._process.poll() is not None and worker.take_latest() is None
        assert worker.status()["anchor_control"]["pending"] == 0
        report = json.loads((worker.output/"anchor_control_report.json").read_text())
        assert len(report["unapplied_or_indeterminate"]) == 2
    finally:
        release.set()
        assert worker.stop()


def test_replay_does_not_bless_rewritten_manifest(controlled, tmp_path, monkeypatch):
    binary, capture, _, _, worker, *_ = controlled
    exported = tmp_path/"input"
    shutil.copytree(capture, exported)
    (exported/"input_manifest.json").unlink()
    shutil.copyfile(worker.output/"owned_stream.jsonl", exported/"owned_stream.jsonl")
    seal_input(exported, "synthetic_controlled_stream", {"anchor_control": {
        "capacity": 1, "worker_run_id": worker.worker_run_id, "stream_path": "owned_stream.jsonl"}})
    original = subprocess.run
    def changed(*args, **kwargs):
        result = original(*args, **kwargs)
        if "--anchor-control" in args[0]:
            path = exported/"input_manifest.json"
            manifest = json.loads(path.read_text())
            manifest["scope"] = "changed_after_native_execution"
            path.write_text(json.dumps(manifest))
        return result
    monkeypatch.setattr(subprocess, "run", changed)
    with pytest.raises(ValueError, match="source changed"):
        run(binary, exported, tmp_path/"replay", pose_window=True)
    assert not (tmp_path/"replay/report.json").exists()
