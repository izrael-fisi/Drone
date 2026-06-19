#!/usr/bin/env bash
set -euo pipefail

PI_USER="${PI_USER:-pi}"
PI_HOST="${PI_HOST:-raspberrypi.local}"
PI_REPO_DIR="${PI_REPO_DIR:-/home/${PI_USER}/Drone}"

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

echo "Syncing repository to ${PI_USER}@${PI_HOST}:${PI_REPO_DIR}"
rsync -avh --delete \
  --exclude '.git/' \
  --exclude '__pycache__/' \
  --exclude '.pytest_cache/' \
  --exclude 'data/' \
  --exclude 'logs/' \
  --exclude 'map_bundles/' \
  "$repo_root/" "${PI_USER}@${PI_HOST}:${PI_REPO_DIR}/"

echo "Running Pi bootstrap remotely. You may be prompted for the Pi sudo password."
ssh -t "${PI_USER}@${PI_HOST}" \
  "cd '${PI_REPO_DIR}' && chmod +x scripts/pi/*.sh && ./scripts/pi/bootstrap_pi5.sh"

echo
echo "Bootstrap finished. Reboot the Pi before Docker group membership is active:"
echo "  ssh ${PI_USER}@${PI_HOST} 'sudo reboot'"

