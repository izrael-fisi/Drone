#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
skip_docker="${VISION_NAV_SKIP_DOCKER_SMOKE:-0}"
skip_camera_health="${VISION_NAV_SKIP_CAMERA_HEALTH:-0}"
check_xrce="${VISION_NAV_CHECK_XRCE:-0}"

section() {
  printf '\n== %s ==\n' "$*"
}

run_step() {
  local label="$1"
  shift
  section "$label"
  "$@"
}

cd "$repo_root"

run_step "Verify Pi setup" ./scripts/pi/verify_pi_setup.sh
run_step "Collect Pi diagnostic info" ./scripts/pi/collect_pi_info.sh

if [[ "$skip_camera_health" == "1" ]]; then
  section "Skip global shutter camera health check"
  echo "VISION_NAV_SKIP_CAMERA_HEALTH=1"
else
  run_step "Check global shutter camera health" ./scripts/pi/check_global_shutter_camera.sh
fi

run_step "Run host camera/synthetic vision smoke test" ./scripts/pi/smoke_test_vision.sh

if [[ "$check_xrce" == "1" ]]; then
  run_step "Check Micro XRCE-DDS Agent" ./scripts/pi/check_micro_xrce_dds_agent.sh
fi

if [[ "$skip_docker" == "1" ]]; then
  section "Skip Docker smoke test"
  echo "VISION_NAV_SKIP_DOCKER_SMOKE=1"
else
  run_step "Build Docker runtime" ./scripts/pi/build_docker.sh
  run_step "Run Docker synthetic vision smoke test" ./scripts/pi/smoke_test_docker.sh
fi

section "First-run checks complete"
echo "Diagnostics: $HOME/DroneTransfer/outgoing/pi-info/"
echo "Camera health outputs: $HOME/DroneTransfer/outgoing/camera-health/"
echo "Host smoke outputs: $HOME/DroneTransfer/outgoing/vision-smoke/"
echo "Docker smoke outputs: $repo_root/data/docker-smoke/"
