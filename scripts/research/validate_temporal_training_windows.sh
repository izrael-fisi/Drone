#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${VISION_NAV_RESEARCH_PYTHON:-python3}"
window_dir="${1:-${VISION_NAV_TEMPORAL_WINDOWS_DIR:-}}"

if [[ -z "$window_dir" ]]; then
  echo "Usage: $0 /path/to/temporal-windows" >&2
  exit 1
fi

args=(
  --window-dir "$window_dir"
  --output "$window_dir/temporal_windows_validation.json"
)
if [[ "${VISION_NAV_ALLOW_MISSING_POSE_CONTEXT:-0}" == "1" ]]; then
  args+=(--allow-missing-pose-context)
fi
PYTHONPATH="$repo_root/src" "$python_bin" -c 'from vision_nav.neural.temporal_training_windows import validate_main; validate_main()' "${args[@]}"
