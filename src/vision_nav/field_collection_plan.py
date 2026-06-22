from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import shlex
from typing import Any

from vision_nav.field_capture_metadata import (
    capture_checklist_template,
    capture_metadata_template,
    is_filled_text,
)
from vision_nav.field_conditions import (
    REQUIRED_FIELD_CONDITIONS,
    expected_behavior_for_condition,
    label_for_condition,
    notes_for_condition,
)
from vision_nav.replay_case_manifest import sanitize_filename
from vision_nav.replay_case_registry import normalize_conditions


SCHEMA_VERSION = "vision_nav_field_collection_plan_v1"
METADATA_UPDATE_TEXT_ENV_FIELDS = (
    ("VISION_NAV_FIELD_OPERATOR", "operator", "TODO_operator"),
    ("VISION_NAV_FIELD_LOCATION_LABEL", "location_label", "TODO_location_label"),
    ("VISION_NAV_FIELD_LIGHTING", "lighting", "TODO_lighting"),
    ("VISION_NAV_FIELD_WEATHER", "weather", "TODO_weather"),
    ("VISION_NAV_FIELD_TERRAIN_TEXTURE", "terrain_texture", "TODO_terrain_texture"),
    ("VISION_NAV_FIELD_MAP_AGE_OR_SEASON_NOTES", "map_age_or_season_notes", "TODO_map_age_or_season_notes"),
    ("VISION_NAV_FIELD_CAMERA_FOCUS_EXPOSURE_NOTES", "camera_focus_exposure_notes", "TODO_camera_focus_exposure_notes"),
    ("VISION_NAV_FIELD_IMU_PX4_STATE_NOTES", "imu_px4_state_notes", "TODO_imu_px4_state_notes"),
    ("VISION_NAV_FIELD_SAFETY_NOTES", "safety_notes", "TODO_safety_notes"),
)
METADATA_UPDATE_NUMERIC_ENV_FIELDS = (
    ("VISION_NAV_FIELD_ALTITUDE_AGL_M", "flight_altitude_agl_m", "TODO_altitude_agl_m"),
    ("VISION_NAV_FIELD_SPEED_MPS", "speed_mps", "TODO_speed_mps"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create an operator checklist for collecting required real field replay evidence."
    )
    parser.add_argument(
        "--manifest",
        default="data/replay_cases/field_manifest.json",
        help="Active field replay manifest to inspect. Missing files are treated as no cases registered.",
    )
    parser.add_argument(
        "--output",
        default="data/replay_cases/field_collection_plan.json",
        help="JSON collection plan path to write.",
    )
    parser.add_argument(
        "--markdown-output",
        help="Optional Markdown checklist path to write.",
    )
    parser.add_argument("--site-name", default="field-site", help="Short site or test-area label.")
    parser.add_argument(
        "--bundle",
        default="TODO: mission_bundle path or map provenance",
        help="Bundle path or provenance label to use in generated registration commands.",
    )
    parser.add_argument(
        "--source-log",
        default="$HOME/DroneTransfer/outgoing/terrain-match/terrain_matches.jsonl",
        help="Legacy Pi-side terrain runtime/replay log path. Kept for compatibility with older collection plans.",
    )
    parser.add_argument(
        "--capture-root",
        default="$HOME/DroneTransfer/outgoing/field-captures",
        help="Pi-side directory where condition-specific terrain captures should be written.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON only.")
    return parser.parse_args()


def create_field_collection_plan(
    *,
    manifest_path: str | Path,
    output_path: str | Path | None = None,
    markdown_output_path: str | Path | None = None,
    site_name: str = "field-site",
    bundle: str = "TODO: mission_bundle path or map provenance",
    source_log: str = "$HOME/DroneTransfer/outgoing/terrain-match/terrain_matches.jsonl",
    capture_root: str = "$HOME/DroneTransfer/outgoing/field-captures",
) -> dict[str, Any]:
    manifest = Path(manifest_path).expanduser()
    manifest_data = load_manifest_or_empty(manifest)
    template = manifest_data.get("template") if isinstance(manifest_data.get("template"), dict) else {}
    if site_name == "field-site":
        template_site_name = template.get("site_name")
        if isinstance(template_site_name, str) and template_site_name.strip():
            site_name = template_site_name
    cases = [case for case in manifest_data.get("cases") or [] if isinstance(case, dict)]
    conditions = [
        condition_plan(
            condition=condition,
            manifest_path=manifest,
            collection_plan_path=Path(output_path).expanduser() if output_path is not None else None,
            cases=cases,
            site_name=site_name,
            bundle=bundle,
            source_log=source_log,
            capture_root=capture_root,
        )
        for condition in REQUIRED_FIELD_CONDITIONS
    ]
    summary = {
        "required_count": len(REQUIRED_FIELD_CONDITIONS),
        "registered_count": sum(1 for item in conditions if item["status"] == "registered"),
        "registered_missing_log_count": sum(1 for item in conditions if item["status"] == "registered_missing_log"),
        "placeholder_count": sum(1 for item in conditions if item["status"] == "placeholder"),
        "missing_count": sum(1 for item in conditions if item["status"] == "missing"),
    }
    pending_conditions = [item for item in conditions if item["status"] != "registered"]
    all_registered = summary["registered_count"] == summary["required_count"]
    next_condition = pending_conditions[0] if pending_conditions else None
    plan = {
        "schema_version": SCHEMA_VERSION,
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "status": "passed" if all_registered else "degraded",
        "manifest_path": str(manifest),
        "manifest_exists": manifest.exists(),
        "site_name": site_name,
        "bundle": bundle,
        "source_log": source_log,
        "capture_root": capture_root,
        "pending_capture_command_count": sum(1 for item in pending_conditions if item.get("capture_command")),
        "pending_preflight_command_count": sum(1 for item in pending_conditions if item.get("preflight_command")),
        "pending_preflight_capture_command_count": sum(
            1 for item in pending_conditions if item.get("preflight_capture_command")
        ),
        "pending_metadata_update_command_count": sum(
            1 for item in pending_conditions if item.get("metadata_update_command")
        ),
        "pending_registration_command_count": sum(1 for item in pending_conditions if item.get("register_command")),
        "capture_output_dir_count": sum(1 for item in conditions if item.get("capture_output_dir")),
        "runtime_status_path_count": sum(1 for item in conditions if item.get("runtime_status_path")),
        "condition_source_log_count": sum(1 for item in conditions if item.get("source_log")),
        "next_condition": next_condition,
        "summary": summary,
        "conditions": conditions,
        "next_steps": next_steps(summary),
    }
    if output_path is not None:
        output = Path(output_path).expanduser()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(plan, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        plan["output_path"] = str(output)
    if markdown_output_path is not None:
        markdown = Path(markdown_output_path).expanduser()
        markdown.parent.mkdir(parents=True, exist_ok=True)
        markdown.write_text(render_field_collection_markdown(plan), encoding="utf-8")
        plan["markdown_output_path"] = str(markdown)
    return plan


def load_manifest_or_empty(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"version": "0.1.0", "cases": []}
    raw = json.loads(path.read_text())
    if not isinstance(raw, dict):
        raise ValueError(f"Replay manifest must be a JSON object: {path}")
    raw.setdefault("cases", [])
    return raw


def condition_plan(
    *,
    condition: str,
    manifest_path: Path,
    collection_plan_path: Path | None,
    cases: list[dict[str, Any]],
    site_name: str,
    bundle: str,
    source_log: str,
    capture_root: str,
) -> dict[str, Any]:
    matching = [case for case in cases if condition in normalize_conditions([str(value) for value in case.get("conditions") or []])]
    selected = select_best_case(matching, manifest_path)
    site_slug = sanitize_filename(site_name)
    generated_case_name = sanitize_filename(f"{site_slug}-{condition}")
    capture_output_dir = remote_path_join(capture_root, generated_case_name)
    planned_source_log = remote_path_join(capture_output_dir, "terrain_matches.jsonl")
    runtime_status_path = remote_path_join(capture_output_dir, "runtime_status.json")
    expected = expected_behavior_for_condition(condition)
    status = "missing"
    manifest_log_path: str | None = None
    log_exists: bool | None = None
    notes = notes_for_condition(condition)
    case_name = generated_case_name
    if selected is not None:
        case_name = str(selected.get("case_name") or generated_case_name)
        expected = str(selected.get("expected") or expected)
        if selected.get("bundle"):
            bundle = str(selected.get("bundle"))
        manifest_log_path = str(selected.get("log") or "") or None
        notes = str(selected.get("notes") or notes)
        log_exists = case_log_exists(manifest_path, selected)
        if selected.get("template_status"):
            status = "placeholder"
        elif log_exists:
            status = "registered"
        else:
            status = "registered_missing_log"
    capture_metadata = selected.get("capture_metadata") if isinstance(selected, dict) else None
    if not isinstance(capture_metadata, dict):
        capture_metadata = capture_metadata_template(
            site_name=site_name,
            condition=condition,
            expected=expected,
            bundle=bundle,
            notes=notes,
        )
    capture_checklist = selected.get("capture_checklist") if isinstance(selected, dict) else None
    if not isinstance(capture_checklist, dict):
        capture_checklist = capture_checklist_template(condition)
    register_env = {
        "VISION_NAV_FIELD_CASE_NAME": case_name,
        "VISION_NAV_FIELD_EXPECTED": expected,
        "VISION_NAV_FIELD_CONDITION": condition,
        "VISION_NAV_FIELD_LOG": planned_source_log,
        "VISION_NAV_FIELD_BUNDLE": bundle,
        "VISION_NAV_FIELD_NOTES": notes,
        "VISION_NAV_FIELD_CAPTURE_METADATA": json.dumps(capture_metadata, sort_keys=True),
        "VISION_NAV_FIELD_REPLACE": "1",
    }
    capture_env = {
        "VISION_NAV_BUNDLE": bundle,
        "VISION_NAV_OUTPUT_DIR": capture_output_dir,
        "VISION_NAV_COUNT": "30",
    }
    metadata_update_env = metadata_update_env_for_condition(
        manifest_path=manifest_path,
        condition=condition,
        capture_metadata=capture_metadata,
    )
    metadata_update_command = shell_command(metadata_update_env, "./scripts/pi/update_field_capture_metadata.sh")
    capture_command = shell_command(capture_env, "./scripts/pi/run_terrain_nav_loop.sh")
    preflight_command = (
        preflight_command_for_condition(plan_path=collection_plan_path, condition=condition)
        if collection_plan_path is not None
        else shell_command({"VISION_NAV_FIELD_CONDITION": condition}, "./scripts/pi/preflight_field_capture.sh")
    )
    capture_with_status_command = command_with_runtime_status_read(
        capture_command,
        runtime_status_root=capture_output_dir,
    )
    preflight_capture_command = command_sequence(preflight_command, capture_with_status_command)
    return {
        "condition": condition,
        "label": label_for_condition(condition),
        "expected": expected,
        "status": status,
        "notes": notes,
        "case_name": case_name,
        "manifest_log_path": manifest_log_path,
        "manifest_log_exists": log_exists,
        "source_log": planned_source_log,
        "legacy_source_log": source_log,
        "capture_output_dir": capture_output_dir,
        "runtime_status_path": runtime_status_path,
        "bundle": bundle,
        "capture_metadata": capture_metadata,
        "capture_checklist": capture_checklist,
        "capture_env": capture_env,
        "preflight_command": preflight_command,
        "preflight_capture_command": preflight_capture_command,
        "capture_command": capture_with_status_command,
        "metadata_update_env": metadata_update_env,
        "metadata_update_command": metadata_update_command,
        "register_env": register_env,
        "register_command": shell_command(register_env, "./scripts/pi/register_field_replay_case.sh"),
    }


def metadata_update_command_for_condition(
    *,
    manifest_path: str | Path,
    condition: str,
    capture_metadata: dict[str, Any] | None = None,
) -> str:
    return shell_command(
        metadata_update_env_for_condition(
            manifest_path=manifest_path,
            condition=condition,
            capture_metadata=capture_metadata,
        ),
        "./scripts/pi/update_field_capture_metadata.sh",
    )


def preflight_command_for_condition(*, plan_path: str | Path, condition: str) -> str:
    return shell_command(
        {
            "VISION_NAV_FIELD_COLLECTION_PLAN": str(plan_path),
            "VISION_NAV_FIELD_CONDITION": condition,
        },
        "./scripts/pi/preflight_field_capture.sh",
    )


def metadata_update_command_is_detailed(command: str | None) -> bool:
    if not isinstance(command, str) or not command.strip():
        return False
    return any(
        marker in command
        for marker in (
            "VISION_NAV_FIELD_OPERATOR",
            "--operator",
            "VISION_NAV_FIELD_CAPTURE_METADATA",
            "--json-updates",
        )
    )


def metadata_update_env_for_condition(
    *,
    manifest_path: str | Path,
    condition: str,
    capture_metadata: dict[str, Any] | None = None,
) -> dict[str, str]:
    metadata = capture_metadata if isinstance(capture_metadata, dict) else {}
    env = {
        "VISION_NAV_FIELD_MANIFEST": str(manifest_path),
        "VISION_NAV_FIELD_CONDITION": str(condition),
    }
    capture_date = metadata.get("capture_date_utc")
    if is_filled_text(capture_date):
        env["VISION_NAV_FIELD_CAPTURE_DATE_UTC"] = str(capture_date)
    for env_key, metadata_key, placeholder in METADATA_UPDATE_TEXT_ENV_FIELDS:
        value = metadata.get(metadata_key)
        env[env_key] = str(value) if is_filled_text(value) else placeholder
    for env_key, metadata_key, placeholder in METADATA_UPDATE_NUMERIC_ENV_FIELDS:
        value = metadata.get(metadata_key)
        env[env_key] = str(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else placeholder
    notes = metadata.get("notes")
    if is_filled_text(notes):
        env["VISION_NAV_FIELD_NOTES"] = str(notes)
    return env


def select_best_case(cases: list[dict[str, Any]], manifest_path: Path) -> dict[str, Any] | None:
    if not cases:
        return None
    registered_existing = [case for case in cases if not case.get("template_status") and case_log_exists(manifest_path, case)]
    if registered_existing:
        return sorted(registered_existing, key=lambda case: str(case.get("case_name") or ""))[0]
    registered = [case for case in cases if not case.get("template_status")]
    if registered:
        return sorted(registered, key=lambda case: str(case.get("case_name") or ""))[0]
    return sorted(cases, key=lambda case: str(case.get("case_name") or ""))[0]


def case_log_exists(manifest_path: Path, case: dict[str, Any]) -> bool:
    log = case.get("log")
    if not log:
        return False
    path = Path(str(log)).expanduser()
    if not path.is_absolute():
        path = manifest_path.parent / path
    return path.exists()


def shell_command(env: dict[str, str], command: str) -> str:
    parts = [f"{key}={shell_env_value(value)}" for key, value in env.items()]
    return " \\\n  ".join(parts + [command])


def shell_env_value(value: Any) -> str:
    text = str(value)
    expandable_prefixes = ("$HOME/", "${HOME}/", "$PWD/", "${PWD}/")
    if text.startswith(expandable_prefixes) and all(ch not in text for ch in " \t\n\"'`;&|<>"):
        return text
    return shlex.quote(text)


def command_with_runtime_status_read(command: str, runtime_status_root: str | None = None) -> str:
    read_command = "./scripts/pi/read_runtime_status.sh"
    if runtime_status_root:
        read_command = shell_command(
            {"VISION_NAV_RUNTIME_STATUS_ROOTS": runtime_status_root},
            "./scripts/pi/read_runtime_status.sh",
        )
    if "read_runtime_status.sh" in command:
        if runtime_status_root and "VISION_NAV_RUNTIME_STATUS_ROOTS" not in command:
            return command.replace("./scripts/pi/read_runtime_status.sh", read_command)
        return command
    return f"{command} && {read_command}"


def command_sequence(*commands: str | None) -> str:
    return " && ".join(command.strip() for command in commands if isinstance(command, str) and command.strip())


def remote_path_join(root: str, *parts: str) -> str:
    normalized = str(root).rstrip("/")
    suffix = "/".join(str(part).strip("/") for part in parts if str(part).strip("/"))
    if not normalized:
        return suffix
    if not suffix:
        return normalized
    return f"{normalized}/{suffix}"


def next_steps(summary: dict[str, int]) -> list[str]:
    if summary["registered_count"] == summary["required_count"]:
        return [
            "Run ./scripts/pi/run_feature_method_benchmark.sh against the latest real field log.",
            "Run ./scripts/pi/run_threshold_tuning_report.sh against the complete field manifest.",
            "Run ./scripts/pi/run_autonomy_readiness_audit.sh after PX4 receiver proof is available.",
        ]
    return [
        "Capture a real terrain runtime/replay log for each unchecked condition.",
        "Run the generated registration command for each condition after capture.",
        "Repeat until placeholder_count, missing_count, and registered_missing_log_count are all zero.",
        "Then run ./scripts/pi/run_threshold_tuning_report.sh.",
    ]


def render_field_collection_markdown(plan: dict[str, Any]) -> str:
    summary = plan.get("summary") or {}
    lines = [
        "# Field Evidence Collection Plan",
        "",
        f"- Status: {plan.get('status')}",
        f"- Site: {plan.get('site_name')}",
        f"- Manifest: `{plan.get('manifest_path')}`",
        f"- Bundle: `{plan.get('bundle')}`",
        f"- Capture root: `{plan.get('capture_root')}`",
        f"- Registered: {summary.get('registered_count', 0)}/{summary.get('required_count', 0)}",
        f"- Placeholder: {summary.get('placeholder_count', 0)}",
        f"- Missing: {summary.get('missing_count', 0)}",
        f"- Registered missing log: {summary.get('registered_missing_log_count', 0)}",
        "",
        "## Checklist",
        "",
    ]
    next_condition = plan.get("next_condition") if isinstance(plan.get("next_condition"), dict) else None
    if next_condition:
        lines.extend(
            [
                "## Next Pending Condition",
                "",
                f"- Condition: `{next_condition.get('condition')}`",
                f"- Expected behavior: `{next_condition.get('expected')}`",
                f"- Current status: `{next_condition.get('status')}`",
                f"- Capture output: `{next_condition.get('capture_output_dir')}`",
                f"- Terrain log: `{next_condition.get('source_log')}`",
                f"- Runtime status: `{next_condition.get('runtime_status_path')}`",
                "",
                "Preflight:",
                "",
                "```bash",
                str(next_condition.get("preflight_command") or ""),
                "```",
                "",
                "Preflight and capture:",
                "",
                "```bash",
                str(next_condition.get("preflight_capture_command") or ""),
                "```",
                "",
                "Capture:",
                "",
                "```bash",
                str(next_condition.get("capture_command") or ""),
                "```",
                "",
                "Update capture metadata:",
                "",
                "```bash",
                str(next_condition.get("metadata_update_command") or ""),
                "```",
                "",
                "Register:",
                "",
                "```bash",
                str(next_condition.get("register_command") or ""),
                "```",
                "",
            ]
        )
    for item in plan.get("conditions") or []:
        checked = "x" if item.get("status") == "registered" else " "
        lines.append(f"- [{checked}] {item.get('label')} (`{item.get('condition')}`) - {item.get('status')}")
    lines.extend(["", "## Conditions", ""])
    for item in plan.get("conditions") or []:
        lines.extend(
            [
                f"### {item.get('label')}",
                "",
                f"- Condition: `{item.get('condition')}`",
                f"- Expected behavior: `{item.get('expected')}`",
                f"- Current status: `{item.get('status')}`",
                f"- Capture output: `{item.get('capture_output_dir')}`",
                f"- Terrain log: `{item.get('source_log')}`",
                f"- Runtime status: `{item.get('runtime_status_path')}`",
                f"- Notes: {item.get('notes') or 'n/a'}",
                "",
                "Preflight the capture setup:",
                "",
                "```bash",
                str(item.get("preflight_command") or ""),
                "```",
                "",
                "Preflight and capture:",
                "",
                "```bash",
                str(item.get("preflight_capture_command") or ""),
                "```",
                "",
                "Capture metadata to fill before registration:",
                "",
                "```json",
                json.dumps(item.get("capture_metadata") or {}, indent=2, sort_keys=True),
                "```",
                "",
                "Update the capture metadata:",
                "",
                "```bash",
                str(item.get("metadata_update_command") or ""),
                "```",
                "",
                "Checklist:",
                "",
            ]
        )
        checklist = item.get("capture_checklist") if isinstance(item.get("capture_checklist"), dict) else {}
        for checklist_item in checklist.get("items") or []:
            if not isinstance(checklist_item, dict):
                continue
            key = str(checklist_item.get("key") or "").replace("_", " ")
            status = checklist_item.get("status") or "todo"
            lines.append(f"- [ ] {key} (`{status}`)")
        lines.extend(
            [
                "",
                "Capture or replay a representative log:",
                "",
                "```bash",
                str(item.get("capture_command") or ""),
                "```",
                "",
                "Register the captured evidence:",
                "",
                "```bash",
                str(item.get("register_command") or ""),
                "```",
                "",
            ]
        )
    lines.extend(["## Next Steps", ""])
    for step in plan.get("next_steps") or []:
        lines.append(f"- {step}")
    lines.append("")
    return "\n".join(lines)


def print_human(plan: dict[str, Any]) -> None:
    summary = plan["summary"]
    print(f"Field collection plan: {plan.get('output_path') or '(not written)'}")
    if plan.get("markdown_output_path"):
        print(f"Markdown checklist: {plan['markdown_output_path']}")
    print(f"Status: {plan['status']}")
    print(
        "Registered: "
        f"{summary['registered_count']}/{summary['required_count']} "
        f"(placeholder {summary['placeholder_count']}, missing {summary['missing_count']}, "
        f"registered missing log {summary['registered_missing_log_count']})"
    )
    for item in plan["conditions"]:
        print(f"- {item['condition']}: {item['status']} expected={item['expected']}")
    next_condition = plan.get("next_condition") if isinstance(plan.get("next_condition"), dict) else None
    if next_condition:
        print(
            "Next pending: "
            f"{next_condition.get('condition')} "
            f"({next_condition.get('status')}, expected={next_condition.get('expected')})"
        )
        if next_condition.get("preflight_command"):
            print("Next preflight command:")
            print(next_condition["preflight_command"])
        if next_condition.get("preflight_capture_command"):
            print("Next preflight + capture command:")
            print(next_condition["preflight_capture_command"])
        if next_condition.get("capture_command"):
            print("Next capture command:")
            print(next_condition["capture_command"])
        if next_condition.get("runtime_status_path"):
            print("Next runtime status path:")
            print(next_condition["runtime_status_path"])
        if next_condition.get("metadata_update_command"):
            print("Next metadata update command:")
            print(next_condition["metadata_update_command"])
        if next_condition.get("register_command"):
            print("Next register command:")
            print(next_condition["register_command"])


def main() -> None:
    args = parse_args()
    plan = create_field_collection_plan(
        manifest_path=args.manifest,
        output_path=args.output,
        markdown_output_path=args.markdown_output,
        site_name=args.site_name,
        bundle=args.bundle,
        source_log=args.source_log,
        capture_root=args.capture_root,
    )
    if args.json:
        print(json.dumps(plan, indent=2, sort_keys=True))
    else:
        print_human(plan)
    if plan["status"] == "passed":
        return


if __name__ == "__main__":
    main()
