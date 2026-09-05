"""Real image/IMU native process with explicit synthetic conditional map inputs."""

from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading
import time

import numpy as np
import pytest

from vision_nav.vio import map_frame_control as control
from vision_nav.vio.openvins_replay import make_synthetic, run, digest
from vision_nav.vio.stream_replay import sensor_batches
from vision_nav.vio.stream_worker import NativeVioStreamWorker
from test_vio_stream_worker import wait_for


def bindings():
    return {"fix_id": "synthetic-fix", "map_release_id": "synthetic-map", "model_release_id": "test-model",
        "calibration_id": "synthetic-camera", "uncertainty_sha256": "a" * 64,
        "conditional_model": "complete_native_state_and_future_innovations_v1",
        "uncertainty_status": "research_unqualified"}


def initializer(snapshot, expiry):
    # Explicit synthetic joint conditional model, not inferred map/VIO independence.
    n = snapshot["dimension"]
    b = np.sin(np.arange(6 * n).reshape(6, n) + 1) * .015
    b[:, snapshot["query_variable_offset"]:snapshot["query_variable_offset"] + 6] += np.eye(6)
    t = np.eye(4)
    t[:3, 3] = [2., -1., .5]
    return {"snapshot_id": snapshot["snapshot_id"], "snapshot_sha256": snapshot["sha256"],
        "expires_at_ns": expiry, "local_T_map": t.tolist(), "regression_native": b.tolist(),
        "conditional_covariance_native": (np.eye(6) * .03).tolist(), "bindings": bindings()}


@pytest.fixture(scope="module")
def source(tmp_path_factory):
    value = os.environ.get("VISION_NAV_OPENVINS_BINARY")
    if not value:
        pytest.skip("Actual pinned native binary required")
    binary = Path(value).resolve()
    root = Path(__file__).resolve().parents[1]
    tmp = tmp_path_factory.mktemp("native-map-frame")
    capture = tmp / "capture"
    make_synthetic(root / "research/vendor/open_vins", capture)
    baseline = tmp / "baseline"
    assert run(binary, capture, baseline, pose_window=True)["complete"]
    rows = [json.loads(line) for line in (baseline / "local_states.jsonl").read_text().splitlines()]
    first = next(i for i, row in enumerate(rows) if row["local_state"] == "LOCAL_ESTIMATE")
    return binary, capture, tmp, rows, first


@pytest.fixture(scope="module")
def experiment(source):
    binary, capture, tmp, baseline, first = source
    worker = NativeVioStreamWorker(binary, capture, tmp / "controlled", anchor_capacity=1, map_frame_control=True)
    query = baseline[first]["timestamp_ns"]
    batches = list(sensor_batches(capture / "events.jsonl"))
    expiry_index = first + 55
    expiry = baseline[expiry_index]["timestamp_ns"]
    commands, snapshots, observations = [], [], []

    def command(op, payload):
        receipt = worker.request_map_frame(op, payload, worker_run_id=worker.worker_run_id)
        assert receipt is not None
        ack = receipt.result(5.)
        commands.append((receipt.request, ack))
        return ack

    def offer(i):
        wait_for(lambda: worker.submit(batches[i]))
        wait_for(lambda: worker.status()["completed_batch_count"] == i + 1)
        snapshot = worker.take_latest()
        assert snapshot is not None
        observations.append(snapshot)
        return snapshot

    worker.start()
    try:
        assert command("capture", {"query_timestamp_ns": query})["reason"] == "no_initialized_posterior"
        for i in range(first + 3):
            offer(i)
        anchor = worker.request_anchor("retain", query, worker_run_id=worker.worker_run_id)
        assert anchor.result(5.)["status"] == "applied"
        capture_ack = command("capture", {"query_timestamp_ns": query})
        assert capture_ack["status"] == "applied", capture_ack
        stale = capture_ack["snapshot"]
        offer(first + 3)
        assert command("initialize", initializer(stale, expiry))["status"] == "rejected"
        assert command("initialize", initializer(stale, expiry))["reason"] == "unknown_or_consumed_snapshot"
        snapshot = command("capture", {"query_timestamp_ns": query})["snapshot"]
        bad = initializer(snapshot, expiry); bad["snapshot_sha256"] = "0" * 64
        assert command("initialize", bad)["reason"] == "snapshot_digest_mismatch"
        snapshot = command("capture", {"query_timestamp_ns": query})["snapshot"]
        snapshots.append(snapshot)
        applied = command("initialize", initializer(snapshot, expiry))
        assert applied["status"] == "applied", applied
        active_id = applied["request_id"]
        assert worker.take_latest() is None  # A control ACK is not a new sensor receipt.
        assert worker.status()["completed_batch_count"] == first + 4
        assert command("capture", {"query_timestamp_ns": query})["reason"] == "map_frame_already_active"
        for i in range(first + 4, len(batches)):
            row = offer(i)["record"]
            projection = row["map_frame_projection"]
            if i < expiry_index:
                assert projection["status"] == "conditional_projection", projection
                assert projection["initialization_id"] == active_id
                assert projection["timestamp_ns"] == row["timestamp_ns"]
                assert projection["observation_timestamp_ns"] == query
                assert projection["map_age_s"] == (row["timestamp_ns"] - query) / 1e9
            else:
                assert projection["status"] == "unavailable" and "map_T_imu" not in projection
            if i == first + 10:
                receipt = worker.request_anchor("release", query, worker_run_id=worker.worker_run_id)
                assert receipt.result(5.)["status"] == "applied"
            if i == expiry_index:
                assert worker.status()["map_frame_control"]["active_initialization_id"] is None
        assert command("clear", {})["status"] == "applied"
        worker.finish()
        assert worker.join(10.) and worker.status()["status"] == "complete", worker.status()
        with pytest.raises(control.MapFrameInputClosed):
            worker.request_map_frame("clear", {}, worker_run_id=worker.worker_run_id)
    finally:
        assert worker.stop()
    return worker, commands, snapshots, observations, query, expiry_index


def test_map_frame_advances_with_actual_native_camera_imu_and_survives_query_eviction(source, experiment):
    _, _, _, baseline, first = source
    worker, _, _, observed, query, expiry_index = experiment
    actual = [json.loads(line) for line in (worker.output / "local_states.jsonl").read_text().splitlines()]
    assert len(actual) == len(baseline) == 160
    positive = [row for row in actual if row["map_frame_projection"]["status"] == "conditional_projection"]
    assert len(positive) == expiry_index - first - 4 == 51
    assert query not in positive[-1]["pose_window"]["pose_timestamps_ns"]
    assert len({row["map_frame_projection"]["timestamp_ns"] for row in positive}) == 51
    assert np.linalg.norm(np.array(positive[0]["map_frame_projection"]["map_T_imu"])
                          - np.array(positive[-1]["map_frame_projection"]["map_T_imu"])) > .01
    for expected, row in zip(baseline, actual):
        if expected["local_state"] == "LOCAL_ESTIMATE":
            for key in ("local_T_imu", "velocity_local_m_s", "gyro_bias_rad_s", "accel_bias_m_s2", "state_covariance"):
                np.testing.assert_allclose(row[key], expected[key], atol=1e-10, rtol=1e-8)
        assert row["position_authority"] is False and row["px4_external_vision"] == "disabled"
        assert row["map_frame_projection"]["covariance_calibrated"] is False
    assert all(item["received_host_monotonic_ns"] > 0 for item in observed)
    report = json.loads((worker.output / "map_frame_control_report.json").read_text())
    assert report["status"] == "complete" and not report["unapplied_or_indeterminate"]
    assert report["acknowledged_count"] == report["command_count"]
    for name, sha in report["checksums"].items():
        assert digest(worker.output / name) == sha
    assert not worker.status()["thread_alive"] and worker._process.poll() == 0


def test_full_native_control_stream_replays_exactly(source, experiment, tmp_path):
    binary, capture, *_ = source
    worker, *_ = experiment
    exported = tmp_path / "input"
    shutil.copytree(capture, exported)
    shutil.copyfile(worker.output / "owned_stream.jsonl", exported / "controlled.jsonl")
    out = tmp_path / "output"; out.mkdir()
    with (out / "native.log").open("w") as log:
        result = subprocess.run([str(binary), str(exported / "estimator_config.yaml"), str(exported / "controlled.jsonl"),
            str(out / "local_states.jsonl"), "--pose-window", "--anchor-control", "1", worker.worker_run_id,
            str(out / "anchor_acknowledgements.jsonl"), "--map-frame-control", worker.worker_run_id,
            str(out / "map_frame_acknowledgements.jsonl")], stdout=log, stderr=subprocess.STDOUT, timeout=30)
    assert result.returncode == 0, (out / "native.log").read_text()
    for name in ("local_states.jsonl", "anchor_acknowledgements.jsonl", "map_frame_acknowledgements.jsonl"):
        assert (out / name).read_bytes() == (worker.output / name).read_bytes()


@pytest.mark.parametrize("fault", ["epoch", "digest", "snapshot_time", "layout", "query", "authority"])
def test_parent_rejects_bad_snapshot_acknowledgements(experiment, fault):
    worker, commands, *_ = experiment
    request, _ = next((req, ack) for req, ack in commands if req["operation"] == "capture" and ack["status"] == "applied")
    raw = next(json.loads(line) for line in (worker.output / "map_frame_acknowledgements.jsonl").read_text().splitlines()
               if json.loads(line)["request_id"] == request["request_id"])
    if fault == "epoch": raw["worker_run_id"] = "0" * 36
    elif fault == "digest": raw["snapshot"]["sha256"] = "0" * 64
    elif fault == "authority": raw["position_authority"] = True
    else:
        snapshot = json.loads(raw["snapshot"]["canonical_json"])
        if fault == "snapshot_time": snapshot["posterior_timestamp_ns"] += 1
        elif fault == "layout": snapshot["layout"][0]["offset"] = 1
        elif fault == "query": snapshot["query_variable_offset"] = 0
        encoded = json.dumps(snapshot, sort_keys=True)
        raw["snapshot"] = {"canonical_json": encoded, "sha256": hashlib.sha256(encoded.encode()).hexdigest()}
    with pytest.raises(ValueError):
        control.validate_ack(raw, request, raw["posterior_timestamp_ns"], None)


@pytest.mark.parametrize("fault", ["time", "original_time", "covariance", "pose", "authority", "release", "expiry", "unavailable_pose"])
def test_parent_rejects_bad_projections(experiment, fault):
    worker, commands, _, observed, *_ = experiment
    value = deepcopy(next(s["record"]["map_frame_projection"] for s in observed
                          if s["record"]["map_frame_projection"]["status"] == "conditional_projection"))
    request = next(req for req, ack in commands if req["operation"] == "initialize" and ack["status"] == "applied")
    active = {**request, "observation_timestamp_ns": value["observation_timestamp_ns"]}
    stamp = value["timestamp_ns"]
    if fault == "time": value["timestamp_ns"] += 1
    elif fault == "original_time": value["observation_timestamp_ns"] += 1
    elif fault == "covariance": value["covariance"][0][0] = -1
    elif fault == "pose": value["map_T_imu"][3][3] = 2
    elif fault == "authority": value["position_authority"] = True
    elif fault == "release": value["bindings"]["map_release_id"] = "other-map"
    elif fault == "expiry": value["expires_at_ns"] = stamp
    elif fault == "unavailable_pose": value["status"] = "unavailable"; value["initialization_id"] = None
    with pytest.raises(ValueError):
        control.validate_projection(value, worker.worker_run_id, stamp, active)


def test_map_commands_are_opt_in_and_own_payloads(source, experiment, tmp_path):
    binary, capture, *_ = source
    worker = NativeVioStreamWorker(binary, capture, tmp_path / "disabled")
    with pytest.raises(RuntimeError):
        worker.request_map_frame("clear", {}, worker_run_id=worker.worker_run_id)
    _, _, snapshots, *_ = experiment
    value = initializer(snapshots[0], 20_000_000_000)
    owned = control.prepare_payload("initialize", value)
    value["regression_native"][0][0] += 9
    assert owned["regression_native"] != value["regression_native"]
    for changed in ({**value, "extra": 1}, {**value, "snapshot_sha256": "invalid"},
                    {**value, "regression_native": [[0.] * 257] * 6},
                    {**value, "bindings": {}}, {"query_timestamp_ns": True}):
        with pytest.raises(ValueError):
            control.prepare_payload("capture" if "query_timestamp_ns" in changed else "initialize", changed)


def test_shared_control_backpressure_and_orderly_close(source, tmp_path, monkeypatch):
    binary, capture, *_ = source
    worker = NativeVioStreamWorker(binary, capture, tmp_path / "queue", capacity=1, map_frame_control=True)
    entered, release = threading.Event(), threading.Event()
    original = worker._read_result
    def hold(*args):
        entered.set()
        assert release.wait(5.)
        return original(*args)
    monkeypatch.setattr(worker, "_read_result", hold)
    batches = list(sensor_batches(capture / "events.jsonl"))[:2]
    worker.start()
    try:
        assert worker.submit(batches[0]) and entered.wait(5.)
        receipts = [worker.request_map_frame("clear", {}, worker_run_id=worker.worker_run_id) for _ in range(4)]
        assert all(receipts)
        assert worker.request_map_frame("clear", {}, worker_run_id=worker.worker_run_id) is None
        status = worker.status()
        assert status["map_frame_control"]["pending"] == status["control_queue"]["pending"] == 4
        assert status["map_frame_control"]["backpressure_count"] == 1
        assert status["anchor_control"]["pending"] == status["anchor_control"]["backpressure_count"] == 0
        assert worker.submit(batches[1])
        receipts[0]._deadline = time.monotonic() - 1
        release.set(); worker.finish()
        assert worker.join(10.)
        assert receipts[0].result()["reason"] == "queue_deadline_exceeded"
        assert all(r.result()["status"] == "applied" for r in receipts[1:])
        assert worker.status()["completed_batch_count"] == 2
        assert worker.status()["map_frame_control"]["acknowledged"] == 3
    finally:
        release.set()
        assert worker.stop()


def test_bad_map_ack_terminates_and_settles_native_epoch(source, tmp_path, monkeypatch):
    binary, capture, *_ = source
    worker = NativeVioStreamWorker(binary, capture, tmp_path / "fault", map_frame_control=True)
    original = worker._read_line
    def corrupt(reader, deadline, limit):
        value = original(reader, deadline, limit)
        if value.get("schema_version") == control.ACK_SCHEMA:
            value["worker_run_id"] = "invalid"
        return value
    monkeypatch.setattr(worker, "_read_line", corrupt)
    worker.start()
    try:
        receipt = worker.request_map_frame("clear", {}, worker_run_id=worker.worker_run_id)
        assert worker.join(10.)
        assert worker.status()["status"] == "failed"
        assert receipt.result()["status"] == "indeterminate"
        assert worker.take_latest() is None and worker._process.poll() is not None
    finally:
        assert worker.stop()


def test_native_map_positions_are_visible_during_advancing_20hz_input(source, tmp_path):
    binary, capture, _, baseline, first = source
    worker = NativeVioStreamWorker(binary, capture, tmp_path / "timed", map_frame_control=True)
    batches = list(sensor_batches(capture / "events.jsonl"))
    start_index = first + 3
    query = baseline[first]["timestamp_ns"]
    def command(op, payload):
        receipt = worker.request_map_frame(op, payload, worker_run_id=worker.worker_run_id)
        assert receipt is not None
        return receipt.result(5.)
    worker.start()
    producer = None
    done, errors = threading.Event(), []
    observations = []
    try:
        for i in range(start_index):
            wait_for(lambda: worker.submit(batches[i]))
            wait_for(lambda: worker.status()["completed_batch_count"] == i + 1)
        captured = command("capture", {"query_timestamp_ns": query})
        assert captured["status"] == "applied", captured
        expiry = baseline[start_index + 60]["timestamp_ns"] + 1_000_000_000
        applied = command("initialize", initializer(captured["snapshot"], expiry))
        assert applied["status"] == "applied", applied
        def produce():
            try:
                beginning = time.monotonic()
                for j in range(60):
                    time.sleep(max(0, beginning + .05 * j - time.monotonic()))
                    wait_for(lambda: worker.submit(batches[start_index + j]))
                worker.finish()
            except Exception as exc:
                errors.append(str(exc))
            finally:
                done.set()
        start = time.monotonic_ns()
        producer = threading.Thread(target=produce)
        producer.start()
        deadline = time.monotonic() + 15
        while not done.is_set():
            assert time.monotonic() < deadline
            snapshot = worker.take_latest()
            if snapshot:
                p = snapshot["record"]["map_frame_projection"]
                if p["status"] == "conditional_projection":
                    observations.append({"observed_host_ns": time.monotonic_ns(), "sensor_timestamp_ns": p["timestamp_ns"],
                        "received_host_ns": snapshot["received_host_monotonic_ns"], "input_closed": worker.status()["input_closed"]})
            time.sleep(.005)
        stop = time.monotonic_ns()
        producer.join(5.)
        assert not producer.is_alive() and not errors
        assert worker.join(10.) and worker.status()["status"] == "complete", worker.status()
        visible = [row for row in observations if not row["input_closed"]]
        stamps = [row["sensor_timestamp_ns"] for row in visible]
        assert stamps and stamps == sorted(set(stamps))
        rows = [json.loads(line) for line in (worker.output / "local_states.jsonl").read_text().splitlines()]
        assert len(rows) == start_index + 60
        assert all(row["map_frame_projection"]["status"] == "conditional_projection" for row in rows[start_index:])
        edges = [start] + [row["observed_host_ns"] for row in visible] + [stop]
        report = {"scope": "native_20hz_images_imu_with_synthetic_map_conditional_not_learned_or_flight_qualification",
            "sent_frames": 60, "advancing_visible_poses": len(stamps), "observations": observations,
            "max_observed_gap_including_edges_s": max(b - a for a, b in zip(edges, edges[1:])) / 1e9,
            "duration_s": (stop - start) / 1e9, "position_authority": False}
        (tmp_path / "map_stream_observation.json").write_text(json.dumps(report, indent=2) + "\n")
    finally:
        assert worker.stop()
        if producer:
            producer.join(5.)
            assert not producer.is_alive()


@pytest.mark.parametrize("fault", ["no_opt_in", "epoch", "sequence", "operation", "oversized"])
def test_native_rejects_invalid_control_protocol(source, experiment, tmp_path, fault):
    binary, capture, *_ = source
    worker, *_ = experiment
    exported = tmp_path / "input"; shutil.copytree(capture, exported)
    lines = (worker.output / "owned_stream.jsonl").read_text().splitlines()
    indices = [i for i, line in enumerate(lines) if json.loads(line).get("type") == "map_frame_control"]
    index = indices[1] if fault == "sequence" else indices[0]
    value = json.loads(lines[index])
    if fault == "epoch": value["worker_run_id"] = "0" * 36
    elif fault == "sequence": value["request_id"] = 1
    elif fault == "operation": value["operation"] = "publish"
    elif fault == "oversized": value["padding"] = "x" * 65536
    lines[index] = json.dumps(value)
    (exported / "controlled.jsonl").write_text("\n".join(lines) + "\n")
    out = tmp_path / "output"; out.mkdir()
    args = [str(binary), str(exported / "estimator_config.yaml"), str(exported / "controlled.jsonl"),
        str(out / "states.jsonl"), "--pose-window", "--anchor-control", "1", worker.worker_run_id, str(out / "anchors.jsonl")]
    if fault != "no_opt_in": args += ["--map-frame-control", worker.worker_run_id, str(out / "maps.jsonl")]
    result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    assert result.returncode == 2 and "failed" in result.stderr


def test_native_rejects_aliasing_evidence_paths_before_creating_files(source, tmp_path):
    binary, capture, *_ = source
    sub = tmp_path / "sub"; sub.mkdir()
    result = subprocess.run([str(binary), str(capture / "estimator_config.yaml"), str(capture / "events.jsonl"),
        str(tmp_path / "states.jsonl"), "--pose-window", "--map-frame-control", "12345678-1234-1234-1234-123456789abc",
        str(sub / ".." / "states.jsonl")], capture_output=True, text=True, timeout=30)
    assert result.returncode == 2 and "alias" in result.stderr
    assert not (tmp_path / "states.jsonl").exists()
