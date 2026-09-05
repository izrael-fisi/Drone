"""Fail-closed readiness check before recording a learned-navigation session.

This combines previously recorded prop-off evidence with the selected immutable
map release and the target capture directory.  It is a planning check only: it
never opens MAVLink, captures a frame, or commands PX4.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import shutil
from typing import Any, Callable

from ..prop_off_preflight import PROP_OFF_PREFLIGHT_SCHEMA
from .map_release import load_release
from .reference_map import load_reference_map


TRAINING_CAPTURE_PREFLIGHT_SCHEMA = "vision_nav_training_capture_preflight_v1"
DEFAULT_MIN_FREE_GIB = 20.0
DEFAULT_ESTIMATED_FRAME_BYTES = 8 * 1024 * 1024


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _load_json(path: str | Path, label: str) -> tuple[Path, dict[str, Any]]:
    source = Path(path).expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"{label} does not exist: {source}")
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ValueError(f"{label} is not valid JSON: {source}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must be a JSON object: {source}")
    return source, raw


def _check(name: str, status: str, message: str, **details: Any) -> dict[str, Any]:
    return {"name": name, "status": status, "message": message, "details": details}


def _existing_parent(path: Path) -> Path:
    current = path.expanduser().resolve()
    while not current.exists() and current != current.parent:
        current = current.parent
    return current


def evaluate_training_capture_preflight(
    *,
    prop_off_report: str | Path,
    map_release: str | Path | None = None,
    reference_map: str | Path | None = None,
    calibration_id: str,
    capture_output_dir: str | Path,
    capture_frame_count: int = 120,
    min_free_gib: float = DEFAULT_MIN_FREE_GIB,
    estimated_frame_bytes: int = DEFAULT_ESTIMATED_FRAME_BYTES,
    disk_usage: Callable[[str | Path], shutil._ntuple_diskusage] = shutil.disk_usage,
) -> dict[str, Any]:
    """Evaluate prerequisites for a timestamped camera/PX4 training session."""

    if not calibration_id.strip():
        raise ValueError("calibration_id is required.")
    if capture_frame_count <= 0:
        raise ValueError("capture_frame_count must be positive.")
    if min_free_gib <= 0.0:
        raise ValueError("min_free_gib must be positive.")
    if estimated_frame_bytes <= 0:
        raise ValueError("estimated_frame_bytes must be positive.")
    if (map_release is None) == (reference_map is None):
        raise ValueError("Provide exactly one deployment map_release or model-independent reference_map.")

    checks: list[dict[str, Any]] = []
    prop_path, prop = _load_json(prop_off_report, "Prop-off preflight report")
    prop_ready = (
        prop.get("schema_version") == PROP_OFF_PREFLIGHT_SCHEMA
        and prop.get("status") == "passed"
        and prop.get("stage") == "ready_for_training_capture"
    )
    checks.append(
        _check(
            "prop_off_evidence",
            "passed" if prop_ready else "failed",
            "Prop-off camera, MAVLink, calibration, and extrinsics evidence is ready for training capture."
            if prop_ready else "A passing prop-off report at ready_for_training_capture is required.",
            path=str(prop_path), schema_version=prop.get("schema_version"), report_status=prop.get("status"), stage=prop.get("stage"),
        )
    )

    map_path = Path(map_release or reference_map or "").expanduser().resolve()
    active_map: dict[str, Any] | None = None
    map_kind = "deployment_map_release" if map_release is not None else "reference_map_release"
    try:
        active_map = load_release(map_path) if map_release is not None else load_reference_map(map_path)
        calibration_compatible = calibration_id in active_map.get("calibration_ids", [])
        checks.append(
            _check(
                "map_reference",
                "passed" if calibration_compatible else "failed",
                "Selected immutable map reference is valid and compatible with the training camera."
                if calibration_compatible else "Selected map reference does not list the training camera calibration.",
                kind=map_kind,
                path=str(map_path), release_id=active_map.get("release_id"), map_id=active_map.get("map_id"),
                model_release_id=active_map.get("model_release_id"), calibration_ids=active_map.get("calibration_ids", []),
            )
        )
    except (OSError, ValueError, json.JSONDecodeError) as exc:
        checks.append(
            _check("map_reference", "failed", "Selected immutable map reference could not be validated.", kind=map_kind, path=str(map_path), error=str(exc))
        )

    capture_path = Path(capture_output_dir).expanduser().resolve()
    output_empty = not capture_path.exists() or not any(capture_path.iterdir())
    checks.append(
        _check(
            "capture_output_directory",
            "passed" if output_empty else "failed",
            "Capture output is empty or will be created by the recorder."
            if output_empty else "Capture output already contains files; choose a new session directory.",
            path=str(capture_path), exists=capture_path.exists(),
        )
    )
    storage_parent = _existing_parent(capture_path)
    usage = disk_usage(storage_parent)
    requested_bytes = max(int(min_free_gib * 1024 ** 3), capture_frame_count * estimated_frame_bytes * 2)
    storage_ready = int(usage.free) >= requested_bytes
    checks.append(
        _check(
            "capture_storage",
            "passed" if storage_ready else "failed",
            "Pi storage has sufficient headroom for raw training evidence."
            if storage_ready else "Pi storage is below the configured training-capture headroom.",
            path=str(storage_parent), free_bytes=int(usage.free), total_bytes=int(usage.total),
            required_free_bytes=requested_bytes, min_free_gib=min_free_gib,
            capture_frame_count=capture_frame_count, estimated_frame_bytes=estimated_frame_bytes,
        )
    )

    status = "passed" if all(check["status"] == "passed" for check in checks) else "failed"
    return {
        "schema_version": TRAINING_CAPTURE_PREFLIGHT_SCHEMA,
        "generated_at_utc": _utc_now(),
        "status": status,
        "read_only": True,
        "px4_commands_sent": False,
        "selected_inputs": {
            "map_release": str(map_path),
            "map_release_id": active_map.get("release_id") if active_map else None,
            "map_reference_kind": map_kind,
            "calibration_id": calibration_id,
            "capture_output_dir": str(capture_path),
        },
        "recording_environment": (
            {"VISION_NAV_REFERENCE_MAP_ID": active_map.get("release_id"), "VISION_NAV_MAP_REFERENCE_KIND": "reference_map_release"}
            if map_kind == "reference_map_release" and active_map else
            {"VISION_NAV_MAP_RELEASE_ID": active_map.get("release_id"), "VISION_NAV_MAP_REFERENCE_KIND": "deployment_map_release"}
            if active_map else {}
        ),
        "checks": checks,
        "next_action": (
            "./scripts/pi/record_training_capture.sh" if status == "passed"
            else "Resolve every failed check before recording a training session."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Check prop-off, map, calibration, and storage readiness before training capture.")
    parser.add_argument("--prop-off-report", required=True, type=Path)
    map_inputs = parser.add_mutually_exclusive_group(required=True)
    map_inputs.add_argument("--map-release", type=Path, help="Deployment MapRelease v2 when learned assets already exist.")
    map_inputs.add_argument("--reference-map", type=Path, help="Model-independent ReferenceMapRelease for first data collection.")
    parser.add_argument("--calibration-id", required=True)
    parser.add_argument("--capture-output-dir", required=True, type=Path)
    parser.add_argument("--capture-frame-count", type=int, default=120)
    parser.add_argument("--min-free-gib", type=float, default=DEFAULT_MIN_FREE_GIB)
    parser.add_argument("--estimated-frame-bytes", type=int, default=DEFAULT_ESTIMATED_FRAME_BYTES)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        report = evaluate_training_capture_preflight(
            prop_off_report=args.prop_off_report,
            map_release=args.map_release,
            reference_map=args.reference_map,
            calibration_id=args.calibration_id,
            capture_output_dir=args.capture_output_dir,
            capture_frame_count=args.capture_frame_count,
            min_free_gib=args.min_free_gib,
            estimated_frame_bytes=args.estimated_frame_bytes,
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
