from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def runtime_status_snapshot(
    *,
    bundle: Any,
    output_dir: Path,
    log_path: Path,
    sequence: int,
    record: dict[str, Any],
    status_counts: dict[str, int],
    started_at_utc: str,
) -> dict[str, Any]:
    result = record.get("result") or {}
    estimator = result.get("estimator") if isinstance(result.get("estimator"), dict) else {}
    external_position = record.get("external_position_health")
    telemetry = record.get("telemetry") if isinstance(record.get("telemetry"), list) else []
    latest_telemetry = telemetry[-1] if telemetry else None
    return {
        "schema_version": "vision_nav_runtime_status_v1",
        "started_at_utc": started_at_utc,
        "updated_at_utc": record.get("timestamp_utc"),
        "sequence": sequence,
        "active_map": {
            "bundle_id": bundle.manifest.get("bundle_id"),
            "bundle_dir": str(bundle.bundle_dir),
            "manifest_path": str(bundle.manifest_path),
            "orthophoto_path": str(bundle.orthophoto_path),
            "tile_index_path": str(bundle.tile_index_path) if bundle.tile_index_path else None,
            "has_tile_index": bundle.has_tile_index,
            "crs": bundle.crs,
            "gsd_m": bundle.gsd_m,
        },
        "output": {
            "output_dir": str(output_dir),
            "log_path": str(log_path),
            "latest_frame_path": record.get("frame_path"),
        },
        "last_match": {
            "status": result.get("status"),
            "reason": result.get("reason"),
            "tile_id": result.get("tile_id"),
            "confidence": result.get("confidence"),
            "scale_confidence": result.get("scale_confidence"),
            "inliers": result.get("inliers"),
            "reprojection_error_px": result.get("reprojection_error_px"),
            "local_enu_m": result.get("local_enu_m"),
            "lat_lon": result.get("lat_lon"),
        },
        "estimator": {
            "initialized": estimator.get("initialized"),
            "health": estimator.get("health") or result.get("status"),
            "last_update_timestamp_us": estimator.get("last_update_timestamp_us"),
            "covariance": result.get("covariance"),
            "altitude_source": result.get("altitude_source"),
            "scale_confidence": result.get("scale_confidence"),
        },
        "external_position": external_position,
        "mavlink": record.get("mavlink"),
        "ros2": {
            "published": (record.get("ros2") or {}).get("published") if isinstance(record.get("ros2"), dict) else None,
            "skip_reason": (record.get("ros2") or {}).get("skip_reason") if isinstance(record.get("ros2"), dict) else None,
        },
        "telemetry": {
            "sample_count": len(telemetry),
            "latest_message_type": latest_telemetry.get("message_type") if isinstance(latest_telemetry, dict) else None,
            "latest_timestamp_us": latest_telemetry.get("timestamp_us") if isinstance(latest_telemetry, dict) else None,
        },
        "timing": {
            "capture_duration_s": record.get("capture_duration_s"),
            "match_duration_s": record.get("match_duration_s"),
        },
        "status_counts": dict(sorted(status_counts.items())),
    }


def write_runtime_status(path: Path, status: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n")
