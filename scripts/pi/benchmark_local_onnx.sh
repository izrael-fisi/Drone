#!/usr/bin/env bash
set -euo pipefail

# Run only on an idle, prop-off bench. No camera or vehicle connection is opened.
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
if [[ ! -x "$python" ]]; then
  echo "Missing runtime Python: $python" >&2
  exit 1
fi
if [[ "${VISION_NAV_BENCH_IDLE_PROP_OFF:-}" != "yes" ]]; then
  echo "Confirm propellers removed and no flight/capture workload: VISION_NAV_BENCH_IDLE_PROP_OFF=yes" >&2
  exit 1
fi
PYTHONPATH="$repo_root/src${PYTHONPATH:+:$PYTHONPATH}" exec "$python" -m vision_nav.neural.local_benchmark --require-pi "$@"
