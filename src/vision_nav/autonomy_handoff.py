from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Render a human-readable autonomy readiness handoff from an autonomy_readiness_report.json file."
    )
    parser.add_argument("--report", required=True, help="Path to autonomy_readiness_report.json.")
    parser.add_argument("--output", help="Optional Markdown output path. Prints to stdout when omitted.")
    return parser.parse_args()


def render_handoff_markdown(report: dict[str, Any], *, report_path: str | Path | None = None) -> str:
    evidence = report.get("evidence_manifest") if isinstance(report.get("evidence_manifest"), dict) else {}
    proof_items = dict_items(evidence.get("proof_items"))
    completion_blockers = dict_items(evidence.get("completion_blockers"))
    blockers = dict_items(evidence.get("external_blockers"))
    ready = evidence.get("ready_for_goal_completion") is True
    lines = [
        "# Autonomy Readiness Handoff",
        "",
        f"- Status: {format_cell(report.get('status'))}",
        f"- Goal completion: {'ready' if ready else 'waiting on proof'}",
        f"- Checks: {summary_counts(report.get('summary'))}",
    ]
    if proof_items:
        lines.append(f"- Proof items: {count_status(proof_items, 'passed')}/{len(proof_items)} passed")
    if completion_blockers:
        lines.append(f"- Completion blockers: {len(completion_blockers)}")
    if blockers:
        lines.append(f"- External blockers: {len(blockers)}")

    metadata = report.get("metadata") if isinstance(report.get("metadata"), dict) else {}
    if metadata:
        lines.extend(["", "## Audit Metadata", ""])
        lines.extend(audit_metadata_lines(metadata))

    lines.extend(["", "## Inputs", ""])
    inputs = report.get("inputs") if isinstance(report.get("inputs"), dict) else {}
    if inputs:
        lines.extend(table(["Input", "Path"], [[key, value or "not provided"] for key, value in sorted(inputs.items())]))
    else:
        lines.append("No input metadata was recorded.")

    plan_snapshot = report.get("plan_snapshot") if isinstance(report.get("plan_snapshot"), dict) else {}
    if plan_snapshot:
        lines.extend(["", "## Plan Source Snapshot", ""])
        lines.extend(plan_snapshot_lines(plan_snapshot))

    artifacts = artifact_availability(report, report_path=report_path)
    if artifacts:
        lines.extend(["", "## Artifact Availability", ""])
        lines.extend(
            table(
                ["Artifact", "Path", "Present", "Size Bytes"],
                [
                    [
                        item["label"],
                        item["path"],
                        "yes" if item["present"] else "no",
                        item["size_bytes"] if item["size_bytes"] is not None else "",
                    ]
                    for item in artifacts
                ],
            )
        )

    field_plan = load_field_collection_plan(report, report_path=report_path)
    if field_plan is not None:
        lines.extend(["", "## Field Collection Plan", ""])
        summary = field_plan.get("summary") if isinstance(field_plan.get("summary"), dict) else {}
        lines.extend(
            [
                f"- Status: {format_cell(field_plan.get('status'))}",
                f"- Site: {format_cell(field_plan.get('site_name'))}",
                f"- Manifest: {format_cell(field_plan.get('manifest_path'))}",
                (
                    "- Registered: "
                    f"{int(summary.get('registered_count') or 0)}/"
                    f"{int(summary.get('required_count') or 0)}"
                ),
                f"- Placeholder: {int(summary.get('placeholder_count') or 0)}",
                f"- Registered missing log: {int(summary.get('registered_missing_log_count') or 0)}",
                f"- Missing: {int(summary.get('missing_count') or 0)}",
            ]
        )
        pending_conditions = field_collection_pending_conditions(field_plan)
        if pending_conditions:
            lines.extend(["", "Pending collection items:", ""])
            lines.extend(
                table(
                    ["Condition", "Status", "Expected", "Case"],
                    [
                        [
                            item.get("condition"),
                            item.get("status"),
                            item.get("expected"),
                            item.get("case_name"),
                        ]
                        for item in pending_conditions[:12]
                    ],
                )
            )

    lines.extend(["", "## Checks", ""])
    checks = report.get("checks") if isinstance(report.get("checks"), list) else []
    if checks:
        lines.extend(
            table(
                ["Check", "Status", "Message"],
                [
                    [item.get("name"), item.get("status"), item.get("message")]
                    for item in checks
                    if isinstance(item, dict)
                ],
            )
        )
    else:
        lines.append("No checks were recorded.")

    lines.extend(["", "## Goal Proof Items", ""])
    if proof_items:
        lines.extend(
            table(
                ["Proof Item", "Status", "Missing Conditions", "Bench Subchecks", "Message"],
                [
                    [
                        item.get("name"),
                        item.get("status"),
                        join_values(item.get("missing_conditions")),
                        bench_subcheck_summary(item.get("bench_subchecks")),
                        item.get("message"),
                    ]
                    for item in proof_items
                ],
            )
        )
    else:
        lines.append("No proof items were recorded.")

    lines.extend(["", "## Completion Blockers", ""])
    if completion_blockers:
        lines.extend(
            table(
                ["Blocker", "Status", "Missing Conditions", "Bench Subchecks", "Message"],
                [
                    [
                        item.get("name"),
                        item.get("status"),
                        join_values(item.get("missing_conditions")),
                        bench_subcheck_summary(item.get("bench_subchecks")),
                        item.get("message"),
                    ]
                    for item in completion_blockers
                ],
            )
        )
    else:
        lines.append("No completion blockers were recorded.")

    lines.extend(["", "## External Proof Blockers", ""])
    if blockers:
        lines.extend(
            table(
                ["Blocker", "Status", "Missing Conditions", "Bench Subchecks", "Message"],
                [
                    [
                        item.get("name"),
                        item.get("status"),
                        join_values(item.get("missing_conditions")),
                        bench_subcheck_summary(item.get("bench_subchecks")),
                        item.get("message"),
                    ]
                    for item in blockers
                    if isinstance(item, dict)
                ],
            )
        )
    else:
        lines.append("No external proof blockers were recorded.")

    proof_runbook = report.get("proof_runbook") if isinstance(report.get("proof_runbook"), dict) else {}
    if proof_runbook:
        lines.extend(["", "## Proof Runbook", ""])
        lines.extend(proof_runbook_lines(proof_runbook))

    missing_conditions = missing_condition_checklist(report)
    if missing_conditions:
        lines.extend(["", "## Field Evidence Collection Checklist", ""])
        for condition in missing_conditions:
            lines.append(f"- [ ] {human_label(condition)} (`{condition}`)")

    bench_subchecks = bench_subcheck_checklist(report)
    if bench_subchecks:
        lines.extend(["", "## Bench Subcheck Checklist", ""])
        for item in bench_subchecks:
            status = item.get("status") or "unknown"
            name = item.get("name") or "unknown"
            message = item.get("message") or ""
            suffix = f" - {message}" if message else ""
            lines.append(f"- [ ] {human_label(str(name))} ({status}){suffix}")

    lines.extend(["", "## Next Actions", ""])
    actions = report.get("next_actions") if isinstance(report.get("next_actions"), list) else []
    if actions:
        lines.extend(
            table(
                ["Check", "Status", "Desktop Action", "Command", "Notes"],
                [
                    [
                        item.get("check"),
                        item.get("status"),
                        item.get("desktop_action"),
                        item.get("command"),
                        item.get("notes"),
                    ]
                    for item in actions
                    if isinstance(item, dict)
                ],
            )
        )
    else:
        lines.append("No next actions were recorded.")
    command_groups = command_bundle(report, field_plan)
    if command_groups:
        lines.extend(["", "## Command Bundle", ""])
        guided_workflow_commands = command_groups.get("guided_workflow") or []
        if guided_workflow_commands:
            lines.extend(["Guided workflow command:", "", "```bash"])
            lines.extend(guided_workflow_commands)
            lines.append("```")
        next_action_commands = command_groups.get("next_actions") or []
        if next_action_commands:
            lines.extend(["", "Next-action commands:", "", "```bash"])
            lines.extend(next_action_commands)
            lines.append("```")
        field_capture_commands = command_groups.get("field_collection_capture") or []
        if field_capture_commands:
            lines.extend(["", "Field collection capture commands:", "", "```bash"])
            lines.extend(field_capture_commands)
            lines.append("```")
        field_commands = command_groups.get("field_collection") or []
        if field_commands:
            lines.extend(["", "Field collection registration commands:", "", "```bash"])
            lines.extend(field_commands)
            lines.append("```")
    lines.append("")
    return "\n".join(lines)


def artifact_availability(report: dict[str, Any], *, report_path: str | Path | None = None) -> list[dict[str, Any]]:
    paths: dict[str, str] = {}
    inputs = report.get("inputs") if isinstance(report.get("inputs"), dict) else {}
    for key, value in sorted(inputs.items()):
        if isinstance(value, str) and looks_like_path(value):
            paths[f"input:{key}"] = value
    for key in ("field_collection_plan", "field_collection_plan_markdown"):
        resolved = resolve_report_input_path(report, key, report_path=report_path)
        if resolved is not None:
            paths[f"input:{key}"] = str(resolved)

    evidence = report.get("evidence_manifest") if isinstance(report.get("evidence_manifest"), dict) else {}
    for key in ("proof_items", "external_blockers", "completion_blockers"):
        items = evidence.get(key)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            source = item.get("source")
            name = item.get("name") or key
            if isinstance(source, str) and looks_like_path(source):
                paths[f"source:{name}"] = source

    artifacts = []
    for label, raw_path in paths.items():
        path = Path(raw_path).expanduser()
        exists = path.exists()
        artifacts.append(
            {
                "label": label,
                "path": raw_path,
                "present": exists,
                "size_bytes": path.stat().st_size if exists and path.is_file() else None,
            }
        )
    return artifacts


def looks_like_path(value: str) -> bool:
    if not value or value in {"support_bundle", "px4_sitl_session"}:
        return False
    return (
        value.startswith("/")
        or value.startswith("~/")
        or value.startswith("./")
        or value.startswith("../")
        or "/" in value
        or "\\" in value
    )


def resolve_report_input_path(
    report: dict[str, Any],
    key: str,
    *,
    report_path: str | Path | None = None,
) -> Path | None:
    inputs = report.get("inputs") if isinstance(report.get("inputs"), dict) else {}
    path_value = inputs.get(key)
    if isinstance(path_value, str) and path_value:
        path = Path(path_value).expanduser()
        if path.is_file():
            return path
    sibling_names = {
        "field_collection_plan": "field_collection_plan.json",
        "field_collection_plan_markdown": "field_collection_plan.md",
    }
    sibling_name = sibling_names.get(key)
    if not sibling_name or report_path is None:
        return None
    sibling = Path(report_path).expanduser().parent / sibling_name
    return sibling if sibling.is_file() else None


def load_field_collection_plan(
    report: dict[str, Any],
    *,
    report_path: str | Path | None = None,
) -> dict[str, Any] | None:
    path = resolve_report_input_path(report, "field_collection_plan", report_path=report_path)
    if path is None:
        return None
    try:
        plan = json.loads(path.read_text())
    except Exception:
        return None
    if not isinstance(plan, dict) or plan.get("schema_version") != "vision_nav_field_collection_plan_v1":
        return None
    return plan


def field_collection_pending_conditions(plan: dict[str, Any]) -> list[dict[str, Any]]:
    conditions = plan.get("conditions")
    if not isinstance(conditions, list):
        return []
    pending = [
        item
        for item in conditions
        if isinstance(item, dict) and item.get("status") != "registered"
    ]
    return sorted(
        pending,
        key=lambda item: (
            str(item.get("status") or ""),
            str(item.get("condition") or ""),
        ),
    )


def missing_condition_checklist(report: dict[str, Any]) -> list[str]:
    seen: set[str] = set()
    values: list[str] = []
    for item in report_items_with_missing_conditions(report):
        for condition in item:
            text = str(condition)
            if text and text not in seen:
                seen.add(text)
                values.append(text)
    return values


def report_items_with_missing_conditions(report: dict[str, Any]) -> list[list[Any]]:
    evidence = report.get("evidence_manifest") if isinstance(report.get("evidence_manifest"), dict) else {}
    candidates: list[Any] = []
    for key in ("external_blockers", "completion_blockers", "proof_items"):
        value = evidence.get(key)
        if isinstance(value, list):
            candidates.extend(value)
    actions = report.get("next_actions")
    if isinstance(actions, list):
        candidates.extend(actions)
    return [
        item.get("missing_conditions")
        for item in candidates
        if isinstance(item, dict) and isinstance(item.get("missing_conditions"), list)
    ]


def bench_subcheck_checklist(report: dict[str, Any]) -> list[dict[str, str]]:
    evidence = report.get("evidence_manifest") if isinstance(report.get("evidence_manifest"), dict) else {}
    candidates: list[Any] = []
    for key in ("external_blockers", "completion_blockers", "proof_items"):
        value = evidence.get(key)
        if isinstance(value, list):
            candidates.extend(value)
    actions = report.get("next_actions")
    if isinstance(actions, list):
        candidates.extend(actions)
    seen: set[str] = set()
    subchecks: list[dict[str, str]] = []
    for item in candidates:
        if not isinstance(item, dict) or not isinstance(item.get("bench_subchecks"), list):
            continue
        for subcheck in item["bench_subchecks"]:
            if not isinstance(subcheck, dict):
                continue
            name = str(subcheck.get("name") or "")
            if not name or name in seen:
                continue
            seen.add(name)
            subchecks.append(
                {
                    "name": name,
                    "status": str(subcheck.get("status") or "unknown"),
                    "message": str(subcheck.get("message") or ""),
                }
            )
    return subchecks


def command_bundle(report: dict[str, Any], field_plan: dict[str, Any] | None) -> dict[str, list[str]]:
    report_bundle = report.get("command_bundle") if isinstance(report.get("command_bundle"), dict) else {}
    actions = report.get("next_actions") if isinstance(report.get("next_actions"), list) else []
    guided_workflow_commands = json_string_list(report_bundle.get("guided_workflow_commands"))
    next_action_commands = unique_strings(
        [
            *json_string_list(report_bundle.get("next_action_commands")),
            *[
                item.get("command")
                for item in actions
                if isinstance(item, dict)
            ],
        ]
    )
    field_capture_commands = json_string_list(report_bundle.get("field_collection_capture_commands"))
    field_commands = json_string_list(report_bundle.get("field_collection_registration_commands"))
    if field_plan is not None:
        field_capture_commands = unique_strings(
            [
                *field_capture_commands,
                *[
                    item.get("capture_command")
                    for item in field_collection_pending_conditions(field_plan)
                    if isinstance(item, dict)
                ],
            ]
        )
        field_commands = unique_strings(
            [
                *field_commands,
                *[
                    item.get("register_command")
                    for item in field_collection_pending_conditions(field_plan)
                    if isinstance(item, dict)
                ],
            ]
        )
    result: dict[str, list[str]] = {}
    if guided_workflow_commands:
        result["guided_workflow"] = guided_workflow_commands
    if next_action_commands:
        result["next_actions"] = next_action_commands
    if field_capture_commands:
        result["field_collection_capture"] = field_capture_commands
    if field_commands:
        result["field_collection"] = field_commands
    return result


def proof_runbook_lines(runbook: dict[str, Any]) -> list[str]:
    phases = dict_items(runbook.get("phases"))
    if not phases:
        return ["No proof runbook phases were recorded."]
    summary = runbook.get("summary") if isinstance(runbook.get("summary"), dict) else {}
    lines = [
        (
            f"- Phases: {int(summary.get('passed') or 0)} passed, "
            f"{int(summary.get('action_required') or 0)} action required, "
            f"{int(summary.get('blocked') or 0)} blocked"
        ),
        "",
    ]
    lines.extend(
        table(
            ["Phase", "Status", "Depends On", "Checks", "Commands", "Notes"],
            [
                [
                    phase.get("title") or phase.get("id"),
                    phase.get("status"),
                    proof_runbook_dependencies(phase),
                    proof_runbook_check_summary(phase.get("checks")),
                    join_values(phase.get("commands")),
                    phase.get("notes"),
                ]
                for phase in phases
            ],
        )
    )
    action_rows = []
    for phase in phases:
        phase_title = phase.get("title") or phase.get("id")
        for action in dict_items(phase.get("actions")):
            action_rows.append(
                [
                    phase_title,
                    action.get("check"),
                    action.get("desktop_action"),
                    action.get("command"),
                ]
            )
    if action_rows:
        lines.extend(["", "Runbook action commands:", ""])
        lines.extend(table(["Phase", "Check", "Desktop Action", "Command"], action_rows))
    return lines


def proof_runbook_dependencies(phase: dict[str, Any]) -> str:
    depends_on = phase.get("depends_on")
    if not isinstance(depends_on, list) or not depends_on:
        return ""
    dependency_status = phase.get("dependency_status") if isinstance(phase.get("dependency_status"), dict) else {}
    parts = []
    for dependency in depends_on:
        name = str(dependency)
        status = dependency_status.get(name)
        parts.append(f"{name} ({status})" if status else name)
    return ", ".join(parts)


def proof_runbook_check_summary(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    parts = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        status = item.get("status")
        if name and status:
            parts.append(f"{name} ({status})")
        elif name:
            parts.append(str(name))
    return ", ".join(parts)


def json_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def unique_strings(values: Any) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for value in values:
        if not isinstance(value, str) or not value:
            continue
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def summary_counts(summary: Any) -> str:
    if not isinstance(summary, dict):
        return "unknown"
    return (
        f"{int(summary.get('passed') or 0)} passed, "
        f"{int(summary.get('degraded') or 0)} degraded, "
        f"{int(summary.get('failed') or 0)} failed"
    )


def plan_snapshot_lines(snapshot: dict[str, Any]) -> list[str]:
    research = snapshot.get("research_doc") if isinstance(snapshot.get("research_doc"), dict) else {}
    implementation = (
        snapshot.get("implementation_plan")
        if isinstance(snapshot.get("implementation_plan"), dict)
        else {}
    )
    rows = []
    if research:
        rows.append(
            [
                "research_doc",
                research.get("path"),
                "yes" if research.get("exists") else "no",
                research.get("required_marker_count"),
                len(research.get("missing_markers") or []),
                short_hash(research.get("source_sha256")),
                (
                    f"{int(research.get('highest_value_reference_count') or 0)} refs, "
                    f"{int(research.get('near_term_item_count') or 0)} near-term"
                ),
            ]
        )
    if implementation:
        rows.append(
            [
                "implementation_plan",
                implementation.get("path"),
                "yes" if implementation.get("exists") else "no",
                implementation.get("required_marker_count"),
                len(implementation.get("missing_markers") or []),
                short_hash(implementation.get("source_sha256")),
                (
                    f"{int(implementation.get('track_count') or 0)} tracks, "
                    f"{int(implementation.get('done_count') or 0)} done, "
                    f"{int(implementation.get('task_count') or 0)} tasks"
                ),
            ]
        )
    if not rows:
        return ["No plan snapshot was recorded."]
    return table(["Source", "Path", "Present", "Markers", "Missing", "SHA256", "Summary"], rows)


def audit_metadata_lines(metadata: dict[str, Any]) -> list[str]:
    repo = metadata.get("repo") if isinstance(metadata.get("repo"), dict) else {}
    rows = [
        ["Schema", metadata.get("schema_version")],
        ["Generated UTC", metadata.get("generated_at_utc")],
    ]
    if repo:
        rows.extend(
            [
                ["Repo detected", repo.get("detected")],
                ["Repo root", repo.get("root") or repo.get("path")],
                ["Branch", repo.get("branch")],
                ["Commit", short_hash(repo.get("commit"))],
                ["Dirty", repo.get("dirty")],
                ["Remote", repo.get("remote")],
            ]
        )
    return table(["Field", "Value"], rows)


def short_hash(value: Any) -> str:
    if not isinstance(value, str) or not value:
        return "n/a"
    return value[:12]


def dict_items(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def count_status(items: list[dict[str, Any]], status: str) -> int:
    return sum(1 for item in items if item.get("status") == status)


def bench_subcheck_summary(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    parts = []
    for item in value:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        status = item.get("status")
        if name and status:
            parts.append(f"{name} ({status})")
        elif name:
            parts.append(str(name))
    return ", ".join(parts)


def join_values(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    return ", ".join(str(item) for item in value if str(item))


def human_label(value: str) -> str:
    return value.replace("_", " ").replace(".", " ").strip().capitalize()


def table(headers: list[str], rows: list[list[Any]]) -> list[str]:
    escaped_headers = [escape_table_cell(header) for header in headers]
    lines = [
        "| " + " | ".join(escaped_headers) + " |",
        "| " + " | ".join("---" for _ in escaped_headers) + " |",
    ]
    for row in rows:
        cells = [escape_table_cell(format_cell(value)) for value in row]
        if len(cells) < len(headers):
            cells.extend("" for _ in range(len(headers) - len(cells)))
        lines.append("| " + " | ".join(cells[: len(headers)]) + " |")
    return lines


def format_cell(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (dict, list)):
        return json.dumps(value, sort_keys=True)
    return str(value)


def escape_table_cell(value: str) -> str:
    return value.replace("|", "\\|").replace("\n", "<br>")


def main() -> None:
    args = parse_args()
    report_path = Path(args.report).expanduser()
    report = json.loads(report_path.read_text())
    markdown = render_handoff_markdown(report, report_path=report_path)
    if args.output:
        output = Path(args.output).expanduser()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(markdown)
        print(f"Autonomy handoff: {output}")
        print(f"__VISION_NAV_AUTONOMY_HANDOFF__={output}")
    else:
        print(markdown, end="")


if __name__ == "__main__":
    main()
