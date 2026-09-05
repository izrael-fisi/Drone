#!/usr/bin/env bash
set -euo pipefail

# Run explicitly on an already provisioned Linux host. Never modifies system Python.
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
environment="${1:-}"
if [[ -z "$environment" || "$environment" != /* ]]; then
  echo "Usage: bash $0 /absolute/path/to/new-bearing-venv" >&2
  exit 2
fi
if [[ "$(uname -s)" != Linux || "$(uname -m)" != x86_64 ]]; then
  echo "This lock targets Linux x86_64 only; do not install it on the Pi or Mac." >&2
  exit 2
fi
if [[ -e "$environment" || -L "$environment" ]]; then
  echo "Environment already exists; choose a new path instead of replacing it." >&2
  exit 2
fi
command -v uv >/dev/null || { echo "Install uv on the research host first." >&2; exit 2; }
uv venv --python 3.11 "$environment"
uv pip sync --python "$environment/bin/python" --require-hashes --no-build \
  "$repo_root/research/requirements-bearinguav-linux.lock"
uv pip check --python "$environment/bin/python"
PYTHONPATH="$repo_root/src" "$environment/bin/python" -m vision_nav.neural.research_environment \
  --require-cuda --exercise-torch \
  --bearing-reference-requirements "$repo_root/research/requirements-bearinguav-reference.txt" \
  --output "$environment/bearing-environment.json"
echo "Environment preflight passed. Model/data validation and a bounded training run are still required."
