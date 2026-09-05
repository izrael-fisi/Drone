"""Owned device interfaces with synthetic arrivals and actual native VIO."""

from copy import deepcopy
import json
import os
from pathlib import Path
import threading
import time

import cv2
import pytest

from vision_nav.camera_ingress import CapturedFrame
from vision_nav.mavlink_bridge import MavlinkTelemetrySample
from vision_nav.vio.sensor_session import LiveSensorSession, main
from test_prepare_vio_capture import sources, CHECKOUT
from test_vio_stream_worker import wait_for
from test_live_sensor_buffer import exchange, sample


class SyntheticDevices:
    """Known synthetic clocks/physics; not a hardware timing emulation claim."""

    def __init__(self, sources, *, clock=True, camera_failure=None, close_failure=False, freeze_clock=False):
        self.session, self.profile, self.rendered = sources
        self.frames = json.loads((self.session / "manifest.json").read_text())["frames"]
        self.operations = {"camera": [], "link": []}
        self.clock, self.camera_failure, self.close_failure, self.freeze_clock = clock, camera_failure, close_failure, freeze_clock
        self.camera_closed = self.link_closed = False
        self.link_count = self.camera_count = 0

    def record(self, owner, operation):
        self.operations[owner].append((operation, threading.get_ident()))

    def link_factory(self):
        self.record("link", "create")
        self.link_count += 1
        rig = self
        class Link:
            def __init__(self):
                self.next_imu = 2_990_000_000
                self.next_clock = 3_100_000_000
                self.rows = []
                self.started = time.monotonic()
            def connect(self):
                rig.record("link", "connect")
            def start_timesync_recording(self, **kwargs):
                rig.record("link", "start_timesync")
                assert kwargs["interval_s"] == .2
                if rig.clock:
                    self.rows = [exchange(ns) for ns in
                        (1_800_000_000, 2_000_000_000, 2_200_000_000, 2_400_000_000, 2_600_000_000, 2_900_000_000)]
            def try_read_telemetry(self, timeout_s):
                rig.record("link", "read")
                due = self.started+(self.next_imu-2_990_000_000)/1e9
                time.sleep(max(0., due-time.monotonic()))
                ns = self.next_imu
                self.next_imu += 5_000_000
                if rig.clock and not rig.freeze_clock and ns >= self.next_clock:
                    self.rows.append(exchange(self.next_clock))
                    self.next_clock += 200_000_000
                values = sample(ns)
                seconds = (ns-3_000_000_000)/1e9
                values["acceleration_m_s2"] = [4. if 3. <= seconds < 3.5 else -4. if 5. <= seconds < 5.5 else 0., 0., 9.81]
                return MavlinkTelemetrySample(timestamp_us=1, **values)
            def read_timesync_recording(self, cursor):
                rig.record("link", "read_clock")
                return {"next_cursor": len(self.rows), "exchanges": deepcopy(self.rows[cursor:]), "capacity_reached": False}
            def stop_timesync_recording(self):
                rig.record("link", "stop_timesync")
                return {"exchanges": self.rows, "synthetic": True}
            def close(self):
                rig.record("link", "close")
                rig.link_closed = True
                if rig.close_failure:
                    raise OSError("synthetic close failure")
        return Link()

    def camera_factory(self):
        self.record("camera", "create")
        self.camera_count += 1
        rig = self
        class Camera:
            def __init__(self):
                self.index = 0
            def capture_memory(self, output):
                rig.record("camera", "capture")
                if rig.camera_failure == self.index:
                    raise OSError("synthetic camera failure")
                row = rig.frames[self.index]
                self.index += 1
                metadata = deepcopy(row["camera_metadata"])
                metadata["sensor_timestamp_ns"] += 2_000_000_000
                for bracket in metadata["host_clock_samples"]:
                    for key in ("monotonic_before_ns", "monotonic_after_ns", "boottime_ns"):
                        bracket[key] += 2_000_000_000
                pixels = cv2.imread(str(rig.session / row["path"]), cv2.IMREAD_UNCHANGED)
                return CapturedFrame(output, "picamera2", bgr=pixels, **metadata)
            def close(self):
                rig.record("camera", "close")
                rig.camera_closed = True
        return Camera()


@pytest.fixture
def setup(sources, tmp_path):
    binary = os.environ.get("VISION_NAV_OPENVINS_BINARY")
    if not binary:
        pytest.skip("Actual native build required")
    def create(*, rig=None, **kwargs):
        rig = rig or SyntheticDevices(sources)
        session = LiveSensorSession(Path(binary), CHECKOUT, sources[1], tmp_path / "live-session",
            camera_factory=rig.camera_factory, link_factory=rig.link_factory, **kwargs)
        return session, rig
    return create


def test_owned_sensor_threads_feed_native_while_learned_consumer_waits(setup):
    session, rig = setup(frame_count=160, duration_s=20.)
    session.start()
    try:
        wait_for(lambda: session.status()["delivered_frames"] >= 8, timeout=10)
        first = session.take_latest_window()
        assert first and len(first["tickets"]) == 2
        first_bytes = first["tickets"][0].pixels.tobytes()
        first_sequence = first["tickets"][-1].sequence
        # No calls from the learned consumer are needed to keep either the
        # physical-owner interfaces or native ingestion making progress.
        wait_for(lambda: session.status()["delivered_frames"] >= 30, timeout=10)
        assert session.status()["native"]["completed_batch_count"] > first_sequence
        assert first["tickets"][0].pixels.tobytes() == first_bytes
        assert session.join(25.), session.status()
        status = session.status()
        assert status["state"] == "complete", status
        assert status["camera_frames"] == status["delivered_frames"] == 160
        assert status["native"]["completed_batch_count"] == 160
        assert status["native"]["local_state_counts"]["LOCAL_ESTIMATE"] > 30
        assert status["native"]["backpressure_count"] == 0
        assert status["superseded_learned_windows"] > 0
        assert status["buffer"]["peak_frames"] <= 16
        final = session.take_latest_window()
        assert final["tickets"][-1].sequence == 159
        assert final["source_session_state"] == "complete" and not final["position_authority"]
        assert rig.link_closed and rig.camera_closed
        assert rig.camera_count == rig.link_count == 1
        camera_threads = {t for _, t in rig.operations["camera"]}
        link_threads = {t for _, t in rig.operations["link"]}
        assert len(camera_threads) == len(link_threads) == 1 and camera_threads.isdisjoint(link_threads)
        assert threading.get_ident() not in camera_threads | link_threads
        assert not status["camera_thread_alive"] and not status["link_thread_alive"]
        assert not list((session.native.capture / "images").iterdir())
        report = json.loads((session.root / "session_report.json").read_text())
        assert report["state"] == "complete" and report["position_authority"] is False
        assert len(report["python_adapter_sha256"]) == 4
    finally:
        assert session.stop()


def test_warmup_missing_clock_never_opens_camera_or_native(setup, sources):
    rig = SyntheticDevices(sources, clock=False)
    session, rig = setup(rig=rig, warmup_timeout_s=1.)
    session.start()
    assert session.join(5)
    status = session.status()
    assert status["state"] == "failed" and "warmup" in status["error"]
    assert rig.camera_count == 0 and rig.link_closed
    assert session.native._process is None


@pytest.mark.parametrize("fault", ["camera_failure", "clock_loss", "close_failure"])
def test_owner_fault_stops_native_and_clears_learned_window(setup, sources, fault):
    rig = SyntheticDevices(sources, camera_failure=3 if fault == "camera_failure" else None,
        close_failure=fault == "close_failure", freeze_clock=fault == "clock_loss")
    session, rig = setup(rig=rig, frame_count=30)
    session.start()
    try:
        assert session.join(10), session.status()
        status = session.status()
        assert status["state"] == "failed" and status["error"], status
        assert session.take_latest_window() is None
        assert rig.link_closed and rig.camera_closed
        assert session.native._process.poll() is not None
        assert not status["position_authority"]
    finally:
        assert session.stop()


def test_cli_requires_explicit_prop_off_attestation_before_setup(tmp_path):
    with pytest.raises(SystemExit) as exc:
        main(["--binary", "missing", "--checkout", "missing", "--sensor-profile", "missing",
              "--output", str(tmp_path / "unused"), "--endpoint", "serial:/dev/ttyACM0:115200"])
    assert exc.value.code == 2
    assert not (tmp_path / "unused").exists()


def test_native_final_drain_does_not_require_intentionally_closed_sensor_input(setup, monkeypatch):
    session, rig = setup(frame_count=2)
    original = session.native._read_result
    def delayed(reader, deadline, stamp):
        if session.native.status()["completed_batch_count"] == 1:
            time.sleep(1.2)
        return original(reader, deadline, stamp)
    monkeypatch.setattr(session.native, "_read_result", delayed)
    session.start()
    try:
        assert session.join(8)
        assert session.status()["state"] == "completed_without_local_estimate", session.status()
        assert rig.link_closed and rig.camera_closed
        assert session.status()["native"]["completed_batch_count"] == 2
    finally:
        assert session.stop()


def test_stop_while_owners_active_reaps_and_invalidates_window(setup):
    session, rig = setup(frame_count=160)
    session.start()
    try:
        wait_for(lambda: session.status()["delivered_frames"] >= 4, timeout=10)
        assert session.stop(8)
        assert session.take_latest_window() is None
        assert session.status()["state"] in {"stopped", "failed"}
        assert rig.link_closed and rig.camera_closed
        assert session.native._process.poll() is not None
    finally:
        assert session.stop()


def test_clock_releases_burst_without_native_retry_or_dropped_frames(setup, sources, monkeypatch):
    rig = SyntheticDevices(sources, freeze_clock=True)
    session, rig = setup(rig=rig, frame_count=12)
    original = session.native._read_result
    def delayed(*args):
        time.sleep(.03)
        return original(*args)
    monkeypatch.setattr(session.native, "_read_result", delayed)
    session.start()
    try:
        wait_for(lambda: session.status()["camera_frames"] >= 6, timeout=5)
        assert session.status()["delivered_frames"] == 0
        rig.freeze_clock = False
        assert session.join(10), session.status()
        status = session.status()
        assert status["state"] == "completed_without_local_estimate", status
        assert status["delivered_frames"] == status["native"]["completed_batch_count"] == 12
        assert status["buffer"]["peak_frames"] >= 6
        assert status["native"]["backpressure_count"] == 0
    finally:
        assert session.stop()


def test_terminal_evidence_write_failure_cannot_claim_complete(setup, monkeypatch):
    session, _ = setup(frame_count=2)
    original = Path.write_text
    def fail_report(path, *args, **kwargs):
        if path.name == "session_report.json":
            raise OSError("synthetic disk failure")
        return original(path, *args, **kwargs)
    monkeypatch.setattr(Path, "write_text", fail_report)
    session.start()
    try:
        assert session.join(10)
        assert session.status()["state"] == "failed"
        assert "report_write_failed" in session.status()["error"]
        assert session.take_latest_window() is None
    finally:
        assert session.stop()
