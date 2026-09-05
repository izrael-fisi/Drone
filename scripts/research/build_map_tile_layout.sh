#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${VISION_NAV_RESEARCH_PYTHON:-python3}"
cog="${1:-${VISION_NAV_MAP_COG:-}}"
output_dir="${2:-${VISION_NAV_MAP_TILE_LAYOUT_DIR:-}}"

if [[ -z "$cog" || -z "$output_dir" ]]; then
  echo "Usage: VISION_NAV_MAP_ORIGIN_LAT_DEG=... VISION_NAV_MAP_ORIGIN_LON_DEG=... VISION_NAV_MAP_ORIGIN_ALTITUDE_M=... $0 /path/to/map.tif /output/tile-layout" >&2
  exit 1
fi
: "${VISION_NAV_MAP_ORIGIN_LAT_DEG:?Set VISION_NAV_MAP_ORIGIN_LAT_DEG.}"
: "${VISION_NAV_MAP_ORIGIN_LON_DEG:?Set VISION_NAV_MAP_ORIGIN_LON_DEG.}"
: "${VISION_NAV_MAP_ORIGIN_ALTITUDE_M:?Set VISION_NAV_MAP_ORIGIN_ALTITUDE_M.}"

PYTHONPATH="$repo_root/src" "$python_bin" -m vision_nav.neural.map_tile_layout \
  --cog "$cog" \
  --output-dir "$output_dir" \
  --origin-lat-deg "$VISION_NAV_MAP_ORIGIN_LAT_DEG" \
  --origin-lon-deg "$VISION_NAV_MAP_ORIGIN_LON_DEG" \
  --origin-altitude-m "$VISION_NAV_MAP_ORIGIN_ALTITUDE_M" \
  --tile-size-px "${VISION_NAV_MAP_TILE_SIZE_PX:-512}" \
  --stride-px "${VISION_NAV_MAP_TILE_STRIDE_PX:-512}" \
  --min-valid-fraction "${VISION_NAV_MAP_TILE_MIN_VALID_FRACTION:-0.98}"
