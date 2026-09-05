from copy import deepcopy
from dataclasses import replace
import json
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from vision_nav.capture_timing import (
    TimingPolicy, audit_capture_timing, camera_exposure_time, fit_px4_clock, sample_host_clocks,
    audit_training_session_timing, main,
)
from vision_nav.camera_ingress import Picamera2Ingress
from vision_nav.mavlink_bridge import MavlinkVisionBridge, summarize_timesync_exchanges
from vision_nav.training_capture import record_training_session, validate_training_session


HOST = 1_000_000_000_000_000
OFFSET = -900_000_000_000_000
BOOT_OFFSET = 20_000_000_000


def remote(host, drift=20):
    return host + OFFSET + round((host - HOST) * drift * 1e-6)


def exchanges(drift=20):
    result = []
    for seconds in (0, 0.1, 0.2, 20, 20.1, 20.2):
        mid = HOST + round(seconds * 1e9)
        result.append({"host_sent_monotonic_ns": mid - 1_000_000,
                       "host_received_monotonic_ns": mid + 1_000_000,
                       "px4_timestamp_ns": remote(mid, drift),
                       "source_system": 1, "source_component": 1})
    return result


def clock_sample(host, offset=BOOT_OFFSET):
    return {"status": "observed", "monotonic_before_ns": host - 100,
            "monotonic_after_ns": host + 100, "boottime_ns": host + offset}


def camera_metadata(host):
    return {"sensor_timestamp_ns": host + BOOT_OFFSET, "sensor_clock_domain": "linux_boottime_ns",
            "sensor_timestamp_reference": "first_row_exposure_start", "exposure_time_us": 5000,
            "host_clock_samples": [clock_sample(host - 10_000_000), clock_sample(host + 20_000_000)]}


def session_evidence():
    host = HOST + 10_000_000_000
    frame = {"sequence": 0, "host_monotonic_ns": host + 250_000_000,
             "camera_metadata": camera_metadata(host)}
    telemetry = [{"index": 7, "host_monotonic_ns": host + 500_000_000,
                  "sample": {"message_type": "ATTITUDE", "source_system": 1, "source_component": 1,
                             "vehicle_time_domain": "px4_boot_us", "vehicle_timestamp_us": remote(host) // 1_000,
                             "roll_rad": 0.1, "pitch_rad": 0.2, "yaw_rad": 0.3}}]
    rows = exchanges()
    timing = {"pre_capture": {"exchanges": rows[:3]}, "post_capture": {"exchanges": rows[3:]}}
    return [frame], telemetry, timing


def test_affine_fit_recovers_offset_drift_without_large_timestamp_precision_loss():
    fit, audit = fit_px4_clock(exchanges())
    assert audit["status"] == "available"
    assert audit["drift_ppm"] == pytest.approx(20, abs=0.001)
    query = HOST + 8_000_000_000
    mapped = fit.convert(query, 100)
    assert abs(mapped["px4_timestamp_ns"] - remote(query)) <= 1
    assert mapped["clock_uncertainty_ns"] > 1_000_000
    assert audit["flight_ready"] is False
    assert json.loads(json.dumps(audit))["evidence_sha256"] == audit["evidence_sha256"]
    assert fit.convert(HOST - 1)["reason"] == "outside_clock_fit_interval"
    assert fit.convert(HOST, 1)["reason"] == "outside_clock_fit_interval"
    assert fit.convert(True)["status"] == "rejected"


@pytest.mark.parametrize("fault,reason", [
    ("source", "timesync_source_ambiguous_or_missing"),
    ("missing_source", "timesync_source_identity_missing"),
    ("duplicate", "clock_reset_duplicate_or_reordering"),
    ("reboot", "clock_reset_duplicate_or_reordering"),
    ("offset_step", "clock_step_or_inconsistent_offset"),
    ("drift", "clock_drift_exceeds_policy"),
    ("gap", "clock_observation_gap_too_large"),
    ("span", "clock_fit_span_too_short"),
])
def test_clock_fit_rejects_faults(fault, reason):
    rows = exchanges(1000 if fault == "drift" else 20)
    if fault == "source":
        rows[-1]["source_component"] = 2
    elif fault == "missing_source":
        rows[0].pop("source_system")
    elif fault == "duplicate":
        rows[1] = rows[0].copy()
    elif fault == "reboot":
        rows[-1]["px4_timestamp_ns"] = 10_000_000
    elif fault == "offset_step":
        rows[2]["px4_timestamp_ns"] += 10_000_000
    elif fault == "gap":
        for row in rows[3:]:
            for key in ("host_sent_monotonic_ns", "host_received_monotonic_ns", "px4_timestamp_ns"):
                row[key] += 100_000_000_000
    elif fault == "span":
        for index, row in enumerate(rows):
            mid = HOST + index * 10_000_000
            row.update(host_sent_monotonic_ns=mid - 1000, host_received_monotonic_ns=mid + 1000,
                       px4_timestamp_ns=remote(mid))
    fit, audit = fit_px4_clock(rows)
    assert fit is None
    assert reason in audit["errors"]


def test_high_rtt_excluded_and_uncertainty_not_hidden():
    rows = exchanges()
    slow = {**rows[0], "host_received_monotonic_ns": rows[0]["host_received_monotonic_ns"] + 100_000_000}
    fit, audit = fit_px4_clock([slow, *rows, {"reason": "timeout"}])
    assert fit
    assert audit["accepted_sample_count"] == 6
    assert len(audit["discarded_samples"]) == 2
    strict = replace(TimingPolicy(), max_conversion_uncertainty_ms=0.1)
    fit, _ = fit_px4_clock(rows, strict)
    assert fit.convert(HOST + 10_000_000_000)["reason"] == "clock_uncertainty_exceeds_policy"


@pytest.mark.parametrize("value", [float("nan"), float("inf"), 0, -1, True])
def test_timing_policy_rejects_invalid_limits(value):
    with pytest.raises(ValueError):
        TimingPolicy(max_drift_ppm=value)


def test_camera_sensor_clock_conversion_and_reference():
    host = HOST + 10_000_000_000
    mapped = camera_exposure_time(camera_metadata(host), TimingPolicy())
    assert mapped["host_monotonic_ns"] == host
    assert mapped["clock_uncertainty_ns"] == 100
    assert mapped["timestamp_reference"] == "first_row_exposure_start"
    assert mapped["full_frame_timing_available"] is False
    assert mapped["sensor_timing_calibrated"] is False


@pytest.mark.parametrize("fault,reason", [
    ("domain", "camera_sensor_clock_unverified"),
    ("missing", "camera_host_clock_brackets_missing"),
    ("wide", "host_clock_bracket_too_wide"),
    ("suspend", "host_suspend_or_clock_step"),
    ("stale", "camera_sensor_timestamp_outside_delivery_window"),
    ("future", "camera_sensor_timestamp_outside_delivery_window"),
])
def test_camera_clock_rejection(fault, reason):
    metadata = camera_metadata(HOST + 10_000_000_000)
    if fault == "domain":
        metadata["sensor_clock_domain"] = "unknown"
    elif fault == "missing":
        metadata.pop("host_clock_samples")
    elif fault == "wide":
        metadata["host_clock_samples"][0]["monotonic_after_ns"] += 10_000_000
    elif fault == "suspend":
        metadata["host_clock_samples"][1]["boottime_ns"] += 1_000_000_000
    elif fault == "stale":
        metadata["sensor_timestamp_ns"] -= 2_000_000_000
    elif fault == "future":
        metadata["sensor_timestamp_ns"] += 2_000_000_000
    assert camera_exposure_time(metadata, TimingPolicy())["reason"] == reason


def test_pairing_uses_exposure_and_native_attitude_not_receive_or_file_time():
    frames, telemetry, timing = session_evidence()
    original = deepcopy(frames)
    report = audit_capture_timing(frames, telemetry, timing)
    assert report["status"] == "available"
    row = report["frames"][0]
    assert row["telemetry_index"] == 7
    assert row["native_delta_ms"] < 0.001
    assert row["total_timing_envelope_ms"] > 1
    assert report["vio_ready"] is report["flight_ready"] is False
    assert frames == original


@pytest.mark.parametrize("fault,reason", [
    ("source", "compatible_native_attitude_missing"),
    ("domain", "compatible_native_attitude_missing"),
    ("old", "native_attitude_age_exceeds_policy"),
    ("duplicate_attitude", "attitude_clock_reset_duplicate_or_reordering"),
    ("duplicate_frame", "camera_timestamp_missing_duplicate_or_reordered"),
    ("between_suspend", "host_suspend_or_clock_step_between_frames"),
])
def test_alignment_rejection(fault, reason):
    frames, telemetry, timing = session_evidence()
    if fault == "source":
        telemetry[0]["sample"]["source_system"] = 2
    elif fault == "domain":
        telemetry[0]["sample"]["vehicle_time_domain"] = "unix_epoch_us"
    elif fault == "old":
        telemetry[0]["sample"]["vehicle_timestamp_us"] -= 1_000_000
    elif fault == "duplicate_attitude":
        telemetry.append(deepcopy(telemetry[0]))
    elif fault == "duplicate_frame":
        frames.append(deepcopy(frames[0]))
    elif fault == "between_suspend":
        new = deepcopy(frames[0])
        new["camera_metadata"] = camera_metadata(HOST + 11_000_000_000)
        new["camera_metadata"]["sensor_timestamp_ns"] += 1_000_000_000
        for sample in new["camera_metadata"]["host_clock_samples"]:
            sample["boottime_ns"] += 1_000_000_000
        frames.append(new)
    report = audit_capture_timing(frames, telemetry, timing)
    assert report["status"] == "incomplete"
    assert report["frames"][-1]["reason"] == reason


@pytest.mark.parametrize("memory", [False, True])
def test_picamera2_preserves_bgr_pixels_before_releasing_request(tmp_path, monkeypatch, memory):
    source = np.zeros((8, 8, 3), dtype=np.uint8)
    source[:] = (11, 73, 221)
    class Request:
        def get_metadata(self):
            return {"SensorTimestamp": HOST + BOOT_OFFSET, "ExposureTime": 1000}
        def make_array(self, name):
            assert name == "main"
            return source
        def release(self):
            source[:] = 0
    ingress = Picamera2Ingress.__new__(Picamera2Ingress)
    ingress._camera = SimpleNamespace(capture_request=lambda: Request())
    clocks = iter([clock_sample(HOST - 10_000_000), clock_sample(HOST + 20_000_000)])
    monkeypatch.setattr("vision_nav.camera_ingress.sample_host_clocks", lambda: next(clocks))
    frame = (ingress.capture_memory if memory else ingress.capture)(tmp_path / "color.png")
    if memory:
        assert not frame.path.exists()
        assert frame.bgr[0, 0].tolist() == [11, 73, 221]
        assert frame.bgr.flags.owndata and not frame.bgr.flags.writeable
        assert frame.quality["status"] == "measured"
    else:
        assert frame.bgr is None
        assert cv2.imread(str(frame.path))[0, 0].tolist() == [11, 73, 221]
    assert "bgr" not in frame.to_dict()
    assert camera_exposure_time(frame.to_dict(), TimingPolicy())["status"] == "available"


def test_host_clock_sampling_has_no_wall_clock_fallback(monkeypatch):
    import vision_nav.capture_timing as module
    monkeypatch.delattr(module.time, "CLOCK_BOOTTIME", raising=False)
    assert sample_host_clocks()["reason"] == "linux_boottime_unavailable"
    monkeypatch.setattr(module.time, "CLOCK_BOOTTIME", 7, raising=False)
    times = iter([1000, 1100])
    monkeypatch.setattr(module.time, "monotonic_ns", lambda: next(times))
    monkeypatch.setattr(module.time, "clock_gettime_ns", lambda _: 2050)
    assert sample_host_clocks() == {"status": "observed", "monotonic_before_ns": 1000,
                                    "monotonic_after_ns": 1100, "boottime_ns": 2050}


def test_timesync_identity_preserved_and_other_targets_ignored():
    class Connection:
        def __init__(self):
            self.mav = SimpleNamespace(timesync_send=self.send)
            self.replies = []
        def send(self, tc1, ts1):
            self.replies = [SimpleNamespace(ts1=ts1, tc1=ts1 - 10000, target_system=target,
                target_component=197, get_srcSystem=lambda: 1, get_srcComponent=lambda: 1)
                for target in (99, 42)]
        def recv_match(self, **kwargs):
            return self.replies.pop(0)
    bridge = MavlinkVisionBridge("udp:14550")
    bridge._conn = Connection()
    report = bridge.capture_timesync_evidence(sample_count=3)
    assert report["status"] == "available"
    assert all(row["source_system"] == row["source_component"] == 1 for row in report["exchanges"])
    assert all(row["response_target_system"] == 42 for row in report["exchanges"])
    summary = summarize_timesync_exchanges(exchanges(), requested_sample_count=6, max_round_trip_ms=10)
    assert fit_px4_clock(summary["exchanges"])[0] is not None


def test_recorded_session_reports_missing_timing_without_fabricating_sync(tmp_path):
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    cv2.imwrite(str(inputs / "frame.png"), np.zeros((8, 8, 3), np.uint8))
    result = record_training_session(tmp_path / "session", calibration_id="cam@1", map_release_id="map@1",
                                    capture_count=1, interval_s=0, input_frames_dir=inputs)
    audit = result["manifest"]["mavlink"]["exposure_alignment"]
    assert audit["status"] == "incomplete"
    assert audit["paired_frame_count"] == 0
    assert audit["vio_ready"] is False
    assert validate_training_session(tmp_path / "session", require_attitude=False)["status"] == "valid"
    report = audit_training_session_timing(tmp_path / "session")
    assert report["source_checksums"] == "passed"
    assert report["status"] == "incomplete"
    assert main(["--session", str(tmp_path / "session")]) == 2
    (tmp_path / "session" / "telemetry" / "px4.jsonl").write_text("{}\n")
    with pytest.raises(ValueError, match="checksum"):
        audit_training_session_timing(tmp_path / "session")


def test_periodic_timesync_multiplexes_attitude_on_one_reader(monkeypatch):
    now = [HOST]
    monkeypatch.setattr("vision_nav.mavlink_bridge.time.monotonic_ns", lambda: now[0])
    class Connection:
        def __init__(self):
            self.requests = []
            self.replies = []
            self.mav = SimpleNamespace(timesync_send=self.send)
        def send(self, tc1, ts1):
            self.requests.append((tc1, ts1))
        def recv_match(self, **kwargs):
            assert "TIMESYNC" in kwargs["type"]
            assert "ATTITUDE" in kwargs["type"]
            return self.replies.pop(0) if self.replies else None
    bridge = MavlinkVisionBridge("udp:14550")
    conn = Connection()
    bridge._conn = conn
    bridge.start_timesync_recording()
    assert bridge.try_read_telemetry() is None
    assert len(conn.requests) == 1
    now[0] += 1_000_000
    conn.replies.append(SimpleNamespace(get_type=lambda: "ATTITUDE", time_boot_ms=123,
        roll=0.1, pitch=0.2, yaw=0.3, get_srcSystem=lambda: 1, get_srcComponent=lambda: 1))
    attitude = bridge.try_read_telemetry()
    assert attitude.roll_rad == 0.1
    assert attitude.source_system == attitude.source_component == 1
    now[0] += 1_000_000
    conn.replies.append(SimpleNamespace(get_type=lambda: "TIMESYNC", ts1=HOST, tc1=remote(HOST + 1_000_000),
        get_srcSystem=lambda: 1, get_srcComponent=lambda: 1))
    assert bridge.try_read_telemetry() is None
    now[0] = HOST + 1_000_000_000
    bridge.try_read_telemetry()
    assert len(conn.requests) == 2
    now[0] += 300_000_000
    bridge.try_read_telemetry()
    report = bridge.stop_timesync_recording()
    assert report["exchanges"][0]["host_received_monotonic_ns"] == HOST + 2_000_000
    assert report["exchanges"][1]["reason"] == "timesync_response_timeout"
    assert report["capacity_reached"] is False
    assert all(request[0] == 0 for request in conn.requests)


def test_periodic_recorder_bounds_memory_and_keeps_probe_errors():
    bridge = MavlinkVisionBridge("udp:14550")
    def failed(*args):
        raise OSError("disconnected")
    bridge._conn = SimpleNamespace(mav=SimpleNamespace(timesync_send=failed))
    bridge.start_timesync_recording(max_exchanges=1)
    bridge._poll_timesync_recording(HOST)
    bridge._poll_timesync_recording(HOST + 1_000_000_000)
    report = bridge.stop_timesync_recording()
    assert len(report["exchanges"]) == 1
    assert report["capacity_reached"] is True
    assert report["exchanges"][0]["reason"] == "timesync_request_failed:OSError"
    with pytest.raises(ValueError):
        bridge.start_timesync_recording(interval_s=1, timeout_s=2)


def test_periodic_incremental_clock_read_is_nonconsuming_and_owned_copy():
    bridge = MavlinkVisionBridge("udp:14550")
    with pytest.raises(RuntimeError):
        bridge.read_timesync_recording()
    bridge.start_timesync_recording()
    bridge._timesync_recording["exchanges"].extend(exchanges()[:2])
    first = bridge.read_timesync_recording()
    assert first["next_cursor"] == 2
    first["exchanges"][0]["px4_timestamp_ns"] = -1
    assert bridge.read_timesync_recording()["exchanges"][0]["px4_timestamp_ns"] > 0
    assert bridge.read_timesync_recording(2)["exchanges"] == []
    for bad in (-1, True, 3):
        with pytest.raises(ValueError):
            bridge.read_timesync_recording(bad)
    assert len(bridge.stop_timesync_recording()["exchanges"]) == 2


def test_periodic_clock_evidence_covers_long_capture_gap():
    rows = exchanges()
    for row in rows[3:]:
        for key in ("host_sent_monotonic_ns", "host_received_monotonic_ns"):
            row[key] += 60_000_000_000
        mid = (row["host_sent_monotonic_ns"] + row["host_received_monotonic_ns"]) // 2
        row["px4_timestamp_ns"] = remote(mid)
    frames, telemetry, _ = session_evidence()
    timing = {"pre_capture": {"exchanges": rows[:3]}, "post_capture": {"exchanges": rows[3:]}}
    assert audit_capture_timing(frames, telemetry, timing)["clock_fit"]["status"] == "rejected"
    periodic = []
    for sec in range(1, 80):
        mid = HOST + sec * 1_000_000_000
        periodic.append({**rows[0], "host_sent_monotonic_ns": mid - 1_000_000,
                         "host_received_monotonic_ns": mid + 1_000_000, "px4_timestamp_ns": remote(mid)})
    timing["during_capture"] = {"exchanges": periodic}
    assert audit_capture_timing(frames, telemetry, timing)["status"] == "available"
