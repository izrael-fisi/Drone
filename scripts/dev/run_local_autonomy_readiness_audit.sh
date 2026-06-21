#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${VISION_NAV_PYTHON:-python3}"
download_root="${VISION_NAV_DESKTOP_TRANSFER_FROM_PI:-$HOME/DroneTransfer/from-pi}"
support_dir="${VISION_NAV_LOCAL_SUPPORT_DIR:-$download_root/support-bundles}"
replay_dir="${VISION_NAV_LOCAL_REPLAY_DIR:-$download_root/replay-cases}"
support_bundle="${VISION_NAV_AUTONOMY_SUPPORT_BUNDLE:-}"
field_evidence_report="${VISION_NAV_FIELD_EVIDENCE_REPORT:-$replay_dir/field_evidence_report.json}"
threshold_tuning_report="${VISION_NAV_THRESHOLD_TUNING_REPORT:-$replay_dir/threshold_tuning_report.json}"
px4_sitl_session="${VISION_NAV_PX4_SITL_SESSION:-}"
output_report="${VISION_NAV_AUTONOMY_READINESS_REPORT:-$replay_dir/autonomy_readiness_report.json}"
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

if [[ -z "$support_bundle" ]]; then
  support_bundle="$(latest_glob "$support_dir/*.zip")"
fi

if [[ -z "$px4_sitl_session" ]]; then
  px4_sitl_session="$(first_existing_px4_session || true)"
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
fi

if [[ -f "$field_evidence_report" ]]; then
  args+=(--field-evidence-report "$field_evidence_report")
fi

if [[ -f "$threshold_tuning_report" ]]; then
  args+=(--threshold-tuning-report "$threshold_tuning_report")
fi

set +e
PYTHONPATH="$repo_root/src" "$python_bin" "${args[@]}"
audit_status=$?
set -e

cat <<EOF

Local autonomy readiness audit inputs:
  support bundle:          ${support_bundle:-not found}
  px4 sitl session:        ${px4_sitl_session:-not found}
  field evidence report:   $([[ -f "$field_evidence_report" ]] && printf '%s' "$field_evidence_report" || printf 'not found')
  threshold tuning report: $([[ -f "$threshold_tuning_report" ]] && printf '%s' "$threshold_tuning_report" || printf 'not found')
  output report:           $output_report

__VISION_NAV_AUTONOMY_REPORT__=$output_report
EOF

if [[ "$audit_status" -ne 0 ]]; then
  echo
  echo "Local autonomy readiness is not passing yet. Review the report for missing proof artifacts." >&2
  if [[ "$allow_failed" != "1" && "$allow_failed" != "true" ]]; then
    exit "$audit_status"
  fi
fi
