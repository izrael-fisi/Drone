"""Build immutable DatasetRelease records from validated Pi capture sessions."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Iterable

from vision_nav.training_capture import validate_training_session

from .releases import DatasetRelease


DATASET_RELEASE_SCHEMA = "vision_nav_dataset_release_v1"


def _digest(value: Any) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()


def create_dataset_release(
    session_dirs: Iterable[str | Path],
    *,
    release_id: str,
    version: str,
    source_revision: str,
    license_summary: str,
    proprietary: bool = True,
    require_attitude: bool = True,
    require_sensor_timestamps: bool = True,
    require_offline_gnss_reference: bool = True,
    require_mavlink_timesync: bool = True,
    attitude_timing_policy: str = "exposure_native_attitude_v1",
) -> dict[str, Any]:
    sessions = []
    for session_dir in session_dirs:
        validation = validate_training_session(
            session_dir,
            require_attitude=require_attitude,
            require_sensor_timestamps=require_sensor_timestamps,
            require_offline_gnss_reference=require_offline_gnss_reference,
            require_mavlink_timesync=require_mavlink_timesync,
            attitude_timing_policy=attitude_timing_policy,
        )
        if validation["status"] != "valid":
            detail = "; ".join(validation["errors"])
            raise ValueError(f"Training session {session_dir} is not eligible: {detail}")
        manifest = validation["manifest"]
        sessions.append(
            {
                "session_id": manifest["session_id"],
                "path": str(Path(session_dir).expanduser().resolve()),
                "manifest_sha256": _digest(manifest),
                "frame_count": int(manifest["frame_count"]),
                "calibration_id": manifest["calibration"]["calibration_id"],
                "map_release_id": manifest["map_release_id"],
                "map_reference": manifest.get("map_reference", {"release_id": manifest["map_release_id"], "kind": "legacy_unspecified"}),
                "extrinsic_id": (manifest.get("camera_to_body") or {}).get("extrinsic_id"),
                "attitude_sample_count": int(manifest["mavlink"]["attitude_sample_count"]),
                "frame_attitude_alignment": manifest["mavlink"].get("frame_attitude_alignment", {}),
                "mavlink_timesync": manifest["mavlink"].get("timesync", {}),
            }
        )
    if not sessions:
        raise ValueError("At least one validated training capture session is required.")
    session_ids = [item["session_id"] for item in sessions]
    if len(set(session_ids)) != len(session_ids):
        raise ValueError("Dataset release cannot include the same capture session more than once.")
    checksum = _digest(sessions)
    release = DatasetRelease(
        release_id=release_id,
        version=version,
        source_revision=source_revision,
        sources=tuple(item["path"] for item in sessions),
        license_summary=license_summary,
        checksum_sha256=checksum,
        proprietary=proprietary,
        capture_session_ids=tuple(session_ids),
        calibration_ids=tuple(sorted({item["calibration_id"] for item in sessions})),
        map_release_ids=tuple(sorted({item["map_release_id"] for item in sessions})),
        extrinsic_ids=tuple(sorted({str(item["extrinsic_id"]) for item in sessions if item.get("extrinsic_id")})),
        frame_count=sum(item["frame_count"] for item in sessions),
    )
    return {
        "schema_version": DATASET_RELEASE_SCHEMA,
        **release.to_dict(),
        "capture_sessions": sessions,
        "eligibility": {
            "requires_px4_attitude": require_attitude,
            "requires_sensor_timestamps": require_sensor_timestamps,
            "requires_offline_gnss_reference": require_offline_gnss_reference,
            "requires_mavlink_timesync": require_mavlink_timesync,
            "attitude_timing_policy": attitude_timing_policy,
            "gnss_truth_policy": "offline_training_only",
        },
    }


def load_dataset_release(path: str | Path) -> dict[str, Any]:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_dataset_release(raw)
    return raw


def validate_dataset_release(raw: dict[str, Any]) -> None:
    if raw.get("schema_version") != DATASET_RELEASE_SCHEMA:
        raise ValueError(f"Dataset release must use {DATASET_RELEASE_SCHEMA}.")
    release = DatasetRelease.from_dict(raw)
    sessions = raw.get("capture_sessions")
    if not isinstance(sessions, list) or not sessions:
        raise ValueError("Dataset release requires capture_sessions.")
    if sorted(item.get("session_id") for item in sessions) != sorted(release.capture_session_ids):
        raise ValueError("Dataset release session IDs do not match capture_sessions.")
    if release.gnss_truth_policy != "offline_training_only":
        raise ValueError("Dataset release GNSS policy must be offline_training_only.")
    if release.frame_count != sum(int(item.get("frame_count", 0)) for item in sessions):
        raise ValueError("Dataset release frame_count does not match capture sessions.")
    expected_checksum = _digest(
        [
            {
                "session_id": item["session_id"],
                "path": item["path"],
                "manifest_sha256": item["manifest_sha256"],
                "frame_count": int(item["frame_count"]),
                "calibration_id": item["calibration_id"],
                "map_release_id": item["map_release_id"],
                "map_reference": item.get("map_reference", {"release_id": item["map_release_id"], "kind": "legacy_unspecified"}),
                "extrinsic_id": item.get("extrinsic_id"),
                "attitude_sample_count": int(item["attitude_sample_count"]),
                "frame_attitude_alignment": item.get("frame_attitude_alignment", {}),
                "mavlink_timesync": item.get("mavlink_timesync", {}),
            }
            for item in sessions
        ]
    )
    if release.checksum_sha256 != expected_checksum:
        raise ValueError("Dataset release checksum does not match capture session manifests.")


def main() -> None:
    parser = argparse.ArgumentParser(description="Create an immutable DatasetRelease from validated Pi capture sessions.")
    parser.add_argument("--session", action="append", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--license-summary", required=True)
    parser.add_argument("--public", action="store_true", help="Mark this release as non-proprietary.")
    parser.add_argument("--allow-missing-attitude", action="store_true")
    parser.add_argument("--allow-missing-sensor-timestamps", action="store_true")
    parser.add_argument("--attitude-timing-policy", choices=("exposure_native_attitude_v1", "legacy_host_receipt"), default="exposure_native_attitude_v1")
    parser.add_argument(
        "--allow-missing-offline-gnss-reference",
        action="store_true",
        help="Bench-only: do not accept this DatasetRelease as supervised learned-navigation training data.",
    )
    parser.add_argument(
        "--allow-missing-mavlink-timesync",
        action="store_true",
        help="Bench-only: do not accept this DatasetRelease as time-synchronized field-training data.",
    )
    args = parser.parse_args()
    try:
        release = create_dataset_release(
            args.session,
            release_id=args.release_id,
            version=args.version,
            source_revision=args.source_revision,
            license_summary=args.license_summary,
            proprietary=not args.public,
            require_attitude=not args.allow_missing_attitude,
            require_sensor_timestamps=not args.allow_missing_sensor_timestamps,
            require_offline_gnss_reference=not args.allow_missing_offline_gnss_reference,
            require_mavlink_timesync=not args.allow_missing_mavlink_timesync,
            attitude_timing_policy=args.attitude_timing_policy,
        )
        validate_dataset_release(release)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(release, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"release_id": release["release_id"], "output": str(args.output), "frame_count": release["frame_count"]}))


if __name__ == "__main__":
    main()
