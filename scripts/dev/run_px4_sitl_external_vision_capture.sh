#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${VISION_NAV_PYTHON:-python3}"
px4_dir="${VISION_NAV_PX4_AUTOPILOT_DIR:-$HOME/PX4-Autopilot}"
px4_target="${VISION_NAV_PX4_SITL_TARGET:-px4_sitl_default sihsim_quadx}"
session_dir="${VISION_NAV_SITL_SMOKE_DIR:-$PWD/px4-sitl-evidence}"
tmux_session="${VISION_NAV_PX4_TMUX_SESSION:-vision-nav-px4-sitl}"
boot_wait_s="${VISION_NAV_PX4_BOOT_WAIT_S:-45}"
listener_arm_wait_s="${VISION_NAV_PX4_LISTENER_ARM_WAIT_S:-2}"
listener_sample_count="${VISION_NAV_PX4_LISTENER_SAMPLE_COUNT:-5}"
listener_sample_wait_s="${VISION_NAV_PX4_LISTENER_SAMPLE_WAIT_S:-0.2}"
capture_wait_s="${VISION_NAV_PX4_CAPTURE_WAIT_S:-4}"
keep_tmux="${VISION_NAV_PX4_KEEP_TMUX:-0}"
dry_run="${VISION_NAV_SITL_CAPTURE_DRY_RUN:-0}"

capture_dir="$session_dir/receiver_capture"
listener_capture="$capture_dir/vehicle_visual_odometry.txt"
mavlink_status_capture="$capture_dir/mavlink_status.txt"
px4_console_capture="$capture_dir/px4_sitl_console.txt"
receiver_report="$session_dir/receiver_evidence.json"
prereq_report="$session_dir/px4_sitl_capture_prereqs.json"

mkdir -p "$capture_dir"

write_prereq_report() {
  local report_status="$1"
  local tmux_status="$2"
  local tmux_message="$3"
  local px4_status="$4"
  local px4_message="$5"
  local cmake_status="$6"
  local cmake_message="$7"
  local px4_python_status="$8"
  local px4_python_message="$9"
  local session_status="${10}"
  local session_message="${11}"
  PYTHONPATH="$repo_root/src" "$python_bin" - \
    "$prereq_report" \
    "$report_status" \
    "$session_dir" \
    "$capture_dir" \
    "$repo_root" \
    "$python_bin" \
    "$px4_dir" \
    "$px4_target" \
    "$tmux_session" \
    "$receiver_report" \
    "$tmux_status" \
    "$tmux_message" \
    "$px4_status" \
    "$px4_message" \
    "$cmake_status" \
    "$cmake_message" \
    "$px4_python_status" \
    "$px4_python_message" \
    "$session_status" \
    "$session_message" <<'PY'
from __future__ import annotations

import json
import shlex
from datetime import datetime, timezone
from pathlib import Path
import sys

(
    report_path,
    status,
    session_dir,
    capture_dir,
    repo_root,
    python_bin,
    px4_dir,
    px4_target,
    tmux_session,
    receiver_report,
    tmux_status,
    tmux_message,
    px4_status,
    px4_message,
    cmake_status,
    cmake_message,
    px4_python_status,
    px4_python_message,
    session_status,
    session_message,
) = sys.argv[1:]
path = Path(report_path)
checks = [
    {"name": "tmux_installed", "status": tmux_status, "message": tmux_message},
    {"name": "px4_autopilot_dir", "status": px4_status, "message": px4_message},
    {"name": "cmake_installed", "status": cmake_status, "message": cmake_message},
    {"name": "px4_python_requirements", "status": px4_python_status, "message": px4_python_message},
    {"name": "tmux_session_available", "status": session_status, "message": session_message},
]


def fix_command(label: str, command: str, condition: str) -> dict[str, str]:
    return {"label": label, "command": command, "condition": condition}


def q(value: str) -> str:
    return shlex.quote(value)


fix_commands = []
setup_helper = str(Path(repo_root) / "scripts/dev/setup_px4_sitl_prereqs.sh")
if tmux_status == "failed" or px4_status == "failed" or cmake_status == "failed" or px4_python_status == "failed":
    fix_commands.append(
        fix_command(
            "Review PX4 SITL prerequisite setup helper",
            q(setup_helper),
            "px4_sitl_prereqs",
        )
    )
if cmake_status == "failed":
    fix_commands.extend(
        [
            fix_command(
                "Install cmake with the setup helper",
                " ".join([q(setup_helper), "--apply", "--px4-dir", q(px4_dir)]),
                "cmake_installed",
            ),
            fix_command("Install cmake with Homebrew", "brew install cmake", "cmake_installed"),
            fix_command(
                "Install cmake on Ubuntu/Debian",
                "sudo apt update && sudo apt install -y cmake",
                "cmake_installed",
            ),
        ]
    )
if px4_python_status == "failed":
    requirements = Path(px4_dir) / "Tools/setup/requirements.txt"
    fix_commands.extend(
        [
            fix_command(
                "Install PX4 Python requirements with the setup helper",
                " ".join([q(setup_helper), "--apply", "--px4-dir", q(px4_dir)]),
                "px4_python_requirements",
            ),
            fix_command(
                "Install PX4 Python requirements with pip",
                " ".join([q(python_bin), "-m", "pip", "install", "-r", q(str(requirements))]),
                "px4_python_requirements",
            ),
        ]
    )
if tmux_status == "failed":
    fix_commands.extend(
        [
            fix_command(
                "Install tmux with the setup helper",
                " ".join([q(setup_helper), "--apply", "--px4-dir", q(px4_dir)]),
                "tmux_installed",
            ),
            fix_command("Install tmux with Homebrew", "brew install tmux", "tmux_installed"),
            fix_command(
                "Install tmux on Ubuntu/Debian",
                "sudo apt update && sudo apt install -y tmux",
                "tmux_installed",
            ),
        ]
    )
if px4_status == "failed":
    fix_commands.extend(
        [
            fix_command(
                "Clone PX4-Autopilot with the setup helper",
                " ".join([q(setup_helper), "--apply", "--clone-px4", "--px4-dir", q(px4_dir)]),
                "px4_autopilot_dir",
            ),
            fix_command(
                "Clone PX4-Autopilot to the default path",
                f"git clone https://github.com/PX4/PX4-Autopilot.git {q(str(Path.home() / 'PX4-Autopilot'))}",
                "px4_autopilot_dir",
            ),
            fix_command(
                "Point the harness at an existing PX4 checkout",
                "export VISION_NAV_PX4_AUTOPILOT_DIR=/path/to/PX4-Autopilot",
                "px4_autopilot_dir",
            ),
        ]
    )
if session_status == "failed":
    fix_commands.append(
        fix_command(
            "Use a different tmux session name",
            "export VISION_NAV_PX4_TMUX_SESSION=vision-nav-px4-sitl-2",
            "tmux_session_available",
        )
    )
if status != "passed":
    fix_commands.append(
        fix_command(
            "Rerun PX4 receiver capture harness",
            " ".join(
                [
                    f"VISION_NAV_SITL_SMOKE_DIR={q(session_dir)}",
                    f"VISION_NAV_PX4_AUTOPILOT_DIR={q(px4_dir)}",
                    q(str(Path(repo_root) / "scripts/dev/run_px4_sitl_external_vision_capture.sh")),
                ]
            ),
            "rerun_capture",
        )
    )
report = {
    "schema_version": "vision_nav_px4_sitl_capture_prereqs_v1",
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "status": status,
    "session_dir": session_dir,
    "capture_dir": capture_dir,
    "px4_dir": px4_dir,
    "px4_target": px4_target,
    "tmux_session": tmux_session,
    "receiver_report": receiver_report,
    "checks": checks,
    "next_actions": [check["message"] for check in checks if check["status"] == "failed"],
    "fix_commands": fix_commands,
    "markers": {
        "__VISION_NAV_PX4_SITL_SESSION__": session_dir,
        "__VISION_NAV_PX4_SITL_PREREQS__": str(path),
        "__VISION_NAV_PX4_SITL_REPORT__": receiver_report,
    },
}
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
}

check_capture_prereqs() {
  local tmux_status="passed"
  local tmux_message="tmux is installed."
  local px4_status="passed"
  local px4_message="PX4-Autopilot directory exists: $px4_dir"
  local cmake_status="passed"
  local cmake_message="cmake is installed."
  local px4_python_status="passed"
  local px4_python_message="PX4 Python build requirements are installed for $python_bin."
  local session_status="passed"
  local session_message="tmux session name is available: $tmux_session"
  local report_status="passed"
  local tmux_available=1

  if ! command -v tmux >/dev/null 2>&1; then
    tmux_available=0
    tmux_status="failed"
    tmux_message="tmux is required for automated PX4 SITL shell capture. Install tmux or use receiver_capture/README.md for manual PX4 shell captures."
    session_status="skipped"
    session_message="tmux session availability was not checked because tmux is missing."
    report_status="failed"
  fi

  if [[ ! -d "$px4_dir" ]]; then
    px4_status="failed"
    px4_message="PX4-Autopilot directory not found: $px4_dir. Set VISION_NAV_PX4_AUTOPILOT_DIR=/path/to/PX4-Autopilot if it lives elsewhere."
    report_status="failed"
  fi

  local px4_requirements="$px4_dir/Tools/setup/requirements.txt"
  if [[ ! -d "$px4_dir" ]]; then
    px4_python_status="skipped"
    px4_python_message="PX4 Python requirements were not checked because the PX4-Autopilot directory is missing."
  elif [[ ! -f "$px4_requirements" ]]; then
    px4_python_status="failed"
    px4_python_message="PX4 Python requirements file not found: $px4_requirements."
    report_status="failed"
  elif ! "$python_bin" -c "import menuconfig" >/dev/null 2>&1; then
    px4_python_status="failed"
    px4_python_message="PX4 Python build requirements are missing for $python_bin. Install $px4_requirements before running make $px4_target."
    report_status="failed"
  fi

  if ! command -v cmake >/dev/null 2>&1; then
    cmake_status="failed"
    cmake_message="cmake is required to build PX4 SITL. Install cmake before running make $px4_target."
    report_status="failed"
  fi

  if [[ "$tmux_available" == "1" ]] && tmux has-session -t "$tmux_session" 2>/dev/null; then
    session_status="failed"
    session_message="tmux session already exists: $tmux_session. Set VISION_NAV_PX4_TMUX_SESSION to another name or close the existing session."
    report_status="failed"
  fi

  write_prereq_report \
    "$report_status" \
    "$tmux_status" \
    "$tmux_message" \
    "$px4_status" \
    "$px4_message" \
    "$cmake_status" \
    "$cmake_message" \
    "$px4_python_status" \
    "$px4_python_message" \
    "$session_status" \
    "$session_message"
  [[ "$report_status" == "passed" ]]
}

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
  echo "__VISION_NAV_PX4_SITL_PREREQS__=$prereq_report"
  echo "__VISION_NAV_PX4_SITL_REPORT__=$receiver_report"
}

fail_prereq() {
  prepare_session_scaffold >/dev/null 2>&1 || true
  cat <<EOF >&2

PX4 SITL receiver capture prerequisites are not ready.
Prepared a reusable evidence-session scaffold when possible:
  session: $session_dir
  prerequisite report: $prereq_report
  capture instructions: $capture_dir/README.md
  expected receiver report: $receiver_report
  setup helper: $repo_root/scripts/dev/setup_px4_sitl_prereqs.sh

After fixing the prerequisite, rerun:
  VISION_NAV_SITL_SMOKE_DIR="$session_dir" $repo_root/scripts/dev/run_px4_sitl_external_vision_capture.sh
EOF
  print_session_markers
  exit 2
}

if [[ "$dry_run" == "1" ]]; then
  prepare_session_scaffold
  write_prereq_report \
    "not_checked" \
    "not_checked" \
    "Dry run prepared the evidence-session scaffold without requiring tmux." \
    "not_checked" \
    "Dry run prepared the evidence-session scaffold without requiring PX4." \
    "not_checked" \
    "Dry run prepared the evidence-session scaffold without requiring cmake." \
    "not_checked" \
    "Dry run prepared the evidence-session scaffold without requiring PX4 Python build requirements." \
    "not_checked" \
    "Dry run did not inspect tmux session availability."
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

if ! check_capture_prereqs; then
  fail_prereq
fi

cleanup() {
  if [[ "$keep_tmux" != "1" ]] && tmux has-session -t "$tmux_session" 2>/dev/null; then
    tmux kill-session -t "$tmux_session" >/dev/null 2>&1 || true
  fi
}
trap cleanup EXIT

fail_px4_session_unavailable() {
  local stage="$1"
  "$repo_root/scripts/dev/evaluate_px4_sitl_session.sh" "$session_dir" || true
  cat <<EOF >&2
PX4 SITL tmux session was not available during: $stage
Inspect the PX4 console log for build or simulator startup errors:
  $px4_console_capture
EOF
  exit 1
}

tmux_send_keys_checked() {
  local stage="$1"
  shift
  tmux has-session -t "$tmux_session" 2>/dev/null || fail_px4_session_unavailable "$stage"
  tmux send-keys -t "$tmux_session" "$@" || fail_px4_session_unavailable "$stage"
}

tmux_capture_checked() {
  local stage="$1"
  local output="$2"
  shift 2
  tmux has-session -t "$tmux_session" 2>/dev/null || fail_px4_session_unavailable "$stage"
  tmux capture-pane -t "$tmux_session" "$@" > "$output" || fail_px4_session_unavailable "$stage"
}

tmux new-session -d -s "$tmux_session" -c "$px4_dir"
: > "$listener_capture"
: > "$mavlink_status_capture"
: > "$px4_console_capture"
tmux pipe-pane -o -t "$tmux_session" "cat >> '$px4_console_capture'"
tmux_send_keys_checked "PX4 SITL launch" "make $px4_target" C-m

cat <<EOF
Started PX4 SITL in tmux session '$tmux_session':
  cd $px4_dir
  make $px4_target

Waiting ${boot_wait_s}s before sending synthetic external-vision records...
EOF
sleep "$boot_wait_s"

if ! tmux has-session -t "$tmux_session" 2>/dev/null; then
  fail_px4_session_unavailable "PX4 boot wait"
fi

VISION_NAV_SITL_DRY_RUN=1 \
VISION_NAV_SITL_SMOKE_DIR="$session_dir" \
"$repo_root/scripts/dev/px4_sitl_external_vision_smoke.sh" >/dev/null

sender_status=0
VISION_NAV_SITL_EVALUATE_RECEIVER=0 \
VISION_NAV_SITL_SMOKE_DIR="$session_dir" \
"$repo_root/scripts/dev/px4_sitl_external_vision_smoke.sh" &
sender_pid=$!

sleep "$listener_arm_wait_s"
tmux_send_keys_checked "listener clear" C-l
for ((sample_index = 1; sample_index <= listener_sample_count; sample_index += 1)); do
  tmux_send_keys_checked "listener sample $sample_index" "listener vehicle_visual_odometry" C-m
  sleep "$listener_sample_wait_s"
done

wait "$sender_pid" || sender_status=$?
if [[ "$sender_status" -ne 0 ]]; then
  echo "Synthetic external-vision sender failed with status $sender_status." >&2
  exit "$sender_status"
fi

if ! tmux has-session -t "$tmux_session" 2>/dev/null; then
  fail_px4_session_unavailable "synthetic sender"
fi

sleep "$capture_wait_s"
tmux_capture_checked "listener capture" "$listener_capture" -p -S -2000

tmux_send_keys_checked "mavlink status clear" C-l
tmux_send_keys_checked "mavlink status command" "mavlink status" C-m
sleep "$capture_wait_s"
tmux_capture_checked "mavlink status capture" "$mavlink_status_capture" -p -S -2000

eval_status=0
"$repo_root/scripts/dev/evaluate_px4_sitl_session.sh" "$session_dir" || eval_status=$?

cat <<EOF
PX4 SITL receiver capture complete:
  session:  $session_dir
  console:  $px4_console_capture
  listener: $listener_capture
  mavlink status: $mavlink_status_capture
  report: $receiver_report
EOF

echo "__VISION_NAV_PX4_SITL_SESSION__=$session_dir"
echo "__VISION_NAV_PX4_SITL_REPORT__=$receiver_report"

if [[ "$keep_tmux" == "1" ]]; then
  echo "Keeping tmux session '$tmux_session' because VISION_NAV_PX4_KEEP_TMUX=1."
fi

exit "$eval_status"
