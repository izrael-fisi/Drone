#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
config="${1:-$repo_root/research/configs/pi5_student.yaml}"
python_bin="${VISION_NAV_RESEARCH_PYTHON:-python3}"
checkpoint="${VISION_NAV_TEMPORAL_TEACHER_CHECKPOINT:-}"
window_dir="${VISION_NAV_TEMPORAL_WINDOWS_DIR:-}"
output_dir="${VISION_NAV_TEACHER_TARGETS_DIR:-}"

test -f "$config" || { echo "Student config is missing: $config" >&2; exit 1; }
if [[ -z "$checkpoint" || -z "$window_dir" || -z "$output_dir" ]]; then
  cat >&2 <<'EOF'
Set VISION_NAV_TEMPORAL_TEACHER_CHECKPOINT, VISION_NAV_TEMPORAL_WINDOWS_DIR,
and VISION_NAV_TEACHER_TARGETS_DIR. This first stage materializes hash-bound
teacher targets only; it does not create a Pi model release or enable PX4.
EOF
  exit 1
fi

"$repo_root/scripts/research/validate_temporal_training_windows.sh" "$window_dir" >/dev/null
args=(
  -m vision_nav.neural.teacher_targets
  --checkpoint "$checkpoint"
  --window-dir "$window_dir"
  --output-dir "$output_dir"
  --device "${VISION_NAV_TEACHER_DEVICE:-auto}"
)
if [[ -n "${VISION_NAV_TEACHER_MAX_TARGET_BATCHES:-}" ]]; then
  args+=(--max-batches-per-split "$VISION_NAV_TEACHER_MAX_TARGET_BATCHES")
fi
PYTHONPATH="$repo_root/src" "$python_bin" "${args[@]}"
