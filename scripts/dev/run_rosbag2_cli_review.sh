#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${VISION_NAV_PYTHON:-python3}"
ros_setup="${VISION_NAV_ROS2_SETUP:-}"
source_log="${VISION_NAV_ROSBAG_SOURCE_LOG:-${VISION_NAV_FIELD_LOG:-$HOME/DroneTransfer/outgoing/terrain-match/terrain_matches.jsonl}}"
export_dir="${VISION_NAV_ROSBAG2_EXPORT_DIR:-$HOME/DroneTransfer/outgoing/terrain-match/rosbag2-native}"
review_report="${VISION_NAV_ROSBAG2_CLI_REVIEW:-$HOME/DroneTransfer/outgoing/terrain-match/rosbag2-cli-review.json}"
include_frame_topic="${VISION_NAV_ROSBAG_INCLUDE_FRAME_TOPIC:-1}"
frame_root="${VISION_NAV_ROSBAG_FRAME_ROOT:-}"
max_frame_bytes="${VISION_NAV_ROSBAG_MAX_FRAME_BYTES:-2000000}"
storage_id="${VISION_NAV_ROSBAG2_STORAGE_ID:-sqlite3}"
ros2_command="${VISION_NAV_ROS2_COMMAND:-ros2}"
timeout_s="${VISION_NAV_ROSBAG2_REVIEW_TIMEOUT_S:-30}"
require_ros2="${VISION_NAV_ROSBAG2_REQUIRE_ROS2:-1}"
allow_failed="${VISION_NAV_ROSBAG2_ALLOW_FAILED:-0}"
dry_run="${VISION_NAV_ROSBAG2_DRY_RUN:-0}"

usage() {
  cat >&2 <<EOF
Usage:
  ./scripts/dev/run_rosbag2_cli_review.sh

Exports a terrain runtime/replay log to native rosbag2 on a sourced ROS 2
workstation, then writes the final rosbag2 CLI review artifact used by autonomy
readiness.

Common optional overrides:
  VISION_NAV_ROS2_SETUP              Source this setup file before export, e.g. /opt/ros/humble/setup.bash
  VISION_NAV_ROSBAG_SOURCE_LOG       Default: $source_log
  VISION_NAV_ROSBAG2_EXPORT_DIR      Default: $export_dir
  VISION_NAV_ROSBAG2_CLI_REVIEW      Default: $review_report
  VISION_NAV_ROSBAG_INCLUDE_FRAME_TOPIC=0  Disable bounded camera frame topic export
  VISION_NAV_ROSBAG_FRAME_ROOT       Resolve relative frame_path values from this folder
  VISION_NAV_ROSBAG_MAX_FRAME_BYTES  Default: $max_frame_bytes
  VISION_NAV_ROSBAG2_STORAGE_ID      Default: $storage_id
  VISION_NAV_ROS2_COMMAND            Default: $ros2_command
  VISION_NAV_ROSBAG2_REQUIRE_ROS2=0  Degrade instead of fail when ros2 CLI is unavailable
  VISION_NAV_ROSBAG2_ALLOW_FAILED=1  Keep exit zero after writing a failed review report
  VISION_NAV_ROSBAG2_DRY_RUN=1       Print planned commands without requiring ROS 2
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ "$dry_run" == "1" || "$dry_run" == "true" ]]; then
  cat <<EOF
Native rosbag2 CLI review dry run:
  source log:     $source_log
  export dir:     $export_dir
  review report:  $review_report
  ros setup:      ${ros_setup:-already sourced or not provided}
  storage id:     $storage_id
  ros2 command:   $ros2_command
  require ros2:   $require_ros2

Planned commands:
  vision-nav-ros2-replay-log --log "$source_log" --export-rosbag2 "$export_dir" --rosbag2-storage-id "$storage_id"
  vision-nav-review-rosbag2-cli --artifact "$export_dir" --output "$review_report" --ros2-command "$ros2_command" --timeout-s "$timeout_s"$([[ "$require_ros2" == "1" || "$require_ros2" == "true" ]] && printf ' --require-ros2')

__VISION_NAV_ROSBAG2_EXPORT_DIR__=$export_dir
__VISION_NAV_ROSBAG2_CLI_REVIEW__=$review_report
EOF
  exit 0
fi

if [[ -n "$ros_setup" ]]; then
  if [[ ! -f "$ros_setup" ]]; then
    echo "ROS 2 setup file not found: $ros_setup" >&2
    exit 1
  fi
  # shellcheck disable=SC1090
  source "$ros_setup"
fi

if [[ "$python_bin" == */* ]]; then
  if [[ ! -x "$python_bin" ]]; then
    echo "Python executable not found: $python_bin" >&2
    echo "Set VISION_NAV_PYTHON to the sourced ROS 2 Python or an installed vision-nav environment." >&2
    exit 1
  fi
elif ! command -v "$python_bin" >/dev/null 2>&1; then
  echo "Python command not found: $python_bin" >&2
  echo "Set VISION_NAV_PYTHON to the sourced ROS 2 Python or an installed vision-nav environment." >&2
  exit 1
fi

if [[ ! -f "$source_log" ]]; then
  echo "Missing terrain runtime/replay log: $source_log" >&2
  echo "Run ./scripts/pi/run_terrain_nav_loop.sh, sync logs from the Pi, or set VISION_NAV_ROSBAG_SOURCE_LOG." >&2
  exit 1
fi

mkdir -p "$export_dir" "$(dirname "$review_report")"

export_args=(
  -m vision_nav.ros2_bridge
  --log "$source_log"
  --export-rosbag2 "$export_dir"
  --rosbag2-storage-id "$storage_id"
  --max-frame-bytes "$max_frame_bytes"
)

if [[ "$include_frame_topic" == "1" || "$include_frame_topic" == "true" ]]; then
  export_args+=(--include-frame-topic)
fi

if [[ -n "$frame_root" ]]; then
  export_args+=(--frame-root "$frame_root")
fi

PYTHONPATH="$repo_root/src" "$python_bin" "${export_args[@]}"

review_args=(
  -m vision_nav.rosbag2_cli_review
  --artifact "$export_dir"
  --output "$review_report"
  --ros2-command "$ros2_command"
  --timeout-s "$timeout_s"
)

if [[ "$require_ros2" == "1" || "$require_ros2" == "true" ]]; then
  review_args+=(--require-ros2)
fi

set +e
PYTHONPATH="$repo_root/src" "$python_bin" "${review_args[@]}"
review_status=$?
set -e

cat <<EOF

Native rosbag2 review output:
  source log:    $source_log
  export dir:    $export_dir
  review report: $review_report

The support-bundle and autonomy-readiness wrappers auto-include this report
when present at the default path.

__VISION_NAV_ROSBAG2_EXPORT_DIR__=$export_dir
__VISION_NAV_ROSBAG2_CLI_REVIEW__=$review_report
EOF

if [[ "$review_status" -ne 0 ]]; then
  echo
  echo "Native rosbag2 CLI review failed. Review the report before using it as readiness evidence." >&2
  if [[ "$allow_failed" != "1" && "$allow_failed" != "true" ]]; then
    exit "$review_status"
  fi
fi
