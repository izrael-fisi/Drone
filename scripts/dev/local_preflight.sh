#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"

echo "[1/8] Checking shell script syntax"
find scripts -type f -name '*.sh' -exec bash -n {} \;

echo "[2/8] Compiling Python"
python3 -m compileall src tests

echo "[3/8] Running unit tests"
PYTHONPATH=src python3 tests/run_unit_tests.py

echo "[4/8] Evaluating synthetic replay cases"
./scripts/dev/evaluate_synthetic_replay_cases.sh >/tmp/vision_nav_synthetic_replay_cases.txt
tail -n 8 /tmp/vision_nav_synthetic_replay_cases.txt

echo "[5/8] Auditing replay coverage template"
PYTHONPATH=src python3 -m vision_nav.replay_dataset_audit \
  --manifest data/replay_cases/manifest.example.json \
  --skip-log-exists >/tmp/vision_nav_replay_coverage_template.txt
tail -n 10 /tmp/vision_nav_replay_coverage_template.txt
field_smoke_dir="$(mktemp -d "${TMPDIR:-/tmp}/vision-nav-field-case-preflight.XXXXXX")"
PYTHONPATH=src python3 -m vision_nav.field_evidence_template \
  --output "$field_smoke_dir/field_manifest.template.json" \
  --site-name preflight \
  --bundle preflight-bundle >/tmp/vision_nav_field_template_preflight.txt
VISION_NAV_PYTHON=python3 \
VISION_NAV_FIELD_TEMPLATE="$field_smoke_dir/pi_field_manifest.template.json" \
VISION_NAV_FIELD_MANIFEST="$field_smoke_dir/pi_field_manifest.json" \
VISION_NAV_FIELD_SITE_NAME=preflight-pi-wrapper \
VISION_NAV_FIELD_BUNDLE=preflight-bundle \
./scripts/pi/create_field_evidence_template.sh >/tmp/vision_nav_pi_field_template_preflight.txt
grep -q "__VISION_NAV_FIELD_TEMPLATE__=" /tmp/vision_nav_pi_field_template_preflight.txt
grep -q "__VISION_NAV_FIELD_MANIFEST__=" /tmp/vision_nav_pi_field_template_preflight.txt
test -f "$field_smoke_dir/pi_field_manifest.template.json"
test -f "$field_smoke_dir/pi_field_manifest.json"
PYTHONPATH=src python3 -m vision_nav.replay_case_manifest \
  --manifest "$field_smoke_dir/field_manifest.template.json" \
  --schema-only >/tmp/vision_nav_field_template_schema_preflight.txt
mkdir -p "$field_smoke_dir/logs"
cat >"$field_smoke_dir/logs/terrain_matches.jsonl" <<'EOF'
{"sequence":1,"timestamp_us":1000000,"result":{"status":"accepted","confidence":0.82,"inliers":34,"reprojection_error_px":1.6,"scale_confidence":0.74,"local_enu_m":{"x":0.0,"y":0.0,"z":null},"covariance":{"x_m2":4.0,"y_m2":4.0,"z_m2":null,"yaw_rad2":null}}}
{"sequence":2,"timestamp_us":2000000,"result":{"status":"accepted","confidence":0.84,"inliers":36,"reprojection_error_px":1.4,"scale_confidence":0.76,"local_enu_m":{"x":4.0,"y":1.0,"z":null},"covariance":{"x_m2":4.0,"y_m2":4.0,"z_m2":null,"yaw_rad2":null}}}
EOF
VISION_NAV_PYTHON=python3 \
VISION_NAV_FIELD_MANIFEST="$field_smoke_dir/field_manifest.json" \
VISION_NAV_FIELD_EVIDENCE_REPORT="$field_smoke_dir/field_evidence_report.json" \
VISION_NAV_FIELD_CASE_REPORT_DIR="$field_smoke_dir/case_reports" \
VISION_NAV_FIELD_CASE_NAME=field-good-texture \
VISION_NAV_FIELD_EXPECTED=good_map \
VISION_NAV_FIELD_CONDITION=good_texture \
VISION_NAV_FIELD_LOG="$field_smoke_dir/logs/terrain_matches.jsonl" \
VISION_NAV_FIELD_BUNDLE=preflight-bundle \
./scripts/pi/register_field_replay_case.sh >/tmp/vision_nav_field_register_preflight.txt 2>&1
test -f "$field_smoke_dir/field_manifest.json"
test -f "$field_smoke_dir/field_evidence_report.json"
workflow_smoke_dir="$field_smoke_dir/workflow-smoke"
VISION_NAV_PYTHON=python3 \
VISION_NAV_EVIDENCE_WORKFLOW_DIR="$workflow_smoke_dir/workflow" \
VISION_NAV_EVIDENCE_WORKFLOW_REPORT="$workflow_smoke_dir/workflow/autonomy_evidence_workflow.json" \
VISION_NAV_FIELD_TEMPLATE="$workflow_smoke_dir/field_manifest.template.json" \
VISION_NAV_FIELD_MANIFEST="$workflow_smoke_dir/field_manifest.json" \
VISION_NAV_FIELD_SITE_NAME=preflight-workflow \
VISION_NAV_FIELD_BUNDLE=preflight-bundle \
VISION_NAV_BUNDLE=preflight-bundle \
VISION_NAV_FIELD_LOG="$workflow_smoke_dir/missing-terrain-matches.jsonl" \
VISION_NAV_FIELD_EVIDENCE_REPORT="$workflow_smoke_dir/replay-cases/field_evidence_report.json" \
VISION_NAV_FIELD_CASE_REPORT_DIR="$workflow_smoke_dir/replay-cases/field_evidence_cases" \
VISION_NAV_FEATURE_METHOD_BENCHMARK="$workflow_smoke_dir/feature-method-bench" \
VISION_NAV_THRESHOLD_TUNING_REPORT="$workflow_smoke_dir/replay-cases/threshold_tuning_report.json" \
VISION_NAV_THRESHOLD_CASE_REPORT_DIR="$workflow_smoke_dir/replay-cases/threshold_tuning_cases" \
VISION_NAV_SUPPORT_OUTPUT_DIR="$workflow_smoke_dir/support-bundles" \
VISION_NAV_AUTONOMY_READINESS_REPORT="$workflow_smoke_dir/replay-cases/autonomy_readiness_report.json" \
VISION_NAV_AUTONOMY_HANDOFF="$workflow_smoke_dir/replay-cases/autonomy_readiness_report.md" \
VISION_NAV_AUTONOMY_EVIDENCE_PACKAGE="$workflow_smoke_dir/replay-cases/autonomy_readiness_report.evidence.zip" \
./scripts/pi/run_autonomy_evidence_workflow.sh >/tmp/vision_nav_evidence_workflow_preflight.txt 2>&1
grep -q "__VISION_NAV_EVIDENCE_WORKFLOW_REPORT__=" /tmp/vision_nav_evidence_workflow_preflight.txt
test -f "$workflow_smoke_dir/workflow/autonomy_evidence_workflow.json"
python3 - "$workflow_smoke_dir/workflow/autonomy_evidence_workflow.json" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

report = json.loads(Path(sys.argv[1]).read_text())
assert report["schema_version"] == "vision_nav_autonomy_evidence_workflow_v1"
steps = {step["name"]: step for step in report["steps"]}
assert "create_field_evidence_template" in steps
assert "run_autonomy_readiness_audit" in steps
assert report["status"] in {"passed", "degraded", "failed"}
PY
rm -rf "$field_smoke_dir"

echo "[6/8] Checking autonomy-readiness fail-closed behavior"
if PYTHONPATH=src python3 -m vision_nav.autonomy_readiness --json >/tmp/vision_nav_autonomy_readiness_preflight.txt; then
  echo "Expected autonomy readiness to fail before support bundle, PX4, field, feature, and threshold evidence exist." >&2
  exit 1
fi
tail -n 18 /tmp/vision_nav_autonomy_readiness_preflight.txt
local_audit_dir="$(mktemp -d "${TMPDIR:-/tmp}/vision-nav-local-audit-preflight.XXXXXX")"
mkdir -p "$local_audit_dir/feature-method-bench"
mkdir -p "$local_audit_dir/px4-sitl-evidence"
cat >"$local_audit_dir/feature-method-bench/preflight_feature_benchmark.json" <<'EOF'
{
  "status": "passed",
  "case_name": "preflight-feature-method",
  "expected": "good_map",
  "recommended_method": "orb",
  "methods": [
    {"method": "orb", "status": "passed", "record_count": 2}
  ]
}
EOF
cat >"$local_audit_dir/px4-sitl-evidence/receiver_evidence.json" <<'EOF'
{
  "status": "passed",
  "expected_message": "odometry",
  "session_dir": "preflight-px4-sitl-evidence",
  "report_path": "receiver_evidence.json",
  "listener": {
    "sample_count": 3,
    "latest_sample_age_s": 0.2,
    "last_position": [0.0, 1.0, -2.0]
  },
  "issues": []
}
EOF
VISION_NAV_LOCAL_SUPPORT_DIR="$local_audit_dir/support-bundles" \
VISION_NAV_LOCAL_REPLAY_DIR="$local_audit_dir/replay-cases" \
VISION_NAV_LOCAL_FEATURE_BENCH_DIR="$local_audit_dir/feature-method-bench" \
VISION_NAV_PX4_SITL_REPORT="$local_audit_dir/px4-sitl-evidence/receiver_evidence.json" \
VISION_NAV_AUTONOMY_ALLOW_FAILED=1 \
./scripts/dev/run_local_autonomy_readiness_audit.sh >/tmp/vision_nav_local_autonomy_readiness_preflight.txt 2>&1
grep -q "__VISION_NAV_AUTONOMY_REPORT__=" /tmp/vision_nav_local_autonomy_readiness_preflight.txt
grep -q "__VISION_NAV_AUTONOMY_HANDOFF__=" /tmp/vision_nav_local_autonomy_readiness_preflight.txt
grep -q "__VISION_NAV_AUTONOMY_EVIDENCE_PACKAGE__=" /tmp/vision_nav_local_autonomy_readiness_preflight.txt
grep -q "__VISION_NAV_PX4_SITL_REPORT__=" /tmp/vision_nav_local_autonomy_readiness_preflight.txt
test -f "$local_audit_dir/replay-cases/autonomy_readiness_report.json"
test -f "$local_audit_dir/replay-cases/autonomy_readiness_report.md"
test -f "$local_audit_dir/replay-cases/autonomy_readiness_report.evidence.zip"
grep -q "preflight_feature_benchmark.json" "$local_audit_dir/replay-cases/autonomy_readiness_report.json"
grep -q "receiver_evidence.json" "$local_audit_dir/replay-cases/autonomy_readiness_report.json"
grep -q '"next_actions"' "$local_audit_dir/replay-cases/autonomy_readiness_report.json"
grep -q "Autonomy Readiness Handoff" "$local_audit_dir/replay-cases/autonomy_readiness_report.md"
python3 - "$local_audit_dir/replay-cases/autonomy_readiness_report.evidence.zip" <<'PY'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as archive:
    names = set(archive.namelist())
    assert "manifest.json" in names
    assert "reports/autonomy_readiness_report.json" in names
    assert "reports/autonomy_readiness_report.md" in names
PY
rm -rf "$local_audit_dir"

echo "[7/8] Preparing PX4 SITL evidence session dry-run"
smoke_dir="$(mktemp -d "${TMPDIR:-/tmp}/vision-nav-sitl-smoke-preflight.XXXXXX")"
VISION_NAV_SITL_DRY_RUN=1 \
VISION_NAV_SITL_SMOKE_DIR="$smoke_dir" \
./scripts/dev/px4_sitl_external_vision_smoke.sh >/tmp/vision_nav_px4_sitl_smoke_dry_run.txt
grep -q "__VISION_NAV_PX4_SITL_SESSION__=" /tmp/vision_nav_px4_sitl_smoke_dry_run.txt
grep -q "__VISION_NAV_PX4_SITL_REPORT__=" /tmp/vision_nav_px4_sitl_smoke_dry_run.txt
test -f "$smoke_dir/px4_sitl_evidence_session.json"
test -f "$smoke_dir/receiver_capture/README.md"
test -f "$smoke_dir/synthetic_external_vision.jsonl"
capture_smoke_dir="$(mktemp -d "${TMPDIR:-/tmp}/vision-nav-sitl-capture-preflight.XXXXXX")"
VISION_NAV_SITL_CAPTURE_DRY_RUN=1 \
VISION_NAV_SITL_SMOKE_DIR="$capture_smoke_dir" \
./scripts/dev/run_px4_sitl_external_vision_capture.sh >/tmp/vision_nav_px4_sitl_capture_dry_run.txt
grep -q "__VISION_NAV_PX4_SITL_SESSION__=" /tmp/vision_nav_px4_sitl_capture_dry_run.txt
grep -q "__VISION_NAV_PX4_SITL_REPORT__=" /tmp/vision_nav_px4_sitl_capture_dry_run.txt
test -f "$capture_smoke_dir/px4_sitl_evidence_session.json"
test -f "$capture_smoke_dir/receiver_capture/README.md"
if VISION_NAV_ALLOW_DEGRADED=1 ./scripts/dev/evaluate_px4_sitl_session.sh "$smoke_dir" >/tmp/vision_nav_px4_sitl_session_missing_capture.txt; then
  echo "Expected PX4 SITL session evaluation to fail before receiver captures exist." >&2
  exit 1
fi
rm -rf "$smoke_dir"
rm -rf "$capture_smoke_dir"

echo "[8/8] Checking unrelated agent/chatbot scope is absent"
scope_pattern="M""CP|L""LM|Chat""GPT"
if rg -n "$scope_pattern" .; then
  echo "Found unrelated agent/chatbot scope text. Remove it before committing." >&2
  exit 1
fi

echo "Local preflight passed."
