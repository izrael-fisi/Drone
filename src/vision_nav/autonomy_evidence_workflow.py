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
    "check_px4_receiver_proof",
    "create_support_bundle",
    "run_autonomy_readiness_audit",
]

IMPORTANT_MARKERS = [
    "__VISION_NAV_EVIDENCE_WORKFLOW_LOGS__",
    "__VISION_NAV_SUPPORT_ZIP__",
    "__VISION_NAV_FIELD_COLLECTION_PLAN__",
    "__VISION_NAV_FIELD_COLLECTION_PLAN_MD__",
    "__VISION_NAV_TERRAIN_LOG__",
    "__VISION_NAV_RUNTIME_STATUS__",
    "__VISION_NAV_PX4_SITL_SESSION__",
    "__VISION_NAV_PX4_SITL_REPORT__",
    "__VISION_NAV_ROSBAG_EXPORT_VALIDATION__",
    "__VISION_NAV_AUTONOMY_REPORT__",
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

    markers = report.get("markers") if isinstance(report.get("markers"), dict) else {}
    missing_markers = [marker for marker in IMPORTANT_MARKERS if not markers.get(marker)]
    if missing_markers:
        checks.append(
            degraded(
                "important_markers",
                "Some high-value artifact markers are missing.",
                {"missing_markers": missing_markers, "marker_count": len(markers)},
            )
        )
    else:
        checks.append(passed("important_markers", "Workflow report includes the high-value artifact markers.", {"marker_count": len(markers)}))

    missing_final_proof_markers = [marker for marker in FINAL_PROOF_MARKERS if not markers.get(marker)]
    if missing_final_proof_markers:
        checks.append(
            degraded(
                "final_proof_markers",
                "Workflow report is missing final-readiness proof artifact markers.",
                {
                    "missing_markers": missing_final_proof_markers,
                    "present_markers": [marker for marker in FINAL_PROOF_MARKERS if markers.get(marker)],
                    "marker_count": len(markers),
                },
            )
        )
    else:
        checks.append(
            passed(
                "final_proof_markers",
                "Workflow report includes every final-readiness proof artifact marker.",
                {"marker_count": len(markers)},
            )
        )

    log_archive_raw = markers.get("__VISION_NAV_EVIDENCE_WORKFLOW_LOGS__") or report.get("log_archive")
    archive_result = validate_log_archive(log_archive_raw, report_path=path, step_names=step_names)
    checks.append(archive_result)

    workflow_status = str(report.get("status") or "unknown")
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
        "checks": checks,
        "issues": issues,
    }


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
