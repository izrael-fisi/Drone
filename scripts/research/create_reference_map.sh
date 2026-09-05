#!/usr/bin/env bash
set -euo pipefail

# Create the model-independent map record needed before first field capture.
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${VISION_NAV_RESEARCH_PYTHON:-python3}"
output="${1:-${VISION_NAV_REFERENCE_MAP_OUTPUT:-}}"

if [[ -z "$output" ]]; then
  echo "Usage: $0 /path/to/reference-map/reference_map.json" >&2
  exit 1
fi
: "${VISION_NAV_REFERENCE_MAP_ID:?Set VISION_NAV_REFERENCE_MAP_ID.}"
: "${VISION_NAV_REFERENCE_MAP_VERSION:?Set VISION_NAV_REFERENCE_MAP_VERSION.}"
: "${VISION_NAV_REFERENCE_MAP_CRS:?Set VISION_NAV_REFERENCE_MAP_CRS.}"
: "${VISION_NAV_REFERENCE_ORIGIN_LAT_DEG:?Set VISION_NAV_REFERENCE_ORIGIN_LAT_DEG.}"
: "${VISION_NAV_REFERENCE_ORIGIN_LON_DEG:?Set VISION_NAV_REFERENCE_ORIGIN_LON_DEG.}"
: "${VISION_NAV_REFERENCE_ORIGIN_ALT_M:?Set VISION_NAV_REFERENCE_ORIGIN_ALT_M.}"
: "${VISION_NAV_REFERENCE_GSD_M_PER_PX:?Set VISION_NAV_REFERENCE_GSD_M_PER_PX.}"
: "${VISION_NAV_REFERENCE_IMAGERY_URI:?Set VISION_NAV_REFERENCE_IMAGERY_URI relative to the output directory.}"
: "${VISION_NAV_REFERENCE_STAC_URI:?Set VISION_NAV_REFERENCE_STAC_URI relative to the output directory.}"
: "${VISION_NAV_CALIBRATION_ID:?Set VISION_NAV_CALIBRATION_ID.}"

PYTHONPATH="$repo_root/src" "$python_bin" -m vision_nav.neural.reference_map create \
  --output "$output" \
  --map-id "$VISION_NAV_REFERENCE_MAP_ID" \
  --version "$VISION_NAV_REFERENCE_MAP_VERSION" \
  --crs "$VISION_NAV_REFERENCE_MAP_CRS" \
  --origin-enu 0 0 0 \
  --origin-wgs84 "$VISION_NAV_REFERENCE_ORIGIN_LAT_DEG" "$VISION_NAV_REFERENCE_ORIGIN_LON_DEG" "$VISION_NAV_REFERENCE_ORIGIN_ALT_M" \
  --gsd-m-per-px "$VISION_NAV_REFERENCE_GSD_M_PER_PX" \
  --imagery-uri "$VISION_NAV_REFERENCE_IMAGERY_URI" \
  --stac-item-uri "$VISION_NAV_REFERENCE_STAC_URI" \
  --calibration-id "$VISION_NAV_CALIBRATION_ID"
