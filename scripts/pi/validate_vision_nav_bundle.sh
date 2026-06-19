#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
bundle="${VISION_NAV_BUNDLE:-$HOME/drone-data/map_bundles/mission_bundle}"
require_features="${VISION_NAV_REQUIRE_FEATURES:-0}"
require_calibration="${VISION_NAV_REQUIRE_CALIBRATION:-1}"
require_checksums="${VISION_NAV_REQUIRE_CHECKSUMS:-0}"

if [[ ! -x "$venv_python" ]]; then
  echo "Missing Python venv: $venv_python" >&2
  echo "Run ./scripts/pi/bootstrap_pi5.sh first, then reboot." >&2
  exit 1
fi

args=(--bundle "$bundle")
if [[ "$require_features" == "1" ]]; then
  args+=(--require-features)
fi
if [[ "$require_calibration" == "1" ]]; then
  args+=(--require-calibration)
fi
if [[ "$require_checksums" == "1" ]]; then
  args+=(--require-checksums)
fi

PYTHONPATH="$repo_root/src" "$venv_python" -m vision_nav.validate_map_bundle "${args[@]}"
