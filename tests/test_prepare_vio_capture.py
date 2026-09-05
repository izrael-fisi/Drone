"""Camera/PX4 conversion with controlled clocks; no real sensor qualification."""

import json
from copy import deepcopy
import os
from pathlib import Path
import shutil

import pytest

from vision_nav.training_capture import write_session_checksums
from vision_nav.vio.openvins_replay import make_synthetic, read_yaml, write_yaml, digest, run
from vision_nav.vio.prepare_capture import prepare_capture

ROOT = Path(__file__).resolve().parents[1]
CHECKOUT = ROOT / "research/vendor/open_vins"
HOST_OFFSET = 20_000_000_000
BOOT_OFFSET = 5_000_000_000


@pytest.fixture
def sources(tmp_path):
    if not (CHECKOUT / ".git").exists():
        pytest.skip("Pinned native OpenVINS calibration source required")
    rendered = tmp_path / "rendered"
    make_synthetic(CHECKOUT, rendered)
    session, profile = tmp_path / "session", tmp_path / "profile"
    session.mkdir()
    profile.mkdir()
    shutil.copytree(rendered / "images", session / "images")
    events = [json.loads(line) for line in (rendered / "events.jsonl").read_text().splitlines()]
    frames, telemetry = [], []
    for event in events:
        ns = event["timestamp_ns"]
        host = ns + HOST_OFFSET
        if event["type"] == "imu":
            telemetry.append({"sample": {"message_type": "HIGHRES_IMU", "source_system": 1, "source_component": 1,
                "imu_sensor_id": 0, "imu_fields_updated": 63, "imu_frame": "body_frd",
                "vehicle_timestamp_us": ns // 1000, "vehicle_time_domain": "px4_boot_us",
                "acceleration_m_s2": event["specific_force_m_s2"], "angular_velocity_rad_s": event["angular_velocity_rad_s"]}})
        else:
            def bracket(at):
                return {"monotonic_before_ns": at - 100, "monotonic_after_ns": at + 100,
                        "boottime_ns": at + BOOT_OFFSET}
            frames.append({"sequence": len(frames), "path": event["image_path"], "camera_metadata": {
                "sensor_timestamp_ns": host + BOOT_OFFSET, "sensor_clock_domain": "linux_boottime_ns",
                "sensor_timestamp_reference": "first_row_exposure_start",
                "host_clock_samples": [bracket(host - 1000), bracket(host + 1_000_000)]}})
    # Bracket the first exposure's clock envelope, not just its nominal time.
    prefix = []
    for offset_us in (10000, 5000):
        row = deepcopy(telemetry[0])
        row["sample"]["vehicle_timestamp_us"] -= offset_us
        prefix.append(row)
    telemetry = prefix + telemetry
    # These intentionally remain outside estimator inputs.
    telemetry.append({"sample": {"message_type": "GPS_RAW_INT", "lat_deg": 12.345}})
    (session / "telemetry.jsonl").write_text("".join(json.dumps(row) + "\n" for row in telemetry))
    exchanges = [{"host_sent_monotonic_ns": HOST_OFFSET + ns - 10000,
                  "host_received_monotonic_ns": HOST_OFFSET + ns + 10000,
                  "px4_timestamp_ns": ns, "source_system": 1, "source_component": 1}
                 for ns in range(900_000_000, 10_000_000_000, 1_000_000_000)]
    manifest = {"schema_version": "vision_nav_training_capture_v1", "session_id": "sensor-fixture-not-aircraft",
        "calibration": {"calibration_id": "fixture-camera"}, "map_release_id": "unused-map",
        "training_policy": {"gnss_truth_policy": "offline_training_only"}, "frames": frames,
        "frame_count": len(frames), "telemetry_path": "telemetry.jsonl",
        "mavlink": {"timesync": {"during_capture": {"exchanges": exchanges}}}}
    (session / "manifest.json").write_text(json.dumps(manifest))
    write_session_checksums(session)
    for name in ("kalibr_imucam_chain.yaml", "kalibr_imu_chain.yaml"):
        shutil.copy2(rendered / name, profile / name)
    camera = read_yaml(profile / "kalibr_imucam_chain.yaml")
    camera["cam0"]["timeshift_cam_imu"] = 0.
    write_yaml(profile / "kalibr_imucam_chain.yaml", camera)
    (profile / "qualification.txt").write_text("Synthetic sensor fixture only. Not real measured hardware calibration.")
    description = {"schema_version": "vision_nav_openvins_sensor_profile_v1", "calibration_status": "measured",
        "profile_id": "fixture", "camera_calibration_id": "fixture-camera", "firmware_revision": "fixture",
        "qualification_notes": "Synthetic test exercising required measured-profile fields, not hardware evidence.",
        "camera_shutter": "global", "camera_timestamp_reference": "first_row_exposure_start",
        "imu_frame": "body_frd", "imu_measurement": "specific_force_including_gravity",
        "imu_intrinsics_policy": "sender_corrected_no_second_correction",
        "timesync_clock_matches_imu_boot_clock": True, "source_system": 1, "source_component": 1,
        "imu_sensor_id": 0, "message_type": "HIGHRES_IMU", "gravity_m_s2": 9.81,
        "qualification_evidence": ["qualification.txt"], "checksums": {}}
    for p in profile.iterdir():
        description["checksums"][p.name] = digest(p)
    (profile / "sensor_profile.json").write_text(json.dumps(description))
    return session, profile, rendered


def test_pi_capture_conversion_preserves_sensor_values_and_excludes_gps(sources, tmp_path):
    session, profile, rendered = sources
    output = tmp_path / "converted"
    report = prepare_capture(CHECKOUT, session, profile, output)
    assert report["position_authority"] is False
    assert report["source"]["ground_truth_used_by_frontend"] is False
    assert report["source"]["ignored_nonselected_telemetry_records"] == 1
    events = [json.loads(line) for line in (output / "events.jsonl").read_text().splitlines()]
    old = [json.loads(line) for line in (rendered / "events.jsonl").read_text().splitlines()]
    for kind in ("imu", "camera"):
        current = [e for e in events if e["type"] == kind]
        expected = [e for e in old if e["type"] == kind]
        if kind == "imu":
            assert current[2:] == expected[:len(current) - 2]
        else:
            assert [e["timestamp_ns"] for e in current] == [e["timestamp_ns"] for e in expected]
            for a, b in zip(current, expected):
                assert digest(output / a["image_path"]) == digest(rendered / b["image_path"])
    assert "GPS_RAW_INT" not in (output / "events.jsonl").read_text()
    assert not (output / "telemetry.jsonl").exists()
    with pytest.raises(FileExistsError):
        prepare_capture(CHECKOUT, session, profile, output)


def test_clone_history_override_is_in_new_sealed_input_not_original(sources, tmp_path):
    session, profile, _ = sources
    before = digest(session / "manifest.json")
    original, changed = tmp_path / "default", tmp_path / "longer"
    first = prepare_capture(CHECKOUT, session, profile, original)
    second = prepare_capture(CHECKOUT, session, profile, changed, max_clones=24)
    assert first["source"]["native_max_clones"] == 11
    assert second["source"]["native_max_clones"] == 24
    assert second["source"]["native_max_clones_override"] == 24
    assert read_yaml(original / "estimator_config.yaml")["max_clones"] == 11
    assert read_yaml(changed / "estimator_config.yaml")["max_clones"] == 24
    assert first["checksums"]["events.jsonl"] == second["checksums"]["events.jsonl"]
    assert first["checksums"]["estimator_config.yaml"] != second["checksums"]["estimator_config.yaml"]
    assert digest(session / "manifest.json") == before


@pytest.mark.parametrize("fault", ["imu_gap", "imu_reorder", "imu_partial", "imu_wrong_clock", "attitude_only",
    "camera_sparse", "camera_missing_time", "wrong_calibration", "rolling_shutter", "clock_fit", "corrupt_profile"])
def test_pi_capture_rejects_unqualified_or_uncovered_sensors(sources, tmp_path, fault):
    session, profile, _ = sources
    manifest = json.loads((session / "manifest.json").read_text())
    telemetry = [json.loads(line) for line in (session / "telemetry.jsonl").read_text().splitlines()]
    description = json.loads((profile / "sensor_profile.json").read_text())
    if fault == "imu_gap":
        del telemetry[100:130]
    elif fault == "imu_reorder":
        telemetry[0], telemetry[1] = telemetry[1], telemetry[0]
    elif fault == "imu_partial":
        telemetry[0]["sample"]["imu_fields_updated"] = 1
    elif fault == "imu_wrong_clock":
        telemetry[0]["sample"]["vehicle_time_domain"] = "unix_epoch_us"
    elif fault == "attitude_only":
        for row in telemetry:
            row["sample"]["message_type"] = "ATTITUDE"
    elif fault == "camera_sparse":
        manifest["frames"] = manifest["frames"][::10]
        for i, frame in enumerate(manifest["frames"]):
            frame["sequence"] = i
        manifest["frame_count"] = len(manifest["frames"])
    elif fault == "camera_missing_time":
        manifest["frames"][0]["camera_metadata"].pop("sensor_timestamp_ns")
    elif fault == "wrong_calibration":
        description["camera_calibration_id"] = "wrong"
    elif fault == "rolling_shutter":
        description["camera_shutter"] = "rolling"
    elif fault == "clock_fit":
        manifest["mavlink"]["timesync"] = {}
    else:
        (profile / "qualification.txt").write_text("changed")
    (session / "manifest.json").write_text(json.dumps(manifest))
    (session / "telemetry.jsonl").write_text("".join(json.dumps(row) + "\n" for row in telemetry))
    (profile / "sensor_profile.json").write_text(json.dumps(description))
    write_session_checksums(session)
    output = tmp_path / "rejected"
    with pytest.raises(ValueError):
        prepare_capture(CHECKOUT, session, profile, output)
    assert not output.exists()


def test_converted_pi_capture_runs_actual_native_vio(sources, tmp_path):
    location = os.environ.get("VISION_NAV_OPENVINS_BINARY")
    if not location:
        pytest.skip("Set native binary for actual VIO verification")
    session, profile, _ = sources
    prepared = tmp_path / "native-input"
    prepare_capture(CHECKOUT, session, profile, prepared)
    output = tmp_path / "native-output"
    report = run(Path(location), prepared, output, pose_window=True)
    assert report["complete"], (output / "native.log").read_text()
    assert report["states"]["LOCAL_ESTIMATE"] > 40
    assert report["position_authority"] is False
    assert report["px4_external_vision"] == "disabled"


@pytest.mark.parametrize("delay", [-.002, .002])
def test_fixed_delay_is_preserved_for_native_application_only(sources, tmp_path, delay):
    session, profile, _ = sources
    path = profile / "kalibr_imucam_chain.yaml"
    camera = read_yaml(path)
    camera["cam0"]["timeshift_cam_imu"] = delay
    write_yaml(path, camera)
    description = json.loads((profile / "sensor_profile.json").read_text())
    description["checksums"][path.name] = digest(path)
    (profile / "sensor_profile.json").write_text(json.dumps(description))
    output = tmp_path / "delayed"
    prepare_capture(CHECKOUT, session, profile, output)
    events = [json.loads(line) for line in (output / "events.jsonl").read_text().splitlines()]
    assert next(e for e in events if e["type"] == "camera")["timestamp_ns"] == 1_000_000_000
    assert read_yaml(output / path.name)["cam0"]["timeshift_cam_imu"] == delay
    timing = json.loads((output / "capture_timing.json").read_text())
    assert timing["delay_applied_by"] == "native_OpenVINS_once_not_added_to_camera_event"


def test_profile_rejects_second_imu_correction_and_output_inside_capture(sources, tmp_path):
    session, profile, _ = sources
    with pytest.raises(ValueError, match="outside"):
        prepare_capture(CHECKOUT, session, profile, session / "output")
    path = profile / "kalibr_imu_chain.yaml"
    noise = read_yaml(path)
    noise["imu0"]["Tw"][0][0] = 2.
    write_yaml(path, noise)
    description = json.loads((profile / "sensor_profile.json").read_text())
    description["checksums"][path.name] = digest(path)
    (profile / "sensor_profile.json").write_text(json.dumps(description))
    with pytest.raises(ValueError, match="identity"):
        prepare_capture(CHECKOUT, session, profile, tmp_path / "output")
