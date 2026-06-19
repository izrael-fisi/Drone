#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
out_dir="${CALIBRATION_OUT_DIR:-$HOME/DroneTransfer/outgoing/calibration/down_camera}"
count="${CALIBRATION_COUNT:-20}"
delay_s="${CALIBRATION_DELAY_S:-2}"
width="${CALIBRATION_WIDTH:-1456}"
height="${CALIBRATION_HEIGHT:-1088}"

if [[ ! -x "$venv_python" ]]; then
  echo "Missing Python venv: $venv_python" >&2
  echo "Run ./scripts/pi/bootstrap_pi5.sh first, then reboot." >&2
  exit 1
fi

mkdir -p "$out_dir"

echo "Capturing $count calibration images to $out_dir"
echo "Move/tilt the chessboard between captures. Press Ctrl+C to stop early."

for i in $(seq -w 1 "$count"); do
  output="$out_dir/calib_${i}.jpg"
  echo "[$i/$count] $output"
  PYTHONPATH="$repo_root/src" "$venv_python" -m vision_nav.capture_frame \
    --output "$output" \
    --width "$width" \
    --height "$height" \
    --timeout-ms 1000
  sleep "$delay_s"
done

echo "Calibration images written to: $out_dir"

