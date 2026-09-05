#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
map_release="${1:-${VISION_NAV_MAP_RELEASE:-$HOME/drone-data/map_releases/active/map_release.json}}"
output="${VISION_NAV_MAP_ASSET_VALIDATION_OUTPUT:-}"

if [[ ! -x "$venv_python" ]]; then
  echo "Missing Python venv: $venv_python" >&2
  exit 1
fi

args=( -m vision_nav.neural.map_asset_validation --map-release "$map_release" )
if [[ -n "$output" ]]; then
  args+=(--output "$output")
fi
PYTHONPATH="$repo_root/src" "$venv_python" "${args[@]}"
