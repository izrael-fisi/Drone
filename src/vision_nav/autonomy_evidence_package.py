from __future__ import annotations

import argparse
import json
from datetime import datetime, timezone
from pathlib import Path
import re
from typing import Any
import zipfile

from vision_nav.autonomy_handoff import artifact_availability


DEFAULT_MAX_ARTIFACT_BYTES = 25_000_000
MAX_MANIFEST_PROOF_ITEMS = 12
MAX_MANIFEST_BLOCKERS = 12
MAX_MANIFEST_RUNBOOK_PHASES = 8
MAX_MANIFEST_RUNBOOK_ACTIONS = 8
MAX_MANIFEST_WORKFLOW_CHECKS = 8
COMMAND_GROUP_DESKTOP_ACTIONS = {
    "guided_workflow": "Module Setup > Evidence Workflow",
    "prerequisite_fix": "Module Setup > PX4 Prereq Setup",
    "field_collection_preflight": "Module Setup > Field Capture Preflight",
    "field_collection_preflight_capture": "Module Setup > Field Capture Preflight, then Field Log Capture",
    "field_collection_capture": "Module Setup > Field Log Capture",
    "field_collection_metadata_update": "Module Setup > Field Evidence Case > Update Metadata",
    "field_collection_registration": "Module Setup > Field Evidence Case > Register",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Package autonomy readiness report, handoff, and small referenced evidence artifacts into a support-review ZIP."
    )
    parser.add_argument("--report", required=True, help="Path to autonomy_readiness_report.json.")
    parser.add_argument("--handoff", help="Optional Markdown handoff path. Defaults to report sibling .md.")
    parser.add_argument("--output", help="Optional output ZIP path. Defaults to report sibling .evidence.zip.")
    parser.add_argument(
        "--max-artifact-bytes",
        type=int,
        default=DEFAULT_MAX_ARTIFACT_BYTES,
        help="Maximum size for each referenced evidence artifact copied into the package.",
    )
    return parser.parse_args()


def create_evidence_package(
    report_path: str | Path,
    *,
    handoff_path: str | Path | None = None,
    output_path: str | Path | None = None,
    max_artifact_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES,
) -> dict[str, Any]:
    report_file = Path(report_path).expanduser()
    report = json.loads(report_file.read_text())
    handoff_file = Path(handoff_path).expanduser() if handoff_path else report_file.with_suffix(".md")
    output_file = Path(output_path).expanduser() if output_path else report_file.with_suffix(".evidence.zip")
    output_file.parent.mkdir(parents=True, exist_ok=True)

    included: list[dict[str, Any]] = []
    missing: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    used_names: set[str] = set()

    with zipfile.ZipFile(output_file, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        add_file(archive, report_file, "reports/autonomy_readiness_report.json", "autonomy_report", included, used_names)
        if handoff_file.exists() and handoff_file.is_file():
            add_file(archive, handoff_file, "reports/autonomy_readiness_report.md", "autonomy_handoff", included, used_names)
        else:
            missing.append({"label": "autonomy_handoff", "path": str(handoff_file)})
        missing.extend(missing_proof_artifacts(report))

        for artifact in artifact_availability(report, report_path=report_file):
            label = str(artifact.get("label") or "artifact")
            raw_path = str(artifact.get("path") or "")
            if not raw_path:
                continue
            source = Path(raw_path).expanduser()
            if same_file(source, report_file) or same_file(source, handoff_file):
                continue
            if not source.exists():
                missing.append({"label": label, "path": raw_path})
                continue
            if not source.is_file():
                skipped.append({"label": label, "path": raw_path, "reason": "not_a_file"})
                continue
            size = source.stat().st_size
            if size > max_artifact_bytes:
                skipped.append(
                    {
                        "label": label,
                        "path": raw_path,
                        "reason": "too_large",
                        "size_bytes": size,
                        "max_artifact_bytes": max_artifact_bytes,
                    }
                )
                continue
            arcname = unique_arcname(
                f"artifacts/{safe_name(label)}-{safe_name(source.name)}",
                used_names,
            )
            add_file(archive, source, arcname, label, included, used_names)

        manifest = {
            "schema_version": "vision_nav_autonomy_evidence_package_v1",
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "source_report": str(report_file),
            "source_handoff": str(handoff_file),
            "output_path": str(output_file),
            "readiness_status": report.get("status"),
            "ready_for_goal_completion": (report.get("evidence_manifest") or {}).get("ready_for_goal_completion")
            if isinstance(report.get("evidence_manifest"), dict)
            else None,
            "readiness_report_metadata": report.get("metadata") if isinstance(report.get("metadata"), dict) else None,
            "plan_snapshot": report.get("plan_snapshot") if isinstance(report.get("plan_snapshot"), dict) else None,
            "proof_summary": build_proof_summary(report),
            "diagnostic_summary": build_diagnostic_summary(report),
            "proof_runbook_summary": build_proof_runbook_summary(report),
            "command_bundle": build_command_bundle_summary(report),
            "workflow_validation_summary": build_workflow_validation_summary(report, report_path=report_file),
            "max_artifact_bytes": max_artifact_bytes,
            "included": included,
            "missing": missing,
            "skipped": skipped,
        }
        archive.writestr("manifest.json", json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    result = {
        "zip_path": str(output_file),
        "manifest": manifest,
        "included_count": len(included),
        "missing_count": len(missing),
        "skipped_count": len(skipped),
    }
    return result


def missing_proof_artifacts(report: dict[str, Any]) -> list[dict[str, Any]]:
    evidence = report.get("evidence_manifest") if isinstance(report.get("evidence_manifest"), dict) else {}
    items = dict_items(evidence.get("proof_items")) or dict_items(evidence.get("completion_blockers"))
    missing: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        name = item.get("name")
        if not isinstance(name, str) or not name or name in seen:
            continue
        if item.get("status") == "passed" or item.get("requires_external_proof") is not True:
            continue
        seen.add(name)
        entry: dict[str, Any] = {
            "label": f"proof:{name}",
            "reason": "proof_gate_not_passed",
            "status": str(item.get("status") or "missing"),
        }
        message = item.get("message")
        if isinstance(message, str) and message:
            entry["message"] = message
        source = item.get("source")
        if isinstance(source, str) and source:
            entry["source"] = source
        missing_conditions = string_list(item.get("missing_conditions"))
        if missing_conditions:
            entry["missing_conditions"] = missing_conditions
        missing.append(entry)
    return missing


def build_proof_summary(report: dict[str, Any]) -> dict[str, Any]:
    evidence = report.get("evidence_manifest") if isinstance(report.get("evidence_manifest"), dict) else {}
    proof_items = dict_items(evidence.get("proof_items"))
    completion_blockers = dict_items(evidence.get("completion_blockers"))
    external_blockers = dict_items(evidence.get("external_blockers"))
    return {
        "schema_version": evidence.get("schema_version"),
        "ready_for_goal_completion": evidence.get("ready_for_goal_completion"),
        "proof_item_count": len(proof_items),
        "proof_item_passed_count": count_status(proof_items, "passed"),
        "proof_items_truncated": len(proof_items) > MAX_MANIFEST_PROOF_ITEMS,
        "completion_blocker_count": len(completion_blockers),
        "completion_blockers_truncated": len(completion_blockers) > MAX_MANIFEST_BLOCKERS,
        "external_blocker_count": len(external_blockers),
        "external_blockers_truncated": len(external_blockers) > MAX_MANIFEST_BLOCKERS,
        "proof_items": compact_evidence_items(proof_items, limit=MAX_MANIFEST_PROOF_ITEMS),
        "completion_blockers": compact_evidence_items(completion_blockers, limit=MAX_MANIFEST_BLOCKERS),
        "external_blockers": compact_evidence_items(external_blockers, limit=MAX_MANIFEST_BLOCKERS),
    }


def build_diagnostic_summary(report: dict[str, Any]) -> dict[str, Any] | None:
    evidence = report.get("evidence_manifest") if isinstance(report.get("evidence_manifest"), dict) else {}
    diagnostic_items = dict_items(evidence.get("diagnostic_items"))
    diagnostics = report.get("diagnostics") if isinstance(report.get("diagnostics"), dict) else {}
    if not diagnostic_items and not diagnostics:
        return None
    summary: dict[str, Any] = {
        "diagnostic_item_count": len(diagnostic_items),
        "diagnostic_items": compact_evidence_items(diagnostic_items, limit=MAX_MANIFEST_PROOF_ITEMS),
    }
    px4_prereqs = diagnostics.get("px4_sitl_prereqs") if isinstance(diagnostics, dict) else None
    if isinstance(px4_prereqs, dict):
        summary["px4_sitl_prereqs"] = compact_px4_prereq_diagnostic(px4_prereqs)
    return summary


def compact_px4_prereq_diagnostic(item: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in ("status", "path", "schema_version", "session_dir", "px4_target", "tmux_session", "receiver_report"):
        value = item.get(key)
        if isinstance(value, str) and value:
            compact[key] = value
    failed_checks = dict_items(item.get("failed_checks"))
    if failed_checks:
        compact["failed_checks"] = [
            {
                key: str(check.get(key))
                for key in ("name", "status", "message")
                if check.get(key) is not None
            }
            for check in failed_checks[:MAX_MANIFEST_RUNBOOK_ACTIONS]
        ]
    next_actions = string_list(item.get("next_actions"))
    if next_actions:
        compact["next_actions"] = next_actions[:MAX_MANIFEST_RUNBOOK_ACTIONS]
    fix_commands = dict_items(item.get("fix_commands"))
    if fix_commands:
        compact["fix_commands"] = [
            {
                key: str(command.get(key))
                for key in ("label", "command", "condition")
                if command.get(key) is not None
            }
            for command in fix_commands[:MAX_MANIFEST_RUNBOOK_ACTIONS]
            if command.get("command")
        ]
    return compact


def build_proof_runbook_summary(report: dict[str, Any]) -> dict[str, Any] | None:
    runbook = report.get("proof_runbook") if isinstance(report.get("proof_runbook"), dict) else {}
    if not runbook:
        return None
    phases = dict_items(runbook.get("phases"))
    return {
        "schema_version": runbook.get("schema_version"),
        "ready_for_goal_completion": runbook.get("ready_for_goal_completion"),
        "summary": runbook.get("summary") if isinstance(runbook.get("summary"), dict) else {},
        "phases_truncated": len(phases) > MAX_MANIFEST_RUNBOOK_PHASES,
        "phases": [compact_runbook_phase(phase) for phase in phases[:MAX_MANIFEST_RUNBOOK_PHASES]],
    }


def build_command_bundle_summary(report: dict[str, Any]) -> dict[str, Any] | None:
    bundle = report.get("command_bundle") if isinstance(report.get("command_bundle"), dict) else {}
    if not bundle:
        return None
    command_items = command_bundle_items(report, bundle)
    summary = {
        "guided_workflow_commands": string_list(bundle.get("guided_workflow_commands")),
        "prerequisite_fix_commands": string_list(bundle.get("prerequisite_fix_commands")),
        "next_action_commands": string_list(bundle.get("next_action_commands")),
        "immediate_next_action_commands": string_list(bundle.get("immediate_next_action_commands")),
        "blocked_follow_up_commands": string_list(bundle.get("blocked_follow_up_commands")),
        "field_collection_preflight_commands": string_list(bundle.get("field_collection_preflight_commands")),
        "field_collection_preflight_capture_commands": string_list(
            bundle.get("field_collection_preflight_capture_commands")
        ),
        "field_collection_capture_commands": string_list(bundle.get("field_collection_capture_commands")),
        "field_collection_metadata_update_commands": string_list(
            bundle.get("field_collection_metadata_update_commands")
        ),
        "field_collection_registration_commands": string_list(bundle.get("field_collection_registration_commands")),
    }
    command_count = bundle.get("command_count")
    if isinstance(command_count, int):
        summary["command_count"] = command_count
    if command_items:
        summary["command_items"] = command_items
    if not any(value for value in summary.values() if isinstance(value, list)):
        return None
    return summary


def command_bundle_items(report: dict[str, Any], bundle: dict[str, Any]) -> list[dict[str, str]]:
    app_hints = command_app_hints(report)
    groups = [
        ("guided_workflow", "guided_workflow_commands"),
        ("prerequisite_fix", "prerequisite_fix_commands"),
        ("next_action", "next_action_commands"),
        ("immediate_next_action", "immediate_next_action_commands"),
        ("blocked_follow_up", "blocked_follow_up_commands"),
        ("field_collection_preflight", "field_collection_preflight_commands"),
        ("field_collection_preflight_capture", "field_collection_preflight_capture_commands"),
        ("field_collection_capture", "field_collection_capture_commands"),
        ("field_collection_metadata_update", "field_collection_metadata_update_commands"),
        ("field_collection_registration", "field_collection_registration_commands"),
    ]
    items: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for group, key in groups:
        for command in string_list(bundle.get(key)):
            dedupe_key = (group, command)
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            item = {"group": group, "command": command}
            desktop_action = app_hints.get(command) or COMMAND_GROUP_DESKTOP_ACTIONS.get(group)
            if desktop_action:
                item["desktop_action"] = desktop_action
            items.append(item)
    return items


def command_app_hints(report: dict[str, Any]) -> dict[str, str]:
    hints: dict[str, str] = {}
    actions = report.get("next_actions") if isinstance(report.get("next_actions"), list) else []
    for action in dict_items(actions):
        add_command_app_hint(hints, action.get("command"), action.get("desktop_action"))
    runbook = report.get("proof_runbook") if isinstance(report.get("proof_runbook"), dict) else {}
    for phase in dict_items(runbook.get("phases")):
        for action in dict_items(phase.get("actions")):
            add_command_app_hint(hints, action.get("command"), action.get("desktop_action"))
    return hints


def add_command_app_hint(hints: dict[str, str], command: Any, desktop_action: Any) -> None:
    if not isinstance(command, str) or not command.strip():
        return
    if not isinstance(desktop_action, str) or not desktop_action.strip():
        return
    hints.setdefault(command, desktop_action)


def build_workflow_validation_summary(report: dict[str, Any], *, report_path: Path) -> dict[str, Any] | None:
    path = resolve_workflow_validation_path(report, report_path=report_path)
    if path is None:
        return None
    try:
        validation = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {
            "path": str(path),
            "status": "unreadable",
            "issues": ["Workflow validation report could not be read."],
        }
    if not isinstance(validation, dict) or validation.get("schema_version") != "vision_nav_autonomy_evidence_workflow_validation_v1":
        return {
            "path": str(path),
            "status": "unrecognized",
            "issues": ["Workflow validation report schema is not recognized."],
        }
    checks = dict_items(validation.get("checks"))
    highlighted_checks = [check for check in checks if check.get("status") != "passed"]
    if not highlighted_checks:
        highlighted_checks = checks
    return {
        "path": str(path),
        "schema_version": validation.get("schema_version"),
        "status": validation.get("status"),
        "workflow_status": validation.get("workflow_status"),
        "step_count": validation.get("step_count"),
        "marker_count": validation.get("marker_count"),
        "issue_count": validation.get("issue_count", len(string_list(validation.get("issues")))),
        "issues": string_list(validation.get("issues"))[:MAX_MANIFEST_RUNBOOK_ACTIONS],
        "next_required_step": compact_workflow_next_step(validation.get("next_required_step")),
        "checks_truncated": len(highlighted_checks) > MAX_MANIFEST_WORKFLOW_CHECKS,
        "checks": [
            compact_workflow_validation_check(check)
            for check in highlighted_checks[:MAX_MANIFEST_WORKFLOW_CHECKS]
        ],
    }


def resolve_workflow_validation_path(report: dict[str, Any], *, report_path: Path) -> Path | None:
    inputs = report.get("inputs") if isinstance(report.get("inputs"), dict) else {}
    raw_path = inputs.get("evidence_workflow_validation_report")
    if isinstance(raw_path, str) and raw_path:
        candidate = Path(raw_path).expanduser()
        if candidate.is_file():
            return candidate
    sibling_names = [
        "autonomy_evidence_workflow.validation.json",
        "autonomy_readiness_report.validation.json",
    ]
    for name in sibling_names:
        sibling = report_path.parent / name
        if sibling.is_file():
            return sibling
    return None


def compact_workflow_validation_check(check: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in ("name", "status", "message"):
        value = check.get(key)
        if isinstance(value, str) and value:
            compact[key] = value
    details = check.get("details") if isinstance(check.get("details"), dict) else {}
    marker_count = details.get("marker_count", check.get("marker_count"))
    if isinstance(marker_count, int):
        compact["marker_count"] = marker_count
    missing_markers = string_list(details.get("missing_markers") or check.get("missing_markers"))
    if missing_markers:
        compact["missing_markers"] = missing_markers[:MAX_MANIFEST_RUNBOOK_ACTIONS]
        compact["missing_markers_truncated"] = len(missing_markers) > MAX_MANIFEST_RUNBOOK_ACTIONS
    present_markers = string_list(details.get("present_markers") or check.get("present_markers"))
    if present_markers:
        compact["present_markers"] = present_markers[:MAX_MANIFEST_RUNBOOK_ACTIONS]
        compact["present_markers_truncated"] = len(present_markers) > MAX_MANIFEST_RUNBOOK_ACTIONS
    missing_steps = string_list(details.get("missing_steps") or check.get("missing_steps"))
    if missing_steps:
        compact["missing_steps"] = missing_steps[:MAX_MANIFEST_RUNBOOK_ACTIONS]
        compact["missing_steps_truncated"] = len(missing_steps) > MAX_MANIFEST_RUNBOOK_ACTIONS
    non_passed_count = details.get("non_passed_count", check.get("non_passed_count"))
    if isinstance(non_passed_count, int):
        compact["non_passed_count"] = non_passed_count
    superseded_count = details.get("superseded_count", check.get("superseded_count"))
    if isinstance(superseded_count, int):
        compact["superseded_count"] = superseded_count
    non_passed_steps = dict_items(details.get("non_passed_steps") or check.get("non_passed_steps"))
    if non_passed_steps:
        compact["non_passed_steps_truncated"] = len(non_passed_steps) > MAX_MANIFEST_RUNBOOK_ACTIONS
        compact["non_passed_steps"] = [
            compact_workflow_validation_step(step)
            for step in non_passed_steps[:MAX_MANIFEST_RUNBOOK_ACTIONS]
        ]
    superseded_steps = dict_items(details.get("superseded_steps") or check.get("superseded_steps"))
    if superseded_steps:
        compact["superseded_steps_truncated"] = len(superseded_steps) > MAX_MANIFEST_RUNBOOK_ACTIONS
        compact["superseded_steps"] = [
            compact_workflow_validation_step(step)
            for step in superseded_steps[:MAX_MANIFEST_RUNBOOK_ACTIONS]
        ]
    return compact


def compact_workflow_validation_step(step: dict[str, Any]) -> dict[str, Any]:
    compact = {
        key: str(step.get(key))
        for key in (
            "name",
            "status",
            "notes",
            "current_preflight_report",
            "current_preflight_status",
            "guidance",
        )
        if step.get(key) is not None
    }
    if isinstance(step.get("exit_code"), int):
        compact["exit_code"] = step["exit_code"]
    if isinstance(step.get("current_preflight_allows_capture"), bool):
        compact["current_preflight_allows_capture"] = step["current_preflight_allows_capture"]
    if isinstance(step.get("current_ready_for_registration"), bool):
        compact["current_ready_for_registration"] = step["current_ready_for_registration"]
    return compact


def compact_workflow_next_step(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, dict):
        return None
    compact: dict[str, Any] = {}
    for key in (
        "name",
        "status",
        "notes",
        "command",
        "desktop_action",
        "metadata_update_command",
        "bundle_path",
        "expected_log",
        "output_dir",
        "runtime_status_path",
        "capture_command_after_bundle",
    ):
        item = value.get(key)
        if isinstance(item, str) and item:
            compact[key] = item
    exit_code = value.get("exit_code")
    if isinstance(exit_code, int):
        compact["exit_code"] = exit_code
    return compact or None


def compact_runbook_phase(phase: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in ("id", "title", "status", "notes"):
        value = phase.get(key)
        if isinstance(value, str) and value:
            compact[key] = value
    depends_on = string_list(phase.get("depends_on"))
    if depends_on:
        compact["depends_on"] = depends_on
    commands = string_list(phase.get("commands"))
    if commands:
        compact["commands"] = commands
    checks = dict_items(phase.get("checks"))
    if checks:
        compact["checks"] = [
            {
                key: str(item.get(key))
                for key in ("name", "status", "message")
                if item.get(key) is not None
            }
            for item in checks
        ]
    actions = dict_items(phase.get("actions"))
    if actions:
        compact["actions_truncated"] = len(actions) > MAX_MANIFEST_RUNBOOK_ACTIONS
        compact["actions"] = [compact_runbook_action(action) for action in actions[:MAX_MANIFEST_RUNBOOK_ACTIONS]]
    return compact


def compact_runbook_action(action: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in ("check", "status", "desktop_action", "command", "notes", "bench_subcheck"):
        value = action.get(key)
        if isinstance(value, str) and value:
            compact[key] = value
    missing_conditions = string_list(action.get("missing_conditions"))
    if missing_conditions:
        compact["missing_conditions"] = missing_conditions
    return compact


def dict_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def count_status(items: list[dict[str, Any]], status: str) -> int:
    return sum(1 for item in items if item.get("status") == status)


def compact_evidence_items(items: list[dict[str, Any]], *, limit: int) -> list[dict[str, Any]]:
    return [compact_evidence_item(item) for item in items[:limit]]


def compact_evidence_item(item: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in ("name", "status", "message", "source"):
        value = item.get(key)
        if isinstance(value, str) and value:
            compact[key] = value
    requires_external = item.get("requires_external_proof")
    if isinstance(requires_external, bool):
        compact["requires_external_proof"] = requires_external
    missing_conditions = string_list(item.get("missing_conditions"))
    if missing_conditions:
        compact["missing_conditions"] = missing_conditions
    bench_subchecks = compact_bench_subchecks(item.get("bench_subchecks"))
    if bench_subchecks:
        compact["bench_subchecks"] = bench_subchecks
    return compact


def compact_bench_subchecks(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    subchecks: list[dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            continue
        compact: dict[str, Any] = {}
        for key in ("name", "status", "message"):
            text = item.get(key)
            if isinstance(text, str) and text:
                compact[key] = text
        if compact:
            subchecks.append(compact)
    return subchecks


def string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def add_file(
    archive: zipfile.ZipFile,
    source: Path,
    arcname: str,
    label: str,
    included: list[dict[str, Any]],
    used_names: set[str],
) -> None:
    final_arcname = unique_arcname(arcname, used_names)
    archive.write(source, final_arcname)
    included.append(
        {
            "label": label,
            "path": str(source),
            "archive_path": final_arcname,
            "size_bytes": source.stat().st_size,
        }
    )


def unique_arcname(name: str, used_names: set[str]) -> str:
    base = name
    index = 2
    while name in used_names:
        stem = Path(base).with_suffix("").as_posix()
        suffix = Path(base).suffix
        name = f"{stem}-{index}{suffix}"
        index += 1
    used_names.add(name)
    return name


def safe_name(value: str) -> str:
    sanitized = re.sub(r"[^A-Za-z0-9_.-]+", "_", value.strip())
    sanitized = sanitized.strip("._-")
    return sanitized or "artifact"


def missing_artifact_lines(manifest: dict[str, Any], *, limit: int = 8) -> list[str]:
    missing = dict_items(manifest.get("missing"))
    lines = [format_missing_artifact(item) for item in missing[:limit]]
    if len(missing) > limit:
        lines.append(f"... {len(missing) - limit} more missing artifacts")
    return lines


def format_missing_artifact(item: dict[str, Any]) -> str:
    label = str(item.get("label") or item.get("path") or "artifact")
    details: list[str] = []
    for key in ("status", "reason"):
        value = item.get(key)
        if isinstance(value, str) and value:
            details.append(value)
    source = item.get("source")
    if isinstance(source, str) and source:
        details.append(f"source={source}")
    missing_conditions = string_list(item.get("missing_conditions"))
    if missing_conditions:
        visible = ", ".join(missing_conditions[:3])
        if len(missing_conditions) > 3:
            visible = f"{visible} +{len(missing_conditions) - 3}"
        details.append(f"missing={visible}")
    message = item.get("message")
    suffix = f": {message}" if isinstance(message, str) and message else ""
    return f"{label} ({', '.join(details)}){suffix}" if details else f"{label}{suffix}"


def same_file(left: Path, right: Path) -> bool:
    try:
        return left.exists() and right.exists() and left.resolve() == right.resolve()
    except OSError:
        return False


def main() -> None:
    args = parse_args()
    result = create_evidence_package(
        args.report,
        handoff_path=args.handoff,
        output_path=args.output,
        max_artifact_bytes=args.max_artifact_bytes,
    )
    print(f"Autonomy evidence package: {result['zip_path']}")
    print(f"Included: {result['included_count']} Missing: {result['missing_count']} Skipped: {result['skipped_count']}")
    missing_lines = missing_artifact_lines(result["manifest"])
    if missing_lines:
        print("Missing package artifacts:")
        for line in missing_lines:
            print(f"- {line}")
    print(f"__VISION_NAV_AUTONOMY_EVIDENCE_PACKAGE__={result['zip_path']}")


if __name__ == "__main__":
    main()
