#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
out_dir="${CALIBRATION_OUT_DIR:-$HOME/DroneTransfer/outgoing/calibration/down_camera}"
count="${CALIBRATION_COUNT:-20}"
delay_s="${CALIBRATION_DELAY_S:-2}"
width="${CALIBRATION_WIDTH:-1456}"
height="${CALIBRATION_HEIGHT:-1088}"
camera_backend="${CALIBRATION_CAMERA_BACKEND:-auto}"
board_cols="${CALIBRATION_BOARD_COLS:-}"
board_rows="${CALIBRATION_BOARD_ROWS:-}"
square_size_m="${CALIBRATION_SQUARE_SIZE_M:-}"

if [[ ! -x "$venv_python" ]]; then
  echo "Missing Python venv: $venv_python" >&2
  echo "Run ./scripts/pi/bootstrap_pi5.sh first, then reboot." >&2
  exit 1
fi

args=(
  -m vision_nav.calibration_session
  --output-dir "$out_dir"
  --count "$count"
  --delay-s "$delay_s"
  --width "$width"
  --height "$height"
  --timeout-ms 1000
  --camera-backend "$camera_backend"
)
if [[ -n "$board_cols" ]]; then args+=(--cols "$board_cols"); fi
if [[ -n "$board_rows" ]]; then args+=(--rows "$board_rows"); fi
if [[ -n "$square_size_m" ]]; then args+=(--square-size-m "$square_size_m"); fi

echo "Capturing $count traceable calibration images to $out_dir"
echo "Move/tilt the chessboard between captures. Press Ctrl+C to stop early."
PYTHONPATH="$repo_root/src" "$venv_python" "${args[@]}"
