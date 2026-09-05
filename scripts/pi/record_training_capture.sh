#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
capture_root="${VISION_NAV_TRAINING_CAPTURE_ROOT:-$HOME/drone-data/captures}"
session_name="${VISION_NAV_TRAINING_SESSION_ID:-capture-$(date -u +%Y%m%dT%H%M%SZ)}"
output_dir="${VISION_NAV_TRAINING_CAPTURE_DIR:-$capture_root/$session_name}"
calibration_id="${VISION_NAV_CALIBRATION_ID:-}"
map_reference_id="${VISION_NAV_REFERENCE_MAP_ID:-${VISION_NAV_MAP_RELEASE_ID:-}}"
map_reference_kind="${VISION_NAV_MAP_REFERENCE_KIND:-}"
reference_map_path="${VISION_NAV_REFERENCE_MAP:-}"
frames="${VISION_NAV_CAPTURE_FRAMES:-120}"
interval_s="${VISION_NAV_CAPTURE_INTERVAL_S:-0.5}"
width="${VISION_NAV_WIDTH:-1456}"
height="${VISION_NAV_HEIGHT:-1088}"
timeout_ms="${VISION_NAV_TIMEOUT_MS:-1000}"
mavlink_endpoint="${VISION_NAV_MAVLINK_ENDPOINT:-}"
calibration_file="${VISION_NAV_CALIBRATION_FILE:-$repo_root/config/camera/down_camera.yaml}"
camera_to_body_file="${VISION_NAV_CAMERA_TO_BODY_FILE:-$repo_root/config/camera/camera_to_body.yaml}"
# Field training needs Picamera2 SensorTimestamp provenance. Other backends are
# available only when explicitly requested for bench investigation.
camera_backend="${VISION_NAV_CAMERA_BACKEND:-picamera2}"
max_attitude_frame_delta_ms="${VISION_NAV_MAX_ATTITUDE_FRAME_DELTA_MS:-50}"
require_offline_gnss_reference="${VISION_NAV_REQUIRE_OFFLINE_GNSS_REFERENCE:-1}"
min_offline_gnss_fix_type="${VISION_NAV_MIN_OFFLINE_GNSS_FIX_TYPE:-3}"
max_offline_gnss_horizontal_accuracy_m="${VISION_NAV_MAX_OFFLINE_GNSS_HORIZONTAL_ACCURACY_M:-10}"
max_offline_gnss_frame_age_ms="${VISION_NAV_MAX_OFFLINE_GNSS_FRAME_AGE_MS:-500}"
require_mavlink_timesync="${VISION_NAV_REQUIRE_MAVLINK_TIMESYNC:-1}"
timesync_sample_count="${VISION_NAV_TIMESYNC_SAMPLE_COUNT:-8}"
max_timesync_round_trip_ms="${VISION_NAV_MAX_TIMESYNC_ROUND_TRIP_MS:-50}"

if [[ ! -x "$venv_python" ]]; then
  echo "Missing Python venv: $venv_python" >&2
  echo "Run ./scripts/pi/bootstrap_pi5.sh first." >&2
  exit 1
fi
if [[ -z "$map_reference_kind" ]]; then
  if [[ -n "${VISION_NAV_REFERENCE_MAP_ID:-}" ]]; then
    map_reference_kind="reference_map_release"
  else
    map_reference_kind="deployment_map_release"
  fi
fi
if [[ -z "$map_reference_id" && -n "$reference_map_path" ]]; then
  if [[ ! -f "$reference_map_path" ]]; then
    echo "Reference map file not found: $reference_map_path" >&2
    exit 1
  fi
  map_reference_id="$($venv_python - "$reference_map_path" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

try:
    raw = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
except (OSError, json.JSONDecodeError) as exc:
    raise SystemExit(f"Could not read ReferenceMapRelease: {exc}") from exc
release_id = raw.get("release_id") if isinstance(raw, dict) else None
if not isinstance(release_id, str) or not release_id:
    raise SystemExit("ReferenceMapRelease is missing a non-empty release_id.")
print(release_id)
PY
)"
  map_reference_kind="reference_map_release"
fi
if [[ -z "$calibration_id" || -z "$map_reference_id" ]]; then
  echo "Set VISION_NAV_CALIBRATION_ID plus VISION_NAV_REFERENCE_MAP or VISION_NAV_REFERENCE_MAP_ID before first-data capture." >&2
  echo "VISION_NAV_MAP_RELEASE_ID remains supported for a later deployment MapRelease v2." >&2
  exit 1
fi
if [[ ! -f "$calibration_file" ]]; then
  echo "Camera calibration file not found: $calibration_file" >&2
  exit 1
fi
if [[ ! -f "$camera_to_body_file" ]]; then
  echo "Camera-to-body extrinsics file not found: $camera_to_body_file" >&2
  exit 1
fi

args=(
  -m vision_nav.training_capture
  --output-dir "$output_dir"
  --session-id "$session_name"
  --calibration-id "$calibration_id"
  --calibration-file "$calibration_file"
  --camera-to-body-file "$camera_to_body_file"
  --map-release-id "$map_reference_id"
  --map-reference-kind "$map_reference_kind"
  --camera-backend "$camera_backend"
  --frames "$frames"
  --interval-s "$interval_s"
  --width "$width"
  --height "$height"
  --timeout-ms "$timeout_ms"
  --max-attitude-frame-delta-ms "$max_attitude_frame_delta_ms"
)

if [[ "${VISION_NAV_ALLOW_PROVISIONAL_CALIBRATION:-0}" == "1" ]]; then
  args+=(--allow-provisional-calibration)
fi
if [[ "${VISION_NAV_ALLOW_PROVISIONAL_EXTRINSICS:-0}" == "1" ]]; then
  args+=(--allow-provisional-extrinsics)
fi

if [[ -n "$mavlink_endpoint" ]]; then
  args+=(--mavlink-endpoint "$mavlink_endpoint")
fi
if [[ "${VISION_NAV_REQUIRE_MAVLINK_ATTITUDE:-1}" == "1" ]]; then
  args+=(--require-mavlink-attitude)
fi
if [[ "${VISION_NAV_REQUIRE_SENSOR_TIMESTAMPS:-1}" == "1" ]]; then
  args+=(--require-sensor-timestamps)
fi
if [[ "$require_offline_gnss_reference" == "1" ]]; then
  args+=(--require-offline-gnss-reference)
fi
if [[ "$require_mavlink_timesync" == "1" ]]; then
  args+=(--require-mavlink-timesync)
fi
args+=(
  --min-offline-gnss-fix-type "$min_offline_gnss_fix_type"
  --max-offline-gnss-horizontal-accuracy-m "$max_offline_gnss_horizontal_accuracy_m"
  --max-offline-gnss-frame-age-ms "$max_offline_gnss_frame_age_ms"
  --timesync-sample-count "$timesync_sample_count"
  --max-timesync-round-trip-ms "$max_timesync_round_trip_ms"
)
if [[ -n "${VISION_NAV_CAPTURE_SITE_NAME:-}" ]]; then
  args+=(--site-name "$VISION_NAV_CAPTURE_SITE_NAME")
fi
if [[ -n "${VISION_NAV_CAPTURE_OPERATOR:-}" ]]; then
  args+=(--operator "$VISION_NAV_CAPTURE_OPERATOR")
fi
if [[ -n "${VISION_NAV_CAPTURE_CONDITION:-}" ]]; then
  args+=(--condition "$VISION_NAV_CAPTURE_CONDITION")
fi

mkdir -p "$capture_root"
PYTHONPATH="$repo_root/src" "$venv_python" "${args[@]}"

echo
echo "Training capture recorded: $output_dir"
echo "Validate it again with: ./scripts/pi/validate_training_capture.sh $output_dir"
