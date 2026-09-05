#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${VISION_NAV_RESEARCH_PYTHON:-python3}"
index_dir="${1:-${VISION_NAV_TRAINING_INDEX_DIR:-}}"
output_dir="${2:-${VISION_NAV_TRAINING_LABEL_DIR:-}}"

if [[ -z "$index_dir" || -z "$output_dir" ]]; then
  echo "Usage: $0 /path/to/training-index /path/to/training-labels" >&2
  exit 1
fi

args=(
  -m vision_nav.neural.training_labels
  --index-dir "$index_dir"
  --output-dir "$output_dir"
  --min-gps-fix-type "${VISION_NAV_MIN_GPS_FIX_TYPE:-3}"
  --max-gps-age-ms "${VISION_NAV_MAX_GPS_LABEL_AGE_MS:-500}"
  --max-gps-horizontal-accuracy-m "${VISION_NAV_MAX_GPS_HORIZONTAL_ACCURACY_M:-10}"
)
if [[ "${VISION_NAV_ALLOW_MISSING_GPS_FIX_TYPE:-0}" == "1" ]]; then
  args+=(--allow-missing-fix-type)
fi
if [[ "${VISION_NAV_ALLOW_MISSING_GPS_HORIZONTAL_ACCURACY:-0}" == "1" ]]; then
  args+=(--allow-missing-gps-horizontal-accuracy)
fi
if [[ -n "${VISION_NAV_MAP_RELEASE:-}" ]]; then
  args+=(--map-release "$VISION_NAV_MAP_RELEASE")
fi
if [[ -n "${VISION_NAV_REFERENCE_MAP:-}" ]]; then
  args+=(--reference-map "$VISION_NAV_REFERENCE_MAP")
fi
if [[ -n "${VISION_NAV_LABEL_ORIGIN_LAT_DEG:-}" ]]; then
  : "${VISION_NAV_LABEL_ORIGIN_LON_DEG:?Set VISION_NAV_LABEL_ORIGIN_LON_DEG with VISION_NAV_LABEL_ORIGIN_LAT_DEG.}"
  : "${VISION_NAV_LABEL_ORIGIN_ALTITUDE_M:?Set VISION_NAV_LABEL_ORIGIN_ALTITUDE_M with VISION_NAV_LABEL_ORIGIN_LAT_DEG.}"
  args+=(
    --origin-lat-deg "$VISION_NAV_LABEL_ORIGIN_LAT_DEG"
    --origin-lon-deg "$VISION_NAV_LABEL_ORIGIN_LON_DEG"
    --origin-altitude-m "$VISION_NAV_LABEL_ORIGIN_ALTITUDE_M"
  )
fi

PYTHONPATH="$repo_root/src" "$python_bin" "${args[@]}"
