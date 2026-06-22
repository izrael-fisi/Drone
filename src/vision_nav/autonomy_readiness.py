from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from datetime import datetime, timezone
from pathlib import Path
import re
import shlex
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
from vision_nav.autonomy_evidence_workflow import REQUIRED_WORKFLOW_STEPS, validate_workflow_report
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
    "Track 6: ArduPilot Adapter Path",
]

EXTERNAL_PROOF_CHECKS = {
    "support_bundle_bench_readiness",
    "px4_receiver_proof",
    "field_collection_plan",
    "field_evidence_proof",
    "feature_method_benchmark",
    "threshold_tuning",
    "rosbag_export_validation",
    "rosbag2_cli_review",
    "evidence_workflow_validation",
}

FIELD_COLLECTION_BOOTSTRAP_COMMAND = (
    "./scripts/pi/create_field_evidence_template.sh && ./scripts/pi/create_field_collection_plan.sh"
)
GUIDED_EVIDENCE_WORKFLOW_COMMAND = "./scripts/pi/run_autonomy_evidence_workflow.sh"
SUPPORT_BUNDLE_COMMAND = "./scripts/pi/create_support_bundle.sh"
COMMAND_GROUP_DESKTOP_ACTIONS = {
    "guided_workflow": "Module Setup > Evidence Workflow",
    "prerequisite_fix": "Module Setup > PX4 Prereq Setup",
    "field_collection_capture": "Module Setup > Field Log Capture",
    "field_collection_metadata_update": "Module Setup > Field Evidence Case > Update Metadata",
    "field_collection_registration": "Module Setup > Field Evidence Case > Register",
}
BENCH_SUBCHECK_ACTION_ORDER = {
    "bundle_health": 10,
    "gnss_denied_plan": 20,
    "runtime_logs": 30,
    "runtime_status": 40,
    "replay_gates": 50,
    "px4_sitl_evidence": 60,
    "px4_params": 70,
    "field_evidence": 80,
    "feature_method_benchmarks": 90,
    "threshold_tuning": 100,
    "rosbag_export_validations": 110,
    "rosbag2_cli_reviews": 120,
    "ardupilot_params": 130,
}
BENCH_SUBCHECKS_DELEGATED_TO_LATER_PHASES = {
    "replay_gates",
    "field_evidence",
    "feature_method_benchmarks",
    "threshold_tuning",
    "rosbag_export_validations",
    "rosbag2_cli_reviews",
}
STRICT_SUPPORT_BUNDLE_INPUTS = [
    "terrain bundle health and GNSS-denied mission prep",
    "runtime terrain log and runtime_status.json snapshot",
    "PX4 ODOMETRY receiver evidence report",
    "PX4 external-vision parameter check report",
    "field evidence report covering all required real-world conditions",
    "feature-method benchmark report from real field logs",
    "threshold tuning report from real field logs",
    "ROS replay export validation report",
    "native rosbag2 CLI review report",
]
STRICT_SUPPORT_BUNDLE_ACTIONS = [
    {
        "label": "Prepare the GNSS-denied mission bundle.",
        "desktop_action": "Mission Planner > GNSS-Denied Prep, Build Bundle, Upload Bundle",
        "command": "./scripts/pi/validate_terrain_bundle.sh",
        "notes": "The support bundle must include terrain bundle health plus GNSS-denied mission metadata.",
    },
    {
        "label": "Capture a runtime terrain log and status snapshot.",
        "desktop_action": "Module Setup > Field Log Capture, then Runtime Status",
        "command": "VISION_NAV_COUNT=30 ./scripts/pi/run_terrain_nav_loop.sh",
        "notes": "Field Log Capture writes terrain_matches.jsonl and runtime_status.json for bench review.",
    },
    {
        "label": "Review PX4 receiver prerequisites.",
        "desktop_action": "Module Setup > PX4 Prereq Setup",
        "command": "./scripts/dev/setup_px4_sitl_prereqs.sh",
        "notes": "Run the dry-run setup helper before receiver capture so missing tmux or PX4 checkout setup is visible.",
    },
    {
        "label": "Capture PX4 ODOMETRY receiver proof.",
        "desktop_action": "Module Setup > PX4 SITL Receiver Capture",
        "command": "VISION_NAV_SITL_SMOKE_DIR=$PWD/px4-sitl-evidence ./scripts/dev/run_px4_sitl_external_vision_capture.sh",
        "notes": "Receiver evidence must prove the MAVLink ODOMETRY path, not only VISION_POSITION_ESTIMATE.",
    },
    {
        "label": "Check PX4 external-vision parameters.",
        "desktop_action": "Module Setup > PX4 parameter check",
        "command": "./scripts/pi/check_px4_params.sh",
        "notes": "Export PX4 parameters first; the checker records guidance without modifying the flight controller.",
    },
    {
        "label": "Create the field collection checklist.",
        "desktop_action": "Module Setup > Create Plan",
        "command": FIELD_COLLECTION_BOOTSTRAP_COMMAND,
        "notes": "This creates the field evidence template and condition-specific collection plan.",
    },
    {
        "label": "Load the next field condition into the registration form.",
        "desktop_action": "Module Setup > Load Next Field Condition",
        "notes": "Use the newest downloaded plan to prefill the next required condition before manual registration.",
    },
    {
        "label": "Collect and register real field evidence.",
        "desktop_action": "Module Setup > Evidence Workflow",
        "command": GUIDED_EVIDENCE_WORKFLOW_COMMAND,
        "notes": "The workflow auto-loads the next field condition and preserves partial artifacts.",
    },
    {
        "label": "Benchmark feature methods on field logs.",
        "desktop_action": "Module Setup > Feature Benchmark",
        "command": "./scripts/pi/run_feature_method_benchmark.sh",
        "blocked_by": "field_dataset",
        "notes": "Run after real field logs exist.",
    },
    {
        "label": "Tune replay gates against field logs.",
        "desktop_action": "Module Setup > Threshold Tuning",
        "command": "./scripts/pi/run_threshold_tuning_report.sh",
        "blocked_by": "field_dataset",
        "notes": "Run after all required field conditions have registered logs.",
    },
    {
        "label": "Export and validate the ROS replay artifact.",
        "desktop_action": "Module Setup > ROS Bag Validation",
        "command": "./scripts/pi/run_rosbag_export_validation.sh",
        "blocked_by": "field_dataset",
        "notes": "Run after field replay logs exist so final readiness has odometry and diagnostics replay proof.",
    },
    {
        "label": "Review the native rosbag2 export with ROS 2 CLI tools.",
        "desktop_action": "Module Setup > Native rosbag2 Review, then Local Readiness Re-Audit",
        "command": "./scripts/dev/run_rosbag2_cli_review.sh",
        "blocked_by": "field_dataset",
        "notes": "Run on a sourced ROS 2 workstation after native rosbag2 export is available.",
    },
    {
        "label": "Create or refresh the support bundle.",
        "desktop_action": "Module Setup > Bench Report",
        "command": SUPPORT_BUNDLE_COMMAND,
        "notes": "Run after the relevant bench, field, threshold, and ROS proof artifacts exist.",
    },
]

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
        "checks": ["px4_receiver_proof", "support_bundle_bench_readiness"],
        "depends_on": ["plan_source"],
        "notes": "Build and upload the terrain bundle, run the runtime, capture PX4 ODOMETRY receiver proof, export parameters, then create the support bundle.",
    },
    {
        "id": "field_dataset",
        "title": "Collect real field replay coverage",
        "checks": ["field_collection_plan", "field_evidence_proof"],
        "depends_on": ["plan_source"],
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
        "depends_on": ["field_dataset"],
        "notes": "After real terrain replay logs exist, export the replay artifact, then review the native rosbag2 export on a sourced ROS 2 workstation.",
    },
    {
        "id": "final_audit",
        "title": "Run final autonomy readiness audit",
        "checks": [
            "research_doc",
            "implementation_plan",
            "px4_receiver_proof",
            "support_bundle_bench_readiness",
            "field_collection_plan",
            "field_evidence_proof",
            "feature_method_benchmark",
            "threshold_tuning",
            "rosbag_export_validation",
            "rosbag2_cli_review",
            "evidence_workflow_validation",
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
        "--px4-sitl-prereqs",
        help="Optional px4_sitl_capture_prereqs.json diagnostic report from the PX4 receiver-capture wrapper.",
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
    px4_sitl_prereq_path: str | Path | None = None,
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
    field_next_condition = field_collection_next_condition_from_path(field_collection_plan_path)
    support_report = evaluate_support_bundle(
        support_bundle_path,
        require_px4_evidence=px4_sitl_session_path is None and px4_sitl_report_path is None,
        require_feature_method_benchmark=feature_method_benchmark_report_path is None,
        require_field_evidence=field_evidence_report_path is None,
        field_next_condition=field_next_condition,
    )
    support_checks = support_checks_by_name(support_report)
    support_manifest = support_report.get("manifest") if isinstance(support_report.get("manifest"), dict) else None
    checks = [
        check_research_doc_source(research_doc_path),
        check_implementation_plan_source(implementation_plan_path),
        support_report["check"],
        check_px4_receiver_proof(px4_sitl_session_path, px4_sitl_report_path, support_checks),
        check_field_collection_plan(field_collection_plan_path, support_manifest=support_manifest),
        check_field_evidence_proof(field_evidence_report_path, support_checks),
        check_feature_method_proof(feature_method_benchmark_report_path, support_checks),
        check_threshold_tuning(threshold_tuning_report_path, support_manifest=support_manifest),
        check_rosbag_export_validation(rosbag_export_validation_path, support_manifest=support_manifest),
        check_rosbag2_cli_review(rosbag2_cli_review_path, support_checks),
        check_evidence_workflow_validation(
            evidence_workflow_report_path,
            evidence_workflow_validation_report_path,
            evidence_workflow_log_archive_path,
        ),
    ]
    status = readiness_status(checks)
    next_actions = next_actions_for_checks(
        checks,
        field_next_condition=field_next_condition,
    )
    plan_snapshot = build_plan_snapshot(
        research_doc_path=research_doc_path,
        implementation_plan_path=implementation_plan_path,
    )
    field_collection_markdown_path = None
    if field_collection_plan_path:
        candidate = Path(field_collection_plan_path).expanduser().with_suffix(".md")
        if candidate.exists():
            field_collection_markdown_path = candidate
    resolved_px4_sitl_prereq_path = resolve_px4_sitl_prereq_path(
        px4_sitl_prereq_path,
        px4_sitl_session_path=px4_sitl_session_path,
        px4_sitl_report_path=px4_sitl_report_path,
    )
    px4_sitl_prereq_diagnostic = summarize_px4_sitl_prereq_diagnostic(
        resolved_px4_sitl_prereq_path,
        explicit=px4_sitl_prereq_path is not None,
    )
    inputs = {
        "research_doc": str(Path(research_doc_path).expanduser()),
        "implementation_plan": str(Path(implementation_plan_path).expanduser()),
        "support_bundle": str(Path(support_bundle_path).expanduser()) if support_bundle_path else None,
        "px4_sitl_session": str(Path(px4_sitl_session_path).expanduser()) if px4_sitl_session_path else None,
        "px4_sitl_report": str(Path(px4_sitl_report_path).expanduser()) if px4_sitl_report_path else None,
        "px4_sitl_prereqs": (
            str(resolved_px4_sitl_prereq_path) if resolved_px4_sitl_prereq_path is not None else None
        ),
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
    diagnostics = build_diagnostics(px4_sitl_prereq_diagnostic)
    evidence_manifest = build_evidence_manifest(status, checks, inputs, next_actions, diagnostics=diagnostics)
    proof_runbook = build_proof_runbook(checks, next_actions, evidence_manifest)
    report = {
        "metadata": build_audit_metadata(),
        "status": status,
        "checks": checks,
        "next_actions": next_actions,
        "command_bundle": build_command_bundle(
            next_actions,
            field_collection_plan_path=field_collection_plan_path,
            proof_runbook=proof_runbook,
            diagnostics=diagnostics,
        ),
        "summary": {
            "failed": sum(1 for check in checks if check["status"] == "failed"),
            "degraded": sum(1 for check in checks if check["status"] == "degraded"),
            "passed": sum(1 for check in checks if check["status"] == "passed"),
        },
        "inputs": inputs,
        "plan_snapshot": plan_snapshot,
        "diagnostics": diagnostics,
        "evidence_manifest": evidence_manifest,
        "proof_runbook": proof_runbook,
    }
    if support_report.get("report") is not None:
        report["bench_readiness"] = support_report["report"]
    return report


def build_audit_metadata(*, repo_dir: str | Path | None = None) -> dict[str, Any]:
    return {
        "schema_version": "vision_nav_autonomy_readiness_audit_metadata_v1",
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "repo": build_repo_metadata(repo_dir or Path.cwd()),
    }


def build_repo_metadata(repo_dir: str | Path) -> dict[str, Any]:
    root = Path(repo_dir).expanduser()
    inside = git_output(root, ["rev-parse", "--is-inside-work-tree"])
    if inside != "true":
        return {
            "detected": False,
            "path": str(root),
        }
    top_level = git_output(root, ["rev-parse", "--show-toplevel"])
    git_root = Path(top_level).expanduser() if top_level else root
    branch = git_output(git_root, ["branch", "--show-current"])
    status = git_output(git_root, ["status", "--porcelain"])
    remote = git_output(git_root, ["remote", "get-url", "origin"])
    return {
        "detected": True,
        "root": str(git_root),
        "branch": branch or git_output(git_root, ["rev-parse", "--abbrev-ref", "HEAD"]),
        "commit": git_output(git_root, ["rev-parse", "HEAD"]),
        "dirty": bool(status),
        "remote": remote,
    }


def git_output(repo_dir: Path, args: list[str]) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_dir), *args],
            capture_output=True,
            check=False,
            text=True,
            timeout=2,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    return result.stdout.strip() or None


def build_command_bundle(
    next_actions: list[dict[str, Any]],
    *,
    field_collection_plan_path: str | Path | None = None,
    proof_runbook: dict[str, Any] | None = None,
    diagnostics: dict[str, Any] | None = None,
) -> dict[str, Any]:
    guided_workflow_commands = [GUIDED_EVIDENCE_WORKFLOW_COMMAND] if next_actions else []
    raw_next_action_commands = unique_strings(
        action.get("command")
        for action in next_actions
        if isinstance(action, dict)
    )
    immediate_next_action_commands, blocked_follow_up_commands = proof_runbook_command_groups(
        proof_runbook,
        fallback_commands=raw_next_action_commands,
    )
    next_action_commands = unique_strings(
        [
            *immediate_next_action_commands,
            *blocked_follow_up_commands,
            *raw_next_action_commands,
        ]
    )
    field_collection_capture_commands = field_collection_commands(field_collection_plan_path, "capture_command")
    field_collection_metadata_update_commands = field_collection_commands(
        field_collection_plan_path,
        "metadata_update_command",
    )
    field_collection_registration_commands = field_collection_commands(field_collection_plan_path, "register_command")
    prerequisite_fix_commands = diagnostic_fix_commands(diagnostics)
    command_groups = {
        "guided_workflow": guided_workflow_commands,
        "prerequisite_fix": prerequisite_fix_commands,
        "next_action": next_action_commands,
        "immediate_next_action": immediate_next_action_commands,
        "blocked_follow_up": blocked_follow_up_commands,
        "field_collection_capture": field_collection_capture_commands,
        "field_collection_metadata_update": field_collection_metadata_update_commands,
        "field_collection_registration": field_collection_registration_commands,
    }
    return {
        "guided_workflow_commands": guided_workflow_commands,
        "prerequisite_fix_commands": prerequisite_fix_commands,
        "next_action_commands": next_action_commands,
        "immediate_next_action_commands": immediate_next_action_commands,
        "blocked_follow_up_commands": blocked_follow_up_commands,
        "field_collection_capture_commands": field_collection_capture_commands,
        "field_collection_metadata_update_commands": field_collection_metadata_update_commands,
        "field_collection_registration_commands": field_collection_registration_commands,
        "command_items": command_bundle_items(command_groups, next_actions, proof_runbook),
        "command_count": len(
            unique_strings(
                [
                    *guided_workflow_commands,
                    *prerequisite_fix_commands,
                    *next_action_commands,
                    *field_collection_capture_commands,
                    *field_collection_metadata_update_commands,
                    *field_collection_registration_commands,
                ]
            )
        ),
    }


def command_bundle_items(
    command_groups: dict[str, list[str]],
    next_actions: list[dict[str, Any]],
    proof_runbook: dict[str, Any] | None,
) -> list[dict[str, str]]:
    app_hints = command_app_hints(next_actions, proof_runbook)
    items: list[dict[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for group, commands in command_groups.items():
        for command in commands:
            if not isinstance(command, str) or not command:
                continue
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


def command_app_hints(
    next_actions: list[dict[str, Any]],
    proof_runbook: dict[str, Any] | None,
) -> dict[str, str]:
    hints: dict[str, str] = {}
    for action in next_actions:
        if isinstance(action, dict):
            add_command_app_hint(hints, action.get("command"), action.get("desktop_action"))
    if isinstance(proof_runbook, dict):
        for phase in json_dict_list(proof_runbook.get("phases")):
            for action in json_dict_list(phase.get("actions")):
                add_command_app_hint(hints, action.get("command"), action.get("desktop_action"))
    return hints


def add_command_app_hint(hints: dict[str, str], command: Any, desktop_action: Any) -> None:
    if not isinstance(command, str) or not command.strip():
        return
    if not isinstance(desktop_action, str) or not desktop_action.strip():
        return
    hints.setdefault(command, desktop_action)


def diagnostic_fix_commands(diagnostics: dict[str, Any] | None) -> list[str]:
    if not isinstance(diagnostics, dict):
        return []
    px4_prereqs = diagnostics.get("px4_sitl_prereqs")
    if not isinstance(px4_prereqs, dict):
        return []
    return unique_strings(
        item.get("command")
        for item in px4_prereqs.get("fix_commands") or []
        if isinstance(item, dict)
    )


def proof_runbook_command_groups(
    proof_runbook: dict[str, Any] | None,
    *,
    fallback_commands: list[str],
) -> tuple[list[str], list[str]]:
    if not isinstance(proof_runbook, dict):
        return fallback_commands, []
    phases = proof_runbook.get("phases")
    if not isinstance(phases, list):
        return fallback_commands, []
    immediate_commands: list[str] = []
    blocked_commands: list[str] = []
    for phase in phases:
        if not isinstance(phase, dict):
            continue
        phase_commands = json_string_list(phase.get("commands"))
        phase_status = str(phase.get("status") or "")
        if phase_status == "action_required":
            immediate_commands.extend(phase_commands)
        elif phase_status == "blocked" and phase.get("id") != "final_audit":
            blocked_commands.extend(phase_commands)
    immediate_next_action_commands = defer_support_bundle_command(unique_strings(immediate_commands))
    blocked_follow_up_commands = unique_strings(
        command
        for command in blocked_commands
        if command not in immediate_next_action_commands
    )
    if not immediate_next_action_commands and fallback_commands:
        immediate_next_action_commands = fallback_commands
    return immediate_next_action_commands, blocked_follow_up_commands


def defer_support_bundle_command(commands: list[str]) -> list[str]:
    support_commands = [command for command in commands if command == SUPPORT_BUNDLE_COMMAND]
    other_commands = [command for command in commands if command != SUPPORT_BUNDLE_COMMAND]
    return [*other_commands, *support_commands]


def json_string_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, str) and item]


def json_dict_list(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def field_collection_commands(path: str | Path | None, key: str) -> list[str]:
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
        item.get(key)
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
    *,
    diagnostics: dict[str, Any] | None = None,
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
        "diagnostic_items": evidence_diagnostic_items(diagnostics or {}, inputs),
    }


def evidence_diagnostic_items(diagnostics: dict[str, Any], inputs: dict[str, Any]) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    px4_prereqs = diagnostics.get("px4_sitl_prereqs") if isinstance(diagnostics, dict) else None
    if isinstance(px4_prereqs, dict) and px4_prereqs.get("status") != "not_provided":
        status = str(px4_prereqs.get("status") or "unknown")
        if status == "passed":
            message = "PX4 SITL capture prerequisites were available when the capture wrapper ran."
        elif status == "failed":
            message = "PX4 SITL capture prerequisites were not ready; this is diagnostic only and does not satisfy receiver proof."
        else:
            message = "PX4 SITL capture prerequisite report was recorded as a diagnostic artifact."
        items.append(
            {
                "name": "px4_sitl_prereqs",
                "status": status,
                "message": message,
                "source": str(px4_prereqs.get("path") or inputs.get("px4_sitl_prereqs") or ""),
                "requires_external_proof": False,
            }
        )
    return items


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
    seen: set[tuple[str, str]] = set()
    for name in check_names:
        matching_actions = []
        for action in next_actions:
            if not isinstance(action, dict):
                continue
            action_check = str(action.get("check") or "")
            if not action_check:
                continue
            if action_check != name and not action_check.startswith(f"{name}."):
                continue
            if (
                name == "support_bundle_bench_readiness"
                and str(action.get("bench_subcheck") or "") in BENCH_SUBCHECKS_DELEGATED_TO_LATER_PHASES
            ):
                continue
            matching_actions.append(action)
        for action in sorted(matching_actions, key=proof_runbook_action_order):
            action_check = str(action.get("check") or "")
            key = (action_check, str(action.get("command") or ""))
            if key in seen:
                continue
            seen.add(key)
            actions.append(compact_proof_runbook_action(action))
    return actions


def proof_runbook_action_order(action: dict[str, Any]) -> tuple[int, str]:
    action_check = str(action.get("check") or "")
    if action_check.startswith("support_bundle_bench_readiness."):
        subcheck = str(action.get("bench_subcheck") or "")
        return (BENCH_SUBCHECK_ACTION_ORDER.get(subcheck, 1000), action_check)
    if action.get("command") == SUPPORT_BUNDLE_COMMAND:
        return (2000, action_check)
    return (1, action_check)


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
        "field_condition",
        "field_label",
        "field_expected",
        "field_capture_output_dir",
        "field_source_log",
        "field_runtime_status_path",
        "field_bundle",
        "field_metadata_update_command",
        "field_register_command",
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


def compact_bench_evidence_actions(raw: Any) -> list[dict[str, str]]:
    if not isinstance(raw, list):
        return []
    actions: list[dict[str, str]] = []
    for item in raw:
        if not isinstance(item, dict):
            continue
        compact: dict[str, str] = {}
        for key in (
            "label",
            "desktop_action",
            "command",
            "blocked_by",
            "notes",
            "field_condition",
            "field_label",
            "field_expected",
            "field_capture_output_dir",
            "field_source_log",
            "field_runtime_status_path",
            "field_bundle",
            "field_metadata_update_command",
        ):
            value = item.get(key)
            if isinstance(value, str) and value:
                compact[key] = value
        if compact:
            actions.append(compact)
    return actions


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
        expected_inputs = details.get("expected_bench_inputs")
        if isinstance(expected_inputs, list):
            item["expected_bench_inputs"] = [str(value) for value in expected_inputs if str(value)]
        if details.get("support_bundle_command"):
            item["support_bundle_command"] = str(details["support_bundle_command"])
        actions = compact_bench_evidence_actions(details.get("bench_evidence_actions"))
        if actions:
            item["bench_evidence_actions"] = actions
    return item


def evidence_source_for_check(name: str, details: dict[str, Any], inputs: dict[str, Any]) -> str | None:
    input_keys = {
        "research_doc": "research_doc",
        "implementation_plan": "implementation_plan",
        "support_bundle_bench_readiness": "support_bundle",
        "px4_receiver_proof": "px4_sitl_report",
        "field_collection_plan": "field_collection_plan",
        "field_evidence_proof": "field_evidence_report",
        "feature_method_benchmark": "feature_method_benchmark_report",
        "threshold_tuning": "threshold_tuning_report",
        "rosbag_export_validation": "rosbag_export_validation",
        "rosbag2_cli_review": "rosbag2_cli_review",
        "evidence_workflow_validation": "evidence_workflow_validation_report",
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
    if item.get("expected_bench_inputs"):
        summary["expected_bench_inputs"] = item["expected_bench_inputs"]
    if item.get("support_bundle_command"):
        summary["support_bundle_command"] = item["support_bundle_command"]
    if item.get("bench_evidence_actions"):
        summary["bench_evidence_actions"] = item["bench_evidence_actions"]
    return summary


def next_actions_for_checks(
    checks: list[dict[str, Any]],
    *,
    field_next_condition: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    actions = {
        "research_doc": {
            "title": "Restore the autonomy ground-control research source document.",
            "desktop_action": "Repo docs > autonomy-ground-control-research.md",
            "command": "sed -n '1,260p' docs/autonomy-ground-control-research.md",
            "notes": "The final audit requires the research document sections for references, architecture, and near-term integration plan.",
        },
        "implementation_plan": {
            "title": "Update the autonomy ground-control implementation plan.",
            "desktop_action": "Repo docs > autonomy-ground-control-implementation-plan.md",
            "command": "sed -n '1,900p' docs/autonomy-ground-control-implementation-plan.md",
            "notes": "The implementation plan must include all delivery tracks, executable task lists, acceptance checks, and execution order.",
        },
        "support_bundle_bench_readiness": {
            "title": "Create a support bundle with bench evidence.",
            "desktop_action": "Module Setup > Bench Report",
            "command": "./scripts/pi/create_support_bundle.sh",
            "notes": "Run after the terrain bundle, runtime logs, PX4 parameter export, and receiver proof are available.",
        },
        "px4_receiver_proof": {
            "title": "Capture PX4 external-vision receiver proof.",
            "desktop_action": "Module Setup > PX4 SITL Receiver Capture, then Local Readiness Re-Audit",
            "command": "VISION_NAV_SITL_SMOKE_DIR=$PWD/px4-sitl-evidence ./scripts/dev/run_px4_sitl_external_vision_capture.sh",
            "notes": "The final report must show the MAVLink ODOMETRY path arriving as fresh vehicle_visual_odometry samples with covariance/variance fields.",
        },
        "field_collection_plan": {
            "title": "Create or refresh the field collection checklist.",
            "desktop_action": "Module Setup > Create Plan",
            "command": FIELD_COLLECTION_BOOTSTRAP_COMMAND,
            "notes": "Create or refresh the field evidence template plus field collection plan before capturing condition-specific terrain logs.",
        },
        "field_evidence_proof": {
            "title": "Run the guided evidence workflow for field replay proof.",
            "desktop_action": "Module Setup > Evidence Workflow",
            "command": GUIDED_EVIDENCE_WORKFLOW_COMMAND,
            "notes": "The guided workflow creates or loads the field collection plan, captures the next pending condition, skips registration until capture metadata is complete, and then registers real logs for every required condition.",
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
            "desktop_action": "Module Setup > Native rosbag2 Review, then Local Readiness Re-Audit",
            "command": "./scripts/dev/run_rosbag2_cli_review.sh",
            "notes": "Run on a sourced ROS 2 workstation after native rosbag2 export. The review must include a passing validator result and successful ros2 bag info output.",
        },
        "evidence_workflow_validation": {
            "title": "Run the guided evidence workflow and validate its report.",
            "desktop_action": "Module Setup > Evidence Workflow",
            "command": GUIDED_EVIDENCE_WORKFLOW_COMMAND,
            "notes": "The final readiness gate requires a passed workflow validation report, including every ordered step and final proof marker.",
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
            next_actions.extend(
                next_actions_for_bench_subchecks(
                    details,
                    field_next_condition=field_next_condition,
                )
            )
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


def next_actions_for_bench_subchecks(
    details: dict[str, Any],
    *,
    field_next_condition: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
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
            "command": "./scripts/pi/validate_terrain_bundle.sh",
            "notes": "The support bundle must include a Mission Planner export with satellite source disabled, map reset, home reset, heading, and estimator readiness all marked complete.",
        },
        "runtime_logs": {
            "title": "Run the terrain runtime before creating the bench report.",
            "desktop_action": "Module Setup > Field Log Capture, then Bench Report",
            "command": "VISION_NAV_COUNT=30 ./scripts/pi/run_terrain_nav_loop.sh",
            "notes": "Create the support bundle after Field Log Capture has produced terrain_matches.jsonl.",
        },
        "runtime_status": {
            "title": "Fetch a fresh runtime status snapshot.",
            "desktop_action": "Module Setup > Field Log Capture, then Runtime Status and Bench Report",
            "command": "VISION_NAV_COUNT=30 ./scripts/pi/run_terrain_nav_loop.sh && ./scripts/pi/read_runtime_status.sh",
            "notes": "Field Log Capture writes runtime_status.json beside the terrain log; Runtime Status verifies the latest snapshot before the bench report.",
        },
        "replay_gates": {
            "title": "Run the guided field workflow for replay-gate evidence.",
            "desktop_action": "Module Setup > Load Next Field Condition, then Evidence Workflow",
            "command": GUIDED_EVIDENCE_WORKFLOW_COMMAND,
            "notes": "The field workflow captures, validates, and registers condition-specific logs. Generated field plans carry the exact registration commands; support bundles auto-ingest replay-gate reports from the field manifest.",
        },
        "px4_sitl_evidence": {
            "title": "Capture PX4 external-vision receiver proof.",
            "desktop_action": "Module Setup > PX4 SITL Receiver Capture, then Bench Report",
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
            "title": "Run the guided evidence workflow for field replay proof.",
            "desktop_action": "Module Setup > Evidence Workflow",
            "command": GUIDED_EVIDENCE_WORKFLOW_COMMAND,
            "notes": "Field evidence must cover all required terrain conditions with real captured logs; the workflow auto-loads the next pending condition and preserves partial artifacts.",
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
            "desktop_action": "Module Setup > Native rosbag2 Review, then Bench Report",
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
        action = {
            "check": f"support_bundle_bench_readiness.{subcheck['name']}",
            "status": subcheck["status"],
            "bench_subcheck": subcheck["name"],
            "bench_message": subcheck["message"],
            **spec,
        }
        if subcheck["name"] in {"bundle_health", "gnss_denied_plan"}:
            enrich_action_with_field_bundle(action, field_next_condition)
        elif subcheck["name"] == "runtime_logs":
            enrich_action_with_field_capture(action, field_next_condition)
        elif subcheck["name"] == "runtime_status":
            enrich_action_with_field_capture(
                action,
                field_next_condition,
                append_runtime_status_read=True,
            )
        next_actions.append(action)
    return next_actions


def field_collection_next_condition_from_path(path: str | Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    source = Path(path).expanduser()
    if not source.is_file():
        return None
    try:
        plan = json.loads(source.read_text())
    except Exception:
        return None
    if not isinstance(plan, dict):
        return None
    return field_collection_next_condition(plan)


def enrich_action_with_field_capture(
    action: dict[str, Any],
    condition: dict[str, Any] | None,
    *,
    append_runtime_status_read: bool = False,
) -> None:
    if not condition:
        return
    capture_command = condition.get("capture_command")
    if isinstance(capture_command, str) and capture_command.strip():
        if append_runtime_status_read:
            action["command"] = f"{capture_command} && ./scripts/pi/read_runtime_status.sh"
        else:
            action["command"] = capture_command

    field_mappings = {
        "field_condition": "condition",
        "field_label": "label",
        "field_expected": "expected",
        "field_capture_output_dir": "capture_output_dir",
        "field_source_log": "source_log",
        "field_runtime_status_path": "runtime_status_path",
        "field_bundle": "bundle",
        "field_metadata_update_command": "metadata_update_command",
        "field_register_command": "register_command",
    }
    for target_key, source_key in field_mappings.items():
        value = condition.get(source_key)
        if isinstance(value, str) and value.strip():
            action[target_key] = value

    detail_lines = []
    label = condition.get("label") or condition.get("condition")
    condition_name = condition.get("condition")
    if label and condition_name:
        detail_lines.append(f"Next pending field condition: {label} ({condition_name}).")
    elif condition_name:
        detail_lines.append(f"Next pending field condition: {condition_name}.")
    if condition.get("source_log"):
        detail_lines.append(f"Expected log: {condition['source_log']}.")
    if condition.get("capture_output_dir"):
        detail_lines.append(f"Output: {condition['capture_output_dir']}.")
    if condition.get("runtime_status_path"):
        detail_lines.append(f"Runtime status: {condition['runtime_status_path']}.")
    if detail_lines:
        action["notes"] = " ".join([str(action.get("notes") or ""), *detail_lines]).strip()


def enrich_action_with_field_bundle(action: dict[str, Any], condition: dict[str, Any] | None) -> None:
    if not condition:
        return
    bundle = condition.get("bundle")
    if not isinstance(bundle, str) or not bundle.strip():
        return
    action["command"] = shell_command(
        {"VISION_NAV_BUNDLE": bundle},
        "./scripts/pi/validate_terrain_bundle.sh",
    )
    action["field_bundle"] = bundle
    action["notes"] = " ".join(
        [
            str(action.get("notes") or ""),
            f"Selected field-plan bundle: {bundle}.",
        ]
    ).strip()


def strict_support_bundle_actions(field_next_condition: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    actions = [dict(action) for action in STRICT_SUPPORT_BUNDLE_ACTIONS]
    for action in actions:
        if action.get("desktop_action") == "Module Setup > Field Log Capture, then Runtime Status":
            enrich_action_with_field_capture(
                action,
                field_next_condition,
                append_runtime_status_read=True,
            )
        elif action.get("desktop_action") == "Mission Planner > GNSS-Denied Prep, Build Bundle, Upload Bundle":
            enrich_action_with_field_bundle(action, field_next_condition)
    return actions


def next_action_missing_conditions(name: str, details: dict[str, Any]) -> list[str]:
    raw = details.get("missing_conditions")
    if raw is None and name in {"field_collection_plan", "field_evidence_proof", "threshold_tuning"}:
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
    field_next_condition: dict[str, Any] | None = None,
) -> dict[str, Any]:
    bench_evidence_actions = strict_support_bundle_actions(field_next_condition)
    if path is None:
        details = {
            "expected_bench_inputs": STRICT_SUPPORT_BUNDLE_INPUTS,
            "bench_evidence_actions": bench_evidence_actions,
            "support_bundle_command": SUPPORT_BUNDLE_COMMAND,
        }
        return {
            "check": failed(
                "support_bundle_bench_readiness",
                "Strict autonomy readiness requires a support bundle with bench evidence.",
                details,
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
    if status != "passed":
        details.update(
            {
                "expected_bench_inputs": STRICT_SUPPORT_BUNDLE_INPUTS,
                "bench_evidence_actions": bench_evidence_actions,
                "support_bundle_command": SUPPORT_BUNDLE_COMMAND,
            }
        )
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


def check_research_doc_source(path: str | Path) -> dict[str, Any]:
    summary = summarize_research_doc(path)
    details = {
        "path": summary["path"],
        "source_sha256": summary["source_sha256"],
        "source_size_bytes": summary["source_size_bytes"],
        "required_marker_count": summary["required_marker_count"],
        "missing_markers": summary["missing_markers"],
        "highest_value_reference_count": summary["highest_value_reference_count"],
        "fit_criteria_count": summary["fit_criteria_count"],
        "architecture_section_count": summary["architecture_section_count"],
        "near_term_item_count": summary["near_term_item_count"],
        "avoid_choice_count": summary["avoid_choice_count"],
    }
    if not summary["exists"]:
        return failed("research_doc", f"Missing {summary['path']}.", details)
    if summary["missing_markers"]:
        return failed("research_doc", f"{summary['path']} is missing required research sections.", details)
    if summary["highest_value_reference_count"] <= 0:
        return failed("research_doc", "Research doc is missing highest-value reference rows.", details)
    if summary["fit_criteria_count"] <= 0:
        return failed("research_doc", "Research doc is missing fit criteria.", details)
    if summary["architecture_section_count"] <= 0:
        return failed("research_doc", "Research doc is missing architecture recommendation sections.", details)
    if summary["near_term_item_count"] <= 0:
        return failed("research_doc", "Research doc is missing near-term integration plan items.", details)
    if summary["avoid_choice_count"] <= 0:
        return failed("research_doc", "Research doc is missing implementation choices to avoid.", details)
    return passed(
        "research_doc",
        "Autonomy research doc covers references, fit criteria, architecture, near-term plan, and avoid choices.",
        details,
    )


def check_implementation_plan_source(path: str | Path) -> dict[str, Any]:
    summary = summarize_implementation_plan(path)
    details = {
        "path": summary["path"],
        "source_sha256": summary["source_sha256"],
        "source_size_bytes": summary["source_size_bytes"],
        "required_marker_count": summary["required_marker_count"],
        "missing_markers": summary["missing_markers"],
        "track_count": summary["track_count"],
        "task_count": summary["task_count"],
        "next_task_count": summary["next_task_count"],
        "acceptance_check_count": summary["acceptance_check_count"],
        "execution_order_count": summary["execution_order_count"],
    }
    if not summary["exists"]:
        return failed("implementation_plan", f"Missing {summary['path']}.", details)
    if summary["missing_markers"]:
        return failed("implementation_plan", f"{summary['path']} is missing required delivery tracks.", details)
    if summary["track_count"] < summary["required_marker_count"]:
        return failed("implementation_plan", "Implementation plan track count is incomplete.", details)
    if summary["task_count"] + summary["next_task_count"] <= 0:
        return failed("implementation_plan", "Implementation plan is missing executable task lists.", details)
    if summary["acceptance_check_count"] <= 0:
        return failed("implementation_plan", "Implementation plan is missing acceptance checks.", details)
    if summary["execution_order_count"] <= 0:
        return failed("implementation_plan", "Implementation plan is missing execution order.", details)
    return passed(
        "implementation_plan",
        "Implementation plan covers delivery tracks, tasks, acceptance checks, and execution order.",
        details,
    )


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
        "source_sha256": sha256_file(source),
        "source_size_bytes": file_size_bytes(source),
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
        "source_sha256": sha256_file(source),
        "source_size_bytes": file_size_bytes(source),
        "required_marker_count": len(IMPLEMENTATION_PLAN_MARKERS),
        "missing_markers": missing_markers,
        "track_count": len(re.findall(r"^### Track \d+:", text, flags=re.MULTILINE)),
        "done_count": count_status_lines(text, "Done"),
        "in_progress_count": count_status_lines(text, "In progress"),
        "task_count": count_plan_list_items_under_heading(text, "Tasks"),
        "next_task_count": count_plan_list_items_under_heading(text, "Next tasks"),
        "acceptance_check_count": count_plan_list_items_under_heading(
            text,
            "Acceptance checks",
            include_bullets=True,
        ),
        "execution_order_count": count_numbered_lines(section_text(text, "Execution Order")),
    }


def safe_read_text(path: Path) -> str:
    if not path.is_file():
        return ""
    return path.read_text(errors="replace")


def sha256_file(path: Path) -> str | None:
    if not path.is_file():
        return None
    hasher = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def file_size_bytes(path: Path) -> int | None:
    if not path.is_file():
        return None
    return path.stat().st_size


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


def count_plan_list_items_under_heading(text: str, heading: str, *, include_bullets: bool = False) -> int:
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
        if include_bullets:
            total += count_bullet_lines(block)
        search_from = start
    return total


def resolve_px4_sitl_prereq_path(
    path: str | Path | None,
    *,
    px4_sitl_session_path: str | Path | None = None,
    px4_sitl_report_path: str | Path | None = None,
) -> Path | None:
    if path is not None:
        return Path(path).expanduser()
    candidates: list[Path] = []
    if px4_sitl_session_path is not None:
        session = Path(px4_sitl_session_path).expanduser()
        if session.is_dir():
            candidates.append(session / "px4_sitl_capture_prereqs.json")
        else:
            candidates.append(session.parent / "px4_sitl_capture_prereqs.json")
    if px4_sitl_report_path is not None:
        candidates.append(Path(px4_sitl_report_path).expanduser().parent / "px4_sitl_capture_prereqs.json")
    for candidate in candidates:
        if candidate.is_file():
            return candidate
    return None


def summarize_px4_sitl_prereq_diagnostic(path: Path | None, *, explicit: bool = False) -> dict[str, Any]:
    if path is None:
        return {"status": "not_provided"}
    if not path.exists():
        if not explicit:
            return {"status": "not_provided", "path": str(path)}
        return {
            "status": "failed",
            "path": str(path),
            "issues": [{"severity": "error", "message": "PX4 SITL capture prerequisite report is missing."}],
        }
    try:
        report = json.loads(path.read_text())
        if not isinstance(report, dict):
            raise ValueError("PX4 prerequisite report root must be an object")
    except Exception as exc:
        return {
            "status": "failed",
            "path": str(path),
            "issues": [{"severity": "error", "message": f"PX4 prerequisite report could not be parsed: {exc}"}],
        }
    checks = []
    failed_checks = []
    for check in report.get("checks") or []:
        if not isinstance(check, dict):
            continue
        item = {
            "name": check.get("name"),
            "status": check.get("status"),
            "message": check.get("message"),
        }
        checks.append(item)
        if normalize_status(check.get("status")) == "failed":
            failed_checks.append(item)
    fix_commands = []
    for command in report.get("fix_commands") or []:
        if not isinstance(command, dict):
            continue
        item = {
            "label": command.get("label"),
            "command": command.get("command"),
            "condition": command.get("condition"),
        }
        if item["command"]:
            fix_commands.append(item)
    return {
        "status": normalize_status(report.get("status")) or "unknown",
        "path": str(path),
        "schema_version": report.get("schema_version"),
        "generated_at": report.get("generated_at"),
        "session_dir": report.get("session_dir"),
        "px4_dir": report.get("px4_dir"),
        "px4_target": report.get("px4_target"),
        "tmux_session": report.get("tmux_session"),
        "receiver_report": report.get("receiver_report"),
        "checks": checks,
        "failed_checks": failed_checks,
        "next_actions": [str(action) for action in report.get("next_actions") or [] if str(action)],
        "fix_commands": fix_commands,
        "issues": [],
    }


def build_diagnostics(px4_sitl_prereq_diagnostic: dict[str, Any]) -> dict[str, Any]:
    diagnostics: dict[str, Any] = {}
    if px4_sitl_prereq_diagnostic.get("status") != "not_provided":
        diagnostics["px4_sitl_prereqs"] = px4_sitl_prereq_diagnostic
    return diagnostics


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


def check_field_collection_plan(
    path: str | Path | None,
    *,
    support_manifest: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if path is not None:
        source = Path(path).expanduser()
        try:
            plan = json.loads(source.read_text())
        except Exception as exc:
            return failed("field_collection_plan", f"Could not read field collection plan: {exc}", {"path": str(source)})
        return field_collection_plan_check_from_plan(plan, source=str(source))

    field_plans = (support_manifest or {}).get("field_collection_plans") if support_manifest else None
    if isinstance(field_plans, dict):
        return field_collection_plan_check_from_summary(field_plans)
    return failed(
        "field_collection_plan",
        "A completed field collection plan is required for autonomy readiness.",
        {"required_conditions": REQUIRED_FIELD_CONDITIONS},
    )


def field_collection_plan_check_from_plan(plan: dict[str, Any], *, source: str) -> dict[str, Any]:
    if plan.get("schema_version") != "vision_nav_field_collection_plan_v1":
        return failed(
            "field_collection_plan",
            "Field collection plan has an unexpected schema.",
            {"source": source, "schema_version": plan.get("schema_version")},
        )
    summary = plan.get("summary") if isinstance(plan.get("summary"), dict) else {}
    conditions = [item for item in plan.get("conditions") or [] if isinstance(item, dict)]
    registered_conditions = sorted(
        {
            str(item.get("condition"))
            for item in conditions
            if item.get("condition") and str(item.get("status") or "") == "registered"
        }
    )
    missing_conditions = missing_required_conditions(registered_conditions)
    missing_traceability = field_collection_missing_traceability(conditions)
    details = {
        "source": source,
        "status": normalize_status(plan.get("status")),
        "site_name": plan.get("site_name"),
        "required_count": summary.get("required_count"),
        "registered_count": summary.get("registered_count"),
        "placeholder_count": summary.get("placeholder_count"),
        "missing_count": summary.get("missing_count"),
        "registered_missing_log_count": summary.get("registered_missing_log_count"),
        "capture_output_dir_count": plan.get("capture_output_dir_count"),
        "runtime_status_path_count": plan.get("runtime_status_path_count"),
        "condition_source_log_count": plan.get("condition_source_log_count"),
        "registered_conditions": registered_conditions,
        "missing_conditions": missing_conditions,
        "missing_traceability": missing_traceability,
    }
    next_condition = field_collection_next_condition(plan)
    if next_condition:
        details["next_condition"] = next_condition
    if missing_conditions:
        return failed("field_collection_plan", "Field collection plan is missing required registered conditions.", details)
    if missing_traceability:
        return failed("field_collection_plan", "Field collection plan is missing capture/log traceability.", details)
    if normalize_status(plan.get("status")) == "passed":
        return passed("field_collection_plan", "Field collection plan covers every required condition with traceable logs.", details)
    return failed("field_collection_plan", f"Field collection plan is {plan.get('status') or 'missing'}.", details)


def field_collection_plan_check_from_summary(summary: dict[str, Any]) -> dict[str, Any]:
    status = normalize_status(summary.get("status"))
    reports = summary.get("reports") if isinstance(summary.get("reports"), list) else []
    covered_conditions: list[str] = []
    for report in reports:
        if not isinstance(report, dict):
            continue
        conditions = report.get("conditions") if isinstance(report.get("conditions"), list) else []
        covered_conditions.extend(
            str(condition.get("condition"))
            for condition in conditions
            if isinstance(condition, dict)
            and condition.get("condition")
            and str(condition.get("status") or "") == "registered"
        )
    covered_conditions = sorted(set(covered_conditions))
    missing_conditions = missing_required_conditions(covered_conditions)
    required_count = int(summary.get("required_count") or len(REQUIRED_FIELD_CONDITIONS))
    registered_count = int(summary.get("registered_count") or 0)
    condition_source_log_count = int(summary.get("condition_source_log_count") or 0)
    capture_output_dir_count = int(summary.get("capture_output_dir_count") or 0)
    runtime_status_path_count = int(summary.get("runtime_status_path_count") or 0)
    details = {
        "source": "support_bundle",
        "status": status,
        "report_count": summary.get("report_count"),
        "required_count": required_count,
        "registered_count": registered_count,
        "capture_output_dir_count": capture_output_dir_count,
        "runtime_status_path_count": runtime_status_path_count,
        "condition_source_log_count": condition_source_log_count,
        "missing_conditions": missing_conditions,
    }
    next_condition = summary.get("next_condition")
    if isinstance(next_condition, dict):
        details["next_condition"] = next_condition
    if status == "not_provided" or status is None:
        return failed("field_collection_plan", "A completed field collection plan is required for autonomy readiness.", details)
    if missing_conditions or registered_count < required_count:
        return failed("field_collection_plan", "Field collection plan is missing required registered conditions.", details)
    if (
        condition_source_log_count < required_count
        or capture_output_dir_count < required_count
        or runtime_status_path_count < required_count
    ):
        return failed("field_collection_plan", "Field collection plan is missing capture/log traceability.", details)
    if status == "passed":
        return passed("field_collection_plan", "Field collection plan proof is present in the support bundle.", details)
    return failed("field_collection_plan", f"Field collection plan proof in the support bundle is {status}.", details)


def field_collection_next_condition(plan: dict[str, Any]) -> dict[str, Any] | None:
    raw_next = plan.get("next_condition")
    if isinstance(raw_next, dict):
        return condition_with_metadata_update_command(compact_field_collection_condition(raw_next), plan)
    conditions = plan.get("conditions")
    if not isinstance(conditions, list):
        return None
    for item in conditions:
        if isinstance(item, dict) and item.get("status") != "registered":
            return condition_with_metadata_update_command(compact_field_collection_condition(item), plan)
    return None


def compact_field_collection_condition(item: dict[str, Any]) -> dict[str, Any]:
    keys = (
        "condition",
        "label",
        "expected",
        "status",
        "case_name",
        "source_log",
        "capture_output_dir",
        "runtime_status_path",
        "bundle",
        "capture_command",
        "metadata_update_command",
        "register_command",
    )
    return {key: str(item[key]) for key in keys if item.get(key) is not None}


def condition_with_metadata_update_command(condition: dict[str, Any], plan: dict[str, Any]) -> dict[str, Any]:
    if condition.get("metadata_update_command"):
        return condition
    condition_name = condition.get("condition")
    manifest_path = plan.get("manifest_path")
    if not condition_name or not manifest_path:
        return condition
    updated = dict(condition)
    updated["metadata_update_command"] = shell_command(
        {
            "VISION_NAV_FIELD_MANIFEST": str(manifest_path),
            "VISION_NAV_FIELD_CONDITION": str(condition_name),
        },
        "./scripts/pi/update_field_capture_metadata.sh",
    )
    return updated


def shell_command(env: dict[str, str], command: str) -> str:
    parts = [f"{key}={shlex.quote(str(value))}" for key, value in env.items() if str(value)]
    return " \\\n  ".join(parts + [command])


def field_collection_missing_traceability(conditions: list[dict[str, Any]]) -> list[str]:
    missing: list[str] = []
    for condition in REQUIRED_FIELD_CONDITIONS:
        item = next(
            (
                entry
                for entry in conditions
                if str(entry.get("condition") or "") == condition and str(entry.get("status") or "") == "registered"
            ),
            None,
        )
        if item is None:
            continue
        if not item.get("source_log") or not item.get("capture_output_dir") or not item.get("runtime_status_path"):
            missing.append(condition)
    return missing


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


REQUIRED_WORKFLOW_VALIDATION_CHECKS = [
    "schema",
    "workflow_provenance",
    "required_steps",
    "step_statuses",
    "required_step_results",
    "important_markers",
    "final_proof_markers",
    "log_archive",
    "final_readiness_status",
    "workflow_status",
]


def check_evidence_workflow_validation(
    workflow_report_path: str | Path | None,
    workflow_validation_report_path: str | Path | None,
    workflow_log_archive_path: str | Path | None,
) -> dict[str, Any]:
    details: dict[str, Any] = {
        "required_steps": REQUIRED_WORKFLOW_STEPS,
        "required_validation_checks": REQUIRED_WORKFLOW_VALIDATION_CHECKS,
        "workflow_report": str(Path(workflow_report_path).expanduser()) if workflow_report_path else None,
        "validation_report": str(Path(workflow_validation_report_path).expanduser())
        if workflow_validation_report_path
        else None,
        "log_archive": str(Path(workflow_log_archive_path).expanduser()) if workflow_log_archive_path else None,
    }
    validation: dict[str, Any]

    if workflow_validation_report_path:
        source = Path(workflow_validation_report_path).expanduser()
        if not source.is_file():
            return failed(
                "evidence_workflow_validation",
                "Autonomy evidence workflow validation report is missing.",
                details,
            )
        try:
            validation = json.loads(source.read_text(encoding="utf-8"))
        except Exception as exc:
            details["error"] = str(exc)
            return failed(
                "evidence_workflow_validation",
                "Could not read autonomy evidence workflow validation report.",
                details,
            )
        details["source"] = str(source)
    elif workflow_report_path:
        source = Path(workflow_report_path).expanduser()
        if not source.is_file():
            return failed(
                "evidence_workflow_validation",
                "Autonomy evidence workflow report is missing.",
                details,
            )
        try:
            validation = validate_workflow_report(source)
        except Exception as exc:
            details["error"] = str(exc)
            return failed(
                "evidence_workflow_validation",
                "Could not validate autonomy evidence workflow report.",
                details,
            )
        details["source"] = str(source)
        details["validation_source"] = "computed_from_workflow_report"
    else:
        return failed(
            "evidence_workflow_validation",
            "A passed autonomy evidence workflow validation report is required for final readiness.",
            details,
        )

    checks = validation.get("checks") if isinstance(validation.get("checks"), list) else []
    checks_by_name = {
        str(check.get("name") or ""): check
        for check in checks
        if isinstance(check, dict) and check.get("name")
    }
    missing_checks = [name for name in REQUIRED_WORKFLOW_VALIDATION_CHECKS if name not in checks_by_name]
    non_passing_checks = [
        {
            "name": name,
            "status": normalize_status(checks_by_name[name].get("status")) or "unknown",
            "message": str(checks_by_name[name].get("message") or ""),
        }
        for name in REQUIRED_WORKFLOW_VALIDATION_CHECKS
        if name in checks_by_name and normalize_status(checks_by_name[name].get("status")) != "passed"
    ]
    issues = validation.get("issues") if isinstance(validation.get("issues"), list) else []
    details.update(
        {
            "schema_version": validation.get("schema_version"),
            "status": normalize_status(validation.get("status")) or "unknown",
            "workflow_status": normalize_status(validation.get("workflow_status")) or "unknown",
            "report_path": validation.get("report_path"),
            "step_count": validation.get("step_count"),
            "marker_count": validation.get("marker_count"),
            "issue_count": validation.get("issue_count", len(issues)),
            "issues": [str(issue) for issue in issues[:8]],
            "next_required_step": validation.get("next_required_step")
            if isinstance(validation.get("next_required_step"), dict)
            else None,
            "missing_validation_checks": missing_checks,
            "non_passing_validation_checks": non_passing_checks,
        }
    )

    if validation.get("schema_version") != "vision_nav_autonomy_evidence_workflow_validation_v1":
        return failed(
            "evidence_workflow_validation",
            "Autonomy evidence workflow validation report has an unexpected schema.",
            details,
        )

    try:
        step_count = int(validation.get("step_count") or 0)
    except (TypeError, ValueError):
        step_count = 0
    if step_count < len(REQUIRED_WORKFLOW_STEPS):
        return failed(
            "evidence_workflow_validation",
            "Autonomy evidence workflow validation is missing required workflow step coverage.",
            details,
        )

    if workflow_log_archive_path:
        archive_source = Path(workflow_log_archive_path).expanduser()
        if not archive_source.is_file():
            return failed(
                "evidence_workflow_validation",
                "Autonomy evidence workflow log archive is missing.",
                details,
            )

    if missing_checks:
        return failed(
            "evidence_workflow_validation",
            "Autonomy evidence workflow validation is missing required validator checks.",
            details,
        )
    if non_passing_checks:
        return failed(
            "evidence_workflow_validation",
            "Autonomy evidence workflow validation has non-passing required checks.",
            details,
        )

    if details["status"] == "passed" and details["workflow_status"] == "passed":
        return passed(
            "evidence_workflow_validation",
            "Autonomy evidence workflow validation passed.",
            details,
        )
    return failed(
        "evidence_workflow_validation",
        f"Autonomy evidence workflow validation is {details['status']} with workflow status {details['workflow_status']}.",
        details,
    )


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
    diagnostics = report.get("diagnostics") if isinstance(report.get("diagnostics"), dict) else {}
    px4_prereqs = diagnostics.get("px4_sitl_prereqs") if isinstance(diagnostics, dict) else None
    if isinstance(px4_prereqs, dict):
        fix_commands = [
            item
            for item in px4_prereqs.get("fix_commands") or []
            if isinstance(item, dict) and str(item.get("command") or "")
        ]
        if fix_commands:
            print("PX4 prerequisite fix commands:")
            for item in fix_commands:
                label = item.get("label") or item.get("condition") or "command"
                print(f"- {label}: {item.get('command')}")
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
        px4_sitl_prereq_path=args.px4_sitl_prereqs,
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
