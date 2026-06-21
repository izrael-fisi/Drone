#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
bundle="${VISION_NAV_BUNDLE:-$HOME/drone-data/map_bundles/mission_bundle}"
output_dir="${VISION_NAV_SUPPORT_OUTPUT_DIR:-$HOME/DroneTransfer/outgoing/support-bundles}"
mavlink_endpoint="${VISION_NAV_MAVLINK_ENDPOINT:-}"
feature_method_benchmark="${VISION_NAV_FEATURE_METHOD_BENCHMARK:-$HOME/DroneTransfer/outgoing/feature-method-bench}"
field_evidence_report="${VISION_NAV_FIELD_EVIDENCE_REPORT:-$HOME/DroneTransfer/outgoing/replay-cases/field_evidence_report.json}"

if [[ ! -x "$venv_python" ]]; then
  echo "Missing Python venv: $venv_python" >&2
  echo "Run ./scripts/pi/bootstrap_pi5.sh first, then reboot." >&2
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

if [[ -n "${VISION_NAV_PX4_SITL_SESSION:-}" && -e "${VISION_NAV_PX4_SITL_SESSION}" ]]; then
  args+=(--px4-sitl-session "$VISION_NAV_PX4_SITL_SESSION")
fi

if [[ -n "${VISION_NAV_PX4_PARAMS:-}" && -f "${VISION_NAV_PX4_PARAMS}" ]]; then
  args+=(--px4-params "$VISION_NAV_PX4_PARAMS")
fi

if [[ -n "${VISION_NAV_ARDUPILOT_PARAMS:-}" && -f "${VISION_NAV_ARDUPILOT_PARAMS}" ]]; then
  args+=(--ardupilot-params "$VISION_NAV_ARDUPILOT_PARAMS")
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

if [[ "${VISION_NAV_SUPPORT_INCLUDE_MAP_ASSETS:-0}" == "1" ]]; then
  args+=(--include-map-assets)
fi

PYTHONPATH="$repo_root/src" "$venv_python" -m vision_nav.support_bundle "${args[@]}"
