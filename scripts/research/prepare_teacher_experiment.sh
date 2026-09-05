#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${VISION_NAV_RESEARCH_PYTHON:-python3}"
split_dir="${1:-${VISION_NAV_TRAINING_SPLIT_DIR:-}}"
labels_dir="${2:-${VISION_NAV_TRAINING_LABEL_DIR:-}}"
output_dir="${3:-${VISION_NAV_TEACHER_EXPERIMENT_DIR:-}}"

if [[ -z "$split_dir" || -z "$labels_dir" || -z "$output_dir" ]]; then
  echo "Usage: $0 /path/to/training-split /path/to/training-labels /path/to/teacher-experiment" >&2
  exit 1
fi

args=(
  -m vision_nav.neural.training_experiment
  --split-dir "$split_dir"
  --labels-dir "$labels_dir"
  --output-dir "$output_dir"
)
if [[ "${VISION_NAV_REQUIRE_HEADING_LABELS:-0}" == "1" ]]; then
  args+=(--require-heading)
fi

PYTHONPATH="$repo_root/src" "$python_bin" "${args[@]}"
