#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
bundle="${VISION_NAV_BUNDLE:-$HOME/drone-data/map_bundles/mission_bundle}"

if [[ ! -x "$venv_python" ]]; then
  echo "Missing Python venv: $venv_python" >&2
  echo "Run ./scripts/pi/bootstrap_pi5.sh first, then reboot." >&2
  exit 1
fi

PYTHONPATH="$repo_root/src" "$venv_python" -m vision_nav.validate_map_bundle --bundle "$bundle" --require-features
PYTHONPATH="$repo_root/src" "$venv_python" -m vision_nav.terrain_bundle --bundle "$bundle"
