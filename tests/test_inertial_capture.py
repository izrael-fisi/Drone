import json
from types import SimpleNamespace

import pytest

from vision_nav.mavlink_bridge import MavlinkVisionBridge
from vision_nav.inertial_capture import summarize_inertial_capture
from vision_nav.training_capture import record_training_session, validate_training_session


def _sample(kind="HIGHRES_IMU", **fields):
    values = {"xacc": 1, "yacc": 2, "zacc": -9.8, "xgyro": 0.1, "ygyro": -0.2, "zgyro": 0.3,
              "time_usec": 1000000, "id": 0, "fields_updated": 63, **fields}
    message = SimpleNamespace(**values, get_type=lambda: kind, get_srcSystem=lambda: 1, get_srcComponent=lambda: 1)
    bridge = MavlinkVisionBridge("serial:/dev/test:921600")
    def receive(**kwargs):
        assert kind in kwargs["type"]
        return message
    bridge._conn = SimpleNamespace(recv_match=receive)
    return bridge.try_read_telemetry()


def test_highres_preserves_units_time_identity_and_update_mask():
    sample = _sample(id=2, fields_updated=7)
    assert sample.acceleration_m_s2 == (1, 2, -9.8)
    assert sample.angular_velocity_rad_s == (0.1, -0.2, 0.3)
    assert sample.vehicle_timestamp_us == 1000000
    assert sample.vehicle_time_domain == "highres_imu_boot_or_unknown_us"
    assert sample.imu_frame == "body_frd"
    assert sample.imu_sensor_id == 2
    assert sample.imu_fields_updated == 7
    assert sample.source_system == sample.source_component == 1
    assert json.loads(json.dumps(sample.to_dict()))["acceleration_m_s2"] == [1, 2, -9.8]


@pytest.mark.parametrize("kind,sensor", [("SCALED_IMU", 0), ("SCALED_IMU2", 1), ("SCALED_IMU3", 2)])
def test_scaled_values_convert_milligravity_and_milliradians(kind, sensor):
    sample = _sample(kind, xacc=1000, yacc=-1000, zacc=0, xgyro=1000, ygyro=-2000, zgyro=0, time_boot_ms=42)
    assert sample.acceleration_m_s2 == pytest.approx((9.80665, -9.80665, 0))
    assert sample.angular_velocity_rad_s == (1, -2, 0)
    assert sample.vehicle_timestamp_us == 42000
    assert sample.vehicle_time_domain == "px4_boot_us"
    assert sample.imu_sensor_id == sensor
    assert sample.imu_frame == "sender_axes_unverified"


def test_nonfinite_acceleration_not_promoted_to_measurement():
    sample = _sample(xacc=float("nan"))
    assert sample.acceleration_m_s2 is None
    report = summarize_inertial_capture([{"sample": sample.to_dict()}])
    assert report["streams"][0]["finite_vector_pair_count"] == 0
    assert report["vio_ready"] is False


def test_cadence_audit_keeps_sensors_separate_and_reports_clock_steps():
    samples = [_sample(time_usec=t, fields_updated=7 if i == 0 else 63) for i, t in enumerate([1000000, 1005000, 1005000, 900000])]
    samples.append(_sample(id=1, time_usec=2000000))
    report = summarize_inertial_capture([{"sample": sample.to_dict()} for sample in samples])
    first = report["streams"][0]
    assert len(report["streams"]) == 2
    assert first["observed_sensor_rate_hz"] == 200
    assert first["nonmonotonic_timestamp_count"] == 2
    assert first["all_six_axes_updated_count"] == 3
    assert report["vio_ready"] is False
    assert summarize_inertial_capture([])["status"] == "absent"


def test_training_capture_preserves_inertial_samples_and_summary(tmp_path):
    frames = tmp_path / "frames"
    frames.mkdir()
    (frames / "frame.jpg").write_bytes(b"replay-fixture")
    samples = iter([_sample(time_usec=1000000), _sample(time_usec=1005000)])
    capture = record_training_session(tmp_path / "capture", calibration_id="cam@1", map_release_id="map@1",
        capture_count=1, interval_s=0, input_frames_dir=frames, telemetry_reader=lambda: next(samples, None))
    assert capture["manifest"]["mavlink"]["inertial_capture"]["sample_count"] == 2
    assert capture["manifest"]["mavlink"]["inertial_capture"]["vio_ready"] is False
    recorded = [json.loads(line) for line in (tmp_path / "capture" / "telemetry" / "px4.jsonl").read_text().splitlines()]
    assert recorded[0]["sample"]["acceleration_m_s2"] == [1, 2, -9.8]
    assert validate_training_session(tmp_path / "capture", require_attitude=False)["status"] == "valid"
