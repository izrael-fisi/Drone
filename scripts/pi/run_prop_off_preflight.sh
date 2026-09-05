#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
output_dir="${VISION_NAV_PROP_OFF_PREFLIGHT_DIR:-$HOME/DroneTransfer/outgoing/prop-off-preflight/$timestamp}"
camera_report="${VISION_NAV_PROP_OFF_CAMERA_BENCH_REPORT:-}"
mavlink_report="${VISION_NAV_PROP_OFF_MAVLINK_BENCH_REPORT:-}"
calibration_file="${VISION_NAV_CALIBRATION_FILE:-$repo_root/config/camera/down_camera.yaml}"
camera_to_body_file="${VISION_NAV_CAMERA_TO_BODY_FILE:-$repo_root/config/camera/camera_to_body.yaml}"

latest_report() {
  local root="$1"
  local filename="$2"
  if [[ ! -d "$root" ]]; then
    return
  fi
  find "$root" -type f -name "$filename" -print | sort | tail -n 1 || true
}

if [[ ! -x "$venv_python" ]]; then
  echo "Missing Python venv: $venv_python" >&2
  echo "Run ./scripts/pi/bootstrap_pi5.sh first." >&2
  exit 1
fi
if [[ "${VISION_NAV_PROP_OFF_CONFIRMED:-0}" != "1" ]]; then
  echo "Refusing to run without VISION_NAV_PROP_OFF_CONFIRMED=1." >&2
  echo "Physically remove all propellers, then attest explicitly before this read-only check." >&2
  exit 1
fi
if [[ -z "$camera_report" ]]; then
  camera_report="$(latest_report "$HOME/DroneTransfer/outgoing/camera-bench" camera_bench_report.json)"
fi
if [[ -z "$mavlink_report" ]]; then
  mavlink_report="$(latest_report "$HOME/DroneTransfer/outgoing/mavlink-bench" mavlink_bench_report.json)"
fi
if [[ -z "$camera_report" || -z "$mavlink_report" ]]; then
  echo "Missing camera or MAVLink bench report. Run capture_camera_bench.sh and capture_mavlink_bench.sh first." >&2
  exit 1
fi

args=(
  -m vision_nav.prop_off_preflight
  --camera-bench-report "$camera_report"
  --mavlink-bench-report "$mavlink_report"
  --output "$output_dir/prop_off_preflight_report.json"
  --confirm-propellers-removed
)
if [[ -f "$calibration_file" ]]; then
  args+=(--calibration-file "$calibration_file")
fi
if [[ -f "$camera_to_body_file" ]]; then
  args+=(--camera-to-body-file "$camera_to_body_file")
fi
if [[ "${VISION_NAV_REQUIRE_TRAINING_CAPTURE_INPUTS:-0}" == "1" ]]; then
  args+=(--require-training-capture-inputs)
fi

mkdir -p "$output_dir"
PYTHONPATH="$repo_root/src" "$venv_python" "${args[@]}"

echo
echo "Prop-off preflight report: $output_dir/prop_off_preflight_report.json"
