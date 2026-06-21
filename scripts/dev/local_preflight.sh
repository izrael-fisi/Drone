#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

echo "[1/7] Checking shell script syntax"
find scripts -type f -name '*.sh' -exec bash -n {} \;

echo "[2/7] Compiling Python"
python3 -m compileall src tests

echo "[3/7] Running unit tests"
PYTHONPATH=src python3 tests/run_unit_tests.py

echo "[4/7] Evaluating synthetic replay cases"
./scripts/dev/evaluate_synthetic_replay_cases.sh >/tmp/vision_nav_synthetic_replay_cases.txt
tail -n 8 /tmp/vision_nav_synthetic_replay_cases.txt

echo "[5/7] Auditing replay coverage template"
PYTHONPATH=src python3 -m vision_nav.replay_dataset_audit \
  --manifest data/replay_cases/manifest.example.json \
  --skip-log-exists >/tmp/vision_nav_replay_coverage_template.txt
tail -n 10 /tmp/vision_nav_replay_coverage_template.txt

echo "[6/7] Preparing PX4 SITL evidence session dry-run"
smoke_dir="$(mktemp -d "${TMPDIR:-/tmp}/vision-nav-sitl-smoke-preflight.XXXXXX")"
VISION_NAV_SITL_DRY_RUN=1 \
VISION_NAV_SITL_SMOKE_DIR="$smoke_dir" \
./scripts/dev/px4_sitl_external_vision_smoke.sh >/tmp/vision_nav_px4_sitl_smoke_dry_run.txt
test -f "$smoke_dir/px4_sitl_evidence_session.json"
test -f "$smoke_dir/receiver_capture/README.md"
test -f "$smoke_dir/synthetic_external_vision.jsonl"
if VISION_NAV_ALLOW_DEGRADED=1 ./scripts/dev/evaluate_px4_sitl_session.sh "$smoke_dir" >/tmp/vision_nav_px4_sitl_session_missing_capture.txt; then
  echo "Expected PX4 SITL session evaluation to fail before receiver captures exist." >&2
  exit 1
fi
rm -rf "$smoke_dir"

echo "[7/7] Checking unrelated agent/chatbot scope is absent"
scope_pattern="M""CP|L""LM|Chat""GPT"
if rg -n "$scope_pattern" .; then
  echo "Found unrelated agent/chatbot scope text. Remove it before committing." >&2
  exit 1
fi

echo "Local preflight passed."
