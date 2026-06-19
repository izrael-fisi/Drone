#!/usr/bin/env bash
set -euo pipefail

mkdir -p "$HOME/DroneTransfer/to-pi" "$HOME/DroneTransfer/from-pi"

if [[ ! -f "$HOME/.ssh/id_ed25519.pub" ]]; then
  echo "No Ed25519 SSH key found. Creating one for Mac/Pi transfer."
  ssh-keygen -t ed25519 -f "$HOME/.ssh/id_ed25519" -N "" -C "$USER@$(hostname)-drone"
fi

echo "Transfer folders created:"
echo "  $HOME/DroneTransfer/to-pi"
echo "  $HOME/DroneTransfer/from-pi"
echo
echo "To enable SSH Remote Login on macOS, run:"
echo "  sudo systemsetup -setremotelogin on"
echo
echo "To show this Mac's SSH address:"
echo "  ipconfig getifaddr en0"
echo "  whoami"

