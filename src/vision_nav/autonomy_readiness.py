from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
from typing import Any

from vision_nav.bench_readiness import (
    REQUIRED_PX4_RECEIVER_MESSAGE,
    degraded,
    evaluate_bench_readiness_file,
    failed,
    load_support_manifest,
    normalize_status,
    passed,
    readiness_status,
)
from vision_nav.field_conditions import REQUIRED_FIELD_CONDITIONS
from vision_nav.px4_sitl_session import evaluate_px4_sitl_session


RESEARCH_DOC_MARKERS = [
    "Highest-Value References",
    "Recommended Product Architecture Changes",
    "Near-Term Repo Integration Plan",
]

IMPLEMENTATION_PLAN_MARKERS = [
    "Track 1: External Position Output",
    "Track 2: ROS 2 Companion Runtime",
    "Track 3: Terrain Map Bundle Pipeline",
    "Track 4: Desktop Setup And Mission UX",
    "Track 5: Validation And Product Risk Controls",
]

EXTERNAL_PROOF_CHECKS = {
    "support_bundle_bench_readiness",
    "px4_receiver_proof",
    "field_evidence_proof",
    "feature_method_benchmark",
    "threshold_tuning",
    "rosbag_export_validation",
    "rosbag2_cli_review",
}

PROOF_RUNBOOK_PHASES = [
    {
        "id": "plan_source",
        "title": "Confirm source plan coverage",
        "checks": ["research_doc", "implementation_plan"],
        "depends_on": [],
        "notes": "Keep the research and implementation-plan documents present so the final audit records which plan version it evaluated.",
    },
    {
        "id": "bench_foundation",
        "title": "Create bench evidence package",
        "checks": ["support_bundle_bench_readiness", "px4_receiver_proof"],
        "depends_on": [],
        "notes": "Build and upload the terrain bundle, run the runtime, capture PX4 ODOMETRY receiver proof, export parameters, then create the support bundle.",
    },
    {
        "id": "field_dataset",
        "title": "Collect real field replay coverage",
        "checks": ["field_evidence_proof"],
        "depends_on": [],
        "notes": "Start from the field evidence template and replace every required placeholder with a real terrain log.",
    },
    {
        "id": "method_thresholds",
        "title": "Benchmark methods and tune replay thresholds",
        "checks": ["feature_method_benchmark", "threshold_tuning"],
        "depends_on": ["field_dataset"],
        "notes": "Run after field logs exist so method selection and replay thresholds are based on real GNSS-denied terrain data.",
    },
    {
        "id": "ros2_replay",
        "title": "Validate ROS replay artifacts",
        "checks": ["rosbag_export_validation", "rosbag2_cli_review"],
        "depends_on": [],
        "notes": "Export the replay artifact, then review the native rosbag2 export on a sourced ROS 2 workstation.",
    },
    {
        "id": "final_audit",
        "title": "Run final autonomy readiness audit",
        "checks": [
            "research_doc",
            "implementation_plan",
            "support_bundle_bench_readiness",
            "px4_receiver_proof",
            "field_evidence_proof",
            "feature_method_benchmark",
            "threshold_tuning",
            "rosbag_export_validation",
            "rosbag2_cli_review",
        ],
        "depends_on": ["plan_source", "bench_foundation", "field_dataset", "method_thresholds", "ros2_replay"],
        "commands": ["./scripts/pi/run_autonomy_readiness_audit.sh", "./scripts/dev/run_local_autonomy_readiness_audit.sh"],
        "notes": "Re-run after all proof artifacts are downloaded so the JSON report, Markdown handoff, and evidence ZIP can be used for final review.",
    },
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit whether the autonomy ground-control implementation plan has enough evidence to be considered executed."
    )
    parser.add_argument(
        "--research-doc",
        default="docs/autonomy-ground-control-research.md",
        help="Research document path.",
    )
    parser.add_argument(
        "--implementation-plan",
        default="docs/autonomy-ground-control-implementation-plan.md",
        help="Implementation plan document path.",
    )
    parser.add_argument(
        "--support-bundle",
        help="support_manifest.json or support bundle ZIP containing bench evidence.",
    )
    parser.add_argument(
        "--px4-sitl-session",
        help="Optional PX4 SITL evidence-session folder to evaluate directly.",
    )
    parser.add_argument(
        "--px4-sitl-report",
        help="Optional receiver_evidence.json report to evaluate directly.",
    )
    parser.add_argument(
        "--field-evidence-report",
        help="Optional field-evidence gate report to evaluate directly.",
    )
    parser.add_argument(
        "--field-collection-plan",
        help="Optional field collection plan JSON generated before real replay cases are complete.",
    )
    parser.add_argument(
        "--feature-method-benchmark-report",
        help="Optional feature-method benchmark report to evaluate directly.",
    )
    parser.add_argument(
        "--threshold-tuning-report",
        help="JSON report proving replay thresholds were tuned against real field logs.",
    )
    parser.add_argument(
        "--rosbag-export-validation",
        help="ROS bag export validation report proving replay artifacts are structurally usable.",
    )
    parser.add_argument(
        "--rosbag2-cli-review",
        help="Native rosbag2 CLI review report proving the export is readable by standard ROS 2 tooling.",
    )
    parser.add_argument(
        "--evidence-workflow-report",
        help="Optional autonomy evidence workflow report to reference in readiness handoff/packages.",
    )
    parser.add_argument(
        "--evidence-workflow-validation-report",
        help="Optional validation report for the autonomy evidence workflow report/log archive.",
    )
    parser.add_argument(
        "--evidence-workflow-log-archive",
        help="Optional compressed log archive emitted by the autonomy evidence workflow.",
    )
    parser.add_argument("--output", help="Optional JSON report output path.")
    parser.add_argument("--json", action="store_true", help="Emit JSON only.")
    return parser.parse_args()


def evaluate_autonomy_readiness(
    *,
    research_doc_path: str | Path = "docs/autonomy-ground-control-research.md",
    implementation_plan_path: str | Path = "docs/autonomy-ground-control-implementation-plan.md",
    support_bundle_path: str | Path | None = None,
    px4_sitl_session_path: str | Path | None = None,
    px4_sitl_report_path: str | Path | None = None,
    field_evidence_report_path: str | Path | None = None,
    field_collection_plan_path: str | Path | None = None,
    feature_method_benchmark_report_path: str | Path | None = None,
    threshold_tuning_report_path: str | Path | None = None,
    rosbag_export_validation_path: str | Path | None = None,
    rosbag2_cli_review_path: str | Path | None = None,
    evidence_workflow_report_path: str | Path | None = None,
    evidence_workflow_validation_report_path: str | Path | None = None,
    evidence_workflow_log_archive_path: str | Path | None = None,
) -> dict[str, Any]:
    support_report = evaluate_support_bundle(
        support_bundle_path,
        require_px4_evidence=px4_sitl_session_path is None and px4_sitl_report_path is None,
        require_feature_method_benchmark=feature_method_benchmark_report_path is None,
        require_field_evidence=field_evidence_report_path is None,
    )
    support_checks = support_checks_by_name(support_report)
    support_manifest = support_report.get("manifest") if isinstance(support_report.get("manifest"), dict) else None
    checks = [
        check_doc_markers(
            "research_doc",
            research_doc_path,
            RESEARCH_DOC_MARKERS,
            "Autonomy research doc covers references, architecture, and integration plan.",
        ),
        check_doc_markers(
            "implementation_plan",
            implementation_plan_path,
            IMPLEMENTATION_PLAN_MARKERS,
            "Implementation plan covers the required delivery tracks.",
        ),
        support_report["check"],
        check_px4_receiver_proof(px4_sitl_session_path, px4_sitl_report_path, support_checks),
        check_field_evidence_proof(field_evidence_report_path, support_checks),
        check_feature_method_proof(feature_method_benchmark_report_path, support_checks),
        check_threshold_tuning(threshold_tuning_report_path, support_manifest=support_manifest),
        check_rosbag_export_validation(rosbag_export_validation_path, support_manifest=support_manifest),
        check_rosbag2_cli_review(rosbag2_cli_review_path, support_checks),
    ]
    status = readiness_status(checks)
    next_actions = next_actions_for_checks(checks)
    plan_snapshot = build_plan_snapshot(
        research_doc_path=research_doc_path,
        implementation_plan_path=implementation_plan_path,
    )
    field_collection_markdown_path = None
    if field_collection_plan_path:
        candidate = Path(field_collection_plan_path).expanduser().with_suffix(".md")
        if candidate.exists():
            field_collection_markdown_path = candidate
    inputs = {
        "research_doc": str(Path(research_doc_path).expanduser()),
        "implementation_plan": str(Path(implementation_plan_path).expanduser()),
        "support_bundle": str(Path(support_bundle_path).expanduser()) if support_bundle_path else None,
        "px4_sitl_session": str(Path(px4_sitl_session_path).expanduser()) if px4_sitl_session_path else None,
        "px4_sitl_report": str(Path(px4_sitl_report_path).expanduser()) if px4_sitl_report_path else None,
        "field_evidence_report": str(Path(field_evidence_report_path).expanduser()) if field_evidence_report_path else None,
        "field_collection_plan": str(Path(field_collection_plan_path).expanduser()) if field_collection_plan_path else None,
        "field_collection_plan_markdown": str(field_collection_markdown_path) if field_collection_markdown_path else None,
        "feature_method_benchmark_report": (
            str(Path(feature_method_benchmark_report_path).expanduser())
            if feature_method_benchmark_report_path
            else None
        ),
        "threshold_tuning_report": str(Path(threshold_tuning_report_path).expanduser()) if threshold_tuning_report_path else None,
        "rosbag_export_validation": (
            str(Path(rosbag_export_validation_path).expanduser()) if rosbag_export_validation_path else None
        ),
        "rosbag2_cli_review": str(Path(rosbag2_cli_review_path).expanduser()) if rosbag2_cli_review_path else None,
        "evidence_workflow_report": (
            str(Path(evidence_workflow_report_path).expanduser()) if evidence_workflow_report_path else None
        ),
        "evidence_workflow_validation_report": (
            str(Path(evidence_workflow_validation_report_path).expanduser())
            if evidence_workflow_validation_report_path
            else None
        ),
        "evidence_workflow_log_archive": (
            str(Path(evidence_workflow_log_archive_path).expanduser()) if evidence_workflow_log_archive_path else None
        ),
    }
    evidence_manifest = build_evidence_manifest(status, checks, inputs, next_actions)
    report = {
        "status": status,
        "checks": checks,
        "next_actions": next_actions,
        "command_bundle": build_command_bundle(
            next_actions,
            field_collection_plan_path=field_collection_plan_path,
        ),
        "summary": {
            "failed": sum(1 for check in checks if check["status"] == "failed"),
            "degraded": sum(1 for check in checks if check["status"] == "degraded"),
            "passed": sum(1 for check in checks if check["status"] == "passed"),
        },
        "inputs": inputs,
        "plan_snapshot": plan_snapshot,
        "evidence_manifest": evidence_manifest,
        "proof_runbook": build_proof_runbook(checks, next_actions, evidence_manifest),
    }
    if support_report.get("report") is not None:
        report["bench_readiness"] = support_report["report"]
    return report


def build_command_bundle(
    next_actions: list[dict[str, Any]],
    *,
    field_collection_plan_path: str | Path | None = None,
) -> dict[str, Any]:
    next_action_commands = unique_strings(
        action.get("command")
        for action in next_actions
        if isinstance(action, dict)
    )
    field_collection_commands = field_collection_registration_commands(field_collection_plan_path)
    return {
        "next_action_commands": next_action_commands,
        "field_collection_registration_commands": field_collection_commands,
        "command_count": len(next_action_commands) + len(field_collection_commands),
    }


def field_collection_registration_commands(path: str | Path | None) -> list[str]:
    if not path:
        return []
    source = Path(path).expanduser()
    if not source.is_file():
        return []
    try:
        plan = json.loads(source.read_text())
    except Exception:
        return []
    if not isinstance(plan, dict):
        return []
    conditions = plan.get("conditions")
    if not isinstance(conditions, list):
        return []
    return unique_strings(
        item.get("register_command")
        for item in conditions
        if isinstance(item, dict) and item.get("status") != "registered"
    )


def unique_strings(values: Any) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for value in values:
        if not isinstance(value, str) or not value:
            continue
        if value in seen:
            continue
        seen.add(value)
        result.append(value)
    return result


def build_evidence_manifest(
    status: str,
    checks: list[dict[str, Any]],
    inputs: dict[str, Any],
    next_actions: list[dict[str, Any]],
) -> dict[str, Any]:
    action_conditions: dict[str, list[str]] = {}
    for action in next_actions:
        if not isinstance(action, dict):
            continue
        check = str(action.get("check") or "")
        if not check:
            continue
        raw_conditions = action.get("missing_conditions")
        if isinstance(raw_conditions, list):
            action_conditions.setdefault(check, [])
            for condition in raw_conditions:
                text = str(condition)
                if text and text not in action_conditions[check]:
                    action_conditions[check].append(text)

    proof_items = [
        evidence_manifest_item(check, inputs, action_conditions)
        for check in checks
        if isinstance(check, dict)
    ]
    completion_blockers = [
        blocker_summary(item)
        for item in proof_items
        if item.get("status") != "passed"
    ]
    external_blockers = [
        blocker_summary(item)
        for item in proof_items
        if item.get("requires_external_proof") and item.get("status") != "passed"
    ]
    return {
        "schema_version": "vision_nav_autonomy_evidence_manifest_v1",
        "ready_for_goal_completion": status == "passed",
        "completion_blockers": completion_blockers,
        "external_blockers": external_blockers,
        "proof_items": proof_items,
    }


def build_proof_runbook(
    checks: list[dict[str, Any]],
    next_actions: list[dict[str, Any]],
    evidence_manifest: dict[str, Any],
) -> dict[str, Any]:
    checks_by_name = {
        str(check.get("name") or ""): check
        for check in checks
        if isinstance(check, dict) and check.get("name")
    }
    phase_statuses: dict[str, str] = {}
    phases: list[dict[str, Any]] = []
    for spec in PROOF_RUNBOOK_PHASES:
        phase_id = str(spec["id"])
        check_names = [str(name) for name in spec.get("checks", []) if str(name)]
        depends_on = [str(name) for name in spec.get("depends_on", []) if str(name)]
        dependency_status = {name: phase_statuses.get(name, "unknown") for name in depends_on}
        dependencies_passed = all(status == "passed" for status in dependency_status.values())
        check_items = [proof_runbook_check_item(name, checks_by_name.get(name)) for name in check_names]
        checks_passed = all(item.get("status") == "passed" for item in check_items)
        if dependencies_passed and checks_passed:
            phase_status = "passed"
        elif not dependencies_passed:
            phase_status = "blocked"
        else:
            phase_status = "action_required"
        phase_statuses[phase_id] = phase_status
        phase_actions = proof_runbook_actions(check_names, next_actions)
        commands = unique_strings(
            [
                *[action.get("command") for action in phase_actions if isinstance(action, dict)],
                *[command for command in spec.get("commands", []) if isinstance(command, str)],
            ]
        )
        phase = {
            "id": phase_id,
            "title": str(spec["title"]),
            "status": phase_status,
            "depends_on": depends_on,
            "dependency_status": dependency_status,
            "checks": check_items,
            "actions": phase_actions,
            "commands": commands,
            "notes": str(spec.get("notes") or ""),
        }
        phases.append(phase)
    return {
        "schema_version": "vision_nav_autonomy_proof_runbook_v1",
        "ready_for_goal_completion": evidence_manifest.get("ready_for_goal_completion") is True,
        "summary": {
            "phase_count": len(phases),
            "passed": sum(1 for phase in phases if phase.get("status") == "passed"),
            "action_required": sum(1 for phase in phases if phase.get("status") == "action_required"),
            "blocked": sum(1 for phase in phases if phase.get("status") == "blocked"),
        },
        "phases": phases,
    }


def proof_runbook_check_item(name: str, check: dict[str, Any] | None) -> dict[str, Any]:
    if check is None:
        return {
            "name": name,
            "status": "missing",
            "message": "Check was not present in this readiness report.",
        }
    return {
        "name": name,
        "status": str(check.get("status") or "unknown"),
        "message": str(check.get("message") or ""),
    }


def proof_runbook_actions(
    check_names: list[str],
    next_actions: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for action in next_actions:
        if not isinstance(action, dict):
            continue
        action_check = str(action.get("check") or "")
        if not action_check:
            continue
        if not any(action_check == name or action_check.startswith(f"{name}.") for name in check_names):
            continue
        actions.append(compact_proof_runbook_action(action))
    return actions


def compact_proof_runbook_action(action: dict[str, Any]) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in (
        "check",
        "status",
        "title",
        "desktop_action",
        "command",
        "notes",
        "bench_subcheck",
        "bench_message",
    ):
        value = action.get(key)
        if isinstance(value, str) and value:
            compact[key] = value
    missing_conditions = action.get("missing_conditions")
    if isinstance(missing_conditions, list):
        compact["missing_conditions"] = [str(item) for item in missing_conditions if str(item)]
    bench_subchecks = normalize_bench_subchecks(action.get("bench_subchecks"))
    if bench_subchecks:
        compact["bench_subchecks"] = bench_subchecks
    return compact


def evidence_manifest_item(
    check: dict[str, Any],
    inputs: dict[str, Any],
    action_conditions: dict[str, list[str]],
) -> dict[str, Any]:
    name = str(check.get("name") or "")
    details = check.get("details") if isinstance(check.get("details"), dict) else {}
    missing_conditions = next_action_missing_conditions(name, details) or action_conditions.get(name, [])
    item = {
        "name": name,
        "status": str(check.get("status") or "unknown"),
        "message": str(check.get("message") or ""),
        "source": evidence_source_for_check(name, details, inputs),
        "requires_external_proof": name in EXTERNAL_PROOF_CHECKS,
        "missing_conditions": missing_conditions,
    }
    if name == "support_bundle_bench_readiness":
        subchecks = normalize_bench_subchecks(details.get("failed_or_degraded_checks"))
        if subchecks:
            item["bench_subchecks"] = subchecks
    return item


def evidence_source_for_check(name: str, details: dict[str, Any], inputs: dict[str, Any]) -> str | None:
    input_keys = {
        "research_doc": "research_doc",
        "implementation_plan": "implementation_plan",
        "support_bundle_bench_readiness": "support_bundle",
        "px4_receiver_proof": "px4_sitl_report",
        "field_evidence_proof": "field_evidence_report",
        "feature_method_benchmark": "feature_method_benchmark_report",
        "threshold_tuning": "threshold_tuning_report",
        "rosbag_export_validation": "rosbag_export_validation",
        "rosbag2_cli_review": "rosbag2_cli_review",
    }
    for key in ("source", "support_bundle_path", "report_path", "session_dir", "path"):
        value = details.get(key)
        if value:
            return str(value)
    input_value = inputs.get(input_keys.get(name, "")) if name in input_keys else None
    if input_value:
        return str(input_value)
    return None


def blocker_summary(item: dict[str, Any]) -> dict[str, Any]:
    summary = {
        "name": item.get("name"),
        "status": item.get("status"),
        "message": item.get("message"),
    }
    if item.get("missing_conditions"):
        summary["missing_conditions"] = item["missing_conditions"]
    if item.get("bench_subchecks"):
        summary["bench_subchecks"] = item["bench_subchecks"]
    return summary


def next_actions_for_checks(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actions = {
        "support_bundle_bench_readiness": {
            "title": "Create a support bundle with bench evidence.",
            "desktop_action": "Module Setup > Bench Report",
            "command": "./scripts/pi/create_support_bundle.sh",
            "notes": "Run after the terrain bundle, runtime logs, PX4 parameter export, and receiver proof are available.",
        },
        "px4_receiver_proof": {
            "title": "Capture PX4 external-vision receiver proof.",
            "desktop_action": "PX4 SITL capture harness, then Module Setup > Autonomy Readiness",
            "command": "VISION_NAV_SITL_SMOKE_DIR=$PWD/px4-sitl-evidence ./scripts/dev/run_px4_sitl_external_vision_capture.sh",
            "notes": "The final report must show the MAVLink ODOMETRY path arriving as fresh vehicle_visual_odometry samples with covariance/variance fields.",
        },
        "field_evidence_proof": {
            "title": "Create the field evidence template, then register real replay cases.",
            "desktop_action": "Module Setup > Field Evidence Case > Create Template, then Register",
            "command": "./scripts/pi/create_field_evidence_template.sh && ./scripts/pi/register_field_replay_case.sh",
            "notes": "Start with the field evidence template so every required condition has a placeholder, then replace placeholders with real logs for good texture, low texture, blur, seasonal change, lighting change, altitude/scale change, repeated patterns, and wrong map.",
        },
        "feature_method_benchmark": {
            "title": "Benchmark feature methods on field logs.",
            "desktop_action": "Module Setup > Feature Benchmark",
            "command": "./scripts/pi/run_feature_method_benchmark.sh",
            "notes": "Use the same field replay log to compare ORB, AKAZE, SIFT, and the neural placeholder path.",
        },
        "threshold_tuning": {
            "title": "Tune replay gates against field logs.",
            "desktop_action": "Module Setup > Threshold Tuning",
            "command": "./scripts/pi/run_threshold_tuning_report.sh",
            "notes": "Run after all required field conditions have passing replay evidence.",
        },
        "rosbag_export_validation": {
            "title": "Export and validate the ROS replay artifact.",
            "desktop_action": "Module Setup > ROS Bag Validation",
            "command": "./scripts/pi/run_rosbag_export_validation.sh",
            "notes": "Run after a terrain runtime/replay log exists. The final readiness package must include a passed ROS replay export validation with odometry and diagnostics topics.",
        },
        "rosbag2_cli_review": {
            "title": "Review the native rosbag2 export with ROS 2 CLI tools.",
            "desktop_action": "Sourced ROS 2 workstation > rosbag2 CLI Review, then Local Readiness Re-Audit",
            "command": "./scripts/dev/run_rosbag2_cli_review.sh",
            "notes": "Run on a sourced ROS 2 workstation after native rosbag2 export. The review must include a passing validator result and successful ros2 bag info output.",
        },
    }
    next_actions: list[dict[str, Any]] = []
    for check in checks:
        name = str(check.get("name") or "")
        if check.get("status") == "passed" or name not in actions:
            continue
        details = check.get("details") if isinstance(check.get("details"), dict) else {}
        missing_conditions = next_action_missing_conditions(name, details)
        action = {
            "check": name,
            "status": str(check.get("status") or "unknown"),
            **actions[name],
        }
        if name == "support_bundle_bench_readiness":
            bench_subchecks = normalize_bench_subchecks(details.get("failed_or_degraded_checks"))
            if bench_subchecks:
                action["bench_subchecks"] = bench_subchecks
                action["notes"] = (
                    f"{action['notes']} Bench subchecks needing attention: "
                    f"{', '.join(item['name'] for item in bench_subchecks)}."
                )
        if missing_conditions:
            action["missing_conditions"] = missing_conditions
            action["notes"] = f"{action['notes']} Missing conditions: {', '.join(missing_conditions)}."
        next_actions.append(action)
        if name == "support_bundle_bench_readiness":
            next_actions.extend(next_actions_for_bench_subchecks(details))
    return next_actions


def normalize_bench_subchecks(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    subchecks: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        name = str(item.get("name") or "")
        status = str(item.get("status") or "unknown")
        if not name:
            continue
        subchecks.append(
            {
                "name": name,
                "status": status,
                "message": str(item.get("message") or ""),
            }
        )
    return subchecks


def next_actions_for_bench_subchecks(details: dict[str, Any]) -> list[dict[str, Any]]:
    actions = {
        "bundle_health": {
            "title": "Rebuild or validate the terrain bundle.",
            "desktop_action": "Mission Planner > Build Bundle, then Module Setup > Bench Report",
            "command": "./scripts/pi/validate_terrain_bundle.sh",
            "notes": "The support bundle must include passing terrain bundle health before bench readiness can pass.",
        },
        "gnss_denied_plan": {
            "title": "Complete GNSS-denied mission prep before rebuilding the bundle.",
            "desktop_action": "Mission Planner > GNSS-Denied Prep, then Build/Upload Bundle and Bench Report",
            "command": "./scripts/pi/validate_terrain_bundle.sh && ./scripts/pi/create_support_bundle.sh",
            "notes": "The support bundle must include a Mission Planner export with satellite source disabled, map reset, home reset, heading, and estimator readiness all marked complete.",
        },
        "runtime_logs": {
            "title": "Run the terrain runtime before creating the bench report.",
            "desktop_action": "Module Setup > Runtime Bundle Check, then Bench Report",
            "command": "./scripts/pi/run_terrain_nav_loop.sh",
            "notes": "Create the support bundle after a runtime or replay log exists.",
        },
        "runtime_status": {
            "title": "Fetch a fresh runtime status snapshot.",
            "desktop_action": "Module Setup > Runtime Status, then Bench Report",
            "command": "./scripts/pi/run_terrain_nav_loop.sh && ./scripts/pi/read_runtime_status.sh",
            "notes": "The support bundle should include runtime_status.json beside the terrain log.",
        },
        "replay_gates": {
            "title": "Evaluate replay gates for the bench log.",
            "desktop_action": "Module Setup > Bench Report",
            "command": "./scripts/pi/register_field_replay_case.sh",
            "notes": "Replay-gate evidence should show accepted, degraded, and wrong-map behavior before final readiness.",
        },
        "px4_sitl_evidence": {
            "title": "Capture PX4 external-vision receiver proof.",
            "desktop_action": "PX4 SITL capture harness, then Module Setup > Bench Report",
            "command": "VISION_NAV_SITL_SMOKE_DIR=$PWD/px4-sitl-evidence ./scripts/dev/run_px4_sitl_external_vision_capture.sh",
            "notes": "Receiver proof must use the MAVLink ODOMETRY path and show fresh vehicle_visual_odometry samples.",
        },
        "px4_params": {
            "title": "Export and check PX4 external-vision parameters.",
            "desktop_action": "Module Setup > PX4 parameter check, then Bench Report",
            "command": "./scripts/pi/check_px4_params.sh",
            "notes": "Export PX4 parameters from QGroundControl or the PX4 shell before creating the support bundle.",
        },
        "feature_method_benchmarks": {
            "title": "Benchmark feature methods on the field log.",
            "desktop_action": "Module Setup > Feature Benchmark",
            "command": "./scripts/pi/run_feature_method_benchmark.sh",
            "notes": "Use real field logs to choose the low-compute and high-compute feature methods.",
        },
        "field_evidence": {
            "title": "Create the field evidence template, then register real replay cases.",
            "desktop_action": "Module Setup > Field Evidence Case > Create Template, then Register",
            "command": "./scripts/pi/create_field_evidence_template.sh && ./scripts/pi/register_field_replay_case.sh",
            "notes": "Field evidence must cover all required terrain conditions.",
        },
        "threshold_tuning": {
            "title": "Tune replay gates against field logs.",
            "desktop_action": "Module Setup > Threshold Tuning",
            "command": "./scripts/pi/run_threshold_tuning_report.sh",
            "notes": "Threshold tuning should run after the full field-evidence manifest passes.",
        },
        "rosbag_export_validations": {
            "title": "Export and validate the ROS replay artifact.",
            "desktop_action": "Module Setup > ROS Bag Validation, then Bench Report",
            "command": "./scripts/pi/run_rosbag_export_validation.sh && ./scripts/pi/create_support_bundle.sh",
            "notes": "Support bundles should include a passed ROS replay export validation summary.",
        },
        "rosbag2_cli_reviews": {
            "title": "Review the native rosbag2 export with ROS 2 CLI tools.",
            "desktop_action": "Sourced ROS 2 workstation > rosbag2 CLI Review, then Bench Report",
            "command": "./scripts/dev/run_rosbag2_cli_review.sh && ./scripts/pi/create_support_bundle.sh",
            "notes": "Support bundles should include a passed native rosbag2 CLI review summary when native rosbag2 export is part of the evidence package.",
        },
        "ardupilot_params": {
            "title": "Review ArduPilot ExternalNav parameters.",
            "desktop_action": "Module Setup > ArduPilot parameter check",
            "command": "./scripts/pi/check_ardupilot_params.sh",
            "notes": "ArduPilot is optional for the PX4-first bench path unless explicitly required.",
        },
    }
    next_actions: list[dict[str, Any]] = []
    for subcheck in normalize_bench_subchecks(details.get("failed_or_degraded_checks")):
        spec = actions.get(subcheck["name"])
        if not spec:
            continue
        next_actions.append(
            {
                "check": f"support_bundle_bench_readiness.{subcheck['name']}",
                "status": subcheck["status"],
                "bench_subcheck": subcheck["name"],
                "bench_message": subcheck["message"],
                **spec,
            }
        )
    return next_actions


def next_action_missing_conditions(name: str, details: dict[str, Any]) -> list[str]:
    raw = details.get("missing_conditions")
    if raw is None and name in {"field_evidence_proof", "threshold_tuning"}:
        raw = details.get("required_conditions") or REQUIRED_FIELD_CONDITIONS
    if not isinstance(raw, (list, tuple, set)):
        return []
    return [str(value) for value in raw if str(value)]


def evaluate_support_bundle(
    path: str | Path | None,
    *,
    require_px4_evidence: bool = True,
    require_feature_method_benchmark: bool = True,
    require_field_evidence: bool = True,
) -> dict[str, Any]:
    if path is None:
        return {
            "check": failed(
                "support_bundle_bench_readiness",
                "Strict autonomy readiness requires a support bundle with bench evidence.",
            ),
            "report": None,
            "manifest": None,
        }
    try:
        manifest = load_support_manifest(path)
        report = evaluate_bench_readiness_file(
            path,
            require_px4_evidence=require_px4_evidence,
            require_feature_method_benchmark=require_feature_method_benchmark,
            require_field_evidence=require_field_evidence,
        )
    except Exception as exc:
        return {
            "check": failed("support_bundle_bench_readiness", f"Could not evaluate support bundle: {exc}"),
            "report": None,
            "manifest": None,
        }
    status = normalize_status(report.get("status"))
    details = {
        "support_bundle_path": report.get("support_bundle_path"),
        "summary": report.get("summary"),
        "failed_or_degraded_checks": support_bench_subchecks(report),
    }
    if status == "passed":
        return {"check": passed("support_bundle_bench_readiness", "Support bundle bench-readiness gate passed.", details), "report": report, "manifest": manifest}
    if status == "degraded":
        return {
            "check": degraded("support_bundle_bench_readiness", "Support bundle bench-readiness gate is degraded.", details),
            "report": report,
            "manifest": manifest,
        }
    return {
        "check": failed("support_bundle_bench_readiness", f"Support bundle bench-readiness gate is {status or 'missing'}.", details),
        "report": report,
        "manifest": manifest,
    }


def support_bench_subchecks(report: dict[str, Any]) -> list[dict[str, Any]]:
    subchecks: list[dict[str, Any]] = []
    for check in report.get("checks", []):
        if not isinstance(check, dict):
            continue
        status = normalize_status(check.get("status"))
        if status == "passed":
            continue
        subchecks.append(
            {
                "name": str(check.get("name") or ""),
                "status": status or "unknown",
                "message": str(check.get("message") or ""),
                "details": check.get("details") if isinstance(check.get("details"), dict) else {},
            }
        )
    return subchecks


def support_checks_by_name(support_report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    report = support_report.get("report")
    if not isinstance(report, dict):
        return {}
    return {str(check.get("name")): check for check in report.get("checks", []) if isinstance(check, dict)}


def check_doc_markers(name: str, path: str | Path, markers: list[str], message: str) -> dict[str, Any]:
    source = Path(path).expanduser()
    if not source.exists():
        return failed(name, f"Missing {source}.", {"path": str(source)})
    text = source.read_text(errors="replace")
    missing = [marker for marker in markers if marker not in text]
    details = {"path": str(source), "missing_markers": missing}
    if missing:
        return failed(name, f"{source} is missing required plan sections.", details)
    return passed(name, message, details)


def build_plan_snapshot(
    *,
    research_doc_path: str | Path,
    implementation_plan_path: str | Path,
) -> dict[str, Any]:
    return {
        "schema_version": "vision_nav_autonomy_plan_snapshot_v1",
        "research_doc": summarize_research_doc(research_doc_path),
        "implementation_plan": summarize_implementation_plan(implementation_plan_path),
    }


def summarize_research_doc(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser()
    text = safe_read_text(source)
    missing_markers = [marker for marker in RESEARCH_DOC_MARKERS if marker not in text]
    return {
        "path": str(source),
        "exists": source.is_file(),
        "required_marker_count": len(RESEARCH_DOC_MARKERS),
        "missing_markers": missing_markers,
        "highest_value_reference_count": count_markdown_table_rows(
            section_text(text, "Highest-Value References")
        ),
        "fit_criteria_count": count_section_bullets(text, "Fit Criteria"),
        "architecture_section_count": count_numbered_headings(
            section_text(text, "Recommended Product Architecture Changes")
        ),
        "near_term_item_count": count_numbered_lines(
            section_text(text, "Near-Term Repo Integration Plan")
        ),
        "avoid_choice_count": count_section_bullets(text, "Implementation Choices To Avoid"),
    }


def summarize_implementation_plan(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser()
    text = safe_read_text(source)
    missing_markers = [marker for marker in IMPLEMENTATION_PLAN_MARKERS if marker not in text]
    return {
        "path": str(source),
        "exists": source.is_file(),
        "required_marker_count": len(IMPLEMENTATION_PLAN_MARKERS),
        "missing_markers": missing_markers,
        "track_count": len(re.findall(r"^### Track \d+:", text, flags=re.MULTILINE)),
        "done_count": count_status_lines(text, "Done"),
        "in_progress_count": count_status_lines(text, "In progress"),
        "task_count": count_plan_list_items_under_heading(text, "Tasks"),
        "next_task_count": count_plan_list_items_under_heading(text, "Next tasks"),
        "acceptance_check_count": count_plan_list_items_under_heading(text, "Acceptance checks"),
        "execution_order_count": count_numbered_lines(section_text(text, "Execution Order")),
    }


def safe_read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(errors="replace")


def section_text(text: str, heading: str) -> str:
    pattern = re.compile(rf"^(#+)\s+{re.escape(heading)}\s*$", flags=re.MULTILINE)
    match = pattern.search(text)
    if not match:
        return ""
    level = len(match.group(1))
    start = match.end()
    following = re.finditer(r"^(#+)\s+", text[start:], flags=re.MULTILINE)
    for candidate in following:
        if len(candidate.group(1)) <= level:
            return text[start : start + candidate.start()]
    return text[start:]


def count_section_bullets(text: str, heading: str) -> int:
    return count_bullet_lines(section_text(text, heading))


def count_bullet_lines(text: str) -> int:
    return len(re.findall(r"^\s*-\s+", text, flags=re.MULTILINE))


def count_numbered_lines(text: str) -> int:
    return len(re.findall(r"^\s*\d+\.\s+", text, flags=re.MULTILINE))


def count_numbered_headings(text: str) -> int:
    return len(re.findall(r"^###\s+\d+\.\s+", text, flags=re.MULTILINE))


def count_markdown_table_rows(text: str) -> int:
    rows = 0
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|") or "---" in stripped:
            continue
        cells = [cell.strip() for cell in stripped.strip("|").split("|")]
        if cells and cells[0] and cells[0] != "Reference":
            rows += 1
    return rows


def count_status_lines(text: str, status: str) -> int:
    return len(re.findall(rf"^\s*-\s+{re.escape(status)}:", text, flags=re.MULTILINE))


def count_plan_list_items_under_heading(text: str, heading: str) -> int:
    total = 0
    search_from = 0
    pattern = re.compile(rf"^({re.escape(heading)}):\s*$", flags=re.MULTILINE)
    while True:
        match = pattern.search(text, search_from)
        if not match:
            break
        start = match.end()
        next_heading = re.search(r"^\S.*:$|^#{1,6}\s+", text[start:], flags=re.MULTILINE)
        block = text[start : start + next_heading.start()] if next_heading else text[start:]
        total += count_numbered_lines(block)
        search_from = start
    return total


def check_px4_receiver_proof(
    session_path: str | Path | None,
    report_path: str | Path | None,
    support_checks: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if session_path is not None:
        try:
            report = evaluate_px4_sitl_session(session_path)
        except Exception as exc:
            return failed("px4_receiver_proof", f"Could not evaluate PX4 SITL session: {exc}", {"path": str(session_path)})
        return px4_receiver_check_from_report(report, source="px4_sitl_session")

    if report_path is not None:
        source = Path(report_path).expanduser()
        try:
            report = json.loads(source.read_text())
        except Exception as exc:
            return failed("px4_receiver_proof", f"Could not read PX4 receiver report: {exc}", {"path": str(source)})
        return px4_receiver_check_from_report(report, source=str(source))

    support_check = support_checks.get("px4_sitl_evidence")
    if support_check and support_check.get("status") == "passed":
        details = px4_receiver_details_from_support_check(support_check)
        if str(details.get("expected_message") or "").lower() != REQUIRED_PX4_RECEIVER_MESSAGE:
            return failed(
                "px4_receiver_proof",
                "PX4 receiver proof in the support bundle must use the MAVLink ODOMETRY path.",
                details,
            )
        return passed(
            "px4_receiver_proof",
            "PX4 receiver proof is present in the support bundle.",
            details,
        )
    if support_check and support_check.get("status") == "degraded":
        details = px4_receiver_details_from_support_check(support_check)
        if str(details.get("expected_message") or "").lower() != REQUIRED_PX4_RECEIVER_MESSAGE:
            return failed(
                "px4_receiver_proof",
                "PX4 receiver proof in the support bundle must use the MAVLink ODOMETRY path.",
                details,
            )
        return degraded(
            "px4_receiver_proof",
            "PX4 receiver proof in the support bundle is degraded.",
            details,
        )
    return failed("px4_receiver_proof", "PX4 SITL or bench receiver proof is required.", {"source": "support_bundle"})


def px4_receiver_check_from_report(report: dict[str, Any], *, source: str) -> dict[str, Any]:
    status = normalize_status(report.get("status"))
    listener = report.get("listener") if isinstance(report.get("listener"), dict) else {}
    details = {
        "source": source,
        "session_dir": report.get("session_dir"),
        "report_path": report.get("report_path"),
        "sample_count": listener.get("sample_count"),
        "observed_rate_hz": listener.get("observed_rate_hz"),
        "expected_message": report.get("expected_message"),
        "required_message": REQUIRED_PX4_RECEIVER_MESSAGE,
        "expected_rate_hz": (report.get("config") or {}).get("expected_rate_hz"),
        "min_rate_ratio": (report.get("config") or {}).get("min_rate_ratio"),
        "latest_sample_age_s": listener.get("latest_sample_age_s"),
    }
    if str(details.get("expected_message") or "").lower() != REQUIRED_PX4_RECEIVER_MESSAGE:
        return failed(
            "px4_receiver_proof",
            "PX4 receiver proof must use the MAVLink ODOMETRY path for final autonomy readiness.",
            details,
        )
    if status == "passed":
        return passed("px4_receiver_proof", "PX4 receiver evidence passed.", details)
    if status == "degraded":
        return degraded("px4_receiver_proof", "PX4 receiver evidence is degraded.", details)
    return failed("px4_receiver_proof", f"PX4 receiver evidence is {status or 'missing'}.", details)


def px4_receiver_details_from_support_check(support_check: dict[str, Any]) -> dict[str, Any]:
    details = dict(support_check.get("details") or {})
    details["source"] = "support_bundle"
    details["required_message"] = REQUIRED_PX4_RECEIVER_MESSAGE
    return details


def check_field_evidence_proof(path: str | Path | None, support_checks: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if path is not None:
        try:
            report = json.loads(Path(path).expanduser().read_text())
        except Exception as exc:
            return failed("field_evidence_proof", f"Could not read field evidence report: {exc}", {"path": str(path)})
        return field_evidence_check_from_report(report, source=str(Path(path).expanduser()))

    support_check = support_checks.get("field_evidence")
    if support_check and support_check.get("status") == "passed":
        details = {"source": "support_bundle", **(support_check.get("details") or {})}
        missing_conditions = missing_required_conditions(details.get("covered_conditions") or [])
        if missing_conditions:
            return failed("field_evidence_proof", "Field evidence is missing required real-world conditions.", {**details, "missing_conditions": missing_conditions})
        metadata_status = capture_metadata_proof_status(details)
        if metadata_status != "passed":
            message = "Field evidence is missing capture metadata audit proof." if metadata_status == "missing" else "Field evidence has incomplete capture metadata."
            return failed("field_evidence_proof", message, {**details, "missing_conditions": missing_conditions})
        return passed("field_evidence_proof", "Field evidence proof is present in the support bundle.", details)
    if support_check and support_check.get("status") == "degraded":
        return degraded("field_evidence_proof", "Field evidence proof in the support bundle is degraded.", {"source": "support_bundle"})
    return failed("field_evidence_proof", "Real field evidence is required for autonomy readiness.", {"source": "support_bundle"})


def field_evidence_check_from_report(report: dict[str, Any], *, source: str) -> dict[str, Any]:
    status = normalize_status(report.get("status"))
    summary = report.get("summary") or {}
    covered = summary.get("covered_conditions") or []
    missing_conditions = missing_required_conditions(covered)
    details = {
        "source": source,
        "coverage_status": summary.get("coverage_status"),
        "replay_status": summary.get("replay_status"),
        "case_count": summary.get("case_count"),
        "field_case_count": summary.get("field_case_count"),
        "capture_metadata_issue_count": summary.get("capture_metadata_issue_count"),
        "covered_conditions": covered,
        "missing_conditions": missing_conditions,
    }
    metadata_status = capture_metadata_proof_status(details)
    if status == "passed" and metadata_status == "missing":
        return failed("field_evidence_proof", "Field evidence report is missing capture metadata audit proof.", details)
    if status == "passed" and metadata_status == "failed":
        return failed("field_evidence_proof", "Field evidence report has incomplete capture metadata.", details)
    if status == "passed" and not missing_conditions:
        return passed("field_evidence_proof", "Field evidence gate passed for all required conditions.", details)
    if status == "passed":
        return failed("field_evidence_proof", "Field evidence gate passed but did not cover all required conditions.", details)
    if status == "degraded":
        return degraded("field_evidence_proof", "Field evidence gate is degraded.", details)
    return failed("field_evidence_proof", f"Field evidence gate is {status or 'missing'}.", details)


def check_feature_method_proof(path: str | Path | None, support_checks: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if path is not None:
        source = Path(path).expanduser()
        try:
            report = json.loads(source.read_text())
        except Exception as exc:
            return failed(
                "feature_method_benchmark",
                f"Could not read feature-method benchmark report: {exc}",
                {"path": str(source)},
            )
        return feature_method_check_from_report(report, source=str(source))

    support_check = support_checks.get("feature_method_benchmarks")
    if support_check and support_check.get("status") == "passed":
        return passed("feature_method_benchmark", "Feature-method benchmark evidence is present in the support bundle.", support_check.get("details") or {})
    if support_check and support_check.get("status") == "degraded":
        return degraded("feature_method_benchmark", "Feature-method benchmark evidence is degraded.", support_check.get("details") or {})
    return failed("feature_method_benchmark", "Feature-method benchmark evidence on field logs is required.", {})


def feature_method_check_from_report(report: dict[str, Any], *, source: str) -> dict[str, Any]:
    status = normalize_status(report.get("status"))
    methods = report.get("methods") if isinstance(report.get("methods"), list) else []
    passed_methods = [
        str(method.get("method"))
        for method in methods
        if isinstance(method, dict) and normalize_status(method.get("status")) == "passed" and method.get("method")
    ]
    degraded_methods = [
        str(method.get("method"))
        for method in methods
        if isinstance(method, dict) and normalize_status(method.get("status")) == "degraded" and method.get("method")
    ]
    recommended_method = report.get("recommended_method")
    details = {
        "source": source,
        "case_name": report.get("case_name"),
        "expected": report.get("expected"),
        "recommended_method": recommended_method,
        "method_count": len(methods),
        "passed_methods": passed_methods,
        "degraded_methods": degraded_methods,
    }
    if status == "passed" and recommended_method and passed_methods:
        return passed("feature_method_benchmark", "Feature-method benchmark report passed.", details)
    if status == "passed":
        return failed("feature_method_benchmark", "Feature-method benchmark report passed without a usable recommended method.", details)
    if status == "degraded":
        return degraded("feature_method_benchmark", "Feature-method benchmark report is degraded.", details)
    return failed("feature_method_benchmark", f"Feature-method benchmark report is {status or 'missing'}.", details)


def check_threshold_tuning(path: str | Path | None, *, support_manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    if path is None:
        threshold_tuning = (support_manifest or {}).get("threshold_tuning") if support_manifest else None
        if isinstance(threshold_tuning, dict):
            return threshold_tuning_check_from_summary(threshold_tuning)
        return failed(
            "threshold_tuning",
            "Threshold tuning report is required after real field logs are collected.",
            {"required_conditions": REQUIRED_FIELD_CONDITIONS},
        )
    source = Path(path).expanduser()
    try:
        report = json.loads(source.read_text())
    except Exception as exc:
        return failed("threshold_tuning", f"Could not read threshold tuning report: {exc}", {"path": str(source)})
    return threshold_tuning_check_from_report(report, source=str(source))


def threshold_tuning_check_from_summary(summary: dict[str, Any]) -> dict[str, Any]:
    reports = summary.get("reports") if isinstance(summary.get("reports"), list) else []
    status = normalize_status(summary.get("status"))
    covered = summary.get("covered_conditions") or []
    if not covered and reports:
        covered = [
            condition
            for report in reports
            if isinstance(report, dict)
            for condition in report.get("covered_conditions", [])
        ]
    details = {
        "source": "support_bundle",
        "report_count": summary.get("report_count"),
        "field_case_count": summary.get("field_case_count"),
        "capture_metadata_issue_count": summary.get("capture_metadata_issue_count"),
        "covered_conditions": sorted({str(condition) for condition in covered}),
        "missing_conditions": missing_required_conditions(covered),
    }
    if status == "not_provided" or status is None:
        return failed("threshold_tuning", "Threshold tuning report is required after real field logs are collected.", details)
    metadata_status = capture_metadata_proof_status(details)
    if status == "passed" and metadata_status == "missing":
        return failed("threshold_tuning", "Threshold tuning proof is missing capture metadata audit proof.", details)
    if status == "passed" and metadata_status == "failed":
        return failed("threshold_tuning", "Threshold tuning proof has incomplete capture metadata.", details)
    if status == "passed" and not details["missing_conditions"]:
        return passed("threshold_tuning", "Threshold tuning proof is present in the support bundle.", details)
    if status == "passed":
        return failed("threshold_tuning", "Threshold tuning proof is missing required conditions.", details)
    if status == "degraded":
        return degraded("threshold_tuning", "Threshold tuning proof in the support bundle is degraded.", details)
    return failed("threshold_tuning", f"Threshold tuning proof in the support bundle is {status}.", details)


def threshold_tuning_check_from_report(report: dict[str, Any], *, source: str) -> dict[str, Any]:
    status = normalize_status(report.get("status"))
    covered = report_conditions(report)
    missing_conditions = missing_required_conditions(covered)
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    details = {
        "source": source,
        "covered_conditions": sorted(covered),
        "missing_conditions": missing_conditions,
        "method": report.get("method"),
        "capture_metadata_issue_count": summary.get("capture_metadata_issue_count"),
    }
    metadata_status = capture_metadata_proof_status(details)
    if status == "passed" and metadata_status == "missing":
        return failed("threshold_tuning", "Threshold tuning report is missing capture metadata audit proof.", details)
    if status == "passed" and metadata_status == "failed":
        return failed("threshold_tuning", "Threshold tuning report has incomplete capture metadata.", details)
    if status == "passed" and not missing_conditions:
        return passed("threshold_tuning", "Thresholds were tuned against all required field conditions.", details)
    if status == "passed":
        return failed("threshold_tuning", "Threshold tuning report is missing required conditions.", details)
    if status == "degraded":
        return degraded("threshold_tuning", "Threshold tuning report is degraded.", details)
    return failed("threshold_tuning", f"Threshold tuning report is {status or 'missing'}.", details)


def check_rosbag_export_validation(
    path: str | Path | None,
    *,
    support_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if path is not None:
        source = Path(path).expanduser()
        try:
            report = json.loads(source.read_text())
        except Exception as exc:
            return failed(
                "rosbag_export_validation",
                f"Could not read ROS bag export validation report: {exc}",
                {"path": str(source)},
            )
        return rosbag_export_validation_check_from_report(report, source=str(source))

    validations = (support_manifest or {}).get("rosbag_export_validations") if support_manifest else None
    if isinstance(validations, dict):
        return rosbag_export_validation_check_from_summary(validations)
    return failed(
        "rosbag_export_validation",
        "ROS replay export validation report is required for final readiness.",
        {"required_topics": ["/vision_nav/odometry", "/diagnostics"]},
    )


def rosbag_export_validation_check_from_summary(summary: dict[str, Any]) -> dict[str, Any]:
    reports = summary.get("reports") if isinstance(summary.get("reports"), list) else []
    status = normalize_status(summary.get("status"))
    topics = sorted(
        {
            topic
            for report in reports
            if isinstance(report, dict)
            for topic in rosbag_report_topics(report)
        }
    )
    missing_topics = missing_rosbag_topics(topics)
    details = {
        "source": "support_bundle",
        "report_count": summary.get("report_count"),
        "formats": summary.get("formats"),
        "message_count": summary.get("message_count"),
        "topic_count": summary.get("topic_count"),
        "topics": topics,
        "missing_topics": missing_topics,
    }
    if status == "not_provided" or status is None:
        return failed(
            "rosbag_export_validation",
            "ROS replay export validation report is required for final readiness.",
            details,
        )
    if status == "passed" and not missing_topics and int(summary.get("message_count") or 0) > 0:
        return passed("rosbag_export_validation", "ROS replay export validation proof is present in the support bundle.", details)
    if status == "passed":
        return failed(
            "rosbag_export_validation",
            "ROS replay export validation proof is missing required topics or messages.",
            details,
        )
    if status == "degraded":
        return degraded(
            "rosbag_export_validation",
            "ROS replay export validation proof in the support bundle is degraded.",
            details,
        )
    return failed(
        "rosbag_export_validation",
        f"ROS replay export validation proof in the support bundle is {status}.",
        details,
    )


def rosbag_export_validation_check_from_report(report: dict[str, Any], *, source: str) -> dict[str, Any]:
    status = normalize_status(report.get("status"))
    topics = rosbag_report_topics(report)
    missing_topics = missing_rosbag_topics(topics)
    details = {
        "source": source,
        "format": report.get("format"),
        "artifact_path": report.get("artifact_path"),
        "metadata_path": report.get("metadata_path"),
        "message_count": report.get("message_count"),
        "topic_count": report.get("topic_count"),
        "topics": topics,
        "missing_topics": missing_topics,
    }
    if report.get("schema_version") != "vision_nav_rosbag_export_validation_v1":
        return failed(
            "rosbag_export_validation",
            "ROS replay export validation report has an unexpected schema.",
            details,
        )
    if status == "passed" and not missing_topics and int(report.get("message_count") or 0) > 0:
        return passed("rosbag_export_validation", "ROS replay export validation passed.", details)
    if status == "passed":
        return failed(
            "rosbag_export_validation",
            "ROS replay export validation is missing required topics or messages.",
            details,
        )
    if status == "degraded":
        return degraded("rosbag_export_validation", "ROS replay export validation is degraded.", details)
    return failed("rosbag_export_validation", f"ROS replay export validation is {status or 'missing'}.", details)


def check_rosbag2_cli_review(
    path: str | Path | None,
    support_checks: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if path is not None:
        source = Path(path).expanduser()
        try:
            report = json.loads(source.read_text())
        except Exception as exc:
            return failed(
                "rosbag2_cli_review",
                f"Could not read native rosbag2 CLI review report: {exc}",
                {"path": str(source)},
            )
        return rosbag2_cli_review_check_from_report(report, source=str(source))

    support_check = support_checks.get("rosbag2_cli_reviews")
    if support_check and support_check.get("status") == "passed":
        return passed(
            "rosbag2_cli_review",
            "Native rosbag2 CLI review proof is present in the support bundle.",
            {"source": "support_bundle", **(support_check.get("details") or {})},
        )
    if support_check and support_check.get("status") == "degraded":
        return degraded(
            "rosbag2_cli_review",
            "Native rosbag2 CLI review proof in the support bundle is degraded.",
            {"source": "support_bundle", **(support_check.get("details") or {})},
        )
    if support_check:
        return failed(
            "rosbag2_cli_review",
            "Native rosbag2 CLI review proof in the support bundle failed.",
            {"source": "support_bundle", **(support_check.get("details") or {})},
        )
    return failed(
        "rosbag2_cli_review",
        "Native rosbag2 CLI review proof is required for final readiness.",
        {"required_format": "vision_nav_rosbag2_v1", "required_cli": "ros2 bag info"},
    )


def rosbag2_cli_review_check_from_report(report: dict[str, Any], *, source: str) -> dict[str, Any]:
    status = normalize_status(report.get("status"))
    cli = report.get("ros2_cli") if isinstance(report.get("ros2_cli"), dict) else {}
    validation = report.get("validation_report") if isinstance(report.get("validation_report"), dict) else {}
    details = {
        "source": source,
        "artifact_path": report.get("artifact_path"),
        "bag_dir": report.get("bag_dir"),
        "validation_status": report.get("validation_status"),
        "validation_format": report.get("validation_format"),
        "validation_message_count": validation.get("message_count"),
        "validation_topic_count": validation.get("topic_count"),
        "ros2_cli_status": cli.get("status"),
        "ros2_cli_exit_code": cli.get("exit_code"),
    }
    if report.get("schema_version") != "vision_nav_rosbag2_cli_review_v1":
        return failed(
            "rosbag2_cli_review",
            "Native rosbag2 CLI review report has an unexpected schema.",
            details,
        )
    if (
        status == "passed"
        and normalize_status(report.get("validation_status")) == "passed"
        and report.get("validation_format") == "vision_nav_rosbag2_v1"
        and normalize_status(cli.get("status")) == "passed"
        and cli.get("exit_code") == 0
    ):
        return passed("rosbag2_cli_review", "Native rosbag2 CLI review passed.", details)
    if status == "degraded":
        return degraded("rosbag2_cli_review", "Native rosbag2 CLI review is degraded.", details)
    return failed("rosbag2_cli_review", f"Native rosbag2 CLI review is {status or 'missing'}.", details)


def rosbag_report_topics(report: dict[str, Any]) -> list[str]:
    raw_topics = report.get("topics")
    if not isinstance(raw_topics, list):
        return []
    topics = []
    for item in raw_topics:
        if not isinstance(item, dict):
            continue
        name = item.get("name")
        if isinstance(name, str) and name:
            topics.append(name)
    return topics


def missing_rosbag_topics(topics: list[str]) -> list[str]:
    available = set(topics)
    return [topic for topic in ["/vision_nav/odometry", "/diagnostics"] if topic not in available]


def report_conditions(report: dict[str, Any]) -> set[str]:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    raw = (
        report.get("conditions")
        or report.get("covered_conditions")
        or summary.get("covered_conditions")
        or summary.get("tuned_conditions")
        or []
    )
    if not isinstance(raw, list):
        return set()
    return {str(value) for value in raw if str(value)}


def missing_required_conditions(covered: Any) -> list[str]:
    if not isinstance(covered, (list, tuple, set)):
        covered_set: set[str] = set()
    else:
        covered_set = {str(value) for value in covered if str(value)}
    return [condition for condition in REQUIRED_FIELD_CONDITIONS if condition not in covered_set]


def capture_metadata_proof_status(details: dict[str, Any]) -> str:
    value = details.get("capture_metadata_issue_count")
    if value is None:
        return "missing"
    try:
        return "passed" if int(value) == 0 else "failed"
    except (TypeError, ValueError):
        return "failed"


def print_human(report: dict[str, Any]) -> None:
    print("Autonomy readiness audit")
    print(f"Status: {report['status']}")
    for check in report["checks"]:
        print(f"- {check['name']}: {check['status']} - {check['message']}")
    if report.get("next_actions"):
        print("Next actions:")
        for action in report["next_actions"]:
            print(f"- {action.get('check')}: {action.get('desktop_action')} / {action.get('command')}")


def main() -> None:
    args = parse_args()
    report = evaluate_autonomy_readiness(
        research_doc_path=args.research_doc,
        implementation_plan_path=args.implementation_plan,
        support_bundle_path=args.support_bundle,
        px4_sitl_session_path=args.px4_sitl_session,
        px4_sitl_report_path=args.px4_sitl_report,
        field_evidence_report_path=args.field_evidence_report,
        field_collection_plan_path=args.field_collection_plan,
        feature_method_benchmark_report_path=args.feature_method_benchmark_report,
        threshold_tuning_report_path=args.threshold_tuning_report,
        rosbag_export_validation_path=args.rosbag_export_validation,
        rosbag2_cli_review_path=args.rosbag2_cli_review,
        evidence_workflow_report_path=args.evidence_workflow_report,
        evidence_workflow_validation_report_path=args.evidence_workflow_validation_report,
        evidence_workflow_log_archive_path=args.evidence_workflow_log_archive,
    )
    if args.output:
        destination = Path(args.output).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human(report)
    if report["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
