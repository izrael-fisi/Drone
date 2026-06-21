#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
bundle="${VISION_NAV_BUNDLE:-$HOME/drone-data/map_bundles/mission_bundle}"
log_path="${VISION_NAV_TERRAIN_REPLAY_LOG:-$HOME/DroneTransfer/outgoing/terrain-match/terrain_matches.jsonl}"
out_dir="${VISION_NAV_REPLAY_OUTPUT_DIR:-$HOME/DroneTransfer/outgoing/terrain-replay}"
camera_calibration="${VISION_NAV_CAMERA_CALIBRATION-$repo_root/config/camera/down_camera.yaml}"
max_candidates="${VISION_NAV_TERRAIN_MAX_CANDIDATES:-64}"
search_radius_m="${VISION_NAV_TERRAIN_SEARCH_RADIUS_M:-80.0}"

if [[ ! -x "$venv_python" ]]; then
  echo "Missing Python venv: $venv_python" >&2
  echo "Run ./scripts/pi/bootstrap_pi5.sh first, then reboot." >&2
  exit 1
fi

if [[ ! -e "$bundle" ]]; then
  echo "Missing map bundle: $bundle" >&2
  exit 1
fi

if [[ ! -f "$log_path" ]]; then
  echo "Missing terrain replay log: $log_path" >&2
  exit 1
fi

mkdir -p "$out_dir"

calibration_args=()
if [[ -n "$camera_calibration" ]]; then
  calibration_args=(--camera-calibration "$camera_calibration")
fi

PYTHONPATH="$repo_root/src" "$venv_python" -m vision_nav.replay_terrain_log \
  --bundle "$bundle" \
  --log "$log_path" \
  --output-dir "$out_dir" \
  --max-candidates "$max_candidates" \
  --search-radius-m "$search_radius_m" \
  "${calibration_args[@]}"
