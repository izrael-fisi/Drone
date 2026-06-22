#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${VISION_NAV_PYTHON:-python3}"
download_root="${VISION_NAV_DESKTOP_TRANSFER_FROM_PI:-$HOME/DroneTransfer/from-pi}"
support_dir="${VISION_NAV_LOCAL_SUPPORT_DIR:-$download_root/support-bundles}"
replay_dir="${VISION_NAV_LOCAL_REPLAY_DIR:-$download_root/replay-cases}"
feature_benchmark_dir="${VISION_NAV_LOCAL_FEATURE_BENCH_DIR:-$download_root/feature-method-bench}"
support_bundle="${VISION_NAV_AUTONOMY_SUPPORT_BUNDLE:-}"
field_evidence_report="${VISION_NAV_FIELD_EVIDENCE_REPORT:-$replay_dir/field_evidence_report.json}"
field_collection_plan="${VISION_NAV_FIELD_COLLECTION_PLAN:-$replay_dir/field_collection_plan.json}"
feature_method_benchmark_report="${VISION_NAV_FEATURE_METHOD_BENCHMARK_REPORT:-}"
threshold_tuning_report="${VISION_NAV_THRESHOLD_TUNING_REPORT:-$replay_dir/threshold_tuning_report.json}"
rosbag_export_validation="${VISION_NAV_ROSBAG_EXPORT_VALIDATION:-$download_root/terrain-match/rosbag-jsonl-validation.json}"
rosbag2_cli_review="${VISION_NAV_ROSBAG2_CLI_REVIEW:-$download_root/terrain-match/rosbag2-cli-review.json}"
evidence_workflow_report="${VISION_NAV_EVIDENCE_WORKFLOW_REPORT:-}"
evidence_workflow_validation_report="${VISION_NAV_EVIDENCE_WORKFLOW_VALIDATION:-}"
evidence_workflow_log_archive="${VISION_NAV_EVIDENCE_WORKFLOW_LOG_ARCHIVE:-}"
px4_sitl_session="${VISION_NAV_PX4_SITL_SESSION:-}"
px4_sitl_report="${VISION_NAV_PX4_SITL_REPORT:-}"
px4_sitl_prereqs="${VISION_NAV_PX4_SITL_PREREQS:-}"
skip_conventional_px4="${VISION_NAV_SKIP_CONVENTIONAL_PX4_SITL:-0}"
json_copy="${VISION_NAV_AUTONOMY_GOAL_STATUS_JSON:-}"
quiet_exit="${VISION_NAV_AUTONOMY_GOAL_STATUS_QUIET_EXIT:-0}"
tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/vision-nav-goal-status.XXXXXX")"
tmp_report="$tmp_dir/report.json"
trap 'rm -rf "$tmp_dir"' EXIT

latest_glob() {
  local pattern="$1"
  local matches=()
  while IFS= read -r path; do
    matches+=("$path")
  done < <(compgen -G "$pattern" || true)
  if ((${#matches[@]} == 0)); then
    return 0
  fi
  ls -t "${matches[@]}" 2>/dev/null | head -n 1
}

first_existing_px4_session() {
  if [[ "$skip_conventional_px4" == "1" ]]; then
    return 0
  fi
  local candidates=(
    "$repo_root/px4-sitl-evidence"
    "$PWD/px4-sitl-evidence"
    "$HOME/px4-sitl-evidence"
    "$download_root/px4-sitl-evidence"
  )
  local candidate
  for candidate in "${candidates[@]}"; do
    if [[ -f "$candidate/px4_sitl_evidence_session.json" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
}

first_existing_px4_report() {
  if [[ "$skip_conventional_px4" == "1" ]]; then
    return 0
  fi
  local candidates=(
    "$repo_root/px4-sitl-evidence/receiver_evidence.json"
    "$PWD/px4-sitl-evidence/receiver_evidence.json"
    "$HOME/px4-sitl-evidence/receiver_evidence.json"
    "$download_root/px4-sitl-evidence/receiver_evidence.json"
  )
  local candidate
  for candidate in "${candidates[@]}"; do
    if [[ -f "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
}

first_existing_px4_prereqs() {
  local candidates=()
  if [[ -n "$px4_sitl_session" ]]; then
    candidates+=("$px4_sitl_session/px4_sitl_capture_prereqs.json")
  fi
  if [[ -n "$px4_sitl_report" ]]; then
    candidates+=("$(dirname "$px4_sitl_report")/px4_sitl_capture_prereqs.json")
  fi
  if [[ "$skip_conventional_px4" != "1" ]]; then
    candidates+=(
      "$repo_root/px4-sitl-evidence/px4_sitl_capture_prereqs.json"
      "$PWD/px4-sitl-evidence/px4_sitl_capture_prereqs.json"
      "$HOME/px4-sitl-evidence/px4_sitl_capture_prereqs.json"
      "$download_root/px4-sitl-evidence/px4_sitl_capture_prereqs.json"
    )
  fi
  if ((${#candidates[@]} == 0)); then
    return 0
  fi
  local candidate
  for candidate in "${candidates[@]}"; do
    if [[ -f "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
}

first_existing_workflow_report() {
  local candidates=(
    "$replay_dir/autonomy_evidence_workflow.json"
    "$replay_dir/autonomy-evidence-workflow/autonomy_evidence_workflow.json"
    "$download_root/replay-cases/autonomy_evidence_workflow.json"
    "$download_root/replay-cases/autonomy-evidence-workflow/autonomy_evidence_workflow.json"
  )
  local candidate
  for candidate in "${candidates[@]}"; do
    if [[ -f "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
}

first_existing_workflow_validation_report() {
  local candidates=()
  if [[ -n "$evidence_workflow_report" ]]; then
    candidates+=("${evidence_workflow_report%.json}.validation.json")
  fi
  candidates+=(
    "$replay_dir/autonomy_evidence_workflow.validation.json"
    "$replay_dir/autonomy-evidence-workflow/autonomy_evidence_workflow.validation.json"
    "$download_root/replay-cases/autonomy_evidence_workflow.validation.json"
    "$download_root/replay-cases/autonomy-evidence-workflow/autonomy_evidence_workflow.validation.json"
  )
  local candidate
  for candidate in "${candidates[@]}"; do
    if [[ -f "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
}

first_existing_workflow_log_archive() {
  local candidates=()
  if [[ -n "$evidence_workflow_report" ]]; then
    candidates+=("${evidence_workflow_report%.json}.logs.tar.gz")
  fi
  candidates+=(
    "$replay_dir/autonomy_evidence_workflow.logs.tar.gz"
    "$replay_dir/autonomy-evidence-workflow/autonomy_evidence_workflow.logs.tar.gz"
    "$download_root/replay-cases/autonomy_evidence_workflow.logs.tar.gz"
    "$download_root/replay-cases/autonomy-evidence-workflow/autonomy_evidence_workflow.logs.tar.gz"
  )
  local candidate
  for candidate in "${candidates[@]}"; do
    if [[ -f "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
}

if [[ -z "$support_bundle" ]]; then
  support_bundle="$(latest_glob "$support_dir/*.zip")"
fi

if [[ -z "$feature_method_benchmark_report" ]]; then
  feature_method_benchmark_report="$(latest_glob "$feature_benchmark_dir/*.json")"
fi

if [[ -z "$px4_sitl_session" ]]; then
  px4_sitl_session="$(first_existing_px4_session || true)"
fi

if [[ -z "$px4_sitl_report" ]]; then
  px4_sitl_report="$(first_existing_px4_report || true)"
fi

if [[ -z "$px4_sitl_prereqs" ]]; then
  px4_sitl_prereqs="$(first_existing_px4_prereqs || true)"
fi

if [[ -z "$evidence_workflow_report" ]]; then
  evidence_workflow_report="$(first_existing_workflow_report || true)"
fi

if [[ -z "$evidence_workflow_validation_report" ]]; then
  evidence_workflow_validation_report="$(first_existing_workflow_validation_report || true)"
fi

if [[ -z "$evidence_workflow_log_archive" ]]; then
  evidence_workflow_log_archive="$(first_existing_workflow_log_archive || true)"
fi

args=(
  -m vision_nav.autonomy_readiness
  --research-doc "$repo_root/docs/autonomy-ground-control-research.md"
  --implementation-plan "$repo_root/docs/autonomy-ground-control-implementation-plan.md"
  --json
)

if [[ -n "$support_bundle" && -f "$support_bundle" ]]; then
  args+=(--support-bundle "$support_bundle")
fi

if [[ -n "$px4_sitl_session" && -f "$px4_sitl_session/px4_sitl_evidence_session.json" ]]; then
  args+=(--px4-sitl-session "$px4_sitl_session")
elif [[ -f "$px4_sitl_report" ]]; then
  args+=(--px4-sitl-report "$px4_sitl_report")
fi

if [[ -f "$px4_sitl_prereqs" ]]; then
  args+=(--px4-sitl-prereqs "$px4_sitl_prereqs")
fi

if [[ -f "$field_evidence_report" ]]; then
  args+=(--field-evidence-report "$field_evidence_report")
fi

if [[ -f "$field_collection_plan" ]]; then
  args+=(--field-collection-plan "$field_collection_plan")
fi

if [[ -f "$feature_method_benchmark_report" ]]; then
  args+=(--feature-method-benchmark-report "$feature_method_benchmark_report")
fi

if [[ -f "$threshold_tuning_report" ]]; then
  args+=(--threshold-tuning-report "$threshold_tuning_report")
fi

if [[ -f "$rosbag_export_validation" ]]; then
  args+=(--rosbag-export-validation "$rosbag_export_validation")
fi

if [[ -f "$rosbag2_cli_review" ]]; then
  args+=(--rosbag2-cli-review "$rosbag2_cli_review")
fi

if [[ -f "$evidence_workflow_report" ]]; then
  args+=(--evidence-workflow-report "$evidence_workflow_report")
fi

if [[ -f "$evidence_workflow_validation_report" ]]; then
  args+=(--evidence-workflow-validation-report "$evidence_workflow_validation_report")
fi

if [[ -f "$evidence_workflow_log_archive" ]]; then
  args+=(--evidence-workflow-log-archive "$evidence_workflow_log_archive")
fi

set +e
PYTHONPATH="$repo_root/src" "$python_bin" "${args[@]}" >"$tmp_report"
audit_status=$?
set -e

if [[ -n "$json_copy" ]]; then
  mkdir -p "$(dirname "$json_copy")"
  cp "$tmp_report" "$json_copy"
fi

PYTHONPATH="$repo_root/src" "$python_bin" - "$tmp_report" <<'PY'
import json
import sys

try:
    from vision_nav.field_conditions import (
        REQUIRED_FIELD_CONDITIONS,
        expected_behavior_for_condition,
        label_for_condition,
    )
except Exception:  # pragma: no cover - keeps this formatter useful from partial checkouts.
    REQUIRED_FIELD_CONDITIONS = [
        "good_texture",
        "low_texture",
        "blur",
        "seasonal_change",
        "lighting_change",
        "altitude_scale_change",
        "repeated_patterns",
        "wrong_map",
    ]

    def expected_behavior_for_condition(condition):
        return "wrong_map" if condition == "wrong_map" else "degraded"

    def label_for_condition(condition):
        return str(condition).replace("_", " ").title()


FIELD_COLLECTION_CHECKS = {"field_collection_plan", "field_evidence_proof", "threshold_tuning"}


def unique_ordered(values):
    seen = set()
    ordered = []
    for value in values:
        if value is None:
            continue
        key = str(value)
        if not key or key in seen:
            continue
        seen.add(key)
        ordered.append(key)
    return ordered


def order_field_conditions(values):
    values = unique_ordered(values)
    known = [condition for condition in REQUIRED_FIELD_CONDITIONS if condition in values]
    unknown = [condition for condition in values if condition not in REQUIRED_FIELD_CONDITIONS]
    return known + unknown


def field_condition_from_details(item):
    details = item.get("details") if isinstance(item.get("details"), dict) else {}
    next_condition = details.get("next_condition")
    if isinstance(next_condition, dict) and next_condition.get("condition"):
        return next_condition
    return None


def field_condition_names_from_report(report, external_blockers):
    names = []
    for item in report.get("checks") or []:
        if not isinstance(item, dict) or item.get("name") not in FIELD_COLLECTION_CHECKS:
            continue
        details = item.get("details") if isinstance(item.get("details"), dict) else {}
        names.extend(details.get("missing_conditions") or [])
        names.extend(details.get("required_conditions") or [])
    for blocker in external_blockers:
        if not isinstance(blocker, dict) or blocker.get("name") not in FIELD_COLLECTION_CHECKS:
            continue
        names.extend(blocker.get("missing_conditions") or [])
    return order_field_conditions(names)


def find_next_field_condition(report, missing_conditions):
    for item in report.get("checks") or []:
        if not isinstance(item, dict) or item.get("name") != "field_collection_plan":
            continue
        next_condition = field_condition_from_details(item)
        if next_condition:
            return next_condition
    if missing_conditions:
        condition = missing_conditions[0]
        return {
            "condition": condition,
            "label": label_for_condition(condition),
            "expected": expected_behavior_for_condition(condition),
        }
    return None


def check_details(report, name):
    for item in report.get("checks") or []:
        if isinstance(item, dict) and item.get("name") == name:
            details = item.get("details")
            return details if isinstance(details, dict) else {}
    return {}


def print_multiline_command(prefix, command):
    if not command:
        return
    print(prefix)
    for line in str(command).splitlines():
        print(f"  {line}")


report_path = sys.argv[1]
with open(report_path, "r", encoding="utf-8") as handle:
    report = json.load(handle)

summary = report.get("summary") or {}
evidence = report.get("evidence_manifest") or {}
proof_items = evidence.get("proof_items") or []
external_blockers = evidence.get("external_blockers") or []
completion_blockers = evidence.get("completion_blockers") or []
runbook = report.get("proof_runbook") or {}
runbook_summary = runbook.get("summary") or {}
phases = runbook.get("phases") or []
metadata = report.get("metadata") or {}
repo = metadata.get("repo") or {}
inputs = report.get("inputs") or {}
diagnostics = report.get("diagnostics") if isinstance(report.get("diagnostics"), dict) else {}
command_bundle = report.get("command_bundle") if isinstance(report.get("command_bundle"), dict) else {}
guided_workflow_commands = [
    str(command)
    for command in command_bundle.get("guided_workflow_commands") or []
    if isinstance(command, str) and command
]
prerequisite_fix_commands = [
    str(command)
    for command in command_bundle.get("prerequisite_fix_commands") or []
    if isinstance(command, str) and command
]
if external_blockers and not guided_workflow_commands:
    guided_workflow_commands = ["./scripts/pi/run_autonomy_evidence_workflow.sh"]

passed = sum(1 for item in proof_items if item.get("status") == "passed")
print(f"Autonomy goal status: {report.get('status', 'unknown')}")
if repo.get("detected"):
    dirty = "dirty" if repo.get("dirty") else "clean"
    print(f"Repo: {repo.get('branch', 'unknown')} @ {str(repo.get('commit') or '')[:8]} ({dirty})")
print(
    "Checks: "
    f"{summary.get('passed', 0)} passed, "
    f"{summary.get('degraded', 0)} degraded, "
    f"{summary.get('failed', 0)} failed"
)
print(f"Proof items: {passed}/{len(proof_items)} passed")
print(f"Completion blockers: {len(completion_blockers)}")
print(f"External proof blockers: {len(external_blockers)}")
if runbook_summary:
    print(
        "Proof runbook: "
        f"{runbook_summary.get('passed', 0)} passed, "
        f"{runbook_summary.get('action_required', 0)} action-required, "
        f"{runbook_summary.get('blocked', 0)} blocked"
    )

present_inputs = [
    (key, value)
    for key, value in inputs.items()
    if value and key not in {"research_doc", "implementation_plan"}
]
if present_inputs:
    print()
    print("Evidence inputs:")
    for key, value in present_inputs[:12]:
        print(f"- {key}: {value}")
    if len(present_inputs) > 12:
        print(f"- ... {len(present_inputs) - 12} more")

if phases:
    print()
    print("Proof phases:")
    for phase in phases:
        phase_id = phase.get("id") or "unknown"
        status = phase.get("status") or "unknown"
        title = phase.get("title") or phase_id
        print(f"- {phase_id} [{status}]: {title}")
        dependencies = phase.get("dependency_status") or {}
        waiting_on = [
            f"{name}={value}"
            for name, value in dependencies.items()
            if value != "passed"
        ]
        if waiting_on:
            print(f"  waiting on: {', '.join(waiting_on)}")

if external_blockers:
    print()
    print("External blockers:")
    for blocker in external_blockers[:12]:
        name = blocker.get("name") or "unknown"
        status = blocker.get("status") or "unknown"
        message = blocker.get("message") or ""
        print(f"- {name} [{status}]: {message}")
        missing = blocker.get("missing_conditions") or []
        if missing:
            visible = ", ".join(str(item) for item in missing[:8])
            extra = "" if len(missing) <= 8 else f" +{len(missing) - 8}"
            print(f"  missing conditions: {visible}{extra}")
    if len(external_blockers) > 12:
        print(f"- ... {len(external_blockers) - 12} more")

support_details = check_details(report, "support_bundle_bench_readiness")
bench_inputs = [
    str(value)
    for value in support_details.get("expected_bench_inputs") or []
    if str(value)
]
support_bundle_command = str(support_details.get("support_bundle_command") or "")
if bench_inputs or support_bundle_command:
    print()
    print("Bench evidence preview:")
    if bench_inputs:
        print("- support bundle should include:")
        for value in bench_inputs[:10]:
            print(f"  - {value}")
        if len(bench_inputs) > 10:
            print(f"  - ... {len(bench_inputs) - 10} more")
    if support_bundle_command:
        print(f"- create or refresh support bundle after inputs exist: {support_bundle_command}")

field_conditions = field_condition_names_from_report(report, external_blockers)
next_field_condition = find_next_field_condition(report, field_conditions)
if field_conditions or next_field_condition:
    print()
    print("Field collection preview:")
    if next_field_condition:
        condition = str(next_field_condition.get("condition") or "")
        label = next_field_condition.get("label") or label_for_condition(condition)
        expected = next_field_condition.get("expected") or expected_behavior_for_condition(condition)
        status = next_field_condition.get("status")
        suffix = f", status {status}" if status else ""
        print(f"- next: {label} ({condition}), expected {expected}{suffix}")
        capture_output_dir = next_field_condition.get("capture_output_dir")
        runtime_status_path = next_field_condition.get("runtime_status_path")
        source_log = next_field_condition.get("source_log")
        if capture_output_dir:
            print(f"  capture output: {capture_output_dir}")
        if source_log:
            print(f"  terrain log: {source_log}")
        if runtime_status_path:
            print(f"  runtime status: {runtime_status_path}")
        print_multiline_command("  capture command:", next_field_condition.get("capture_command"))
        print_multiline_command("  register command:", next_field_condition.get("register_command"))
    remaining = [
        condition
        for condition in field_conditions
        if not next_field_condition or condition != next_field_condition.get("condition")
    ]
    if remaining:
        print("- remaining required conditions:")
        for condition in remaining[:12]:
            print(f"  - {label_for_condition(condition)} ({condition}), expected {expected_behavior_for_condition(condition)}")
        if len(remaining) > 12:
            print(f"  - ... {len(remaining) - 12} more")
    if not next_field_condition or not next_field_condition.get("capture_command"):
        print("- create or refresh the Pi field collection plan to get condition-specific capture/register commands.")

px4_prereqs = diagnostics.get("px4_sitl_prereqs") if isinstance(diagnostics, dict) else None
if isinstance(px4_prereqs, dict):
    failed_checks = [
        item
        for item in px4_prereqs.get("failed_checks") or []
        if isinstance(item, dict)
    ]
    issues = [
        item
        for item in px4_prereqs.get("issues") or []
        if isinstance(item, dict)
    ]
    next_prereq_actions = [
        str(item)
        for item in px4_prereqs.get("next_actions") or []
        if str(item)
    ]
    prereq_fix_commands = [
        item
        for item in px4_prereqs.get("fix_commands") or []
        if isinstance(item, dict) and str(item.get("command") or "")
    ]
    if failed_checks or issues or px4_prereqs.get("status") not in {"passed", "not_provided", None}:
        print()
        print("Diagnostics:")
        status = px4_prereqs.get("status") or "unknown"
        path = px4_prereqs.get("path") or inputs.get("px4_sitl_prereqs") or "unknown"
        print(f"- px4_sitl_prereqs [{status}]: {path}")
        for item in failed_checks[:6]:
            name = item.get("name") or "unknown"
            item_status = item.get("status") or "unknown"
            message = item.get("message") or ""
            print(f"  failed check: {name} [{item_status}] {message}")
        for item in issues[:4]:
            severity = item.get("severity") or "info"
            message = item.get("message") or ""
            print(f"  issue [{severity}]: {message}")
        for action in next_prereq_actions[:4]:
            print(f"  next action: {action}")
        for command in prereq_fix_commands[:6]:
            label = command.get("label") or command.get("condition") or "fix command"
            print(f"  fix command ({label}): {command.get('command')}")

if prerequisite_fix_commands:
    print()
    print("Immediate prerequisite fixes:")
    print("- Run the applicable setup fixes before PX4 receiver capture or support-bundle creation.")
    for index, command in enumerate(prerequisite_fix_commands[:8], start=1):
        print(f"{index}. {command}")
    if len(prerequisite_fix_commands) > 8:
        print(f"- ... {len(prerequisite_fix_commands) - 8} more")

if guided_workflow_commands:
    print()
    print("Guided workflow option:")
    for index, command in enumerate(guided_workflow_commands[:4], start=1):
        if index == 1:
            print(f"{index}. Run the ordered Pi evidence workflow and preserve partial artifacts.")
        else:
            print(f"{index}. Guided evidence workflow command.")
        print(f"   {command}")

phase_commands = []
blocked_phase_commands = []
if phases:
    for phase in phases:
        phase_command_values = set()
        phase_items = []
        for action in phase.get("actions") or []:
            command = action.get("command")
            if not command:
                continue
            phase_command_values.add(command)
            phase_items.append(
                {
                    "title": action.get("title") or phase.get("title") or phase.get("id") or "next action",
                    "command": command,
                }
            )
        for command in phase.get("commands") or []:
            if command in phase_command_values:
                continue
            phase_items.append(
                {
                    "title": phase.get("title") or phase.get("id") or "next action",
                    "command": command,
                }
            )
        if phase.get("status") == "action_required":
            phase_commands.extend(phase_items)
        elif phase.get("status") == "blocked" and phase.get("id") != "final_audit":
            dependencies = phase.get("dependency_status") or {}
            waiting_on = [
                f"{name}={value}"
                for name, value in dependencies.items()
                if value != "passed"
            ]
            for item in phase_items:
                blocked_phase_commands.append(
                    {
                        **item,
                        "waiting_on": ", ".join(waiting_on),
                    }
                )
next_actions = phase_commands or (report.get("next_actions") or [])
printed_commands = set()
if next_actions:
    print()
    print("Next commands:")
    count = 0
    for action in next_actions:
        command = action.get("command")
        if not command or command in printed_commands:
            continue
        printed_commands.add(command)
        count += 1
        title = action.get("title") or action.get("check") or "next action"
        print(f"{count}. {title}")
        print(f"   {command}")
        if count >= 8:
            break
if blocked_phase_commands:
    print()
    print("Blocked follow-up commands:")
    count = 0
    for action in blocked_phase_commands:
        command = action.get("command")
        if not command or command in printed_commands:
            continue
        printed_commands.add(command)
        count += 1
        title = action.get("title") or "blocked follow-up"
        print(f"{count}. {title}")
        waiting_on = action.get("waiting_on")
        if waiting_on:
            print(f"   waiting on: {waiting_on}")
        print(f"   {command}")
        if count >= 8:
            break
PY

if [[ "$audit_status" -ne 0 ]]; then
  if [[ "$quiet_exit" != "1" && "$quiet_exit" != "true" ]]; then
    echo
    echo "Autonomy goal is not complete yet; run prerequisite fixes if shown, then the immediate next commands, then blocked follow-ups after their prerequisites clear." >&2
  fi
  exit "$audit_status"
fi
