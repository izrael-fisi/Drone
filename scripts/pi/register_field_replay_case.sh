#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
manifest="${VISION_NAV_FIELD_MANIFEST:-$HOME/DroneTransfer/outgoing/replay-cases/field_manifest.json}"
report="${VISION_NAV_FIELD_EVIDENCE_REPORT:-$HOME/DroneTransfer/outgoing/replay-cases/field_evidence_report.json}"
case_report_dir="${VISION_NAV_FIELD_CASE_REPORT_DIR:-$HOME/DroneTransfer/outgoing/replay-cases/field_evidence_cases}"
case_name="${VISION_NAV_FIELD_CASE_NAME:-}"
expected="${VISION_NAV_FIELD_EXPECTED:-}"
conditions_raw="${VISION_NAV_FIELD_CONDITIONS:-${VISION_NAV_FIELD_CONDITION:-}}"
log_path="${VISION_NAV_FIELD_LOG:-$HOME/DroneTransfer/outgoing/terrain-match/terrain_matches.jsonl}"
bundle="${VISION_NAV_FIELD_BUNDLE:-${VISION_NAV_BUNDLE:-$HOME/drone-data/map_bundles/mission_bundle}}"
notes="${VISION_NAV_FIELD_NOTES:-}"
copy_log="${VISION_NAV_FIELD_COPY_LOG:-1}"
replace_case="${VISION_NAV_FIELD_REPLACE:-0}"
strict_gate="${VISION_NAV_FIELD_GATE_STRICT:-0}"

usage() {
  cat >&2 <<EOF
Usage:
  VISION_NAV_FIELD_CASE_NAME=field-good-texture \\
  VISION_NAV_FIELD_EXPECTED=good_map \\
  VISION_NAV_FIELD_CONDITION=good_texture \\
  ./scripts/pi/register_field_replay_case.sh

Required:
  VISION_NAV_FIELD_CASE_NAME      Stable case name.
  VISION_NAV_FIELD_EXPECTED       good_map, degraded, or wrong_map.
  VISION_NAV_FIELD_CONDITION      One tag, or use VISION_NAV_FIELD_CONDITIONS.

Common optional overrides:
  VISION_NAV_FIELD_LOG            Default: $HOME/DroneTransfer/outgoing/terrain-match/terrain_matches.jsonl
  VISION_NAV_FIELD_MANIFEST       Default: $HOME/DroneTransfer/outgoing/replay-cases/field_manifest.json
  VISION_NAV_FIELD_EVIDENCE_REPORT Default: $HOME/DroneTransfer/outgoing/replay-cases/field_evidence_report.json
  VISION_NAV_FIELD_BUNDLE         Default: $bundle
  VISION_NAV_FIELD_NOTES          Human-readable setup notes.
  VISION_NAV_FIELD_REPLACE=1      Replace an existing case with the same name.
  VISION_NAV_FIELD_GATE_STRICT=1  Exit nonzero when full field coverage is not passing yet.
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

if [[ -z "$case_name" || -z "$expected" || -z "$conditions_raw" ]]; then
  usage
  exit 2
fi

if [[ ! -f "$log_path" ]]; then
  echo "Missing field replay/runtime log: $log_path" >&2
  echo "Run ./scripts/pi/run_terrain_nav_loop.sh or set VISION_NAV_FIELD_LOG." >&2
  exit 1
fi

mkdir -p "$(dirname "$manifest")" "$(dirname "$report")" "$case_report_dir"

condition_args=()
conditions_normalized="${conditions_raw//,/ }"
read -r -a condition_items <<< "$conditions_normalized"
for condition in "${condition_items[@]}"; do
  if [[ -n "$condition" ]]; then
    condition_args+=(--condition "$condition")
  fi
done

if ((${#condition_args[@]} == 0)); then
  echo "No field condition tags were provided." >&2
  usage
  exit 2
fi

register_args=(
  -m vision_nav.replay_case_registry
  --manifest "$manifest"
  --case-name "$case_name"
  --expected "$expected"
  --dataset-type field
  "${condition_args[@]}"
  --bundle "$bundle"
  --log "$log_path"
)

if [[ -n "$notes" ]]; then
  register_args+=(--notes "$notes")
fi
if [[ "$copy_log" == "1" || "$copy_log" == "true" ]]; then
  register_args+=(--copy-log)
fi
if [[ "$replace_case" == "1" || "$replace_case" == "true" ]]; then
  register_args+=(--replace)
fi

PYTHONPATH="$repo_root/src" "$venv_python" "${register_args[@]}"

set +e
PYTHONPATH="$repo_root/src" "$venv_python" -m vision_nav.field_evidence_gate \
  --manifest "$manifest" \
  --output "$report" \
  --case-output-dir "$case_report_dir"
gate_status=$?
set -e

cat <<EOF

Field replay evidence outputs:
  manifest: $manifest
  report:   $report
  cases:    $case_report_dir

The support-bundle wrapper auto-includes this report when present:
  ./scripts/pi/create_support_bundle.sh
EOF

echo "__VISION_NAV_FIELD_EVIDENCE_REPORT__=$report"

if [[ "$gate_status" -ne 0 ]]; then
  echo
  echo "Field evidence gate is not passing yet. This is expected until all required real field conditions are registered." >&2
  if [[ "$strict_gate" == "1" || "$strict_gate" == "true" ]]; then
    exit "$gate_status"
  fi
fi
