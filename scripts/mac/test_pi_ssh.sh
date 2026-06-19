#!/usr/bin/env bash
set -euo pipefail

PI_TARGET="${PI_TARGET:-drone-pi}"

echo "Testing SSH target: $PI_TARGET"
ssh -o BatchMode=yes -o ConnectTimeout=8 "$PI_TARGET" '
set -e
echo "[OK] Connected to $(hostname)"
echo "[OK] User: $(whoami)"
echo "[OK] IP: $(hostname -I 2>/dev/null || true)"
'

