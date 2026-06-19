#!/usr/bin/env bash
set -euo pipefail

PI_USER="${PI_USER:-pi}"
PI_HOST="${PI_HOST:-raspberrypi.local}"

if (($# == 0)); then
  echo "Usage: PI_USER=pi PI_HOST=raspberrypi.local $0 <command...>" >&2
  echo "Example: $0 'hostname && uptime'" >&2
  exit 1
fi

ssh "${PI_USER}@${PI_HOST}" "$*"

