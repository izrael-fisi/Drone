#!/usr/bin/env bash
set -euo pipefail

# Measure a release-bound learned pipeline on saved, calibrated camera frames.
# This is shadow-only evidence. It never connects to PX4 and never publishes
# external vision or odometry.
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
map_release="${VISION_NAV_MAP_RELEASE:-$HOME/drone-data/map_releases/active/map_release.json}"
model_release="${VISION_NAV_MODEL_RELEASE:-$HOME/drone-data/model_releases/pi5_crossview_nav_v1/model_release.json}"
model_artifact_root="${VISION_NAV_MODEL_ARTIFACT_ROOT:-}"
calibration_id="${VISION_NAV_CALIBRATION_ID:-down-camera-v1}"
calibration_file="${VISION_NAV_CALIBRATION_FILE:-$repo_root/config/camera/down_camera.yaml}"
frames_dir="${VISION_NAV_BENCHMARK_FRAMES_DIR:-$HOME/drone-data/benchmark-frames}"
output_dir="${VISION_NAV_BENCHMARK_OUTPUT_DIR:-$HOME/DroneTransfer/outgoing/learned-runtime-bench}"
provider="${VISION_NAV_INFERENCE_PROVIDER:-onnxruntime_cpu}"
warmup_count="${VISION_NAV_BENCHMARK_WARMUP_COUNT:-1}"
top_k="${VISION_NAV_BENCHMARK_RETRIEVAL_TOP_K:-12}"
min_frames="${VISION_NAV_BENCHMARK_MIN_FRAMES:-3}"
max_p95_ms="${VISION_NAV_BENCHMARK_MAX_P95_MS:-1000}"
max_rss_mib="${VISION_NAV_BENCHMARK_MAX_RSS_MIB:-2048}"
max_temperature_c="${VISION_NAV_BENCHMARK_MAX_TEMPERATURE_C:-80}"

if [[ ! -x "$venv_python" ]]; then
  echo "Missing Python venv: $venv_python" >&2
  exit 1
fi
if [[ ! -d "$frames_dir" ]]; then
  echo "Missing benchmark frame directory: $frames_dir" >&2
  echo "Record or copy at least three measured-calibration JPG/PNG frames before benchmarking." >&2
  exit 1
fi

mkdir -p "$output_dir"
args=(
  --map-release "$map_release"
  --model-release "$model_release"
  --calibration-id "$calibration_id"
  --camera-calibration "$calibration_file"
  --provider "$provider"
  --frames-dir "$frames_dir"
  --output "$output_dir/learned_runtime_benchmark.json"
  --warmup-count "$warmup_count"
  --retrieval-top-k "$top_k"
  --min-recorded-frames "$min_frames"
  --max-p95-end-to-end-ms "$max_p95_ms"
  --max-rss-mib "$max_rss_mib"
  --max-temperature-c "$max_temperature_c"
)
if [[ -n "$model_artifact_root" ]]; then
  args+=(--model-artifact-root "$model_artifact_root")
fi

PYTHONPATH="$repo_root/src" exec "$venv_python" -m vision_nav.neural.runtime_benchmark "${args[@]}" "$@"
