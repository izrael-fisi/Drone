from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from vision_nav.field_capture_metadata import audit_capture_metadata
from vision_nav.field_collection_plan import (
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
    parser.add_argument("--json", action="store_true", help="Emit JSON only.")
    return parser.parse_args()


def evaluate_field_capture_preflight(
    *,
    plan_path: str | Path,
    condition: str | None = None,
    repo_root: str | Path = ".",
    output_path: str | Path | None = None,
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

    report = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "plan_path": str(plan_file),
        "repo_root": str(repo),
        "condition": selected.get("condition") if selected else condition,
        "case_name": selected.get("case_name") if selected else None,
        "expected": selected.get("expected") if selected else None,
        "ready_for_capture": ready_for_capture,
        "ready_for_registration": ready_for_registration,
        "preflight_command": selected.get("preflight_command") if selected else None,
        "capture_command": selected.get("capture_command") if selected else None,
        "metadata_update_command": selected.get("metadata_update_command") if selected else None,
        "register_command": selected.get("register_command") if selected else None,
        "capture_output_dir": selected.get("capture_output_dir") if selected else None,
        "source_log": selected.get("source_log") if selected else None,
        "runtime_status_path": selected.get("runtime_status_path") if selected else None,
        "checks": checks,
        "summary": summarize_checks(checks),
    }
    if output_path is not None:
        output = Path(output_path).expanduser()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        report["output_path"] = str(output)
    return report


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
    capture_command = str(normalized.get("capture_command") or "").strip()
    if capture_command:
        normalized["capture_command"] = command_with_runtime_status_read(capture_command)
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


def condition_keys(plan: dict[str, Any]) -> list[str]:
    return [
        str(item.get("condition"))
        for item in plan.get("conditions") or []
        if isinstance(item, dict) and item.get("condition")
    ]


def condition_checks(condition: dict[str, Any], repo: Path) -> list[dict[str, Any]]:
    checks: list[dict[str, Any]] = []
    checks.append(path_exists_check("bundle_path", "Mission bundle exists.", "Mission bundle is missing.", condition.get("bundle")))
    checks.append(capture_output_parent_check(condition.get("capture_output_dir")))
    checks.append(script_check(repo, "terrain_runtime_wrapper", "scripts/pi/run_terrain_nav_loop.sh"))
    checks.append(script_check(repo, "runtime_status_wrapper", "scripts/pi/read_runtime_status.sh"))
    checks.append(capture_command_check(str(condition.get("capture_command") or "")))
    checks.append(metadata_command_check(str(condition.get("metadata_update_command") or "")))
    checks.append(capture_metadata_check(condition))
    checks.append(registration_inputs_check(condition, checks))
    return checks


def path_exists_check(name: str, passed_message: str, failed_message: str, value: Any) -> dict[str, Any]:
    path = expanded_path(value)
    if path and path.exists():
        return passed(name, passed_message, {"path": str(path)})
    return failed(name, failed_message, {"path": str(path) if path else None})


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
    status = "degraded" if ancestor and os.access(ancestor, os.W_OK) else "failed"
    message = "Capture output path parent is missing; create or verify it before field capture."
    return check(status, "capture_output_parent", message, {"path": str(path), "nearest_existing": str(ancestor) if ancestor else None})


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
        for needle in ("run_terrain_nav_loop.sh", "read_runtime_status.sh", "VISION_NAV_OUTPUT_DIR", "VISION_NAV_COUNT")
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
    for item in report.get("checks") or []:
        status = item.get("status")
        name = item.get("name")
        message = item.get("message")
        print(f"- {name} [{status}]: {message}")
    if report.get("capture_command"):
        print("Capture command:")
        print(report["capture_command"])
    if report.get("metadata_update_command"):
        print("Metadata update command:")
        print(report["metadata_update_command"])
    if report.get("register_command"):
        print("Register command:")
        print(report["register_command"])
    if report.get("output_path"):
        print(f"Report: {report['output_path']}")
    print(f"__VISION_NAV_FIELD_CAPTURE_PREFLIGHT_STATUS__={report['status']}")
    print(f"__VISION_NAV_FIELD_CAPTURE_READY__={1 if report.get('ready_for_capture') else 0}")
    print(f"__VISION_NAV_FIELD_REGISTRATION_READY__={1 if report.get('ready_for_registration') else 0}")
    if report.get("output_path"):
        print(f"__VISION_NAV_FIELD_CAPTURE_PREFLIGHT__={report['output_path']}")


def main() -> None:
    args = parse_args()
    report = evaluate_field_capture_preflight(
        plan_path=args.plan,
        condition=args.condition,
        repo_root=args.repo_root,
        output_path=args.output,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human(report)
    if report["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
