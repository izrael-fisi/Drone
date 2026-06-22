from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from vision_nav.field_capture_metadata import (
    CAPTURE_METADATA_SCHEMA_VERSION,
    audit_capture_metadata,
    capture_metadata_template,
    parse_capture_metadata_json,
)
from vision_nav.replay_case_registry import normalize_conditions


TEXT_UPDATE_FIELDS = {
    "operator": "operator",
    "capture_date_utc": "capture_date_utc",
    "location_label": "location_label",
    "lighting": "lighting",
    "weather": "weather",
    "terrain_texture": "terrain_texture",
    "map_age_or_season_notes": "map_age_or_season_notes",
    "camera_focus_exposure_notes": "camera_focus_exposure_notes",
    "imu_px4_state_notes": "imu_px4_state_notes",
    "safety_notes": "safety_notes",
    "notes": "notes",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Patch proof-grade capture metadata for a field replay condition."
    )
    parser.add_argument("--manifest", required=True, help="Active field_manifest.json to update.")
    parser.add_argument("--condition", required=True, help="Required field condition to update.")
    parser.add_argument("--json-updates", help="Optional JSON object merged into capture_metadata.")
    parser.add_argument("--operator")
    parser.add_argument("--capture-date-utc")
    parser.add_argument("--location-label")
    parser.add_argument("--altitude-agl-m", type=float)
    parser.add_argument("--speed-mps", type=float)
    parser.add_argument("--lighting")
    parser.add_argument("--weather")
    parser.add_argument("--terrain-texture")
    parser.add_argument("--map-age-or-season-notes")
    parser.add_argument("--camera-focus-exposure-notes")
    parser.add_argument("--imu-px4-state-notes")
    parser.add_argument("--safety-notes")
    parser.add_argument("--notes")
    parser.add_argument("--json", action="store_true", help="Emit JSON only.")
    return parser.parse_args()


def update_field_capture_metadata(
    *,
    manifest_path: str | Path,
    condition: str,
    updates: dict[str, Any],
) -> dict[str, Any]:
    manifest_file = Path(manifest_path).expanduser()
    if not manifest_file.exists():
        raise FileNotFoundError(f"Field manifest does not exist: {manifest_file}")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("Field manifest must be a JSON object.")
    cases = manifest.get("cases")
    if not isinstance(cases, list):
        raise ValueError("Field manifest must contain a cases array.")

    condition_key = normalize_conditions([condition])
    if len(condition_key) != 1:
        raise ValueError(f"Exactly one field condition is required, got: {condition!r}")
    condition_name = condition_key[0]
    case = find_field_case(cases, condition_name)
    if case is None:
        raise ValueError(f"No field case found for condition: {condition_name}")

    expected = str(case.get("expected") or "")
    bundle = str(case.get("bundle") or "")
    existing = case.get("capture_metadata") if isinstance(case.get("capture_metadata"), dict) else None
    metadata = existing or capture_metadata_template(
        site_name=infer_site_name(manifest, case),
        condition=condition_name,
        expected=expected,
        bundle=bundle,
        notes=str(case.get("notes") or ""),
    )
    metadata = dict(metadata)
    metadata.setdefault("schema_version", CAPTURE_METADATA_SCHEMA_VERSION)
    metadata.setdefault("condition", condition_name)
    metadata.setdefault("expected_behavior", expected)
    if bundle:
        metadata.setdefault("bundle", bundle)
    metadata.update({key: value for key, value in updates.items() if value is not None})
    metadata["updated_at_utc"] = datetime.now(timezone.utc).isoformat()

    case["capture_metadata"] = metadata
    issues = audit_capture_metadata(metadata, conditions=[condition_name], expected=expected or None)
    manifest_file.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "status": "updated",
        "manifest_path": str(manifest_file),
        "condition": condition_name,
        "case_name": case.get("case_name"),
        "expected": expected,
        "capture_metadata_status": "passed" if not issues else "failed",
        "capture_metadata_issue_count": len(issues),
        "capture_metadata_issues": issues,
        "capture_metadata": metadata,
    }


def find_field_case(cases: list[Any], condition: str) -> dict[str, Any] | None:
    matches: list[dict[str, Any]] = []
    for case in cases:
        if not isinstance(case, dict):
            continue
        if case.get("dataset_type") != "field":
            continue
        case_conditions = normalize_conditions([str(value) for value in case.get("conditions") or []])
        if condition in case_conditions:
            matches.append(case)
    if not matches:
        return None
    real_cases = [case for case in matches if not case.get("template_status")]
    if real_cases:
        return sorted(real_cases, key=lambda item: str(item.get("case_name") or ""))[0]
    return sorted(matches, key=lambda item: str(item.get("case_name") or ""))[0]


def infer_site_name(manifest: dict[str, Any], case: dict[str, Any]) -> str:
    template = manifest.get("template") if isinstance(manifest.get("template"), dict) else {}
    site_name = template.get("site_name") or case.get("site_name")
    if isinstance(site_name, str) and site_name.strip():
        return site_name
    case_name = str(case.get("case_name") or "field-site")
    condition_values = normalize_conditions([str(value) for value in case.get("conditions") or []])
    if condition_values and case_name.endswith(f"-{condition_values[0]}"):
        return case_name[: -len(condition_values[0]) - 1] or "field-site"
    return "field-site"


def updates_from_args(args: argparse.Namespace) -> dict[str, Any]:
    updates: dict[str, Any] = {}
    if args.json_updates:
        parsed = parse_capture_metadata_json(args.json_updates)
        if parsed:
            updates.update(parsed)
    args_dict = vars(args)
    for arg_name, metadata_key in TEXT_UPDATE_FIELDS.items():
        value = args_dict.get(arg_name)
        if value is not None:
            updates[metadata_key] = value
    if args.altitude_agl_m is not None:
        updates["flight_altitude_agl_m"] = args.altitude_agl_m
    if args.speed_mps is not None:
        updates["speed_mps"] = args.speed_mps
    return updates


def print_human(result: dict[str, Any]) -> None:
    print(f"Updated field capture metadata: {result['case_name']}")
    print(f"Manifest: {result['manifest_path']}")
    print(f"Condition: {result['condition']}")
    print(f"Metadata status: {result['capture_metadata_status']}")
    if result["capture_metadata_issues"]:
        print("Metadata issues:")
        for issue in result["capture_metadata_issues"][:8]:
            print(f"- {issue.get('field')}: {issue.get('message')}")


def main() -> None:
    args = parse_args()
    try:
        result = update_field_capture_metadata(
            manifest_path=args.manifest,
            condition=args.condition,
            updates=updates_from_args(args),
        )
    except Exception as exc:
        result = {"status": "failed", "error": str(exc)}
        if args.json:
            print(json.dumps(result, indent=2, sort_keys=True))
        else:
            print(f"Field capture metadata update failed: {exc}")
        raise SystemExit(1)
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print_human(result)


if __name__ == "__main__":
    main()
