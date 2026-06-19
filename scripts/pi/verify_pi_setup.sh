#!/usr/bin/env bash
set -euo pipefail

ok() {
  printf '[OK] %s\n' "$*"
}

warn() {
  printf '[WARN] %s\n' "$*" >&2
}

failures=0

check_command() {
  local command_name="$1"
  if command -v "$command_name" >/dev/null 2>&1; then
    ok "Found command: $command_name"
  else
    warn "Missing command: $command_name"
    failures=$((failures + 1))
  fi
}

check_optional_command() {
  local command_name="$1"
  if command -v "$command_name" >/dev/null 2>&1; then
    ok "Found optional command: $command_name"
  else
    warn "Optional command not found: $command_name"
  fi
}

check_command python3
check_command docker
check_command ssh
check_command rsync
check_optional_command rpicam-hello
check_optional_command libcamera-hello
check_optional_command v4l2-ctl

if systemctl is-active --quiet ssh; then
  ok "SSH service is active"
else
  warn "SSH service is not active"
  failures=$((failures + 1))
fi

if systemctl is-active --quiet docker; then
  ok "Docker service is active"
else
  warn "Docker service is not active"
  failures=$((failures + 1))
fi

if groups "$USER" | grep -q '\bdocker\b'; then
  ok "User is in docker group"
else
  warn "User is not in docker group yet. Reboot or log out/in after bootstrap."
fi

if [[ -d "$HOME/DroneTransfer/incoming" && -d "$HOME/DroneTransfer/outgoing" ]]; then
  ok "DroneTransfer folders exist"
else
  warn "DroneTransfer folders are missing"
  failures=$((failures + 1))
fi

if [[ -x "$HOME/drone_vision_nav_venv/bin/python" ]]; then
  ok "Python venv exists"
  "$HOME/drone_vision_nav_venv/bin/python" - <<'PY'
import importlib

modules = ["cv2", "numpy", "vision_nav"]
for module in modules:
    importlib.import_module(module)
    print(f"[OK] Imported Python module: {module}")
PY
else
  warn "Python venv missing: $HOME/drone_vision_nav_venv"
  failures=$((failures + 1))
fi

echo
if ((failures > 0)); then
  echo "Verification completed with $failures required issue(s)." >&2
  exit 1
fi

echo "Verification passed."

