from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shlex
import tarfile
from typing import Any


SCHEMA_VERSION = "vision_nav_autonomy_evidence_workflow_v1"
CAPTURE_SCRIPT_GENERATION_HINT = (
    "Rerun field capture preflight to generate run_field_capture.sh for this capture-ready condition."
)

REQUIRED_WORKFLOW_STEPS = [
    "create_field_evidence_template",
    "create_field_collection_plan",
    "select_field_collection_condition",
    "preflight_field_capture",
    "capture_field_terrain_log",
    "register_field_replay_case",
    "refresh_field_collection_plan",
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
    "select_field_collection_condition": {
        "command": "Load the next condition from field_collection_plan.json before capture/register.",
        "desktop_action": "Module Setup > Load Next Field Condition",
    },
    "capture_field_terrain_log": {
        "command": "VISION_NAV_COUNT=30 ./scripts/pi/run_terrain_nav_loop.sh && VISION_NAV_RUNTIME_STATUS_ROOTS=$HOME/DroneTransfer/outgoing/terrain-match ./scripts/pi/read_runtime_status.sh",
        "desktop_action": "Module Setup > Field Log Capture",
    },
    "preflight_field_capture": {
        "command": "./scripts/pi/preflight_field_capture.sh",
        "desktop_action": "Module Setup > Field Capture Preflight",
    },
    "register_field_replay_case": {
        "command": "./scripts/pi/register_field_replay_case.sh",
        "desktop_action": "Module Setup > Field Evidence Case > Register",
    },
    "refresh_field_collection_plan": {
        "command": "./scripts/pi/create_field_collection_plan.sh",
        "desktop_action": "Module Setup > Field Collection Plan > Create Plan",
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
    "__VISION_NAV_FIELD_CAPTURE_PREFLIGHT__",
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
FIELD_METADATA_UPDATE_COMMAND_MARKER = "__VISION_NAV_FIELD_METADATA_UPDATE_COMMAND__"
TERRAIN_BUNDLE_MARKER = "__VISION_NAV_TERRAIN_BUNDLE__"
TERRAIN_BUNDLE_STATUS_MARKER = "__VISION_NAV_TERRAIN_BUNDLE_STATUS__"
TERRAIN_CAPTURE_COMMAND_MARKER = "__VISION_NAV_TERRAIN_CAPTURE_COMMAND__"
TERRAIN_CAPTURE_SCRIPT_MARKER = "__VISION_NAV_TERRAIN_CAPTURE_SCRIPT__"
TERRAIN_PREFLIGHT_CAPTURE_COMMAND_MARKER = "__VISION_NAV_TERRAIN_PREFLIGHT_CAPTURE_COMMAND__"
TERRAIN_CAPTURE_OUTPUT_DIR_MARKER = "__VISION_NAV_TERRAIN_CAPTURE_OUTPUT_DIR__"
EXPECTED_TERRAIN_LOG_MARKER = "__VISION_NAV_EXPECTED_TERRAIN_LOG__"
FIELD_CAPTURE_PREFLIGHT_MARKER = "__VISION_NAV_FIELD_CAPTURE_PREFLIGHT__"
FIELD_CAPTURE_PREFLIGHT_STATUS_MARKER = "__VISION_NAV_FIELD_CAPTURE_PREFLIGHT_STATUS__"
FIELD_CAPTURE_READY_MARKER = "__VISION_NAV_FIELD_CAPTURE_READY__"
FIELD_REGISTRATION_READY_MARKER = "__VISION_NAV_FIELD_REGISTRATION_READY__"
FIELD_SELECTED_CONDITION_MARKER = "__VISION_NAV_FIELD_SELECTED_CONDITION__"
FIELD_SELECTED_CASE_MARKER = "__VISION_NAV_FIELD_SELECTED_CASE__"
FIELD_SELECTED_LOG_MARKER = "__VISION_NAV_FIELD_SELECTED_LOG__"
TERRAIN_LOG_MARKER = "__VISION_NAV_TERRAIN_LOG__"

TERRAIN_LOG_DEPENDENT_STEPS = {
    "register_field_replay_case",
    "run_feature_method_benchmark",
    "run_threshold_tuning_report",
    "validate_rosbag_export",
    "check_native_rosbag2_review",
    "run_autonomy_readiness_audit",
}


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

    checks.append(validate_workflow_provenance(report))

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

    markers = report.get("markers") if isinstance(report.get("markers"), dict) else {}
    next_required_step = workflow_next_required_step(steps, markers=markers)
    step_result_check = validate_required_step_results(steps, markers=markers)
    checks.append(step_result_check)
    required_step_details = (
        step_result_check.get("details")
        if isinstance(step_result_check.get("details"), dict)
        else {}
    )

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
        "workflow_provenance": report.get("workflow_provenance")
        if isinstance(report.get("workflow_provenance"), dict)
        else None,
        "summary": report.get("summary") if isinstance(report.get("summary"), dict) else status_counts,
        "step_count": len(step_names),
        "marker_count": len(markers),
        "issue_count": len(issues),
        "missing_required_step_count": len(required_step_details.get("missing_steps") or []),
        "active_required_step_count": int(required_step_details.get("non_passed_count") or 0),
        "downstream_blocked_step_count": int(required_step_details.get("blocked_count") or 0),
        "superseded_step_count": int(required_step_details.get("superseded_count") or 0),
        "log_archive": archive_result.get("details", {}).get("path"),
        "next_required_step": next_required_step,
        "checks": checks,
        "issues": issues,
    }


def workflow_script_sha256(repo_root: str | Path) -> str | None:
    script_path = Path(repo_root) / "scripts/pi/run_autonomy_evidence_workflow.sh"
    if not script_path.is_file():
        return None
    return hashlib.sha256(script_path.read_bytes()).hexdigest()


def validate_workflow_provenance(report: dict[str, Any]) -> dict[str, Any]:
    provenance = report.get("workflow_provenance")
    expected_step_count = len(REQUIRED_WORKFLOW_STEPS)
    expected_steps = list(REQUIRED_WORKFLOW_STEPS)
    if not isinstance(provenance, dict):
        return degraded(
            "workflow_provenance",
            "Workflow report has no provenance metadata; rerun the evidence workflow to stamp the current script and required-step contract.",
            {
                "expected_required_steps": expected_steps,
                "expected_required_step_count": expected_step_count,
            },
        )

    reported_steps = provenance.get("required_steps")
    details: dict[str, Any] = {
        "repo_commit": provenance.get("repo_commit"),
        "repo_dirty": provenance.get("repo_dirty"),
        "script_path": provenance.get("script_path"),
        "script_sha256": provenance.get("script_sha256"),
        "reported_required_step_count": len(reported_steps) if isinstance(reported_steps, list) else None,
        "expected_required_step_count": expected_step_count,
    }
    if reported_steps != expected_steps:
        details["reported_required_steps"] = reported_steps
        details["expected_required_steps"] = expected_steps
        return failed(
            "workflow_provenance",
            "Workflow report was produced with a different required-step contract; rerun the evidence workflow with the current repo.",
            details,
        )

    missing_fields = [
        field
        for field in ("repo_commit", "script_path", "script_sha256")
        if not isinstance(provenance.get(field), str) or not str(provenance.get(field)).strip()
    ]
    if missing_fields:
        details["missing_fields"] = missing_fields
        return degraded(
            "workflow_provenance",
            "Workflow report provenance is incomplete; rerun the evidence workflow before using it as final proof.",
            details,
        )

    return passed(
        "workflow_provenance",
        "Workflow report includes repo/script provenance and the current required-step contract.",
        details,
    )


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


def validate_required_step_results(steps: list[Any], *, markers: dict[str, Any] | None = None) -> dict[str, Any]:
    by_name = {str(step.get("name")): step for step in steps if isinstance(step, dict) and step.get("name")}
    non_passed_steps: list[dict[str, Any]] = []
    blocked_steps: list[dict[str, Any]] = []
    superseded_steps: list[dict[str, Any]] = []
    missing_steps: list[str] = []
    active_preflight_report = current_preflight_report(markers) if isinstance(markers, dict) else None
    active_preflight_report_path = (
        marker_string(markers, FIELD_CAPTURE_PREFLIGHT_MARKER)
        if isinstance(markers, dict)
        else None
    )
    next_required_step = workflow_next_required_step(steps, markers=markers)
    for name in REQUIRED_WORKFLOW_STEPS:
        step = by_name.get(name)
        if step is None:
            missing_steps.append(name)
            continue
        status = str(step.get("status") or "unknown")
        if status != "passed":
            non_passed_step = {
                "name": name,
                "status": status,
                "exit_code": step.get("exit_code"),
                "notes": step.get("notes"),
            }
            annotate_current_preflight_override(
                non_passed_step,
                active_preflight_report,
                active_preflight_report_path,
            )
            annotate_capture_step_from_current_preflight(
                non_passed_step,
                active_preflight_report,
                active_preflight_report_path,
            )
            annotate_selected_condition_override(non_passed_step, markers)
            if is_superseded_workflow_step(non_passed_step):
                superseded_steps.append(non_passed_step)
            elif is_terrain_log_dependent_step(non_passed_step, markers, next_required_step):
                annotate_terrain_log_dependency(non_passed_step, markers)
                blocked_steps.append(non_passed_step)
            else:
                non_passed_steps.append(non_passed_step)
    details = {
        "required_count": len(REQUIRED_WORKFLOW_STEPS),
        "missing_steps": missing_steps,
        "non_passed_steps": non_passed_steps,
        "non_passed_count": len(non_passed_steps),
        "blocked_steps": blocked_steps,
        "blocked_count": len(blocked_steps),
        "superseded_steps": superseded_steps,
        "superseded_count": len(superseded_steps),
        "next_required_step": next_required_step,
    }
    if missing_steps:
        return failed(
            "required_step_results",
            "Workflow report is missing required step result records.",
            details,
        )
    if non_passed_steps or blocked_steps:
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


def is_superseded_workflow_step(step: dict[str, Any]) -> bool:
    if step.get("name") == "preflight_field_capture" and step.get("current_preflight_allows_capture") is True:
        return True
    if step.get("name") == "select_field_collection_condition" and step.get("current_selected_condition"):
        return True
    return False


def is_terrain_log_dependent_step(
    step: dict[str, Any],
    markers: dict[str, Any] | None,
    next_required_step: dict[str, Any] | None,
) -> bool:
    if step.get("name") not in TERRAIN_LOG_DEPENDENT_STEPS:
        return False
    if not isinstance(markers, dict):
        return False
    if marker_string(markers, TERRAIN_LOG_MARKER):
        return False
    if not isinstance(next_required_step, dict):
        return False
    return next_required_step.get("name") == "capture_field_terrain_log"


def annotate_terrain_log_dependency(step: dict[str, Any], markers: dict[str, Any] | None) -> None:
    step["blocked_by"] = "capture_field_terrain_log"
    if isinstance(markers, dict):
        expected_log = marker_string(markers, EXPECTED_TERRAIN_LOG_MARKER)
        output_dir = marker_string(markers, TERRAIN_CAPTURE_OUTPUT_DIR_MARKER)
        if expected_log:
            step["required_log"] = expected_log
        if output_dir:
            step["required_runtime_status"] = runtime_status_path_for_output(output_dir)
    step["guidance"] = (
        "This workflow step waits for a parseable terrain log and runtime_status.json; "
        "capture those artifacts before rerunning this step."
    )


def annotate_current_preflight_override(
    step: dict[str, Any],
    report: dict[str, Any] | None,
    report_path: str | None,
) -> None:
    if step.get("name") != "preflight_field_capture" or not report:
        return
    if report.get("ready_for_capture") is not True:
        return
    step["current_preflight_allows_capture"] = True
    if report_path:
        step["current_preflight_report"] = report_path
    if isinstance(report.get("status"), str):
        step["current_preflight_status"] = report["status"]
    if isinstance(report.get("ready_for_registration"), bool):
        step["current_ready_for_registration"] = report["ready_for_registration"]
    capture_script_hint = field_capture_script_hint(report)
    if capture_script_hint:
        step["capture_script_hint"] = capture_script_hint
    step["guidance"] = (
        "A newer field-capture preflight report is capture-ready; the stale "
        "preflight step is superseded for capture guidance, and the next "
        "required workflow step advances to terrain-log capture."
    )


def annotate_capture_step_from_current_preflight(
    step: dict[str, Any],
    report: dict[str, Any] | None,
    report_path: str | None,
) -> None:
    if step.get("name") != "capture_field_terrain_log" or not report:
        return
    if report.get("ready_for_capture") is not True:
        return
    original_notes = str(step.get("notes") or "").strip()
    if original_notes:
        step["workflow_notes"] = original_notes
    step["notes"] = "Current field capture preflight is ready; terrain_matches.jsonl and runtime_status.json are missing."
    if report_path:
        step["preflight_report"] = report_path
    if isinstance(report.get("status"), str):
        step["preflight_status"] = report["status"]
    if isinstance(report.get("ready_for_capture"), bool):
        step["ready_for_capture"] = report["ready_for_capture"]
    if isinstance(report.get("ready_for_registration"), bool):
        step["ready_for_registration"] = report["ready_for_registration"]
    if isinstance(report.get("bundle_path"), str) and report["bundle_path"].strip():
        step["bundle_path"] = report["bundle_path"].strip()
    if isinstance(report.get("capture_script_path"), str) and report["capture_script_path"].strip():
        step["capture_script_path"] = report["capture_script_path"].strip()
    else:
        capture_script_hint = field_capture_script_hint(report)
        if capture_script_hint:
            step["capture_script_hint"] = capture_script_hint
    expected_log = report.get("terrain_log_path") or report.get("source_log")
    if isinstance(expected_log, str) and expected_log.strip():
        step["expected_log"] = expected_log.strip()
    output_dir = report.get("capture_output_dir")
    if isinstance(output_dir, str) and output_dir.strip():
        output_dir_text = output_dir.strip()
        step["output_dir"] = output_dir_text
        step["runtime_status_path"] = str(report.get("runtime_status_path") or runtime_status_path_for_output(output_dir_text))
    elif isinstance(report.get("runtime_status_path"), str) and report["runtime_status_path"].strip():
        step["runtime_status_path"] = report["runtime_status_path"].strip()
    capture_command = report.get("capture_command")
    if isinstance(capture_command, str) and capture_command.strip():
        capture_command_with_status = command_with_runtime_status_read(
            capture_command.strip(),
            runtime_status_root=str(report.get("capture_output_dir") or "").strip() or None,
        )
        step["capture_command_after_preflight"] = capture_command_with_status
        preflight_capture_command = preflight_capture_command_from_report(
            report,
            capture_command_with_status=capture_command_with_status,
        )
        if preflight_capture_command:
            step["preflight_capture_command"] = preflight_capture_command
            step["command"] = preflight_capture_command
            step["desktop_action"] = "Module Setup > Field Capture Preflight, then Field Log Capture"
        else:
            step["command"] = capture_command_with_status
            step["desktop_action"] = "Module Setup > Field Log Capture"
    metadata_update_command = report.get("metadata_update_command")
    if isinstance(metadata_update_command, str) and metadata_update_command.strip():
        step["metadata_update_command"] = metadata_update_command.strip()
    step["guidance"] = (
        "Current field capture preflight is ready; capture terrain_matches.jsonl "
        "and runtime_status.json next."
    )


def annotate_selected_condition_override(step: dict[str, Any], markers: dict[str, Any] | None) -> None:
    if step.get("name") != "select_field_collection_condition" or not isinstance(markers, dict):
        return
    status = str(step.get("status") or "unknown")
    if not should_defer_selected_condition_step("select_field_collection_condition", status, markers):
        return
    selected_condition = marker_string(markers, FIELD_SELECTED_CONDITION_MARKER)
    selected_case = marker_string(markers, FIELD_SELECTED_CASE_MARKER)
    selected_log = marker_string(markers, FIELD_SELECTED_LOG_MARKER)
    if selected_condition:
        step["current_selected_condition"] = selected_condition
    if selected_case:
        step["current_selected_case"] = selected_case
    if selected_log:
        step["current_selected_log"] = selected_log
    step["guidance"] = (
        "A field collection condition is already selected; the stale selection "
        "step is superseded for capture guidance, and the next required "
        "workflow step advances to terrain-log capture or registration."
    )


def workflow_next_required_step(steps: list[Any], *, markers: dict[str, Any] | None = None) -> dict[str, Any] | None:
    by_name = {str(step.get("name")): step for step in steps if isinstance(step, dict) and step.get("name")}
    deferred_selected_condition_step: dict[str, Any] | None = None
    for name in REQUIRED_WORKFLOW_STEPS:
        step = by_name.get(name)
        if step is None:
            return workflow_step_summary(
                name,
                status="missing",
                notes="Required workflow step has not been recorded yet.",
                markers=markers,
            )
        status = str(step.get("status") or "unknown")
        if status != "passed":
            if name == "preflight_field_capture" and current_preflight_ready_for_capture(markers):
                continue
            if should_defer_selected_condition_step(name, status, markers):
                deferred_selected_condition_step = step
                continue
            return workflow_step_summary(
                name,
                status=status,
                exit_code=step.get("exit_code"),
                notes=step.get("notes"),
                markers=markers,
            )
    if deferred_selected_condition_step is not None:
        return workflow_step_summary(
            "select_field_collection_condition",
            status=str(deferred_selected_condition_step.get("status") or "unknown"),
            exit_code=deferred_selected_condition_step.get("exit_code"),
            notes=deferred_selected_condition_step.get("notes"),
            markers=markers,
        )
    return None


def should_defer_selected_condition_step(
    name: str,
    status: str,
    markers: dict[str, Any] | None,
) -> bool:
    if name != "select_field_collection_condition" or status not in {"degraded", "skipped"}:
        return False
    if not isinstance(markers, dict):
        return False
    selected_condition = markers.get(FIELD_SELECTED_CONDITION_MARKER)
    selected_case = markers.get(FIELD_SELECTED_CASE_MARKER)
    return any(isinstance(value, str) and value.strip() for value in (selected_condition, selected_case))


def workflow_step_summary(
    name: str,
    *,
    status: str,
    exit_code: Any = None,
    notes: Any = None,
    markers: dict[str, Any] | None = None,
) -> dict[str, Any]:
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
    apply_marker_guidance(summary, markers)
    return summary


def apply_marker_guidance(summary: dict[str, Any], markers: dict[str, Any] | None) -> None:
    if not isinstance(markers, dict):
        return
    apply_preflight_marker_guidance(summary, markers)
    apply_registration_marker_guidance(summary, markers)
    apply_capture_marker_guidance(summary, markers)
    apply_current_preflight_report_guidance(summary, markers)
    apply_preflight_bundle_diagnostic(summary, markers)


def current_preflight_ready_for_capture(markers: dict[str, Any] | None) -> bool:
    if not isinstance(markers, dict):
        return False
    report = current_preflight_report(markers)
    if not report:
        return False
    return report.get("ready_for_capture") is True


def field_capture_script_hint(report: dict[str, Any]) -> str | None:
    if report.get("ready_for_capture") is not True:
        return None
    if isinstance(report.get("capture_script_path"), str) and report["capture_script_path"].strip():
        return None
    return CAPTURE_SCRIPT_GENERATION_HINT


def current_preflight_report(markers: dict[str, Any]) -> dict[str, Any] | None:
    preflight_report = marker_string(markers, FIELD_CAPTURE_PREFLIGHT_MARKER)
    if not preflight_report:
        return None
    return read_field_capture_preflight_report(preflight_report)


def apply_preflight_marker_guidance(summary: dict[str, Any], markers: dict[str, Any]) -> None:
    if summary.get("name") != "preflight_field_capture":
        return
    preflight_report = marker_string(markers, FIELD_CAPTURE_PREFLIGHT_MARKER)
    preflight_status = marker_string(markers, FIELD_CAPTURE_PREFLIGHT_STATUS_MARKER)
    capture_ready = marker_string(markers, FIELD_CAPTURE_READY_MARKER)
    registration_ready = marker_string(markers, FIELD_REGISTRATION_READY_MARKER)
    bundle_path = marker_string(markers, TERRAIN_BUNDLE_MARKER)
    bundle_status = marker_string(markers, TERRAIN_BUNDLE_STATUS_MARKER)
    expected_log = marker_string(markers, EXPECTED_TERRAIN_LOG_MARKER)
    output_dir = marker_string(markers, TERRAIN_CAPTURE_OUTPUT_DIR_MARKER)
    capture_command = marker_string(markers, TERRAIN_CAPTURE_COMMAND_MARKER)
    capture_script_path = marker_string(markers, TERRAIN_CAPTURE_SCRIPT_MARKER)
    preflight_capture_command = marker_string(markers, TERRAIN_PREFLIGHT_CAPTURE_COMMAND_MARKER)
    metadata_update_command = marker_string(markers, FIELD_METADATA_UPDATE_COMMAND_MARKER)
    if preflight_report:
        summary["preflight_report"] = preflight_report
    if preflight_status:
        summary["preflight_status"] = preflight_status
    if capture_ready is not None:
        summary["ready_for_capture"] = capture_ready == "1"
    if registration_ready is not None:
        summary["ready_for_registration"] = registration_ready == "1"
    if bundle_path:
        summary["bundle_path"] = bundle_path
    if expected_log:
        summary["expected_log"] = expected_log
    if output_dir:
        summary["output_dir"] = output_dir
        summary["runtime_status_path"] = runtime_status_path_for_output(output_dir)
    if capture_command:
        summary["capture_command_after_preflight"] = command_with_runtime_status_read(
            capture_command,
            runtime_status_root=output_dir,
        )
    if capture_script_path:
        summary["capture_script_path"] = capture_script_path
    if preflight_capture_command:
        summary["preflight_capture_command"] = preflight_capture_command
    if metadata_update_command:
        summary["metadata_update_command"] = metadata_update_command
    if bundle_status == "missing":
        summary["desktop_action"] = "Mission Planner > Build Bundle, Upload Bundle, then Module Setup > Field Capture Preflight"
        summary["command"] = bundle_validation_command(bundle_path)
        existing_notes = str(summary.get("notes") or "").strip()
        guidance = "Field capture preflight found the selected terrain bundle missing; build/upload it, validate it, then rerun preflight."
        summary["notes"] = f"{existing_notes} {guidance}".strip() if existing_notes else guidance


def apply_current_preflight_report_guidance(summary: dict[str, Any], markers: dict[str, Any]) -> None:
    if summary.get("name") not in {"preflight_field_capture", "capture_field_terrain_log"}:
        return
    report = current_preflight_report(markers)
    if not report:
        return
    report_path = marker_string(markers, FIELD_CAPTURE_PREFLIGHT_MARKER)
    if report_path:
        summary["preflight_report"] = report_path
    if isinstance(report.get("status"), str):
        summary["preflight_status"] = report["status"]
    if isinstance(report.get("ready_for_capture"), bool):
        summary["ready_for_capture"] = report["ready_for_capture"]
    if isinstance(report.get("ready_for_registration"), bool):
        summary["ready_for_registration"] = report["ready_for_registration"]
    if isinstance(report.get("bundle_path"), str) and report["bundle_path"].strip():
        summary["bundle_path"] = report["bundle_path"].strip()
    if isinstance(report.get("capture_script_path"), str) and report["capture_script_path"].strip():
        summary["capture_script_path"] = report["capture_script_path"].strip()
    else:
        capture_script_hint = field_capture_script_hint(report)
        if capture_script_hint:
            summary["capture_script_hint"] = capture_script_hint
    expected_log = report.get("terrain_log_path") or report.get("source_log")
    if isinstance(expected_log, str) and expected_log.strip():
        summary["expected_log"] = expected_log.strip()
    output_dir = report.get("capture_output_dir")
    if isinstance(output_dir, str) and output_dir.strip():
        summary["output_dir"] = output_dir.strip()
        summary["runtime_status_path"] = str(report.get("runtime_status_path") or runtime_status_path_for_output(output_dir))
    elif isinstance(report.get("runtime_status_path"), str) and report["runtime_status_path"].strip():
        summary["runtime_status_path"] = report["runtime_status_path"].strip()
    capture_command = report.get("capture_command")
    if isinstance(capture_command, str) and capture_command.strip():
        capture_command_with_status = command_with_runtime_status_read(
            capture_command,
            runtime_status_root=str(report.get("capture_output_dir") or "").strip() or None,
        )
        preflight_capture_command = preflight_capture_command_from_report(
            report,
            capture_command_with_status=capture_command_with_status,
        )
        if preflight_capture_command:
            summary["preflight_capture_command"] = preflight_capture_command
        if summary.get("name") == "capture_field_terrain_log" and report.get("ready_for_capture") is True:
            summary["command"] = preflight_capture_command or capture_command_with_status
            summary["desktop_action"] = (
                "Module Setup > Field Capture Preflight, then Field Log Capture"
                if preflight_capture_command
                else "Module Setup > Field Log Capture"
            )
            summary.pop("capture_command_after_bundle", None)
            summary["notes"] = (
                "Current field capture preflight is ready; rerun preflight and then capture the terrain log and runtime status."
                if preflight_capture_command
                else "Current field capture preflight is ready; capture the terrain log and runtime status next."
            )
        else:
            summary["capture_command_after_preflight"] = capture_command_with_status
    metadata_update_command = report.get("metadata_update_command")
    if isinstance(metadata_update_command, str) and metadata_update_command.strip():
        summary["metadata_update_command"] = metadata_update_command.strip()


def apply_registration_marker_guidance(summary: dict[str, Any], markers: dict[str, Any]) -> None:
    if summary.get("name") != "register_field_replay_case":
        return
    metadata_update_command = markers.get(FIELD_METADATA_UPDATE_COMMAND_MARKER)
    if not isinstance(metadata_update_command, str) or not metadata_update_command.strip():
        return
    summary["command"] = metadata_update_command.strip()
    summary["desktop_action"] = "Module Setup > Field Evidence Case > Update Metadata"
    summary["metadata_update_command"] = metadata_update_command.strip()
    existing_notes = str(summary.get("notes") or "").strip()
    guidance = "Capture metadata is incomplete; run the metadata update command before registration."
    summary["notes"] = f"{existing_notes} {guidance}".strip() if existing_notes else guidance


def apply_capture_marker_guidance(summary: dict[str, Any], markers: dict[str, Any]) -> None:
    if summary.get("name") != "capture_field_terrain_log":
        return
    capture_command = marker_string(markers, TERRAIN_CAPTURE_COMMAND_MARKER)
    capture_script_path = marker_string(markers, TERRAIN_CAPTURE_SCRIPT_MARKER)
    preflight_capture_command = marker_string(markers, TERRAIN_PREFLIGHT_CAPTURE_COMMAND_MARKER)
    bundle_path = marker_string(markers, TERRAIN_BUNDLE_MARKER)
    bundle_status = marker_string(markers, TERRAIN_BUNDLE_STATUS_MARKER)
    expected_log = marker_string(markers, EXPECTED_TERRAIN_LOG_MARKER)
    output_dir = marker_string(markers, TERRAIN_CAPTURE_OUTPUT_DIR_MARKER)
    metadata_update_command = marker_string(markers, FIELD_METADATA_UPDATE_COMMAND_MARKER)
    if capture_command:
        summary["command"] = capture_command
    if capture_script_path:
        summary["capture_script_path"] = capture_script_path
    if preflight_capture_command:
        summary["preflight_capture_command"] = preflight_capture_command
    if expected_log:
        summary["expected_log"] = expected_log
    if output_dir:
        summary["output_dir"] = output_dir
        summary["runtime_status_path"] = runtime_status_path_for_output(output_dir)
    if bundle_path:
        summary["bundle_path"] = bundle_path
    if metadata_update_command:
        summary["metadata_update_command"] = metadata_update_command
    if bundle_status == "missing":
        summary["desktop_action"] = "Mission Planner > Build Bundle, Upload Bundle, then Module Setup > Field Log Capture"
        summary["command"] = bundle_validation_command(bundle_path)
        if capture_command:
            summary["capture_command_after_bundle"] = command_with_runtime_status_read(
                capture_command,
                runtime_status_root=output_dir,
            )
        if preflight_capture_command:
            summary["preflight_capture_command_after_bundle"] = preflight_capture_command
        existing_notes = str(summary.get("notes") or "").strip()
        guidance = "Terrain bundle is missing; build/upload the selected mission bundle and validate it before field-log capture."
        summary["notes"] = f"{existing_notes} {guidance}".strip() if existing_notes else guidance


def apply_preflight_bundle_diagnostic(summary: dict[str, Any], markers: dict[str, Any]) -> None:
    if summary.get("name") not in {"preflight_field_capture", "capture_field_terrain_log"}:
        return
    preflight_report = marker_string(markers, FIELD_CAPTURE_PREFLIGHT_MARKER)
    if not preflight_report:
        return
    report = read_field_capture_preflight_report(preflight_report)
    if not report:
        return
    report_bundle = report.get("bundle_path")
    summary_bundle = summary.get("bundle_path")
    diagnostic = refreshed_preflight_bundle_diagnostic(report, report_bundle or summary_bundle)
    if not diagnostic:
        return
    if (
        isinstance(report_bundle, str)
        and report_bundle.strip()
        and isinstance(summary_bundle, str)
        and summary_bundle.strip()
        and report_bundle.strip() != summary_bundle.strip()
    ):
        return
    summary["bundle_diagnostic"] = diagnostic


def refreshed_preflight_bundle_diagnostic(
    report: dict[str, Any],
    bundle_path: Any,
) -> dict[str, Any] | None:
    existing = preflight_bundle_diagnostic(report)
    try:
        from vision_nav.bundle_diagnostics import refresh_compact_bundle_diagnostic

        return refresh_compact_bundle_diagnostic(bundle_path, existing)
    except Exception:
        return existing


def read_field_capture_preflight_report(path_text: str) -> dict[str, Any] | None:
    path = Path(path_text).expanduser()
    if not path.exists() or not path.is_file():
        return None
    try:
        report = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None
    if not isinstance(report, dict) or report.get("schema_version") != "vision_nav_field_capture_preflight_v1":
        return None
    return report


def preflight_bundle_diagnostic(report: dict[str, Any]) -> dict[str, Any] | None:
    for action in report.get("next_actions") or []:
        if not isinstance(action, dict) or action.get("id") != "prepare_bundle":
            continue
        diagnostic = action.get("bundle_diagnostic")
        if isinstance(diagnostic, dict):
            return diagnostic
    for check_item in report.get("checks") or []:
        if not isinstance(check_item, dict) or check_item.get("name") != "bundle_path":
            continue
        details = check_item.get("details")
        if not isinstance(details, dict):
            continue
        diagnostic = details.get("diagnostic") or details.get("bundle_diagnostic")
        if isinstance(diagnostic, dict):
            from vision_nav.bundle_diagnostics import compact_bundle_diagnostic

            return compact_bundle_diagnostic(diagnostic)
    return None


def marker_string(markers: dict[str, Any], name: str) -> str | None:
    value = markers.get(name)
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


def preflight_capture_command_from_report(
    report: dict[str, Any],
    *,
    capture_command_with_status: str,
) -> str | None:
    explicit = report.get("preflight_capture_command")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    preflight_command = report.get("preflight_command")
    if isinstance(preflight_command, str) and preflight_command.strip():
        return command_sequence(preflight_command.strip(), capture_command_with_status)
    if report.get("ready_for_capture") is True:
        return command_sequence("./scripts/pi/preflight_field_capture.sh", capture_command_with_status)
    return None


def command_sequence(*commands: str | None) -> str | None:
    parts = [command.strip() for command in commands if isinstance(command, str) and command.strip()]
    if not parts:
        return None
    return " && ".join(parts)


def bundle_validation_command(bundle_path: str | None) -> str:
    if bundle_path:
        return f"VISION_NAV_BUNDLE={shlex.quote(bundle_path)} ./scripts/pi/validate_terrain_bundle.sh"
    return "./scripts/pi/validate_terrain_bundle.sh"


def command_with_runtime_status_read(command: str, runtime_status_root: str | None = None) -> str:
    read_command = "./scripts/pi/read_runtime_status.sh"
    if runtime_status_root:
        read_command = (
            f"VISION_NAV_RUNTIME_STATUS_ROOTS={shell_env_value(runtime_status_root)} "
            "./scripts/pi/read_runtime_status.sh"
        )
    if "read_runtime_status.sh" in command:
        if runtime_status_root and "VISION_NAV_RUNTIME_STATUS_ROOTS" not in command:
            return command.replace("./scripts/pi/read_runtime_status.sh", read_command)
        return command
    return f"{command} && {read_command}"


def shell_env_value(value: Any) -> str:
    text = str(value)
    expandable_prefixes = ("$HOME/", "${HOME}/", "$PWD/", "${PWD}/")
    if text.startswith(expandable_prefixes) and all(ch not in text for ch in " \t\n\"'`;&|<>"):
        return text
    return shlex.quote(text)


def runtime_status_path_for_output(output_dir: str) -> str:
    return f"{output_dir.rstrip('/')}/runtime_status.json"


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


def workflow_validation_detail_lines(report: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    printed_missing_steps: set[str] = set()
    for check_item in report.get("checks") or []:
        if not isinstance(check_item, dict) or check_item.get("status") == "passed":
            continue
        details = check_item.get("details") if isinstance(check_item.get("details"), dict) else {}
        missing_steps = details.get("missing_steps")
        if not isinstance(missing_steps, list):
            missing_steps = check_item.get("missing_steps")
        if isinstance(missing_steps, list):
            new_missing_steps = [
                str(step)
                for step in missing_steps
                if str(step) and str(step) not in printed_missing_steps
            ]
            if new_missing_steps:
                for step in new_missing_steps[:6]:
                    printed_missing_steps.add(step)
                lines.append(f"- Missing workflow steps: {', '.join(new_missing_steps[:6])}")
                if len(new_missing_steps) > 6:
                    lines.append(f"  ... {len(new_missing_steps) - 6} more")

        non_passed_steps = details.get("non_passed_steps")
        if not isinstance(non_passed_steps, list):
            non_passed_steps = check_item.get("non_passed_steps")
        if isinstance(non_passed_steps, list):
            for step in non_passed_steps[:4]:
                if not isinstance(step, dict):
                    continue
                step_name = step.get("name") or "unknown"
                step_status = step.get("status") or "unknown"
                lines.append(f"- Non-passing workflow step: {step_name} [{step_status}]")
                if step.get("notes"):
                    lines.append(f"  Notes: {step.get('notes')}")
                if step.get("workflow_notes"):
                    lines.append(f"  Workflow notes: {step.get('workflow_notes')}")
                if step.get("desktop_action"):
                    lines.append(f"  Desktop action: {step.get('desktop_action')}")
                if step.get("command"):
                    lines.append(f"  Command: {step.get('command')}")
                if step.get("bundle_path"):
                    lines.append(f"  Bundle: {step.get('bundle_path')}")
                if step.get("expected_log"):
                    lines.append(f"  Expected log: {step.get('expected_log')}")
                if step.get("output_dir"):
                    lines.append(f"  Output: {step.get('output_dir')}")
                if step.get("runtime_status_path"):
                    lines.append(f"  Runtime status: {step.get('runtime_status_path')}")
                if step.get("capture_script_path"):
                    lines.append(f"  Capture script: {step.get('capture_script_path')}")
                if step.get("capture_script_hint"):
                    lines.append(f"  Capture script hint: {step.get('capture_script_hint')}")
                if step.get("preflight_report"):
                    lines.append(f"  Preflight report: {step.get('preflight_report')}")
                if step.get("preflight_status"):
                    lines.append(f"  Preflight status: {step.get('preflight_status')}")
                if isinstance(step.get("ready_for_capture"), bool):
                    lines.append(f"  Ready for capture: {'yes' if step.get('ready_for_capture') else 'no'}")
                if isinstance(step.get("ready_for_registration"), bool):
                    lines.append(f"  Ready for registration: {'yes' if step.get('ready_for_registration') else 'no'}")
                if step.get("metadata_update_command"):
                    lines.append(f"  Metadata update: {step.get('metadata_update_command')}")
                if step.get("current_selected_condition"):
                    lines.append(f"  Current condition: {step.get('current_selected_condition')}")
                if step.get("current_selected_case"):
                    lines.append(f"  Current case: {step.get('current_selected_case')}")
                if step.get("current_selected_log"):
                    lines.append(f"  Current log: {step.get('current_selected_log')}")
                if step.get("current_preflight_allows_capture") is True:
                    status = step.get("current_preflight_status") or "unknown"
                    lines.append(f"  Current preflight report: capture-ready ({status})")
                    if step.get("current_preflight_report"):
                        lines.append(f"  Current preflight path: {step.get('current_preflight_report')}")
                    if step.get("guidance"):
                        lines.append(f"  Guidance: {step.get('guidance')}")

        blocked_steps = details.get("blocked_steps")
        if not isinstance(blocked_steps, list):
            blocked_steps = check_item.get("blocked_steps")
        if isinstance(blocked_steps, list):
            for step in blocked_steps[:4]:
                if not isinstance(step, dict):
                    continue
                step_name = step.get("name") or "unknown"
                step_status = step.get("status") or "unknown"
                lines.append(f"- Blocked workflow step: {step_name} [{step_status}]")
                if step.get("blocked_by"):
                    lines.append(f"  Blocked by: {step.get('blocked_by')}")
                if step.get("required_log"):
                    lines.append(f"  Required log: {step.get('required_log')}")
                if step.get("required_runtime_status"):
                    lines.append(f"  Required runtime status: {step.get('required_runtime_status')}")
                if step.get("notes"):
                    lines.append(f"  Notes: {step.get('notes')}")
                if step.get("guidance"):
                    lines.append(f"  Guidance: {step.get('guidance')}")

        superseded_steps = details.get("superseded_steps")
        if not isinstance(superseded_steps, list):
            superseded_steps = check_item.get("superseded_steps")
        if isinstance(superseded_steps, list):
            for step in superseded_steps[:4]:
                if not isinstance(step, dict):
                    continue
                step_name = step.get("name") or "unknown"
                step_status = step.get("status") or "unknown"
                lines.append(f"- Superseded workflow step: {step_name} [{step_status}]")
                if step.get("notes"):
                    lines.append(f"  Notes: {step.get('notes')}")
                if step.get("current_selected_condition"):
                    lines.append(f"  Current condition: {step.get('current_selected_condition')}")
                if step.get("current_selected_case"):
                    lines.append(f"  Current case: {step.get('current_selected_case')}")
                if step.get("current_selected_log"):
                    lines.append(f"  Current log: {step.get('current_selected_log')}")
                if step.get("current_preflight_allows_capture") is True:
                    status = step.get("current_preflight_status") or "unknown"
                    lines.append(f"  Current preflight report: capture-ready ({status})")
                    if step.get("current_preflight_report"):
                        lines.append(f"  Current preflight path: {step.get('current_preflight_report')}")
                    if step.get("guidance"):
                        lines.append(f"  Guidance: {step.get('guidance')}")

        missing_markers = details.get("missing_markers")
        if not isinstance(missing_markers, list):
            missing_markers = check_item.get("missing_markers")
        if isinstance(missing_markers, list) and missing_markers:
            marker_label = (
                "Missing final proof markers"
                if check_item.get("name") == "final_proof_markers"
                else "Missing workflow markers"
            )
            marker_names = [str(marker) for marker in missing_markers if str(marker)]
            lines.append(f"- {marker_label}: {', '.join(marker_names[:6])}")
            if len(marker_names) > 6:
                lines.append(f"  ... {len(marker_names) - 6} more")
    return lines


def workflow_next_step_detail_lines(next_step: dict[str, Any]) -> list[str]:
    details = [
        ("Preflight report", next_step.get("preflight_report")),
        ("Preflight status", next_step.get("preflight_status")),
        ("Bundle", next_step.get("bundle_path")),
        ("Expected log", next_step.get("expected_log")),
        ("Output", next_step.get("output_dir")),
        ("Runtime status", next_step.get("runtime_status_path")),
        ("Capture script", next_step.get("capture_script_path")),
        ("Capture script hint", next_step.get("capture_script_hint")),
        ("Preflight + capture", next_step.get("preflight_capture_command")),
        ("After bundle", next_step.get("capture_command_after_bundle")),
        ("Preflight + capture after bundle", next_step.get("preflight_capture_command_after_bundle")),
        ("After preflight", next_step.get("capture_command_after_preflight")),
        ("Metadata update", next_step.get("metadata_update_command")),
    ]
    lines = [
        f"{label}: {value}"
        for label, value in details
        if isinstance(value, str) and value.strip()
    ]
    if isinstance(next_step.get("ready_for_capture"), bool):
        lines.append(f"Ready for capture: {'yes' if next_step.get('ready_for_capture') else 'no'}")
    if isinstance(next_step.get("ready_for_registration"), bool):
        lines.append(f"Ready for registration: {'yes' if next_step.get('ready_for_registration') else 'no'}")
    diagnostic = next_step.get("bundle_diagnostic")
    if isinstance(diagnostic, dict):
        missing = [str(item) for item in diagnostic.get("missing_required_files") or [] if str(item)]
        if missing:
            lines.append(f"Missing bundle files: {', '.join(missing[:8])}")
        candidates = [
            item
            for item in diagnostic.get("bundle_candidates") or []
            if isinstance(item, dict) and item.get("path")
        ]
        for candidate in candidates[:3]:
            warning = " (example/synthetic only)" if candidate.get("field_proof_warning") else ""
            lines.append(f"Detected bundle candidate: {candidate.get('path')}{warning}")
        map_sources = [
            item
            for item in diagnostic.get("map_source_candidates") or []
            if isinstance(item, dict) and item.get("path")
        ]
        for source in map_sources[:3]:
            label_parts = [str(source.get("name") or "unnamed")]
            if source.get("source_format"):
                label_parts.append(str(source["source_format"]))
            if source.get("requires_import"):
                label_parts.append("import required")
            lines.append(f"Detected map source: {source.get('path')} [{'; '.join(label_parts)}]")
        search_roots = [str(item) for item in diagnostic.get("search_roots") or [] if str(item)]
        for root in search_roots[:5]:
            lines.append(f"Searched bundle/map root: {root}")
        recommendations = [
            item
            for item in diagnostic.get("recommended_actions") or []
            if isinstance(item, dict)
        ]
        for recommendation in recommendations[:3]:
            title = recommendation.get("title") or recommendation.get("id") or "bundle action"
            status = recommendation.get("status") or "unknown"
            lines.append(f"Recommended bundle action: {title} [{status}]")
            if recommendation.get("notes"):
                lines.append(f"Recommended notes: {recommendation['notes']}")
            if recommendation.get("desktop_action"):
                lines.append(f"Recommended app action: {recommendation['desktop_action']}")
            if recommendation.get("command"):
                lines.append(f"Recommended command: {recommendation['command']}")
            if recommendation.get("mission_plan_path"):
                lines.append(f"Recommended mission plan: {recommendation['mission_plan_path']}")
            if recommendation.get("qgc_plan_path"):
                lines.append(f"Recommended QGC plan: {recommendation['qgc_plan_path']}")
            if not recommendation.get("command") and recommendation.get("map_source_path"):
                lines.append(f"Recommended map source: {recommendation['map_source_path']}")
    return lines


def print_human(report: dict[str, Any]) -> None:
    print(f"Evidence workflow validation: {report.get('report_path')}")
    print(f"Status: {report.get('status')} workflow={report.get('workflow_status')}")
    print(
        f"Steps: {report.get('step_count')} markers={report.get('marker_count')} "
        f"active={report.get('active_required_step_count', 0)} "
        f"downstream={report.get('downstream_blocked_step_count', 0)} "
        f"superseded={report.get('superseded_step_count', 0)}"
    )
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
        for line in workflow_next_step_detail_lines(next_step):
            print(line)
    issues = report.get("issues") if isinstance(report.get("issues"), list) else []
    if issues:
        print("Issues:")
        for issue in issues:
            print(f"- {issue}")
    detail_lines = workflow_validation_detail_lines(report)
    if detail_lines:
        print("Details:")
        for line in detail_lines:
            print(line)


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
