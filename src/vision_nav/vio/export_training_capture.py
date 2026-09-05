"""Bind recorded native sensor evidence to the existing training capture format.

This is offline packaging, not sensor acquisition, label fabrication, model
promotion or aircraft qualification. Strict training gates are unchanged.
"""

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import shutil

import cv2
import numpy as np

from ..camera import load_camera_calibration
from ..camera_extrinsics import load_camera_to_body
from ..camera_ingress import frame_quality_metrics
from ..capture_timing import audit_capture_timing
from ..inertial_capture import summarize_inertial_capture
from ..training_capture import (TRAINING_CAPTURE_SCHEMA, _frame_attitude_alignment,
    _frame_camera_timing, _offline_gnss_reference_summary, validate_training_session,
    write_session_checksums)
from ..neural.reference_map import load_reference_map
from ..neural.map_release import load_release
from .export_sensor_replay import _rows, _verify, export_sensor_replay, validate_recorded_session
from .openvins_replay import digest, validate_capture
from .prepare_capture import load_sensor_profile


def _calibration_binding(profile_root, calibration_path, extrinsics_path):
    profile, native_camera, _ = load_sensor_profile(profile_root)
    camera, extrinsics = load_camera_calibration(calibration_path), load_camera_to_body(extrinsics_path)
    identity = profile["camera_calibration_id"]
    native = native_camera["cam0"]
    if (camera.calibration_status != "measured" or camera.calibration_id != identity
            or extrinsics.status != "measured" or extrinsics.camera_calibration_id != identity
            or list(camera.image_size) != native["resolution"]):
        raise ValueError("Measured camera/extrinsics must match the recorded calibration identity and dimensions")
    fx, fy, cx, cy = native["intrinsics"]
    expected = np.array([[fx, 0., cx], [0., fy, cy], [0., 0., 1.]])
    coeffs = camera.distortion_coefficients
    if native["distortion_model"] == "radtan":
        supported = camera.distortion_model in {"plumb_bob", "radtan"} and len(coeffs) in {4, 5}
        if len(coeffs) == 5 and coeffs[4] != 0.:
            supported = False
    else:
        supported = camera.distortion_model == "equidistant" and len(coeffs) == 4
    if (not supported or not np.allclose(camera.camera_matrix, expected, atol=1e-8, rtol=0)
            or not np.isfinite(coeffs).all()
            or not np.allclose(coeffs[:4], native["distortion_coeffs"], atol=1e-8, rtol=0)):
        raise ValueError("Camera intrinsics/distortion differ from the native recording")
    q = np.asarray(extrinsics.rotation_quat_xyzw)
    q = q / np.linalg.norm(q)
    length = float(np.linalg.norm(q[:3]))
    rotation_vector = np.zeros(3) if length < 1e-15 else q[:3] * (2*math.atan2(length, q[3])/length)
    transform = np.eye(4)
    transform[:3, :3] = cv2.Rodrigues(rotation_vector)[0]
    transform[:3, 3] = extrinsics.translation_m
    # The supported sensor profile declares body-FRD IMU and identity T_i_b.
    # Do not invent an additional body/IMU transform or lever-arm correction.
    if not np.allclose(transform, native["T_imu_cam"], atol=1e-8, rtol=0):
        raise ValueError("Camera-to-body transform differs from the recorded camera/IMU profile")
    return camera, extrinsics


def export_training_capture(session, output, *, calibration_file, camera_to_body_file,
                            reference_map=None, map_release=None, site_name=None, operator=None, conditions=()):
    if (reference_map is None) == (map_release is None):
        raise ValueError("Select exactly one reference map or deployment map release")
    root, output = Path(session).resolve(), Path(output).resolve()
    if output.exists() or output.is_relative_to(root) or root.is_relative_to(output):
        raise ValueError("Training output must be new and outside the recorded session")
    checked = validate_recorded_session(root)
    calibration_path, extrinsics_path = Path(calibration_file).resolve(), Path(camera_to_body_file).resolve()
    calibration_hash, extrinsics_hash = digest(calibration_path), digest(extrinsics_path)
    camera, extrinsics = _calibration_binding(root / "vio/input/sensor_profile", calibration_path, extrinsics_path)
    map_path = Path(reference_map or map_release).resolve()
    map_hash = digest(map_path)
    map_data = load_reference_map(map_path) if reference_map else load_release(map_path)
    if camera.calibration_id not in map_data["calibration_ids"]:
        raise ValueError("Selected map does not support the recorded camera calibration")
    map_checksums = map_data["checksums_sha256"]
    _verify(map_path.parent, map_checksums)
    kind = "reference_map_release" if reference_map else "deployment_map_release"
    conditions = list(conditions)
    if not all(isinstance(value, str) and value.strip() for value in conditions):
        raise ValueError("Conditions must be non-empty descriptive strings")
    output.mkdir(parents=True, exist_ok=False)
    try:
        native_manifest = export_sensor_replay(root, output / "native_replay")
        if native_manifest["source"]["session_report_sha256"] != checked["files"]["session_report.json"]:
            raise ValueError("Source session changed while preparing training capture")
        (output / "calibration").mkdir()
        copied = {}
        for source, relative, expected in (
                (calibration_path, "calibration/down_camera.yaml", calibration_hash),
                (extrinsics_path, "calibration/camera_to_body.yaml", extrinsics_hash),
                (map_path, "map_reference.json", map_hash)):
            shutil.copyfile(source, output / relative)
            copied[relative] = expected
        _verify(output, copied)
        frames = []
        camera_rows = list(_rows(root / "camera.jsonl"))
        offers = (r for r in _rows(root / "vio/input/live_frames.jsonl", limit=1024**3) if r["record_type"] == "offered")
        for index, offered in enumerate(offers):
            relative = "native_replay/" + offered["image_path"]
            frames.append({"sequence": index, "path": relative, "source_path": None, "captured_utc": None,
                "host_monotonic_ns": offered["camera_clock"]["host_monotonic_ns"],
                "timestamp_reference": "first_row_exposure_start",
                "host_capture_started_monotonic_ns": None, "host_capture_finished_monotonic_ns": None,
                "capture_duration_ms": None, "camera_backend": "picamera2",
                "camera_metadata": deepcopy(camera_rows[index]["camera_metadata"]),
                "image_quality": frame_quality_metrics(output / relative),
                "native_input": {"worker_run_id": checked["worker_run_id"], "sequence": index,
                    "timestamp_ns": offered["timestamp_ns"], "image_sha256": offered["image_sha256"],
                    "pixel_sha256": offered["pixel_sha256"], "px4_clock": offered["px4_clock"],
                    "clock_fit": offered["clock_fit"]}})
        telemetry = []
        exchanges = []
        for row in _rows(root / "telemetry.jsonl"):
            if row["record_type"] == "timesync":
                exchanges.append(row["exchange"])
            elif row["record_type"] == "telemetry":
                telemetry.append({"index": len(telemetry), "host_utc": None,
                    "host_monotonic_ns": row["host_monotonic_ns"], "sample": row["sample"]})
        (output / "telemetry").mkdir()
        telemetry_path = output / "telemetry/px4.jsonl"
        with telemetry_path.open("x") as stream:
            for row in telemetry:
                stream.write(json.dumps(row, sort_keys=True, allow_nan=False)+"\n")
        timesync = {"schema_version": "vision_nav_recorded_periodic_timesync_v1",
            "status": "periodic_evidence_only", "pre_capture": {"status": "not_recorded"},
            "post_capture": {"status": "not_recorded"},
            "during_capture": {"exchanges": exchanges, "source": "original_single_owner_telemetry_journal"},
            "native_clock_policy": "per_frame_fits_preserved_in_native_input",
            "training_audit_policy": "offline_fit_not_substituted_for_native_replay"}
        alignment = _frame_attitude_alignment(frames, telemetry)
        gps = _offline_gnss_reference_summary(frames, telemetry)
        timing = _frame_camera_timing(frames)
        timing.update(pairing_timestamp_source="camera_exposure_host_monotonic_ns",
                      sensor_timestamp_clock_relation="recorded_per_frame_clock_evidence_not_physical_certification")
        calibration = {**camera.to_log_dict(), "path": "calibration/down_camera.yaml",
                       "file_path": "calibration/down_camera.yaml", "file_sha256": calibration_hash}
        ext = {**extrinsics.to_log_dict(), "path": "calibration/camera_to_body.yaml", "file_sha256": extrinsics_hash}
        map_binding = {"release_id": map_data["release_id"], "kind": kind,
            "manifest_path": "map_reference.json", "manifest_sha256": map_hash,
            "binding_stage": "offline_export_not_capture_time_selection",
            "assets_policy": "separate_immutable_map_release_not_duplicated_per_capture"}
        manifest = {"schema_version": TRAINING_CAPTURE_SCHEMA,
            "session_id": "live-vio-"+checked["worker_run_id"], "status": "captured",
            "started_utc": None, "completed_utc": None,
            "exported_utc": datetime.now(timezone.utc).isoformat(),
            "started_monotonic_ns": None,
            "timebase": {"primary": "host_monotonic_ns", "utc": "capture_utc_not_recorded",
                         "frame_timestamp_reference": "first_row_exposure_start"},
            "training_policy": {"gnss_truth_policy": "offline_training_only", "flight_runtime_gnss_dependency": "prohibited",
                                "require_offline_gnss_reference": True, "require_mavlink_timesync": True},
            "calibration": calibration, "camera_to_body": ext,
            "map_release_id": map_data["release_id"], "map_reference": map_binding,
            "camera": {"backend": "picamera2", "width": camera.image_width, "height": camera.image_height,
                       "timeout_ms": None, "frame_timing": timing},
            "mavlink": {"endpoint": None, "state": "connected_continuous",
                "attitude_sample_count": alignment["attitude_sample_count"],
                "inertial_capture": summarize_inertial_capture(telemetry), "frame_attitude_alignment": alignment,
                "offline_gnss_reference": gps, "timesync": timesync,
                "exposure_alignment": audit_capture_timing(frames, telemetry, timesync),
                "capture": {"mode": "continuous_background_reader", "errors": [],
                            "telemetry_record_count": len(telemetry), "source_worker_run_id": checked["worker_run_id"]}},
            "operator_context": {"site_name": site_name, "operator": operator, "conditions": conditions,
                                 "provided_at": "offline_export"},
            "frames_path": "native_replay/images", "telemetry_path": "telemetry/px4.jsonl",
            "frame_count": len(frames), "telemetry_count": len(telemetry), "frames": frames,
            "source_recording": {"session_report_sha256": checked["files"]["session_report.json"],
                "worker_run_id": checked["worker_run_id"], "native_replay_manifest_path": "native_replay/input_manifest.json",
                "native_replay_manifest_sha256": digest(output / "native_replay/input_manifest.json"),
                "exporter_sha256": digest(__file__), "training_eligibility_path": "training_eligibility.json"},
            "position_authority": False, "field_qualified": False}
        manifest_path = output / "manifest.json"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True, allow_nan=False)+"\n")
        copied["manifest.json"] = digest(manifest_path)
        copied["native_replay/input_manifest.json"] = manifest["source_recording"]["native_replay_manifest_sha256"]
        eligibility = validate_training_session(output, verify_checksums=False, require_attitude=True,
            require_sensor_timestamps=True, require_offline_gnss_reference=True, require_mavlink_timesync=True,
            attitude_timing_policy="exposure_native_attitude_v1")
        summary = {"status": eligibility["status"], "errors": eligibility["errors"],
                   "checksums_verified_separately": True, "field_qualified": False,
                   "scope": "existing_strict_training_capture_gate_not_flight_or_model_promotion"}
        (output / "training_eligibility.json").write_text(json.dumps(summary, indent=2)+"\n")
        copied["training_eligibility.json"] = digest(output / "training_eligibility.json")
        _verify(root, checked["files"])
        _verify(map_path.parent, map_checksums)
        if digest(map_path) != map_hash or digest(calibration_path) != calibration_hash or digest(extrinsics_path) != extrinsics_hash:
            raise ValueError("Map or calibration changed during export")
        _verify(output, copied)
        write_session_checksums(output)
        _verify(output, copied)
        validate_capture(output / "native_replay")
        validation = validate_training_session(output, require_sensor_timestamps=True)
        if validation["status"] != "valid":
            raise ValueError("Exported training capture validation failed: " + "; ".join(validation["errors"]))
        return {"session_dir": str(output), "manifest": manifest, "training_eligibility": summary}
    except Exception:
        (output / "manifest.json").unlink(missing_ok=True)
        (output / "checksums.sha256").unlink(missing_ok=True)
        raise


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("session", "output", "calibration", "camera-to-body"):
        parser.add_argument("--"+name, type=Path, required=True)
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--reference-map", type=Path)
    group.add_argument("--map-release", type=Path)
    parser.add_argument("--site-name")
    parser.add_argument("--operator")
    parser.add_argument("--condition", action="append", default=[])
    args = parser.parse_args(argv)
    result = export_training_capture(args.session, args.output, calibration_file=args.calibration,
        camera_to_body_file=args.camera_to_body, reference_map=args.reference_map, map_release=args.map_release,
        site_name=args.site_name, operator=args.operator, conditions=args.condition)
    print(json.dumps({"session_dir": result["session_dir"], "training_eligibility": result["training_eligibility"]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
