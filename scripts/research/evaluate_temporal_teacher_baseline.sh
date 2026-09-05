#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${VISION_NAV_RESEARCH_PYTHON:-python3}"
checkpoint="${1:-${VISION_NAV_TEMPORAL_TEACHER_CHECKPOINT:-}}"
window_dir="${2:-${VISION_NAV_TEMPORAL_WINDOWS_DIR:-}}"
output="${3:-${VISION_NAV_TEMPORAL_TEACHER_EVALUATION:-}}"

if [[ -z "$checkpoint" || -z "$window_dir" || -z "$output" ]]; then
  echo "Usage: $0 /path/to/temporal_teacher_baseline.pt /path/to/temporal-windows /path/to/evaluation.json" >&2
  exit 1
fi

"$repo_root/scripts/research/validate_temporal_training_windows.sh" "$window_dir" >/dev/null

args=(
  -c 'from vision_nav.neural.temporal_teacher_baseline import evaluate_main; evaluate_main()'
  --checkpoint "$checkpoint"
  --window-dir "$window_dir"
  --device "${VISION_NAV_TEACHER_DEVICE:-auto}"
  --output "$output"
)
if [[ -n "${VISION_NAV_TEACHER_MAX_VALIDATION_BATCHES:-}" ]]; then
  args+=(--max-validation-batches "$VISION_NAV_TEACHER_MAX_VALIDATION_BATCHES")
fi
PYTHONPATH="$repo_root/src" "$python_bin" "${args[@]}"
