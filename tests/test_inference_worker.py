"""Spawned model execution, bounded IPC and injected worker failures."""

from dataclasses import replace
import hashlib
import json
import os
import signal
import struct
import threading
import time

import numpy as np
import pytest

from vision_nav.neural.inference_worker import (MAX_RESPONSE, ProcessWindowInference,
    pack_window, unpack_window, _json, _recv_exact)
from vision_nav.neural.live_sensor_inference import run_live_learned_session, WindowModelInference
from vision_nav.vio.live_worker import LiveFrameTicket
from test_live_learned_session import rig_factory
from test_map_conditioned_replay import artifacts
from test_replay_retrieval import retrieval_artifacts
from test_prepare_vio_capture import sources


def _ready(connection, config):
    # This is only a protocol fault fixture, never an actual inference result.
    data = _json({"type": "ready", "worker_id": config["worker_id"], "provenance": {
        "files_sha256": config["expected_files"], "worker_run_id": config["binding"]["worker_run_id"]}})
    connection.sendall(struct.pack("!Q", len(data))+data)


def startup_hang(connection, config):
    time.sleep(60)


def request_hang(connection, config):
    _ready(connection, config)
    size = struct.unpack("!Q", _recv_exact(connection, 8))[0]
    _recv_exact(connection, size)
    # Verify escalation past TERM on POSIX, not just cooperative Python cleanup.
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, signal.SIG_IGN)
    time.sleep(60)


def partial_response(connection, config):
    _ready(connection, config)
    size = struct.unpack("!Q", _recv_exact(connection, 8))[0]
    _recv_exact(connection, size)
    connection.sendall(struct.pack("!Q", 1000)+b'{"type":')
    time.sleep(60)


def response_too_large(connection, config):
    _ready(connection, config)
    size = struct.unpack("!Q", _recv_exact(connection, 8))[0]
    _recv_exact(connection, size)
    connection.sendall(struct.pack("!Q", MAX_RESPONSE+1))
    time.sleep(60)


def response_wrong_identity(connection, config):
    _ready(connection, config)
    size = struct.unpack("!Q", _recv_exact(connection, 8))[0]
    _recv_exact(connection, size)
    data = _json({"type": "result", "worker_id": "other-worker"})
    connection.sendall(struct.pack("!Q", len(data))+data)
    time.sleep(60)


def input_not_read(connection, config):
    _ready(connection, config)
    time.sleep(60)


def startup_crash(connection, config):
    os._exit(7)


def _protocol_result(connection, config, *, wrong_request=False):
    _ready(connection, config)
    size = struct.unpack("!Q", _recv_exact(connection, 8))[0]
    payload = _recv_exact(connection, size)
    request_id, item = unpack_window(payload, config["max_window_bytes"])
    anchor = item["tickets"][-1]
    # Synthetic protocol-only rejection, not fabricated model evidence.
    from vision_nav.neural.live_sensor_inference import SCHEMA
    row = {"schema_version": SCHEMA, "worker_run_id": item["worker_run_id"],
        "state": "REJECTED", "reason": "protocol_fault_fixture", "position_authority": False,
        "navigation_mode": "NO_GLOBAL_FIX", "px4_external_vision": "disabled", "global_fusion_connected": False,
        "sequence": anchor.sequence, "anchor_vio_timestamp_ns": anchor.timestamp_ns,
        "anchor_pixel_sha256": anchor.pixel_sha256}
    data = _json({"type": "result", "worker_id": config["worker_id"],
        "request_id": request_id+int(wrong_request), "request_sha256": hashlib.sha256(payload).hexdigest(), "row": row})
    connection.sendall(struct.pack("!Q", len(data))+data)


def wrong_request_reply(connection, config):
    _protocol_result(connection, config, wrong_request=True)
    time.sleep(60)


def crash_after_reply(connection, config):
    _protocol_result(connection, config)
    os._exit(9)


def _item(pixels=None):
    if pixels is None:
        pixels = np.arange(48, dtype=np.uint8).reshape(4, 4, 3)
    pixels = np.frombuffer(pixels.tobytes(), dtype=np.uint8).reshape(pixels.shape)
    hashed = hashlib.sha256(pixels.tobytes()).hexdigest()
    tickets = tuple(LiveFrameTicket("native-epoch", n, (n+1)*1_000_000, hashed, "a"*64, pixels) for n in range(2))
    return {"worker_run_id": "native-epoch", "tickets": tickets, "contexts": [{}, {}]}


def test_ipc_preserves_pixel_identity_without_pickle_or_files():
    item = _item()
    request, decoded = unpack_window(pack_window(item, 4, 4096), 4096)
    assert request == 4
    for before, after in zip(item["tickets"], decoded["tickets"]):
        assert before.sequence == after.sequence and before.timestamp_ns == after.timestamp_ns
        assert before.pixel_sha256 == after.pixel_sha256
        np.testing.assert_array_equal(before.pixels, after.pixels)
        with pytest.raises(ValueError):
            after.pixels.setflags(write=True)


@pytest.mark.parametrize("fault", ["budget", "mutable", "hash", "extra", "truncated", "length", "pixel_mutated"])
def test_ipc_rejects_invalid_input(fault):
    item = _item()
    if fault == "budget":
        with pytest.raises(ValueError, match="budget"):
            pack_window(item, 1, 60)
        return
    if fault in {"mutable", "hash"}:
        change = {"pixels": item["tickets"][0].pixels.copy()} if fault == "mutable" else {"pixel_sha256": "0"*64}
        item["tickets"] = (replace(item["tickets"][0], **change), item["tickets"][1])
        with pytest.raises(ValueError):
            pack_window(item, 1, 4096)
        return
    payload = pack_window(item, 1, 4096)
    if fault == "extra":
        payload += b'other'
    elif fault == "truncated":
        payload = payload[:-1]
    elif fault == "length":
        payload = struct.pack("!I", 999999)+payload[4:]
    else:
        payload = payload[:-1]+bytes([payload[-1] ^ 1])
    with pytest.raises(ValueError):
        unpack_window(payload, 4096)


def test_actual_spawned_onnx_runs_without_loading_models_in_parent(rig_factory, tmp_path, monkeypatch):
    session, worker, rig = rig_factory(frames=40, isolated=True)
    def prohibited(*args, **kwargs):
        raise AssertionError("Models must initialize only in the spawned process")
    monkeypatch.setattr(WindowModelInference, "__init__", prohibited)
    report = run_live_learned_session(session, worker, tmp_path / "learned")
    assert report["status"] == "completed" and report["model_windows"] > 0, report
    status = report["inference_worker"]
    assert status["pid"] != os.getpid() and not status["process_alive"]
    assert status["returncode"] == 0 and status["state"] == "stopped"
    assert report["source_session"]["native"]["completed_batch_count"] == 40
    assert rig.camera_count == rig.link_count == 1
    assert rig.camera_closed and rig.link_closed
    assert report["provenance"]["execution"]["mode"] == "spawned_model_process"


def test_full_learned_stack_and_native_use_owned_separate_processes(rig_factory, tmp_path):
    from vision_nav.estimation.correlated_window import payload_digest
    session, worker, _ = rig_factory(frames=160, isolated=True, actual_models=True)
    report = run_live_learned_session(session, worker, tmp_path / "learned")
    assert report["status"] == "completed" and report["model_windows"] > 2, report
    assert report["source_session"]["native"]["completed_batch_count"] == 160
    assert not report["inference_worker"]["process_alive"]
    rows = [json.loads(line) for line in (tmp_path / "learned/trace.jsonl").read_text().splitlines()]
    assert rows[-1]["sequence"] == 159
    assert all(row["inference_worker"]["pid"] != row["native_after"]["native_pid"] for row in rows)
    assert all(not row["position_authority"] for row in rows)
    assert any(row.get("local_verification", {}).get("attempts") for row in rows)
    provenance = {k: v for k, v in report["provenance"].items() if k != "execution"}
    assert all(row["provenance_sha256"] == payload_digest(provenance) for row in rows)
    for row in rows:
        for attempt in row.get("local_verification", {}).get("attempts", []):
            if attempt["status"] == "verified":
                assert len(attempt["matched_query_feature_indices"]) == attempt["match_count"]
                assert len(attempt["matched_map_feature_indices"]) == attempt["match_count"]
                assert len(set(attempt["matched_map_feature_indices"])) == attempt["match_count"]


@pytest.mark.parametrize("entry", [startup_hang, startup_crash])
def test_startup_failure_reaps_worker_before_any_sensor_opens(rig_factory, tmp_path, entry):
    session, worker, rig = rig_factory(isolated=True, worker_options={"startup_timeout_s": .2 if entry == startup_hang else 5.})
    worker.worker_entry = entry
    began = time.monotonic()
    report = run_live_learned_session(session, worker, tmp_path / "learned")
    assert time.monotonic()-began < 5
    assert report["status"] == "incomplete" and report["inference_worker"]["error"]
    assert not report["inference_worker"]["process_alive"]
    assert rig.camera_count == rig.link_count == 0
    assert session.native._process is None
    if entry == startup_crash:
        assert report["inference_worker"]["returncode"] == 7
        assert "socket_closed" in report["inference_worker"]["error"]
    else:
        assert "startup_timeout" in report["inference_worker"]["error"]


@pytest.mark.parametrize("entry, reason", [(request_hang, "timeout"), (partial_response, "timeout"),
    (response_too_large, "length"), (response_wrong_identity, "identity"), (wrong_request_reply, "submitted shadow window")])
def test_worker_fault_never_stops_ordered_vio_or_restarts_inference(rig_factory, tmp_path, entry, reason):
    session, worker, rig = rig_factory(frames=40, isolated=True, worker_options={"inference_timeout_s": .15})
    worker.worker_entry = entry
    report = run_live_learned_session(session, worker, tmp_path / "learned")
    assert report["status"] == "incomplete" and report["model_windows"] == 0, report
    assert reason in report["inference_worker"]["error"]
    assert report["source_session"]["native"]["completed_batch_count"] == 40
    assert report["inference_worker"]["submitted_windows"] == 1
    assert not report["inference_worker"]["process_alive"]
    assert rig.camera_closed and rig.link_closed


def test_crash_after_final_response_is_not_reported_as_clean_shutdown(rig_factory):
    session, worker, _ = rig_factory(frames=8, isolated=True)
    worker.worker_entry = crash_after_reply
    worker.start()
    session.start()
    try:
        assert session.join(10)
        row = worker.process(session.take_latest_window())
        assert row["reason"] == "protocol_fault_fixture"
        assert worker.stop()
        assert worker.status()["returncode"] == 9
        assert worker.status()["state"] == "failed"
        assert "shutdown_exit_failed" in worker.status()["error"]
    finally:
        worker.stop()
        session.stop()


def test_cancellation_interrupts_inference_and_reaps_uncooperative_child(rig_factory):
    session, worker, _ = rig_factory(frames=8, isolated=True)
    worker.worker_entry = request_hang
    worker.start()
    session.start()
    try:
        assert session.join(10)
        item = session.take_latest_window()
        timer = threading.Timer(.1, worker.cancel)
        timer.start()
        began = time.monotonic()
        row = worker.process(item)
        timer.join()
        assert time.monotonic()-began < 3
        assert row["state"] == "REJECTED" and "cancelled" in row["reason"]
        assert not worker.status()["process_alive"]
    finally:
        worker.stop()
        session.stop()


def test_socket_backpressure_is_deadline_bounded(rig_factory):
    session, worker, _ = rig_factory(isolated=True, worker_options={"inference_timeout_s": .1})
    worker.worker_entry = input_not_read
    worker.start()
    try:
        # Oversized for the synthetic camera, but fits the IPC cap. The child
        # never reads; this specifically exercises a blocked socket sender.
        item = _item(np.zeros((1024, 1024, 3), dtype=np.uint8))
        began = time.monotonic()
        row = worker.process(item)
        assert time.monotonic()-began < 3
        assert row["state"] == "REJECTED" and "timeout" in row["reason"]
        assert not worker.status()["process_alive"]
    finally:
        worker.stop()


def test_cancel_before_start_cannot_spawn_or_restart(rig_factory):
    _, worker, _ = rig_factory(isolated=True)
    worker.cancel()
    with pytest.raises(RuntimeError, match="restart"):
        worker.start()
    assert worker.status()["pid"] is None


def test_neural_package_disables_ort_telemetry_before_import():
    import subprocess
    import sys
    result = subprocess.run([sys.executable, "-c", "import os,sys; import vision_nav.neural; "
        "assert os.environ['ORT_DISABLE_TELEMETRY'] == '1'; assert 'onnxruntime' not in sys.modules"],
        check=False, capture_output=True, text=True, env={**os.environ, "ORT_DISABLE_TELEMETRY": "0"})
    assert result.returncode == 0, result.stderr
