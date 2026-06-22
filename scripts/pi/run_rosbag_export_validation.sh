#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
source_log="${VISION_NAV_ROSBAG_SOURCE_LOG:-${VISION_NAV_FIELD_LOG:-$HOME/DroneTransfer/outgoing/terrain-match/terrain_matches.jsonl}}"
export_dir="${VISION_NAV_ROSBAG_EXPORT_DIR:-$HOME/DroneTransfer/outgoing/terrain-match/rosbag-jsonl}"
validation_report="${VISION_NAV_ROSBAG_EXPORT_VALIDATION:-$HOME/DroneTransfer/outgoing/terrain-match/rosbag-jsonl-validation.json}"
include_frame_topic="${VISION_NAV_ROSBAG_INCLUDE_FRAME_TOPIC:-1}"
frame_root="${VISION_NAV_ROSBAG_FRAME_ROOT:-}"
max_frame_bytes="${VISION_NAV_ROSBAG_MAX_FRAME_BYTES:-2000000}"
allow_failed="${VISION_NAV_ROSBAG_ALLOW_FAILED:-0}"

usage() {
  cat >&2 <<EOF
Usage:
  ./scripts/pi/run_rosbag_export_validation.sh

Exports a terrain runtime/replay log to the dependency-free ROS bag JSONL
artifact format and validates the exported topics.

Common optional overrides:
  VISION_NAV_ROSBAG_SOURCE_LOG        Default: $source_log
  VISION_NAV_ROSBAG_EXPORT_DIR        Default: $export_dir
  VISION_NAV_ROSBAG_EXPORT_VALIDATION Default: $validation_report
  VISION_NAV_ROSBAG_INCLUDE_FRAME_TOPIC=0  Disable bounded camera frame topic export
  VISION_NAV_ROSBAG_FRAME_ROOT        Resolve relative frame_path values from this folder
  VISION_NAV_ROSBAG_MAX_FRAME_BYTES   Default: $max_frame_bytes
  VISION_NAV_ROSBAG_ALLOW_FAILED=1    Keep exit zero after writing a failed validation report
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ "$venv_python" == */* ]]; then
  if [[ ! -x "$venv_python" ]]; then
    echo "Missing Python venv: $venv_python" >&2
    echo "Run ./scripts/pi/bootstrap_pi5.sh first, then reboot." >&2
    exit 1
  fi
elif ! command -v "$venv_python" >/dev/null 2>&1; then
  echo "Python command not found: $venv_python" >&2
  echo "Run ./scripts/pi/bootstrap_pi5.sh first, then reboot, or set VISION_NAV_PYTHON." >&2
  exit 1
fi

if [[ ! -f "$source_log" ]]; then
  echo "Missing terrain runtime/replay log: $source_log" >&2
  echo "Run ./scripts/pi/run_terrain_nav_loop.sh or set VISION_NAV_ROSBAG_SOURCE_LOG." >&2
  exit 1
fi

mkdir -p "$export_dir" "$(dirname "$validation_report")"

export_args=(
  -m vision_nav.ros2_bridge
  --log "$source_log"
  --export-rosbag-jsonl "$export_dir"
  --max-frame-bytes "$max_frame_bytes"
)

if [[ "$include_frame_topic" == "1" || "$include_frame_topic" == "true" ]]; then
  export_args+=(--include-frame-topic)
fi

if [[ -n "$frame_root" ]]; then
  export_args+=(--frame-root "$frame_root")
fi

PYTHONPATH="$repo_root/src" "$venv_python" "${export_args[@]}"

set +e
PYTHONPATH="$repo_root/src" "$venv_python" -m vision_nav.rosbag_export_check \
  --artifact "$export_dir" \
  --output "$validation_report"
validation_status=$?
set -e

cat <<EOF

ROS bag export validation output:
  source log: $source_log
  export dir: $export_dir
  report:     $validation_report

The support-bundle and autonomy-readiness wrappers auto-include this report
when present at the default path.

__VISION_NAV_ROSBAG_EXPORT_DIR__=$export_dir
__VISION_NAV_ROSBAG_EXPORT_VALIDATION__=$validation_report
EOF

if [[ "$validation_status" -ne 0 ]]; then
  echo
  echo "ROS bag export validation failed. Review the report before using it as readiness evidence." >&2
  if [[ "$allow_failed" != "1" && "$allow_failed" != "true" ]]; then
    exit "$validation_status"
  fi
fi
