#!/usr/bin/env bash
set -euo pipefail

if [[ "${EUID}" -eq 0 ]]; then
  echo "Run this script as your normal Pi user, not with sudo." >&2
  exit 1
fi

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
service_src="$repo_root/systemd/user/drone-vision-nav.service"
service_dst="$HOME/.config/systemd/user/drone-vision-nav.service"
env_example="$repo_root/config/pi/vision-nav.env.example"
env_file="$repo_root/config/pi/vision-nav.env"

if [[ ! -f "$service_src" ]]; then
  echo "Missing service template: $service_src" >&2
  exit 1
fi

mkdir -p "$HOME/.config/systemd/user"
cp "$service_src" "$service_dst"

if [[ ! -f "$env_file" && -f "$env_example" ]]; then
  cp "$env_example" "$env_file"
fi

systemctl --user daemon-reload
systemctl --user enable drone-vision-nav.service

cat <<EOF
Installed user service:
  $service_dst

Runtime override file:
  $env_file

Start manually:
  systemctl --user start drone-vision-nav.service

Check status:
  systemctl --user status drone-vision-nav.service

Follow logs:
  journalctl --user -u drone-vision-nav.service -f

To allow user services after logout/reboot:
  sudo loginctl enable-linger $USER
EOF
