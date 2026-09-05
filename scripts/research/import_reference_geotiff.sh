#!/usr/bin/env bash
set -euo pipefail

# Convert a projected, georeferenced GeoTIFF into the first-capture reference
# package. This needs the map-production extra on the research computer, never
# on the Pi flight image.
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${VISION_NAV_RESEARCH_PYTHON:-python3}"
input="${1:-${VISION_NAV_REFERENCE_GEOTIFF:-}}"
output_dir="${2:-${VISION_NAV_REFERENCE_MAP_DIR:-}}"

if [[ -z "$input" || -z "$output_dir" ]]; then
  echo "Usage: $0 /path/to/source-georeferenced.tif /path/to/reference-map-directory" >&2
  exit 1
fi
: "${VISION_NAV_REFERENCE_MAP_ID:?Set VISION_NAV_REFERENCE_MAP_ID.}"
: "${VISION_NAV_REFERENCE_MAP_VERSION:?Set VISION_NAV_REFERENCE_MAP_VERSION.}"
: "${VISION_NAV_CALIBRATION_ID:?Set VISION_NAV_CALIBRATION_ID.}"

args=(
  -m vision_nav.neural.reference_map_import
  --input "$input"
  --output-dir "$output_dir"
  --map-id "$VISION_NAV_REFERENCE_MAP_ID"
  --version "$VISION_NAV_REFERENCE_MAP_VERSION"
  --calibration-id "$VISION_NAV_CALIBRATION_ID"
)
if [[ -n "${VISION_NAV_REFERENCE_ORIGIN_LAT_DEG:-}" ]]; then
  : "${VISION_NAV_REFERENCE_ORIGIN_LON_DEG:?Set VISION_NAV_REFERENCE_ORIGIN_LON_DEG with the latitude.}"
  : "${VISION_NAV_REFERENCE_ORIGIN_ALT_M:?Set VISION_NAV_REFERENCE_ORIGIN_ALT_M with the latitude.}"
  args+=(--origin-wgs84 "$VISION_NAV_REFERENCE_ORIGIN_LAT_DEG" "$VISION_NAV_REFERENCE_ORIGIN_LON_DEG" "$VISION_NAV_REFERENCE_ORIGIN_ALT_M")
fi
if [[ -n "${VISION_NAV_REFERENCE_GSD_M_PER_PX:-}" ]]; then
  args+=(--gsd-m-per-px "$VISION_NAV_REFERENCE_GSD_M_PER_PX")
fi

PYTHONPATH="$repo_root/src" "$python_bin" "${args[@]}"
