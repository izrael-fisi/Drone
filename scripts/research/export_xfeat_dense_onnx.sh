#!/usr/bin/env bash
set -euo pipefail

# Research-only exporter for the exact XFeat source revision in upstreams.lock.
# The output is a dense-backbone artifact, not a ModelRelease or Pi deployment.

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${VISION_NAV_RESEARCH_PYTHON:-python3}"
checkout="${1:-${VISION_NAV_XFEAT_CHECKOUT:-}}"
weights="${2:-${VISION_NAV_XFEAT_WEIGHTS:-}}"
output_dir="${3:-${VISION_NAV_XFEAT_EXPORT_DIR:-}}"

if [[ -z "$checkout" || -z "$weights" || -z "$output_dir" ]]; then
  cat >&2 <<'EOF'
Usage:
  ./scripts/research/export_xfeat_dense_onnx.sh <pinned-xfeat-checkout> <xfeat-weights.pt> <empty-output-dir>

Or set VISION_NAV_XFEAT_CHECKOUT, VISION_NAV_XFEAT_WEIGHTS, and
VISION_NAV_XFEAT_EXPORT_DIR. Optional settings:
  VISION_NAV_XFEAT_DEVICE=cpu|cuda        (default: cpu)
  VISION_NAV_XFEAT_IMAGE_WIDTH=640        (must be divisible by 32)
  VISION_NAV_XFEAT_IMAGE_HEIGHT=480       (must be divisible by 32)

This export stays research-only. It does not build a ModelRelease, transfer to
the Pi, open MAVLink, or enable PX4 external-vision publication.
EOF
  exit 2
fi

args=(
  -m vision_nav.neural.xfeat_export
  --checkout "$checkout"
  --weights "$weights"
  --output-dir "$output_dir"
  --upstream-lock "$repo_root/research/upstreams.lock.json"
  --image-width "${VISION_NAV_XFEAT_IMAGE_WIDTH:-640}"
  --image-height "${VISION_NAV_XFEAT_IMAGE_HEIGHT:-480}"
  --device "${VISION_NAV_XFEAT_DEVICE:-cpu}"
)

PYTHONPATH="$repo_root/src" "$python_bin" "${args[@]}"
PYTHONPATH="$repo_root/src" "$python_bin" -c "from vision_nav.neural.xfeat_export import validate_main; validate_main()" \
  --export-dir "$output_dir" --output "$output_dir/export_validation.json"
PYTHONPATH="$repo_root/src" "$python_bin" -c "from vision_nav.neural.xfeat_export import parity_main; parity_main()" \
  --export-dir "$output_dir" --output "$output_dir/onnx_parity.json"
