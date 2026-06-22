#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
px4_dir="${VISION_NAV_PX4_AUTOPILOT_DIR:-$HOME/PX4-Autopilot}"
px4_target="${VISION_NAV_PX4_SITL_TARGET:-px4_sitl gz_x500}"
session_dir="${VISION_NAV_SITL_SMOKE_DIR:-$PWD/px4-sitl-evidence}"
tmux_session="${VISION_NAV_PX4_TMUX_SESSION:-vision-nav-px4-sitl}"
boot_wait_s="${VISION_NAV_PX4_BOOT_WAIT_S:-45}"
listener_arm_wait_s="${VISION_NAV_PX4_LISTENER_ARM_WAIT_S:-1}"
capture_wait_s="${VISION_NAV_PX4_CAPTURE_WAIT_S:-4}"
keep_tmux="${VISION_NAV_PX4_KEEP_TMUX:-0}"
dry_run="${VISION_NAV_SITL_CAPTURE_DRY_RUN:-0}"

capture_dir="$session_dir/receiver_capture"
listener_capture="$capture_dir/vehicle_visual_odometry.txt"
mavlink_status_capture="$capture_dir/mavlink_status.txt"
receiver_report="$session_dir/receiver_evidence.json"

mkdir -p "$capture_dir"

prepare_session_scaffold() {
  if VISION_NAV_SITL_DRY_RUN=1 \
    VISION_NAV_SITL_SMOKE_DIR="$session_dir" \
    "$repo_root/scripts/dev/px4_sitl_external_vision_smoke.sh" >/dev/null; then
    return 0
  fi
  echo "Warning: could not prepare PX4 evidence-session scaffold." >&2
  return 1
}

print_session_markers() {
  echo "__VISION_NAV_PX4_SITL_SESSION__=$session_dir"
  echo "__VISION_NAV_PX4_SITL_REPORT__=$receiver_report"
}

fail_prereq() {
  local message="$1"
  echo "$message" >&2
  prepare_session_scaffold >/dev/null 2>&1 || true
  cat <<EOF >&2

PX4 SITL receiver capture prerequisites are not ready.
Prepared a reusable evidence-session scaffold when possible:
  session: $session_dir
  capture instructions: $capture_dir/README.md
  expected receiver report: $receiver_report

After fixing the prerequisite, rerun:
  VISION_NAV_SITL_SMOKE_DIR="$session_dir" $repo_root/scripts/dev/run_px4_sitl_external_vision_capture.sh
EOF
  print_session_markers
  exit 2
}

if [[ "$dry_run" == "1" ]]; then
  prepare_session_scaffold
  cat <<EOF
PX4 SITL capture dry run prepared:
  session: $session_dir
  tmux session: $tmux_session
  PX4 dir: $px4_dir
  PX4 target: $px4_target
EOF
  print_session_markers
  exit 0
fi

if ! command -v tmux >/dev/null 2>&1; then
  fail_prereq "tmux is required for automated PX4 SITL shell capture. Install tmux or use the scaffolded manual PX4 shell capture instructions."
fi

if [[ ! -d "$px4_dir" ]]; then
  fail_prereq "PX4-Autopilot directory not found: $px4_dir. Set VISION_NAV_PX4_AUTOPILOT_DIR=/path/to/PX4-Autopilot if it lives elsewhere."
fi

if tmux has-session -t "$tmux_session" 2>/dev/null; then
  fail_prereq "tmux session already exists: $tmux_session. Set VISION_NAV_PX4_TMUX_SESSION to another name or close the existing session."
fi

cleanup() {
  if [[ "$keep_tmux" != "1" ]] && tmux has-session -t "$tmux_session" 2>/dev/null; then
    tmux kill-session -t "$tmux_session" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

tmux new-session -d -s "$tmux_session" -c "$px4_dir"
tmux send-keys -t "$tmux_session" "make $px4_target" C-m

cat <<EOF
Started PX4 SITL in tmux session '$tmux_session':
  cd $px4_dir
  make $px4_target

Waiting ${boot_wait_s}s before sending synthetic external-vision records...
EOF
sleep "$boot_wait_s"

VISION_NAV_SITL_DRY_RUN=1 \
VISION_NAV_SITL_SMOKE_DIR="$session_dir" \
"$repo_root/scripts/dev/px4_sitl_external_vision_smoke.sh" >/dev/null

tmux send-keys -t "$tmux_session" C-l
tmux send-keys -t "$tmux_session" "listener vehicle_visual_odometry 5" C-m
sleep "$listener_arm_wait_s"

VISION_NAV_SITL_SMOKE_DIR="$session_dir" \
"$repo_root/scripts/dev/px4_sitl_external_vision_smoke.sh"

sleep "$capture_wait_s"
tmux capture-pane -t "$tmux_session" -p -S -2000 > "$listener_capture"

tmux send-keys -t "$tmux_session" C-l
tmux send-keys -t "$tmux_session" "mavlink status" C-m
sleep "$capture_wait_s"
tmux capture-pane -t "$tmux_session" -p -S -2000 > "$mavlink_status_capture"

"$repo_root/scripts/dev/evaluate_px4_sitl_session.sh" "$session_dir"

cat <<EOF
PX4 SITL receiver capture complete:
  session:  $session_dir
  listener: $listener_capture
  mavlink status: $mavlink_status_capture
  report: $receiver_report
EOF

echo "__VISION_NAV_PX4_SITL_SESSION__=$session_dir"
echo "__VISION_NAV_PX4_SITL_REPORT__=$receiver_report"

if [[ "$keep_tmux" == "1" ]]; then
  echo "Keeping tmux session '$tmux_session' because VISION_NAV_PX4_KEEP_TMUX=1."
fi
