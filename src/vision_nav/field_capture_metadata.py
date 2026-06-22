from __future__ import annotations

import json
from typing import Any


CAPTURE_METADATA_SCHEMA_VERSION = "vision_nav_field_capture_metadata_v1"
CAPTURE_CHECKLIST_SCHEMA_VERSION = "vision_nav_field_capture_checklist_v1"


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
