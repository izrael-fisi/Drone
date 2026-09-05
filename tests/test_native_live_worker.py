"""Arriving camera/IMU adapter tests; synthetic clocks, actual native estimator."""

from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import os
from pathlib import Path
import threading
import time

import cv2
import numpy as np
import pytest

from vision_nav.camera_ingress import CapturedFrame
from vision_nav.capture_timing import fit_px4_clock
from vision_nav.vio.live_worker import NativeVioLiveWorker
from vision_nav.vio.openvins_replay import digest, run
from vision_nav.vio.prepare_capture import prepare_capture
from vision_nav.vio.stream_replay import sensor_batches
from test_prepare_vio_capture import sources, CHECKOUT
from test_vio_stream_worker import wait_for


@pytest.fixture
def arrival_inputs(sources, tmp_path):
    location = os.environ.get("VISION_NAV_OPENVINS_BINARY")
    if not location:
        pytest.skip("Pinned native VIO binary required")
    session, profile, rendered = sources
    manifest = json.loads((session / "manifest.json").read_text())
    fit, audit = fit_px4_clock(manifest["mavlink"]["timesync"]["during_capture"]["exchanges"])
    assert fit is not None, audit
    prepared = tmp_path / "comparison-input"
    prepare_capture(CHECKOUT, session, profile, prepared)
    samples = {row["sample"]["vehicle_timestamp_us"]*1000: row["sample"] for row in
               map(json.loads, (session / "telemetry.jsonl").read_text().splitlines())
               if row["sample"]["message_type"] == "HIGHRES_IMU"}
    arrivals = []
    for index, batch in enumerate(sensor_batches(prepared / "events.jsonl")):
        source = manifest["frames"][index]
        path = session / source["path"]
        frame = CapturedFrame(path=path, backend="picamera2", bgr=cv2.imread(str(path), cv2.IMREAD_UNCHANGED),
                              **source["camera_metadata"])
        arrivals.append((frame, [samples[e["timestamp_ns"]] for e in batch[:-1]]))
    return Path(location), profile, prepared, fit, arrivals


def make_worker(arrival_inputs, tmp_path, **kwargs):
    binary, profile, _, _, _ = arrival_inputs
    return NativeVioLiveWorker(binary, CHECKOUT, profile, tmp_path / "live", **kwargs)


def offer(worker, arrival_inputs, index=0):
    return worker.submit_frame(*arrival_inputs[-1][index], calibration_id="fixture-camera", clock_fit=arrival_inputs[3])


def test_evidence_delay_counts_toward_snapshot_age(arrival_inputs, tmp_path, monkeypatch):
    worker = make_worker(arrival_inputs, tmp_path)
    original = worker._after_batch
    callback_started = []

    def delayed_ack(payload, record):
        callback_started.append(time.monotonic_ns())
        time.sleep(.05)
        original(payload, record)

    monkeypatch.setattr(worker, "_after_batch", delayed_ack)
    worker.start()
    try:
        offer(worker, arrival_inputs)
        wait_for(lambda: worker.status()["completed_batch_count"] == 1)
        snapshot = worker.take_latest()
        timing = worker.timing_snapshot()["samples"][0]
        assert snapshot["received_host_monotonic_ns"] == timing["received_host_ns"]
        assert timing["received_host_ns"] <= callback_started[0]
        assert timing["published_host_ns"] - timing["received_host_ns"] >= 50_000_000
        assert worker.status()["last_result_received_age_ms"] >= 50
        assert timing["sensor_timestamp_ns"] == snapshot["record"]["timestamp_ns"]
        stages = ("prepare_ms", "write_ms", "native_wait_and_decode_ms",
                  "adapter_ack_ms", "publish_lock_wait_ms")
        assert timing["service_ms"] == pytest.approx(sum(timing[key] for key in stages))
        assert timing["queue_to_snapshot_ms"] == pytest.approx(timing["queue_wait_ms"] + timing["service_ms"])
        assert all(timing[key] >= 0 for key in stages)
        worker.finish()
        assert worker.join(10)
        report = json.loads((worker.root / "live_report.json").read_text())
        assert report["batch_timings"]["samples"] == [timing]
        assert not report["position_authority"]
    finally:
        assert worker.stop()


def test_actual_arriving_frames_match_sealed_native_replay_and_release_spool(arrival_inputs, tmp_path):
    binary, profile, prepared, fit, arrivals = arrival_inputs
    reference = tmp_path / "reference"
    assert run(binary, prepared, reference, pose_window=True)["complete"]
    worker = make_worker(arrival_inputs, tmp_path)
    assert not (worker.capture / "input_manifest.json").exists()
    assert not (worker.capture / "events.jsonl").exists()
    manifest = json.loads((worker.capture / "live_input.json").read_text())
    assert len(manifest["python_adapter_sha256"]) == 6
    assert all(len(value) == 64 for value in manifest["python_adapter_sha256"].values())
    worker.start()
    first = None
    try:
        for index in range(len(arrivals)):
            ticket = offer(worker, arrival_inputs, index)
            first = first or ticket
            wait_for(lambda: worker.status()["completed_batch_count"] == index+1)
            assert not list((worker.capture / "images").iterdir())
            assert ticket.worker_run_id == worker.worker_run_id
            assert not ticket.pixels.flags.writeable
            with pytest.raises(ValueError):
                ticket.pixels.setflags(write=True)
        worker.finish()
        assert worker.join(10)
        status = worker.status()
        assert status["status"] == "complete", status
        assert status["completed_batch_count"] == 160
        assert status["live_input"]["acknowledged_frames"] == 160
        assert status["live_input"]["peak_spool_images"] <= worker.capacity+2
        assert status["live_input"]["spool_images"] == 0
        assert (worker.output / "local_states.jsonl").read_bytes() == (reference / "local_states.jsonl").read_bytes()
        journal = [json.loads(line) for line in worker._journal.read_text().splitlines()]
        assert len(journal) == 320
        for offered, ack in zip(journal[::2], journal[1::2]):
            assert offered["record_type"] == "offered" and ack["record_type"] == "acknowledged"
            assert offered["timestamp_ns"] == ack["timestamp_ns"]
            assert offered["image_sha256"] == ack["image_sha256"]
        report = json.loads((worker.root / "live_report.json").read_text())
        assert not report["position_authority"] and not report["unacknowledged_images"]
        assert report["native_states_sha256"] == digest(worker.output / "local_states.jsonl")
        assert hashlib.sha256(first.pixels.tobytes()).hexdigest() == first.pixel_sha256
        assert not worker._process.poll()
    finally:
        assert worker.stop()


@pytest.mark.parametrize("fault", ["calibration", "resolution", "no_pixels", "clock_source", "no_clock_coverage",
    "partial_imu", "imu_source", "imu_clock", "imu_nan", "imu_reorder", "insufficient_imu", "camera_clock"])
def test_live_input_fault_ends_epoch_without_retry_or_authority(arrival_inputs, tmp_path, fault):
    worker = make_worker(arrival_inputs, tmp_path)
    frame, samples = deepcopy(arrival_inputs[-1][0])
    fit = arrival_inputs[3]
    calibration = "fixture-camera"
    if fault == "calibration":
        calibration = "different"
    elif fault == "resolution":
        frame = replace(frame, bgr=np.zeros((10, 10), dtype=np.uint8))
    elif fault == "no_pixels":
        frame = replace(frame, bgr=None)
    elif fault == "clock_source":
        fit = replace(fit, source=(2, 1))
    elif fault == "no_clock_coverage":
        fit = replace(fit, host_samples_ns=tuple(t+10**12 for t in fit.host_samples_ns))
    elif fault == "partial_imu":
        samples[0]["imu_fields_updated"] = 1
    elif fault == "imu_source":
        samples[0]["source_system"] = 2
    elif fault == "imu_clock":
        samples[0]["vehicle_time_domain"] = "unix_us"
    elif fault == "imu_nan":
        samples[0]["acceleration_m_s2"][0] = float("nan")
    elif fault == "imu_reorder":
        samples.reverse()
    elif fault == "insufficient_imu":
        samples = samples[:1]
    else:
        frame = replace(frame, sensor_timestamp_ns=None)
    worker.start()
    try:
        with pytest.raises(ValueError):
            worker.submit_frame(frame, samples, calibration_id=calibration, clock_fit=fit)
        assert worker.join(5)
        assert worker.status()["status"] == "failed"
        assert worker.status()["completed_batch_count"] == 0
        assert not worker.status()["position_authority"]
        assert not list((worker.capture / "images").iterdir())
        with pytest.raises(RuntimeError):
            offer(worker, arrival_inputs)
    finally:
        assert worker.stop()


def test_live_overflow_stops_instead_of_replay_retry(arrival_inputs, tmp_path, monkeypatch):
    worker = make_worker(arrival_inputs, tmp_path, capacity=1)
    entered, release = threading.Event(), threading.Event()
    original = worker._read_result
    def hold(*args):
        entered.set()
        assert release.wait(3)
        return original(*args)
    monkeypatch.setattr(worker, "_read_result", hold)
    worker.start()
    try:
        offer(worker, arrival_inputs)
        assert entered.wait(3)
        offer(worker, arrival_inputs, 1)
        with pytest.raises(RuntimeError, match="queue overflow"):
            offer(worker, arrival_inputs, 2)
        release.set()
        assert worker.join(5)
        assert worker.status()["status"] == "failed"
        assert worker.status()["backpressure_count"] == 1
        assert worker.status()["live_input"]["peak_spool_images"] == 3
        assert not list((worker.capture / "images").iterdir())
        assert json.loads((worker.root / "live_report.json").read_text())["unacknowledged_images"]
    finally:
        release.set()
        assert worker.stop()


def test_live_image_and_config_mutations_fail_before_snapshot(arrival_inputs, tmp_path, monkeypatch):
    worker = make_worker(arrival_inputs, tmp_path)
    original = worker._before_batch
    def corrupt(payload):
        event = json.loads(payload.splitlines()[-1])
        (worker.capture / event["image_path"]).write_bytes(b"mutated")
        original(payload)
    monkeypatch.setattr(worker, "_before_batch", corrupt)
    worker.start()
    try:
        ticket = offer(worker, arrival_inputs)
        assert worker.join(5)
        assert worker.status()["status"] == "failed"
        assert "changed before native" in worker.status()["error"]
        assert worker.take_latest() is None
        assert not list((worker.capture / "images").iterdir())
        assert ticket.pixel_sha256 == hashlib.sha256(ticket.pixels.tobytes()).hexdigest()
    finally:
        assert worker.stop()


def test_configuration_change_cannot_start_live_native(arrival_inputs, tmp_path):
    worker = make_worker(arrival_inputs, tmp_path)
    (worker.capture / "estimator_config.yaml").write_text("changed")
    with pytest.raises(ValueError, match="configuration changed"):
        worker.start()
    assert worker._process is None


def test_frame_limit_and_no_restart(arrival_inputs, tmp_path):
    worker = make_worker(arrival_inputs, tmp_path, max_frames=2)
    worker.start()
    start = worker._started_ns
    try:
        with pytest.raises(RuntimeError, match="cannot be restarted"):
            worker.start()
        assert worker._started_ns == start
        for index in range(2):
            offer(worker, arrival_inputs, index)
            wait_for(lambda: worker.status()["completed_batch_count"] == index+1)
        with pytest.raises(ValueError, match="frame limit"):
            offer(worker, arrival_inputs, 2)
        assert worker.join(5)
        assert worker.status()["status"] == "failed"
    finally:
        assert worker.stop()


def test_idle_session_deadline_reaps_owned_process(arrival_inputs, tmp_path):
    worker = make_worker(arrival_inputs, tmp_path, max_duration_s=1.)
    worker.start()
    assert worker.join(5)
    assert "duration exceeded" in worker.status()["error"]
    assert worker._process.poll() is not None
    assert not worker.status()["thread_alive"]


@pytest.mark.parametrize("fault", ["repeated_camera", "camera_gap", "imu_gap", "host_offset_step"])
def test_cross_batch_discontinuity_cannot_continue_native_epoch(arrival_inputs, tmp_path, fault):
    worker = make_worker(arrival_inputs, tmp_path)
    worker.start()
    try:
        offer(worker, arrival_inputs)
        wait_for(lambda: worker.status()["completed_batch_count"] == 1)
        frame, samples = deepcopy(arrival_inputs[-1][1])
        if fault == "repeated_camera":
            frame = arrival_inputs[-1][0][0]
        elif fault == "camera_gap":
            frame, samples = deepcopy(arrival_inputs[-1][7])
        elif fault == "imu_gap":
            samples[0]["vehicle_timestamp_us"] += 100_000
        else:
            clocks = deepcopy(frame.host_clock_samples)
            for bracket in clocks:
                bracket["boottime_ns"] += 1_000_000
            frame = replace(frame, sensor_timestamp_ns=frame.sensor_timestamp_ns+1_000_000, host_clock_samples=clocks)
        with pytest.raises(ValueError):
            worker.submit_frame(frame, samples, calibration_id="fixture-camera", clock_fit=arrival_inputs[3])
        assert worker.join(5)
        assert worker.status()["status"] == "failed"
        assert worker.status()["completed_batch_count"] == 1
    finally:
        assert worker.stop()


def test_evidence_threshold_ends_idle_session(arrival_inputs, tmp_path):
    worker = make_worker(arrival_inputs, tmp_path, max_evidence_bytes=1024*1024)
    worker.start()
    try:
        offer(worker, arrival_inputs)
        wait_for(lambda: worker.status()["completed_batch_count"] == 1)
        with worker._journal.open("a") as stream:
            stream.write(" " * (1024*1024))
        assert worker.join(5)
        assert "evidence byte limit" in worker.status()["error"]
        assert worker._process.poll() is not None
    finally:
        assert worker.stop()


def test_source_camera_buffer_mutation_cannot_change_native_or_learned_copy(arrival_inputs, tmp_path, monkeypatch):
    worker = make_worker(arrival_inputs, tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = worker._before_batch
    def hold(payload):
        entered.set()
        assert release.wait(3)
        original(payload)
    monkeypatch.setattr(worker, "_before_batch", hold)
    worker.start()
    try:
        ticket = offer(worker, arrival_inputs)
        assert entered.wait(3)
        arrival_inputs[-1][0][0].bgr[:] = 0
        assert np.count_nonzero(ticket.pixels) > 0
        assert hashlib.sha256(ticket.pixels.tobytes()).hexdigest() == ticket.pixel_sha256
        release.set()
        wait_for(lambda: worker.status()["completed_batch_count"] == 1)
        worker.finish()
        assert worker.join(5)
        assert worker.status()["status"] == "no_local_estimate"
    finally:
        release.set()
        assert worker.stop()


def test_configuration_mutation_after_frame_fails_before_next_native_write(arrival_inputs, tmp_path):
    worker = make_worker(arrival_inputs, tmp_path)
    worker.start()
    try:
        offer(worker, arrival_inputs)
        wait_for(lambda: worker.status()["completed_batch_count"] == 1)
        (worker.capture / "kalibr_imu_chain.yaml").write_text("changed")
        offer(worker, arrival_inputs, 1)
        assert worker.join(5)
        assert worker.status()["status"] == "failed"
        assert worker.status()["completed_batch_count"] == 1
        assert not list((worker.capture / "images").iterdir())
    finally:
        assert worker.stop()


def test_stop_during_frame_encoding_waits_for_ownership_cleanup(arrival_inputs, tmp_path, monkeypatch):
    worker = make_worker(arrival_inputs, tmp_path)
    entered, release = threading.Event(), threading.Event()
    original = cv2.imencode
    def hold(*args, **kwargs):
        entered.set()
        assert release.wait(3)
        return original(*args, **kwargs)
    monkeypatch.setattr(cv2, "imencode", hold)
    errors = []
    def produce():
        try:
            offer(worker, arrival_inputs)
        except RuntimeError as exc:
            errors.append(str(exc))
    worker.start()
    producer = threading.Thread(target=produce)
    producer.start()
    try:
        assert entered.wait(3)
        assert not worker.stop(timeout_s=.01)
        release.set()
        producer.join(3)
        assert not producer.is_alive() and errors
        assert worker.join(5)
        assert not list((worker.capture / "images").iterdir())
        report = json.loads((worker.root / "live_report.json").read_text())
        assert report["worker"]["status"] == "failed"
    finally:
        release.set()
        producer.join(3)
        assert worker.stop()


@pytest.mark.parametrize("kwargs", [{"max_frames": True}, {"max_frames": 20001},
    {"max_duration_s": float("inf")}, {"max_duration_s": 1801}, {"max_evidence_bytes": True},
    {"max_frame_bytes": 1024}, {"max_frame_bytes": 64*1024*1024+1}])
def test_invalid_live_bounds_do_not_create_output(arrival_inputs, tmp_path, kwargs):
    with pytest.raises(ValueError):
        make_worker(arrival_inputs, tmp_path, **kwargs)
    assert not (tmp_path / "live").exists()
