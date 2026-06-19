#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
bundle="${VISION_NAV_BUNDLE:-$HOME/drone-data/map_bundles/mission_bundle}"
frames="${VISION_NAV_REPLAY_FRAMES:-$HOME/DroneTransfer/outgoing/runtime-match/frames/*.jpg}"
out_dir="${VISION_NAV_REPLAY_OUTPUT_DIR:-$HOME/DroneTransfer/outgoing/replay-match}"
viz_every="${VISION_NAV_VIZ_EVERY:-10}"
camera_calibration="${VISION_NAV_CAMERA_CALIBRATION-$repo_root/config/camera/down_camera.yaml}"
min_scale="${VISION_NAV_MIN_SCALE:-0.2}"
max_scale="${VISION_NAV_MAX_SCALE:-5.0}"
max_rotation_deg="${VISION_NAV_MAX_ROTATION_DEG:-90.0}"
max_scale_anisotropy="${VISION_NAV_MAX_SCALE_ANISOTROPY:-3.0}"
max_perspective_norm="${VISION_NAV_MAX_PERSPECTIVE_NORM:-0.01}"

if [[ ! -x "$venv_python" ]]; then
  echo "Missing Python venv: $venv_python" >&2
  echo "Run ./scripts/pi/bootstrap_pi5.sh first, then reboot." >&2
  exit 1
fi

if [[ ! -e "$bundle" ]]; then
  echo "Missing map bundle: $bundle" >&2
  echo "Set VISION_NAV_BUNDLE=/path/to/mission_bundle or copy a bundle to:" >&2
  echo "  $HOME/drone-data/map_bundles/mission_bundle" >&2
  exit 1
fi

mkdir -p "$out_dir"

calibration_args=()
if [[ -n "$camera_calibration" ]]; then
  calibration_args=(--camera-calibration "$camera_calibration")
fi

PYTHONPATH="$repo_root/src" "$venv_python" -m vision_nav.replay_bundle_frames \
  --bundle "$bundle" \
  --frames "$frames" \
  --output-dir "$out_dir" \
  --viz-every "$viz_every" \
  --min-scale "$min_scale" \
  --max-scale "$max_scale" \
  --max-rotation-deg "$max_rotation_deg" \
  --max-scale-anisotropy "$max_scale_anisotropy" \
  --max-perspective-norm "$max_perspective_norm" \
  "${calibration_args[@]}" \
  --build-if-missing
