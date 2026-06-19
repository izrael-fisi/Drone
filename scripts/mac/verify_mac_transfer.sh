#!/usr/bin/env bash
set -euo pipefail

failures=0

if [[ -d "$HOME/DroneTransfer/to-pi" && -d "$HOME/DroneTransfer/from-pi" ]]; then
  echo "[OK] Mac DroneTransfer folders exist"
else
  echo "[WARN] Missing Mac DroneTransfer folders" >&2
  failures=$((failures + 1))
fi

if [[ -f "$HOME/.ssh/id_ed25519.pub" ]]; then
  echo "[OK] SSH public key exists: $HOME/.ssh/id_ed25519.pub"
else
  echo "[WARN] SSH public key missing. Run scripts/mac/setup_mac_ssh_and_transfer.sh" >&2
  failures=$((failures + 1))
fi

if command -v systemsetup >/dev/null 2>&1; then
  remote_login_status="$(systemsetup -getremotelogin 2>&1 || true)"
  echo "[INFO] Remote Login status requires admin permission on some macOS versions:"
  echo "$remote_login_status"
  if [[ "$remote_login_status" != Remote\ Login:* ]]; then
    if command -v launchctl >/dev/null 2>&1 && launchctl print system/com.openssh.sshd >/dev/null 2>&1; then
      echo "[OK] macOS SSH service is registered with launchd"
    else
      echo "[WARN] Remote Login status could not be verified without admin permission." >&2
      echo "[WARN] Run ./scripts/mac/enable_remote_login.sh when you are ready to enable Mac SSH." >&2
    fi
  fi
fi

if ((failures > 0)); then
  exit 1
fi

echo "Mac transfer verification passed."
