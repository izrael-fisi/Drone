#!/usr/bin/env bash
set -euo pipefail

# Research-only compatibility evidence for the official XFeat LighterGlue
# matcher. This does not create ONNX, a ModelRelease, Pi assets, or PX4 output.

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${VISION_NAV_RESEARCH_PYTHON:-python3}"

if [[ $# -ne 3 ]]; then
  cat >&2 <<'EOF'
Usage:
  ./scripts/research/probe_lighterglue.sh <pinned-xfeat-checkout> <xfeat-lighterglue.pt> <empty-output-dir>

Optional environment:
  VISION_NAV_LIGHTERGLUE_FEATURE_COUNT=1024
  VISION_NAV_LIGHTERGLUE_WIDTH=640
  VISION_NAV_LIGHTERGLUE_HEIGHT=384
  VISION_NAV_LIGHTERGLUE_MIN_CONFIDENCE=0.2
  VISION_NAV_RESEARCH_DEVICE=cpu|cuda
EOF
  exit 2
fi

checkout="$1"
weights="$2"
output_dir="$3"

PYTHONPATH="$repo_root/src" "$python_bin" -m vision_nav.neural.lighterglue_probe \
  --checkout "$checkout" \
  --weights "$weights" \
  --output-dir "$output_dir" \
  --upstream-lock "$repo_root/research/upstreams.lock.json" \
  --feature-count "${VISION_NAV_LIGHTERGLUE_FEATURE_COUNT:-1024}" \
  --image-width "${VISION_NAV_LIGHTERGLUE_WIDTH:-640}" \
  --image-height "${VISION_NAV_LIGHTERGLUE_HEIGHT:-384}" \
  --minimum-confidence "${VISION_NAV_LIGHTERGLUE_MIN_CONFIDENCE:-0.2}" \
  --device "${VISION_NAV_RESEARCH_DEVICE:-cpu}"

PYTHONPATH="$repo_root/src" "$python_bin" -m vision_nav.neural.lighterglue_probe --probe-dir "$output_dir"
