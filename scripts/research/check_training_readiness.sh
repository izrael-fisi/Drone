#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${VISION_NAV_RESEARCH_PYTHON:-python3}"
index_dir="${1:-${VISION_NAV_TRAINING_INDEX_DIR:-}}"

if [[ -z "$index_dir" ]]; then
  echo "Usage: $0 /path/to/training-index" >&2
  exit 1
fi

args=(
  -m vision_nav.neural.training_readiness
  --index-dir "$index_dir"
  --output "$index_dir/training_readiness.json"
)
if [[ "${VISION_NAV_ALLOW_MISSING_SENSOR_TIMESTAMPS:-0}" == "1" ]]; then
  args+=(--allow-missing-sensor-timestamps)
fi
PYTHONPATH="$repo_root/src" "$python_bin" "${args[@]}"
