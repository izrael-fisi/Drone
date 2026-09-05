#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${VISION_NAV_PYTHON:-python3}"
model_release="${VISION_NAV_MODEL_RELEASE:-}"
artifact_root="${VISION_NAV_MODEL_ARTIFACT_ROOT:-}"
cases="${VISION_NAV_ONNX_PARITY_CASES:-}"
output="${VISION_NAV_ONNX_PARITY_REPORT:-}"

if [[ -z "$model_release" || -z "$cases" || -z "$output" ]]; then
  cat >&2 <<'EOF'
Set VISION_NAV_MODEL_RELEASE, VISION_NAV_ONNX_PARITY_CASES, and
VISION_NAV_ONNX_PARITY_REPORT after exporting the ONNX artifacts. Optional:
VISION_NAV_MODEL_ARTIFACT_ROOT defaults to the model-release directory.

The cases manifest contains NPZ logical input tensors and the corresponding
PyTorch/cloud-reference output tensors. This script validates the Pi ONNX
release against those immutable references. It does not enable PX4 publication.
EOF
  exit 1
fi

args=(
  -m vision_nav.neural.onnx_parity
  --model-release "$model_release"
  --cases "$cases"
  --output "$output"
  --provider "${VISION_NAV_INFERENCE_PROVIDER:-onnxruntime_cpu}"
  --atol "${VISION_NAV_ONNX_PARITY_ATOL:-0.0001}"
  --rtol "${VISION_NAV_ONNX_PARITY_RTOL:-0.001}"
)
if [[ -n "$artifact_root" ]]; then
  args+=(--artifact-root "$artifact_root")
fi

PYTHONPATH="$repo_root/src" "$python_bin" "${args[@]}"
