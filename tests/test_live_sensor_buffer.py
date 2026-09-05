"""Causal clock rendezvous and bounded ordered sensor ownership."""

from copy import deepcopy
from dataclasses import replace

import numpy as np
import pytest

from vision_nav.camera_ingress import CapturedFrame
from vision_nav.vio.sensor_buffer import LiveSensorBuffer

HOST = 20_000_000_000
BOOT = 5_000_000_000
PROFILE = {"source_system": 1, "source_component": 1, "imu_sensor_id": 0, "message_type": "HIGHRES_IMU"}
CAMERA = {"resolution": [8, 6], "timeshift_cam_imu": 0.}


def sample(ns):
    return {**PROFILE, "vehicle_timestamp_us": ns//1000, "vehicle_time_domain": "px4_boot_us",
        "imu_fields_updated": 63, "imu_frame": "body_frd", "acceleration_m_s2": [0., 0., 9.81],
        "angular_velocity_rad_s": [0., 0., 0.]}


def exchange(ns):
    return {"host_sent_monotonic_ns": HOST+ns-10000, "host_received_monotonic_ns": HOST+ns+10000,
        "px4_timestamp_ns": ns, "source_system": 1, "source_component": 1}


def frame(ns):
    from pathlib import Path
    host = HOST+ns
    def bracket(at):
        return {"monotonic_before_ns": at-100, "monotonic_after_ns": at+100, "boottime_ns": at+BOOT}
    return CapturedFrame(Path("not-written.png"), "picamera2", bgr=np.ones((6, 8, 3), np.uint8),
        sensor_timestamp_ns=host+BOOT, sensor_clock_domain="linux_boottime_ns",
        sensor_timestamp_reference="first_row_exposure_start",
        host_clock_samples=[bracket(host-1000), bracket(host+1_000_000)])


def warmed(**kwargs):
    now = [HOST+2_990_000_000]
    buffer = LiveSensorBuffer(PROFILE, CAMERA, clock_ns=lambda: now[0], **kwargs)
    for ns in (1_800_000_000, 2_000_000_000, 2_200_000_000, 2_400_000_000, 2_600_000_000, 2_900_000_000):
        buffer.push_exchange(exchange(ns))
    buffer.push_telemetry(sample(2_990_000_000))
    assert buffer.ready()
    buffer.arm()
    return buffer, now


def test_camera_waits_for_future_clock_exchange_and_raw_imu_then_dispatches_once():
    buffer, now = warmed()
    image = frame(3_000_000_000)
    buffer.push_frame(image)
    assert buffer.take_batch() is None
    buffer.push_exchange(exchange(3_100_000_000))
    assert buffer.take_batch() is None
    for ns in range(2_995_000_000, 3_020_000_000, 5_000_000):
        buffer.push_telemetry(sample(ns))
    image.bgr[:] = 9
    batch = buffer.take_batch()
    assert np.all(batch["frame"].bgr == 1)
    with pytest.raises(ValueError):
        batch["frame"].bgr.setflags(write=True)
    assert batch["clock_fit"].convert(HOST+3_000_000_000)["px4_timestamp_ns"] == 3_000_000_000
    stamps = [s["vehicle_timestamp_us"] for s in batch["imu_samples"]]
    assert stamps == [2_990_000, 2_995_000, 3_000_000, 3_005_000, 3_010_000]
    assert buffer.take_batch() is None
    assert buffer.status()["pending_frame_bytes"] == 0
    assert buffer.status()["pending_imu"] == 1
    buffer.push_frame(frame(3_050_000_000))
    for ns in range(3_020_000_000, 3_070_000_000, 5_000_000):
        buffer.push_telemetry(sample(ns))
    second = buffer.take_batch()
    assert second["imu_samples"][0]["vehicle_timestamp_us"] == 3_015_000


@pytest.mark.parametrize("fault", ["imu_reorder", "imu_gap", "imu_partial", "camera_reorder", "camera_gap",
    "camera_overflow", "imu_overflow", "clock_reboot", "clock_step", "clock_wait", "imu_stale"])
def test_sensor_fault_latches_and_releases_pending_frames(fault):
    buffer, now = warmed(frame_capacity=1, imu_capacity=32)
    buffer.push_frame(frame(3_000_000_000))
    with pytest.raises((ValueError, RuntimeError)):
        if fault == "imu_reorder":
            buffer.push_telemetry(sample(2_990_000_000))
        elif fault == "imu_gap":
            buffer.push_telemetry(sample(3_100_000_000))
        elif fault == "imu_partial":
            buffer.push_telemetry({**sample(2_995_000_000), "imu_fields_updated": 1})
        elif fault == "camera_reorder":
            buffer.push_frame(frame(3_000_000_000))
        elif fault == "camera_gap":
            buffer.push_frame(frame(3_500_000_000))
        elif fault == "camera_overflow":
            buffer.push_frame(frame(3_050_000_000))
        elif fault == "imu_overflow":
            for ns in range(2_995_000_000, 3_200_000_000, 5_000_000):
                buffer.push_telemetry(sample(ns))
        elif fault == "clock_reboot":
            buffer.push_exchange({**exchange(3_100_000_000), "px4_timestamp_ns": 1000})
        elif fault == "clock_step":
            buffer.push_exchange({**exchange(3_100_000_000), "px4_timestamp_ns": 3_130_000_000})
        else:
            now[0] += 1_100_000_000
            if fault == "clock_wait":
                buffer.push_telemetry(sample(2_995_000_000))
            buffer.take_batch()
    assert buffer.status()["error"]
    assert buffer.status()["pending_frame_bytes"] == buffer.status()["pending_imu"] == 0
    with pytest.raises(RuntimeError):
        buffer.take_batch()


def test_initial_only_imu_eviction_is_counted_not_hidden_after_arming():
    now = [HOST+3_000_000_000]
    buffer = LiveSensorBuffer(PROFILE, CAMERA, imu_capacity=32, clock_ns=lambda: now[0])
    for ns in range(2_000_000_000, 3_000_000_000, 5_000_000):
        buffer.push_telemetry(sample(ns))
    assert buffer.status()["warmup_imu_discarded"] == 168
    for ns in (1_800_000_000, 2_000_000_000, 2_200_000_000, 2_400_000_000, 2_600_000_000, 3_100_000_000):
        buffer.push_exchange(exchange(ns))
    # Native cold-start batch trimming must not be confused with dropping
    # information from an already initialized native filter.
    buffer.arm()
    buffer.push_frame(frame(2_900_000_000))
    batch = buffer.take_batch()
    assert batch is not None and buffer.status()["initial_imu_discarded"] > 0


def test_other_sensor_gps_and_bad_round_trip_do_not_become_clock_or_inertial_input():
    buffer, _ = warmed()
    before = buffer.status()
    assert not buffer.push_telemetry({**sample(2_995_000_000), "source_system": 2})
    assert not buffer.push_telemetry({"message_type": "GPS_RAW_INT", "lat": 1.})
    buffer.push_exchange({**exchange(3_100_000_000), "source_system": 2})
    buffer.push_exchange({**exchange(3_100_000_000), "host_received_monotonic_ns": HOST+3_200_000_000})
    assert buffer.status()["accepted_imu"] == before["accepted_imu"]
    assert buffer.status()["clock_samples"] == before["clock_samples"]
    assert buffer.status()["invalid_clock_exchanges"] == 2


def test_clock_history_is_bounded_and_cannot_extrapolate():
    buffer, _ = warmed(clock_capacity=8)
    for ns in range(3_100_000_000, 9_100_000_000, 200_000_000):
        buffer.push_exchange(exchange(ns))
    assert buffer.status()["clock_samples"] == 8
    assert buffer._fit.convert(HOST+9_500_000_000)["status"] == "rejected"


def test_camera_byte_limit_is_independent_of_count_limit():
    buffer, _ = warmed(frame_capacity=16, frame_bytes=1024)
    for index in range(7):
        buffer.push_frame(frame(3_000_000_000+index*50_000_000))
    assert buffer.status()["pending_frames"] == 7
    with pytest.raises(ValueError, match="camera_buffer_overflow"):
        buffer.push_frame(frame(3_350_000_000))
    assert buffer.status()["pending_frame_bytes"] == 0


@pytest.mark.parametrize("kwargs", [{"frame_capacity": True}, {"frame_bytes": 0}, {"imu_capacity": 100000},
    {"clock_capacity": 3}, {"wait_ms": float("inf")}])
def test_invalid_bounds(kwargs):
    with pytest.raises(ValueError):
        LiveSensorBuffer(PROFILE, CAMERA, **kwargs)
