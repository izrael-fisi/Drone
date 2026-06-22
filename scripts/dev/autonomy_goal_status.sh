#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${VISION_NAV_PYTHON:-python3}"
download_root="${VISION_NAV_DESKTOP_TRANSFER_FROM_PI:-$HOME/DroneTransfer/from-pi}"
local_output_root="${VISION_NAV_LOCAL_TRANSFER_OUTGOING:-$HOME/DroneTransfer/outgoing}"
support_dir="${VISION_NAV_LOCAL_SUPPORT_DIR:-$download_root/support-bundles}"
replay_dir="${VISION_NAV_LOCAL_REPLAY_DIR:-$download_root/replay-cases}"
feature_benchmark_dir="${VISION_NAV_LOCAL_FEATURE_BENCH_DIR:-$download_root/feature-method-bench}"
local_support_dir="$local_output_root/support-bundles"
local_replay_dir="$local_output_root/replay-cases"
local_feature_benchmark_dir="$local_output_root/feature-method-bench"
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
    "$local_output_root/px4-sitl-evidence"
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
    "$local_output_root/px4-sitl-evidence/receiver_evidence.json"
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
      "$local_output_root/px4-sitl-evidence/px4_sitl_capture_prereqs.json"
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
    "$local_output_root/replay-cases/autonomy_evidence_workflow.json"
    "$local_output_root/replay-cases/autonomy-evidence-workflow/autonomy_evidence_workflow.json"
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
    "$local_output_root/replay-cases/autonomy_evidence_workflow.validation.json"
    "$local_output_root/replay-cases/autonomy-evidence-workflow/autonomy_evidence_workflow.validation.json"
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
    "$local_output_root/replay-cases/autonomy_evidence_workflow.logs.tar.gz"
    "$local_output_root/replay-cases/autonomy-evidence-workflow/autonomy_evidence_workflow.logs.tar.gz"
  )
  local candidate
  for candidate in "${candidates[@]}"; do
    if [[ -f "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return 0
    fi
  done
}

refresh_evidence_workflow_validation_report() {
  if [[ ! -f "$evidence_workflow_report" ]]; then
    return 0
  fi
  local refreshed="${evidence_workflow_validation_report:-${evidence_workflow_report%.json}.validation.json}"
  if [[ -z "$refreshed" ]]; then
    return 0
  fi
  set +e
  PYTHONPATH="$repo_root/src" "$python_bin" -m vision_nav.autonomy_evidence_workflow \
    --report "$evidence_workflow_report" \
    --output "$refreshed" >/dev/null 2>&1
  local refresh_status=$?
  set -e
  if [[ -f "$refreshed" ]]; then
    evidence_workflow_validation_report="$refreshed"
  elif [[ "$refresh_status" -ne 0 && -z "$evidence_workflow_validation_report" ]]; then
    evidence_workflow_validation_report="$(first_existing_workflow_validation_report || true)"
  fi
}

if [[ -z "$support_bundle" ]]; then
  support_bundle="$(latest_glob "$support_dir/*.zip")"
  if [[ -z "$support_bundle" ]]; then
    support_bundle="$(latest_glob "$local_support_dir/*.zip")"
  fi
fi

if [[ -z "$feature_method_benchmark_report" ]]; then
  feature_method_benchmark_report="$(latest_glob "$feature_benchmark_dir/*.json")"
  if [[ -z "$feature_method_benchmark_report" ]]; then
    feature_method_benchmark_report="$(latest_glob "$local_feature_benchmark_dir/*.json")"
  fi
fi

if [[ -z "${VISION_NAV_FIELD_EVIDENCE_REPORT:-}" && ! -f "$field_evidence_report" && -f "$local_replay_dir/field_evidence_report.json" ]]; then
  field_evidence_report="$local_replay_dir/field_evidence_report.json"
fi

if [[ -z "${VISION_NAV_FIELD_COLLECTION_PLAN:-}" && ! -f "$field_collection_plan" && -f "$local_replay_dir/field_collection_plan.json" ]]; then
  field_collection_plan="$local_replay_dir/field_collection_plan.json"
fi

if [[ -z "${VISION_NAV_THRESHOLD_TUNING_REPORT:-}" && ! -f "$threshold_tuning_report" && -f "$local_replay_dir/threshold_tuning_report.json" ]]; then
  threshold_tuning_report="$local_replay_dir/threshold_tuning_report.json"
fi

if [[ -z "${VISION_NAV_ROSBAG_EXPORT_VALIDATION:-}" && ! -f "$rosbag_export_validation" && -f "$local_output_root/terrain-match/rosbag-jsonl-validation.json" ]]; then
  rosbag_export_validation="$local_output_root/terrain-match/rosbag-jsonl-validation.json"
fi

if [[ -z "${VISION_NAV_ROSBAG2_CLI_REVIEW:-}" && ! -f "$rosbag2_cli_review" && -f "$local_output_root/terrain-match/rosbag2-cli-review.json" ]]; then
  rosbag2_cli_review="$local_output_root/terrain-match/rosbag2-cli-review.json"
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

refresh_evidence_workflow_validation_report

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

try:
    from vision_nav.field_collection_plan import metadata_update_command_is_detailed
except Exception:  # pragma: no cover - keeps this formatter useful from partial checkouts.

    def metadata_update_command_is_detailed(command):
        if not isinstance(command, str) or not command.strip():
            return False
        return any(
            marker in command
            for marker in (
                "VISION_NAV_FIELD_OPERATOR",
                "--operator",
                "VISION_NAV_FIELD_CAPTURE_METADATA",
                "--json-updates",
            )
        )


FIELD_COLLECTION_CHECKS = {"field_collection_plan", "field_evidence_proof", "threshold_tuning"}
SUPPORT_BUNDLE_COMMAND = "./scripts/pi/create_support_bundle.sh"
FIELD_COLLECTION_COMMAND_APP_ACTIONS = {
    "capture": "Module Setup > Field Log Capture",
    "metadata_update": "Module Setup > Field Evidence Case > Update Metadata",
    "registration": "Module Setup > Field Evidence Case > Register",
}


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


def enriched_metadata_update_command(command, next_field_condition):
    command = str(command or "")
    if not command.strip():
        return ""
    if metadata_update_command_is_detailed(command):
        return command
    if isinstance(next_field_condition, dict):
        replacement = str(next_field_condition.get("metadata_update_command") or "")
        if metadata_update_command_is_detailed(replacement):
            return replacement
    return command


def check_details(report, name):
    for item in report.get("checks") or []:
        if isinstance(item, dict) and item.get("name") == name:
            details = item.get("details")
            return details if isinstance(details, dict) else {}
    return {}


def load_json_file(path):
    if not path:
        return None
    try:
        with open(str(path), "r", encoding="utf-8") as handle:
            value = json.load(handle)
    except Exception:
        return None
    return value if isinstance(value, dict) else None


def print_multiline_command(prefix, command, desktop_action=None):
    if not command:
        return
    print(prefix)
    if desktop_action:
        print(f"  app: {desktop_action}")
    for line in str(command).splitlines():
        print(f"  {line}")


def short_hash(value):
    value = str(value or "")
    return value[:8] if value else "unknown"


def marker_summary(value):
    if not isinstance(value, dict):
        return None
    required = int(value.get("required_marker_count") or 0)
    missing = len(value.get("missing_markers") or [])
    present = max(required - missing, 0)
    return f"{present}/{required}"


def normalize_bench_subchecks(values):
    subchecks = []
    if not isinstance(values, list):
        return subchecks
    for value in values:
        if not isinstance(value, dict):
            continue
        name = str(value.get("name") or "")
        status = str(value.get("status") or "unknown")
        message = str(value.get("message") or "")
        if not name and not message:
            continue
        subchecks.append({"name": name or "unknown", "status": status, "message": message})
    return subchecks


def defer_support_bundle_actions(actions):
    if not isinstance(actions, list):
        return actions
    support_actions = [
        action
        for action in actions
        if isinstance(action, dict) and action.get("command") == SUPPORT_BUNDLE_COMMAND
    ]
    other_actions = [
        action
        for action in actions
        if not (isinstance(action, dict) and action.get("command") == SUPPORT_BUNDLE_COMMAND)
    ]
    return other_actions + support_actions


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
plan_snapshot = report.get("plan_snapshot") if isinstance(report.get("plan_snapshot"), dict) else {}
guided_workflow_commands = [
    str(command)
    for command in command_bundle.get("guided_workflow_commands") or []
    if isinstance(command, str) and command
]
command_app_hints = {}
for item in command_bundle.get("command_items") or []:
    if not isinstance(item, dict):
        continue
    command = item.get("command")
    desktop_action = item.get("desktop_action")
    if isinstance(command, str) and command and isinstance(desktop_action, str) and desktop_action:
        command_app_hints.setdefault(command, desktop_action)
prerequisite_fix_commands = [
    str(command)
    for command in command_bundle.get("prerequisite_fix_commands") or []
    if isinstance(command, str) and command
]
if external_blockers and not guided_workflow_commands:
    guided_workflow_commands = ["./scripts/pi/run_autonomy_evidence_workflow.sh"]

field_conditions = field_condition_names_from_report(report, external_blockers)
next_field_condition = find_next_field_condition(report, field_conditions)

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

research_doc = plan_snapshot.get("research_doc") if isinstance(plan_snapshot.get("research_doc"), dict) else {}
implementation_plan = (
    plan_snapshot.get("implementation_plan")
    if isinstance(plan_snapshot.get("implementation_plan"), dict)
    else {}
)
if research_doc or implementation_plan:
    print()
    print("Plan snapshot:")
    if research_doc:
        print(
            "- research: "
            f"{research_doc.get('path') or 'unknown'} "
            f"markers {marker_summary(research_doc) or 'unknown'} "
            f"refs {int(research_doc.get('highest_value_reference_count') or 0)} "
            f"fit {int(research_doc.get('fit_criteria_count') or 0)} "
            f"sha {short_hash(research_doc.get('source_sha256'))}"
        )
        missing = research_doc.get("missing_markers") or []
        if missing:
            print(f"  missing markers: {', '.join(str(item) for item in missing)}")
    if implementation_plan:
        print(
            "- implementation: "
            f"{implementation_plan.get('path') or 'unknown'} "
            f"markers {marker_summary(implementation_plan) or 'unknown'} "
            f"tracks {int(implementation_plan.get('track_count') or 0)} "
            f"done {int(implementation_plan.get('done_count') or 0)} "
            f"in-progress {int(implementation_plan.get('in_progress_count') or 0)} "
            f"tasks {int(implementation_plan.get('task_count') or 0)} "
            f"next {int(implementation_plan.get('next_task_count') or 0)} "
            f"checks {int(implementation_plan.get('acceptance_check_count') or 0)} "
            f"sha {short_hash(implementation_plan.get('source_sha256'))}"
        )
        missing = implementation_plan.get("missing_markers") or []
        if missing:
            print(f"  missing markers: {', '.join(str(item) for item in missing)}")

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

workflow_validation = load_json_file(inputs.get("evidence_workflow_validation_report"))
if workflow_validation:
    validation_status = str(workflow_validation.get("status") or "unknown")
    workflow_status = str(workflow_validation.get("workflow_status") or "unknown")
    next_step = (
        workflow_validation.get("next_required_step")
        if isinstance(workflow_validation.get("next_required_step"), dict)
        else None
    )
    issues = workflow_validation.get("issues") if isinstance(workflow_validation.get("issues"), list) else []
    should_print_workflow = validation_status != "passed" or workflow_status != "passed" or next_step or issues
    if should_print_workflow:
        print()
        print("Workflow validation:")
        print(
            f"- status {validation_status}, workflow {workflow_status}, "
            f"steps {workflow_validation.get('step_count', 'unknown')}, "
            f"issues {workflow_validation.get('issue_count', len(issues))}"
        )
        if next_step:
            name = next_step.get("name") or "unknown"
            status = next_step.get("status") or "unknown"
            print(f"- next required step: {name} [{status}]")
            if next_step.get("desktop_action"):
                print(f"  app: {next_step.get('desktop_action')}")
            if next_step.get("command"):
                print(f"  command: {next_step.get('command')}")
            if next_step.get("bundle_path"):
                print(f"  bundle: {next_step.get('bundle_path')}")
            if next_step.get("expected_log"):
                print(f"  expected log: {next_step.get('expected_log')}")
            if next_step.get("output_dir"):
                print(f"  output: {next_step.get('output_dir')}")
            if next_step.get("runtime_status_path"):
                print(f"  runtime status: {next_step.get('runtime_status_path')}")
            if next_step.get("capture_command_after_bundle"):
                print(f"  after bundle: {next_step.get('capture_command_after_bundle')}")
            metadata_update_command = enriched_metadata_update_command(
                next_step.get("metadata_update_command"),
                next_field_condition,
            )
            if metadata_update_command:
                print(f"  metadata update: {metadata_update_command}")
        for issue in issues[:4]:
            print(f"- issue: {issue}")
        printed_missing_steps = set()
        for check in workflow_validation.get("checks") or []:
            if not isinstance(check, dict) or check.get("status") == "passed":
                continue
            details = check.get("details") if isinstance(check.get("details"), dict) else {}
            missing_steps = details.get("missing_steps")
            if not isinstance(missing_steps, list):
                missing_steps = check.get("missing_steps")
            if isinstance(missing_steps, list):
                new_missing_steps = [
                    str(step)
                    for step in missing_steps
                    if str(step) and str(step) not in printed_missing_steps
                ]
                if new_missing_steps:
                    for step in new_missing_steps[:6]:
                        printed_missing_steps.add(step)
                    print(f"- missing workflow steps: {', '.join(new_missing_steps[:6])}")
                    if len(new_missing_steps) > 6:
                        print(f"  ... {len(new_missing_steps) - 6} more")
            non_passed_steps = details.get("non_passed_steps")
            if not isinstance(non_passed_steps, list):
                non_passed_steps = check.get("non_passed_steps")
            if isinstance(non_passed_steps, list):
                for step in non_passed_steps[:4]:
                    if not isinstance(step, dict):
                        continue
                    step_name = step.get("name") or "unknown"
                    step_status = step.get("status") or "unknown"
                    print(f"- non-passing workflow step: {step_name} [{step_status}]")
                    if step.get("notes"):
                        print(f"  notes: {step.get('notes')}")
            missing_markers = details.get("missing_markers")
            if not isinstance(missing_markers, list):
                missing_markers = check.get("missing_markers")
            if isinstance(missing_markers, list) and missing_markers:
                marker_label = (
                    "missing final proof markers"
                    if check.get("name") == "final_proof_markers"
                    else "missing workflow markers"
                )
                marker_names = [str(marker) for marker in missing_markers if str(marker)]
                print(f"- {marker_label}: {', '.join(marker_names[:6])}")
                if len(marker_names) > 6:
                    print(f"  ... {len(marker_names) - 6} more")
        workflow_checks = [
            str(check.get("name") or "unknown")
            for check in workflow_validation.get("checks") or []
            if isinstance(check, dict) and check.get("status") != "passed"
        ]
        if validation_status != "passed" or workflow_status != "passed" or workflow_checks:
            print("- remediation: refresh the guided workflow proof after collecting or repairing the missing evidence.")
            print("  app: Module Setup > Evidence Workflow")
            print("  command: ./scripts/pi/run_autonomy_evidence_workflow.sh")
            if workflow_checks:
                print(f"  non-passing checks: {', '.join(workflow_checks[:6])}")

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
bench_subchecks = normalize_bench_subchecks(support_details.get("failed_or_degraded_checks"))
bench_inputs = [
    str(value)
    for value in support_details.get("expected_bench_inputs") or []
    if str(value)
]
support_bundle_command = str(support_details.get("support_bundle_command") or "")
bench_actions = [
    item
    for item in support_details.get("bench_evidence_actions") or []
    if isinstance(item, dict)
]
if bench_inputs or support_bundle_command or bench_actions:
    print()
    print("Bench evidence preview:")
    if bench_inputs:
        print("- support bundle should include:")
        for value in bench_inputs[:10]:
            print(f"  - {value}")
        if len(bench_inputs) > 10:
            print(f"  - ... {len(bench_inputs) - 10} more")
    if bench_actions:
        print("- suggested collection order:")
        max_bench_actions = 14
        for index, action in enumerate(bench_actions[:max_bench_actions], start=1):
            label = action.get("label") or "bench evidence step"
            print(f"  {index}. {label}")
            desktop_action = action.get("desktop_action")
            if desktop_action:
                print(f"     app: {desktop_action}")
            blocked_by = action.get("blocked_by")
            if blocked_by:
                print(f"     waits on: {blocked_by}")
            command = action.get("command")
            if command:
                print(f"     command: {command}")
            for label, key in (
                ("field", "field_condition"),
                ("bundle", "field_bundle"),
                ("expected log", "field_source_log"),
                ("output", "field_capture_output_dir"),
                ("runtime status", "field_runtime_status_path"),
                ("metadata update", "field_metadata_update_command"),
            ):
                value = action.get(key)
                if key == "field_metadata_update_command":
                    value = enriched_metadata_update_command(value, next_field_condition)
                if value:
                    print(f"     {label}: {value}")
        if len(bench_actions) > max_bench_actions:
            print(f"  - ... {len(bench_actions) - max_bench_actions} more")
    action_commands = {str(action.get("command") or "") for action in bench_actions}
    if support_bundle_command and support_bundle_command not in action_commands:
        print(f"- create or refresh support bundle after inputs exist: {support_bundle_command}")

if bench_subchecks:
    print()
    print("Bench readiness details:")
    for subcheck in bench_subchecks[:12]:
        name = subcheck["name"]
        status = subcheck["status"]
        message = subcheck["message"]
        print(f"- {name} [{status}]: {message}")
    if len(bench_subchecks) > 12:
        print(f"- ... {len(bench_subchecks) - 12} more")

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
        print_multiline_command(
            "  capture command:",
            next_field_condition.get("capture_command"),
            FIELD_COLLECTION_COMMAND_APP_ACTIONS["capture"],
        )
        print_multiline_command(
            "  metadata update command:",
            next_field_condition.get("metadata_update_command"),
            FIELD_COLLECTION_COMMAND_APP_ACTIONS["metadata_update"],
        )
        print_multiline_command(
            "  register command:",
            next_field_condition.get("register_command"),
            FIELD_COLLECTION_COMMAND_APP_ACTIONS["registration"],
        )
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
        print("- create or refresh the Pi field collection plan to get condition-specific capture, metadata-update, and registration commands.")

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
        print(f"   app: {command_app_hints.get(command) or 'Module Setup > Evidence Workflow'}")
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
            item = {
                key: action.get(key)
                for key in (
                    "title",
                    "desktop_action",
                    "command",
                    "field_condition",
                    "field_bundle",
                    "field_source_log",
                    "field_capture_output_dir",
                    "field_runtime_status_path",
                    "field_metadata_update_command",
                    "field_register_command",
                    "notes",
                )
                if action.get(key)
            }
            item["title"] = item.get("title") or phase.get("title") or phase.get("id") or "next action"
            phase_items.append(item)
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
next_actions = defer_support_bundle_actions(phase_commands or (report.get("next_actions") or []))
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
        desktop_action = action.get("desktop_action")
        if desktop_action:
            print(f"   app: {desktop_action}")
        print(f"   {command}")
        notes = action.get("notes")
        if notes:
            print(f"   notes: {notes}")
        for label, key in (
            ("field", "field_condition"),
            ("bundle", "field_bundle"),
            ("expected log", "field_source_log"),
            ("output", "field_capture_output_dir"),
            ("runtime status", "field_runtime_status_path"),
            ("metadata update", "field_metadata_update_command"),
            ):
                value = action.get(key)
                if key == "field_metadata_update_command":
                    value = enriched_metadata_update_command(value, next_field_condition)
                if value:
                    print(f"   {label}: {value}")
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
        desktop_action = action.get("desktop_action")
        if desktop_action:
            print(f"   app: {desktop_action}")
        waiting_on = action.get("waiting_on")
        if waiting_on:
            print(f"   waiting on: {waiting_on}")
        print(f"   {command}")
        notes = action.get("notes")
        if notes:
            print(f"   notes: {notes}")
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
