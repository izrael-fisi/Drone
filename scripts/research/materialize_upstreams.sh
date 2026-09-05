#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${VISION_NAV_RESEARCH_PYTHON:-python3}"
lock_path="${VISION_NAV_UPSTREAM_LOCK:-$repo_root/research/upstreams.lock.json}"
destination="${VISION_NAV_UPSTREAM_DIR:-$repo_root/research/vendor}"

PYTHONPATH="$repo_root/src" "$python_bin" -m vision_nav.neural.upstreams \
  --lock "$lock_path" \
  --materialize-dir "$destination"
