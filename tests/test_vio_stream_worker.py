"""Actual native streaming execution and bounded process ownership checks."""

import json
import os
from pathlib import Path
import threading
import time

import pytest

from vision_nav.vio.openvins_replay import make_synthetic, run as offline_run
from vision_nav.vio.stream_replay import sensor_batches, run
from vision_nav.vio.stream_worker import NativeVioStreamWorker


@pytest.fixture
def inputs(tmp_path):
    root = Path(__file__).resolve().parents[1]
    location = os.environ.get("VISION_NAV_OPENVINS_BINARY")
    if not location or not (root / "research/vendor/open_vins/.git").exists():
        pytest.skip("Pinned native checkout and VISION_NAV_OPENVINS_BINARY required")
    binary = Path(location).resolve()
    assert binary.is_file()
    capture = tmp_path / "capture"
    make_synthetic(root / "research/vendor/open_vins", capture)
    return binary, capture


def wait_for(predicate, timeout=5.):
    deadline = time.monotonic() + timeout
    while not predicate():
        assert time.monotonic() < deadline, "Condition did not become true"
        time.sleep(.005)


def test_streamed_native_states_match_file_replay_exactly(inputs, tmp_path):
    binary, capture = inputs
    original = tmp_path / "file-replay"
    streamed = tmp_path / "streamed"
    assert offline_run(binary, capture, original, pose_window=True)["complete"]
    report = run(binary, capture, streamed)
    assert report["complete"], report
    assert report["states"]["LOCAL_ESTIMATE"] > 40
    assert report["worker"]["submitted_batch_count"] == report["worker"]["completed_batch_count"] == 160
    assert report["worker"]["thread_alive"] is False
    assert report["worker"]["native_returncode"] == 0
    assert (original / "local_states.jsonl").read_bytes() == (streamed / "local_states.jsonl").read_bytes()
    assert report["position_authority"] is False
    assert report["timing_scope"] == "as_fast_as_processed_replay_not_live_sensor_latency"


def test_native_replay_metrics_record_actual_child_separately(inputs, tmp_path):
    pytest.importorskip("psutil")
    from vision_nav.neural.runtime_load_report import summarize_runtime_load

    binary, capture = inputs
    report = run(binary, capture, tmp_path/"measured", record_runtime_metrics=True,
                 metrics_interval_s=.05, metrics_max_samples=1000)
    assert report["complete"], report
    measurements = summarize_runtime_load(Path(report["runtime_metrics"]["path"]))
    child = measurements["native_vio_process"]
    assert child["requested"]
    assert child["identity"]["worker_run_id"] == report["worker"]["worker_run_id"]
    assert child["resource_distributions"]["cpu_percent_one_core_scale"]["max"] > 0
    assert child["resource_distributions"]["rss_bytes"]["max"] > 0
    assert measurements["evidence_status"] == "incomplete"
    assert "not_all_learned_stages_observed" in measurements["evidence_gaps"]
    assert not report["runtime_metrics"]["thread_alive"]
    assert report["runtime_metrics"]["native_vio_available_sample_count"] >= 2


def test_requested_metrics_cap_cannot_claim_complete_replay_measurement(inputs, tmp_path):
    binary, capture = inputs
    report = run(binary, capture, tmp_path/"capped-metrics", record_runtime_metrics=True,
                 metrics_interval_s=.01, metrics_max_samples=1)
    assert report["worker"]["status"] == "complete"
    assert not report["complete"]
    assert report["runtime_metrics"]["status"] == "sample_limit_reached"
    assert "metrics" in report["execution_error"]


def test_vio_advances_while_calling_model_is_waiting(inputs, tmp_path):
    binary, capture = inputs
    worker = NativeVioStreamWorker(binary, capture, tmp_path / "worker")
    done, errors = threading.Event(), []
    def produce():
        try:
            for batch in sensor_batches(capture / "events.jsonl"):
                while not worker.submit(batch):
                    time.sleep(.005)
            worker.finish()
        except Exception as exc:
            errors.append(exc)
        finally:
            done.set()
    worker.start()
    producer = threading.Thread(target=produce)
    producer.start()
    try:
        wait_for(lambda: worker.status()["completed_batch_count"] >= 10)
        first = worker.take_latest()
        first_record = json.loads(json.dumps(first["record"]))
        # No VIO processing on this calling thread during a blocked model stage.
        assert done.wait(10)
        assert worker.join(10)
        assert not errors
        last = worker.take_latest()
        assert last["worker_run_id"] == first["worker_run_id"] == worker.status()["worker_run_id"]
        assert last["completed_batch_count"] == 160
        assert last["record"]["timestamp_ns"] > first_record["timestamp_ns"]
        assert first["record"] == first_record
        assert worker.take_latest() is None
        assert worker.status()["status"] == "complete"
    finally:
        worker.stop()
        producer.join(3)
    assert not producer.is_alive()


def test_native_worker_snapshot_connects_to_delayed_pnp_alignment(inputs, tmp_path):
    from vision_nav.estimation.delayed_alignment import DelayedPnPAlignment
    from test_delayed_alignment import dispatch, complete, noise_for
    from test_pnp_window_alignment import fixture as map_fixture

    binary, capture = inputs
    worker = NativeVioStreamWorker(binary, capture, tmp_path / "delayed-pnp")
    worker.start()
    try:
        for batch in sensor_batches(capture / "events.jsonl"):
            wait_for(lambda: worker.submit(batch))
        worker.finish()
        assert worker.join(10.)
        snapshot = worker.take_latest()
        assert snapshot["record"]["local_state"] == "LOCAL_ESTIMATE"
        # Actual native image/IMU posterior; synthetic map observations/noise.
        # No claim that these PnP pixels were extracted from the native images.
        request, expected = map_fixture(snapshot["record"], vio_cross=False)
        coordinator = DelayedPnPAlignment(request["config"], worker.worker_run_id)
        assert coordinator.update_snapshot(snapshot)["status"] == "updated"
        tickets = dispatch(coordinator, request)
        complete(coordinator, tickets, request)
        draft = coordinator.prepare()
        result = coordinator.solve(draft["draft_id"], noise_for(draft, request))
        assert result["status"] == "research_alignment", result
        assert result["result"]["alignment"]["pose_timestamp_ns"] == snapshot["record"]["timestamp_ns"]
        assert not result["position_authority"]
    finally:
        assert worker.stop()


def test_full_queue_requires_retry_without_consuming_batch(inputs, tmp_path, monkeypatch):
    binary, capture = inputs
    worker = NativeVioStreamWorker(binary, capture, tmp_path / "bounded", capacity=2)
    entered, release = threading.Event(), threading.Event()
    original = worker._read_result
    def hold(reader, deadline, stamp):
        entered.set()
        assert release.wait(3)
        return original(reader, deadline, stamp)
    monkeypatch.setattr(worker, "_read_result", hold)
    batches = list(sensor_batches(capture / "events.jsonl"))[:4]
    worker.start()
    try:
        assert worker.submit(batches[0])
        assert entered.wait(3)
        assert worker.submit(batches[1]) and worker.submit(batches[2])
        assert worker.submit(batches[3]) is False
        assert worker.status()["submitted_batch_count"] == 3
        assert worker.status()["pending_batch_count"] == 2
        release.set()
        wait_for(lambda: worker.status()["completed_batch_count"] >= 3)
        assert worker.submit(batches[3])
        worker.finish()
        assert worker.join(5)
        assert worker.status()["completed_batch_count"] == 4
        assert worker.status()["backpressure_count"] == 1
        assert worker.status()["status"] == "no_local_estimate"
    finally:
        release.set()
        worker.stop()


def test_current_alignment_refresh_tracks_new_native_posterior(inputs, tmp_path):
    import copy
    import numpy as np
    from vision_nav.estimation.delayed_alignment import DelayedPnPAlignment
    from test_delayed_alignment import dispatch, complete, noise_for
    from test_pnp_window_alignment import fixture as map_fixture

    binary, capture = inputs
    worker = NativeVioStreamWorker(binary, capture, tmp_path / "current-refresh")
    batches = list(sensor_batches(capture / "events.jsonl"))
    worker.start()
    try:
        for batch in batches[:-2]:
            wait_for(lambda: worker.submit(batch))
        wait_for(lambda: worker.status()["completed_batch_count"] == len(batches)-2)
        initial = worker.take_latest()
        assert initial["record"]["local_state"] == "LOCAL_ESTIMATE"
        # Generate synthetic map fixes at the last two old clone times, which
        # remain retained after two more real native image/IMU updates.
        anchors = copy.deepcopy(initial["record"])
        window = anchors["pose_window"]
        window["local_T_imu"] = window["local_T_imu"][-2:]
        window["pose_timestamps_ns"] = window["pose_timestamps_ns"][-2:]
        window["joint_covariance_native"] = np.asarray(window["joint_covariance_native"])[-12:, -12:].tolist()
        request, _ = map_fixture(anchors, vio_cross=False)
        coordinator = DelayedPnPAlignment(request["config"], worker.worker_run_id)
        assert coordinator.update_snapshot(initial)["status"] == "updated"
        complete(coordinator, dispatch(coordinator, request), request)
        used = []

        def explicit_synthetic_noise(req):
            used.append(req["native_record"]["timestamp_ns"])
            if len(used) == 1:
                for batch in batches[-2:]:
                    wait_for(lambda: worker.submit(batch))
                worker.finish()
                assert worker.join(10)
                assert coordinator.update_snapshot(worker.take_latest())["status"] == "updated"
            return noise_for({"request": req}, request)

        result = coordinator.refresh_current(explicit_synthetic_noise, max_snapshot_age_ns=10_000_000_000)
        assert result["status"] == "research_current_alignment", result
        assert len(used) == 2 and used[1] > used[0]
        assert result["current_estimate"]["pose_timestamp_ns"] == used[1]
        assert result["current_estimate"]["pose_timestamp_ns"] > request["fixes"][-1]["request"]["context"]["timestamp_ns"]
        assert worker.status()["completed_batch_count"] == 160
        assert not result["position_authority"] and not result["covariance_calibrated"]
        evidence = {"scope": "native_vio_with_synthetic_map_fixes_and_noise_not_aircraft_accuracy",
                    "refresh": result, "native_worker": worker.status()}
        (tmp_path / "current-refresh-result.json").write_text(json.dumps(evidence, allow_nan=False))
    finally:
        assert worker.stop()


def test_isolated_estimator_refreshes_while_native_vio_advances(inputs, tmp_path):
    import copy
    import numpy as np
    from vision_nav.estimation.alignment_worker import ProcessAlignmentEngine
    from vision_nav.estimation.delayed_alignment import DelayedPnPAlignment
    from test_delayed_alignment import dispatch, complete
    from test_pnp_window_alignment import fixture as map_fixture

    binary, capture = inputs
    native = NativeVioStreamWorker(binary, capture, tmp_path / "isolated-native")
    engine = ProcessAlignmentEngine(Path(__file__).parent / "fixtures/alignment_noise.py",
        provider_config={"fault_stage": "noise", "delay_s": .5}, operation_timeout_s=5.)
    batches = list(sensor_batches(capture / "events.jsonl"))
    producer, errors, advanced_during_noise = None, [], []
    try:
        engine.start()
        native.start()
        for batch in batches[:-2]:
            wait_for(lambda: native.submit(batch))
        wait_for(lambda: native.status()["completed_batch_count"] == 158)
        initial = native.take_latest()
        anchors = copy.deepcopy(initial["record"])
        window = anchors["pose_window"]
        window["local_T_imu"] = window["local_T_imu"][-2:]
        window["pose_timestamps_ns"] = window["pose_timestamps_ns"][-2:]
        window["joint_covariance_native"] = np.asarray(window["joint_covariance_native"])[-12:, -12:].tolist()
        request, _ = map_fixture(anchors, shared=False, vio_cross=False)
        coordinator = DelayedPnPAlignment(request["config"], native.worker_run_id, alignment_solver=engine.align)
        assert coordinator.update_snapshot(initial)["status"] == "updated"
        complete(coordinator, dispatch(coordinator, request), request)

        def advance_native():
            try:
                wait_for(lambda: engine.status()["state"] == "processing")
                for batch in batches[-2:]:
                    wait_for(lambda: native.submit(batch))
                native.finish()
                assert native.join(5)
                advanced_during_noise.append(engine.status()["state"] == "processing")
                assert coordinator.update_snapshot(native.take_latest())["status"] == "updated"
            except Exception as exc:
                errors.append(exc)

        calls = []
        def noise(req):
            nonlocal producer
            calls.append(req["native_record"]["timestamp_ns"])
            if len(calls) == 1:
                producer = threading.Thread(target=advance_native)
                producer.start()
            result = engine.joint_noise(req)
            if producer is not None:
                producer.join(5)
                assert not producer.is_alive() and not errors
            return result

        result = coordinator.refresh_current(noise, max_snapshot_age_ns=10_000_000_000)
        assert result["status"] == "research_current_alignment", result
        assert advanced_during_noise == [True] and len(calls) == 2
        assert calls[1] > calls[0] and result["current_estimate"]["pose_timestamp_ns"] == calls[1]
        assert native.status()["completed_batch_count"] == 160
        assert not result["position_authority"]
        (tmp_path / "isolated-alignment.json").write_text(json.dumps({
            "scope": "actual_native_and_gtsam_processes_synthetic_map_noise_not_field_accuracy",
            "refresh": result, "estimator": engine.status(), "native": native.status()}, allow_nan=False))
    finally:
        assert engine.stop()
        assert native.stop()
        if producer:
            producer.join(5)


def test_continuous_observer_advances_with_actual_native_without_new_map_fix(inputs, tmp_path):
    from copy import deepcopy
    from types import SimpleNamespace
    import numpy as np
    from vision_nav.estimation.alignment_worker import ProcessAlignmentEngine
    from vision_nav.estimation.live_alignment import LiveAlignmentObserver
    from test_learned_pnp_binding import envelope
    from test_pnp_window_alignment import fixture as map_fixture

    binary, capture = inputs
    native = NativeVioStreamWorker(binary, capture, tmp_path / "continuous-native")
    config = map_fixture(shared=False, vio_cross=False)[0]["config"]
    # Sealed replay harness supplies metadata; pixels/map fixes/noise below are
    # synthetic, while every VIO posterior comes from the real native process.
    source = SimpleNamespace(state="not_started")
    source.status = lambda: {"state": source.state}
    source.native = SimpleNamespace(worker_run_id=native.worker_run_id, status=native.status,
        take_latest=native.take_latest, _profile={"camera_calibration_id": config["calibration_id"]},
        _camera={"T_imu_cam": config["imu_T_camera"]})
    engine = ProcessAlignmentEngine(Path(__file__).parent / "fixtures/alignment_noise.py", operation_timeout_s=5.)
    observer = LiveAlignmentObserver(source, config, process_engine=engine,
        continuous_refresh_interval_s=.02, max_snapshot_age_ns=10_000_000_000)
    provenance = {"worker_run_id": native.worker_run_id, "map_release_id": config["map_release_id"],
                  "calibration_id": config["calibration_id"], "model_sha256": "a"*64}
    try:
        observer.start(provenance, evidence_path=tmp_path / "continuous-alignment.jsonl")
        source.state = "collecting"
        native.start()
        batches = list(sensor_batches(capture / "events.jsonl"))
        for batch in batches[:-2]:
            wait_for(lambda: native.submit(batch))
        wait_for(lambda: native.status()["completed_batch_count"] == 158)
        anchors = json.loads((native.output / "local_states.jsonl").read_text().splitlines()[-1])
        first_time = anchors["timestamp_ns"]
        wait_for(lambda: observer.coordinator.status()["posterior_timestamp_ns"] == first_time)
        window = anchors["pose_window"]
        window["local_T_imu"] = window["local_T_imu"][-2:]
        window["pose_timestamps_ns"] = window["pose_timestamps_ns"][-2:]
        window["joint_covariance_native"] = np.asarray(window["joint_covariance_native"])[-12:, -12:].tolist()
        request, _ = map_fixture(anchors, shared=False, vio_cross=False)
        row, binding, _ = envelope(request["fixes"][0]["request"], worker_run_id=native.worker_run_id)
        assert binding["provenance"] == provenance
        assert observer.observe({"tickets": [binding["frame_ticket"]]}, row)["status"] == "queued_for_alignment"
        def estimate():
            return observer.status()["continuous_refresh"]["latest_snapshot_estimate"]
        wait_for(lambda: estimate() is not None)
        for batch in batches[-2:]:
            wait_for(lambda: native.submit(batch))
        native.finish()
        assert native.join(10)
        final_time = batches[-1][-1]["timestamp_ns"]
        wait_for(lambda: estimate() is not None and estimate()["pose_timestamp_ns"] == final_time)
        assert final_time > first_time
        source.state = "complete"
        assert observer.finish() and observer.stop()
        events = [json.loads(line) for line in (tmp_path / "continuous-alignment.jsonl").read_text().splitlines()]
        aligned = [e for e in events if e["current_estimate"] is not None]
        assert aligned[0]["current_estimate"]["pose_timestamp_ns"] == first_time
        assert aligned[-1]["current_estimate"]["pose_timestamp_ns"] == final_time
        assert len({e["map_evidence_timestamp_ns"] for e in aligned}) == 1
        assert all(len(e["replay_inputs"][-1]["fixes"]) == 1 and not e["position_authority"] for e in aligned)
        assert native.status()["completed_batch_count"] == 160
        (tmp_path / "continuous-native-summary.json").write_text(json.dumps({
            "scope": "actual_native_and_gtsam_processes_synthetic_pnp_pixels_and_noise_not_field_accuracy",
            "observer": observer.status(), "native": native.status()}, allow_nan=False))
    finally:
        assert observer.stop()
        assert native.stop()


def test_timeout_reaps_only_owned_native_process(inputs, tmp_path, monkeypatch):
    binary, capture = inputs
    worker = NativeVioStreamWorker(binary, capture, tmp_path / "timeout")
    original = worker._write_batch
    def delayed(payload, deadline):
        original(payload, deadline)
        time.sleep(.04)
    monkeypatch.setattr(worker, "_write_batch", delayed)
    worker.start()
    try:
        wait_for(lambda: worker.status()["status"] == "waiting_for_sensors")
        worker.timeout = .01
        assert worker.submit(next(sensor_batches(capture / "events.jsonl")))
        assert worker.join(5)
        status = worker.status()
        assert status["status"] == "failed" and "deadline" in status["error"]
        assert worker._process.poll() is not None
        assert status["completed_batch_count"] == 0
        assert status["position_authority"] is False
    finally:
        worker.stop()


def test_stop_interrupts_native_waiting_for_stdin(inputs, tmp_path):
    binary, capture = inputs
    worker = NativeVioStreamWorker(binary, capture, tmp_path / "idle")
    worker.start()
    try:
        wait_for(lambda: worker.status()["status"] == "waiting_for_sensors")
        assert worker.stop()
        assert worker.status()["status"] == "stopped"
        assert worker._process.poll() is not None
        with pytest.raises(RuntimeError):
            worker.submit(next(sensor_batches(capture / "events.jsonl")))
    finally:
        worker.stop()


def test_batch_parser_rejects_unknown_trailing_and_oversized_events(tmp_path):
    path = tmp_path / "events"
    for data in ([{"type": "gps"}], [{"type": "imu"}], [{"type": "imu", "junk": "x" * 65536}]):
        path.write_text("".join(json.dumps(e) + "\n" for e in data))
        with pytest.raises(ValueError):
            list(sensor_batches(path))
