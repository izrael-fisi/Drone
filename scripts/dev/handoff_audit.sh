#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

failures=0

ok() {
  printf '[OK] %s\n' "$*"
}

warn() {
  printf '[WARN] %s\n' "$*" >&2
}

fail() {
  printf '[FAIL] %s\n' "$*" >&2
  failures=$((failures + 1))
}

require_path() {
  local path="$1"
  if [[ -e "$path" ]]; then
    ok "Found $path"
  else
    fail "Missing $path"
  fi
}

require_executable() {
  local path="$1"
  if [[ -x "$path" ]]; then
    ok "Executable $path"
  else
    fail "Not executable or missing $path"
  fi
}

echo "== Local preflight =="
./scripts/dev/local_preflight.sh

echo
echo "== Required project artifacts =="
required_paths=(
  ".gitignore"
  "README.md"
  "pyproject.toml"
  "requirements/pi-host.txt"
  "requirements/pi.txt"
  "config/camera/down_camera.yaml"
  "config/camera/camera_to_body.yaml"
  "config/pi/vision-nav.env.example"
  "docker/pi/Dockerfile"
  "docker/pi/docker-compose.yml"
  "systemd/user/drone-vision-nav.service"
  "map_bundles/example/manifest.json"
  "data/.gitkeep"
  "logs/.gitkeep"
  "transfer/README.md"
  "transfer/mac_to_pi/.gitkeep"
  "transfer/pi_to_mac/.gitkeep"
  "docs/raspberry-pi-setup.md"
  "docs/setup-runbook.md"
  "docs/ssh-and-transfer.md"
  "docs/vision-pipeline.md"
  "docs/camera-calibration.md"
  "docs/github-push-plan.md"
  "docs/operator-handoff.md"
  "docs/terrain-vision-navigation.md"
)
for path in "${required_paths[@]}"; do
  require_path "$path"
done

echo
echo "== Required Pi scripts =="
required_pi_scripts=(
  "scripts/pi/bootstrap_pi5.sh"
  "scripts/pi/verify_pi_setup.sh"
  "scripts/pi/first_run_checks.sh"
  "scripts/pi/collect_pi_info.sh"
  "scripts/pi/check_global_shutter_camera.sh"
  "scripts/pi/enable_ssh_transfer.sh"
  "scripts/pi/build_docker.sh"
  "scripts/pi/run_docker.sh"
  "scripts/pi/smoke_test_vision.sh"
  "scripts/pi/smoke_test_docker.sh"
  "scripts/pi/validate_vision_nav_bundle.sh"
  "scripts/pi/validate_terrain_bundle.sh"
  "scripts/pi/run_vision_nav_loop.sh"
  "scripts/pi/run_terrain_nav_loop.sh"
  "scripts/pi/replay_vision_nav_frames.sh"
  "scripts/pi/replay_terrain_nav_log.sh"
  "scripts/pi/summarize_vision_nav_logs.sh"
  "scripts/pi/create_support_bundle.sh"
  "scripts/pi/create_field_evidence_template.sh"
  "scripts/pi/create_field_collection_plan.sh"
  "scripts/pi/run_feature_method_benchmark.sh"
  "scripts/pi/run_rosbag_export_validation.sh"
  "scripts/pi/run_threshold_tuning_report.sh"
  "scripts/pi/run_autonomy_readiness_audit.sh"
  "scripts/pi/run_autonomy_evidence_workflow.sh"
  "scripts/pi/install_vision_nav_service.sh"
)
for path in "${required_pi_scripts[@]}"; do
  require_executable "$path"
done

echo
echo "== Required Mac scripts =="
required_mac_scripts=(
  "scripts/mac/setup_mac_ssh_and_transfer.sh"
  "scripts/mac/enable_remote_login.sh"
  "scripts/mac/install_pi_ssh_key.sh"
  "scripts/mac/setup_pi_ssh_config.sh"
  "scripts/mac/test_pi_ssh.sh"
  "scripts/mac/pi_status.sh"
  "scripts/mac/pi_exec.sh"
  "scripts/mac/sync_to_pi.sh"
  "scripts/mac/sync_from_pi.sh"
  "scripts/mac/bootstrap_pi_over_ssh.sh"
  "scripts/mac/run_pi_first_checks.sh"
  "scripts/mac/goal_status.sh"
  "scripts/mac/verify_mac_transfer.sh"
)
for path in "${required_mac_scripts[@]}"; do
  require_executable "$path"
done

if rg -q "Autonomy Goal Proof" scripts/mac/goal_status.sh && rg -q "scripts/dev/autonomy_goal_status.sh" scripts/mac/goal_status.sh; then
  ok "Mac goal status includes autonomy proof summary"
else
  fail "scripts/mac/goal_status.sh does not include the autonomy proof summary"
fi

echo
echo "== Python entrypoints =="
entrypoints=(
  "vision-nav-build-map"
  "vision-nav-build-bundle"
  "vision-nav-build-terrain-bundle"
  "vision-nav-autonomy-readiness"
  "vision-nav-autonomy-evidence-package"
  "vision-nav-autonomy-handoff"
  "vision-nav-validate-evidence-workflow"
  "vision-nav-benchmark-feature-methods"
  "vision-nav-benchmark-retrieval"
  "vision-nav-bundle-checksums"
  "vision-nav-calibrate-camera"
  "vision-nav-camera-health"
  "vision-nav-create-field-collection-plan"
  "vision-nav-create-field-evidence-template"
  "vision-nav-field-evidence-gate"
  "vision-nav-check-px4-params"
  "vision-nav-check-ardupilot-params"
  "vision-nav-map-health"
  "vision-nav-match-frame"
  "vision-nav-match-bundle-frame"
  "vision-nav-match-terrain-frame"
  "vision-nav-generate-synthetic-pair"
  "vision-nav-send-mavlink-log"
  "vision-nav-run-bundle-loop"
  "vision-nav-run-terrain-loop"
  "vision-nav-evaluate-replay-gates"
  "vision-nav-evaluate-replay-manifest"
  "vision-nav-register-replay-case"
  "vision-nav-audit-replay-coverage"
  "vision-nav-evaluate-px4-sitl-evidence"
  "vision-nav-evaluate-px4-sitl-session"
  "vision-nav-replay-bundle-frames"
  "vision-nav-replay-terrain-log"
  "vision-nav-export-rosbag-jsonl"
  "vision-nav-ros2-replay-log"
  "vision-nav-validate-rosbag-export"
  "vision-nav-review-rosbag2-cli"
  "vision-nav-summarize-match-log"
  "vision-nav-support-bundle"
  "vision-nav-tune-replay-thresholds"
  "vision-nav-validate-bundle"
  "vision-nav-validate-terrain-bundle"
)
for entrypoint in "${entrypoints[@]}"; do
  if rg -q "^${entrypoint} = " pyproject.toml; then
    ok "Entrypoint $entrypoint"
  else
    fail "Missing pyproject entrypoint $entrypoint"
  fi
done

echo
echo "== Required Dev Scripts =="
required_dev_scripts=(
  "scripts/dev/autonomy_goal_status.sh"
  "scripts/dev/run_rosbag2_cli_review.sh"
)
for path in "${required_dev_scripts[@]}"; do
  require_executable "$path"
done

echo
echo "== Git state =="
git status --short --branch
if git remote get-url origin >/dev/null 2>&1; then
  ok "origin remote: $(git remote get-url origin)"
else
  fail "Missing origin remote"
fi

echo
echo "== Notes =="
echo "This audit does not push or commit."
echo "Pi camera, Docker runtime, SSH, and service execution still require live Pi validation."

echo
if ((failures > 0)); then
  echo "Handoff audit failed with $failures issue(s)." >&2
  exit 1
fi

echo "Handoff audit passed."
