#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
host="${VISION_NAV_API_HOST:-0.0.0.0}"
port="${VISION_NAV_API_PORT:-5000}"
status_roots="${VISION_NAV_RUNTIME_STATUS_ROOTS:-$HOME/DroneTransfer/outgoing:$HOME/drone-data:$HOME/Drone}"
default_mavlink_endpoint="${VISION_NAV_API_MAVLINK_ENDPOINT:-${VISION_NAV_MAVLINK_ENDPOINT:-}}"
default_serial_baud="${VISION_NAV_API_SERIAL_BAUD:-921600}"

if [[ ! -x "$venv_python" ]]; then
  echo "Missing Python venv: $venv_python" >&2
  echo "Run ./scripts/pi/bootstrap_pi5.sh first, then reboot." >&2
  exit 1
fi

args=(
  --host "$host"
  --port "$port"
  --repo-root "$repo_root"
  --status-roots "$status_roots"
  --default-serial-baud "$default_serial_baud"
)

if [[ -n "$default_mavlink_endpoint" ]]; then
  args+=(--default-mavlink-endpoint "$default_mavlink_endpoint")
fi

if [[ "${VISION_NAV_API_ALLOW_SERVICE_CONTROL:-0}" =~ ^(1|true|TRUE|yes|YES|on|ON)$ ]]; then
  args+=(--allow-service-control)
fi

cd "$repo_root"
PYTHONPATH="$repo_root/src" "$venv_python" -m vision_nav.companion_api "${args[@]}"
