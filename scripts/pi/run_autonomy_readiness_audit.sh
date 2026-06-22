#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
support_dir="${VISION_NAV_SUPPORT_OUTPUT_DIR:-$HOME/DroneTransfer/outgoing/support-bundles}"
feature_benchmark_dir="${VISION_NAV_FEATURE_METHOD_BENCHMARK:-$HOME/DroneTransfer/outgoing/feature-method-bench}"
support_bundle="${VISION_NAV_AUTONOMY_SUPPORT_BUNDLE:-}"
field_evidence_report="${VISION_NAV_FIELD_EVIDENCE_REPORT:-$HOME/DroneTransfer/outgoing/replay-cases/field_evidence_report.json}"
field_collection_plan="${VISION_NAV_FIELD_COLLECTION_PLAN:-$HOME/DroneTransfer/outgoing/replay-cases/field_collection_plan.json}"
feature_method_benchmark_report="${VISION_NAV_FEATURE_METHOD_BENCHMARK_REPORT:-}"
threshold_tuning_report="${VISION_NAV_THRESHOLD_TUNING_REPORT:-$HOME/DroneTransfer/outgoing/replay-cases/threshold_tuning_report.json}"
rosbag_export_validation="${VISION_NAV_ROSBAG_EXPORT_VALIDATION:-$HOME/DroneTransfer/outgoing/terrain-match/rosbag-jsonl-validation.json}"
rosbag2_cli_review="${VISION_NAV_ROSBAG2_CLI_REVIEW:-$HOME/DroneTransfer/outgoing/terrain-match/rosbag2-cli-review.json}"
evidence_workflow_report="${VISION_NAV_EVIDENCE_WORKFLOW_REPORT:-$HOME/DroneTransfer/outgoing/replay-cases/autonomy-evidence-workflow/autonomy_evidence_workflow.json}"
evidence_workflow_validation_report="${VISION_NAV_EVIDENCE_WORKFLOW_VALIDATION:-${evidence_workflow_report%.json}.validation.json}"
evidence_workflow_log_archive="${VISION_NAV_EVIDENCE_WORKFLOW_LOG_ARCHIVE:-${evidence_workflow_report%.json}.logs.tar.gz}"
px4_sitl_session="${VISION_NAV_PX4_SITL_SESSION:-}"
px4_sitl_report="${VISION_NAV_PX4_SITL_REPORT:-}"
px4_sitl_prereqs="${VISION_NAV_PX4_SITL_PREREQS:-}"
output_report="${VISION_NAV_AUTONOMY_READINESS_REPORT:-$HOME/DroneTransfer/outgoing/replay-cases/autonomy_readiness_report.json}"
output_handoff="${VISION_NAV_AUTONOMY_HANDOFF:-${output_report%.json}.md}"
output_package="${VISION_NAV_AUTONOMY_EVIDENCE_PACKAGE:-${output_report%.json}.evidence.zip}"
allow_failed="${VISION_NAV_AUTONOMY_ALLOW_FAILED:-0}"

if [[ "$venv_python" == */* ]]; then
  if [[ ! -x "$venv_python" ]]; then
    echo "Missing Python venv: $venv_python" >&2
    echo "Run ./scripts/pi/bootstrap_pi5.sh first, then reboot." >&2
    exit 1
  fi
elif ! command -v "$venv_python" >/dev/null 2>&1; then
  echo "Python command not found: $venv_python" >&2
  echo "Run ./scripts/pi/bootstrap_pi5.sh first, then reboot, or set VISION_NAV_PYTHON." >&2
  exit 1
fi

if [[ -z "$support_bundle" ]]; then
  support_bundle="$(ls -t "$support_dir"/*.zip 2>/dev/null | head -n 1 || true)"
fi

if [[ -z "$feature_method_benchmark_report" ]]; then
  feature_method_benchmark_report="$(ls -t "$feature_benchmark_dir"/*.json 2>/dev/null | head -n 1 || true)"
fi

if [[ -z "$px4_sitl_session" && -f "$HOME/px4-sitl-evidence/px4_sitl_evidence_session.json" ]]; then
  px4_sitl_session="$HOME/px4-sitl-evidence"
fi

if [[ -z "$px4_sitl_report" && -f "$HOME/px4-sitl-evidence/receiver_evidence.json" ]]; then
  px4_sitl_report="$HOME/px4-sitl-evidence/receiver_evidence.json"
fi

if [[ -z "$px4_sitl_prereqs" && -f "$HOME/px4-sitl-evidence/px4_sitl_capture_prereqs.json" ]]; then
  px4_sitl_prereqs="$HOME/px4-sitl-evidence/px4_sitl_capture_prereqs.json"
fi

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
  echo "No support bundle ZIP found; the readiness audit will fail closed and record the missing proof gate." >&2
  echo "Run ./scripts/pi/create_support_bundle.sh later, or set VISION_NAV_AUTONOMY_SUPPORT_BUNDLE." >&2
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
PYTHONPATH="$repo_root/src" "$venv_python" "${args[@]}"
audit_status=$?
set -e

if [[ -f "$output_report" ]]; then
  PYTHONPATH="$repo_root/src" "$venv_python" -m vision_nav.autonomy_handoff \
    --report "$output_report" \
    --output "$output_handoff"
  PYTHONPATH="$repo_root/src" "$venv_python" -m vision_nav.autonomy_evidence_package \
    --report "$output_report" \
    --handoff "$output_handoff" \
    --output "$output_package"
fi

cat <<EOF

Autonomy readiness audit outputs:
  support bundle:            $([[ -f "$support_bundle" ]] && printf '%s' "$support_bundle" || printf 'not found')
  px4 sitl session:          ${px4_sitl_session:-not found}
  px4 sitl report:           $([[ -f "$px4_sitl_report" ]] && printf '%s' "$px4_sitl_report" || printf 'not found')
  px4 prereq report:         $([[ -f "$px4_sitl_prereqs" ]] && printf '%s' "$px4_sitl_prereqs" || printf 'not found')
  field evidence report:     $([[ -f "$field_evidence_report" ]] && printf '%s' "$field_evidence_report" || printf 'not found')
  field collection plan:     $([[ -f "$field_collection_plan" ]] && printf '%s' "$field_collection_plan" || printf 'not found')
  feature benchmark report:  $([[ -f "$feature_method_benchmark_report" ]] && printf '%s' "$feature_method_benchmark_report" || printf 'not found')
  threshold tuning report:   $([[ -f "$threshold_tuning_report" ]] && printf '%s' "$threshold_tuning_report" || printf 'not found')
  rosbag validation report:  $([[ -f "$rosbag_export_validation" ]] && printf '%s' "$rosbag_export_validation" || printf 'not found')
  rosbag2 cli review:        $([[ -f "$rosbag2_cli_review" ]] && printf '%s' "$rosbag2_cli_review" || printf 'not found')
  evidence workflow report:  $([[ -f "$evidence_workflow_report" ]] && printf '%s' "$evidence_workflow_report" || printf 'not found')
  workflow validation:       $([[ -f "$evidence_workflow_validation_report" ]] && printf '%s' "$evidence_workflow_validation_report" || printf 'not found')
  workflow log archive:      $([[ -f "$evidence_workflow_log_archive" ]] && printf '%s' "$evidence_workflow_log_archive" || printf 'not found')
  report:                    $output_report
  handoff:                   $output_handoff
  evidence package:          $output_package

__VISION_NAV_AUTONOMY_REPORT__=$output_report
__VISION_NAV_AUTONOMY_HANDOFF__=$output_handoff
__VISION_NAV_AUTONOMY_EVIDENCE_PACKAGE__=$output_package
EOF

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
  echo "Autonomy readiness is not passing yet. This is expected until PX4 receiver proof, real field evidence, method benchmarks, and threshold tuning are present." >&2
  if [[ "$allow_failed" != "1" && "$allow_failed" != "true" ]]; then
    exit "$audit_status"
  fi
fi
