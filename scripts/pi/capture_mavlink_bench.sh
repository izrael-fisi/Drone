#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
endpoint="${VISION_NAV_MAVLINK_ENDPOINT:-${1:-}}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
output_dir="${VISION_NAV_MAVLINK_BENCH_DIR:-$HOME/DroneTransfer/outgoing/mavlink-bench/$timestamp}"
duration_s="${VISION_NAV_MAVLINK_BENCH_DURATION_S:-15}"
min_attitude_rate_hz="${VISION_NAV_MIN_ATTITUDE_RATE_HZ:-20}"
max_attitude_gap_ms="${VISION_NAV_MAX_ATTITUDE_GAP_MS:-150}"
require_timesync="${VISION_NAV_REQUIRE_MAVLINK_TIMESYNC:-1}"
timesync_sample_count="${VISION_NAV_TIMESYNC_SAMPLE_COUNT:-8}"
max_timesync_round_trip_ms="${VISION_NAV_MAX_TIMESYNC_ROUND_TRIP_MS:-50}"

if [[ ! -x "$venv_python" ]]; then
  echo "Missing Python venv: $venv_python" >&2
  echo "Run ./scripts/pi/bootstrap_pi5.sh first." >&2
  exit 1
fi
if [[ -z "$endpoint" ]]; then
  echo "Set VISION_NAV_MAVLINK_ENDPOINT or pass an endpoint as the first argument." >&2
  echo "Example: VISION_NAV_MAVLINK_ENDPOINT=serial:/dev/ttyACM0:921600 $0" >&2
  exit 1
fi

mkdir -p "$(dirname "$output_dir")"
args=(
  -m vision_nav.mavlink_bench
  --output-dir "$output_dir"
  --endpoint "$endpoint"
  --duration-s "$duration_s"
  --min-attitude-rate-hz "$min_attitude_rate_hz"
  --max-attitude-gap-ms "$max_attitude_gap_ms"
  --source-system "${VISION_NAV_MAVLINK_SOURCE_SYSTEM:-42}"
  --source-component "${VISION_NAV_MAVLINK_SOURCE_COMPONENT:-197}"
  --timesync-sample-count "$timesync_sample_count"
  --max-timesync-round-trip-ms "$max_timesync_round_trip_ms"
)
if [[ "$require_timesync" == "1" ]]; then
  args+=(--require-timesync)
fi
PYTHONPATH="$repo_root/src" "$venv_python" "${args[@]}"

echo
echo "MAVLink bench evidence: $output_dir"
