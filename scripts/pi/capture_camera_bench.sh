#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
timestamp="$(date -u +%Y%m%dT%H%M%SZ)"
output_dir="${VISION_NAV_CAMERA_BENCH_DIR:-$HOME/DroneTransfer/outgoing/camera-bench/$timestamp}"

if [[ ! -x "$venv_python" ]]; then
  echo "Missing Python venv: $venv_python" >&2
  echo "Run ./scripts/pi/bootstrap_pi5.sh first." >&2
  exit 1
fi

mkdir -p "$(dirname "$output_dir")"
PYTHONPATH="$repo_root/src" "$venv_python" -m vision_nav.camera_bench \
  --output-dir "$output_dir" \
  --count "${VISION_NAV_CAMERA_BENCH_FRAMES:-8}" \
  --delay-s "${VISION_NAV_CAMERA_BENCH_DELAY_S:-0.25}" \
  --width "${VISION_NAV_WIDTH:-1456}" \
  --height "${VISION_NAV_HEIGHT:-1088}" \
  --timeout-ms "${VISION_NAV_TIMEOUT_MS:-1000}" \
  --camera-backend "${VISION_NAV_CAMERA_BACKEND:-auto}" \
  --sharpness-min "${VISION_NAV_CAMERA_SHARPNESS_MIN:-60}" \
  --entropy-min "${VISION_NAV_CAMERA_ENTROPY_MIN:-3.5}" \
  --edge-density-min "${VISION_NAV_CAMERA_EDGE_DENSITY_MIN:-8000}" \
  --saturation-max "${VISION_NAV_CAMERA_SATURATION_MAX:-0.15}" \
  --max-warning-fraction "${VISION_NAV_CAMERA_MAX_WARNING_FRACTION:-0}"

echo
echo "Camera bench evidence: $output_dir"
