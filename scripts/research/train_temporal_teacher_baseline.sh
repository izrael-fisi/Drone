#!/usr/bin/env bash
set -euo pipefail

# Project-owned integration baseline. This is not a substitute for a pinned
# Video2BEV reproduction and must remain research-only until promotion gates.
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${VISION_NAV_RESEARCH_PYTHON:-python3}"
window_dir="${1:-${VISION_NAV_TEMPORAL_WINDOWS_DIR:-}}"
output_dir="${2:-${VISION_NAV_TEMPORAL_TEACHER_OUTPUT_DIR:-}}"

if [[ -z "$window_dir" || -z "$output_dir" ]]; then
  echo "Usage: $0 /path/to/temporal-windows /path/to/teacher-baseline-run" >&2
  exit 1
fi

"$repo_root/scripts/research/validate_temporal_training_windows.sh" "$window_dir" >/dev/null

args=(
  -m vision_nav.neural.temporal_teacher_baseline
  --window-dir "$window_dir"
  --output-dir "$output_dir"
  --device "${VISION_NAV_TEACHER_DEVICE:-auto}"
  --epochs "${VISION_NAV_TEACHER_EPOCHS:-1}"
  --batch-size 1
  --learning-rate "${VISION_NAV_TEACHER_LEARNING_RATE:-0.0001}"
  --seed "${VISION_NAV_TEACHER_SEED:-7}"
  --image-width "${VISION_NAV_TEACHER_IMAGE_WIDTH:-320}"
  --image-height "${VISION_NAV_TEACHER_IMAGE_HEIGHT:-320}"
  --feature-dim "${VISION_NAV_TEACHER_FEATURE_DIM:-128}"
  --temporal-layers "${VISION_NAV_TEACHER_TEMPORAL_LAYERS:-2}"
  --temporal-heads "${VISION_NAV_TEACHER_TEMPORAL_HEADS:-4}"
)
if [[ -n "${VISION_NAV_TEACHER_MAX_TRAIN_BATCHES:-}" ]]; then
  args+=(--max-train-batches "$VISION_NAV_TEACHER_MAX_TRAIN_BATCHES")
fi
if [[ -n "${VISION_NAV_TEACHER_MAX_VALIDATION_BATCHES:-}" ]]; then
  args+=(--max-validation-batches "$VISION_NAV_TEACHER_MAX_VALIDATION_BATCHES")
fi
if [[ -n "${MLFLOW_TRACKING_URI:-}" ]]; then
  args+=(--mlflow-tracking-uri "$MLFLOW_TRACKING_URI")
  args+=(--mlflow-experiment "${VISION_NAV_MLFLOW_EXPERIMENT:-drone-vision-nav-teachers}")
fi
if [[ -n "${VISION_NAV_MLFLOW_RUN_NAME:-}" ]]; then
  args+=(--mlflow-run-name "$VISION_NAV_MLFLOW_RUN_NAME")
fi
PYTHONPATH="$repo_root/src" "$python_bin" "${args[@]}"
