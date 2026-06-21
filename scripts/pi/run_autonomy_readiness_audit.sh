#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
support_dir="${VISION_NAV_SUPPORT_OUTPUT_DIR:-$HOME/DroneTransfer/outgoing/support-bundles}"
feature_benchmark_dir="${VISION_NAV_FEATURE_METHOD_BENCHMARK:-$HOME/DroneTransfer/outgoing/feature-method-bench}"
support_bundle="${VISION_NAV_AUTONOMY_SUPPORT_BUNDLE:-}"
field_evidence_report="${VISION_NAV_FIELD_EVIDENCE_REPORT:-$HOME/DroneTransfer/outgoing/replay-cases/field_evidence_report.json}"
feature_method_benchmark_report="${VISION_NAV_FEATURE_METHOD_BENCHMARK_REPORT:-}"
threshold_tuning_report="${VISION_NAV_THRESHOLD_TUNING_REPORT:-$HOME/DroneTransfer/outgoing/replay-cases/threshold_tuning_report.json}"
px4_sitl_session="${VISION_NAV_PX4_SITL_SESSION:-}"
px4_sitl_report="${VISION_NAV_PX4_SITL_REPORT:-}"
output_report="${VISION_NAV_AUTONOMY_READINESS_REPORT:-$HOME/DroneTransfer/outgoing/replay-cases/autonomy_readiness_report.json}"
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

if [[ -z "$support_bundle" || ! -f "$support_bundle" ]]; then
  echo "Missing support bundle ZIP for autonomy readiness audit." >&2
  echo "Run ./scripts/pi/create_support_bundle.sh first, or set VISION_NAV_AUTONOMY_SUPPORT_BUNDLE." >&2
  exit 1
fi

mkdir -p "$(dirname "$output_report")"

args=(
  -m vision_nav.autonomy_readiness
  --research-doc "$repo_root/docs/autonomy-ground-control-research.md"
  --implementation-plan "$repo_root/docs/autonomy-ground-control-implementation-plan.md"
  --support-bundle "$support_bundle"
  --output "$output_report"
)

if [[ -n "$px4_sitl_session" && -f "$px4_sitl_session/px4_sitl_evidence_session.json" ]]; then
  args+=(--px4-sitl-session "$px4_sitl_session")
elif [[ -f "$px4_sitl_report" ]]; then
  args+=(--px4-sitl-report "$px4_sitl_report")
fi

if [[ -f "$field_evidence_report" ]]; then
  args+=(--field-evidence-report "$field_evidence_report")
fi

if [[ -f "$feature_method_benchmark_report" ]]; then
  args+=(--feature-method-benchmark-report "$feature_method_benchmark_report")
fi

if [[ -f "$threshold_tuning_report" ]]; then
  args+=(--threshold-tuning-report "$threshold_tuning_report")
fi

set +e
PYTHONPATH="$repo_root/src" "$venv_python" "${args[@]}"
audit_status=$?
set -e

cat <<EOF

Autonomy readiness audit outputs:
  support bundle:            $support_bundle
  px4 sitl session:          ${px4_sitl_session:-not found}
  px4 sitl report:           $([[ -f "$px4_sitl_report" ]] && printf '%s' "$px4_sitl_report" || printf 'not found')
  field evidence report:     $([[ -f "$field_evidence_report" ]] && printf '%s' "$field_evidence_report" || printf 'not found')
  feature benchmark report:  $([[ -f "$feature_method_benchmark_report" ]] && printf '%s' "$feature_method_benchmark_report" || printf 'not found')
  threshold tuning report:   $([[ -f "$threshold_tuning_report" ]] && printf '%s' "$threshold_tuning_report" || printf 'not found')
  report:                    $output_report

__VISION_NAV_AUTONOMY_REPORT__=$output_report
EOF

if [[ -f "$px4_sitl_report" ]]; then
  echo "__VISION_NAV_PX4_SITL_REPORT__=$px4_sitl_report"
fi

if [[ "$audit_status" -ne 0 ]]; then
  echo
  echo "Autonomy readiness is not passing yet. This is expected until PX4 receiver proof, real field evidence, method benchmarks, and threshold tuning are present." >&2
  if [[ "$allow_failed" != "1" && "$allow_failed" != "true" ]]; then
    exit "$audit_status"
  fi
fi
