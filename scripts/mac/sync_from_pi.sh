#!/usr/bin/env bash
set -euo pipefail

PI_USER="${PI_USER:-pi}"
PI_HOST="${PI_HOST:-raspberrypi.local}"
PI_DIR="${PI_DIR:-/home/${PI_USER}/DroneTransfer/outgoing}"

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
dst="${SYNC_DST:-$repo_root/transfer/pi_to_mac/}"

mkdir -p "$dst"
rsync -avh --progress "${PI_USER}@${PI_HOST}:${PI_DIR}/" "$dst"

