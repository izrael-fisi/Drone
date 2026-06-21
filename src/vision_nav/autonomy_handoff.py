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


def render_handoff_markdown(report: dict[str, Any]) -> str:
    evidence = report.get("evidence_manifest") if isinstance(report.get("evidence_manifest"), dict) else {}
    ready = evidence.get("ready_for_goal_completion") is True
    lines = [
        "# Autonomy Readiness Handoff",
        "",
        f"- Status: {format_cell(report.get('status'))}",
        f"- Goal completion: {'ready' if ready else 'waiting on proof'}",
        f"- Checks: {summary_counts(report.get('summary'))}",
    ]
    blockers = evidence.get("external_blockers") if isinstance(evidence.get("external_blockers"), list) else []
    if blockers:
        lines.append(f"- External blockers: {len(blockers)}")
    lines.extend(["", "## Inputs", ""])
    inputs = report.get("inputs") if isinstance(report.get("inputs"), dict) else {}
    if inputs:
        lines.extend(table(["Input", "Path"], [[key, value or "not provided"] for key, value in sorted(inputs.items())]))
    else:
        lines.append("No input metadata was recorded.")

    artifacts = artifact_availability(report)
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
    lines.append("")
    return "\n".join(lines)


def artifact_availability(report: dict[str, Any]) -> list[dict[str, Any]]:
    paths: dict[str, str] = {}
    inputs = report.get("inputs") if isinstance(report.get("inputs"), dict) else {}
    for key, value in sorted(inputs.items()):
        if isinstance(value, str) and looks_like_path(value):
            paths[f"input:{key}"] = value

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


def summary_counts(summary: Any) -> str:
    if not isinstance(summary, dict):
        return "unknown"
    return (
        f"{int(summary.get('passed') or 0)} passed, "
        f"{int(summary.get('degraded') or 0)} degraded, "
        f"{int(summary.get('failed') or 0)} failed"
    )


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
    markdown = render_handoff_markdown(report)
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
