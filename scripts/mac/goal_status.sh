#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

PI_USER="${PI_USER:-pi}"
PI_HOST="${PI_HOST:-raspberrypi.local}"
PI_TARGET="${PI_TARGET:-${PI_USER}@${PI_HOST}}"

section() {
  printf '\n== %s ==\n' "$1"
}

ok() {
  printf '[OK] %s\n' "$1"
}

warn() {
  printf '[WARN] %s\n' "$1"
}

info() {
  printf '[INFO] %s\n' "$1"
}

section "Repository"
branch="$(git branch --show-current 2>/dev/null || true)"
head="$(git rev-parse --short HEAD 2>/dev/null || true)"
upstream="$(git rev-parse --abbrev-ref --symbolic-full-name '@{u}' 2>/dev/null || true)"
status="$(git status --short 2>/dev/null || true)"

if [[ -n "$branch" && -n "$head" ]]; then
  ok "Branch: $branch @ $head"
else
  warn "Could not read git branch/head."
fi

if [[ -n "$upstream" ]]; then
  ok "Upstream: $upstream"
else
  warn "No upstream configured for current branch."
fi

if [[ -z "$status" ]]; then
  ok "Working tree clean"
else
  warn "Working tree has local changes:"
  printf '%s\n' "$status"
fi

if command -v gh >/dev/null 2>&1 && gh auth status >/dev/null 2>&1; then
  pr_json="$(gh pr view --json url,isDraft,mergeable,headRefName,baseRefName 2>/dev/null || true)"
  if [[ -n "$pr_json" ]]; then
    ok "GitHub PR detected:"
    printf '%s\n' "$pr_json"
  else
    info "No GitHub PR found for the current branch."
  fi
else
  info "GitHub CLI is unavailable or not authenticated; skipping PR lookup."
fi

section "Autonomy Goal Proof"
if [[ -x "$repo_root/scripts/dev/autonomy_goal_status.sh" ]]; then
  set +e
  VISION_NAV_AUTONOMY_GOAL_STATUS_QUIET_EXIT=1 "$repo_root/scripts/dev/autonomy_goal_status.sh"
  autonomy_status=$?
  set -e
  if [[ "$autonomy_status" -eq 0 ]]; then
    ok "Autonomy goal proof is complete."
  else
    warn "Autonomy goal proof is incomplete; review the blockers above."
  fi
else
  warn "Autonomy goal status helper is missing. Expected scripts/dev/autonomy_goal_status.sh"
fi

section "Mac SSH And Transfer"
if [[ -d "$HOME/DroneTransfer/to-pi" && -d "$HOME/DroneTransfer/from-pi" ]]; then
  ok "Mac transfer folders exist under $HOME/DroneTransfer"
else
  warn "Mac transfer folders are missing. Run ./scripts/mac/setup_mac_ssh_and_transfer.sh"
fi

if [[ -f "$HOME/.ssh/id_ed25519.pub" ]]; then
  ok "Mac SSH public key exists: $HOME/.ssh/id_ed25519.pub"
else
  warn "Mac SSH public key is missing. Run ./scripts/mac/setup_mac_ssh_and_transfer.sh"
fi

if command -v systemsetup >/dev/null 2>&1; then
  remote_login_status="$(systemsetup -getremotelogin 2>&1 || true)"
  if [[ "$remote_login_status" == "Remote Login: On" ]]; then
    ok "macOS Remote Login is on"
  elif [[ "$remote_login_status" == "Remote Login: Off" ]]; then
    warn "macOS Remote Login is off. Run ./scripts/mac/enable_remote_login.sh"
  elif command -v launchctl >/dev/null 2>&1 && launchctl print system/com.openssh.sshd >/dev/null 2>&1; then
    ok "macOS SSH service is registered with launchd"
    info "systemsetup still needs admin permission to print the Remote Login setting."
  else
    warn "macOS Remote Login status needs admin permission to verify."
    info "Run ./scripts/mac/enable_remote_login.sh if Codex or another machine must SSH into this Mac."
  fi
else
  info "systemsetup is unavailable; skipping macOS Remote Login check."
fi

section "Raspberry Pi Connectivity"
info "Using PI_USER=$PI_USER PI_HOST=$PI_HOST"

if ping -c 1 -W 1000 "$PI_HOST" >/dev/null 2>&1; then
  ok "Pi host resolves/responds: $PI_HOST"
else
  warn "Pi host did not resolve/respond: $PI_HOST"
  info "If the Pi is online, run this on the Pi and give Codex the output:"
  printf '%s\n' "  whoami && hostname && hostname -I"
fi

if ssh -o BatchMode=yes -o ConnectTimeout=8 "$PI_TARGET" 'hostname' >/dev/null 2>&1; then
  ok "Non-interactive SSH works: $PI_TARGET"
  ssh -o BatchMode=yes -o ConnectTimeout=8 "$PI_TARGET" '
set -e
echo "Pi hostname: $(hostname)"
echo "Pi user: $(whoami)"
echo "Pi IP: $(hostname -I 2>/dev/null || true)"
echo "SSH service: $(systemctl is-active ssh 2>/dev/null || true)"
echo "Docker service: $(systemctl is-active docker 2>/dev/null || true)"
echo "Docker CLI: $(command -v docker || true)"
echo "Camera tool: $(command -v rpicam-hello || command -v libcamera-hello || true)"
test -d "$HOME/Drone" && echo "Repo: $HOME/Drone exists" || echo "Repo: $HOME/Drone missing"
'
else
  warn "Non-interactive SSH is not ready for $PI_TARGET"
  info "On the Pi, enable SSH and print its address:"
  printf '%s\n' "  cd ~/Drone && ./scripts/pi/enable_ssh_transfer.sh"
  info "On the Mac, after you know the real user/host:"
  printf '%s\n' "  PI_USER=$PI_USER PI_HOST=$PI_HOST ./scripts/mac/install_pi_ssh_key.sh"
  printf '%s\n' "  PI_USER=$PI_USER PI_HOST=$PI_HOST ./scripts/mac/setup_pi_ssh_config.sh"
  printf '%s\n' "  PI_USER=$PI_USER PI_HOST=$PI_HOST ./scripts/mac/test_pi_ssh.sh"
fi

section "Next Verification Command"
printf '%s\n' "PI_USER=$PI_USER PI_HOST=$PI_HOST ./scripts/mac/run_pi_first_checks.sh"
