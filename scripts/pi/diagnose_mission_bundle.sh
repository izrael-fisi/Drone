#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
bundle="${VISION_NAV_BUNDLE:-$HOME/drone-data/map_bundles/mission_bundle}"

usage() {
  cat >&2 <<EOF
Usage:
  ./scripts/pi/diagnose_mission_bundle.sh

Scans for the expected terrain mission bundle, nearby bundle candidates, and
raw Mission Planner map sources with satellite.png plus metadata.json.

Common optional overrides:
  VISION_NAV_BUNDLE               Default: $bundle
  VISION_NAV_BUNDLE_SEARCH_ROOTS  Optional colon-separated scan roots
  VISION_NAV_BUNDLE_DIAGNOSTIC_JSON
                                  Optional path to write JSON diagnostic output
  VISION_NAV_BUNDLE_DIAGNOSTIC_FORMAT
                                  Set to json to print JSON without writing a file
  VISION_NAV_PYTHON               Default: $venv_python
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ "$venv_python" == */* ]]; then
  if [[ ! -x "$venv_python" ]]; then
    echo "Missing Python venv: $venv_python" >&2
    echo "Run ./scripts/pi/bootstrap_pi5.sh first, then reboot." >&2
    exit 1
  fi
elif ! command -v "$venv_python" >/dev/null 2>&1; then
  echo "Python command not found: $venv_python" >&2
  echo "Run ./scripts/pi/bootstrap_pi5.sh first, then reboot, or set VISION_NAV_PYTHON." >&2
  exit 1
fi

json_output="${VISION_NAV_BUNDLE_DIAGNOSTIC_JSON:-}"
format="${VISION_NAV_BUNDLE_DIAGNOSTIC_FORMAT:-text}"

if [[ -n "$json_output" ]]; then
  mkdir -p "$(dirname "$json_output")"
  PYTHONPATH="$repo_root/src" "$venv_python" -m vision_nav.bundle_diagnostics --bundle "$bundle" --json | tee "$json_output"
  echo "__VISION_NAV_BUNDLE_DIAGNOSTIC__=$json_output"
elif [[ "$format" == "json" ]]; then
  PYTHONPATH="$repo_root/src" "$venv_python" -m vision_nav.bundle_diagnostics --bundle "$bundle" --json
else
  PYTHONPATH="$repo_root/src" "$venv_python" -m vision_nav.bundle_diagnostics --bundle "$bundle"
fi
