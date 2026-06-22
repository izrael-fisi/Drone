#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
manifest="${VISION_NAV_FIELD_MANIFEST:-$HOME/DroneTransfer/outgoing/replay-cases/field_manifest.json}"
output="${VISION_NAV_FIELD_COLLECTION_PLAN:-$HOME/DroneTransfer/outgoing/replay-cases/field_collection_plan.json}"
markdown_output="${VISION_NAV_FIELD_COLLECTION_PLAN_MD:-$HOME/DroneTransfer/outgoing/replay-cases/field_collection_plan.md}"
site_name="${VISION_NAV_FIELD_SITE_NAME:-field-site}"
bundle="${VISION_NAV_FIELD_BUNDLE:-${VISION_NAV_BUNDLE:-$HOME/drone-data/map_bundles/mission_bundle}}"
source_log="${VISION_NAV_FIELD_LOG:-$HOME/DroneTransfer/outgoing/terrain-match/terrain_matches.jsonl}"
capture_root="${VISION_NAV_FIELD_CAPTURE_ROOT:-$HOME/DroneTransfer/outgoing/field-captures}"

usage() {
  cat >&2 <<EOF
Usage:
  ./scripts/pi/create_field_collection_plan.sh

Common optional overrides:
  VISION_NAV_FIELD_MANIFEST              Default: $manifest
  VISION_NAV_FIELD_COLLECTION_PLAN       Default: $output
  VISION_NAV_FIELD_COLLECTION_PLAN_MD    Default: $markdown_output
  VISION_NAV_FIELD_SITE_NAME             Default: $site_name
  VISION_NAV_FIELD_BUNDLE                Default: $bundle
  VISION_NAV_FIELD_LOG                   Default: $source_log
  VISION_NAV_FIELD_CAPTURE_ROOT          Default: $capture_root
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

mkdir -p "$(dirname "$output")" "$(dirname "$markdown_output")"

PYTHONPATH="$repo_root/src" "$venv_python" -m vision_nav.field_collection_plan \
  --manifest "$manifest" \
  --output "$output" \
  --markdown-output "$markdown_output" \
  --site-name "$site_name" \
  --bundle "$bundle" \
  --source-log "$source_log" \
  --capture-root "$capture_root"

cat <<EOF

Field collection plan output:
  json:     $output
  markdown: $markdown_output

Use the Markdown checklist during field collection, then register each captured
condition with ./scripts/pi/register_field_replay_case.sh.

__VISION_NAV_FIELD_COLLECTION_PLAN__=$output
__VISION_NAV_FIELD_COLLECTION_PLAN_MD__=$markdown_output
EOF
