#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
map_source="${VISION_NAV_MAP_SOURCE:-}"
bundle="${VISION_NAV_BUNDLE:-$HOME/drone-data/map_bundles/mission_bundle}"
pipeline="${VISION_NAV_PIPELINE:-classical}"
feature_method="${VISION_NAV_FEATURE_METHOD:-orb}"
max_features="${VISION_NAV_MAX_FEATURES:-3000}"
mission_plan_json="${VISION_NAV_MISSION_PLAN_JSON:-${VISION_NAV_MISSION_PLAN:-}}"
qgc_plan_json="${VISION_NAV_QGC_PLAN_JSON:-}"

if [[ ! -x "$venv_python" ]]; then
  echo "Missing Python venv: $venv_python" >&2
  echo "Run ./scripts/pi/bootstrap_pi5.sh first, then reboot." >&2
  exit 1
fi

if [[ -z "$map_source" ]]; then
  echo "Set VISION_NAV_MAP_SOURCE to a saved map-source folder containing satellite.png and metadata.json." >&2
  echo "Example:" >&2
  echo "  VISION_NAV_MAP_SOURCE=\$HOME/DroneVisionNav/maps/flight-region VISION_NAV_BUNDLE=$bundle $0" >&2
  exit 1
fi

if [[ ! -f "$map_source/satellite.png" || ! -f "$map_source/metadata.json" ]]; then
  echo "Map source must contain satellite.png and metadata.json: $map_source" >&2
  exit 1
fi
if [[ -n "$mission_plan_json" && ! -f "$mission_plan_json" ]]; then
  echo "Mission plan JSON was set but does not exist: $mission_plan_json" >&2
  exit 1
fi
if [[ -n "$qgc_plan_json" && ! -f "$qgc_plan_json" ]]; then
  echo "QGroundControl plan JSON was set but does not exist: $qgc_plan_json" >&2
  exit 1
fi

mkdir -p "$(dirname "$bundle")"

args=(
  --map-source "$map_source"
  --bundle "$bundle"
  --repo "$repo_root"
  --pipeline "$pipeline"
  --feature-method "$feature_method"
  --max-features "$max_features"
  --write-checksums
)
if [[ -n "$mission_plan_json" ]]; then
  args+=(--mission-plan-json "$mission_plan_json")
fi
if [[ -n "$qgc_plan_json" ]]; then
  args+=(--qgc-plan-json "$qgc_plan_json")
fi

PYTHONPATH="$repo_root/src" "$venv_python" -m vision_nav.build_bundle_from_map_source "${args[@]}"

cat <<EOF

Built terrain mission bundle:
  map source:   $map_source
  bundle:       $bundle
  mission plan: ${mission_plan_json:-not provided}
  qgc plan:     ${qgc_plan_json:-not provided}

__VISION_NAV_MAP_SOURCE__=$map_source
__VISION_NAV_BUNDLE__=$bundle
EOF
if [[ -n "$mission_plan_json" ]]; then
  echo "__VISION_NAV_MISSION_PLAN_JSON__=$mission_plan_json"
fi
if [[ -n "$qgc_plan_json" ]]; then
  echo "__VISION_NAV_QGC_PLAN_JSON__=$qgc_plan_json"
fi
