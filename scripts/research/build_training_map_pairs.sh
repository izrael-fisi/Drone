#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${VISION_NAV_RESEARCH_PYTHON:-python3}"
experiment_dir="${1:-${VISION_NAV_TEACHER_EXPERIMENT_DIR:-}}"
map_source="${2:-${VISION_NAV_MAP_RELEASE:-${VISION_NAV_REFERENCE_MAP:-}}}"
output_dir="${3:-${VISION_NAV_TRAINING_MAP_PAIRS_DIR:-}}"

if [[ -z "$experiment_dir" || -z "$map_source" || -z "$output_dir" ]]; then
  echo "Usage: $0 /path/to/teacher-experiment /path/to/map-release-or-reference-map.json /path/to/training-map-pairs" >&2
  exit 1
fi

args=(
  -m vision_nav.neural.training_map_pairs
  --teacher-experiment "$experiment_dir"
  --output-dir "$output_dir"
  --negative-count "${VISION_NAV_TRAINING_NEGATIVE_COUNT:-4}"
  --min-negative-distance-m "${VISION_NAV_TRAINING_MIN_NEGATIVE_DISTANCE_M:-75}"
  --max-positive-center-distance-m "${VISION_NAV_TRAINING_MAX_POSITIVE_CENTER_DISTANCE_M:-100}"
)
if [[ -n "${VISION_NAV_REFERENCE_MAP:-}" ]]; then
  : "${VISION_NAV_REFERENCE_TILE_LAYOUT:?Set VISION_NAV_REFERENCE_TILE_LAYOUT for a ReferenceMapRelease.}"
  args+=(--reference-map "$map_source" --tile-layout "$VISION_NAV_REFERENCE_TILE_LAYOUT")
else
  args+=(--map-release "$map_source")
fi
PYTHONPATH="$repo_root/src" "$python_bin" "${args[@]}"
