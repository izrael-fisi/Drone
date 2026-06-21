#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
output="${VISION_NAV_FIELD_TEMPLATE:-$HOME/DroneTransfer/outgoing/replay-cases/field_manifest.template.json}"
manifest="${VISION_NAV_FIELD_MANIFEST:-$HOME/DroneTransfer/outgoing/replay-cases/field_manifest.json}"
site_name="${VISION_NAV_FIELD_SITE_NAME:-field-site}"
bundle="${VISION_NAV_FIELD_BUNDLE:-${VISION_NAV_BUNDLE:-$HOME/drone-data/map_bundles/mission_bundle}}"
log_root="${VISION_NAV_FIELD_TEMPLATE_LOG_ROOT:-field}"
force="${VISION_NAV_FIELD_TEMPLATE_FORCE:-0}"
seed_manifest="${VISION_NAV_FIELD_TEMPLATE_SEED_MANIFEST:-1}"
seed_force="${VISION_NAV_FIELD_TEMPLATE_SEED_FORCE:-0}"

usage() {
  cat >&2 <<EOF
Usage:
  ./scripts/pi/create_field_evidence_template.sh

Common optional overrides:
  VISION_NAV_FIELD_TEMPLATE            Default: $output
  VISION_NAV_FIELD_MANIFEST            Default: $manifest
  VISION_NAV_FIELD_SITE_NAME           Default: $site_name
  VISION_NAV_FIELD_BUNDLE              Default: $bundle
  VISION_NAV_FIELD_TEMPLATE_LOG_ROOT   Default: $log_root
  VISION_NAV_FIELD_TEMPLATE_FORCE=1    Overwrite an existing template.
  VISION_NAV_FIELD_TEMPLATE_SEED_MANIFEST=0  Do not seed active field_manifest.json.
  VISION_NAV_FIELD_TEMPLATE_SEED_FORCE=1     Overwrite active field_manifest.json from the template.
EOF
}

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

mkdir -p "$(dirname "$output")"

args=(
  -m vision_nav.field_evidence_template
  --output "$output"
  --site-name "$site_name"
  --bundle "$bundle"
  --log-root "$log_root"
)

if [[ "$seed_manifest" == "1" || "$seed_manifest" == "true" ]]; then
  args+=(--seed-manifest "$manifest")
fi
if [[ "$force" == "1" || "$force" == "true" ]]; then
  args+=(--force)
fi
if [[ "$seed_force" == "1" || "$seed_force" == "true" ]]; then
  args+=(--seed-force)
fi

set +e
PYTHONPATH="$repo_root/src" "$venv_python" "${args[@]}"
template_status=$?
set -e

if [[ "$template_status" -ne 0 ]]; then
  echo
  echo "Field evidence template was not written." >&2
  echo "Set VISION_NAV_FIELD_TEMPLATE_FORCE=1 to overwrite an existing starter template." >&2
  usage
  exit "$template_status"
fi

cat <<EOF

Field evidence template output:
  template: $output
  active manifest: $manifest

Next:
  1. Capture real field logs for each required condition.
  2. Register logs into the seeded active manifest with:
     ./scripts/pi/register_field_replay_case.sh
  3. Run:
     ./scripts/pi/run_threshold_tuning_report.sh

__VISION_NAV_FIELD_TEMPLATE__=$output
__VISION_NAV_FIELD_MANIFEST__=$manifest
EOF
