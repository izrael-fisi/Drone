#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
python_bin="$venv_python"
bundle="${VISION_NAV_BUNDLE:-$HOME/drone-data/map_bundles/mission_bundle}"

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

PYTHONPATH="$repo_root/src" "$python_bin" -m vision_nav.validate_map_bundle --bundle "$bundle" --require-features
PYTHONPATH="$repo_root/src" "$python_bin" -m vision_nav.terrain_bundle --bundle "$bundle"
