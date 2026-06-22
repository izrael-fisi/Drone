from __future__ import annotations

import argparse
import json
from pathlib import Path
import tarfile
from typing import Any


SCHEMA_VERSION = "vision_nav_autonomy_evidence_workflow_v1"

REQUIRED_WORKFLOW_STEPS = [
    "create_field_evidence_template",
    "create_field_collection_plan",
    "capture_field_terrain_log",
    "register_field_replay_case",
    "run_feature_method_benchmark",
    "run_threshold_tuning_report",
    "validate_rosbag_export",
    "check_native_rosbag2_review",
    "check_px4_receiver_proof",
    "create_support_bundle",
    "run_autonomy_readiness_audit",
]

WORKFLOW_STEP_GUIDANCE = {
    "create_field_evidence_template": {
        "command": "./scripts/pi/create_field_evidence_template.sh",
        "desktop_action": "Module Setup > Field Evidence Template > Create",
    },
    "create_field_collection_plan": {
        "command": "./scripts/pi/create_field_collection_plan.sh",
        "desktop_action": "Module Setup > Field Collection Plan > Create Plan",
    },
    "capture_field_terrain_log": {
        "command": "VISION_NAV_COUNT=30 ./scripts/pi/run_terrain_nav_loop.sh",
        "desktop_action": "Module Setup > Field Log Capture",
    },
    "register_field_replay_case": {
        "command": "./scripts/pi/register_field_replay_case.sh",
        "desktop_action": "Module Setup > Field Evidence Case > Register",
    },
    "run_feature_method_benchmark": {
        "command": "./scripts/pi/run_feature_method_benchmark.sh",
        "desktop_action": "Module Setup > Feature Benchmark",
    },
    "run_threshold_tuning_report": {
        "command": "./scripts/pi/run_threshold_tuning_report.sh",
        "desktop_action": "Module Setup > Threshold Tuning",
    },
    "validate_rosbag_export": {
        "command": "./scripts/pi/run_rosbag_export_validation.sh",
        "desktop_action": "Module Setup > ROS Bag Validation",
    },
    "check_native_rosbag2_review": {
        "command": "./scripts/dev/run_rosbag2_cli_review.sh",
        "desktop_action": "Module Setup > Native rosbag2 Review",
    },
    "check_px4_receiver_proof": {
        "command": "VISION_NAV_SITL_SMOKE_DIR=$PWD/px4-sitl-evidence ./scripts/dev/run_px4_sitl_external_vision_capture.sh",
        "desktop_action": "Module Setup > PX4 SITL Receiver Capture",
    },
    "create_support_bundle": {
        "command": "./scripts/pi/create_support_bundle.sh",
        "desktop_action": "Module Setup > Bench Report",
    },
    "run_autonomy_readiness_audit": {
        "command": "./scripts/pi/run_autonomy_readiness_audit.sh",
        "desktop_action": "Module Setup > Autonomy Readiness",
    },
}

IMPORTANT_MARKERS = [
    "__VISION_NAV_EVIDENCE_WORKFLOW_LOGS__",
    "__VISION_NAV_SUPPORT_ZIP__",
    "__VISION_NAV_PX4_SITL_PREREQS__",
    "__VISION_NAV_FIELD_COLLECTION_PLAN__",
    "__VISION_NAV_FIELD_COLLECTION_PLAN_MD__",
    "__VISION_NAV_TERRAIN_LOG__",
    "__VISION_NAV_RUNTIME_STATUS__",
    "__VISION_NAV_ROSBAG_EXPORT_VALIDATION__",
    "__VISION_NAV_AUTONOMY_REPORT__",
]

IMPORTANT_MARKER_ALTERNATIVES = [
    ("__VISION_NAV_PX4_SITL_SESSION__", "__VISION_NAV_PX4_SITL_REPORT__"),
]

FINAL_PROOF_MARKERS = [
    "__VISION_NAV_SUPPORT_ZIP__",
    "__VISION_NAV_PX4_SITL_REPORT__",
    "__VISION_NAV_FIELD_COLLECTION_PLAN__",
    "__VISION_NAV_FIELD_EVIDENCE_REPORT__",
    "__VISION_NAV_FEATURE_METHOD_REPORT__",
    "__VISION_NAV_THRESHOLD_REPORT__",
    "__VISION_NAV_ROSBAG_EXPORT_VALIDATION__",
    "__VISION_NAV_ROSBAG2_CLI_REVIEW__",
    "__VISION_NAV_AUTONOMY_REPORT__",
    "__VISION_NAV_AUTONOMY_HANDOFF__",
    "__VISION_NAV_AUTONOMY_EVIDENCE_PACKAGE__",
]
FINAL_PROOF_MARKER_ALTERNATIVES: list[tuple[str, ...]] = []


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate an autonomy evidence workflow report and its full step-log archive."
    )
    parser.add_argument("--report", required=True, help="Path to autonomy_evidence_workflow.json.")
    parser.add_argument("--output", help="Optional JSON validation report output path.")
    parser.add_argument("--json", action="store_true", help="Emit JSON only.")
    return parser.parse_args()


def validate_workflow_report(report_path: str | Path) -> dict[str, Any]:
    path = Path(report_path).expanduser()
    checks: list[dict[str, Any]] = []

    if not path.exists():
        return {
            "schema_version": "vision_nav_autonomy_evidence_workflow_validation_v1",
            "status": "failed",
            "report_path": str(path),
            "workflow_status": None,
            "checks": [failed("report_exists", f"Workflow report does not exist: {path}")],
            "issues": [f"Workflow report does not exist: {path}"],
        }

    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        return {
            "schema_version": "vision_nav_autonomy_evidence_workflow_validation_v1",
            "status": "failed",
            "report_path": str(path),
            "workflow_status": None,
            "checks": [failed("report_json", f"Could not parse workflow report JSON: {exc}")],
            "issues": [f"Could not parse workflow report JSON: {exc}"],
        }

    if report.get("schema_version") == SCHEMA_VERSION:
        checks.append(passed("schema", "Workflow report schema is valid."))
    else:
        checks.append(
            failed(
                "schema",
                f"Expected schema_version {SCHEMA_VERSION}, got {report.get('schema_version')!r}.",
            )
        )

    steps = report.get("steps") if isinstance(report.get("steps"), list) else []
    step_names = [str(step.get("name")) for step in steps if isinstance(step, dict) and step.get("name")]
    missing_steps = [name for name in REQUIRED_WORKFLOW_STEPS if name not in step_names]
    if missing_steps:
        checks.append(
            failed(
                "required_steps",
                "Workflow report is missing required step records.",
                {"missing_steps": missing_steps, "step_count": len(step_names)},
            )
        )
    else:
        checks.append(passed("required_steps", "Workflow report includes every ordered evidence step.", {"step_count": len(step_names)}))

    step_statuses = [str(step.get("status") or "unknown") for step in steps if isinstance(step, dict)]
    status_counts = {
        "passed": step_statuses.count("passed"),
        "failed": step_statuses.count("failed"),
        "skipped": step_statuses.count("skipped"),
        "degraded": step_statuses.count("degraded"),
        "unknown": step_statuses.count("unknown"),
    }
    if any(status not in {"passed", "failed", "skipped", "degraded"} for status in step_statuses):
        checks.append(degraded("step_statuses", "Some workflow steps have unknown statuses.", status_counts))
    else:
        checks.append(passed("step_statuses", "Workflow step statuses are parseable.", status_counts))

    next_required_step = workflow_next_required_step(steps)
    step_result_check = validate_required_step_results(steps)
    checks.append(step_result_check)

    markers = report.get("markers") if isinstance(report.get("markers"), dict) else {}
    important_presence = marker_presence(
        markers,
        required_markers=IMPORTANT_MARKERS,
        alternative_groups=IMPORTANT_MARKER_ALTERNATIVES,
    )
    missing_markers = important_presence["missing_markers"]
    if missing_markers:
        checks.append(
            degraded(
                "important_markers",
                "Some high-value artifact markers are missing.",
                {
                    "missing_markers": missing_markers,
                    "present_markers": important_presence["present_markers"],
                    "marker_count": len(markers),
                },
            )
        )
    else:
        checks.append(
            passed(
                "important_markers",
                "Workflow report includes the high-value artifact markers.",
                {"present_markers": important_presence["present_markers"], "marker_count": len(markers)},
            )
        )

    final_proof_presence = marker_presence(
        markers,
        required_markers=FINAL_PROOF_MARKERS,
        alternative_groups=FINAL_PROOF_MARKER_ALTERNATIVES,
    )
    missing_final_proof_markers = final_proof_presence["missing_markers"]
    if missing_final_proof_markers:
        checks.append(
            degraded(
                "final_proof_markers",
                "Workflow report is missing final-readiness proof artifact markers.",
                {
                    "missing_markers": missing_final_proof_markers,
                    "present_markers": final_proof_presence["present_markers"],
                    "marker_count": len(markers),
                },
            )
        )
    else:
        checks.append(
            passed(
                "final_proof_markers",
                "Workflow report includes every final-readiness proof artifact marker.",
                {"present_markers": final_proof_presence["present_markers"], "marker_count": len(markers)},
            )
        )

    log_archive_raw = markers.get("__VISION_NAV_EVIDENCE_WORKFLOW_LOGS__") or report.get("log_archive")
    archive_result = validate_log_archive(log_archive_raw, report_path=path, step_names=step_names)
    checks.append(archive_result)

    workflow_status = str(report.get("status") or "unknown")
    checks.append(
        validate_final_readiness_status(
            markers,
            steps,
            workflow_status=workflow_status,
            report_path=path,
        )
    )
    if workflow_status == "passed":
        checks.append(passed("workflow_status", "Workflow completed without failed or skipped steps."))
    elif workflow_status in {"failed", "degraded"}:
        checks.append(
            degraded(
                "workflow_status",
                f"Workflow status is {workflow_status}; the report is useful, but readiness proof is incomplete.",
            )
        )
    else:
        checks.append(degraded("workflow_status", f"Workflow status is unknown: {workflow_status!r}."))

    status = aggregate_status(checks)
    issues = [check["message"] for check in checks if check["status"] in {"failed", "degraded"}]
    return {
        "schema_version": "vision_nav_autonomy_evidence_workflow_validation_v1",
        "status": status,
        "report_path": str(path),
        "workflow_status": workflow_status,
        "workflow_generated_at": report.get("generated_at"),
        "summary": report.get("summary") if isinstance(report.get("summary"), dict) else status_counts,
        "step_count": len(step_names),
        "marker_count": len(markers),
        "log_archive": archive_result.get("details", {}).get("path"),
        "next_required_step": next_required_step,
        "checks": checks,
        "issues": issues,
    }


def validate_final_readiness_status(
    markers: dict[str, Any],
    steps: list[Any],
    *,
    workflow_status: str,
    report_path: Path,
) -> dict[str, Any]:
    raw_path = markers.get("__VISION_NAV_AUTONOMY_REPORT__")
    if not raw_path:
        return degraded(
            "final_readiness_status",
            "Workflow report does not reference a final autonomy-readiness report.",
        )

    readiness_path = resolve_artifact_path(str(raw_path), report_path.parent)
    details: dict[str, Any] = {"path": str(readiness_path)}
    if not readiness_path.exists():
        return degraded(
            "final_readiness_status",
            f"Final autonomy-readiness report is not available locally: {readiness_path}",
            details,
        )
    if not readiness_path.is_file():
        return failed(
            "final_readiness_status",
            f"Final autonomy-readiness report path is not a file: {readiness_path}",
            details,
        )
    try:
        readiness = json.loads(readiness_path.read_text(encoding="utf-8"))
    except Exception as exc:
        return failed(
            "final_readiness_status",
            f"Could not parse final autonomy-readiness report JSON: {exc}",
            details,
        )

    readiness_status = str(readiness.get("status") or "unknown")
    details["readiness_status"] = readiness_status
    audit_step = last_workflow_step(steps, "run_autonomy_readiness_audit")
    if audit_step is None:
        return failed(
            "final_readiness_status",
            "Workflow report is missing the run_autonomy_readiness_audit step.",
            details,
        )
    step_status = str(audit_step.get("status") or "unknown")
    step_readiness_status = str(audit_step.get("readiness_report_status") or step_status)
    details["step_status"] = step_status
    details["step_readiness_status"] = step_readiness_status
    details["workflow_status"] = workflow_status

    if readiness_status not in {"passed", "degraded", "failed"}:
        return failed(
            "final_readiness_status",
            f"Final autonomy-readiness report status is invalid: {readiness_status!r}.",
            details,
        )
    if step_readiness_status != readiness_status or step_status != readiness_status:
        return failed(
            "final_readiness_status",
            "Workflow final-audit step does not match the generated autonomy-readiness report status.",
            details,
        )
    if readiness_status == "failed" and workflow_status != "failed":
        return failed(
            "final_readiness_status",
            "Workflow status must be failed when the final autonomy-readiness report is failed.",
            details,
        )
    if readiness_status == "degraded" and workflow_status not in {"failed", "degraded"}:
        return failed(
            "final_readiness_status",
            "Workflow status must be degraded or failed when the final autonomy-readiness report is degraded.",
            details,
        )
    return passed(
        "final_readiness_status",
        "Workflow final-audit step matches the generated autonomy-readiness report status.",
        details,
    )


def last_workflow_step(steps: list[Any], name: str) -> dict[str, Any] | None:
    for step in reversed(steps):
        if isinstance(step, dict) and step.get("name") == name:
            return step
    return None


def validate_required_step_results(steps: list[Any]) -> dict[str, Any]:
    by_name = {str(step.get("name")): step for step in steps if isinstance(step, dict) and step.get("name")}
    non_passed_steps: list[dict[str, Any]] = []
    missing_steps: list[str] = []
    for name in REQUIRED_WORKFLOW_STEPS:
        step = by_name.get(name)
        if step is None:
            missing_steps.append(name)
            continue
        status = str(step.get("status") or "unknown")
        if status != "passed":
            non_passed_steps.append(
                {
                    "name": name,
                    "status": status,
                    "exit_code": step.get("exit_code"),
                    "notes": step.get("notes"),
                }
            )
    details = {
        "required_count": len(REQUIRED_WORKFLOW_STEPS),
        "missing_steps": missing_steps,
        "non_passed_steps": non_passed_steps,
        "non_passed_count": len(non_passed_steps),
        "next_required_step": workflow_next_required_step(steps),
    }
    if missing_steps:
        return failed(
            "required_step_results",
            "Workflow report is missing required step result records.",
            details,
        )
    if non_passed_steps:
        return degraded(
            "required_step_results",
            "Some required workflow steps did not pass; preserve the report for diagnostics and rerun after collecting prerequisites.",
            details,
        )
    return passed(
        "required_step_results",
        "Every required workflow step passed.",
        details,
    )


def workflow_next_required_step(steps: list[Any]) -> dict[str, Any] | None:
    by_name = {str(step.get("name")): step for step in steps if isinstance(step, dict) and step.get("name")}
    for name in REQUIRED_WORKFLOW_STEPS:
        step = by_name.get(name)
        if step is None:
            return workflow_step_summary(name, status="missing", notes="Required workflow step has not been recorded yet.")
        status = str(step.get("status") or "unknown")
        if status != "passed":
            return workflow_step_summary(
                name,
                status=status,
                exit_code=step.get("exit_code"),
                notes=step.get("notes"),
            )
    return None


def workflow_step_summary(name: str, *, status: str, exit_code: Any = None, notes: Any = None) -> dict[str, Any]:
    guidance = WORKFLOW_STEP_GUIDANCE.get(name, {})
    summary: dict[str, Any] = {
        "name": name,
        "status": status,
    }
    if isinstance(exit_code, int):
        summary["exit_code"] = exit_code
    if isinstance(notes, str) and notes.strip():
        summary["notes"] = notes.strip()
    if guidance.get("command"):
        summary["command"] = guidance["command"]
    if guidance.get("desktop_action"):
        summary["desktop_action"] = guidance["desktop_action"]
    return summary


def marker_presence(
    markers: dict[str, Any],
    *,
    required_markers: list[str],
    alternative_groups: list[tuple[str, ...]],
) -> dict[str, list[str]]:
    missing: list[str] = []
    present: list[str] = []
    seen_present: set[str] = set()

    for marker in required_markers:
        if markers.get(marker):
            if marker not in seen_present:
                present.append(marker)
                seen_present.add(marker)
        else:
            missing.append(marker)

    for group in alternative_groups:
        present_group = [marker for marker in group if markers.get(marker)]
        if present_group:
            for marker in present_group:
                if marker not in seen_present:
                    present.append(marker)
                    seen_present.add(marker)
        else:
            missing.extend(group)

    return {"missing_markers": missing, "present_markers": present}


def validate_log_archive(raw_path: Any, *, report_path: Path, step_names: list[str]) -> dict[str, Any]:
    if not raw_path:
        return failed("log_archive", "Workflow report does not reference a log archive.")
    archive_path = resolve_artifact_path(str(raw_path), report_path.parent)
    if not archive_path.exists():
        return failed("log_archive", f"Workflow log archive does not exist: {archive_path}", {"path": str(archive_path)})
    if not archive_path.is_file():
        return failed("log_archive", f"Workflow log archive is not a file: {archive_path}", {"path": str(archive_path)})
    try:
        with tarfile.open(archive_path, "r:gz") as archive:
            members = set(archive.getnames())
    except Exception as exc:
        return failed("log_archive", f"Could not read workflow log archive: {exc}", {"path": str(archive_path)})

    expected_logs = [f"logs/{name}.log" for name in step_names if name]
    missing_logs = [name for name in expected_logs if name not in members]
    details = {"path": str(archive_path), "member_count": len(members), "missing_logs": missing_logs}
    if missing_logs:
        return failed("log_archive", "Workflow log archive is missing step logs.", details)
    if not expected_logs:
        return degraded("log_archive", "Workflow log archive is readable, but there were no step names to verify.", details)
    return passed("log_archive", "Workflow log archive contains every recorded step log.", details)


def resolve_artifact_path(raw_path: str, base_dir: Path) -> Path:
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path
    return base_dir / path


def passed(name: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return check(name, "passed", message, details)


def degraded(name: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return check(name, "degraded", message, details)


def failed(name: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return check(name, "failed", message, details)


def check(name: str, status: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    result = {"name": name, "status": status, "message": message}
    if details:
        result["details"] = details
    return result


def aggregate_status(checks: list[dict[str, Any]]) -> str:
    statuses = {str(check.get("status")) for check in checks}
    if "failed" in statuses:
        return "failed"
    if "degraded" in statuses:
        return "degraded"
    return "passed"


def validation_exit_code(report: dict[str, Any]) -> int:
    return 1 if report.get("status") == "failed" else 0


def print_human(report: dict[str, Any]) -> None:
    print(f"Evidence workflow validation: {report.get('report_path')}")
    print(f"Status: {report.get('status')} workflow={report.get('workflow_status')}")
    print(f"Steps: {report.get('step_count')} markers={report.get('marker_count')}")
    if report.get("log_archive"):
        print(f"Log archive: {report.get('log_archive')}")
    next_step = report.get("next_required_step") if isinstance(report.get("next_required_step"), dict) else None
    if next_step:
        print(
            "Next required step: "
            f"{next_step.get('name')} ({next_step.get('status')})"
        )
        if next_step.get("desktop_action"):
            print(f"Desktop action: {next_step.get('desktop_action')}")
        if next_step.get("command"):
            print(f"Command: {next_step.get('command')}")
    issues = report.get("issues") if isinstance(report.get("issues"), list) else []
    if issues:
        print("Issues:")
        for issue in issues:
            print(f"- {issue}")


def main() -> None:
    args = parse_args()
    report = validate_workflow_report(args.report)
    if args.output:
        output = Path(args.output).expanduser()
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        report["output_path"] = str(output)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human(report)
        if args.output:
            print(f"Validation report: {args.output}")
            print(f"__VISION_NAV_EVIDENCE_WORKFLOW_VALIDATION__={args.output}")
    raise SystemExit(validation_exit_code(report))


if __name__ == "__main__":
    main()
