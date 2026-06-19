#!/usr/bin/env bash
set -euo pipefail

PI_USER="${PI_USER:-pi}"
PI_HOST="${PI_HOST:-raspberrypi.local}"
PI_REPO_DIR="${PI_REPO_DIR:-/home/${PI_USER}/Drone}"

ssh "${PI_USER}@${PI_HOST}" "
set -e
echo '== Host =='
hostname
hostname -I || true
echo
echo '== OS =='
cat /etc/os-release | sed -n '1,6p'
echo
echo '== Camera commands =='
command -v rpicam-hello || true
command -v libcamera-hello || true
echo
echo '== Services =='
systemctl is-active ssh || true
systemctl is-active docker || true
echo
echo '== Docker =='
docker --version || true
docker compose version || true
echo
echo '== Repo =='
if [ -d '${PI_REPO_DIR}' ]; then
  cd '${PI_REPO_DIR}'
  pwd
  if [ -x scripts/pi/collect_pi_info.sh ]; then
    scripts/pi/collect_pi_info.sh
  elif [ -x scripts/pi/verify_pi_setup.sh ]; then
    scripts/pi/verify_pi_setup.sh || true
  fi
else
  echo 'Repo not found: ${PI_REPO_DIR}'
fi
"
