#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
python_bin="$venv_python"
bundle="${VISION_NAV_BUNDLE:-$HOME/drone-data/map_bundles/mission_bundle}"
plan="${VISION_NAV_MISSION_PLAN:-}"
output="${VISION_NAV_GNSS_DENIED_PLAN_CHECK:-$HOME/DroneTransfer/outgoing/replay-cases/gnss_denied_plan_check.json}"

if [[ ! -x "$python_bin" ]]; then
  python_bin="$(command -v python3 || true)"
fi
if [[ -z "$python_bin" ]]; then
  echo "[FAIL] No Python interpreter found." >&2
  exit 1
fi

args=(-m vision_nav.gnss_denied_plan)
if [[ -n "$plan" ]]; then
  args+=(--plan "$plan")
else
  args+=(--bundle "$bundle")
fi

mkdir -p "$(dirname "$output")"
args+=(--output "$output")

PYTHONPATH="$repo_root/src" "$python_bin" "${args[@]}"
