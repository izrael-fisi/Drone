#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
manifest="${VISION_NAV_FIELD_MANIFEST:-$HOME/DroneTransfer/outgoing/replay-cases/field_manifest.json}"
condition="${VISION_NAV_FIELD_CONDITION:-}"
metadata_json="${VISION_NAV_FIELD_CAPTURE_METADATA:-}"

usage() {
  cat >&2 <<EOF
Usage:
  VISION_NAV_FIELD_CONDITION=good_texture \\
  VISION_NAV_FIELD_OPERATOR="operator name" \\
  VISION_NAV_FIELD_LOCATION_LABEL="test area" \\
  VISION_NAV_FIELD_ALTITUDE_AGL_M=35 \\
  VISION_NAV_FIELD_SPEED_MPS=4 \\
  VISION_NAV_FIELD_LIGHTING=nominal \\
  VISION_NAV_FIELD_WEATHER=clear \\
  VISION_NAV_FIELD_TERRAIN_TEXTURE=distinct \\
  VISION_NAV_FIELD_MAP_AGE_OR_SEASON_NOTES="same season" \\
  VISION_NAV_FIELD_CAMERA_FOCUS_EXPOSURE_NOTES="manual focus checked" \\
  VISION_NAV_FIELD_IMU_PX4_STATE_NOTES="EKF stable before capture" \\
  VISION_NAV_FIELD_SAFETY_NOTES="spotter present" \\
  ./scripts/pi/update_field_capture_metadata.sh

Common optional overrides:
  VISION_NAV_FIELD_MANIFEST          Default: $manifest
  VISION_NAV_FIELD_CAPTURE_METADATA  Optional JSON object merged first.
  VISION_NAV_FIELD_CAPTURE_DATE_UTC  Optional timestamp; defaults to current UTC.
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

if [[ -z "$condition" ]]; then
  echo "VISION_NAV_FIELD_CONDITION is required." >&2
  usage
  exit 2
fi

metadata_args=(
  -m vision_nav.field_capture_metadata_update
  --manifest "$manifest"
  --condition "$condition"
)

if [[ -n "$metadata_json" ]]; then
  metadata_args+=(--json-updates "$metadata_json")
fi

add_arg() {
  local value="$1"
  local flag="$2"
  if [[ -n "$value" ]]; then
    metadata_args+=("$flag" "$value")
  fi
}

add_arg "${VISION_NAV_FIELD_OPERATOR:-}" --operator
add_arg "${VISION_NAV_FIELD_CAPTURE_DATE_UTC:-$(date -u +%Y-%m-%dT%H:%M:%SZ)}" --capture-date-utc
add_arg "${VISION_NAV_FIELD_LOCATION_LABEL:-}" --location-label
add_arg "${VISION_NAV_FIELD_ALTITUDE_AGL_M:-}" --altitude-agl-m
add_arg "${VISION_NAV_FIELD_SPEED_MPS:-}" --speed-mps
add_arg "${VISION_NAV_FIELD_LIGHTING:-}" --lighting
add_arg "${VISION_NAV_FIELD_WEATHER:-}" --weather
add_arg "${VISION_NAV_FIELD_TERRAIN_TEXTURE:-}" --terrain-texture
add_arg "${VISION_NAV_FIELD_MAP_AGE_OR_SEASON_NOTES:-}" --map-age-or-season-notes
add_arg "${VISION_NAV_FIELD_CAMERA_FOCUS_EXPOSURE_NOTES:-}" --camera-focus-exposure-notes
add_arg "${VISION_NAV_FIELD_IMU_PX4_STATE_NOTES:-}" --imu-px4-state-notes
add_arg "${VISION_NAV_FIELD_SAFETY_NOTES:-}" --safety-notes
add_arg "${VISION_NAV_FIELD_NOTES:-}" --notes

PYTHONPATH="$repo_root/src" "$venv_python" "${metadata_args[@]}"

./scripts/pi/create_field_collection_plan.sh

cat <<EOF

Field capture metadata updated:
  manifest: $manifest
  condition: $condition

__VISION_NAV_FIELD_MANIFEST__=$manifest
EOF
