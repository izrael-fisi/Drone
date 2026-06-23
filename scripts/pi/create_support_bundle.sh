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
rosbag_export_validation="${VISION_NAV_ROSBAG_EXPORT_VALIDATION:-$HOME/DroneTransfer/outgoing/terrain-match/rosbag-jsonl-validation.json}"
rosbag2_cli_review="${VISION_NAV_ROSBAG2_CLI_REVIEW:-$HOME/DroneTransfer/outgoing/terrain-match/rosbag2-cli-review.json}"
evidence_workflow_report="${VISION_NAV_EVIDENCE_WORKFLOW_REPORT:-}"
evidence_workflow_validation="${VISION_NAV_EVIDENCE_WORKFLOW_VALIDATION:-}"
evidence_workflow_log_archive="${VISION_NAV_EVIDENCE_WORKFLOW_LOG_ARCHIVE:-}"
px4_sitl_session="${VISION_NAV_PX4_SITL_SESSION:-}"
px4_sitl_prereqs="${VISION_NAV_PX4_SITL_PREREQS:-}"
px4_sitl_report="${VISION_NAV_PX4_SITL_REPORT:-}"
px4_params="${VISION_NAV_PX4_PARAMS:-}"
ardupilot_params="${VISION_NAV_ARDUPILOT_PARAMS:-}"
replay_case_manifest="${VISION_NAV_REPLAY_CASE_MANIFEST:-}"
home_px4_sitl_dir="$HOME/px4-sitl-evidence"
repo_px4_sitl_dir="$repo_root/px4-sitl-evidence"

if [[ -z "$px4_sitl_session" && -f "$home_px4_sitl_dir/px4_sitl_evidence_session.json" ]]; then
  px4_sitl_session="$home_px4_sitl_dir"
fi

if [[ -z "$px4_sitl_session" && ! -f "$home_px4_sitl_dir/receiver_evidence.json" && ! -f "$home_px4_sitl_dir/px4_sitl_capture_prereqs.json" && -f "$repo_px4_sitl_dir/px4_sitl_evidence_session.json" ]]; then
  px4_sitl_session="$repo_px4_sitl_dir"
fi

if [[ -z "$px4_sitl_report" && -f "$home_px4_sitl_dir/receiver_evidence.json" ]]; then
  px4_sitl_report="$home_px4_sitl_dir/receiver_evidence.json"
fi

if [[ -z "$px4_sitl_report" && -f "$repo_px4_sitl_dir/receiver_evidence.json" ]]; then
  px4_sitl_report="$repo_px4_sitl_dir/receiver_evidence.json"
fi

if [[ -z "$px4_sitl_prereqs" && -f "$home_px4_sitl_dir/px4_sitl_capture_prereqs.json" ]]; then
  px4_sitl_prereqs="$home_px4_sitl_dir/px4_sitl_capture_prereqs.json"
fi

if [[ -z "$px4_sitl_prereqs" && -f "$repo_px4_sitl_dir/px4_sitl_capture_prereqs.json" ]]; then
  px4_sitl_prereqs="$repo_px4_sitl_dir/px4_sitl_capture_prereqs.json"
fi

if [[ -z "$px4_params" && -f "$HOME/px4.params" ]]; then
  px4_params="$HOME/px4.params"
fi

if [[ -z "$ardupilot_params" && -f "$HOME/ardupilot.params" ]]; then
  ardupilot_params="$HOME/ardupilot.params"
fi

if [[ -z "$replay_case_manifest" && -f "$HOME/DroneTransfer/outgoing/replay-cases/field_manifest.json" ]]; then
  replay_case_manifest="$HOME/DroneTransfer/outgoing/replay-cases/field_manifest.json"
fi

if [[ -z "$evidence_workflow_report" ]]; then
  for candidate in \
    "$HOME/DroneTransfer/outgoing/replay-cases/autonomy-evidence-workflow/autonomy_evidence_workflow.json" \
    "$HOME/DroneTransfer/outgoing/replay-cases/autonomy_evidence_workflow.json"
  do
    if [[ -f "$candidate" ]]; then
      evidence_workflow_report="$candidate"
      break
    fi
  done
fi

if [[ -z "$evidence_workflow_validation" ]]; then
  for candidate in \
    "${evidence_workflow_report%.json}.validation.json" \
    "$HOME/DroneTransfer/outgoing/replay-cases/autonomy-evidence-workflow/autonomy_evidence_workflow.validation.json" \
    "$HOME/DroneTransfer/outgoing/replay-cases/autonomy_evidence_workflow.validation.json"
  do
    if [[ -n "$candidate" && -f "$candidate" ]]; then
      evidence_workflow_validation="$candidate"
      break
    fi
  done
fi

if [[ -z "$evidence_workflow_log_archive" ]]; then
  for candidate in \
    "${evidence_workflow_report%.json}.logs.tar.gz" \
    "$HOME/DroneTransfer/outgoing/replay-cases/autonomy-evidence-workflow/autonomy_evidence_workflow.logs.tar.gz" \
    "$HOME/DroneTransfer/outgoing/replay-cases/autonomy_evidence_workflow.logs.tar.gz"
  do
    if [[ -n "$candidate" && -f "$candidate" ]]; then
      evidence_workflow_log_archive="$candidate"
      break
    fi
  done
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

if [[ -n "$px4_sitl_session" && -e "$px4_sitl_session" ]]; then
  args+=(--px4-sitl-session "$px4_sitl_session")
fi

if [[ -n "$px4_sitl_prereqs" && -f "$px4_sitl_prereqs" ]]; then
  args+=(--px4-sitl-prereqs "$px4_sitl_prereqs")
fi

if [[ -n "$px4_sitl_report" && -f "$px4_sitl_report" ]]; then
  args+=(--px4-sitl-report "$px4_sitl_report")
fi

if [[ -n "$px4_params" && -f "$px4_params" ]]; then
  args+=(--px4-params "$px4_params")
fi

if [[ -n "$ardupilot_params" && -f "$ardupilot_params" ]]; then
  args+=(--ardupilot-params "$ardupilot_params")
fi

if [[ -n "${VISION_NAV_SITL_MAVLINK_MESSAGE:-}" ]]; then
  args+=(--px4-expected-message "$VISION_NAV_SITL_MAVLINK_MESSAGE")
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

if [[ -n "$rosbag_export_validation" && -e "$rosbag_export_validation" ]]; then
  args+=(--rosbag-export-validation "$rosbag_export_validation")
fi

if [[ -n "${VISION_NAV_MCAP_EXPORT_VALIDATION:-}" && -e "${VISION_NAV_MCAP_EXPORT_VALIDATION}" ]]; then
  args+=(--rosbag-export-validation "$VISION_NAV_MCAP_EXPORT_VALIDATION")
fi

if [[ -n "$rosbag2_cli_review" && -e "$rosbag2_cli_review" ]]; then
  args+=(--rosbag2-cli-review "$rosbag2_cli_review")
fi

if [[ -n "$evidence_workflow_report" && -f "$evidence_workflow_report" ]]; then
  args+=(--evidence-workflow-report "$evidence_workflow_report")
fi

if [[ -n "$evidence_workflow_validation" && -f "$evidence_workflow_validation" ]]; then
  args+=(--evidence-workflow-validation "$evidence_workflow_validation")
fi

if [[ -n "$evidence_workflow_log_archive" && -f "$evidence_workflow_log_archive" ]]; then
  args+=(--evidence-workflow-log-archive "$evidence_workflow_log_archive")
fi

if [[ "${VISION_NAV_SUPPORT_INCLUDE_MAP_ASSETS:-0}" == "1" ]]; then
  args+=(--include-map-assets)
fi

PYTHONPATH="$repo_root/src" "$venv_python" -m vision_nav.support_bundle "${args[@]}"
