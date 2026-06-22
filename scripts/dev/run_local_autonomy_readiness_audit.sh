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
evidence_workflow_report="${VISION_NAV_EVIDENCE_WORKFLOW_REPORT:-}"
evidence_workflow_validation_report="${VISION_NAV_EVIDENCE_WORKFLOW_VALIDATION:-}"
px4_sitl_session="${VISION_NAV_PX4_SITL_SESSION:-}"
px4_sitl_report="${VISION_NAV_PX4_SITL_REPORT:-}"
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

if [[ -z "$evidence_workflow_report" ]]; then
  evidence_workflow_report="$(first_existing_workflow_report || true)"
fi

if [[ -z "$evidence_workflow_validation_report" ]]; then
  evidence_workflow_validation_report="$(first_existing_workflow_validation_report || true)"
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
fi

if [[ -n "$px4_sitl_session" && -f "$px4_sitl_session/px4_sitl_evidence_session.json" ]]; then
  args+=(--px4-sitl-session "$px4_sitl_session")
elif [[ -f "$px4_sitl_report" ]]; then
  args+=(--px4-sitl-report "$px4_sitl_report")
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

if [[ -f "$evidence_workflow_report" ]]; then
  args+=(--evidence-workflow-report "$evidence_workflow_report")
fi

if [[ -f "$evidence_workflow_validation_report" ]]; then
  args+=(--evidence-workflow-validation-report "$evidence_workflow_validation_report")
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
  support bundle:          ${support_bundle:-not found}
  px4 sitl session:        ${px4_sitl_session:-not found}
  px4 sitl report:         $([[ -f "$px4_sitl_report" ]] && printf '%s' "$px4_sitl_report" || printf 'not found')
  field evidence report:   $([[ -f "$field_evidence_report" ]] && printf '%s' "$field_evidence_report" || printf 'not found')
  field collection plan:   $([[ -f "$field_collection_plan" ]] && printf '%s' "$field_collection_plan" || printf 'not found')
  feature benchmark report: $([[ -f "$feature_method_benchmark_report" ]] && printf '%s' "$feature_method_benchmark_report" || printf 'not found')
  threshold tuning report: $([[ -f "$threshold_tuning_report" ]] && printf '%s' "$threshold_tuning_report" || printf 'not found')
  evidence workflow report: $([[ -f "$evidence_workflow_report" ]] && printf '%s' "$evidence_workflow_report" || printf 'not found')
  workflow validation:     $([[ -f "$evidence_workflow_validation_report" ]] && printf '%s' "$evidence_workflow_validation_report" || printf 'not found')
  output report:           $output_report
  output handoff:          $output_handoff
  evidence package:        $output_package

__VISION_NAV_AUTONOMY_REPORT__=$output_report
__VISION_NAV_AUTONOMY_HANDOFF__=$output_handoff
__VISION_NAV_AUTONOMY_EVIDENCE_PACKAGE__=$output_package
EOF

if [[ -f "$px4_sitl_report" ]]; then
  echo "__VISION_NAV_PX4_SITL_REPORT__=$px4_sitl_report"
fi

if [[ -f "$field_collection_plan" ]]; then
  echo "__VISION_NAV_FIELD_COLLECTION_PLAN__=$field_collection_plan"
  if [[ -f "${field_collection_plan%.json}.md" ]]; then
    echo "__VISION_NAV_FIELD_COLLECTION_PLAN_MD__=${field_collection_plan%.json}.md"
  fi
fi

if [[ -f "$evidence_workflow_report" ]]; then
  echo "__VISION_NAV_EVIDENCE_WORKFLOW_REPORT__=$evidence_workflow_report"
fi

if [[ -f "$evidence_workflow_validation_report" ]]; then
  echo "__VISION_NAV_EVIDENCE_WORKFLOW_VALIDATION__=$evidence_workflow_validation_report"
fi

if [[ "$audit_status" -ne 0 ]]; then
  echo
  echo "Local autonomy readiness is not passing yet. Review the report for missing proof artifacts." >&2
  if [[ "$allow_failed" != "1" && "$allow_failed" != "true" ]]; then
    exit "$audit_status"
  fi
fi
