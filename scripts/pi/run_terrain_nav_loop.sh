#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
bundle="${VISION_NAV_BUNDLE:-$HOME/drone-data/map_bundles/mission_bundle}"
out_dir="${VISION_NAV_OUTPUT_DIR:-$HOME/DroneTransfer/outgoing/terrain-match}"
count="${VISION_NAV_COUNT:-0}"
interval_s="${VISION_NAV_INTERVAL_S:-1.0}"
width="${VISION_NAV_WIDTH:-1456}"
height="${VISION_NAV_HEIGHT:-1088}"
timeout_ms="${VISION_NAV_TIMEOUT_MS:-1000}"
camera_calibration="${VISION_NAV_CAMERA_CALIBRATION-$repo_root/config/camera/down_camera.yaml}"
max_candidates="${VISION_NAV_TERRAIN_MAX_CANDIDATES:-64}"
search_radius_m="${VISION_NAV_TERRAIN_SEARCH_RADIUS_M:-80.0}"
mavlink_endpoint="${VISION_NAV_MAVLINK_ENDPOINT:-}"
mavlink_ev_delay_ms="${VISION_NAV_MAVLINK_EV_DELAY_MS:-50}"
mavlink_source_system="${VISION_NAV_MAVLINK_SOURCE_SYSTEM:-42}"
mavlink_source_component="${VISION_NAV_MAVLINK_SOURCE_COMPONENT:-197}"
mavlink_message="${VISION_NAV_MAVLINK_MESSAGE:-odometry}"
external_position_min_rate_hz="${VISION_NAV_EXTERNAL_POSITION_MIN_RATE_HZ:-1.0}"
external_position_max_latency_ms="${VISION_NAV_EXTERNAL_POSITION_MAX_LATENCY_MS:-500.0}"
position_udp_target="${VISION_NAV_POSITION_UDP_TARGET:-}"
gps_min_fix_type="${VISION_NAV_GPS_MIN_FIX_TYPE:-3}"
gps_min_satellites="${VISION_NAV_GPS_MIN_SATELLITES:-6}"
gps_max_eph_m="${VISION_NAV_GPS_MAX_EPH_M:-3.0}"
gps_max_h_acc_m="${VISION_NAV_GPS_MAX_H_ACC_M:-3.0}"
runtime_profile="${VISION_NAV_RUNTIME_PROFILE:-pi5_full}"
camera_profile="${VISION_NAV_CAMERA_PROFILE:-rgb_global_shutter}"
module_weight_g="${VISION_NAV_MODULE_WEIGHT_G:-}"
estimated_bom_usd="${VISION_NAV_ESTIMATED_BOM_USD:-}"
camera_cost_usd="${VISION_NAV_CAMERA_COST_USD:-}"
sensor_compliance_notes="${VISION_NAV_SENSOR_COMPLIANCE_NOTES:-}"
mount_vibration_notes="${VISION_NAV_MOUNT_VIBRATION_NOTES:-}"
field_capture_report="${VISION_NAV_FIELD_LOG_CAPTURE_REPORT:-$out_dir/field_log_capture_report.json}"
field_capture_preflight="${VISION_NAV_FIELD_CAPTURE_PREFLIGHT:-}"
field_case_name="${VISION_NAV_FIELD_CASE_NAME:-}"
field_expected="${VISION_NAV_FIELD_EXPECTED:-}"
field_condition="${VISION_NAV_FIELD_CONDITION:-}"
field_conditions="${VISION_NAV_FIELD_CONDITIONS:-$field_condition}"

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

position_args=()
if [[ -n "$position_udp_target" ]]; then
  position_args=(
    --position-udp-target "$position_udp_target"
    --gps-min-fix-type "$gps_min_fix_type"
    --gps-min-satellites "$gps_min_satellites"
    --gps-max-eph-m "$gps_max_eph_m"
    --gps-max-h-acc-m "$gps_max_h_acc_m"
  )
fi

profile_args=(
  --runtime-profile "$runtime_profile"
  --camera-profile "$camera_profile"
)
if [[ -n "$module_weight_g" ]]; then
  profile_args+=(--module-weight-g "$module_weight_g")
fi
if [[ -n "$estimated_bom_usd" ]]; then
  profile_args+=(--estimated-bom-usd "$estimated_bom_usd")
fi
if [[ -n "$camera_cost_usd" ]]; then
  profile_args+=(--camera-cost-usd "$camera_cost_usd")
fi
if [[ -n "$sensor_compliance_notes" ]]; then
  profile_args+=(--sensor-compliance-notes "$sensor_compliance_notes")
fi
if [[ -n "$mount_vibration_notes" ]]; then
  profile_args+=(--mount-vibration-notes "$mount_vibration_notes")
fi

set +e
PYTHONPATH="$repo_root/src" "$venv_python" -m vision_nav.run_terrain_loop \
  --bundle "$bundle" \
  --output-dir "$out_dir" \
  --count "$count" \
  --interval-s "$interval_s" \
  --width "$width" \
  --height "$height" \
  --timeout-ms "$timeout_ms" \
  --max-candidates "$max_candidates" \
  --search-radius-m "$search_radius_m" \
  "${calibration_args[@]}" \
  "${mavlink_args[@]}" \
  "${position_args[@]}" \
  "${profile_args[@]}"
capture_status=$?
set -e

report_args=(
  --log "$out_dir/terrain_matches.jsonl"
  --runtime-status "$out_dir/runtime_status.json"
  --output "$field_capture_report"
  --bundle "$bundle"
  --capture-output-dir "$out_dir"
  --command-source "pi terrain nav loop wrapper"
  --command "./scripts/pi/run_terrain_nav_loop.sh"
  --exit-code "$capture_status"
)
if [[ -n "$field_capture_preflight" ]]; then
  report_args+=(--preflight "$field_capture_preflight")
fi
if [[ -n "$field_case_name" ]]; then
  report_args+=(--case-name "$field_case_name")
fi
if [[ -n "$field_expected" ]]; then
  report_args+=(--expected "$field_expected")
fi
if [[ -n "$field_condition" ]]; then
  report_args+=(--condition "$field_condition")
fi
if [[ -n "$field_conditions" ]]; then
  report_args+=(--conditions "$field_conditions")
fi

set +e
PYTHONPATH="$repo_root/src" "$venv_python" -m vision_nav.field_log_capture_report "${report_args[@]}"
report_status=$?
set -e

cat <<EOF

Terrain runtime outputs:
  log:            $out_dir/terrain_matches.jsonl
  runtime status: $out_dir/runtime_status.json
  capture report: $field_capture_report

__VISION_NAV_TERRAIN_LOG__=$out_dir/terrain_matches.jsonl
__VISION_NAV_RUNTIME_STATUS__=$out_dir/runtime_status.json
__VISION_NAV_FIELD_LOG_CAPTURE_REPORT__=$field_capture_report
EOF

if [[ "$capture_status" -ne 0 ]]; then
  exit "$capture_status"
fi
exit "$report_status"
