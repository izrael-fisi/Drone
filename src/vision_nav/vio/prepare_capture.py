"""Convert sealed Pi camera/IMU evidence for native, research-only VIO replay."""

import argparse
from bisect import bisect_left
import json
from pathlib import Path
import shutil

import cv2
import numpy as np

from ..capture_timing import TimingPolicy, camera_exposure_time, fit_px4_clock
from ..training_capture import validate_training_session
from .openvins_replay import checked_max_clones, digest, prepare_config, read_yaml, seal_input, write_yaml


def _file(root, relative):
    if (not isinstance(relative, str) or Path(relative).is_absolute()
            or ".." in Path(relative).parts or str(Path(relative)) != relative):
        raise ValueError("Expected a relative evidence path")
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError("Evidence path missing or outside its source directory")
    return path


def _finite(value):
    if type(value) not in (int, float) or not np.isfinite(value):
        raise ValueError("Expected a finite measurement")
    return value


def _vector(value):
    if not isinstance(value, list) or len(value) != 3:
        raise ValueError("Expected a three-axis SI sample")
    return [_finite(v) for v in value]


def normalize_imu_sample(sample, profile):
    """Select the qualified raw sensor without substituting attitude or receipt time."""
    keys = ("source_system", "source_component", "message_type", "imu_sensor_id")
    if any(sample.get(key) != profile[key] for key in keys):
        return None
    if any(type(sample.get(key)) is not int for key in ("source_system", "source_component", "imu_sensor_id")):
        raise ValueError("IMU sender/sensor identities must be integer values")
    stamp, updated = sample.get("vehicle_timestamp_us"), sample.get("imu_fields_updated")
    if (type(stamp) is not int or not 0 < stamp < (2**63)//1000
            or sample.get("vehicle_time_domain") != "px4_boot_us" or sample.get("imu_frame") != "body_frd"
            or type(updated) is not int or updated & 0x3f != 0x3f):
        raise ValueError("Selected IMU has invalid clock/axes or partially updated measurements")
    return {"type": "imu", "timestamp_ns": stamp * 1000,
            "angular_velocity_rad_s": _vector(sample.get("angular_velocity_rad_s")),
            "specific_force_m_s2": _vector(sample.get("acceleration_m_s2"))}


def load_sensor_profile(root):
    """Validate supplied calibration declarations, not their physical accuracy."""
    path = root / "sensor_profile.json"
    profile = json.loads(path.read_text())
    if (profile.get("schema_version") != "vision_nav_openvins_sensor_profile_v1"
            or profile.get("calibration_status") != "measured"
            or profile.get("camera_shutter") != "global"
            or profile.get("camera_timestamp_reference") != "first_row_exposure_start"
            or profile.get("imu_frame") != "body_frd"
            or profile.get("imu_measurement") != "specific_force_including_gravity"
            or profile.get("imu_intrinsics_policy") != "sender_corrected_no_second_correction"
            or profile.get("timesync_clock_matches_imu_boot_clock") is not True):
        raise ValueError("Measured global-shutter camera/IMU and firmware clock declarations required")
    for key in ("profile_id", "camera_calibration_id", "firmware_revision", "qualification_notes"):
        if not isinstance(profile.get(key), str) or not profile[key].strip():
            raise ValueError(f"Sensor profile requires {key}")
    for key in ("source_system", "source_component", "imu_sensor_id"):
        value = profile.get(key)
        if type(value) is not int or not (0 if key == "imu_sensor_id" else 1) <= value <= 255:
            raise ValueError("Invalid explicit IMU sender/sensor identity")
    if profile.get("message_type") != "HIGHRES_IMU":
        raise ValueError("Only explicitly identified HIGHRES_IMU is supported")
    checksums = profile.get("checksums", {})
    required = {"kalibr_imucam_chain.yaml", "kalibr_imu_chain.yaml"}
    if not required.issubset(checksums) or len(checksums) > 100:
        raise ValueError("Profile must bind calibration and qualification evidence by checksum")
    evidence = profile.get("qualification_evidence", [])
    if not isinstance(evidence, list) or not evidence or any(p not in checksums for p in evidence):
        raise ValueError("Checksummed sensor qualification evidence required")
    for relative, expected in checksums.items():
        if digest(_file(root, relative)) != expected:
            raise ValueError("Sensor profile checksum mismatch")
    camera = read_yaml(root / "kalibr_imucam_chain.yaml")
    imu = read_yaml(root / "kalibr_imu_chain.yaml")
    if set(camera) != {"cam0"} or set(imu) != {"imu0"}:
        raise ValueError("Single calibrated camera and IMU required")
    cam, noise = camera["cam0"], imu["imu0"]
    if cam.get("camera_model") != "pinhole" or cam.get("distortion_model") not in {"radtan", "equidistant"}:
        raise ValueError("Unsupported native camera model")
    intrinsics, distortion, resolution = cam.get("intrinsics"), cam.get("distortion_coeffs"), cam.get("resolution")
    if (not isinstance(intrinsics, list) or len(intrinsics) != 4
            or not isinstance(distortion, list) or len(distortion) != 4
            or not isinstance(resolution, list) or len(resolution) != 2
            or any(type(v) is not int or not 1 <= v <= 8192 for v in resolution)):
        raise ValueError("Invalid native camera calibration dimensions")
    if any(_finite(v) <= 0 for v in intrinsics[:2]):
        raise ValueError("Positive focal lengths required")
    for value in intrinsics[2:] + distortion:
        _finite(value)
    if "T_cam_imu" in cam:
        raise ValueError("Declare only T_imu_cam, mapping camera coordinates into the selected IMU frame")
    transform = np.asarray(cam.get("T_imu_cam"), dtype=float)
    if (transform.shape != (4, 4) or not np.isfinite(transform).all()
            or not np.allclose(transform[3], [0, 0, 0, 1], atol=1e-8)
            or not np.allclose(transform[:3, :3].T @ transform[:3, :3], np.eye(3), atol=1e-6)
            or not np.isclose(np.linalg.det(transform[:3, :3]), 1, atol=1e-6)):
        raise ValueError("Camera-to-IMU calibration must be a rigid transform")
    delay = _finite(cam.get("timeshift_cam_imu"))
    if abs(delay) > .1:
        raise ValueError("Calibrated camera/IMU delay exceeds native replay envelope")
    for key in ("accelerometer_noise_density", "accelerometer_random_walk", "gyroscope_noise_density",
                "gyroscope_random_walk", "update_rate"):
        if _finite(noise.get(key)) <= 0:
            raise ValueError("Measured positive IMU noise/rate values required")
    if _finite(noise.get("time_offset", 0)) != 0:
        raise ValueError("IMU time_offset must be zero; fixed delay belongs in timeshift_cam_imu")
    if noise.get("model") not in {"calibrated", "kalibr"}:
        raise ValueError("Unsupported native IMU model")
    for key in ("Tw", "Ta", "R_IMUtoACC", "R_IMUtoGYRO", "Tg", "T_i_b"):
        expected = np.zeros((3, 3)) if key == "Tg" else np.eye(4 if key == "T_i_b" else 3)
        value = np.asarray(noise.get(key), dtype=float)
        if value.shape != expected.shape or not np.allclose(value, expected, atol=1e-10, rtol=0):
            raise ValueError("Sender-corrected body-frame IMU requires explicit identity intrinsics/extrinsics and zero Tg")
    gravity = _finite(profile.get("gravity_m_s2"))
    if not 9 < gravity < 10:
        raise ValueError("Invalid gravity magnitude")
    return profile, camera, imu


def prepare_capture(checkout: Path, session: Path, profile_dir: Path, output: Path, *, max_clones=None):
    checked_max_clones(max_clones)
    session, profile_dir = session.resolve(), profile_dir.resolve()
    if output.resolve().is_relative_to(session) or output.resolve().is_relative_to(profile_dir):
        raise ValueError("Output must be outside source capture and calibration directories")
    validation = validate_training_session(session, verify_checksums=True)
    if validation["status"] != "valid":
        raise ValueError("Capture validation failed: " + "; ".join(validation["errors"]))
    manifest = validation["manifest"]
    if not 2 <= len(manifest["frames"]) <= 20000:
        raise ValueError("Capture must contain 2..20000 frames")
    manifest_hash = digest(session / "manifest.json")
    profile, camera, imu = load_sensor_profile(profile_dir)
    profile_hash = digest(profile_dir / "sensor_profile.json")
    if profile["camera_calibration_id"] != manifest["calibration"]["calibration_id"]:
        raise ValueError("Capture and VIO camera calibration identities differ")
    policy = TimingPolicy()
    exchanges = []
    for phase in ("pre_capture", "during_capture", "post_capture"):
        exchanges.extend(manifest.get("mavlink", {}).get("timesync", {}).get(phase, {}).get("exchanges", []))
    fit, fit_report = fit_px4_clock(exchanges, policy)
    if fit is None or fit.source != (profile["source_system"], profile["source_component"]):
        raise ValueError("Camera/PX4 clock fit unavailable or sender differs from selected IMU")
    telemetry_path = _file(session, manifest["telemetry_path"])
    if telemetry_path.stat().st_size > 512 * 1024 * 1024:
        raise ValueError("Telemetry exceeds 512 MiB replay preparation limit")
    telemetry_hash = digest(telemetry_path)
    inertial, ignored = [], 0
    with telemetry_path.open() as stream:
        while line := stream.readline(65537):
            if len(line) > 65536:
                raise ValueError("Oversized capture telemetry record")
            sample = json.loads(line).get("sample", {})
            event = normalize_imu_sample(sample, profile)
            if event is None:
                ignored += 1
                continue
            ns = event["timestamp_ns"]
            if inertial and (ns <= inertial[-1]["timestamp_ns"] or ns - inertial[-1]["timestamp_ns"] > 50_000_000):
                raise ValueError("Selected IMU is reordered or has a gap exceeding 50 ms")
            inertial.append(event)
            if len(inertial) > 2_000_000:
                raise ValueError("Too many IMU samples for bounded replay preparation")
    if not inertial:
        raise ValueError("No selected raw IMU samples; attitude is not a replacement")
    times = [e["timestamp_ns"] for e in inertial]
    delay_ns = round(camera["cam0"]["timeshift_cam_imu"] * 1e9)
    frames, events, previous, previous_offset, cursor = [], [], 0, None, 0
    for index, frame in enumerate(manifest["frames"]):
        if type(frame.get("sequence")) is not int or frame["sequence"] != index:
            raise ValueError("Capture frame sequence must be contiguous")
        observation = camera_exposure_time(frame.get("camera_metadata", {}), policy)
        if observation["status"] != "available":
            raise ValueError(f"Frame {index} lacks usable exposure timestamp: {observation['reason']}")
        offset = observation["boottime_minus_monotonic_ns"]
        if previous_offset is not None and abs(offset - previous_offset) > policy.max_host_offset_step_ms * 1e6 + observation["clock_uncertainty_ns"]:
            raise ValueError("Host suspend/clock step between captured frames")
        previous_offset = offset
        converted = fit.convert(observation["host_monotonic_ns"], observation["clock_uncertainty_ns"])
        if converted["status"] != "available":
            raise ValueError(f"Frame {index} clock conversion rejected: {converted['reason']}")
        ns = converted["px4_timestamp_ns"]
        if previous and (ns <= previous or ns - previous > 250_000_000):
            raise ValueError("Camera is reordered or too sparse for native VIO replay (250 ms maximum gap)")
        previous = ns
        target = ns + delay_ns
        uncertainty = converted["clock_uncertainty_ns"]
        end = bisect_left(times, target + uncertainty + 5_000_000)
        if end >= len(times) or times[0] > target - uncertainty or times[end] > target + 150_000_000:
            raise ValueError(f"Frame {index} lacks bounded bracketing raw IMU coverage")
        while cursor <= end:
            events.append(inertial[cursor])
            cursor += 1
        path = _file(session, frame["path"])
        pixels = cv2.imread(str(path), cv2.IMREAD_UNCHANGED)
        width, height = camera["cam0"]["resolution"]
        if pixels is None or pixels.dtype != np.uint8 or pixels.shape not in {(height, width), (height, width, 3)}:
            raise ValueError("Captured pixels do not match calibrated uint8 gray/BGR dimensions")
        relative = f"images/{index:06d}{path.suffix.lower()}"
        frames.append({"source_path": frame["path"], "image_path": relative, "sha256": digest(path),
                       "camera_clock": observation, "px4_clock": converted, "timestamp_ns": ns})
        events.append({"type": "camera", "timestamp_ns": ns, "image_path": relative})
    # Preserve input ownership: no directory is created until sensor checks pass.
    output.mkdir(parents=True, exist_ok=False)
    prepare_config(checkout, output, max_clones=max_clones)
    config = read_yaml(output / "estimator_config.yaml")
    config.update(use_mask=False, gravity_mag=profile["gravity_m_s2"])
    write_yaml(output / "estimator_config.yaml", config)
    write_yaml(output / "kalibr_imucam_chain.yaml", camera)
    write_yaml(output / "kalibr_imu_chain.yaml", imu)
    (output / "images").mkdir()
    for frame in frames:
        shutil.copy2(_file(session, frame["source_path"]), output / frame["image_path"])
        if digest(output / frame["image_path"]) != frame["sha256"]:
            raise ValueError("Camera image changed during conversion")
    (output / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    (output / "capture_timing.json").write_text(json.dumps({"clock_fit": fit_report, "frames": frames,
        "timeshift_cam_imu_s": camera["cam0"]["timeshift_cam_imu"],
        "delay_applied_by": "native_OpenVINS_once_not_added_to_camera_event",
        "sensor_qualification": "operator_supplied_evidence_not_independently_verified"}, indent=2) + "\n")
    profile_output = output / "sensor_profile"
    profile_output.mkdir()
    for relative, expected in profile["checksums"].items():
        destination = profile_output / relative
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(_file(profile_dir, relative), destination)
        if digest(destination) != expected:
            raise ValueError("Sensor calibration/evidence changed during conversion")
    shutil.copy2(profile_dir / "sensor_profile.json", profile_output / "sensor_profile.json")
    if (digest(session / "manifest.json") != manifest_hash or digest(telemetry_path) != telemetry_hash
            or digest(profile_dir / "sensor_profile.json") != profile_hash
            or digest(profile_output / "sensor_profile.json") != profile_hash):
        raise ValueError("Source capture/profile changed during conversion")
    source = {"session_id": manifest["session_id"], "capture_manifest_sha256": manifest_hash,
              "telemetry_sha256": telemetry_hash, "sensor_profile_sha256": profile_hash,
              "ignored_nonselected_telemetry_records": ignored, "input_imu_count": len(inertial),
              "delivered_imu_count": cursor, "camera_count": len(frames),
              "native_max_clones": config["max_clones"], "native_max_clones_override": max_clones,
              "ground_truth_used_by_frontend": False, "flight_qualification": False}
    sealed = seal_input(output, "pi_capture_local_vio_research_not_flight_qualification", source)
    sealed.update(clock="px4_timesync_ns_bound_to_selected_imu_boot_clock_by_profile", sensor_frame="body_frd")
    (output / "input_manifest.json").write_text(json.dumps(sealed, indent=2) + "\n")
    return sealed


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("checkout", "session", "sensor-profile", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--max-clones", type=int, help="Optional research filter-history override, 2..32")
    args = parser.parse_args(argv)
    try:
        result = prepare_capture(args.checkout, args.session, args.sensor_profile, args.output, max_clones=args.max_clones)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"VIO capture preparation rejected: {exc}\n")
    print(json.dumps({key: value for key, value in result.items() if key != "checksums"}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
