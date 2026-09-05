"""Pinned ROS-free OpenVINS evaluation, isolated from map/PX4 publication."""

from __future__ import annotations

import argparse
import csv
import hashlib
import json
from pathlib import Path
import shutil
import subprocess
import time

import numpy as np
import yaml

REVISION = "69488123ed9362dd44b6f28e7f4680abbff1442b"


def digest(path):
    result = hashlib.sha256()
    with Path(path).open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            result.update(chunk)
    return result.hexdigest()


def read_yaml(path):
    lines = Path(path).read_text().splitlines()
    return yaml.safe_load("\n".join(line for line in lines if not line.startswith("%YAML")))


def write_yaml(path, data):
    class OpenCvDumper(yaml.SafeDumper):
        def increase_indent(self, flow=False, indentless=False):
            return super().increase_indent(flow, False)

    Path(path).write_text("%YAML:1.0\n---\n" + yaml.dump(data, Dumper=OpenCvDumper, default_flow_style=None, sort_keys=False))


def verify_checkout(checkout):
    rev = subprocess.check_output(["git", "-C", str(checkout), "rev-parse", "HEAD"], text=True).strip()
    dirty = subprocess.check_output(["git", "-C", str(checkout), "status", "--porcelain", "--untracked-files=all"], text=True).strip()
    if rev != REVISION or dirty:
        raise ValueError("OpenVINS source revision mismatch or dirty checkout")


def checked_max_clones(value):
    if value is not None and (type(value) is not int or not 2 <= value <= 32):
        raise ValueError("max_clones must be an integer in [2,32]")
    return value


def prepare_config(checkout, output, *, synthetic=False, profile="euroc_mav", max_clones=None):
    checked_max_clones(max_clones)
    verify_checkout(checkout)
    shutil.copy2(checkout / "LICENSE", output / "OPENVINS-LICENSE")
    if profile not in {"euroc_mav", "tum_vi"}:
        raise ValueError("Unsupported dataset calibration profile")
    source = checkout / "config" / profile
    config = read_yaml(source / "estimator_config.yaml")
    config.update(max_cameras=1, use_stereo=False, use_klt=True, use_aruco=False,
                  calib_cam_timeoffset=False, calib_cam_extrinsics=False, calib_cam_intrinsics=False,
                  num_opencv_threads=1, record_timing_information=False, save_total_state=False,
                  downsample_cameras=False)
    if max_clones is not None:
        config["max_clones"] = max_clones
    write_yaml(output / "estimator_config.yaml", config)
    if config.get("use_mask"):
        shutil.copy2(source / config["mask0"], output / config["mask0"])
    shutil.copy2(source / "kalibr_imu_chain.yaml", output / "kalibr_imu_chain.yaml")
    cameras = read_yaml(source / "kalibr_imucam_chain.yaml")
    cameras = {"cam0": cameras["cam0"]}
    cameras["cam0"]["cam_overlaps"] = []
    if synthetic:
        cameras["cam0"].update(T_imu_cam=np.eye(4).tolist(), resolution=[320, 240],
                                intrinsics=[250., 250., 160., 120.], distortion_coeffs=[0., 0., 0., 0.])
    write_yaml(output / "kalibr_imucam_chain.yaml", cameras)


def seal_input(output, scope, source):
    if (output / "input_manifest.json").exists():
        raise FileExistsError("Capture is already sealed")
    files = sorted(p for p in output.rglob("*") if p.is_file())
    manifest = {"schema_version": "vision_nav_openvins_input_v1", "scope": scope, "source": source,
                "source_revision": REVISION, "license_review": "GPL-3.0-or-later; research only; distribution review required",
                "position_authority": False, "clock": "dataset_native_nanoseconds",
                "sensor_frame": "calibrated_imu", "acceleration": "specific_force_including_gravity",
                "checksums": {str(p.relative_to(output)): digest(p) for p in files}}
    (output / "input_manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def make_synthetic(checkout: Path, output: Path, *, max_clones=None):
    """Render a textured plane and matching IMU; this is NOT an accuracy dataset."""
    import cv2

    checked_max_clones(max_clones)
    output.mkdir(parents=True, exist_ok=False)
    prepare_config(checkout, output, synthetic=True, max_clones=max_clones)
    (output / "images").mkdir()
    rng = np.random.default_rng(128)
    texture = rng.integers(0, 256, (600, 1000), dtype=np.uint8)
    texture = cv2.GaussianBlur(texture, (3, 3), .7)
    events = []
    origin, imu_step = 1_000_000_000, 5_000_000
    next_imu = 0
    for frame in range(160):
        t = frame / 20
        target = frame * 50_000_000
        while next_imu <= target + 10_000_000:
            imu_t = next_imu / 1e9
            acceleration = 4. if 3. <= imu_t < 3.5 else (-4. if 5. <= imu_t < 5.5 else 0.)
            events.append({"type": "imu", "timestamp_ns": origin+next_imu,
                           "angular_velocity_rad_s": [0., 0., 0.],
                           "specific_force_m_s2": [acceleration, 0., 9.81]})
            next_imu += imu_step
        # Exact piecewise double integration of the commanded specific force.
        x = 2 * max(t-3., 0.)**2 - 2 * max(t-3.5, 0.)**2 - 2 * max(t-5., 0.)**2 + 2 * max(t-5.5, 0.)**2
        affine = np.array([[1., 0., -300.-250.*x/6.], [0., 1., -180.]])
        image = cv2.warpAffine(texture, affine, (320, 240))
        path = f"images/{frame:05d}.png"
        if not cv2.imwrite(str(output / path), image):
            raise OSError("Could not write synthetic camera image")
        events.append({"type": "camera", "timestamp_ns": origin+target, "image_path": path})
    (output / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    return seal_input(output, "synthetic_rendered_sensor_smoke_not_accuracy", {"generator_seed": 128})


def prepare_euroc(checkout: Path, mav0: Path, output: Path, max_frames: int, *, max_clones=None):
    checked_max_clones(max_clones)
    if not 1 <= max_frames <= 20000:
        raise ValueError("max_frames must be in [1,20000]")
    output.mkdir(parents=True, exist_ok=False)
    prepare_config(checkout, output, max_clones=max_clones)
    # Read the actual dataset calibration, not another sequence's image dimensions.
    cam = read_yaml(mav0 / "cam0/sensor.yaml")
    imu = read_yaml(mav0 / "imu0/sensor.yaml")
    body_cam = np.array(cam["T_BS"]["data"]).reshape(4, 4)
    body_imu = np.array(imu["T_BS"]["data"]).reshape(4, 4)
    camera = read_yaml(output / "kalibr_imucam_chain.yaml")
    camera["cam0"].update(T_imu_cam=(np.linalg.inv(body_imu) @ body_cam).tolist(),
                           resolution=cam["resolution"], intrinsics=cam["intrinsics"],
                           camera_model=cam["camera_model"], distortion_model=cam["distortion_model"],
                           distortion_coeffs=cam["distortion_coefficients"])
    write_yaml(output / "kalibr_imucam_chain.yaml", camera)
    noise = read_yaml(output / "kalibr_imu_chain.yaml")
    for key in ("accelerometer_noise_density", "accelerometer_random_walk", "gyroscope_noise_density", "gyroscope_random_walk"):
        value = float(imu[key])
        if not np.isfinite(value) or value <= 0:
            raise ValueError("Dataset must supply positive finite IMU noise parameters")
        noise["imu0"][key] = value
    noise["imu0"]["update_rate"] = float(imu["rate_hz"])
    write_yaml(output / "kalibr_imu_chain.yaml", noise)
    copy_dataset_events(mav0, output, max_frames)
    return seal_input(output, "euroc_monocular_replay_not_holybro_validation", {
        "dataset_path": str(mav0.resolve()), "max_frames": max_frames,
        "dataset_url": "https://projects.asl.ethz.ch/datasets/euroc-mav/",
        "ground_truth_used_by_frontend": False})


def prepare_tumvi(checkout: Path, mav0: Path, output: Path, max_frames: int, *, max_clones=None):
    checked_max_clones(max_clones)
    """Only the official calibrated 512_16 export, not raw sensor recordings."""
    if not 1 <= max_frames <= 20000:
        raise ValueError("max_frames must be in [1,20000]")
    if mav0.name != "mav0" or not mav0.parent.name.endswith("_512_16"):
        raise ValueError("Expected calibrated TUM VI *_512_16/mav0 export")
    output.mkdir(parents=True, exist_ok=False)
    prepare_config(checkout, output, profile="tum_vi", max_clones=max_clones)
    copy_dataset_events(mav0, output, max_frames, tumvi=True)
    return seal_input(output, "tum_vi_monocular_replay_not_holybro_validation", {
        "dataset_path": str(mav0.resolve()), "max_frames": max_frames,
        "dataset_url": "https://cvg.cit.tum.de/data/datasets/visual-inertial-dataset",
        "dataset_license": "CC-BY-4.0", "calibration_profile": "pinned_openvins_tum_vi",
        "clock_and_imu": "official_calibrated_export_no_second_correction",
        "image_conversion": "uint16_right_shift_8_no_resize_no_per_frame_normalization",
        "ground_truth_used_by_frontend": False})


def copy_dataset_events(mav0, output, max_frames, *, tumvi=False):
    (output / "images").mkdir()
    events = []
    source_images = {}
    with (mav0 / "cam0/data.csv").open() as cameras, (mav0 / "imu0/data.csv").open() as inertial:
        camera_rows = csv.reader(line for line in cameras if not line.startswith("#"))
        imu_rows = iter(csv.reader(line for line in inertial if not line.startswith("#")))
        pending = next(imu_rows, None)
        last_camera, last_imu = 0, 0
        for count, row in enumerate(camera_rows):
            if count >= max_frames:
                break
            ns = int(row[0])
            if ns <= last_camera:
                raise ValueError("Dataset camera timestamps are not strictly increasing")
            last_camera = ns
            while pending and (last_imu < ns + 10_000_000):
                ti = int(pending[0])
                if ti <= last_imu:
                    raise ValueError("Dataset IMU timestamps are not strictly increasing")
                values = [float(x) for x in pending[1:]]
                if len(values) != 6 or not np.isfinite(values).all():
                    raise ValueError("Dataset IMU row requires six finite SI values")
                events.append({"type": "imu", "timestamp_ns": ti, "angular_velocity_rad_s": values[:3], "specific_force_m_s2": values[3:]})
                last_imu, pending = ti, next(imu_rows, None)
            if last_imu < ns + 5_000_000:
                raise ValueError("Dataset ends without IMU coverage for camera")
            name = row[1].strip()
            if Path(name).name != name:
                raise ValueError("Invalid dataset image filename")
            destination = f"images/{count:06d}.png"
            image_path = mav0 / "cam0/data" / name
            if tumvi:
                import cv2

                pixels = cv2.imread(str(image_path), cv2.IMREAD_UNCHANGED)
                if pixels is None or pixels.dtype != np.uint16 or pixels.shape != (512, 512):
                    raise ValueError("TUM VI input must be a 512x512 uint16 grayscale image")
                if not cv2.imwrite(str(output / destination), (pixels >> 8).astype(np.uint8)):
                    raise OSError("Unable to write converted dataset frame")
            else:
                shutil.copy2(image_path, output / destination)
            source_images[name] = digest(image_path)
            events.append({"type": "camera", "timestamp_ns": ns, "image_path": destination})
    if not any(e["type"] == "camera" for e in events):
        raise ValueError("Empty camera dataset")
    (output / "events.jsonl").write_text("".join(json.dumps(e) + "\n" for e in events))
    (output / "source_hashes.json").write_text(json.dumps({
        "cam0_data_csv": digest(mav0 / "cam0/data.csv"),
        "imu0_data_csv": digest(mav0 / "imu0/data.csv"),
        "cam0_images": source_images}, indent=2) + "\n")


def validate_capture(capture: Path):
    manifest_path = capture / "input_manifest.json"
    manifest = json.loads(manifest_path.read_text())
    if manifest.get("source_revision") != REVISION or manifest.get("position_authority") is not False:
        raise ValueError("Incompatible VIO capture manifest")
    required = {"estimator_config.yaml", "events.jsonl", "kalibr_imu_chain.yaml", "kalibr_imucam_chain.yaml"}
    if not required.issubset(manifest.get("checksums", {})):
        raise ValueError("Capture manifest omits required input checksums")
    for relative, expected in manifest["checksums"].items():
        path = (capture / relative).resolve()
        if not path.is_relative_to(capture.resolve()) or digest(path) != expected:
            raise ValueError(f"Input checksum/path mismatch: {relative}")
    config = read_yaml(capture / "estimator_config.yaml")
    if (config.get("relative_config_imu") != "kalibr_imu_chain.yaml"
            or config.get("relative_config_imucam") != "kalibr_imucam_chain.yaml"):
        raise ValueError("External configuration must be covered by capture checksums")
    if config.get("use_mask") and config.get("mask0") not in manifest["checksums"]:
        raise ValueError("Camera mask missing from checksum inventory")
    with (capture / "events.jsonl").open() as stream:
        for line in stream:
            if len(line) > 65536:
                raise ValueError("Oversized input event")
            e = json.loads(line)
            if e.get("type") == "camera" and e.get("image_path") not in manifest["checksums"]:
                raise ValueError("Camera image missing from checksum inventory")
    return manifest


def verify_binary(binary: Path, *, pose_window=False, stream_input=False, passive_anchors=False, anchor_control=False, map_frame_control=False):
    native_build = json.loads(subprocess.check_output([str(binary.resolve()), "--version"], text=True, timeout=30))
    if (native_build.get("source_revision") != REVISION or native_build.get("ros_enabled") is not False
            or native_build.get("position_authority") is not False):
        raise ValueError("Native adapter revision or mode mismatch")
    if pose_window and native_build.get("pose_window_schema") != "vision_nav_openvins_pose_window_v1":
        raise ValueError("Rebuild native adapter for joint pose window support")
    if stream_input and native_build.get("stream_input_schema") != "vision_nav_openvins_stream_v1":
        raise ValueError("Rebuild native adapter for bounded streaming input")
    if passive_anchors and native_build.get("passive_anchor_schema") != "vision_nav_openvins_passive_anchor_schedule_v1":
        raise ValueError("Rebuild native adapter for passive anchor support")
    if anchor_control and native_build.get("anchor_control_schema") != "vision_nav_openvins_anchor_control_v1":
        raise ValueError("Rebuild native adapter for owned anchor controls")
    if map_frame_control and native_build.get("map_frame_control_schema") != "vision_nav_openvins_map_frame_control_v1":
        raise ValueError("Rebuild native adapter for conditional map-frame controls")
    return native_build


def load_anchor_schedule(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(131073)
    if len(raw) > 131072:
        raise ValueError("Anchor schedule exceeds 128 KiB")
    data = json.loads(raw)
    if (not isinstance(data, dict) or data.get("schema_version") != "vision_nav_openvins_passive_anchor_schedule_v1"
            or type(data.get("capacity")) is not int or not 1 <= data["capacity"] <= 4
            or not isinstance(data.get("actions"), list) or not 1 <= len(data["actions"]) <= 1024):
        raise ValueError("Invalid passive anchor schedule")
    active, previous = set(), 0
    for action in data["actions"]:
        if not isinstance(action, dict) or set(action) != {"at_timestamp_ns", "anchor_timestamp_ns", "operation"}:
            raise ValueError("Invalid passive anchor action fields")
        at, anchor = action["at_timestamp_ns"], action["anchor_timestamp_ns"]
        if (type(at) is not int or type(anchor) is not int or not 0 < anchor <= at < 2**63 or at < previous):
            raise ValueError("Invalid passive anchor action time")
        if action["operation"] == "retain":
            if anchor in active or len(active) >= data["capacity"]:
                raise ValueError("Duplicate passive anchor or capacity exceeded")
            active.add(anchor)
        elif action["operation"] == "release" and anchor in active:
            active.remove(anchor)
        else:
            raise ValueError("Unknown passive anchor operation or release")
        previous = at
    return data, raw


def run(binary: Path, capture: Path, output: Path, *, pose_window=False, anchor_schedule=None):
    if anchor_schedule is not None and not pose_window:
        raise ValueError("Passive anchors require --pose-window")
    schedule, schedule_bytes = load_anchor_schedule(anchor_schedule) if anchor_schedule is not None else (None, None)
    manifest = validate_capture(capture)
    control = manifest.get("source", {}).get("anchor_control") if isinstance(manifest.get("source"), dict) else None
    if control is not None:
        import uuid
        if (not isinstance(control, dict) or not pose_window or schedule is not None
                or type(control.get("capacity")) is not int or not 1 <= control["capacity"] <= 4
                or str(uuid.UUID(control["worker_run_id"])) != control["worker_run_id"]
                or control.get("stream_path") not in manifest["checksums"]):
            raise ValueError("Controlled replay requires a bound stream, epoch, capacity and --pose-window, without a schedule")
    manifest_path = capture / "input_manifest.json"
    native_build = verify_binary(binary, pose_window=pose_window, passive_anchors=schedule is not None, anchor_control=control is not None)
    control_signatures = (digest(manifest_path), digest(binary), digest(__file__)) if control is not None else None
    output.mkdir(parents=True, exist_ok=False)
    schedule_path = output / "anchor_schedule.json"
    schedule_sha = None
    if schedule is not None:
        schedule_path.write_bytes(schedule_bytes)
        schedule_sha = digest(schedule_path)
    before = time.monotonic()
    execution_error = None
    control_args = []
    if control is not None:
        shutil.copyfile(capture/control["stream_path"], output/"owned_stream.jsonl")
        if digest(output/"owned_stream.jsonl") != manifest["checksums"][control["stream_path"]]:
            raise ValueError("Owned stream changed during copy")
        control_args = ["--stream-input", "--anchor-control", str(control["capacity"]), control["worker_run_id"],
                        str((output/"anchor_acknowledgements.jsonl").resolve())]
    with (output / "native.log").open("w") as log:
        controlled_input = (output/"owned_stream.jsonl").open("rb") if control is not None else None
        try:
            result = subprocess.run([str(binary.resolve()), str((capture / "estimator_config.yaml").resolve()),
                                 str((capture / "events.jsonl").resolve()), str((output / "local_states.jsonl").resolve())]
                                 + (["--pose-window"] if pose_window else [])
                                 + (["--anchor-schedule", str(schedule_path.resolve())] if schedule is not None else []) + control_args,
                                stdin=controlled_input, stdout=log, stderr=subprocess.STDOUT, timeout=1800)
            returncode = result.returncode
        except subprocess.TimeoutExpired:
            returncode, execution_error = -1, "native_replay_timeout"
        finally:
            if controlled_input:
                controlled_input.close()
    control_audit = None
    if control is not None and returncode in (0, 3):
        from .anchor_control import audit_control_stream
        control_audit = audit_control_stream(output/"owned_stream.jsonl", output/"anchor_acknowledgements.jsonl",
            output/"local_states.jsonl", worker_run_id=control["worker_run_id"], capacity=control["capacity"],
            sensor_events_path=capture/"events.jsonl")
        validate_capture(capture)
        if (control_signatures != (digest(manifest_path), digest(binary), digest(__file__))
                or digest(output/"owned_stream.jsonl") != manifest["checksums"][control["stream_path"]]):
            raise ValueError("Controlled replay source changed during execution")
    if schedule is not None and digest(schedule_path) != schedule_sha:
        returncode, execution_error = -1, "anchor_schedule_changed_during_replay"
    states_path = output / "local_states.jsonl"
    counts = {}
    if states_path.exists():
        with states_path.open() as stream:
            for line in stream:
                record = json.loads(line)
                if record["position_authority"] is not False or record["source_revision"] != REVISION:
                    raise ValueError("Unexpected native output authority or source")
                state = record["local_state"]
                counts[state] = counts.get(state, 0) + 1
    report = {"schema_version": "vision_nav_openvins_replay_v1", "returncode": returncode,
              "complete": returncode == 0, "scope": manifest["scope"], "states": counts,
              "execution_error": execution_error, "native_build": native_build,
              "wall_time_s": time.monotonic()-before, "source_revision": REVISION,
              "input_manifest_sha256": digest(manifest_path), "binary_sha256": digest(binary),
              "python_adapter_sha256": digest(__file__),
              "pose_window_requested": bool(pose_window),
              "states_sha256": digest(states_path) if states_path.exists() else None,
              "global_pose_available": False, "position_authority": False, "px4_external_vision": "disabled",
              "smoother_factor_export": "disabled_pending_cross_correlation_contract"}
    if schedule is not None:
        report["passive_anchors"] = {"schedule_sha256": schedule_sha, "capacity": schedule["capacity"],
            "requested_action_count": len(schedule["actions"]), "scope": "offline_scheduled_native_passive_clones"}
    if control is not None:
        report["anchor_control"] = {"capacity": control["capacity"], "worker_run_id": control["worker_run_id"],
            "stream_sha256": digest(output/"owned_stream.jsonl"), "audit": control_audit,
            "acknowledgements_sha256": digest(output/"anchor_acknowledgements.jsonl") if (output/"anchor_acknowledgements.jsonl").exists() else None}
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("synthetic", "prepare-euroc", "prepare-tumvi"):
        command = commands.add_parser(name)
        command.add_argument("--checkout", type=Path, required=True)
        command.add_argument("--output", type=Path, required=True)
        command.add_argument("--max-clones", type=int, help="Optional research filter-history override, 2..32")
        if name != "synthetic":
            command.add_argument("--mav0", type=Path, required=True)
            command.add_argument("--max-frames", type=int, default=400)
    command = commands.add_parser("run")
    command.add_argument("--binary", type=Path, required=True)
    command.add_argument("--capture", type=Path, required=True)
    command.add_argument("--output", type=Path, required=True)
    command.add_argument("--pose-window", action="store_true", help="Export correlated clone windows for research audit")
    command.add_argument("--anchor-schedule", type=Path, help="Opt-in bounded passive pose retention for offline research")
    args = parser.parse_args(argv)
    if args.command == "synthetic":
        result = make_synthetic(args.checkout, args.output, max_clones=args.max_clones)
    elif args.command == "prepare-euroc":
        result = prepare_euroc(args.checkout, args.mav0, args.output, args.max_frames, max_clones=args.max_clones)
    elif args.command == "prepare-tumvi":
        result = prepare_tumvi(args.checkout, args.mav0, args.output, args.max_frames, max_clones=args.max_clones)
    else:
        result = run(args.binary, args.capture, args.output, pose_window=args.pose_window, anchor_schedule=args.anchor_schedule)
    displayed = {key: value for key, value in result.items() if key != "checksums"}
    if "checksums" in result:
        displayed["checksummed_files"] = len(result["checksums"])
    print(json.dumps(displayed, indent=2))
    return 2 if result.get("complete") is False else 0


if __name__ == "__main__":
    raise SystemExit(main())
