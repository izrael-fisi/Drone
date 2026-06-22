from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
from typing import Any

from vision_nav.field_capture_metadata import audit_capture_metadata


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Select the next field collection condition for the autonomy evidence workflow."
    )
    parser.add_argument("--plan", required=True, help="field_collection_plan.json path.")
    parser.add_argument("--shell", action="store_true", help="Emit shell assignments for the Pi workflow wrapper.")
    parser.add_argument("--json", action="store_true", help="Emit JSON selection output.")
    return parser.parse_args()


def select_next_field_condition(plan_path: str | Path) -> dict[str, Any]:
    path = Path(plan_path).expanduser()
    if not path.exists():
        return {
            "status": "missing_plan",
            "plan_path": str(path),
            "message": f"Field collection plan is missing: {path}",
        }
    try:
        plan = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "status": "failed",
            "plan_path": str(path),
            "message": f"Could not parse field collection plan: {exc}",
        }
    if not isinstance(plan, dict):
        return {
            "status": "failed",
            "plan_path": str(path),
            "message": "Field collection plan root is not a JSON object.",
        }
    condition = plan.get("next_condition")
    if not isinstance(condition, dict):
        return {
            "status": "no_pending_condition",
            "plan_path": str(path),
            "message": "Field collection plan has no pending condition.",
            "summary": plan.get("summary") if isinstance(plan.get("summary"), dict) else {},
        }

    condition_key = str(condition.get("condition") or "").strip()
    expected = str(condition.get("expected") or "").strip()
    metadata = condition.get("capture_metadata")
    metadata_issues = audit_capture_metadata(
        metadata,
        conditions=[condition_key] if condition_key else [],
        expected=expected or None,
    )
    env = workflow_environment_for_condition(condition)
    return {
        "status": "selected",
        "plan_path": str(path),
        "condition": condition_key,
        "case_name": condition.get("case_name"),
        "expected": expected,
        "capture_output_dir": condition.get("capture_output_dir"),
        "source_log": condition.get("source_log"),
        "runtime_status_path": condition.get("runtime_status_path"),
        "capture_command": condition.get("capture_command"),
        "register_command": condition.get("register_command"),
        "capture_metadata_status": "passed" if not metadata_issues else "failed",
        "capture_metadata_issue_count": len(metadata_issues),
        "capture_metadata_issues": metadata_issues,
        "environment": env,
    }


def workflow_environment_for_condition(condition: dict[str, Any]) -> dict[str, str]:
    env: dict[str, str] = {}
    for source_key in ("capture_env", "register_env"):
        source = condition.get(source_key)
        if not isinstance(source, dict):
            continue
        for key, value in source.items():
            if isinstance(key, str) and value is not None:
                env[key] = str(value)
    if condition.get("source_log"):
        env["VISION_NAV_FIELD_LOG"] = str(condition["source_log"])
    if condition.get("capture_output_dir"):
        env["VISION_NAV_FIELD_CAPTURE_OUTPUT_DIR"] = str(condition["capture_output_dir"])
    if condition.get("bundle"):
        env["VISION_NAV_BUNDLE"] = str(condition["bundle"])
        env["VISION_NAV_FIELD_BUNDLE"] = str(condition["bundle"])
    if condition.get("condition"):
        env["VISION_NAV_FIELD_CONDITION"] = str(condition["condition"])
    if condition.get("expected"):
        env["VISION_NAV_FIELD_EXPECTED"] = str(condition["expected"])
    if condition.get("case_name"):
        env["VISION_NAV_FIELD_CASE_NAME"] = str(condition["case_name"])
    if condition.get("notes"):
        env["VISION_NAV_FIELD_NOTES"] = str(condition["notes"])
    metadata = condition.get("capture_metadata")
    if isinstance(metadata, dict):
        env["VISION_NAV_FIELD_CAPTURE_METADATA"] = json.dumps(metadata, sort_keys=True)
    env["VISION_NAV_FIELD_REPLACE"] = "1"
    return env


def shell_assignments(selection: dict[str, Any]) -> str:
    lines = [
        shell_assignment("VISION_NAV_FIELD_AUTO_SELECTION_STATUS", str(selection.get("status") or "unknown")),
        shell_assignment("VISION_NAV_FIELD_AUTO_SELECTION_MESSAGE", str(selection.get("message") or "")),
    ]
    if selection.get("status") == "selected":
        lines.extend(
            [
                shell_assignment("VISION_NAV_FIELD_AUTO_SELECTED", "1"),
                shell_assignment("VISION_NAV_FIELD_AUTO_SELECTED_CONDITION", str(selection.get("condition") or "")),
                shell_assignment("VISION_NAV_FIELD_AUTO_SELECTED_CASE", str(selection.get("case_name") or "")),
                shell_assignment("VISION_NAV_FIELD_CAPTURE_METADATA_READY", str(selection.get("capture_metadata_status") or "failed")),
                shell_assignment(
                    "VISION_NAV_FIELD_CAPTURE_METADATA_ISSUE_COUNT",
                    str(selection.get("capture_metadata_issue_count") or 0),
                ),
            ]
        )
        for key, value in (selection.get("environment") or {}).items():
            if isinstance(key, str):
                lines.append(shell_assignment(key, str(value)))
    else:
        lines.append(shell_assignment("VISION_NAV_FIELD_AUTO_SELECTED", "0"))
        lines.append(shell_assignment("VISION_NAV_FIELD_CAPTURE_METADATA_READY", "failed"))
        lines.append(shell_assignment("VISION_NAV_FIELD_CAPTURE_METADATA_ISSUE_COUNT", "0"))
    return "\n".join(lines)


def shell_assignment(key: str, value: str) -> str:
    return f"export {key}={shlex.quote(value)}"


def main() -> None:
    args = parse_args()
    selection = select_next_field_condition(args.plan)
    if args.shell:
        print(shell_assignments(selection))
    else:
        print(json.dumps(selection, indent=2, sort_keys=True))
    if selection.get("status") == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
