#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${VISION_NAV_PYTHON:-python3}"
listener_path="${1:-}"
mavlink_status_path="${2:-}"
expected_message="${VISION_NAV_SITL_MAVLINK_MESSAGE:-odometry}"

if [[ -z "$listener_path" ]]; then
  cat >&2 <<EOF
Usage:
  $0 /path/to/vehicle_visual_odometry.txt [/path/to/mavlink_status.txt]

Capture the first file from PX4 SITL or QGroundControl MAVLink console:
  listener vehicle_visual_odometry 5

Optionally capture the second file from:
  mavlink status
EOF
  exit 2
fi

args=(
  -m vision_nav.px4_sitl_evidence
  --listener "$listener_path"
  --expected-message "$expected_message"
)

if [[ -n "$mavlink_status_path" ]]; then
  args+=(--mavlink-status "$mavlink_status_path")
fi

PYTHONPATH="$repo_root/src" "$python_bin" "${args[@]}"
