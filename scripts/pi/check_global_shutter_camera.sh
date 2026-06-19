#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
out_dir="${VISION_NAV_CAMERA_HEALTH_DIR:-$HOME/DroneTransfer/outgoing/camera-health}"
width="${VISION_NAV_WIDTH:-1456}"
height="${VISION_NAV_HEIGHT:-1088}"
timeout_ms="${VISION_NAV_TIMEOUT_MS:-1000}"
fail_on_warning="${VISION_NAV_CAMERA_FAIL_ON_WARNING:-0}"

if [[ ! -x "$venv_python" ]]; then
  echo "Missing Python venv: $venv_python" >&2
  echo "Run ./scripts/pi/bootstrap_pi5.sh first, then reboot." >&2
  exit 1
fi

mkdir -p "$out_dir"

if command -v rpicam-hello >/dev/null 2>&1; then
  timeout 15 rpicam-hello --list-cameras >"$out_dir/list_cameras.txt" 2>&1 || true
elif command -v libcamera-hello >/dev/null 2>&1; then
  timeout 15 libcamera-hello --list-cameras >"$out_dir/list_cameras.txt" 2>&1 || true
else
  echo "No rpicam-hello/libcamera-hello command found." >"$out_dir/list_cameras.txt"
fi

args=(
  -m vision_nav.camera_health
  --capture
  --output-dir "$out_dir"
  --width "$width"
  --height "$height"
  --timeout-ms "$timeout_ms"
)

if [[ "$fail_on_warning" == "1" ]]; then
  args+=(--fail-on-warning)
fi

PYTHONPATH="$repo_root/src" "$venv_python" "${args[@]}"

echo
echo "Camera health outputs written to: $out_dir"

