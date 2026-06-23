#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/vision-nav-local-preflight.XXXXXX")"
cleanup() {
  local exit_code=$?
  if [[ "$exit_code" -eq 0 ]]; then
    rm -rf "$tmp_dir"
  else
    echo "Preflight temp files preserved at: $tmp_dir" >&2
  fi
}
trap cleanup EXIT

echo "[1/7] Checking shell script syntax"
find scripts -type f -name '*.sh' -exec bash -n {} \;

echo "[2/7] Compiling Python"
python3 -m compileall src tests >/dev/null

echo "[3/7] Running unit tests"
PYTHONPATH=src python3 tests/run_unit_tests.py

echo "[4/7] Evaluating synthetic replay cases"
synthetic_replay_output="$tmp_dir/synthetic_replay_cases.txt"
./scripts/dev/evaluate_synthetic_replay_cases.sh >"$synthetic_replay_output"
tail -n 8 "$synthetic_replay_output"

echo "[5/7] Auditing replay coverage template"
coverage_output="$tmp_dir/replay_coverage_template.txt"
PYTHONPATH=src python3 -m vision_nav.replay_dataset_audit \
  --manifest data/replay_cases/manifest.example.json \
  --skip-log-exists >"$coverage_output"
tail -n 10 "$coverage_output"

echo "[6/7] Checking field evidence scaffold"
field_dir="$tmp_dir/field-case"
mkdir -p "$field_dir/logs"
PYTHONPATH=src python3 -m vision_nav.field_evidence_template \
  --output "$field_dir/field_manifest.template.json" \
  --site-name preflight \
  --bundle preflight-bundle >"$field_dir/template.txt"
VISION_NAV_PYTHON=python3 \
VISION_NAV_FIELD_TEMPLATE="$field_dir/pi_field_manifest.template.json" \
VISION_NAV_FIELD_MANIFEST="$field_dir/pi_field_manifest.json" \
VISION_NAV_FIELD_SITE_NAME=preflight-pi-wrapper \
VISION_NAV_FIELD_BUNDLE=preflight-bundle \
./scripts/pi/create_field_evidence_template.sh >"$field_dir/pi_template.txt"
VISION_NAV_PYTHON=python3 \
VISION_NAV_FIELD_MANIFEST="$field_dir/pi_field_manifest.json" \
VISION_NAV_FIELD_COLLECTION_PLAN="$field_dir/field_collection_plan.json" \
VISION_NAV_FIELD_COLLECTION_PLAN_MD="$field_dir/field_collection_plan.md" \
VISION_NAV_FIELD_SITE_NAME=preflight-pi-wrapper \
VISION_NAV_FIELD_BUNDLE=preflight-bundle \
./scripts/pi/create_field_collection_plan.sh >"$field_dir/collection_plan.txt"
test -f "$field_dir/pi_field_manifest.json"
test -f "$field_dir/field_collection_plan.json"
grep -q "__VISION_NAV_FIELD_COLLECTION_PLAN__=" "$field_dir/collection_plan.txt"
python3 - "$field_dir/field_collection_plan.json" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

plan = json.loads(Path(sys.argv[1]).read_text())
assert plan["schema_version"] == "vision_nav_field_collection_plan_v1"
assert plan["summary"]["required_count"] == 8
assert plan["summary"]["placeholder_count"] == 8
assert "run_terrain_nav_loop.sh" in plan["next_condition"]["capture_command"]
assert "read_runtime_status.sh" in plan["next_condition"]["capture_command"]
PY

echo "[7/7] Checking active scaffold docs"
for path in \
  README.md \
  docs/project-summary.md \
  docs/project-plan.md \
  docs/stable-project-scaffold.md \
  docs/software-download-checklist.md \
  docs/raspberry-pi-setup.md \
  docs/desktop-app.md \
  docs/holybro-x500v2-hardware-data-inputs.md \
  docs/holybro-x500v2-prop-off-hardware-test.md
do
  test -f "$path"
done

if rg -n "make px4_sitl|gz_x500|ros2 launch|Micro XRCE|PX4 SITL Receiver Capture|Native rosbag2 Review|ROS Bag Validation" \
  README.md docs desktop-app/src/pages desktop-app/src/components >/tmp/vision-nav-stale-scaffold.txt; then
  cat /tmp/vision-nav-stale-scaffold.txt >&2
  echo "Active scaffold still contains required-workflow ROS/SITL wording." >&2
  exit 1
fi

echo "Local preflight passed."
