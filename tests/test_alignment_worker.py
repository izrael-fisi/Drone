"""Actual spawned GTSAM/provider tests; all measurements and noise synthetic."""

from copy import deepcopy
import hashlib
import json
from pathlib import Path
import struct
import sys
import threading
import time

import numpy as np
import pytest

from vision_nav.estimation.alignment_worker import ProcessAlignmentEngine, LIMIT, _exact, _send, _decode
from vision_nav.estimation.learned_pnp_binding import prepare
from vision_nav.estimation.live_alignment import LiveAlignmentObserver
from vision_nav.estimation.pnp_window_alignment import align
from test_live_alignment_observer import Source, wait_for
from test_learned_pnp_binding import envelope
from test_pnp_window_alignment import fixture


PROVIDER = Path(__file__).parent / "fixtures/alignment_noise.py"


def draft():
    request, _ = fixture(shared=False, vio_cross=False)
    row, binding, _ = envelope(request["fixes"][0]["request"])
    return prepare(row, **binding), request


def sleeping_worker(connection, config):
    time.sleep(60)


def slow_cleanup_worker(connection, config):
    from vision_nav.estimation.alignment_worker import _worker_main
    _worker_main(connection, config)
    time.sleep(.35)


def test_idle_worker_gets_bounded_time_to_clean_up_without_false_failure():
    engine = ProcessAlignmentEngine(PROVIDER)
    engine.worker_entry = slow_cleanup_worker
    try:
        engine.start()
    finally:
        assert engine.stop()
    status = engine.status()
    assert status["returncode"] == 0 and status["error"] is None
    assert status["shutdown_action"] == "graceful_socket_close"
    assert not status["process_alive"]


@pytest.mark.parametrize("threads", [1, 2])
def test_estimator_thread_budget_is_child_owned_and_observable(threads):
    from threadpoolctl import threadpool_info
    before = threadpool_info()
    engine = ProcessAlignmentEngine(PROVIDER, numerical_threads=threads)
    try:
        engine.start()
        status = engine.status()
        assert status["numerical_threads"] == threads
        assert status["numerical_threadpools"]
        assert all(pool["num_threads"] <= threads for pool in status["numerical_threadpools"])
        status["numerical_threadpools"].clear()
        assert engine.status()["numerical_threadpools"]
        assert threadpool_info() == before
    finally:
        assert engine.stop()
    assert threadpool_info() == before


@pytest.mark.parametrize("threads", [0, 5, -1, True, 1.5, "1", None])
def test_invalid_estimator_thread_budget_is_rejected(threads):
    with pytest.raises(ValueError, match="thread budget"):
        ProcessAlignmentEngine(PROVIDER, numerical_threads=threads)


def corrupt_worker(connection, config):
    kind = config["provider_config"]["corruption"]
    _send(connection, {"type": "ready", "worker_id": config["worker_id"], "sources": config["sources"],
        "versions": {}, "wire_format": "wrong" if kind == "codec" else config["wire_format"]}, config["wire_format"])
    if kind == "codec":
        connection.close()
        return
    size = struct.unpack("!Q", _exact(connection, 8))[0]
    request = _decode(_exact(connection, size), config["wire_format"])
    if kind == "oversized":
        connection.sendall(struct.pack("!Q", LIMIT+1))
    elif kind == "hang":
        time.sleep(60)
    elif kind in ("duplicate", "nan", "binary", "other_encoding"):
        import msgpack
        raw = {"duplicate": b"\x82\xa1a\x01\xa1a\x02", "nan": msgpack.packb(float("nan")),
               "binary": msgpack.packb(b"x"), "other_encoding": b'{"type":"result"}'}[kind]
        connection.sendall(struct.pack("!Q", len(raw))+raw)
    else:
        _send(connection, {"type": "result", "worker_id": "wrong" if kind == "worker" else config["worker_id"],
            "request_id": request["request_id"]+1 if kind == "request" else request["request_id"],
            "operation": request["operation"], "request_sha256": "0"*64, "result": {}}, config["wire_format"])
    connection.close()


def test_real_child_bind_noise_and_gtsam_match_direct_replay():
    prepared, request = draft()
    engine = ProcessAlignmentEngine(PROVIDER, operation_timeout_s=5.)
    try:
        engine.start()
        assert engine.status()["pid"] != __import__("os").getpid()
        bound = engine.bind(prepared)
        assert bound["status"] == "conditional_pnp_request"
        request["fixes"] = [{"fix_id": "original-frame", "request": bound["request"]}]
        request.pop("joint_uncertainty")
        noise = engine.joint_noise(request)
        request["joint_uncertainty"] = noise
        actual = engine.align(request)
        assert actual == align(request)
        assert not actual["position_authority"] and actual["navigation_mode"] == "NO_GLOBAL_FIX"
        assert engine.resource_snapshot()["role"] == "research_alignment"
        assert engine.status()["completed_requests"] == 3
        assert "gtsam" in engine.status()["versions"]
    finally:
        assert engine.stop()
    assert not engine.status()["process_alive"] and engine.status()["returncode"] == 0
    with pytest.raises(RuntimeError):
        engine.start()


@pytest.mark.parametrize("stage", ["bind", "noise"])
@pytest.mark.parametrize("fault", ["delay_s", "raise", "exit"])
def test_provider_failure_or_timeout_is_terminal_and_reaped(stage, fault):
    prepared, request = draft()
    engine = ProcessAlignmentEngine(PROVIDER, provider_config={"fault_stage": stage, fault: 60 if fault == "delay_s" else True},
                                    operation_timeout_s=.2)
    try:
        engine.start()
        began = time.monotonic()
        with pytest.raises((RuntimeError, EOFError, TimeoutError)):
            engine.bind(prepared) if stage == "bind" else engine.joint_noise(request)
        assert time.monotonic()-began < 4
        assert engine.status()["state"] == "failed" and not engine.status()["process_alive"]
        with pytest.raises(RuntimeError):
            engine.align(request)
    finally:
        assert engine.stop()


def test_startup_timeout_reaps_worker_before_sensors_start():
    engine = ProcessAlignmentEngine(PROVIDER, startup_timeout_s=.1)
    engine.worker_entry = sleeping_worker
    with pytest.raises(TimeoutError, match="startup"):
        engine.start()
    assert engine.status()["state"] == "failed" and not engine.status()["process_alive"]


@pytest.mark.parametrize("kind", ["worker", "request", "hash", "oversized", "hang", "duplicate", "nan", "binary", "other_encoding"])
def test_protocol_faults_and_solver_timeout_withhold(kind):
    engine = ProcessAlignmentEngine(PROVIDER, provider_config={"corruption": kind}, operation_timeout_s=.2)
    engine.worker_entry = corrupt_worker
    try:
        engine.start()
        with pytest.raises((ValueError, TimeoutError)):
            engine.align({"synthetic": True})
        assert engine.status()["state"] == "failed" and not engine.status()["process_alive"]
    finally:
        assert engine.stop()


def test_codec_mismatch_is_rejected_at_startup():
    engine = ProcessAlignmentEngine(PROVIDER, provider_config={"corruption": "codec"})
    engine.worker_entry = corrupt_worker
    with pytest.raises(ValueError, match="startup provenance"):
        engine.start()
    assert not engine.status()["process_alive"]


def test_missing_compact_dependency_fails_before_process_start(monkeypatch):
    engine = ProcessAlignmentEngine(PROVIDER)
    monkeypatch.setitem(sys.modules, "msgpack", None)
    with pytest.raises(RuntimeError, match="install the estimation extra"):
        engine.start()
    assert engine.status()["pid"] is None and not engine.status()["process_alive"]


@pytest.mark.parametrize("wire_format", [None, True, [], "pickle", "auto"])
def test_unknown_wire_format_has_no_implicit_fallback(wire_format):
    with pytest.raises(ValueError, match="wire format"):
        ProcessAlignmentEngine(PROVIDER, wire_format=wire_format)


def test_provider_changed_after_start_invalidates_worker(tmp_path):
    path = tmp_path / "provider.py"
    path.write_bytes(PROVIDER.read_bytes())
    engine = ProcessAlignmentEngine(path)
    try:
        engine.start()
        path.write_text("raise RuntimeError('changed')\n")
        with pytest.raises(ValueError, match="source changed"):
            engine.bind(draft()[0])
        assert not engine.status()["process_alive"]
    finally:
        assert engine.stop()


def test_parent_never_imports_provider_and_import_failure_is_closed(tmp_path):
    path = tmp_path / "provider.py"
    path.write_text("raise RuntimeError('provider import test')\n")
    engine = ProcessAlignmentEngine(path)
    assert "vision_nav_explicit_research_noise" not in sys.modules
    with pytest.raises(RuntimeError, match="provider import test"):
        engine.start()
    assert engine.status()["error"] and not engine.status()["process_alive"]


def test_single_inflight_and_cancel_interrupt_hung_provider():
    engine = ProcessAlignmentEngine(PROVIDER, provider_config={"fault_stage": "bind", "delay_s": 60}, operation_timeout_s=20.)
    errors = []
    prepared, _ = draft()
    def run():
        try:
            engine.bind(prepared)
        except Exception as exc:
            errors.append(exc)
    engine.start()
    worker = threading.Thread(target=run)
    worker.start()
    try:
        wait_for(lambda: engine.status()["state"] == "processing")
        with pytest.raises(RuntimeError, match="one estimator"):
            engine.align({})
        engine.cancel()
        worker.join(4)
        assert not worker.is_alive() and errors
        assert not engine.status()["process_alive"]
    finally:
        engine.stop()
        worker.join(4)


def test_observer_real_process_fuses_and_records_exact_replay():
    request, _ = fixture(shared=False, vio_cross=False)
    source = Source(request["config"])
    engine = ProcessAlignmentEngine(PROVIDER, operation_timeout_s=5.)
    observer = LiveAlignmentObserver(source, request["config"], process_engine=engine,
                                     max_snapshot_age_ns=5_000_000_000)
    row, binding, _ = envelope(request["fixes"][0]["request"], worker_run_id=source.native.worker_run_id)
    try:
        observer.start(binding["provenance"])
        source.state, source.native_state = "collecting", "running"
        source.push(request["native_record"])
        wait_for(lambda: observer.status()["counts"].get("native_snapshots") == 1)
        result = observer.observe({"tickets": [binding["frame_ticket"]]}, row)
        assert result["status"] == "research_current_alignment", result
        replay = align(result["replay_inputs"][-1])
        assert replay["alignment"] == result["current_estimate"]
        operations = result["estimator_operations"]
        assert len(operations) == len(result["replay_inputs"])
        assert all(item["operation"] == "align_with_noise" for item in operations)
        assert all(item["transport_profile"]["response_bytes"] > 0 for item in operations)
        assert observer.status()["provider_hard_timeout"]
    finally:
        assert observer.stop()
    assert not engine.status()["process_alive"]


def test_sensor_failure_cancels_process_and_vetoes_late_result():
    request, _ = fixture(shared=False, vio_cross=False)
    source = Source(request["config"])
    engine = ProcessAlignmentEngine(PROVIDER, provider_config={"fault_stage": "bind", "delay_s": 60}, operation_timeout_s=20.)
    observer = LiveAlignmentObserver(source, request["config"], process_engine=engine)
    row, binding, _ = envelope(request["fixes"][0]["request"], worker_run_id=source.native.worker_run_id)
    results = []
    observer.start(binding["provenance"])
    source.state, source.native_state = "collecting", "running"
    source.push(request["native_record"])
    worker = threading.Thread(target=lambda: results.append(observer.observe({"tickets": [binding["frame_ticket"]]}, row)))
    worker.start()
    try:
        wait_for(lambda: engine.status()["state"] == "processing")
        source.state = "failed"
        worker.join(4)
        assert not worker.is_alive() and results[0]["current_estimate"] is None
        assert observer.status()["error"] and not engine.status()["process_alive"]
    finally:
        observer.stop()
        worker.join(4)


@pytest.mark.parametrize("kwargs", [{"startup_timeout_s": 0}, {"operation_timeout_s": float("nan")},
                                    {"operation_timeout_s": 31}, {"provider_config": []}])
def test_rejects_invalid_config_before_start(kwargs):
    with pytest.raises(ValueError):
        ProcessAlignmentEngine(PROVIDER, **kwargs)
