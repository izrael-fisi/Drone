#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
out_dir="${VISION_NAV_OUTPUT_DIR:-$HOME/DroneTransfer/outgoing/status-bridge}"
count="${VISION_NAV_COUNT:-0}"
interval_s="${VISION_NAV_INTERVAL_S:-1.0}"
mavlink_endpoint="${VISION_NAV_MAVLINK_ENDPOINT:-}"
mavlink_source_system="${VISION_NAV_MAVLINK_SOURCE_SYSTEM:-42}"
mavlink_source_component="${VISION_NAV_MAVLINK_SOURCE_COMPONENT:-197}"
position_udp_target="${VISION_NAV_POSITION_UDP_TARGET:-255.255.255.255:17660}"
active_bundle="${VISION_NAV_BUNDLE:-$HOME/drone-data/map_bundles/mission_bundle}"
runtime_profile="${VISION_NAV_RUNTIME_PROFILE:-pi5_full}"
camera_profile="${VISION_NAV_CAMERA_PROFILE:-rgb_global_shutter}"
module_weight_g="${VISION_NAV_MODULE_WEIGHT_G:-}"
estimated_bom_usd="${VISION_NAV_ESTIMATED_BOM_USD:-}"
camera_cost_usd="${VISION_NAV_CAMERA_COST_USD:-}"
sensor_compliance_notes="${VISION_NAV_SENSOR_COMPLIANCE_NOTES:-}"
mount_vibration_notes="${VISION_NAV_MOUNT_VIBRATION_NOTES:-}"
gps_min_fix_type="${VISION_NAV_GPS_MIN_FIX_TYPE:-3}"
gps_min_satellites="${VISION_NAV_GPS_MIN_SATELLITES:-6}"
gps_max_eph_m="${VISION_NAV_GPS_MAX_EPH_M:-3.0}"
gps_max_h_acc_m="${VISION_NAV_GPS_MAX_H_ACC_M:-3.0}"

if [[ ! -x "$venv_python" ]]; then
  echo "Missing Python venv: $venv_python" >&2
  echo "Run ./scripts/pi/bootstrap_pi5.sh first, then reboot." >&2
  exit 1
fi

mkdir -p "$out_dir"

mavlink_args=()
if [[ -n "$mavlink_endpoint" ]]; then
  mavlink_args=(
    --mavlink-endpoint "$mavlink_endpoint"
    --mavlink-source-system "$mavlink_source_system"
    --mavlink-source-component "$mavlink_source_component"
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

PYTHONPATH="$repo_root/src" "$venv_python" -m vision_nav.run_status_bridge \
  --output-dir "$out_dir" \
  --count "$count" \
  --interval-s "$interval_s" \
  --position-udp-target "$position_udp_target" \
  --active-bundle "$active_bundle" \
  --gps-min-fix-type "$gps_min_fix_type" \
  --gps-min-satellites "$gps_min_satellites" \
  --gps-max-eph-m "$gps_max_eph_m" \
  --gps-max-h-acc-m "$gps_max_h_acc_m" \
  "${mavlink_args[@]}" \
  "${profile_args[@]}"
