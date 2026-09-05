#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${VISION_NAV_RESEARCH_PYTHON:-python3}"
pair_dir="${1:-${VISION_NAV_TRAINING_MAP_PAIRS_DIR:-}}"

if [[ -z "$pair_dir" ]]; then
  echo "Usage: $0 /path/to/training-map-pairs" >&2
  exit 1
fi

args=(
  -m vision_nav.neural.teacher_dataset
  --pair-dir "$pair_dir"
  --output "$pair_dir/teacher_dataset_validation.json"
)
if [[ "${VISION_NAV_REQUIRE_HEADING_LABELS:-0}" == "1" ]]; then
  args+=(--require-heading)
fi
if [[ "${VISION_NAV_REQUIRE_POSE_CONTEXT:-0}" == "1" ]]; then
  args+=(--require-pose-context)
fi

PYTHONPATH="$repo_root/src" "$python_bin" "${args[@]}"
