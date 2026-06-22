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
threshold_tuning_report="${VISION_NAV_THRESHOLD_TUNING_REPORT:-$HOME/DroneTransfer/outgoing/replay-cases/threshold_tuning_report.json}"
rosbag_export_validation="${VISION_NAV_ROSBAG_EXPORT_VALIDATION:-$HOME/DroneTransfer/outgoing/terrain-match/rosbag-jsonl-validation.json}"
rosbag2_cli_review="${VISION_NAV_ROSBAG2_CLI_REVIEW:-$HOME/DroneTransfer/outgoing/terrain-match/rosbag2-cli-review.json}"
px4_sitl_session="${VISION_NAV_PX4_SITL_SESSION:-}"
px4_sitl_report="${VISION_NAV_PX4_SITL_REPORT:-}"
px4_params="${VISION_NAV_PX4_PARAMS:-}"
ardupilot_params="${VISION_NAV_ARDUPILOT_PARAMS:-}"

if [[ -z "$px4_sitl_session" && -f "$HOME/px4-sitl-evidence/px4_sitl_evidence_session.json" ]]; then
  px4_sitl_session="$HOME/px4-sitl-evidence"
fi

if [[ -z "$px4_sitl_report" && -f "$HOME/px4-sitl-evidence/receiver_evidence.json" ]]; then
  px4_sitl_report="$HOME/px4-sitl-evidence/receiver_evidence.json"
fi

if [[ -z "$px4_params" && -f "$HOME/px4.params" ]]; then
  px4_params="$HOME/px4.params"
fi

if [[ -z "$ardupilot_params" && -f "$HOME/ardupilot.params" ]]; then
  ardupilot_params="$HOME/ardupilot.params"
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

if [[ -n "${VISION_NAV_REPLAY_CASE_MANIFEST:-}" && -f "${VISION_NAV_REPLAY_CASE_MANIFEST}" ]]; then
  args+=(--replay-case-manifest "$VISION_NAV_REPLAY_CASE_MANIFEST")
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

if [[ "${VISION_NAV_SUPPORT_INCLUDE_MAP_ASSETS:-0}" == "1" ]]; then
  args+=(--include-map-assets)
fi

PYTHONPATH="$repo_root/src" "$venv_python" -m vision_nav.support_bundle "${args[@]}"
