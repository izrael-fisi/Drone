"""Combine prop-off camera and PX4 evidence into a bounded hardware readiness report."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from vision_nav.camera import load_camera_calibration
from vision_nav.camera_bench import CAMERA_BENCH_SCHEMA
from vision_nav.camera_extrinsics import load_camera_to_body
from vision_nav.mavlink_bench import MAVLINK_BENCH_SCHEMA


PROP_OFF_PREFLIGHT_SCHEMA = "vision_nav_prop_off_preflight_v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _load_report(path: str | Path) -> tuple[Path, dict[str, Any]]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"Evidence report does not exist: {source}")
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"Evidence report is not valid JSON: {source}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"Evidence report must be a JSON object: {source}")
    return source, raw


def _check(name: str, status: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"name": name, "status": status, "message": message, "details": details or {}}


def evaluate_prop_off_preflight(
    *,
    camera_bench_report: str | Path,
    mavlink_bench_report: str | Path,
    propellers_removed_confirmed: bool,
    calibration_file: str | Path | None = None,
    camera_to_body_file: str | Path | None = None,
    require_training_capture_inputs: bool = False,
) -> dict[str, Any]:
    """Evaluate operator-attested prop-off evidence without commanding PX4."""

    camera_path, camera = _load_report(camera_bench_report)
    mavlink_path, mavlink = _load_report(mavlink_bench_report)
    checks: list[dict[str, Any]] = []
    checks.append(
        _check(
            "propellers_removed",
            "passed" if propellers_removed_confirmed else "failed",
            "Operator confirmed propellers are removed."
            if propellers_removed_confirmed
            else "Propeller removal must be explicitly confirmed before a bench workflow can proceed.",
            {"operator_attestation": propellers_removed_confirmed},
        )
    )
    camera_schema_valid = camera.get("schema_version") == CAMERA_BENCH_SCHEMA
    camera_passed = camera_schema_valid and camera.get("status") == "passed"
    checks.append(
        _check(
            "camera_bench",
            "passed" if camera_passed else "failed",
            "Multi-frame camera quality bench passed."
            if camera_passed
            else "Camera bench is missing, uses an unsupported schema, or contains image-quality failures.",
            {
                "path": str(camera_path),
                "schema_version": camera.get("schema_version"),
                "report_status": camera.get("status"),
                "errors": camera.get("errors", []),
                "warnings": camera.get("warnings", []),
            },
        )
    )
    mavlink_schema_valid = mavlink.get("schema_version") == MAVLINK_BENCH_SCHEMA
    mavlink_passed = mavlink_schema_valid and mavlink.get("status") == "passed"
    checks.append(
        _check(
            "mavlink_bench",
            "passed" if mavlink_passed else "failed",
            "Read-only Pixhawk heartbeat and attitude-cadence bench passed."
            if mavlink_passed
            else "MAVLink bench is missing, uses an unsupported schema, or fails its telemetry cadence gate.",
            {
                "path": str(mavlink_path),
                "schema_version": mavlink.get("schema_version"),
                "report_status": mavlink.get("status"),
                "errors": mavlink.get("errors", []),
                "warnings": mavlink.get("warnings", []),
            },
        )
    )

    calibration_id: str | None = None
    calibration_ready = False
    calibration_details: dict[str, Any] = {"path": str(calibration_file) if calibration_file else None}
    if calibration_file:
        try:
            calibration = load_camera_calibration(calibration_file)
            calibration_id = calibration.calibration_id
            calibration_ready = calibration.calibration_status == "measured" and calibration_id is not None
            calibration_details.update(calibration.to_log_dict())
        except (OSError, ValueError) as exc:
            calibration_details["error"] = str(exc)
    checks.append(
        _check(
            "camera_calibration",
            "passed" if calibration_ready else ("failed" if require_training_capture_inputs else "pending"),
            "Measured mounted-camera calibration is present."
            if calibration_ready
            else "Measured mounted-camera calibration is required before IMU-aware training capture.",
            calibration_details,
        )
    )

    extrinsics_ready = False
    extrinsics_details: dict[str, Any] = {"path": str(camera_to_body_file) if camera_to_body_file else None}
    if camera_to_body_file:
        try:
            extrinsics = load_camera_to_body(camera_to_body_file)
            extrinsics_ready = (
                extrinsics.status == "measured"
                and extrinsics.extrinsic_id is not None
                and calibration_id is not None
                and extrinsics.camera_calibration_id == calibration_id
            )
            extrinsics_details.update(extrinsics.to_log_dict())
            if calibration_id is not None and extrinsics.camera_calibration_id != calibration_id:
                extrinsics_details["compatibility_error"] = "extrinsics_calibration_id_mismatch"
        except (OSError, ValueError) as exc:
            extrinsics_details["error"] = str(exc)
    checks.append(
        _check(
            "camera_to_body_extrinsics",
            "passed" if extrinsics_ready else ("failed" if require_training_capture_inputs else "pending"),
            "Measured camera-to-body transform matches the mounted-camera calibration."
            if extrinsics_ready
            else "Measured camera-to-body transform is required before IMU-aware training capture.",
            extrinsics_details,
        )
    )

    base_ready = all(check["status"] == "passed" for check in checks[:3])
    training_ready = base_ready and calibration_ready and extrinsics_ready
    if training_ready:
        stage = "ready_for_training_capture"
        next_actions: list[dict[str, str]] = [
            {"title": "Record a synchronized training session.", "command": "./scripts/pi/record_training_capture.sh"}
        ]
    elif base_ready:
        stage = "ready_for_calibration"
        next_actions = [
            {"title": "Capture a chessboard calibration set.", "command": "./scripts/pi/capture_calibration_set.sh"},
            {
                "title": "Create a measured camera-to-body record after the mount is final.",
                "command": "vision-nav-create-camera-extrinsics --help",
            },
        ]
    else:
        stage = "not_ready"
        next_actions = [
            {"title": "Resolve failed prop-off checks before calibration or training capture.", "command": "./scripts/pi/capture_camera_bench.sh"},
            {"title": "Recheck Pixhawk telemetry evidence.", "command": "./scripts/pi/capture_mavlink_bench.sh"},
        ]
    if require_training_capture_inputs and not training_ready:
        status = "failed"
    else:
        status = "passed" if base_ready else "failed"
    return {
        "schema_version": PROP_OFF_PREFLIGHT_SCHEMA,
        "generated_at_utc": _utc_now(),
        "status": status,
        "stage": stage,
        "read_only": True,
        "px4_commands_sent": False,
        "checks": checks,
        "evidence": {"camera_bench_report": str(camera_path), "mavlink_bench_report": str(mavlink_path)},
        "next_actions": next_actions,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate prop-off camera/Pixhawk evidence before calibration or training capture.")
    parser.add_argument("--camera-bench-report", required=True, type=Path)
    parser.add_argument("--mavlink-bench-report", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--confirm-propellers-removed", action="store_true")
    parser.add_argument("--calibration-file", type=Path)
    parser.add_argument("--camera-to-body-file", type=Path)
    parser.add_argument("--require-training-capture-inputs", action="store_true")
    args = parser.parse_args()
    try:
        report = evaluate_prop_off_preflight(
            camera_bench_report=args.camera_bench_report,
            mavlink_bench_report=args.mavlink_bench_report,
            propellers_removed_confirmed=args.confirm_propellers_removed,
            calibration_file=args.calibration_file,
            camera_to_body_file=args.camera_to_body_file,
            require_training_capture_inputs=args.require_training_capture_inputs,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
