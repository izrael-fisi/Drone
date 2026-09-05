#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${VISION_NAV_RESEARCH_PYTHON:-python3}"
checkpoint="${VISION_NAV_PI_STUDENT_CHECKPOINT:-}"
window_dir="${VISION_NAV_TEMPORAL_WINDOWS_DIR:-}"
target_dir="${VISION_NAV_TEACHER_TARGETS_DIR:-}"
output_dir="${VISION_NAV_PI_STUDENT_EXPORT_DIR:-}"

if [[ -z "$checkpoint" || -z "$window_dir" || -z "$target_dir" || -z "$output_dir" ]]; then
  echo "Set VISION_NAV_PI_STUDENT_CHECKPOINT, VISION_NAV_TEMPORAL_WINDOWS_DIR, VISION_NAV_TEACHER_TARGETS_DIR, and VISION_NAV_PI_STUDENT_EXPORT_DIR." >&2
  exit 1
fi

PYTHONPATH="$repo_root/src" "$python_bin" -m vision_nav.neural.pi_temporal_student_export \
  --checkpoint "$checkpoint" \
  --window-dir "$window_dir" \
  --target-dir "$target_dir" \
  --output-dir "$output_dir" \
  --device "${VISION_NAV_STUDENT_EXPORT_DEVICE:-auto}" \
  --opset-version "${VISION_NAV_STUDENT_ONNX_OPSET:-17}"

# Hashes, provenance and tensor layouts must be valid before ONNX Runtime opens
# the graph. This gives a clean failure for a corrupted transfer or bad export.
PYTHONPATH="$repo_root/src" "$python_bin" -c 'from vision_nav.neural.pi_temporal_student_export import validate_main; validate_main()' \
  --export-dir "$output_dir" \
  --output "$output_dir/export_validation.json"

PYTHONPATH="$repo_root/src" "$python_bin" -c 'from vision_nav.neural.pi_temporal_student_export import parity_main; parity_main()' \
  --export-dir "$output_dir" \
  --output "$output_dir/onnx_parity.json" \
  --atol "${VISION_NAV_ONNX_PARITY_ATOL:-0.0001}" \
  --rtol "${VISION_NAV_ONNX_PARITY_RTOL:-0.001}"
