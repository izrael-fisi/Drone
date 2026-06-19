#!/usr/bin/env bash
set -euo pipefail

target_user="${SUDO_USER:-$USER}"
target_home="$(getent passwd "$target_user" | cut -d: -f6)"
target_group="$(id -gn "$target_user")"

sudo apt-get update
sudo apt-get install -y openssh-server rsync avahi-daemon
sudo systemctl enable --now ssh
sudo systemctl enable --now avahi-daemon

install -d \
  "$target_home/DroneTransfer/incoming" \
  "$target_home/DroneTransfer/outgoing" \
  "$target_home/DroneTransfer/logs" \
  "$target_home/DroneTransfer/map-bundles"

sudo chown -R "$target_user:$target_group" "$target_home/DroneTransfer"

echo "SSH enabled."
echo "Pi hostname: $(hostname)"
echo "Pi IP addresses:"
hostname -I || true
echo
echo "From the Mac, try:"
echo "  ssh ${target_user}@$(hostname).local"
