#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
support_dir="${VISION_NAV_SUPPORT_OUTPUT_DIR:-$HOME/DroneTransfer/outgoing/support-bundles}"
support_bundle="${VISION_NAV_AUTONOMY_SUPPORT_BUNDLE:-}"
field_evidence_report="${VISION_NAV_FIELD_EVIDENCE_REPORT:-$HOME/DroneTransfer/outgoing/replay-cases/field_evidence_report.json}"
threshold_tuning_report="${VISION_NAV_THRESHOLD_TUNING_REPORT:-$HOME/DroneTransfer/outgoing/replay-cases/threshold_tuning_report.json}"
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

if [[ -f "$field_evidence_report" ]]; then
  args+=(--field-evidence-report "$field_evidence_report")
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
  support bundle: $support_bundle
  report:         $output_report

__VISION_NAV_AUTONOMY_REPORT__=$output_report
EOF

if [[ "$audit_status" -ne 0 ]]; then
  echo
  echo "Autonomy readiness is not passing yet. This is expected until PX4 receiver proof, real field evidence, method benchmarks, and threshold tuning are present." >&2
  if [[ "$allow_failed" != "1" && "$allow_failed" != "true" ]]; then
    exit "$audit_status"
  fi
fi
