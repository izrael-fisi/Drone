#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
bundle="${VISION_NAV_BUNDLE:-$HOME/drone-data/map_bundles/mission_bundle}"
output_dir="${VISION_NAV_SUPPORT_OUTPUT_DIR:-$HOME/DroneTransfer/outgoing/support-bundles}"
mavlink_endpoint="${VISION_NAV_MAVLINK_ENDPOINT:-}"
feature_method_benchmark="${VISION_NAV_FEATURE_METHOD_BENCHMARK:-$HOME/DroneTransfer/outgoing/feature-method-bench}"
field_evidence_report="${VISION_NAV_FIELD_EVIDENCE_REPORT:-$HOME/DroneTransfer/outgoing/replay-cases/field_evidence_report.json}"
field_collection_plan="${VISION_NAV_FIELD_COLLECTION_PLAN:-$HOME/DroneTransfer/outgoing/replay-cases/field_collection_plan.json}"
field_capture_preflight="${VISION_NAV_FIELD_CAPTURE_PREFLIGHT:-$HOME/DroneTransfer/outgoing/replay-cases/field_capture_preflight.json}"
field_log_capture_report="${VISION_NAV_FIELD_LOG_CAPTURE_REPORT:-$HOME/DroneTransfer/outgoing/terrain-match/field_log_capture_report.json}"
gnss_denied_plan_check="${VISION_NAV_GNSS_DENIED_PLAN_CHECK:-$HOME/DroneTransfer/outgoing/replay-cases/gnss_denied_plan_check.json}"
threshold_tuning_report="${VISION_NAV_THRESHOLD_TUNING_REPORT:-$HOME/DroneTransfer/outgoing/replay-cases/threshold_tuning_report.json}"
px4_params="${VISION_NAV_PX4_PARAMS:-}"
replay_case_manifest="${VISION_NAV_REPLAY_CASE_MANIFEST:-}"

if [[ -z "$px4_params" && -f "$HOME/px4.params" ]]; then
  px4_params="$HOME/px4.params"
fi

if [[ -z "$replay_case_manifest" && -f "$HOME/DroneTransfer/outgoing/replay-cases/field_manifest.json" ]]; then
  replay_case_manifest="$HOME/DroneTransfer/outgoing/replay-cases/field_manifest.json"
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

args=(
  --repo "$repo_root"
  --output-dir "$output_dir"
)

if [[ -d "$bundle" || -f "$bundle" ]]; then
  args+=(--bundle "$bundle")
fi

for log in \
  "${VISION_NAV_TERRAIN_RUNTIME_LOG:-$HOME/DroneTransfer/outgoing/terrain-match/terrain_matches.jsonl}" \
  "${VISION_NAV_RUNTIME_LOG:-$HOME/DroneTransfer/outgoing/runtime-match/matches.jsonl}" \
  "${VISION_NAV_TERRAIN_REPLAY_OUTPUT_LOG:-$HOME/DroneTransfer/outgoing/terrain-replay/replay_matches.jsonl}" \
  "${VISION_NAV_REPLAY_LOG:-$HOME/DroneTransfer/outgoing/replay-match/replay_matches.jsonl}"
do
  if [[ -f "$log" ]]; then
    args+=(--log "$log")
  fi
done

if [[ -n "$mavlink_endpoint" ]]; then
  args+=(--mavlink-endpoint "$mavlink_endpoint")
fi

if [[ -n "${VISION_NAV_PX4_LISTENER_CAPTURE:-}" && -f "${VISION_NAV_PX4_LISTENER_CAPTURE}" ]]; then
  args+=(--px4-listener "$VISION_NAV_PX4_LISTENER_CAPTURE")
fi

if [[ -n "${VISION_NAV_PX4_MAVLINK_STATUS_CAPTURE:-}" && -f "${VISION_NAV_PX4_MAVLINK_STATUS_CAPTURE}" ]]; then
  args+=(--px4-mavlink-status "$VISION_NAV_PX4_MAVLINK_STATUS_CAPTURE")
fi

if [[ -n "$px4_params" && -f "$px4_params" ]]; then
  args+=(--px4-params "$px4_params")
fi

if [[ -n "$replay_case_manifest" && -f "$replay_case_manifest" ]]; then
  args+=(--replay-case-manifest "$replay_case_manifest")
fi

if [[ -n "$feature_method_benchmark" && -e "$feature_method_benchmark" ]]; then
  args+=(--feature-method-benchmark "$feature_method_benchmark")
fi

if [[ -n "$field_evidence_report" && -e "$field_evidence_report" ]]; then
  args+=(--field-evidence-report "$field_evidence_report")
fi

if [[ -n "$field_collection_plan" && -e "$field_collection_plan" ]]; then
  args+=(--field-collection-plan "$field_collection_plan")
fi

if [[ -n "$field_capture_preflight" && -e "$field_capture_preflight" ]]; then
  args+=(--field-capture-preflight "$field_capture_preflight")
fi

field_log_capture_reports=()
add_field_log_capture_report() {
  local candidate="$1"
  local existing
  if [[ -z "$candidate" || ! -e "$candidate" ]]; then
    return
  fi
  if [[ "${#field_log_capture_reports[@]}" -gt 0 ]]; then
    for existing in "${field_log_capture_reports[@]}"; do
      if [[ "$existing" == "$candidate" ]]; then
        return
      fi
    done
  fi
  field_log_capture_reports+=("$candidate")
}

add_field_log_capture_report "$field_log_capture_report"
if [[ -d "$HOME/DroneTransfer/outgoing/field-captures" ]]; then
  while IFS= read -r candidate; do
    add_field_log_capture_report "$candidate"
  done < <(find "$HOME/DroneTransfer/outgoing/field-captures" -maxdepth 2 -type f -name field_log_capture_report.json | sort)
fi

if [[ "${#field_log_capture_reports[@]}" -gt 0 ]]; then
  for report in "${field_log_capture_reports[@]}"; do
    args+=(--field-log-capture-report "$report")
  done
fi

if [[ -n "$gnss_denied_plan_check" && -e "$gnss_denied_plan_check" ]]; then
  args+=(--gnss-denied-plan-check "$gnss_denied_plan_check")
fi

if [[ -n "$threshold_tuning_report" && -e "$threshold_tuning_report" ]]; then
  args+=(--threshold-tuning-report "$threshold_tuning_report")
fi

if [[ "${VISION_NAV_SUPPORT_INCLUDE_MAP_ASSETS:-0}" == "1" ]]; then
  args+=(--include-map-assets)
fi

PYTHONPATH="$repo_root/src" "$venv_python" -m vision_nav.support_bundle "${args[@]}"
