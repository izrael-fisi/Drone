#!/usr/bin/env bash
set -euo pipefail

# Read-only gate before the recorder creates a synchronized training session.
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
prop_off_report="${VISION_NAV_PROP_OFF_PREFLIGHT_REPORT:-}"
map_release="${VISION_NAV_MAP_RELEASE:-$HOME/drone-data/map_releases/active/map_release.json}"
reference_map="${VISION_NAV_REFERENCE_MAP:-}"
calibration_id="${VISION_NAV_CALIBRATION_ID:-}"
capture_root="${VISION_NAV_TRAINING_CAPTURE_ROOT:-$HOME/drone-data/captures}"
session_name="${VISION_NAV_TRAINING_SESSION_ID:-capture-$(date -u +%Y%m%dT%H%M%SZ)}"
capture_dir="${VISION_NAV_TRAINING_CAPTURE_DIR:-$capture_root/$session_name}"
capture_frames="${VISION_NAV_CAPTURE_FRAMES:-120}"
output="${VISION_NAV_TRAINING_CAPTURE_PREFLIGHT:-$HOME/DroneTransfer/outgoing/training-preflight/$session_name.json}"

if [[ ! -x "$venv_python" ]]; then
  echo "Missing Python venv: $venv_python" >&2
  exit 1
fi
if [[ -z "$prop_off_report" || ! -f "$prop_off_report" ]]; then
  echo "Set VISION_NAV_PROP_OFF_PREFLIGHT_REPORT to a passing prop-off preflight report." >&2
  exit 1
fi
if [[ -z "$calibration_id" ]]; then
  echo "Set VISION_NAV_CALIBRATION_ID before training capture preflight." >&2
  exit 1
fi

mkdir -p "$(dirname "$output")"
map_args=(--map-release "$map_release")
if [[ -n "$reference_map" ]]; then
  map_args=(--reference-map "$reference_map")
fi

PYTHONPATH="$repo_root/src" "$venv_python" -m vision_nav.neural.training_capture_preflight \
  --prop-off-report "$prop_off_report" \
  "${map_args[@]}" \
  --calibration-id "$calibration_id" \
  --capture-output-dir "$capture_dir" \
  --capture-frame-count "$capture_frames" \
  --min-free-gib "${VISION_NAV_TRAINING_MIN_FREE_GIB:-20}" \
  --estimated-frame-bytes "${VISION_NAV_TRAINING_ESTIMATED_FRAME_BYTES:-8388608}" \
  --output "$output"

echo
echo "Training capture preflight: $output"
