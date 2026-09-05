#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
session_dir="${1:-${VISION_NAV_TRAINING_CAPTURE_DIR:-}}"
max_attitude_frame_delta_ms="${VISION_NAV_MAX_ATTITUDE_FRAME_DELTA_MS:-50}"

if [[ ! -x "$venv_python" ]]; then
  echo "Missing Python venv: $venv_python" >&2
  exit 1
fi
if [[ -z "$session_dir" ]]; then
  echo "Usage: ./scripts/pi/validate_training_capture.sh /path/to/capture-session" >&2
  exit 1
fi

require_sensor_timestamps="${VISION_NAV_REQUIRE_SENSOR_TIMESTAMPS:-1}"
require_offline_gnss_reference="${VISION_NAV_REQUIRE_OFFLINE_GNSS_REFERENCE:-1}"
min_offline_gnss_fix_type="${VISION_NAV_MIN_OFFLINE_GNSS_FIX_TYPE:-3}"
max_offline_gnss_horizontal_accuracy_m="${VISION_NAV_MAX_OFFLINE_GNSS_HORIZONTAL_ACCURACY_M:-10}"
max_offline_gnss_frame_age_ms="${VISION_NAV_MAX_OFFLINE_GNSS_FRAME_AGE_MS:-500}"
require_mavlink_timesync="${VISION_NAV_REQUIRE_MAVLINK_TIMESYNC:-1}"
timesync_sample_count="${VISION_NAV_TIMESYNC_SAMPLE_COUNT:-8}"
max_timesync_round_trip_ms="${VISION_NAV_MAX_TIMESYNC_ROUND_TRIP_MS:-50}"

PYTHONPATH="$repo_root/src" "$venv_python" -c '
from vision_nav.training_capture import validate_training_session
import json
import sys
result = validate_training_session(
    sys.argv[1],
    require_attitude=True,
    max_attitude_frame_delta_ms=float(sys.argv[2]),
    require_sensor_timestamps=sys.argv[3] == "1",
    require_offline_gnss_reference=sys.argv[4] == "1",
    min_offline_gnss_fix_type=int(sys.argv[5]),
    max_offline_gnss_horizontal_accuracy_m=float(sys.argv[6]),
    max_offline_gnss_frame_age_ms=float(sys.argv[7]),
    require_mavlink_timesync=sys.argv[8] == "1",
    timesync_sample_count=int(sys.argv[9]),
    max_timesync_round_trip_ms=float(sys.argv[10]),
)
print(json.dumps(result, indent=2, sort_keys=True))
raise SystemExit(0 if result["status"] == "valid" else 1)
' "$session_dir" "$max_attitude_frame_delta_ms" "$require_sensor_timestamps" "$require_offline_gnss_reference" "$min_offline_gnss_fix_type" "$max_offline_gnss_horizontal_accuracy_m" "$max_offline_gnss_frame_age_ms" "$require_mavlink_timesync" "$timesync_sample_count" "$max_timesync_round_trip_ms"
