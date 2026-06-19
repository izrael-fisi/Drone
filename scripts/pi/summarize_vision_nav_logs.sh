#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
runtime_log="${VISION_NAV_RUNTIME_LOG:-$HOME/DroneTransfer/outgoing/runtime-match/matches.jsonl}"
replay_log="${VISION_NAV_REPLAY_LOG:-$HOME/DroneTransfer/outgoing/replay-match/replay_matches.jsonl}"

if [[ ! -x "$venv_python" ]]; then
  echo "Missing Python venv: $venv_python" >&2
  echo "Run ./scripts/pi/bootstrap_pi5.sh first, then reboot." >&2
  exit 1
fi

logs=()
if [[ -f "$runtime_log" ]]; then
  logs+=("$runtime_log")
fi
if [[ -f "$replay_log" ]]; then
  logs+=("$replay_log")
fi

if ((${#logs[@]} == 0)); then
  echo "No vision match logs found." >&2
  echo "Expected one of:" >&2
  echo "  $runtime_log" >&2
  echo "  $replay_log" >&2
  exit 1
fi

PYTHONPATH="$repo_root/src" "$venv_python" -m vision_nav.summarize_match_log "${logs[@]}"
