#!/usr/bin/env bash
set -euo pipefail

PI_USER="${PI_USER:-pi}"
PI_HOST="${PI_HOST:-raspberrypi.local}"
PI_DIR="${PI_DIR:-/home/${PI_USER}/DroneTransfer/incoming}"

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
src="${SYNC_SRC:-$repo_root/transfer/mac_to_pi/}"

rsync -avh --progress "$src" "${PI_USER}@${PI_HOST}:${PI_DIR}/"

