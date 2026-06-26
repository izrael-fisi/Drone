from __future__ import annotations

import json
from typing import Any


CAPTURE_METADATA_SCHEMA_VERSION = "vision_nav_field_capture_metadata_v1"
CAPTURE_CHECKLIST_SCHEMA_VERSION = "vision_nav_field_capture_checklist_v1"
REQUIRED_CAPTURE_TEXT_FIELDS = (
    "site_name",
    "condition",
    "expected_behavior",
    "bundle",
    "operator",
    "capture_date_utc",
    "location_label",
    "lighting",
    "weather",
    "terrain_texture",
    "map_age_or_season_notes",
    "camera_focus_exposure_notes",
    "imu_px4_state_notes",
    "safety_notes",
)
REQUIRED_CAPTURE_NUMERIC_FIELDS = ("flight_altitude_agl_m", "speed_mps")


def capture_metadata_template(
    *,
    site_name: str,
    condition: str,
    expected: str,
    bundle: str,
    notes: str = "",
) -> dict[str, Any]:
    return {
        "schema_version": CAPTURE_METADATA_SCHEMA_VERSION,
        "site_name": site_name,
        "condition": condition,
        "expected_behavior": expected,
        "bundle": bundle,
        "operator": "TODO",
        "capture_date_utc": "TODO",
        "location_label": "TODO",
        "flight_altitude_agl_m": None,
        "speed_mps": None,
        "lighting": "TODO",
        "weather": "TODO",
        "terrain_texture": "TODO",
        "map_age_or_season_notes": "TODO",
        "camera_focus_exposure_notes": "TODO",
        "imu_px4_state_notes": "TODO",
        "airframe": "Holybro X500 V2 or TODO",
        "prop_state": "prop-off bench or TODO",
        "camera_mount_rigidity_notes": "TODO",
        "vibration_blur_check_notes": "TODO",
        "gps_degraded_unavailable_scenario": "TODO",
        "runtime_profile": "pi5_full",
        "camera_profile": "rgb_global_shutter",
        "safety_notes": "TODO",
        "notes": notes,
    }


def capture_checklist_template(condition: str) -> dict[str, Any]:
    return {
        "schema_version": CAPTURE_CHECKLIST_SCHEMA_VERSION,
        "condition": condition,
        "items": [
            {"key": "mission_bundle_selected", "status": "todo"},
            {"key": "gnss_denied_prep_complete", "status": "todo"},
            {"key": "camera_focus_exposure_checked", "status": "todo"},
            {"key": "camera_mount_rigidity_checked", "status": "todo"},
            {"key": "vibration_blur_checked", "status": "todo"},
            {"key": "imu_px4_attitude_available", "status": "todo"},
            {"key": "gps_degraded_or_unavailable_scenario_marked", "status": "todo"},
            {"key": "lens_clean", "status": "todo"},
            {"key": "time_sync_checked", "status": "todo"},
            {"key": "runtime_log_saved", "status": "todo"},
            {"key": "condition_representative", "status": "todo"},
            {"key": "operator_notes_recorded", "status": "todo"},
        ],
    }


def parse_capture_metadata_json(value: str | None) -> dict[str, Any] | None:
    if value is None or not value.strip():
        return None
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise ValueError("Capture metadata JSON must be an object.")
    parsed.setdefault("schema_version", CAPTURE_METADATA_SCHEMA_VERSION)
    return parsed


def audit_capture_metadata(
    metadata: Any,
    *,
    conditions: list[str] | tuple[str, ...] | None = None,
    expected: str | None = None,
) -> list[dict[str, str]]:
    issues: list[dict[str, str]] = []
    if not isinstance(metadata, dict):
        return [
            {
                "severity": "error",
                "field": "capture_metadata",
                "message": "Field replay case is missing capture metadata.",
            }
        ]

    schema_version = metadata.get("schema_version")
    if schema_version != CAPTURE_METADATA_SCHEMA_VERSION:
        issues.append(
            {
                "severity": "error",
                "field": "capture_metadata.schema_version",
                "message": f"Capture metadata schema must be {CAPTURE_METADATA_SCHEMA_VERSION}.",
            }
        )

    for field in REQUIRED_CAPTURE_TEXT_FIELDS:
        value = metadata.get(field)
        if not is_filled_text(value):
            issues.append(
                {
                    "severity": "error",
                    "field": f"capture_metadata.{field}",
                    "message": f"Capture metadata field {field} must be filled before field evidence can pass.",
                }
            )

    for field in REQUIRED_CAPTURE_NUMERIC_FIELDS:
        value = metadata.get(field)
        if not isinstance(value, (int, float)) or isinstance(value, bool):
            issues.append(
                {
                    "severity": "error",
                    "field": f"capture_metadata.{field}",
                    "message": f"Capture metadata field {field} must be numeric.",
                }
            )
            continue
        if field == "flight_altitude_agl_m" and float(value) <= 0.0:
            issues.append(
                {
                    "severity": "error",
                    "field": f"capture_metadata.{field}",
                    "message": "Capture altitude must be greater than zero meters AGL.",
                }
            )
        if field == "speed_mps" and float(value) < 0.0:
            issues.append(
                {
                    "severity": "error",
                    "field": f"capture_metadata.{field}",
                    "message": "Capture speed cannot be negative.",
                }
            )

    if expected and metadata.get("expected_behavior") not in {None, expected}:
        issues.append(
            {
                "severity": "error",
                "field": "capture_metadata.expected_behavior",
                "message": f"Capture metadata expected_behavior must match replay expected value {expected}.",
            }
        )

    condition_set = {str(condition) for condition in conditions or [] if condition}
    metadata_condition = metadata.get("condition")
    if condition_set and isinstance(metadata_condition, str) and metadata_condition not in condition_set:
        issues.append(
            {
                "severity": "error",
                "field": "capture_metadata.condition",
                "message": "Capture metadata condition must match one of the replay case conditions.",
            }
        )
    return issues


def is_filled_text(value: Any) -> bool:
    if not isinstance(value, str):
        return False
    normalized = value.strip().lower()
    if not normalized:
        return False
    return not normalized.startswith("todo")
