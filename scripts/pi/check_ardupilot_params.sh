#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
python_bin="$venv_python"
params_path="${VISION_NAV_ARDUPILOT_PARAMS:-${1:-}}"

if [[ ! -x "$python_bin" ]]; then
  python_bin="$(command -v python3 || true)"
fi
if [[ -z "$python_bin" ]]; then
  echo "[FAIL] No Python interpreter found." >&2
  exit 1
fi

if [[ -z "$params_path" ]]; then
  cat >&2 <<EOF
Usage:
  VISION_NAV_ARDUPILOT_PARAMS=/path/to/ardupilot.params ./scripts/pi/check_ardupilot_params.sh

Export ArduPilot parameters from Mission Planner, MAVProxy, or another ground
station first. This script only checks the exported file; it does not modify
flight-controller parameters.
EOF
  exit 2
fi

args=(
  -m vision_nav.ardupilot_params
  --params "$params_path"
  --source-set "${VISION_NAV_ARDUPILOT_SOURCE_SET:-1}"
)

if [[ "${VISION_NAV_GNSS_DENIED_CHECK:-0}" == "1" ]]; then
  args+=(--gnss-denied)
fi
if [[ "${VISION_NAV_VISION_HEIGHT_VALID:-0}" == "1" ]]; then
  args+=(--vision-height-valid)
fi
if [[ "${VISION_NAV_VISION_VELOCITY_VALID:-0}" == "1" ]]; then
  args+=(--vision-velocity-valid)
fi
if [[ "${VISION_NAV_VISION_YAW_VALID:-0}" == "1" ]]; then
  args+=(--vision-yaw-valid)
fi
if [[ "${VISION_NAV_EXTRINSICS_MEASURED:-0}" == "1" ]]; then
  args+=(--extrinsics-measured)
fi
if [[ "${VISION_NAV_REQUIRE_SOURCE_SWITCH:-0}" == "1" ]]; then
  args+=(--require-source-switch)
fi

PYTHONPATH="$repo_root/src" "$python_bin" "${args[@]}"
