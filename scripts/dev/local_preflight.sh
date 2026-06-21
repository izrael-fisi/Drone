#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

echo "[1/5] Checking shell script syntax"
find scripts -type f -name '*.sh' -exec bash -n {} \;

echo "[2/5] Compiling Python"
python3 -m compileall src tests

echo "[3/5] Running unit tests"
PYTHONPATH=src python3 tests/run_unit_tests.py

echo "[4/5] Evaluating synthetic replay cases"
./scripts/dev/evaluate_synthetic_replay_cases.sh >/tmp/vision_nav_synthetic_replay_cases.txt
tail -n 8 /tmp/vision_nav_synthetic_replay_cases.txt

echo "[5/5] Checking unrelated agent/chatbot scope is absent"
scope_pattern="M""CP|L""LM|Chat""GPT"
if rg -n "$scope_pattern" .; then
  echo "Found unrelated agent/chatbot scope text. Remove it before committing." >&2
  exit 1
fi

echo "Local preflight passed."
