#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${VISION_NAV_RESEARCH_PYTHON:-python3}"
pair_dir="${1:-${VISION_NAV_TRAINING_MAP_PAIRS_DIR:-}}"
output_dir="${2:-${VISION_NAV_TEMPORAL_WINDOWS_DIR:-}}"

if [[ -z "$pair_dir" || -z "$output_dir" ]]; then
  echo "Usage: $0 /path/to/training-map-pairs /path/to/temporal-windows" >&2
  exit 1
fi

args=(
  -m vision_nav.neural.temporal_training_windows
  --pair-dir "$pair_dir"
  --output-dir "$output_dir"
  --window-size "${VISION_NAV_TEMPORAL_WINDOW_SIZE:-4}"
  --stride "${VISION_NAV_TEMPORAL_WINDOW_STRIDE:-1}"
  --max-frame-gap-ms "${VISION_NAV_TEMPORAL_MAX_FRAME_GAP_MS:-1000}"
)
if [[ "${VISION_NAV_ALLOW_MISSING_POSE_CONTEXT:-0}" == "1" ]]; then
  args+=(--allow-missing-pose-context)
fi
PYTHONPATH="$repo_root/src" "$python_bin" "${args[@]}"
