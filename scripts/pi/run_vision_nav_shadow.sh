#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
map_release="${VISION_NAV_MAP_RELEASE:-$HOME/drone-data/map_releases/active/map_release.json}"
model_release="${VISION_NAV_MODEL_RELEASE:-$HOME/drone-data/model_releases/pi5_crossview_nav_v1/model_release.json}"
calibration_id="${VISION_NAV_CALIBRATION_ID:-down-camera-v1}"
provider="${VISION_NAV_INFERENCE_PROVIDER:-onnxruntime_cpu}"
out_dir="${VISION_NAV_OUTPUT_DIR:-$HOME/DroneTransfer/outgoing/neural-shadow}"
udp_target="${VISION_NAV_STATUS_UDP_TARGET:-}"
model_artifact_root="${VISION_NAV_MODEL_ARTIFACT_ROOT:-}"
frame="${VISION_NAV_SHADOW_FRAME:-}"
camera_calibration="${VISION_NAV_CALIBRATION_FILE:-}"
camera_backend="${VISION_NAV_CAMERA_BACKEND:-none}"
camera_width="${VISION_NAV_CAMERA_WIDTH:-1456}"
camera_height="${VISION_NAV_CAMERA_HEIGHT:-1088}"
camera_timeout_ms="${VISION_NAV_CAMERA_TIMEOUT_MS:-1000}"
capture_interval_s="${VISION_NAV_SHADOW_CAPTURE_INTERVAL_S:-0.5}"
mavlink_endpoint="${VISION_NAV_MAVLINK_ENDPOINT:-}"
mavlink_source_system="${VISION_NAV_MAVLINK_SOURCE_SYSTEM:-42}"
mavlink_source_component="${VISION_NAV_MAVLINK_SOURCE_COMPONENT:-197}"
mavlink_target_system="${VISION_NAV_MAVLINK_TARGET_SYSTEM:-1}"
mavlink_target_component="${VISION_NAV_MAVLINK_TARGET_COMPONENT:-1}"
attitude_stale_after_ms="${VISION_NAV_ATTITUDE_STALE_AFTER_MS:-500}"
max_attitude_frame_delta_ms="${VISION_NAV_MAX_ATTITUDE_FRAME_DELTA_MS:-50}"
max_temporal_frame_gap_ms="${VISION_NAV_MAX_TEMPORAL_FRAME_GAP_MS:-1500}"
max_frame_age_ms="${VISION_NAV_MAX_FRAME_AGE_MS:-1000}"
heartbeat_interval_s="${VISION_NAV_HEARTBEAT_INTERVAL_S:-0.5}"
inference_warning_ms="${VISION_NAV_INFERENCE_WARNING_MS:-2000}"
record_runtime_metrics="${VISION_NAV_RECORD_RUNTIME_METRICS:-0}"
metrics_interval_s="${VISION_NAV_METRICS_INTERVAL_S:-1}"
metrics_max_samples="${VISION_NAV_METRICS_MAX_SAMPLES:-3600}"

if [[ ! -x "$venv_python" ]]; then
  echo "Missing Python venv: $venv_python" >&2
  exit 1
fi

args=(
  --map-release "$map_release"
  --model-release "$model_release"
  --calibration-id "$calibration_id"
  --provider "$provider"
  --output-dir "$out_dir"
  --camera-backend "$camera_backend"
  --camera-width "$camera_width"
  --camera-height "$camera_height"
  --camera-timeout-ms "$camera_timeout_ms"
  --capture-interval-s "$capture_interval_s"
  --max-attitude-frame-delta-ms "$max_attitude_frame_delta_ms"
  --max-temporal-frame-gap-ms "$max_temporal_frame_gap_ms"
  --max-frame-age-ms "$max_frame_age_ms"
  --heartbeat-interval-s "$heartbeat_interval_s"
  --inference-warning-ms "$inference_warning_ms"
  --metrics-interval-s "$metrics_interval_s"
  --metrics-max-samples "$metrics_max_samples"
)
if [[ "$record_runtime_metrics" == "1" ]]; then
  args+=(--record-runtime-metrics)
elif [[ "$record_runtime_metrics" != "0" ]]; then
  echo "VISION_NAV_RECORD_RUNTIME_METRICS must be 0 or 1" >&2
  exit 1
fi
if [[ -n "$model_artifact_root" ]]; then
  args+=(--model-artifact-root "$model_artifact_root")
fi
if [[ -n "$frame" ]]; then
  args+=(--frame "$frame")
fi
if [[ -n "$camera_calibration" ]]; then
  args+=(--camera-calibration "$camera_calibration")
fi
if [[ -n "$mavlink_endpoint" ]]; then
  args+=(
    --mavlink-endpoint "$mavlink_endpoint"
    --mavlink-source-system "$mavlink_source_system"
    --mavlink-source-component "$mavlink_source_component"
    --mavlink-target-system "$mavlink_target_system"
    --mavlink-target-component "$mavlink_target_component"
    --attitude-stale-after-ms "$attitude_stale_after_ms"
  )
fi
if [[ -n "$udp_target" ]]; then
  args+=(--status-udp-target "$udp_target")
fi

mkdir -p "$out_dir"
PYTHONPATH="$repo_root/src" exec "$venv_python" -m vision_nav.neural.shadow_runtime "${args[@]}" "$@"
