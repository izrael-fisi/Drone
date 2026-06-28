#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -eq 0 ]]; then
  echo "Run this script as your normal Pi user, not with sudo." >&2
  exit 1
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
env_example="$repo_root/config/pi/vision-nav.env.example"
env_file="$repo_root/config/pi/vision-nav.env"

if ! compgen -G "$repo_root/systemd/user/drone-vision-nav*.service" >/dev/null; then
  echo "Missing service templates under: $repo_root/systemd/user" >&2
  exit 1
fi

mkdir -p "$HOME/.config/systemd/user"
cp "$repo_root"/systemd/user/drone-vision-nav*.service "$HOME/.config/systemd/user/"

if [[ ! -f "$env_file" && -f "$env_example" ]]; then
  cp "$env_example" "$env_file"
fi

systemctl --user daemon-reload
systemctl --user enable drone-vision-nav-api.service
systemctl --user enable drone-vision-nav-status-bridge.service
systemctl --user enable drone-vision-nav.service

cat <<EOF
Installed user services:
  $HOME/.config/systemd/user/drone-vision-nav-api.service
  $HOME/.config/systemd/user/drone-vision-nav-status-bridge.service
  $HOME/.config/systemd/user/drone-vision-nav.service

Runtime override file:
  $env_file

Start API and standby telemetry manually:
  systemctl --user start drone-vision-nav-api.service
  systemctl --user start drone-vision-nav-status-bridge.service

Start terrain runtime manually:
  systemctl --user start drone-vision-nav.service

Check status:
  systemctl --user status drone-vision-nav-api.service
  systemctl --user status drone-vision-nav-status-bridge.service
  systemctl --user status drone-vision-nav.service

Follow logs:
  journalctl --user -u drone-vision-nav-api.service -f
  journalctl --user -u drone-vision-nav-status-bridge.service -f
  journalctl --user -u drone-vision-nav.service -f

To allow user services after logout/reboot:
  sudo loginctl enable-linger $USER
EOF
