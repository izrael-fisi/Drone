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
    runtime_profile: dict[str, Any] | None = None,
    camera_profile: dict[str, Any] | None = None,
    hardware_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    result = record.get("result") or {}
    estimator = result.get("estimator") if isinstance(result.get("estimator"), dict) else {}
    external_position = record.get("external_position_health")
    telemetry = record.get("telemetry") if isinstance(record.get("telemetry"), list) else []
    latest_telemetry = telemetry[-1] if telemetry else None
    position_update = record.get("position_update") if isinstance(record.get("position_update"), dict) else {}
    active_map = active_map_snapshot(bundle)
    status = {
        "schema_version": "vision_nav_runtime_status_v2",
        "started_at_utc": started_at_utc,
        "updated_at_utc": record.get("timestamp_utc"),
        "sequence": sequence,
        "runtime_state": {
            "state": "running",
            "loop": "terrain_nav",
            "started_at_utc": started_at_utc,
            "updated_at_utc": record.get("timestamp_utc"),
            "sequence": sequence,
        },
        "active_map": active_map,
        "active_bundle": {
            "state": "active",
            "bundle_id": active_map.get("bundle_id"),
            "bundle_dir": active_map.get("bundle_dir"),
            "manifest_path": active_map.get("manifest_path"),
            "tile_index_path": active_map.get("tile_index_path"),
        },
        "camera_health": camera_health_snapshot(record, camera_profile),
        "mavlink_health": mavlink_health_snapshot(record),
        "gps_health": position_update.get("gps_health") or {"healthy": False, "reason": "not_reported"},
        "vision_health": position_update.get("vision_health") or vision_health_from_result(result),
        "source_state": position_update.get("source_state") or "no_position",
        "fix_cadence": position_update.get("fix_cadence") or {},
        "hardware_profile": hardware_profile or {},
        "runtime_profile": runtime_profile or {},
        "camera_profile": camera_profile or {},
        "readiness_gates": readiness_gates(active_map, record, position_update, camera_profile),
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
        "position_update": position_update or record.get("position_update"),
        "mavlink": record.get("mavlink"),
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
    return status


def bridge_status_snapshot(
    *,
    output_dir: Path,
    sequence: int,
    record: dict[str, Any],
    status_counts: dict[str, int],
    started_at_utc: str,
    active_bundle_path: str | None = None,
    runtime_profile: dict[str, Any] | None = None,
    camera_profile: dict[str, Any] | None = None,
    hardware_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    position_update = record.get("position_update") if isinstance(record.get("position_update"), dict) else {}
    active_map = {
        "bundle_id": None,
        "bundle_dir": active_bundle_path,
        "manifest_path": None,
        "orthophoto_path": None,
        "tile_index_path": None,
        "has_tile_index": False,
        "crs": None,
        "gsd_m": None,
    }
    return {
        "schema_version": "vision_nav_runtime_status_v2",
        "started_at_utc": started_at_utc,
        "updated_at_utc": record.get("timestamp_utc"),
        "sequence": sequence,
        "runtime_state": {
            "state": "standby",
            "loop": "status_bridge",
            "started_at_utc": started_at_utc,
            "updated_at_utc": record.get("timestamp_utc"),
            "sequence": sequence,
        },
        "active_map": active_map,
        "active_bundle": {
            "state": "missing" if not active_bundle_path else "configured",
            "bundle_id": None,
            "bundle_dir": active_bundle_path,
            "manifest_path": None,
            "tile_index_path": None,
        },
        "camera_health": camera_health_snapshot(record, camera_profile),
        "mavlink_health": mavlink_health_snapshot(record),
        "gps_health": position_update.get("gps_health") or {"healthy": False, "reason": "not_reported"},
        "vision_health": position_update.get("vision_health") or {"available": False, "status": "not_running"},
        "source_state": position_update.get("source_state") or "no_position",
        "fix_cadence": position_update.get("fix_cadence") or {},
        "hardware_profile": hardware_profile or {},
        "runtime_profile": runtime_profile or {},
        "camera_profile": camera_profile or {},
        "readiness_gates": readiness_gates(active_map, record, position_update, camera_profile),
        "output": {
            "output_dir": str(output_dir),
            "log_path": None,
            "latest_frame_path": None,
        },
        "last_match": {
            "status": "not_running",
            "reason": "status_bridge_has_no_terrain_match",
            "tile_id": None,
            "confidence": None,
            "scale_confidence": None,
            "inliers": None,
            "reprojection_error_px": None,
            "local_enu_m": None,
            "lat_lon": None,
        },
        "estimator": {
            "initialized": False,
            "health": "not_running",
            "last_update_timestamp_us": None,
            "covariance": None,
            "altitude_source": None,
            "scale_confidence": None,
        },
        "external_position": record.get("external_position_health"),
        "position_update": position_update or record.get("position_update"),
        "mavlink": record.get("mavlink"),
        "telemetry": {
            "sample_count": len(record.get("telemetry") or []),
            "latest_message_type": latest_telemetry(record).get("message_type") if latest_telemetry(record) else None,
            "latest_timestamp_us": latest_telemetry(record).get("timestamp_us") if latest_telemetry(record) else None,
        },
        "timing": {},
        "status_counts": dict(sorted(status_counts.items())),
    }


def write_runtime_status(path: Path, status: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(status, indent=2, sort_keys=True) + "\n")


def active_map_snapshot(bundle: Any) -> dict[str, Any]:
    return {
        "bundle_id": bundle.manifest.get("bundle_id"),
        "bundle_dir": str(bundle.bundle_dir),
        "manifest_path": str(bundle.manifest_path),
        "orthophoto_path": str(bundle.orthophoto_path),
        "tile_index_path": str(bundle.tile_index_path) if bundle.tile_index_path else None,
        "has_tile_index": bundle.has_tile_index,
        "crs": bundle.crs,
        "gsd_m": bundle.gsd_m,
    }


def latest_telemetry(record: dict[str, Any]) -> dict[str, Any] | None:
    telemetry = record.get("telemetry") if isinstance(record.get("telemetry"), list) else []
    return telemetry[-1] if telemetry and isinstance(telemetry[-1], dict) else None


def camera_health_snapshot(record: dict[str, Any], profile: dict[str, Any] | None) -> dict[str, Any]:
    camera = record.get("camera_health") if isinstance(record.get("camera_health"), dict) else {}
    return {
        "status": camera.get("status") or ("ready" if (profile or {}).get("id") == "rgb_global_shutter" else "metadata_only"),
        "profile": (profile or {}).get("id"),
        "label": (profile or {}).get("label"),
        "message": camera.get("message") or (profile or {}).get("notes"),
        "frame_path": record.get("frame_path"),
    }


def mavlink_health_snapshot(record: dict[str, Any]) -> dict[str, Any]:
    telemetry = record.get("telemetry") if isinstance(record.get("telemetry"), list) else []
    mavlink = record.get("mavlink") if isinstance(record.get("mavlink"), dict) else {}
    external = record.get("external_position_health") if isinstance(record.get("external_position_health"), dict) else {}
    connected = bool(telemetry or mavlink.get("sent") or mavlink.get("connected") or external)
    return {
        "connected": connected,
        "status": external.get("status") or ("connected" if connected else "not_connected"),
        "message_type": external.get("message_type") or mavlink.get("message"),
        "telemetry_sample_count": len(telemetry),
        "last_message_type": latest_telemetry(record).get("message_type") if latest_telemetry(record) else None,
    }


def vision_health_from_result(result: dict[str, Any]) -> dict[str, Any]:
    return {
        "available": bool(result.get("lat_lon")),
        "status": result.get("status"),
        "confidence": result.get("confidence"),
        "tile_id": result.get("tile_id"),
        "inliers": result.get("inliers"),
        "reprojection_error_px": result.get("reprojection_error_px"),
    }


def readiness_gates(
    active_map: dict[str, Any],
    record: dict[str, Any],
    position_update: dict[str, Any],
    camera_profile: dict[str, Any] | None = None,
) -> dict[str, Any]:
    camera_health = camera_health_snapshot(record, camera_profile)
    mavlink_health = mavlink_health_snapshot(record)
    return {
        "map_source_selected": bool(active_map.get("bundle_dir")),
        "terrain_bundle_built": bool(active_map.get("manifest_path")),
        "bundle_uploaded": None,
        "bundle_activated": active_map.get("has_tile_index") is True,
        "home_reset_point_set": False,
        "camera_health_passing": camera_health.get("status") in {"ready", "healthy", "passed"},
        "mavlink_endpoint_reachable": mavlink_health.get("connected") is True,
        "px4_external_vision_parameter_check": "not_checked",
        "gps_health_reported": bool(position_update.get("gps_health")),
        "vision_health_reported": bool(position_update.get("vision_health")),
    }
