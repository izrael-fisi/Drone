#!/usr/bin/env bash
set -euo pipefail

# Exports fixed-cardinality XFeat local features for research parity only.
# It does not create a ModelRelease, transfer artifacts to the Pi, or publish PX4 data.

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${VISION_NAV_RESEARCH_PYTHON:-python3}"
checkout="${1:-${VISION_NAV_XFEAT_CHECKOUT:-}}"
weights="${2:-${VISION_NAV_XFEAT_WEIGHTS:-}}"
output_dir="${3:-${VISION_NAV_XFEAT_SPARSE_EXPORT_DIR:-}}"

if [[ -z "$checkout" || -z "$weights" || -z "$output_dir" ]]; then
  cat >&2 <<'EOF'
Usage:
  ./scripts/research/export_xfeat_sparse_onnx.sh <pinned-xfeat-checkout> <xfeat-weights.pt> <empty-output-dir>

Optional settings:
  VISION_NAV_XFEAT_DEVICE=cpu|cuda          (default: cpu)
  VISION_NAV_XFEAT_IMAGE_WIDTH=640          (must be divisible by 32)
  VISION_NAV_XFEAT_IMAGE_HEIGHT=480         (must be divisible by 32)
  VISION_NAV_XFEAT_MAX_FEATURES=2048
  VISION_NAV_XFEAT_DETECTION_THRESHOLD=0.05
EOF
  exit 2
fi

PYTHONPATH="$repo_root/src" "$python_bin" -c "from vision_nav.neural.xfeat_export import sparse_main; sparse_main()" \
  --checkout "$checkout" \
  --weights "$weights" \
  --output-dir "$output_dir" \
  --upstream-lock "$repo_root/research/upstreams.lock.json" \
  --image-width "${VISION_NAV_XFEAT_IMAGE_WIDTH:-640}" \
  --image-height "${VISION_NAV_XFEAT_IMAGE_HEIGHT:-480}" \
  --maximum-feature-count "${VISION_NAV_XFEAT_MAX_FEATURES:-2048}" \
  --detection-threshold "${VISION_NAV_XFEAT_DETECTION_THRESHOLD:-0.05}" \
  --device "${VISION_NAV_XFEAT_DEVICE:-cpu}"
PYTHONPATH="$repo_root/src" "$python_bin" -c "from vision_nav.neural.xfeat_export import sparse_validate_main; sparse_validate_main()" \
  --export-dir "$output_dir" --output "$output_dir/export_validation.json"
PYTHONPATH="$repo_root/src" "$python_bin" -c "from vision_nav.neural.xfeat_export import sparse_parity_main; sparse_parity_main()" \
  --export-dir "$output_dir" --output "$output_dir/onnx_parity.json"
