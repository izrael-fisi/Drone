#!/usr/bin/env bash
set -euo pipefail

timestamp="$(date -u '+%Y%m%dT%H%M%SZ')"
out_dir="${PI_INFO_OUT_DIR:-$HOME/DroneTransfer/outgoing/pi-info}"
out_file="${PI_INFO_OUT:-$out_dir/pi_info_${timestamp}.txt}"
repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"

mkdir -p "$out_dir"

section() {
  printf '\n== %s ==\n' "$*"
}

command_or_warn() {
  local command_name="$1"
  if command -v "$command_name" >/dev/null 2>&1; then
    command -v "$command_name"
  else
    echo "missing: $command_name"
  fi
}

exec > >(tee "$out_file") 2>&1

section "Report"
echo "generated_utc=$timestamp"
echo "output_file=$out_file"

section "Identity"
whoami
id
hostname
hostname -I || true

section "OS"
cat /etc/os-release || true
uname -a

section "Storage"
df -h /
df -h "$HOME" || true
lsblk -o NAME,SIZE,TYPE,MOUNTPOINTS,FSTYPE || true

section "Memory"
free -h || true

section "Thermal And Power"
if command -v vcgencmd >/dev/null 2>&1; then
  vcgencmd measure_temp || true
  vcgencmd get_throttled || true
else
  echo "vcgencmd not available"
fi

section "Camera Tools"
command_or_warn rpicam-hello
command_or_warn rpicam-still
command_or_warn libcamera-hello
command_or_warn libcamera-still
command_or_warn v4l2-ctl
if command -v rpicam-hello >/dev/null 2>&1; then
  timeout 15 rpicam-hello --list-cameras || true
elif command -v libcamera-hello >/dev/null 2>&1; then
  timeout 15 libcamera-hello --list-cameras || true
else
  echo "No rpicam/libcamera list command available."
fi

section "Services"
systemctl is-active ssh || true
systemctl is-enabled ssh || true
systemctl is-active docker || true
systemctl is-enabled docker || true
systemctl --user is-enabled drone-vision-nav.service || true
systemctl --user is-active drone-vision-nav.service || true

section "Docker"
docker --version || true
docker compose version || true
groups "$USER" || true

section "Project Paths"
echo "repo_root=$repo_root"
for path in \
  "$HOME/DroneTransfer" \
  "$HOME/DroneTransfer/incoming" \
  "$HOME/DroneTransfer/outgoing" \
  "$HOME/drone-data" \
  "$HOME/drone-data/map_bundles" \
  "$HOME/drone_vision_nav_venv"; do
  if [[ -e "$path" ]]; then
    ls -ld "$path"
  else
    echo "missing: $path"
  fi
done

section "Python Environment"
if [[ -x "$HOME/drone_vision_nav_venv/bin/python" ]]; then
  "$HOME/drone_vision_nav_venv/bin/python" - <<'PY'
import importlib
import platform
import sys

print(f"python={sys.version}")
print(f"platform={platform.platform()}")
for module in ["cv2", "numpy", "yaml", "vision_nav"]:
    try:
        imported = importlib.import_module(module)
        version = getattr(imported, "__version__", "unknown")
        print(f"import {module}: ok version={version}")
    except Exception as exc:
        print(f"import {module}: failed {exc}")
PY
else
  echo "Missing venv python: $HOME/drone_vision_nav_venv/bin/python"
fi

section "Repository"
cd "$repo_root"
git status --short || true
git remote -v || true

section "Vision Nav Verification"
if [[ -x scripts/pi/verify_pi_setup.sh ]]; then
  scripts/pi/verify_pi_setup.sh || true
fi
if [[ -x scripts/pi/validate_vision_nav_bundle.sh ]]; then
  scripts/pi/validate_vision_nav_bundle.sh || true
fi

section "Done"
echo "Wrote: $out_file"
