#!/usr/bin/env bash
set -euo pipefail

# Offline report only: no camera, MAVLink connection or model inference.
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
if [[ ! -x "$python" ]]; then
  echo "Missing runtime Python: $python" >&2
  exit 1
fi
PYTHONPATH="$repo_root/src${PYTHONPATH:+:$PYTHONPATH}" exec "$python" -m vision_nav.neural.runtime_load_report "$@"
