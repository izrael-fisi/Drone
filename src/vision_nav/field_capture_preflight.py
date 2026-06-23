from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shlex
from typing import Any

from vision_nav.field_capture_metadata import audit_capture_metadata
from vision_nav.field_collection_plan import (
    command_sequence,
    command_with_runtime_status_read,
    metadata_update_command_for_condition,
    metadata_update_command_is_detailed,
    preflight_command_for_condition,
)


SCHEMA_VERSION = "vision_nav_field_capture_preflight_v1"
CAPTURE_BLOCKING_CHECKS = {
    "plan",
    "condition",
    "bundle_path",
    "capture_output_parent",
    "terrain_runtime_wrapper",
    "runtime_status_wrapper",
    "capture_command",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Check whether the next field collection condition is ready for terrain-log capture."
    )
    parser.add_argument("--plan", required=True, help="field_collection_plan.json path.")
    parser.add_argument("--condition", help="Optional condition key. Defaults to the plan next_condition.")
    parser.add_argument("--repo-root", default=".", help="Repository root containing scripts/pi wrappers.")
    parser.add_argument("--output", help="Optional JSON report output path.")
    parser.add_argument("--capture-script-output", help="Optional executable shell script path for the ready capture command.")
    parser.add_argument("--json", action="store_true", help="Emit JSON only.")
    return parser.parse_args()


def evaluate_field_capture_preflight(
    *,
    plan_path: str | Path,
    condition: str | None = None,
    repo_root: str | Path = ".",
    output_path: str | Path | None = None,
    capture_script_output: str | Path | None = None,
) -> dict[str, Any]:
    plan_file = Path(plan_path).expanduser()
    repo = Path(repo_root).expanduser()
    checks: list[dict[str, Any]] = []
    plan: dict[str, Any] = {}
    selected: dict[str, Any] | None = None

    if not plan_file.exists():
        checks.append(failed("plan", f"Field collection plan does not exist: {plan_file}"))
    else:
        try:
            raw_plan = json.loads(plan_file.read_text(encoding="utf-8"))
            if not isinstance(raw_plan, dict):
                raise ValueError("field_collection_plan root is not a JSON object")
            plan = raw_plan
            checks.append(passed("plan", "Field collection plan is readable.", {"path": str(plan_file)}))
        except Exception as exc:
            checks.append(failed("plan", f"Could not parse field collection plan: {exc}", {"path": str(plan_file)}))

    if plan:
        selected = select_condition(plan, condition)
        if selected is None:
            details = {"requested_condition": condition, "available_conditions": condition_keys(plan)}
            checks.append(failed("condition", "No pending field condition matched the requested selection.", details))
        else:
            selected = normalize_selected_condition(selected, plan=plan, plan_path=plan_file)
            checks.append(
                passed(
                    "condition",
                    "Selected field condition for capture preflight.",
                    {
                        "condition": selected.get("condition"),
                        "status": selected.get("status"),
                        "expected": selected.get("expected"),
                    },
                )
            )

    if selected is not None:
        checks.extend(condition_checks(selected, repo))

    ready_for_capture = all(
        check.get("status") == "passed"
        for check in checks
        if check.get("name") in CAPTURE_BLOCKING_CHECKS
    ) and any(check.get("name") == "condition" and check.get("status") == "passed" for check in checks)
    ready_for_registration = ready_for_capture and check_status(checks, "registration_inputs") == "passed"
    if not ready_for_capture:
        status = "failed"
    elif ready_for_registration:
        status = "passed"
    else:
        status = "degraded"
    capture_script_path = None
    if ready_for_capture and selected is not None and capture_script_output is not None:
        capture_script_path = write_capture_script(
            capture_script_output,
            command=str(selected.get("capture_command") or ""),
            repo_root=repo,
            condition=selected,
        )
        if capture_script_path:
            selected["capture_script_path"] = capture_script_path

    report = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "plan_path": str(plan_file),
        "repo_root": str(repo),
        "condition": selected.get("condition") if selected else condition,
        "case_name": selected.get("case_name") if selected else None,
        "expected": selected.get("expected") if selected else None,
        "bundle_path": selected.get("bundle") if selected else None,
        "bundle_validation_command": selected.get("bundle_validation_command") if selected else None,
        "ready_for_capture": ready_for_capture,
        "ready_for_registration": ready_for_registration,
        "preflight_command": selected.get("preflight_command") if selected else None,
        "preflight_capture_command": selected.get("preflight_capture_command") if selected else None,
        "capture_command": selected.get("capture_command") if selected else None,
        "capture_script_path": capture_script_path,
        "metadata_update_command": selected.get("metadata_update_command") if selected else None,
        "register_command": selected.get("register_command") if selected else None,
        "capture_output_dir": selected.get("capture_output_dir") if selected else None,
        "source_log": selected.get("source_log") if selected else None,
        "runtime_status_path": selected.get("runtime_status_path") if selected else None,
        "checks": checks,
        "summary": summarize_checks(checks),
    }
    report["next_actions"] = build_next_actions(
        selected,
        checks,
        ready_for_capture=ready_for_capture,
        ready_for_registration=ready_for_registration,
    )
    if output_path is not None:
        output = Path(output_path).expanduser()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        report["output_path"] = str(output)
    return report


def build_next_actions(
    condition: dict[str, Any] | None,
    checks: list[dict[str, Any]],
    *,
    ready_for_capture: bool,
    ready_for_registration: bool,
) -> list[dict[str, Any]]:
    if condition is None:
        return []
    actions: list[dict[str, Any]] = []
    statuses = {str(item.get("name")): str(item.get("status") or "") for item in checks}
    bundle_diagnostic = compact_check_bundle_diagnostic(checks, "bundle_path")

    if statuses.get("bundle_path") != "passed" and condition.get("bundle_validation_command"):
        actions.append(
            action_item(
                "prepare_bundle",
                "action_required",
                "Build, upload, or validate the selected terrain bundle.",
                "Mission Planner > Build Bundle, Upload Bundle",
                command=str(condition["bundle_validation_command"]),
                bundle_path=str(condition.get("bundle") or ""),
                bundle_diagnostic=bundle_diagnostic,
                notes="Field capture cannot start until the selected mission bundle exists on the runtime module.",
            )
        )

    capture_waits_on = [
        name
        for name in (
            "plan",
            "condition",
            "bundle_path",
            "capture_output_parent",
            "terrain_runtime_wrapper",
            "runtime_status_wrapper",
            "capture_command",
        )
        if statuses.get(name) != "passed"
    ]
    if condition.get("capture_command"):
        actions.append(
            action_item(
                "capture_field_terrain_log",
                "ready" if ready_for_capture else "blocked",
                "Capture the terrain log and runtime status for this condition.",
                "Module Setup > Field Log Capture",
                command=str(condition["capture_command"]),
                waits_on=capture_waits_on,
                source_log=str(condition.get("source_log") or ""),
                runtime_status_path=str(condition.get("runtime_status_path") or ""),
                capture_output_dir=str(condition.get("capture_output_dir") or ""),
                preflight_capture_command=str(condition.get("preflight_capture_command") or ""),
                capture_script_path=str(condition.get("capture_script_path") or ""),
            )
        )

    metadata_status = statuses.get("capture_metadata")
    metadata_command_status = statuses.get("metadata_update_command")
    if (
        condition.get("metadata_update_command")
        and (metadata_status != "passed" or metadata_command_status not in {"passed", ""})
    ):
        actions.append(
            action_item(
                "complete_capture_metadata",
                "action_required" if metadata_command_status == "passed" else "blocked",
                "Fill proof-grade field metadata for the selected condition.",
                "Module Setup > Field Evidence Case > Update Metadata",
                command=str(condition["metadata_update_command"]),
                waits_on=[] if metadata_command_status == "passed" else ["metadata_update_command"],
                notes="Registration remains blocked until operator, site, lighting, weather, camera, IMU/PX4, altitude, speed, and safety metadata are complete.",
            )
        )

    registration_waits_on = list((check_details(checks, "registration_inputs") or {}).get("missing") or [])
    if condition.get("register_command"):
        actions.append(
            action_item(
                "register_field_replay_case",
                "ready" if ready_for_registration else "blocked",
                "Register the captured terrain log as field evidence.",
                "Module Setup > Field Evidence Case > Register",
                command=str(condition["register_command"]),
                waits_on=registration_waits_on,
                source_log=str(condition.get("source_log") or ""),
                runtime_status_path=str(condition.get("runtime_status_path") or ""),
            )
        )
    return actions


def compact_check_bundle_diagnostic(checks: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    details = check_details(checks, name) or {}
    diagnostic = details.get("diagnostic")
    if not diagnostic:
        return None
    from vision_nav.bundle_diagnostics import compact_bundle_diagnostic

    return compact_bundle_diagnostic(diagnostic)


def action_item(
    action_id: str,
    status: str,
    title: str,
    desktop_action: str,
    *,
    command: str | None = None,
    waits_on: list[str] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": action_id,
        "status": status,
        "title": title,
        "desktop_action": desktop_action,
    }
    if command:
        item["command"] = command
    if waits_on:
        item["waits_on"] = waits_on
    for key, value in extra.items():
        if value not in (None, "", []):
            item[key] = value
    return item


def select_condition(plan: dict[str, Any], condition: str | None) -> dict[str, Any] | None:
    if condition:
        requested = condition.strip()
        for item in plan.get("conditions") or []:
            if isinstance(item, dict) and item.get("condition") == requested and item.get("status") != "registered":
                return item
        return None
    next_condition = plan.get("next_condition")
    if isinstance(next_condition, dict):
        return next_condition
    for item in plan.get("conditions") or []:
        if isinstance(item, dict) and item.get("status") != "registered":
            return item
    return None


def normalize_selected_condition(
    condition: dict[str, Any],
    *,
    plan: dict[str, Any],
    plan_path: str | Path,
) -> dict[str, Any]:
    normalized = dict(condition)
    condition_key = str(normalized.get("condition") or "").strip()
    if condition_key and not normalized.get("preflight_command"):
        normalized["preflight_command"] = preflight_command_for_condition(
            plan_path=plan_path,
            condition=condition_key,
        )
    bundle_path = str(normalized.get("bundle") or "").strip()
    if bundle_path:
        normalized["bundle_validation_command"] = bundle_validation_command(bundle_path)
    capture_command = str(normalized.get("capture_command") or "").strip()
    if capture_command:
        normalized["capture_command"] = command_with_runtime_status_read(
            capture_command,
            runtime_status_root=str(normalized.get("capture_output_dir") or "").strip() or None,
        )
    preflight_capture_command = str(normalized.get("preflight_capture_command") or "").strip()
    if not preflight_capture_command:
        normalized["preflight_capture_command"] = command_sequence(
            str(normalized.get("preflight_command") or "").strip(),
            str(normalized.get("capture_command") or "").strip(),
        )
    metadata_command = str(normalized.get("metadata_update_command") or "").strip()
    if condition_key and not metadata_update_command_is_detailed(metadata_command):
        manifest_path = plan.get("manifest_path")
        metadata = normalized.get("capture_metadata")
        if manifest_path:
            normalized["metadata_update_command"] = metadata_update_command_for_condition(
                manifest_path=str(manifest_path),
                condition=condition_key,
                capture_metadata=metadata if isinstance(metadata, dict) else None,
            )
    return normalized


def write_capture_script(
    path: str | Path,
    *,
    command: str,
    repo_root: Path,
    condition: dict[str, Any],
) -> str | None:
    command = marker_shell_command(command)
    if not command:
        return None
    output = Path(path).expanduser()
    output.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "#!/usr/bin/env bash",
        "set -euo pipefail",
        "",
        "# Generated by field_capture_preflight.py after a capture-ready preflight.",
        "# Re-run field capture preflight if the map bundle, camera,",
        "# selected condition, or output directory changes before capture.",
    ]
    for label, key in (
        ("condition", "condition"),
        ("case", "case_name"),
        ("bundle", "bundle"),
        ("output", "capture_output_dir"),
        ("terrain log", "source_log"),
        ("runtime status", "runtime_status_path"),
    ):
        value = condition.get(key)
        if value:
            lines.append(f"# {label}: {value}")
    lines.extend(
        [
            "",
            f"cd {shlex.quote(str(repo_root))}",
            command,
            "",
        ]
    )
    output.write_text("\n".join(lines), encoding="utf-8")
    output.chmod(0o755)
    return str(output)


def condition_keys(plan: dict[str, Any]) -> list[str]:
    return [
        str(item.get("condition"))
        for item in plan.get("conditions") or []
        if isinstance(item, dict) and item.get("condition")
    ]


def condition_checks(condition: dict[str, Any], repo: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    checks.append(bundle_path_check(condition.get("bundle"), condition.get("bundle_validation_command")))
    checks.append(capture_output_parent_check(condition.get("capture_output_dir")))
    checks.append(script_check(repo, "terrain_runtime_wrapper", "scripts/pi/run_terrain_nav_loop.sh"))
    checks.append(script_check(repo, "runtime_status_wrapper", "scripts/pi/read_runtime_status.sh"))
    checks.append(capture_command_check(str(condition.get("capture_command") or "")))
    checks.append(metadata_command_check(str(condition.get("metadata_update_command") or "")))
    checks.append(capture_metadata_check(condition))
    checks.append(registration_inputs_check(condition, checks))
    return checks


def bundle_validation_command(bundle_path: str) -> str:
    return f"VISION_NAV_BUNDLE={shlex.quote(bundle_path)} ./scripts/pi/validate_terrain_bundle.sh"


def bundle_path_check(value: Any, validation_command: Any = None) -> dict[str, Any]:
    path = expanded_path(value)
    command = str(validation_command or "").strip()
    diagnostic = None
    if path is not None:
        from vision_nav.bundle_diagnostics import diagnose_bundle_inputs

        diagnostic = diagnose_bundle_inputs(path)
    details = {
        "path": str(path) if path else None,
        "desktop_action": "Mission Planner > Build Bundle, Upload Bundle",
        "validation_command": command or None,
        "notes": "Build/upload the selected terrain bundle or set VISION_NAV_BUNDLE to the bundle used for this field plan.",
    }
    if diagnostic is not None:
        details["diagnostic"] = diagnostic
    if not path or not path.exists():
        return failed("bundle_path", "Mission bundle is missing.", details)

    validation = validate_selected_terrain_bundle(path)
    details["validation"] = validation
    if validation.get("status") != "passed":
        return failed("bundle_path", "Mission bundle exists but did not pass terrain validation.", details)
    return passed("bundle_path", "Mission terrain bundle exists and validates.", details)


def validate_selected_terrain_bundle(path: Path) -> dict[str, Any]:
    try:
        from vision_nav.terrain_bundle import summarize_terrain_bundle
        from vision_nav.validate_map_bundle import validate_bundle

        map_summary = validate_bundle(str(path), require_features=True)
        terrain_summary = summarize_terrain_bundle(path)
        issues = []
        for source, summary in (("map_bundle", map_summary), ("terrain_bundle", terrain_summary)):
            for issue in summary.get("issues") or []:
                if isinstance(issue, dict):
                    issues.append(
                        {
                            "source": source,
                            "severity": issue.get("severity"),
                            "message": issue.get("message"),
                        }
                    )
        status = "passed"
        if map_summary.get("status") != "passed" or terrain_summary.get("status") != "passed":
            status = "failed"
        return {
            "status": status,
            "map_bundle_status": map_summary.get("status"),
            "terrain_bundle_status": terrain_summary.get("status"),
            "bundle_id": map_summary.get("bundle_id") or terrain_summary.get("bundle_id"),
            "tile_count": terrain_summary.get("tile_count"),
            "feature_count": terrain_summary.get("feature_count"),
            "has_tile_index": terrain_summary.get("has_tile_index"),
            "issues": issues[:12],
        }
    except Exception as exc:
        return {
            "status": "failed",
            "error": str(exc),
        }


def capture_output_parent_check(value: Any) -> dict[str, Any]:
    path = expanded_path(value)
    if path is None:
        return failed("capture_output_parent", "Capture output directory is missing from the plan.")
    if path.exists() and path.is_dir() and os.access(path, os.W_OK):
        return passed("capture_output_parent", "Capture output directory exists and is writable.", {"path": str(path)})
    parent = path.parent
    if parent.exists() and parent.is_dir() and os.access(parent, os.W_OK):
        return passed(
            "capture_output_parent",
            "Capture output parent exists and the runtime can create the condition directory.",
            {"path": str(path), "parent": str(parent)},
        )
    ancestor = first_existing_ancestor(path)
    if ancestor and os.access(ancestor, os.W_OK):
        return passed(
            "capture_output_parent",
            "Capture output path can be created by the terrain runtime.",
            {"path": str(path), "nearest_existing": str(ancestor)},
        )
    message = "Capture output path parent is missing and no writable ancestor was found."
    return failed("capture_output_parent", message, {"path": str(path), "nearest_existing": str(ancestor) if ancestor else None})


def script_check(repo: Path, name: str, relative_path: str) -> dict[str, Any]:
    path = repo / relative_path
    if path.exists() and os.access(path, os.X_OK):
        return passed(name, f"{relative_path} exists and is executable.", {"path": str(path)})
    if path.exists():
        return failed(name, f"{relative_path} exists but is not executable.", {"path": str(path)})
    return failed(name, f"{relative_path} is missing.", {"path": str(path)})


def capture_command_check(command: str) -> dict[str, Any]:
    missing = [
        needle
        for needle in (
            "run_terrain_nav_loop.sh",
            "read_runtime_status.sh",
            "VISION_NAV_RUNTIME_STATUS_ROOTS",
            "VISION_NAV_OUTPUT_DIR",
            "VISION_NAV_COUNT",
        )
        if needle not in command
    ]
    if missing:
        return failed("capture_command", "Capture command is missing required runtime/status pieces.", {"missing": missing})
    return passed("capture_command", "Capture command records terrain log and runtime status.", {"command": command})


def metadata_command_check(command: str) -> dict[str, Any]:
    if metadata_update_command_is_detailed(command):
        return passed("metadata_update_command", "Metadata update command includes proof-grade field prompts.")
    if command.strip():
        return degraded("metadata_update_command", "Metadata update command exists but does not include proof-grade prompts.", {"command": command})
    return degraded("metadata_update_command", "Metadata update command is missing.")


def capture_metadata_check(condition: dict[str, Any]) -> dict[str, Any]:
    issues = audit_capture_metadata(
        condition.get("capture_metadata"),
        conditions=[str(condition.get("condition") or "")],
        expected=str(condition.get("expected") or "") or None,
    )
    details = {"issue_count": len(issues), "issues": issues[:12]}
    if issues:
        return degraded("capture_metadata", "Capture metadata still needs operator-filled field values.", details)
    return passed("capture_metadata", "Capture metadata is complete.", details)


def registration_inputs_check(condition: dict[str, Any], checks: list[dict[str, Any]]) -> dict[str, Any]:
    source_log = expanded_path(condition.get("source_log"))
    runtime_status = expanded_path(condition.get("runtime_status_path"))
    missing = []
    if not source_log or not source_log.exists():
        missing.append("terrain_matches.jsonl")
    if not runtime_status or not runtime_status.exists():
        missing.append("runtime_status.json")
    if check_status(checks, "capture_metadata") != "passed":
        missing.append("complete_capture_metadata")
    if not condition.get("register_command"):
        missing.append("register_command")
    details = {
        "source_log": str(source_log) if source_log else None,
        "runtime_status_path": str(runtime_status) if runtime_status else None,
        "missing": missing,
    }
    if missing:
        return degraded("registration_inputs", "Registration is not ready until capture outputs and metadata are complete.", details)
    return passed("registration_inputs", "Registration inputs are present.", details)


def expanded_path(value: Any) -> Path | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    return Path(os.path.expandvars(text)).expanduser()


def first_existing_ancestor(path: Path) -> Path | None:
    for candidate in [path, *path.parents]:
        if candidate.exists():
            return candidate
    return None


def summarize_checks(checks: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "passed": sum(1 for item in checks if item.get("status") == "passed"),
        "degraded": sum(1 for item in checks if item.get("status") == "degraded"),
        "failed": sum(1 for item in checks if item.get("status") == "failed"),
    }


def check_status(checks: list[dict[str, Any]], name: str) -> str | None:
    for item in checks:
        if item.get("name") == name:
            return str(item.get("status") or "")
    return None


def check_details(checks: list[dict[str, Any]], name: str) -> dict[str, Any] | None:
    for item in checks:
        if item.get("name") == name and isinstance(item.get("details"), dict):
            return item["details"]
    return None


def check(status: str, name: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    item: dict[str, Any] = {"name": name, "status": status, "message": message}
    if details:
        item["details"] = details
    return item


def passed(name: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return check("passed", name, message, details)


def degraded(name: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return check("degraded", name, message, details)


def failed(name: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return check("failed", name, message, details)


def print_human(report: dict[str, Any]) -> None:
    print(f"Field capture preflight: {report['status']}")
    print(f"Condition: {report.get('condition') or 'none'}")
    print(f"Ready for capture: {'yes' if report.get('ready_for_capture') else 'no'}")
    print(f"Ready for registration: {'yes' if report.get('ready_for_registration') else 'no'}")
    if report.get("capture_output_dir"):
        print(f"Capture output: {report['capture_output_dir']}")
    if report.get("source_log"):
        print(f"Terrain log: {report['source_log']}")
    if report.get("runtime_status_path"):
        print(f"Runtime status: {report['runtime_status_path']}")
    if report.get("bundle_path"):
        print(f"Bundle: {report['bundle_path']}")
    for item in report.get("checks") or []:
        status = item.get("status")
        name = item.get("name")
        message = item.get("message")
        print(f"- {name} [{status}]: {message}")
    if report.get("capture_command"):
        print("Capture command:")
        print(report["capture_command"])
    if report.get("capture_script_path"):
        print(f"Capture script: {report['capture_script_path']}")
    if report.get("preflight_capture_command"):
        print("Preflight + capture command:")
        print(report["preflight_capture_command"])
    if report.get("bundle_validation_command"):
        print("Bundle validation command:")
        print(report["bundle_validation_command"])
    if report.get("metadata_update_command"):
        print("Metadata update command:")
        print(report["metadata_update_command"])
    if report.get("register_command"):
        print("Register command:")
        print(report["register_command"])
    if report.get("next_actions"):
        print("Next actions:")
        for action in report["next_actions"]:
            print(f"- {action.get('id')} [{action.get('status')}]: {action.get('title')}")
            if action.get("desktop_action"):
                print(f"  app: {action['desktop_action']}")
            if action.get("waits_on"):
                print(f"  waits on: {', '.join(str(item) for item in action['waits_on'])}")
            diagnostic = action.get("bundle_diagnostic")
            if isinstance(diagnostic, dict):
                missing = diagnostic.get("missing_required_files") or []
                if missing:
                    print(f"  missing bundle files: {', '.join(str(item) for item in missing[:8])}")
                candidates = [
                    item
                    for item in diagnostic.get("bundle_candidates") or []
                    if isinstance(item, dict) and item.get("path")
                ]
                if candidates:
                    print("  detected bundle candidates:")
                    for candidate in candidates[:3]:
                        warning = " (example/synthetic only)" if candidate.get("field_proof_warning") else ""
                        print(f"    - {candidate.get('path')}{warning}")
                map_sources = [
                    item
                    for item in diagnostic.get("map_source_candidates") or []
                    if isinstance(item, dict) and item.get("path")
                ]
                if map_sources:
                    print("  detected map sources:")
                    for source in map_sources[:3]:
                        label_parts = [str(source.get("name") or "unnamed")]
                        if source.get("source_format"):
                            label_parts.append(str(source["source_format"]))
                        if source.get("requires_import"):
                            label_parts.append("import required")
                        print(f"    - {source.get('path')} [{'; '.join(label_parts)}]")
                search_roots = [str(item) for item in diagnostic.get("search_roots") or [] if str(item)]
                if search_roots:
                    print("  searched roots:")
                    for root in search_roots[:5]:
                        print(f"    - {root}")
                recommended = [
                    item
                    for item in diagnostic.get("recommended_actions") or []
                    if isinstance(item, dict)
                ]
                if recommended:
                    print("  recommended bundle actions:")
                    for recommendation in recommended[:3]:
                        title = recommendation.get("title") or recommendation.get("id") or "bundle action"
                        status = recommendation.get("status") or "unknown"
                        print(f"    - {title} [{status}]")
                        if recommendation.get("notes"):
                            print(f"      notes: {recommendation['notes']}")
                        if recommendation.get("desktop_action"):
                            print(f"      app: {recommendation['desktop_action']}")
                        if recommendation.get("command"):
                            print(f"      command: {recommendation['command']}")
                        if recommendation.get("mission_plan_path"):
                            print(f"      mission plan: {recommendation['mission_plan_path']}")
                        if recommendation.get("qgc_plan_path"):
                            print(f"      qgc plan: {recommendation['qgc_plan_path']}")
                        if not recommendation.get("command") and recommendation.get("map_source_path"):
                            print(f"      map source: {recommendation['map_source_path']}")
            if action.get("command"):
                print(f"  command: {action['command']}")
    if report.get("output_path"):
        print(f"Report: {report['output_path']}")
    print(f"__VISION_NAV_FIELD_CAPTURE_PREFLIGHT_STATUS__={report['status']}")
    print(f"__VISION_NAV_FIELD_CAPTURE_READY__={1 if report.get('ready_for_capture') else 0}")
    print(f"__VISION_NAV_FIELD_REGISTRATION_READY__={1 if report.get('ready_for_registration') else 0}")
    if report.get("output_path"):
        print(f"__VISION_NAV_FIELD_CAPTURE_PREFLIGHT__={report['output_path']}")
    if report.get("bundle_path"):
        print(f"__VISION_NAV_TERRAIN_BUNDLE__={report['bundle_path']}")
        print(f"__VISION_NAV_TERRAIN_BUNDLE_STATUS__={'available' if Path(str(report['bundle_path'])).exists() else 'missing'}")
    if report.get("source_log"):
        print(f"__VISION_NAV_EXPECTED_TERRAIN_LOG__={report['source_log']}")
    if report.get("capture_output_dir"):
        print(f"__VISION_NAV_TERRAIN_CAPTURE_OUTPUT_DIR__={report['capture_output_dir']}")
    if report.get("capture_command"):
        print(f"__VISION_NAV_TERRAIN_CAPTURE_COMMAND__={marker_shell_command(report['capture_command'])}")
    if report.get("capture_script_path"):
        print(f"__VISION_NAV_TERRAIN_CAPTURE_SCRIPT__={report['capture_script_path']}")
    if report.get("preflight_capture_command"):
        print(
            "__VISION_NAV_TERRAIN_PREFLIGHT_CAPTURE_COMMAND__="
            f"{marker_shell_command(report['preflight_capture_command'])}"
        )
    if report.get("metadata_update_command"):
        print(f"__VISION_NAV_FIELD_METADATA_UPDATE_COMMAND__={marker_shell_command(report['metadata_update_command'])}")


def marker_shell_command(command: Any) -> str:
    text = str(command)
    text = text.replace("\\\n", " ")
    text = " ".join(part.strip() for part in text.splitlines() if part.strip())
    text = re.sub(r"\\\s+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


def main() -> None:
    args = parse_args()
    report = evaluate_field_capture_preflight(
        plan_path=args.plan,
        condition=args.condition,
        repo_root=args.repo_root,
        output_path=args.output,
        capture_script_output=args.capture_script_output,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human(report)
    if report["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
