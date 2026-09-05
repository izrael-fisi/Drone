#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${VISION_NAV_RESEARCH_PYTHON:-python3}"
dataset_release="${1:-${VISION_NAV_DATASET_RELEASE:-}}"
output_dir="${2:-${VISION_NAV_TRAINING_INDEX_DIR:-$repo_root/data/training-index}}"

if [[ -z "$dataset_release" ]]; then
  echo "Usage: $0 /path/to/dataset-release.json [output-dir]" >&2
  exit 1
fi

args=(
  -m vision_nav.neural.training_dataset
  --dataset-release "$dataset_release"
  --output-dir "$output_dir"
  --timing-policy "${VISION_NAV_TRAINING_TIMING_POLICY:-exposure_native_attitude_v1}"
)
if [[ "${VISION_NAV_ALLOW_MISSING_SENSOR_TIMESTAMPS:-0}" == "1" ]]; then
  args+=(--allow-missing-sensor-timestamps)
fi
PYTHONPATH="$repo_root/src" "$python_bin" "${args[@]}"
