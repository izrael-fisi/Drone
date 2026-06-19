#!/usr/bin/env bash
set -euo pipefail

PI_USER="${PI_USER:-pi}"
PI_HOST="${PI_HOST:-raspberrypi.local}"
PI_ALIAS="${PI_ALIAS:-drone-pi}"
SSH_KEY="${SSH_KEY:-$HOME/.ssh/id_ed25519}"
SSH_CONFIG="$HOME/.ssh/config"

mkdir -p "$HOME/.ssh"
chmod 700 "$HOME/.ssh"

if [[ ! -f "$SSH_KEY" ]]; then
  echo "Missing SSH key: $SSH_KEY" >&2
  echo "Run scripts/mac/setup_mac_ssh_and_transfer.sh first." >&2
  exit 1
fi

touch "$SSH_CONFIG"
chmod 600 "$SSH_CONFIG"

tmp_config="$(mktemp)"

awk -v alias="$PI_ALIAS" '
  $1 == "Host" && $2 == alias { skip = 1; next }
  $1 == "Host" && skip == 1 { skip = 0 }
  skip != 1 { print }
' "$SSH_CONFIG" > "$tmp_config"

cat >> "$tmp_config" <<EOF

Host ${PI_ALIAS}
  HostName ${PI_HOST}
  User ${PI_USER}
  IdentityFile ${SSH_KEY}
  IdentitiesOnly yes
  ServerAliveInterval 30
  ServerAliveCountMax 3
EOF

mv "$tmp_config" "$SSH_CONFIG"
chmod 600 "$SSH_CONFIG"

echo "Added SSH config alias:"
echo "  ${PI_ALIAS} -> ${PI_USER}@${PI_HOST}"
echo
echo "Test with:"
echo "  ssh ${PI_ALIAS} hostname"

