from queue import Empty, Queue
from types import SimpleNamespace
import threading
import time

import numpy as np
import pytest

from vision_nav.camera_ingress import CapturedFrame
from vision_nav.mavlink_bridge import MavlinkTelemetrySample
from vision_nav.neural import shadow_runtime as runtime
from vision_nav.neural.profiles import PI5_CROSSVIEW_NAV_V1
from vision_nav.neural.releases import ModelRelease
from vision_nav.neural.temporal_window import TemporalWindowBuilder


class RuntimeLink:
    def __init__(self):
        self.owner = threading.get_ident()
        self.commands = Queue()
        self.condition = threading.Condition()
        self.completed = 0
        self.returned_sample = False
        self.closed = threading.Event()
    def connect(self):
        assert threading.get_ident() == self.owner
    def try_read_telemetry(self, timeout_s):
        assert threading.get_ident() == self.owner
        with self.condition:
            if self.returned_sample:
                self.completed += 1
                self.returned_sample = False
                self.condition.notify_all()
        try:
            sample = self.commands.get(timeout=timeout_s)
        except Empty:
            return None
        if isinstance(sample, Exception):
            raise sample
        self.returned_sample = True
        return sample
    def close(self):
        assert threading.get_ident() == self.owner
        self.closed.set()
    def send_attitude(self, stamp, *, source_system=1):
        with self.condition:
            expected = self.completed + 1
            self.commands.put(MavlinkTelemetrySample("ATTITUDE", 1, vehicle_timestamp_us=stamp,
                vehicle_time_domain="px4_boot_us", roll_rad=0.1, pitch_rad=0.2, yaw_rad=0.3,
                source_system=source_system, source_component=1))
            assert self.condition.wait_for(lambda: self.completed >= expected, timeout=2)


@pytest.mark.parametrize("fault", ["none", "before_loss", "during_loss", "overflow", "wrong_sender"])
def test_runtime_withholds_temporal_result_when_attitude_breaks(tmp_path, monkeypatch, fault):
    model = ModelRelease("model", PI5_CROSSVIEW_NAV_V1.profile_id, "1", "a" * 64, "b" * 64,
                         ("test",), "test", "test-only", False, ("camera",), {})
    compatibility = runtime.RuntimeCompatibility(True, (), "onnxruntime_cpu", PI5_CROSSVIEW_NAV_V1)
    monkeypatch.setattr(runtime, "_load_inputs", lambda _: ({"release_id": "map"}, model, compatibility))
    monkeypatch.setattr(runtime, "_frame_calibration", lambda *_: (None, compatibility))
    monkeypatch.setattr(runtime, "LearnedModelRunner", lambda *a, **k: object())
    monkeypatch.setattr(runtime, "_temporal_window_size", lambda _: 1)
    image = np.zeros((8, 8, 3), dtype=np.uint8)
    monkeypatch.setattr(runtime, "load_shadow_frame", lambda _: SimpleNamespace(bgr=image))
    monkeypatch.setattr(runtime, "analyze_frame_file", lambda _: {"status": "passing"})
    created, statuses, windows = Queue(), [], []
    def factory(*args, **kwargs):
        link = RuntimeLink()
        created.put(link)
        return link
    monkeypatch.setattr(runtime, "MavlinkVisionBridge", factory)
    link = None
    def capture(*args, **kwargs):
        nonlocal link
        link = created.get(timeout=2)
        link.send_attitude(1000, source_system=2 if fault == "wrong_sender" else 1)
        if fault == "before_loss":
            link.commands.put(OSError("USB lost before inference"))
            assert link.closed.wait(2)
        stamp = time.monotonic_ns()
        offset = 10_000_000
        brackets = [{"status": "observed", "monotonic_before_ns": at - 100,
                     "monotonic_after_ns": at + 100, "boottime_ns": at + offset}
                    for at in (stamp - 1000, stamp + 1000)]
        return CapturedFrame(tmp_path / "frame.jpg", "test", sensor_timestamp_ns=stamp + offset,
            sensor_clock_domain="linux_boottime_ns", sensor_timestamp_reference="first_row_exposure_start",
            host_clock_samples=brackets), None
    monkeypatch.setattr(runtime, "_sample_frame", capture)
    def probe(*args, **kwargs):
        window = kwargs["temporal_window"]
        windows.append(window)
        observation = window.observe_frame(image, host_monotonic_ns=kwargs["frame_monotonic_ns"])
        assert observation["status"] == ("rejected" if fault in {"before_loss", "wrong_sender"} else "ready")
        if fault == "during_loss":
            link.commands.put(OSError("USB lost during inference"))
            assert link.closed.wait(2)
        elif fault == "overflow":
            for stamp in range(2000, 515000, 1000):
                link.send_attitude(stamp)
        return {"decision_reason": "test_model_result", "shadow_tracking_candidate": {"status": "candidate"},
                "temporal_student_evidence": {"status": "available"}}
    monkeypatch.setattr(runtime, "run_shadow_frame_probe", probe)
    monkeypatch.setattr(runtime, "_write_trace", lambda _, value: statuses.append(value))
    monkeypatch.setattr(runtime, "_send_udp", lambda *_: None)
    monkeypatch.setattr("sys.argv", ["shadow", "--map-release", "map.json", "--model-release", "model.json",
        "--calibration-id", "camera", "--camera-backend", "picamera2", "--mavlink-endpoint", "test",
        "--max-frame-age-ms", "5000", "--attitude-stale-after-ms", "5000",
        "--output-dir", str(tmp_path), "--once"])
    runtime.main()
    assert link.closed.is_set() and link.owner != threading.get_ident()
    status = statuses[0]
    probe_status = status["inference"]["frame_probe"]
    if fault != "none":
        assert probe_status["decision_reason"] == "attitude_context_unavailable_or_changed"
        assert probe_status["shadow_tracking_candidate"]["status"] == "withheld"
        assert probe_status["temporal_student_evidence"]["status"] == "withheld"
        assert windows[0].evidence()["status"] == "rejected"
    else:
        assert probe_status["temporal_student_evidence"]["status"] == "available"
        assert status["attitude_health"]["status"] == "receiving"
    if fault == "overflow":
        assert status["attitude_health"]["overflow_count"] > 0
    if fault == "wrong_sender":
        assert status["attitude_health"]["filtered_source_count"] == 1
    assert status["px4_publication"]["enabled"] is False
    assert status["position"]["navigation_mode"] == "NO_GLOBAL_FIX"


def test_context_reset_discards_old_attitude_but_not_frame_watermark():
    window = TemporalWindowBuilder(window_size=1)
    image = np.zeros((4, 4, 3), dtype=np.uint8)
    window.push_attitude(host_monotonic_ns=1_000_000_000, roll_rad=0., pitch_rad=0., yaw_rad=0.)
    assert window.observe_frame(image, host_monotonic_ns=1_000_000_000)["status"] == "ready"
    window.reset_attitude_context("link_lost")
    assert window.observe_frame(image, host_monotonic_ns=1_001_000_000)["reason"] == "no_px4_attitude_samples"
    assert window.observe_frame(image, host_monotonic_ns=1_000_000_000)["reason"] == "nonmonotonic_frame_timestamp"


def test_incompatible_release_still_reports_configured_attitude_link(tmp_path, monkeypatch):
    model = ModelRelease("model", PI5_CROSSVIEW_NAV_V1.profile_id, "1", "a" * 64, "b" * 64,
                         ("test",), "test", "test-only", False, ("camera",), {})
    incompatible = runtime.RuntimeCompatibility(False, ("test_model_mismatch",), "onnxruntime_cpu", PI5_CROSSVIEW_NAV_V1)
    monkeypatch.setattr(runtime, "_load_inputs", lambda _: ({"release_id": "map"}, model, incompatible))
    monkeypatch.setattr(runtime, "_frame_calibration", lambda *_: (None, incompatible))
    monkeypatch.setattr(runtime, "LearnedModelRunner", lambda *a, **k: pytest.fail("Incompatible model loaded"))
    created, statuses = Queue(), []
    def factory(*args, **kwargs):
        link = RuntimeLink()
        created.put(link)
        return link
    monkeypatch.setattr(runtime, "MavlinkVisionBridge", factory)
    original = runtime.runtime_status_v3
    link = None
    def build_status(**kwargs):
        nonlocal link
        link = created.get(timeout=2)
        link.send_attitude(1000)
        return original(**kwargs)
    monkeypatch.setattr(runtime, "runtime_status_v3", build_status)
    monkeypatch.setattr(runtime, "_write_trace", lambda _, value: statuses.append(value))
    monkeypatch.setattr(runtime, "_send_udp", lambda *_: None)
    monkeypatch.setattr("sys.argv", ["shadow", "--map-release", "map.json", "--model-release", "model.json",
        "--calibration-id", "camera", "--mavlink-endpoint", "test", "--output-dir", str(tmp_path), "--once"])
    runtime.main()
    assert statuses[0]["attitude_health"]["status"] == "receiving"
    assert statuses[0]["compatibility"]["compatible"] is False
    assert statuses[0]["px4_publication"]["enabled"] is False
    assert link.closed.is_set()
