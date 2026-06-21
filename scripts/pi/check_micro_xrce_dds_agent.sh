#!/usr/bin/env bash
set -euo pipefail

require_xrce="${VISION_NAV_REQUIRE_XRCE:-0}"
agent_override="${VISION_NAV_XRCE_AGENT_COMMAND:-}"
transport="${VISION_NAV_XRCE_TRANSPORT:-udp4}"
udp_port="${VISION_NAV_XRCE_UDP_PORT:-8888}"
serial_device="${VISION_NAV_XRCE_SERIAL_DEVICE:-}"

ok() {
  printf '[OK] %s\n' "$*"
}

warn() {
  printf '[WARN] %s\n' "$*" >&2
}

fail() {
  printf '[FAIL] %s\n' "$*" >&2
  exit 1
}

find_agent() {
  if [[ -n "$agent_override" ]]; then
    [[ -x "$agent_override" ]] && printf '%s\n' "$agent_override"
    return
  fi

  local candidates=(
    "MicroXRCEAgent"
    "microxrcedds_agent"
    "/usr/local/bin/MicroXRCEAgent"
    "$HOME/Micro-XRCE-DDS-Agent/build/MicroXRCEAgent"
    "$HOME/Micro-XRCE-DDS-Agent/build/microxrcedds_agent"
  )
  local candidate
  for candidate in "${candidates[@]}"; do
    if command -v "$candidate" >/dev/null 2>&1; then
      command -v "$candidate"
      return
    fi
    if [[ -x "$candidate" ]]; then
      printf '%s\n' "$candidate"
      return
    fi
  done
}

agent_path="$(find_agent || true)"
if [[ -z "$agent_path" ]]; then
  warn "Micro XRCE-DDS Agent was not found."
  warn "Install it when using PX4 uXRCE-DDS/ROS 2: https://docs.px4.io/main/en/middleware/uxrce_dds"
  warn "Expected command/path: MicroXRCEAgent, /usr/local/bin/MicroXRCEAgent, or ~/Micro-XRCE-DDS-Agent/build/MicroXRCEAgent"
  if [[ "$require_xrce" == "1" ]]; then
    fail "VISION_NAV_REQUIRE_XRCE=1 and Micro XRCE-DDS Agent is missing."
  fi
  exit 0
fi

ok "Found Micro XRCE-DDS Agent: $agent_path"

if "$agent_path" --version >/tmp/vision-nav-xrce-version.txt 2>&1; then
  sed -n '1,4p' /tmp/vision-nav-xrce-version.txt
else
  warn "Agent did not report a version with --version."
fi

if "$agent_path" --help >/tmp/vision-nav-xrce-help.txt 2>&1; then
  ok "Agent help command works"
else
  warn "Agent help command returned a non-zero status."
fi

case "$transport" in
  udp4|udp)
    if ! [[ "$udp_port" =~ ^[0-9]+$ ]] || ((udp_port < 1 || udp_port > 65535)); then
      fail "Invalid VISION_NAV_XRCE_UDP_PORT: $udp_port"
    fi
    ok "XRCE UDP port is valid: $udp_port"
    if command -v ss >/dev/null 2>&1 && ss -lun | awk '{print $5}' | grep -Eq "[:.]${udp_port}$"; then
      warn "UDP port $udp_port already appears to be bound. Stop the old agent before launching another one."
    fi
    echo "PX4 ROS 2 agent launch:"
    echo "  $agent_path udp4 -p $udp_port"
    ;;
  serial)
    if [[ -z "$serial_device" ]]; then
      fail "VISION_NAV_XRCE_TRANSPORT=serial requires VISION_NAV_XRCE_SERIAL_DEVICE."
    fi
    if [[ ! -e "$serial_device" ]]; then
      fail "XRCE serial device does not exist: $serial_device"
    fi
    if [[ ! -r "$serial_device" || ! -w "$serial_device" ]]; then
      fail "XRCE serial device is not readable/writable by this user: $serial_device"
    fi
    ok "XRCE serial device exists and is readable/writable: $serial_device"
    echo "PX4 ROS 2 agent launch:"
    echo "  $agent_path serial --dev $serial_device"
    ;;
  *)
    fail "Unsupported VISION_NAV_XRCE_TRANSPORT: $transport"
    ;;
esac

echo "Micro XRCE-DDS Agent check complete."
