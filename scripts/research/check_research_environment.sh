#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${VISION_NAV_RESEARCH_PYTHON:-python3}"
output="${VISION_NAV_RESEARCH_ENVIRONMENT_REPORT:-$repo_root/research/environment_report.json}"

args=(
  -m vision_nav.neural.research_environment
  --require-cuda
  --require-map-production
  --require-onnx
  --require-lighterglue
  --output "$output"
)
PYTHONPATH="$repo_root/src" "$python_bin" "${args[@]}"
