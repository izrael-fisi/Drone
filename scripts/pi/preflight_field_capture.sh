#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
plan="${VISION_NAV_FIELD_COLLECTION_PLAN:-$HOME/DroneTransfer/outgoing/replay-cases/field_collection_plan.json}"
condition="${VISION_NAV_FIELD_CONDITION:-}"
output="${VISION_NAV_FIELD_CAPTURE_PREFLIGHT:-$HOME/DroneTransfer/outgoing/replay-cases/field_capture_preflight.json}"

usage() {
  cat >&2 <<EOF
Usage:
  ./scripts/pi/preflight_field_capture.sh

Checks whether the next pending field collection condition is ready for capture.
It does not create field evidence or mark readiness complete.

Common optional overrides:
  VISION_NAV_FIELD_COLLECTION_PLAN     Default: $plan
  VISION_NAV_FIELD_CONDITION           Optional condition key; defaults to plan next_condition
  VISION_NAV_FIELD_CAPTURE_PREFLIGHT   Default: $output
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

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

mkdir -p "$(dirname "$output")"

condition_args=()
if [[ -n "$condition" ]]; then
  condition_args=(--condition "$condition")
fi

PYTHONPATH="$repo_root/src" "$venv_python" -m vision_nav.field_capture_preflight \
  --plan "$plan" \
  "${condition_args[@]}" \
  --repo-root "$repo_root" \
  --output "$output"
