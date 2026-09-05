#!/usr/bin/env bash
set -euo pipefail

# Build teacher-only map crops from a model-independent reference map. This is
# not a learned MapRelease and is never transferred to the Pi runtime.
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${VISION_NAV_RESEARCH_PYTHON:-python3}"
reference_map="${1:-${VISION_NAV_REFERENCE_MAP:-}}"
output_dir="${2:-${VISION_NAV_REFERENCE_TILE_LAYOUT_DIR:-}}"

if [[ -z "$reference_map" || -z "$output_dir" ]]; then
  echo "Usage: $0 /path/to/reference_map.json /path/to/reference-tile-layout" >&2
  exit 1
fi

PYTHONPATH="$repo_root/src" "$python_bin" -m vision_nav.neural.map_tile_layout \
  --reference-map "$reference_map" \
  --output-dir "$output_dir" \
  --tile-size-px "${VISION_NAV_MAP_TILE_SIZE_PX:-512}" \
  --stride-px "${VISION_NAV_MAP_TILE_STRIDE_PX:-512}" \
  --min-valid-fraction "${VISION_NAV_MAP_TILE_MIN_VALID_FRACTION:-0.98}"
