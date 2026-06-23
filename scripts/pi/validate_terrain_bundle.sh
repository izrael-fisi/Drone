#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
python_bin="$venv_python"
bundle="${VISION_NAV_BUNDLE:-$HOME/drone-data/map_bundles/mission_bundle}"
report="${VISION_NAV_TERRAIN_BUNDLE_VALIDATION:-$HOME/DroneTransfer/outgoing/replay-cases/bundle_validation_report.json}"

if [[ "$python_bin" == */* ]]; then
  if [[ ! -x "$python_bin" ]]; then
    echo "Missing Python venv: $python_bin" >&2
    echo "Run ./scripts/pi/bootstrap_pi5.sh first, then reboot, or set VISION_NAV_PYTHON." >&2
    exit 1
  fi
elif ! command -v "$python_bin" >/dev/null 2>&1; then
  echo "Python command not found: $python_bin" >&2
  echo "Run ./scripts/pi/bootstrap_pi5.sh first, then reboot, or set VISION_NAV_PYTHON." >&2
  exit 1
fi

set +e
PYTHONPATH="$repo_root/src" "$python_bin" -m vision_nav.terrain_bundle_validation \
  --bundle "$bundle" \
  --output "$report"
status=$?
set -e

if [[ -f "$report" ]]; then
  echo "__VISION_NAV_TERRAIN_BUNDLE_VALIDATION__=$report"
fi
exit "$status"
