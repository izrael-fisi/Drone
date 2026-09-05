#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${VISION_NAV_PYTHON:-python3}"
index_dir="${1:-${VISION_NAV_TRAINING_INDEX_DIR:-}}"
output_dir="${2:-${VISION_NAV_TRAINING_SPLIT_DIR:-}}"

if [[ -z "$index_dir" || -z "$output_dir" ]]; then
  echo "Usage: $0 /path/to/training-index /path/to/training-split" >&2
  exit 1
fi

PYTHONPATH="$repo_root/src" "$python_bin" -m vision_nav.neural.training_splits \
  --index-dir "$index_dir" \
  --output-dir "$output_dir" \
  --validation-fraction "${VISION_NAV_VALIDATION_FRACTION:-0.2}" \
  --seed "${VISION_NAV_SPLIT_SEED:-20260830}"
