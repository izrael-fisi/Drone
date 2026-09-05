#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${VISION_NAV_RESEARCH_PYTHON:-python3}"
tile_layout="${1:-${VISION_NAV_TILE_LAYOUT:-}}"

if [[ -z "$tile_layout" ]]; then
  echo "Usage: $0 /path/to/map-release-staging/tile_layout.json" >&2
  exit 1
fi
: "${VISION_NAV_MAP_RELEASE_ID:?Set the immutable target ID, for example field-area@1.0.0.}"
: "${VISION_NAV_MODEL_RELEASE:?Set the audited ModelRelease JSON path.}"

args=(
  -m vision_nav.neural.map_asset_compiler
  --tile-layout "$tile_layout"
  --map-release-id "$VISION_NAV_MAP_RELEASE_ID"
  --model-release "$VISION_NAV_MODEL_RELEASE"
  --index-format "${VISION_NAV_RETRIEVAL_INDEX_FORMAT:-numpy_flat}"
  --provider "${VISION_NAV_INFERENCE_PROVIDER:-onnxruntime_cpu}"
)
if [[ -n "${VISION_NAV_MODEL_ARTIFACT_ROOT:-}" ]]; then
  args+=(--artifact-root "$VISION_NAV_MODEL_ARTIFACT_ROOT")
fi

PYTHONPATH="$repo_root/src" "$python_bin" "${args[@]}"
