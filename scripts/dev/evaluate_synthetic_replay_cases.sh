#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${VISION_NAV_PYTHON:-python3}"
manifest="${VISION_NAV_REPLAY_CASE_MANIFEST:-$repo_root/data/replay_cases/synthetic_smoke/manifest.json}"
output_dir="${VISION_NAV_REPLAY_GATE_OUTPUT_DIR:-$repo_root/data/replay_cases/synthetic_smoke/reports}"

PYTHONPATH="$repo_root/src" "$python_bin" -m vision_nav.replay_case_manifest \
  --manifest "$manifest" \
  --output-dir "$output_dir"
