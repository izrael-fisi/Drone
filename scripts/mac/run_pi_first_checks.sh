#!/usr/bin/env bash
set -euo pipefail

PI_USER="${PI_USER:-pi}"
PI_HOST="${PI_HOST:-raspberrypi.local}"
PI_REPO_DIR="${PI_REPO_DIR:-/home/${PI_USER}/Drone}"
VISION_NAV_SKIP_DOCKER_SMOKE="${VISION_NAV_SKIP_DOCKER_SMOKE:-0}"
VISION_NAV_SKIP_CAMERA_HEALTH="${VISION_NAV_SKIP_CAMERA_HEALTH:-0}"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

if [[ "$VISION_NAV_SKIP_DOCKER_SMOKE" != "0" && "$VISION_NAV_SKIP_DOCKER_SMOKE" != "1" ]]; then
  echo "VISION_NAV_SKIP_DOCKER_SMOKE must be 0 or 1." >&2
  exit 1
fi
if [[ "$VISION_NAV_SKIP_CAMERA_HEALTH" != "0" && "$VISION_NAV_SKIP_CAMERA_HEALTH" != "1" ]]; then
  echo "VISION_NAV_SKIP_CAMERA_HEALTH must be 0 or 1." >&2
  exit 1
fi

echo "Running first-run checks on ${PI_USER}@${PI_HOST}:${PI_REPO_DIR}"
ssh -t "${PI_USER}@${PI_HOST}" "
set -euo pipefail
cd '${PI_REPO_DIR}'
chmod +x scripts/pi/*.sh
VISION_NAV_SKIP_DOCKER_SMOKE='${VISION_NAV_SKIP_DOCKER_SMOKE}' \
VISION_NAV_SKIP_CAMERA_HEALTH='${VISION_NAV_SKIP_CAMERA_HEALTH}' \
  ./scripts/pi/first_run_checks.sh
"

echo
echo "Pulling Pi outgoing reports into transfer/pi_to_mac/"
PI_USER="$PI_USER" PI_HOST="$PI_HOST" "$repo_root/scripts/mac/sync_from_pi.sh"
