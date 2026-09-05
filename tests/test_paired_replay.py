"""Scheduling and exact capture binding; synthetic sensors, not field accuracy."""

import json
import os
from pathlib import Path
import time
from uuid import uuid4

import numpy as np
import pytest

from vision_nav.camera import CameraCalibration
from vision_nav.vio.openvins_replay import digest, read_yaml, validate_capture
from vision_nav.vio.prepare_capture import prepare_capture
from vision_nav.vio.paired_replay import PairedSensorReplay
from test_prepare_vio_capture import sources, CHECKOUT


class FakeWorker:
    """Native process stand-in for deterministic ownership/failure tests only."""
    fail_after = None

    def __init__(self, binary, capture, output):
        self.binary, self.capture, self.output = binary, capture, output
        self.manifest = validate_capture(capture)
        self.manifest_sha256, self.binary_sha256 = digest(capture / "input_manifest.json"), digest(binary)
        self.worker_run_id = str(uuid4())
        self.count, self.state, self.stopped = 0, "not_started", False

    def start(self):
        self.output.mkdir()
        self.state = "processing"

    def submit(self, batch):
        if self.fail_after is not None and self.count >= self.fail_after:
            self.state = "failed"
            raise RuntimeError("injected_native_failure")
        self.count += 1
        return True

    def finish(self):
        self.state = "complete"

    def join(self, timeout):
        return self.state in {"complete", "failed", "stopped"}

    def stop(self):
        self.stopped = True
        if self.state == "processing":
            self.state = "stopped"

    def status(self):
        return {"status": self.state, "error": None, "completed_batch_count": self.count,
                "thread_alive": False, "worker_run_id": self.worker_run_id}


@pytest.fixture
def paired_inputs(sources, tmp_path):
    session, profile, _ = sources
    native = tmp_path / "native"
    prepare_capture(CHECKOUT, session, profile, native)
    spec = read_yaml(profile / "kalibr_imucam_chain.yaml")["cam0"]
    fx, fy, cx, cy = spec["intrinsics"]
    calibration = CameraCalibration(profile, "fixture", *spec["resolution"],
        np.array([[fx, 0., cx], [0., fy, cy], [0., 0., 1.]]),
        np.array(spec["distortion_coeffs"] + [0.]), "plumb_bob", "fixture-camera", "measured")
    binary = tmp_path / "fake-binary"
    binary.write_bytes(b"test-only")
    return dict(binary=binary, capture=native, output=tmp_path / "result", session=session,
        manifest=json.loads((session / "manifest.json").read_text()), calibration=calibration,
        camera_context=[fx / 320, fy / 240, (cx+.5)/320, (cy+.5)/240, 0., 0., 0., 1., 0., 0., 0.],
        window_size=3, playback_speed=100.)


def fake(monkeypatch):
    monkeypatch.setattr("vision_nav.vio.paired_replay.NativeVioStreamWorker", FakeWorker)


@pytest.mark.parametrize("override", [{"window_size": 1}, {"window_size": 33}, {"window_size": True},
    {"playback_speed": 0}, {"playback_speed": float("inf")}, {"playback_speed": 101},
    {"max_duration_s": -1}, {"max_duration_s": float("nan")}, {"max_duration_s": 3601}])
def test_invalid_policy_rejected_before_any_input_or_process(override):
    args = dict(binary="absent", capture="absent", output="absent", session="absent", manifest={},
                calibration=None, camera_context=[], window_size=2)
    with pytest.raises(ValueError):
        PairedSensorReplay(**{**args, **override})


def test_slow_model_does_not_block_vio_or_lose_window_context(paired_inputs, monkeypatch):
    fake(monkeypatch)
    paired = PairedSensorReplay(**paired_inputs)
    paired.start()
    selected = []
    try:
        for window in paired.windows():
            before = paired.worker.count
            time.sleep(.04)  # An intentionally slower learned consumer.
            selected.append(window)
            if len(selected) == 1:
                assert paired.worker.count > before
        state = paired.status()
        assert state["complete"]
        assert state["native"]["completed_batch_count"] == 160
        assert state["offered_windows"] == 158
        assert state["superseded_windows"] > 0
        assert state["selected_windows"] + state["superseded_windows"] == 158
        assert state["pending_windows"] == 0
        assert all(w["indices"] == list(range(w["indices"][-1]-2, w["indices"][-1]+1)) for w in selected)
        assert selected[-1]["indices"][-1] == 159
        assert all(w["anchor_vio_timestamp_ns"] == 1_000_000_000 + w["indices"][-1] * 50_000_000 for w in selected)
    finally:
        paired.stop()
    assert not paired.status()["sensor_producer_alive"]
    assert paired.worker.stopped


@pytest.mark.parametrize("fault", ["session", "telemetry", "image", "intrinsics", "distortion", "mount", "event_order"])
def test_mismatched_sources_never_start_native(paired_inputs, monkeypatch, fault):
    fake(monkeypatch)
    args = paired_inputs
    if fault == "session":
        args["manifest"]["session_id"] = "other"
    elif fault == "telemetry":
        with (args["session"] / "telemetry.jsonl").open("a") as stream:
            stream.write('{}\n')
    elif fault == "image":
        (args["session"] / args["manifest"]["frames"][0]["path"]).write_bytes(b"changed")
    elif fault == "intrinsics":
        args["calibration"].camera_matrix[0, 0] += 1
    elif fault == "distortion":
        args["calibration"].distortion_coefficients[-1] = .1
    elif fault == "mount":
        args["camera_context"][-1] = .1
    else:
        path = args["capture"] / "capture_timing.json"
        data = json.loads(path.read_text())
        data["frames"][0]["timestamp_ns"] += 1
        path.write_text(json.dumps(data))
        manifest_path = args["capture"] / "input_manifest.json"
        native = json.loads(manifest_path.read_text())
        native["checksums"][path.name] = digest(path)
        manifest_path.write_text(json.dumps(native))
    with pytest.raises(ValueError):
        PairedSensorReplay(**args)
    assert not args["output"].exists()


def test_native_failure_clears_pending_evidence_and_surfaces_error(paired_inputs, monkeypatch):
    fake(monkeypatch)
    monkeypatch.setattr(FakeWorker, "fail_after", 10)
    paired = PairedSensorReplay(**paired_inputs)
    paired.start()
    try:
        with pytest.raises(RuntimeError, match="injected_native_failure"):
            for _ in paired.windows():
                time.sleep(.02)
    finally:
        paired.stop()
    state = paired.status()
    assert not state["complete"] and state["pending_windows"] == 0
    assert not state["sensor_producer_alive"] and paired.worker.stopped
    assert json.loads((paired_inputs["output"] / "paired_report.json").read_text())["error"]


def test_capture_file_change_during_inference_is_rejected(paired_inputs, monkeypatch):
    fake(monkeypatch)
    paired = PairedSensorReplay(**paired_inputs)
    path = paired_inputs["session"] / paired_inputs["manifest"]["frames"][0]["path"]
    paired.verify_frame(0, path)
    path.write_bytes(b"changed during run")
    with pytest.raises(ValueError, match="changed"):
        paired.verify_frame(0, path)


def test_consumer_abort_stops_both_owned_workers(paired_inputs, monkeypatch):
    fake(monkeypatch)
    paired = PairedSensorReplay(**{**paired_inputs, "playback_speed": 1.})
    paired.start()
    stream = paired.windows()
    try:
        next(stream)
    finally:
        stream.close()
        paired.stop()
    state = paired.status()
    assert not state["complete"] and not state["sensor_producer_alive"]
    assert paired.worker.stopped
    assert state["pending_windows"] == 0


def test_paired_duration_limit_stops_the_owned_worker(paired_inputs, monkeypatch):
    fake(monkeypatch)
    paired = PairedSensorReplay(**{**paired_inputs, "playback_speed": 1., "max_duration_s": .02})
    paired.start()
    try:
        with pytest.raises(RuntimeError, match="duration"):
            list(paired.windows())
    finally:
        paired.stop()
    assert not paired.status()["complete"]
    assert paired.worker.stopped


def test_actual_native_progress_during_slow_consumer(paired_inputs):
    location = os.environ.get("VISION_NAV_OPENVINS_BINARY")
    if not location:
        pytest.skip("Set native binary to exercise actual OpenVINS")
    paired = PairedSensorReplay(**{**paired_inputs, "binary": Path(location), "playback_speed": 4.})
    paired.start()
    progress = []
    try:
        for _ in paired.windows():
            before = paired.worker.status()["completed_batch_count"]
            time.sleep(.08)
            progress.append(paired.worker.status()["completed_batch_count"] - before)
        state = paired.status()
        assert state["complete"], state
        assert state["native"]["completed_batch_count"] == 160
        assert state["native"]["local_state_counts"]["LOCAL_ESTIMATE"] > 40
        assert max(progress) > 1
        assert state["superseded_windows"] > 0
        assert state["position_authority"] is False
    finally:
        paired.stop()
    assert not paired.status()["sensor_producer_alive"]
    assert not paired.worker.status()["thread_alive"]
