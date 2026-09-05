#!/usr/bin/env bash
set -euo pipefail

# Produce all evidence needed before the first real training-data session. This
# is intentionally prop-off, read-only, and does not call the recorder itself.
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
session_id="${VISION_NAV_TRAINING_SESSION_ID:-capture-$(date -u +%Y%m%dT%H%M%SZ)}"
run_root="${VISION_NAV_FIRST_TRAINING_PREP_DIR:-$HOME/DroneTransfer/outgoing/first-training-prep/$session_id}"
camera_dir="$run_root/camera-bench"
mavlink_dir="$run_root/mavlink-bench"
prop_dir="$run_root/prop-off-preflight"
preflight_report="$run_root/training-capture-preflight.json"
capture_root="${VISION_NAV_TRAINING_CAPTURE_ROOT:-$HOME/drone-data/captures}"
capture_dir="${VISION_NAV_TRAINING_CAPTURE_DIR:-$capture_root/$session_id}"
reference_map="${VISION_NAV_REFERENCE_MAP:-}"
calibration_id="${VISION_NAV_CALIBRATION_ID:-}"

if [[ "${VISION_NAV_PROP_OFF_CONFIRMED:-0}" != "1" ]]; then
  echo "Refusing to proceed without VISION_NAV_PROP_OFF_CONFIRMED=1." >&2
  echo "Physically remove all propellers before this camera/Pixhawk evidence workflow." >&2
  exit 1
fi
if [[ -z "$reference_map" || ! -f "$reference_map" ]]; then
  echo "Set VISION_NAV_REFERENCE_MAP to the selected ReferenceMapRelease JSON." >&2
  exit 1
fi
if [[ -z "$calibration_id" ]]; then
  echo "Set VISION_NAV_CALIBRATION_ID to the measured mounted-camera calibration ID." >&2
  exit 1
fi
if [[ -z "${VISION_NAV_MAVLINK_ENDPOINT:-}" ]]; then
  echo "Set VISION_NAV_MAVLINK_ENDPOINT, for example serial:/dev/ttyACM0:921600." >&2
  exit 1
fi
if [[ -e "$run_root" ]]; then
  echo "Preparation output already exists: $run_root" >&2
  echo "Choose a new VISION_NAV_TRAINING_SESSION_ID or VISION_NAV_FIRST_TRAINING_PREP_DIR." >&2
  exit 1
fi

mkdir -p "$run_root"

echo "[1/4] Capturing multi-frame camera evidence"
VISION_NAV_CAMERA_BENCH_DIR="$camera_dir" \
  "$repo_root/scripts/pi/capture_camera_bench.sh"

echo "[2/4] Capturing read-only Pixhawk MAVLink evidence"
VISION_NAV_MAVLINK_BENCH_DIR="$mavlink_dir" \
  "$repo_root/scripts/pi/capture_mavlink_bench.sh"

echo "[3/4] Validating prop-off hardware, camera calibration, and mount transform"
VISION_NAV_PROP_OFF_PREFLIGHT_DIR="$prop_dir" \
VISION_NAV_PROP_OFF_CAMERA_BENCH_REPORT="$camera_dir/camera_bench_report.json" \
VISION_NAV_PROP_OFF_MAVLINK_BENCH_REPORT="$mavlink_dir/mavlink_bench_report.json" \
VISION_NAV_REQUIRE_TRAINING_CAPTURE_INPUTS=1 \
  "$repo_root/scripts/pi/run_prop_off_preflight.sh"

echo "[4/4] Checking map binding and Pi storage before data capture"
VISION_NAV_PROP_OFF_PREFLIGHT_REPORT="$prop_dir/prop_off_preflight_report.json" \
VISION_NAV_REFERENCE_MAP="$reference_map" \
VISION_NAV_CALIBRATION_ID="$calibration_id" \
VISION_NAV_TRAINING_SESSION_ID="$session_id" \
VISION_NAV_TRAINING_CAPTURE_DIR="$capture_dir" \
VISION_NAV_TRAINING_CAPTURE_PREFLIGHT="$preflight_report" \
  "$repo_root/scripts/pi/preflight_training_capture.sh"

cat <<EOF

First training-capture preparation passed.
  Camera evidence: $camera_dir/camera_bench_report.json
  MAVLink evidence: $mavlink_dir/mavlink_bench_report.json
  Prop-off decision: $prop_dir/prop_off_preflight_report.json
  Capture preflight: $preflight_report

Next, while the vehicle remains prop-off and disarmed:
  VISION_NAV_TRAINING_SESSION_ID=$session_id \\
  VISION_NAV_TRAINING_CAPTURE_DIR=$capture_dir \\
  VISION_NAV_REFERENCE_MAP=$reference_map \\
  VISION_NAV_REQUIRE_OFFLINE_GNSS_REFERENCE=1 \\
  VISION_NAV_REQUIRE_MAVLINK_TIMESYNC=1 \\
  ./scripts/pi/record_training_capture.sh
EOF
