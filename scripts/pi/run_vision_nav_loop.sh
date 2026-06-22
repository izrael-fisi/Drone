#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
bundle="${VISION_NAV_BUNDLE:-$HOME/drone-data/map_bundles/mission_bundle}"
out_dir="${VISION_NAV_OUTPUT_DIR:-$HOME/DroneTransfer/outgoing/runtime-match}"
count="${VISION_NAV_COUNT:-0}"
interval_s="${VISION_NAV_INTERVAL_S:-1.0}"
width="${VISION_NAV_WIDTH:-1456}"
height="${VISION_NAV_HEIGHT:-1088}"
timeout_ms="${VISION_NAV_TIMEOUT_MS:-1000}"
viz_every="${VISION_NAV_VIZ_EVERY:-10}"
camera_calibration="${VISION_NAV_CAMERA_CALIBRATION-$repo_root/config/camera/down_camera.yaml}"
min_scale="${VISION_NAV_MIN_SCALE:-0.2}"
max_scale="${VISION_NAV_MAX_SCALE:-5.0}"
max_rotation_deg="${VISION_NAV_MAX_ROTATION_DEG:-90.0}"
max_scale_anisotropy="${VISION_NAV_MAX_SCALE_ANISOTROPY:-3.0}"
max_perspective_norm="${VISION_NAV_MAX_PERSPECTIVE_NORM:-0.01}"
mavlink_endpoint="${VISION_NAV_MAVLINK_ENDPOINT:-}"
mavlink_ev_delay_ms="${VISION_NAV_MAVLINK_EV_DELAY_MS:-50}"
mavlink_source_system="${VISION_NAV_MAVLINK_SOURCE_SYSTEM:-42}"
mavlink_source_component="${VISION_NAV_MAVLINK_SOURCE_COMPONENT:-197}"
mavlink_message="${VISION_NAV_MAVLINK_MESSAGE:-odometry}"
external_position_min_rate_hz="${VISION_NAV_EXTERNAL_POSITION_MIN_RATE_HZ:-1.0}"
external_position_max_latency_ms="${VISION_NAV_EXTERNAL_POSITION_MAX_LATENCY_MS:-500.0}"

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

mavlink_args=()
if [[ -n "$mavlink_endpoint" ]]; then
  mavlink_args=(
    --mavlink-endpoint "$mavlink_endpoint"
    --mavlink-ev-delay-ms "$mavlink_ev_delay_ms"
    --mavlink-source-system "$mavlink_source_system"
    --mavlink-source-component "$mavlink_source_component"
    --mavlink-message "$mavlink_message"
    --external-position-min-rate-hz "$external_position_min_rate_hz"
    --external-position-max-latency-ms "$external_position_max_latency_ms"
  )
fi

PYTHONPATH="$repo_root/src" "$venv_python" -m vision_nav.run_bundle_match_loop \
  --bundle "$bundle" \
  --output-dir "$out_dir" \
  --count "$count" \
  --interval-s "$interval_s" \
  --width "$width" \
  --height "$height" \
  --timeout-ms "$timeout_ms" \
  --viz-every "$viz_every" \
  --min-scale "$min_scale" \
  --max-scale "$max_scale" \
  --max-rotation-deg "$max_rotation_deg" \
  --max-scale-anisotropy "$max_scale_anisotropy" \
  --max-perspective-norm "$max_perspective_norm" \
  "${calibration_args[@]}" \
  "${mavlink_args[@]}" \
  --build-if-missing
