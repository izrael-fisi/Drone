#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
manifest="${VISION_NAV_FIELD_MANIFEST:-$HOME/DroneTransfer/outgoing/replay-cases/field_manifest.json}"
report="${VISION_NAV_THRESHOLD_TUNING_REPORT:-$HOME/DroneTransfer/outgoing/replay-cases/threshold_tuning_report.json}"
case_report_dir="${VISION_NAV_THRESHOLD_CASE_REPORT_DIR:-$HOME/DroneTransfer/outgoing/replay-cases/threshold_tuning_cases}"
allow_synthetic="${VISION_NAV_THRESHOLD_ALLOW_SYNTHETIC:-0}"
allow_failed="${VISION_NAV_THRESHOLD_ALLOW_FAILED:-0}"

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

if [[ ! -f "$manifest" ]]; then
  echo "Missing field replay manifest: $manifest" >&2
  echo "Register field cases first with ./scripts/pi/register_field_replay_case.sh." >&2
  exit 1
fi

mkdir -p "$(dirname "$report")" "$case_report_dir"

args=(
  -m vision_nav.threshold_tuning
  --manifest "$manifest"
  --output "$report"
  --case-output-dir "$case_report_dir"
)

if [[ "$allow_synthetic" == "1" || "$allow_synthetic" == "true" ]]; then
  args+=(--allow-synthetic)
fi

set +e
PYTHONPATH="$repo_root/src" "$venv_python" "${args[@]}"
tuning_status=$?
set -e

cat <<EOF

Threshold tuning outputs:
  manifest: $manifest
  report:   $report
  cases:    $case_report_dir

__VISION_NAV_THRESHOLD_REPORT__=$report
EOF

if [[ "$tuning_status" -ne 0 ]]; then
  echo
  echo "Threshold tuning is not passing yet. This is expected until full real field coverage and replay gates pass." >&2
  if [[ "$allow_failed" != "1" && "$allow_failed" != "true" ]]; then
    exit "$tuning_status"
  fi
fi
