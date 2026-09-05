from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
import json
import threading
import time

import cv2
import numpy as np
import pytest

from vision_nav.camera_ingress import CapturedFrame
from vision_nav.neural import shadow_runtime as runtime
from vision_nav.neural.profiles import PI5_CROSSVIEW_NAV_V1
from vision_nav.neural.releases import ModelRelease
from vision_nav.neural.temporal_window import TemporalWindowBuilder


HOST = 10_000_000_000
OFFSET = 2_000_000_000
FRAME = np.zeros((6, 8, 3), dtype=np.uint8)


def captured(path=Path("frame.jpg")):
    def bracket(stamp):
        return {"monotonic_before_ns": stamp - 100, "monotonic_after_ns": stamp + 100,
                "boottime_ns": stamp + OFFSET, "status": "observed"}
    return CapturedFrame(path, "picamera2", sensor_timestamp_ns=HOST + OFFSET,
                         sensor_clock_domain="linux_boottime_ns",
                         sensor_timestamp_reference="first_row_exposure_start",
                         host_clock_samples=[bracket(HOST - 1_000_000), bracket(HOST + 10_000_000)])


def test_retains_exposure_time_not_processing_time():
    result = runtime.shadow_capture_timing(captured(), now_ns=HOST + 250_000_000, max_age_ms=1000)
    assert result["host_monotonic_ns"] == HOST
    assert result["age_ms"] == 250
    assert result["camera"]["clock_uncertainty_ns"] == 100
    assert result["sensor_timing_calibrated"] is result["position_authority"] is False
    assert result["attitude_time_basis"] == "host_receive_unqualified"


@pytest.mark.parametrize("delay", [-1, 1_000_000_001])
def test_future_and_stale_capture_times_are_not_replaced(delay):
    report = runtime.shadow_capture_timing(captured(), now_ns=HOST + delay, max_age_ms=1000)
    assert report["status"] == "rejected"
    assert report["host_monotonic_ns"] is None
    assert report["camera"]["host_monotonic_ns"] == HOST


@pytest.mark.parametrize("frame", [CapturedFrame(Path("frame.jpg"), "file"),
                                  replace(captured(), host_clock_samples=[]),
                                  replace(captured(), sensor_clock_domain="unknown")])
def test_missing_camera_metadata_does_not_invent_time(frame):
    report = runtime.shadow_capture_timing(frame, now_ns=HOST + 100, max_age_ms=1000)
    assert report["status"] == "unavailable"
    assert report["host_monotonic_ns"] is None


def ready_window():
    window = TemporalWindowBuilder(window_size=2)
    for stamp in (HOST, HOST + 100_000_000):
        window.push_attitude(host_monotonic_ns=stamp, roll_rad=0., pitch_rad=0., yaw_rad=0.)
        window.observe_frame(FRAME, host_monotonic_ns=stamp)
    assert window.evidence()["status"] == "ready"
    return window


@pytest.mark.parametrize("stamp", [HOST, HOST + 100_000_000, 0, True, float("nan"), HOST + 500_000_000])
def test_invalid_or_unaligned_frames_invalidate_old_full_window(stamp):
    window = ready_window()
    report = window.observe_frame(FRAME, host_monotonic_ns=stamp)
    assert report["status"] == "rejected"
    inputs, reason = window.build_inputs({}, image_preprocessor=lambda *_: pytest.fail("stale window reused"))
    assert inputs is None and reason
    assert window.evidence()["frame_count"] == 0


def test_gap_starts_new_window_and_records_capture_times():
    window = ready_window()
    stamp = HOST + 3_000_000_000
    window.push_attitude(host_monotonic_ns=stamp, roll_rad=0., pitch_rad=0., yaw_rad=0.)
    result = window.observe_frame(FRAME, host_monotonic_ns=stamp)
    assert result["status"] == "warming_up"
    assert result["gap_reset"] is True
    assert result["frame_timestamps_ns"] == [stamp]
    assert window.build_inputs({}, image_preprocessor=lambda *_: None)[1] == "temporal_window_not_full"


@pytest.mark.parametrize("setting", [
    {"window_size": True}, {"window_size": 65}, {"window_size": 2.5},
    {"window_size": 2, "max_frame_gap_ms": float("nan")},
    {"window_size": 2, "max_attitude_frame_delta_ms": float("inf")},
])
def test_invalid_window_policy(setting):
    with pytest.raises(ValueError):
        TemporalWindowBuilder(**setting)


def test_sampler_retains_camera_metadata(tmp_path):
    expected = captured(tmp_path / "camera.jpg")
    ingress = SimpleNamespace(capture=lambda _: expected)
    args = SimpleNamespace(frame=None, camera_backend="picamera2", output_dir=tmp_path)
    result, same = runtime._sample_frame(args, sequence=0, ingress=ingress)
    assert result is expected and same is ingress


@pytest.mark.parametrize("completion_delay", [200_000_000, 2_000_000_000])
def test_real_shadow_loop_passes_capture_time_and_withholds_late_results(tmp_path, monkeypatch, completion_delay):
    model = ModelRelease("model", PI5_CROSSVIEW_NAV_V1.profile_id, "1", "a"*64, "b"*64,
                         ("test",), "test", "test-only", False, ("camera",), {})
    compatible = runtime.RuntimeCompatibility(True, (), "onnxruntime_cpu", PI5_CROSSVIEW_NAV_V1)
    frame = captured(tmp_path / "frame.jpg")
    monkeypatch.setattr(runtime, "_load_inputs", lambda _: ({"release_id": "map"}, model, compatible))
    monkeypatch.setattr(runtime, "_frame_calibration", lambda *_: (None, compatible))
    monkeypatch.setattr(runtime, "LearnedModelRunner", lambda *a, **k: object())
    monkeypatch.setattr(runtime, "_sample_frame", lambda *a, **k: (frame, None))
    monkeypatch.setattr(runtime, "load_shadow_frame", lambda _: object())
    monkeypatch.setattr(runtime, "analyze_frame_file", lambda _: {"status": "passing"})
    clock = iter([HOST + 100_000_000, HOST + completion_delay])
    monkeypatch.setattr(runtime.time, "monotonic_ns", lambda: next(clock))

    def probe(*args, **kwargs):
        assert kwargs["frame_monotonic_ns"] == HOST
        return {"decision_reason": "test_probe", "shadow_tracking_candidate": {"status": "warming_up"},
                "temporal_student_evidence": {"status": "available", "position_authority": False}}

    monkeypatch.setattr(runtime, "run_shadow_frame_probe", probe)
    statuses = []
    monkeypatch.setattr(runtime, "_write_trace", lambda _, status: statuses.append(status))
    monkeypatch.setattr(runtime, "_send_udp", lambda *_: None)
    monkeypatch.setattr("sys.argv", ["shadow", "--map-release", "map.json", "--model-release", "model.json",
                                   "--calibration-id", "camera", "--camera-backend", "picamera2",
                                   "--output-dir", str(tmp_path), "--once"])
    runtime.main()
    output = statuses[0]
    evidence = output["inference"]["frame_probe"]
    assert evidence["capture_timing"]["host_monotonic_ns"] == HOST
    assert evidence["capture_metadata"]["sensor_timestamp_ns"] == HOST + OFFSET
    if completion_delay > 1_000_000_000:
        assert evidence["shadow_tracking_candidate"]["status"] == "withheld"
        assert evidence["temporal_student_evidence"]["status"] == "withheld"
        assert evidence["result_timing"]["status"] == "rejected"
    else:
        assert evidence["result_timing"]["status"] == "available"
    assert output["position"]["navigation_mode"] == "NO_GLOBAL_FIX"
    assert output["px4_publication"]["enabled"] is False


def test_continuous_shadow_loop_keeps_capturing_during_slow_probe(tmp_path, monkeypatch):
    model = ModelRelease("model", PI5_CROSSVIEW_NAV_V1.profile_id, "1", "a"*64, "b"*64,
                         ("test",), "test", "test-only", False, ("camera",), {})
    compatible = runtime.RuntimeCompatibility(True, (), "onnxruntime_cpu", PI5_CROSSVIEW_NAV_V1)
    camera_threads, statuses, heartbeats = [], [], []
    heartbeat_condition = threading.Condition()
    capture_condition, closed = threading.Condition(), threading.Event()
    capture_count = 0

    class Camera:
        def __init__(self):
            camera_threads.append(threading.get_ident())
            self.count = 0
        def capture(self, output):
            nonlocal capture_count
            camera_threads.append(threading.get_ident())
            stamp = time.monotonic_ns()
            assert cv2.imwrite(str(output), FRAME)
            self.count += 1
            with capture_condition:
                capture_count += 1
                capture_condition.notify_all()
            def bracket(at):
                return {"monotonic_before_ns": at - 100, "monotonic_after_ns": at + 100,
                        "boottime_ns": at + OFFSET, "status": "observed"}
            return CapturedFrame(output, "test_camera", sensor_timestamp_ns=stamp + OFFSET,
                                 sensor_clock_domain="linux_boottime_ns",
                                 sensor_timestamp_reference="first_row_exposure_start",
                                 host_clock_samples=[bracket(stamp - 1000), bracket(time.monotonic_ns())])
        def close(self):
            camera_threads.append(threading.get_ident())
            closed.set()

    monkeypatch.setattr(runtime, "_load_inputs", lambda _: ({"release_id": "map"}, model, compatible))
    monkeypatch.setattr(runtime, "_frame_calibration", lambda *_: (None, compatible))
    monkeypatch.setattr(runtime, "LearnedModelRunner", lambda *a, **k: object())
    monkeypatch.setattr(runtime, "create_camera_ingress", lambda *a, **k: Camera())
    monkeypatch.setattr(runtime, "_sample_frame", lambda *a, **k: pytest.fail("Live loop used serial capture"))
    probe_count = 0
    def probe(*args, **kwargs):
        nonlocal probe_count
        probe_count += 1
        assert kwargs["frame_monotonic_ns"] is not None
        # This waits inside inference: only an independently running camera can
        # produce the additional frames needed to release it.
        with capture_condition:
            target = capture_count + 3
            assert capture_condition.wait_for(lambda: capture_count >= target, timeout=2)
        with heartbeat_condition:
            target = len(heartbeats) + 2
            assert heartbeat_condition.wait_for(lambda: len(heartbeats) >= target and
                heartbeats[-1].get("runtime_metrics", {}).get("sample_count", 0) >= 2, timeout=2)
            assert heartbeats[-1]["heartbeat"]["phase"] == "inference"
            assert heartbeats[-1]["heartbeat"]["result_iteration"] == probe_count - 1
            assert heartbeats[-1]["heartbeat"]["inference_overdue"] is True
        return {"decision_reason": "test_probe"}
    monkeypatch.setattr(runtime, "run_shadow_frame_probe", probe)
    class Finished(Exception):
        pass
    original_trace = runtime._write_trace
    def record(_, status, **kwargs):
        assert kwargs == {"snapshot": False}
        original_trace(_, status, **kwargs)
        statuses.append(status)
        if len(statuses) == 2:
            raise Finished()
    monkeypatch.setattr(runtime, "_write_trace", record)
    def send_heartbeat(status, _):
        assert status["emission_kind"] == "heartbeat"
        with heartbeat_condition:
            heartbeats.append(status)
            heartbeat_condition.notify_all()
    monkeypatch.setattr(runtime, "_send_udp", send_heartbeat)
    monkeypatch.setattr("sys.argv", ["shadow", "--map-release", "map.json", "--model-release", "model.json",
                                   "--calibration-id", "camera", "--camera-backend", "picamera2",
                                   "--output-dir", str(tmp_path), "--capture-interval-s", "0.005", "--interval-s", "0.1",
                                   "--heartbeat-interval-s", "0.005", "--inference-warning-ms", "1",
                                   "--record-runtime-metrics", "--metrics-interval-s", "0.005"])
    with pytest.raises(Finished):
        runtime.main()
    assert probe_count == 2
    assert closed.is_set()
    assert len(set(camera_threads)) == 1 and camera_threads[0] != threading.get_ident()
    assert statuses[-1]["camera_health"]["capture_worker"]["superseded_count"] > 0
    assert statuses[-1]["camera_health"]["capture_worker"]["pending_capacity"] == 1
    for status in statuses:
        path = Path(status["inference"]["frame_probe"]["capture_metadata"]["path"])
        assert path.is_file()
        assert status["px4_publication"]["enabled"] is False
    assert not list((tmp_path / "frames").glob(".pending-*"))
    final = json.loads((tmp_path / "runtime_status.json").read_text())
    assert final == heartbeats[-1]
    assert final["heartbeat"]["phase"] == "stopped"
    assert final["inference"]["frame_probe"] == {}
    assert len((tmp_path / "decision_trace.jsonl").read_text().splitlines()) == 2
    metrics_file = next(tmp_path.glob("runtime_metrics-*.jsonl"))
    metrics = [json.loads(line) for line in metrics_file.read_text().splitlines()]
    assert metrics[0]["run_id"] == final["heartbeat"]["run_id"]
    assert metrics[-1]["record_type"] == "footer"
    assert any(row.get("activity", {}).get("phase") == "inference" for row in metrics)
    assert final["runtime_metrics"]["sample_count"] >= 2
    assert final["runtime_metrics"]["status"] == "stopped"
    from vision_nav.neural.runtime_load_report import summarize_runtime_load
    report = summarize_runtime_load(metrics_file, tmp_path / "decision_trace.jsonl")
    assert report["run_id"] == final["heartbeat"]["run_id"]
    assert report["decisions"]["count"] == 2
    assert report["decisions"]["capture_to_result_ms"]["count"] == 2
    assert report["decisions"]["ignored_other_or_unbound_run_count"] == 0
    assert report["bindings"] == {"map_release_id": "map", "model_release_id": "model"}
    assert "not_all_learned_stages_observed" in report["evidence_gaps"]
    assert "attitude_context_measurements_incomplete" in report["evidence_gaps"]
    assert report["position_authority"] is False
