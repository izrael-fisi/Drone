#!/usr/bin/env bash
set -euo pipefail

PI_USER="${PI_USER:-pi}"
PI_HOST="${PI_HOST:-raspberrypi.local}"
PUBKEY="${PUBKEY:-$HOME/.ssh/id_ed25519.pub}"

if [[ ! -f "$PUBKEY" ]]; then
  echo "Missing public key: $PUBKEY" >&2
  echo "Run scripts/mac/setup_mac_ssh_and_transfer.sh first." >&2
  exit 1
fi

echo "Installing $PUBKEY on ${PI_USER}@${PI_HOST}"
cat "$PUBKEY" | ssh "${PI_USER}@${PI_HOST}" \
  'mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys'

echo "Testing key-based SSH login..."
ssh -o BatchMode=yes "${PI_USER}@${PI_HOST}" 'echo "SSH key login works: $(hostname)"'

