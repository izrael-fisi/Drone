#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${VISION_NAV_RESEARCH_PYTHON:-python3}"
window_dir="${VISION_NAV_TEMPORAL_WINDOWS_DIR:-}"
target_dir="${VISION_NAV_TEACHER_TARGETS_DIR:-}"
output_dir="${VISION_NAV_PI_STUDENT_OUTPUT_DIR:-}"

if [[ -z "$window_dir" || -z "$target_dir" || -z "$output_dir" ]]; then
  cat >&2 <<'EOF'
Set VISION_NAV_TEMPORAL_WINDOWS_DIR, VISION_NAV_TEACHER_TARGETS_DIR, and
VISION_NAV_PI_STUDENT_OUTPUT_DIR before training the compact Pi student.
EOF
  exit 1
fi

args=(
  -m vision_nav.neural.pi_temporal_student
  --window-dir "$window_dir"
  --target-dir "$target_dir"
  --output-dir "$output_dir"
  --device "${VISION_NAV_STUDENT_DEVICE:-auto}"
  --epochs "${VISION_NAV_STUDENT_EPOCHS:-1}"
  --learning-rate "${VISION_NAV_STUDENT_LEARNING_RATE:-0.0001}"
  --seed "${VISION_NAV_STUDENT_SEED:-7}"
  --image-width "${VISION_NAV_STUDENT_IMAGE_WIDTH:-320}"
  --image-height "${VISION_NAV_STUDENT_IMAGE_HEIGHT:-320}"
  --feature-dim "${VISION_NAV_STUDENT_FEATURE_DIM:-64}"
  --teacher-feature-dim "${VISION_NAV_STUDENT_TEACHER_FEATURE_DIM:-128}"
  --recurrent-layers "${VISION_NAV_STUDENT_RECURRENT_LAYERS:-1}"
)
if [[ -n "${VISION_NAV_STUDENT_MAX_TRAIN_BATCHES:-}" ]]; then args+=(--max-train-batches "$VISION_NAV_STUDENT_MAX_TRAIN_BATCHES"); fi
if [[ -n "${VISION_NAV_STUDENT_MAX_VALIDATION_BATCHES:-}" ]]; then args+=(--max-validation-batches "$VISION_NAV_STUDENT_MAX_VALIDATION_BATCHES"); fi
PYTHONPATH="$repo_root/src" "$python_bin" "${args[@]}"
