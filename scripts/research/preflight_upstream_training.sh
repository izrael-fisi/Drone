#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${VISION_NAV_RESEARCH_PYTHON:-python3}"
workflow="${1:-${VISION_NAV_UPSTREAM_WORKFLOW:-}}"
checkout="${2:-${VISION_NAV_UPSTREAM_CHECKOUT:-}}"
dataset_root="${3:-${VISION_NAV_UPSTREAM_DATASET_ROOT:-}}"
checkpoint="${VISION_NAV_UPSTREAM_CHECKPOINT:-}"

if [[ -z "$workflow" || -z "$checkout" || -z "$dataset_root" ]]; then
  echo "Usage: $0 <workflow-id> <immutable-checkout> <dataset-root>" >&2
  echo "Optional: VISION_NAV_UPSTREAM_CHECKPOINT=/path/to/first-stage-checkpoint" >&2
  exit 1
fi

args=(
  -m vision_nav.neural.upstream_training
  --lock "$repo_root/research/upstreams.lock.json"
  --workflow "$workflow"
  --checkout "$checkout"
  --dataset-root "$dataset_root"
)
if [[ -n "$checkpoint" ]]; then
  args+=(--checkpoint "$checkpoint")
fi

PYTHONPATH="$repo_root/src" "$python_bin" "${args[@]}"
