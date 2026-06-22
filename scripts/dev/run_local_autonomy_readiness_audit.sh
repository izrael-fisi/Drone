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
output_report="${VISION_NAV_AUTONOMY_READINESS_REPORT:-$replay_dir/autonomy_readiness_report.json}"
output_handoff="${VISION_NAV_AUTONOMY_HANDOFF:-${output_report%.json}.md}"
output_package="${VISION_NAV_AUTONOMY_EVIDENCE_PACKAGE:-${output_report%.json}.evidence.zip}"
allow_failed="${VISION_NAV_AUTONOMY_ALLOW_FAILED:-0}"

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

print_workflow_validation_summary() {
  if [[ ! -f "$evidence_workflow_validation_report" ]]; then
    return 0
  fi
  PYTHONPATH="$repo_root/src" "$python_bin" - "$evidence_workflow_validation_report" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

from vision_nav.autonomy_evidence_workflow import workflow_validation_detail_lines

path = Path(sys.argv[1]).expanduser()
try:
    report = json.loads(path.read_text(encoding="utf-8"))
except Exception as exc:
    print()
    print("Workflow validation summary:")
    print(f"- could not parse {path}: {exc}")
    raise SystemExit(0)

validation_status = str(report.get("status") or "unknown")
workflow_status = str(report.get("workflow_status") or "unknown")
issues = report.get("issues") if isinstance(report.get("issues"), list) else []
next_step = report.get("next_required_step") if isinstance(report.get("next_required_step"), dict) else None
checks = [
    str(check.get("name") or "unknown")
    for check in report.get("checks") or []
    if isinstance(check, dict) and check.get("status") != "passed"
]

if validation_status == "passed" and workflow_status == "passed" and not issues and not next_step and not checks:
    raise SystemExit(0)

print()
print("Workflow validation summary:")
print(
    f"- status {validation_status}, workflow {workflow_status}, "
    f"steps {report.get('step_count', 'unknown')}, issues {report.get('issue_count', len(issues))}"
)
if next_step:
    name = next_step.get("name") or "unknown"
    status = next_step.get("status") or "unknown"
    print(f"- next required step: {name} [{status}]")
    if next_step.get("desktop_action"):
        print(f"  app: {next_step.get('desktop_action')}")
    if next_step.get("command"):
        print(f"  command: {next_step.get('command')}")
for issue in issues[:4]:
    print(f"- issue: {issue}")
detail_lines = workflow_validation_detail_lines(report)
if detail_lines:
    print("Details:")
    for line in detail_lines:
        print(line)
if checks:
    print(f"- non-passing checks: {', '.join(checks[:6])}")
PY
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

mkdir -p "$(dirname "$output_report")"

args=(
  -m vision_nav.autonomy_readiness
  --research-doc "$repo_root/docs/autonomy-ground-control-research.md"
  --implementation-plan "$repo_root/docs/autonomy-ground-control-implementation-plan.md"
  --output "$output_report"
)

if [[ -n "$support_bundle" && -f "$support_bundle" ]]; then
  args+=(--support-bundle "$support_bundle")
else
  echo "No support bundle ZIP found; the local readiness audit will fail closed and record the missing proof gate." >&2
  echo "Run Module Setup > Bench Report, copy a support bundle into $support_dir, or set VISION_NAV_AUTONOMY_SUPPORT_BUNDLE." >&2
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
PYTHONPATH="$repo_root/src" "$python_bin" "${args[@]}"
audit_status=$?
set -e

if [[ -f "$output_report" ]]; then
  PYTHONPATH="$repo_root/src" "$python_bin" -m vision_nav.autonomy_handoff \
    --report "$output_report" \
    --output "$output_handoff"
  PYTHONPATH="$repo_root/src" "$python_bin" -m vision_nav.autonomy_evidence_package \
    --report "$output_report" \
    --handoff "$output_handoff" \
    --output "$output_package"
fi

cat <<EOF

Local autonomy readiness audit inputs:
  support bundle:          $([[ -f "$support_bundle" ]] && printf '%s' "$support_bundle" || printf 'not found')
  px4 sitl session:        ${px4_sitl_session:-not found}
  px4 sitl report:         $([[ -f "$px4_sitl_report" ]] && printf '%s' "$px4_sitl_report" || printf 'not found')
  px4 prereq report:       $([[ -f "$px4_sitl_prereqs" ]] && printf '%s' "$px4_sitl_prereqs" || printf 'not found')
  field evidence report:   $([[ -f "$field_evidence_report" ]] && printf '%s' "$field_evidence_report" || printf 'not found')
  field collection plan:   $([[ -f "$field_collection_plan" ]] && printf '%s' "$field_collection_plan" || printf 'not found')
  feature benchmark report: $([[ -f "$feature_method_benchmark_report" ]] && printf '%s' "$feature_method_benchmark_report" || printf 'not found')
  threshold tuning report: $([[ -f "$threshold_tuning_report" ]] && printf '%s' "$threshold_tuning_report" || printf 'not found')
  rosbag validation report: $([[ -f "$rosbag_export_validation" ]] && printf '%s' "$rosbag_export_validation" || printf 'not found')
  rosbag2 cli review:     $([[ -f "$rosbag2_cli_review" ]] && printf '%s' "$rosbag2_cli_review" || printf 'not found')
  evidence workflow report: $([[ -f "$evidence_workflow_report" ]] && printf '%s' "$evidence_workflow_report" || printf 'not found')
  workflow validation:     $([[ -f "$evidence_workflow_validation_report" ]] && printf '%s' "$evidence_workflow_validation_report" || printf 'not found')
  workflow log archive:    $([[ -f "$evidence_workflow_log_archive" ]] && printf '%s' "$evidence_workflow_log_archive" || printf 'not found')
  output report:           $output_report
  output handoff:          $output_handoff
  evidence package:        $output_package

__VISION_NAV_AUTONOMY_REPORT__=$output_report
__VISION_NAV_AUTONOMY_HANDOFF__=$output_handoff
__VISION_NAV_AUTONOMY_EVIDENCE_PACKAGE__=$output_package
EOF

print_workflow_validation_summary

if [[ -f "$px4_sitl_report" ]]; then
  echo "__VISION_NAV_PX4_SITL_REPORT__=$px4_sitl_report"
fi

if [[ -f "$px4_sitl_prereqs" ]]; then
  echo "__VISION_NAV_PX4_SITL_PREREQS__=$px4_sitl_prereqs"
fi

if [[ -f "$field_evidence_report" ]]; then
  echo "__VISION_NAV_FIELD_EVIDENCE_REPORT__=$field_evidence_report"
fi

if [[ -f "$field_collection_plan" ]]; then
  echo "__VISION_NAV_FIELD_COLLECTION_PLAN__=$field_collection_plan"
  if [[ -f "${field_collection_plan%.json}.md" ]]; then
    echo "__VISION_NAV_FIELD_COLLECTION_PLAN_MD__=${field_collection_plan%.json}.md"
  fi
fi

if [[ -f "$feature_method_benchmark_report" ]]; then
  echo "__VISION_NAV_FEATURE_METHOD_REPORT__=$feature_method_benchmark_report"
fi

if [[ -f "$threshold_tuning_report" ]]; then
  echo "__VISION_NAV_THRESHOLD_REPORT__=$threshold_tuning_report"
fi

if [[ -f "$rosbag_export_validation" ]]; then
  echo "__VISION_NAV_ROSBAG_EXPORT_VALIDATION__=$rosbag_export_validation"
fi

if [[ -f "$rosbag2_cli_review" ]]; then
  echo "__VISION_NAV_ROSBAG2_CLI_REVIEW__=$rosbag2_cli_review"
fi

if [[ -f "$evidence_workflow_report" ]]; then
  echo "__VISION_NAV_EVIDENCE_WORKFLOW_REPORT__=$evidence_workflow_report"
fi

if [[ -f "$evidence_workflow_validation_report" ]]; then
  echo "__VISION_NAV_EVIDENCE_WORKFLOW_VALIDATION__=$evidence_workflow_validation_report"
fi

if [[ -f "$evidence_workflow_log_archive" ]]; then
  echo "__VISION_NAV_EVIDENCE_WORKFLOW_LOGS__=$evidence_workflow_log_archive"
fi

if [[ "$audit_status" -ne 0 ]]; then
  echo
  echo "Local autonomy readiness is not passing yet. Review the report for missing proof artifacts." >&2
  if [[ "$allow_failed" != "1" && "$allow_failed" != "true" ]]; then
    exit "$audit_status"
  fi
fi
