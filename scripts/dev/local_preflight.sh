#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
cd "$repo_root"
preflight_tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/vision-nav-local-preflight.XXXXXX")"

cleanup() {
  local exit_code=$?
  if [[ "$exit_code" -eq 0 ]]; then
    rm -rf "$preflight_tmp_dir"
  else
    echo "Preflight temp files preserved at: $preflight_tmp_dir" >&2
  fi
}
trap cleanup EXIT

echo "[1/8] Checking shell script syntax"
find scripts -type f -name '*.sh' -exec bash -n {} \;

echo "[2/8] Compiling Python"
python3 -m compileall src tests

echo "[3/8] Running unit tests"
PYTHONPATH=src python3 tests/run_unit_tests.py

echo "[4/8] Evaluating synthetic replay cases"
synthetic_replay_output="$preflight_tmp_dir/synthetic_replay_cases.txt"
./scripts/dev/evaluate_synthetic_replay_cases.sh >"$synthetic_replay_output"
tail -n 8 "$synthetic_replay_output"

echo "[5/8] Auditing replay coverage template"
replay_coverage_output="$preflight_tmp_dir/replay_coverage_template.txt"
PYTHONPATH=src python3 -m vision_nav.replay_dataset_audit \
  --manifest data/replay_cases/manifest.example.json \
  --skip-log-exists >"$replay_coverage_output"
tail -n 10 "$replay_coverage_output"
field_template_output="$preflight_tmp_dir/field_template_preflight.txt"
pi_field_template_output="$preflight_tmp_dir/pi_field_template_preflight.txt"
field_collection_plan_output="$preflight_tmp_dir/field_collection_plan_preflight.txt"
field_template_schema_output="$preflight_tmp_dir/field_template_schema_preflight.txt"
field_register_output="$preflight_tmp_dir/field_register_preflight.txt"
evidence_workflow_output="$preflight_tmp_dir/evidence_workflow_preflight.txt"
evidence_workflow_validation_output="$preflight_tmp_dir/evidence_workflow_validation_preflight.txt"
invalid_evidence_workflow_output="$preflight_tmp_dir/evidence_workflow_invalid_log.txt"
bad_status_evidence_workflow_output="$preflight_tmp_dir/evidence_workflow_bad_status.txt"
support_autodetect_output="$preflight_tmp_dir/support_autodetect_px4_report.txt"
rosbag_validation_output="$preflight_tmp_dir/rosbag_validation_preflight.txt"
rosbag2_review_output="$preflight_tmp_dir/rosbag2_cli_review_dry_run.txt"
field_smoke_dir="$(mktemp -d "$preflight_tmp_dir/field-case.XXXXXX")"
PYTHONPATH=src python3 -m vision_nav.field_evidence_template \
  --output "$field_smoke_dir/field_manifest.template.json" \
  --site-name preflight \
  --bundle preflight-bundle >"$field_template_output"
VISION_NAV_PYTHON=python3 \
VISION_NAV_FIELD_TEMPLATE="$field_smoke_dir/pi_field_manifest.template.json" \
VISION_NAV_FIELD_MANIFEST="$field_smoke_dir/pi_field_manifest.json" \
VISION_NAV_FIELD_SITE_NAME=preflight-pi-wrapper \
VISION_NAV_FIELD_BUNDLE=preflight-bundle \
./scripts/pi/create_field_evidence_template.sh >"$pi_field_template_output"
grep -q "__VISION_NAV_FIELD_TEMPLATE__=" "$pi_field_template_output"
grep -q "__VISION_NAV_FIELD_MANIFEST__=" "$pi_field_template_output"
test -f "$field_smoke_dir/pi_field_manifest.template.json"
test -f "$field_smoke_dir/pi_field_manifest.json"
VISION_NAV_PYTHON=python3 \
VISION_NAV_FIELD_MANIFEST="$field_smoke_dir/pi_field_manifest.json" \
VISION_NAV_FIELD_COLLECTION_PLAN="$field_smoke_dir/field_collection_plan.json" \
VISION_NAV_FIELD_COLLECTION_PLAN_MD="$field_smoke_dir/field_collection_plan.md" \
VISION_NAV_FIELD_SITE_NAME=preflight-pi-wrapper \
VISION_NAV_FIELD_BUNDLE=preflight-bundle \
./scripts/pi/create_field_collection_plan.sh >"$field_collection_plan_output"
grep -q "__VISION_NAV_FIELD_COLLECTION_PLAN__=" "$field_collection_plan_output"
grep -q "__VISION_NAV_FIELD_COLLECTION_PLAN_MD__=" "$field_collection_plan_output"
test -f "$field_smoke_dir/field_collection_plan.json"
test -f "$field_smoke_dir/field_collection_plan.md"
python3 - "$field_smoke_dir/field_collection_plan.json" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

plan = json.loads(Path(sys.argv[1]).read_text())
assert plan["schema_version"] == "vision_nav_field_collection_plan_v1"
assert plan["summary"]["required_count"] == 8
assert plan["summary"]["placeholder_count"] == 8
assert plan["summary"]["registered_count"] == 0
PY
PYTHONPATH=src python3 -m vision_nav.replay_case_manifest \
  --manifest "$field_smoke_dir/field_manifest.template.json" \
  --schema-only >"$field_template_schema_output"
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
./scripts/pi/register_field_replay_case.sh >"$field_register_output" 2>&1
test -f "$field_smoke_dir/field_manifest.json"
test -f "$field_smoke_dir/field_evidence_report.json"
workflow_smoke_dir="$field_smoke_dir/workflow-smoke"
mkdir -p "$workflow_smoke_dir"
cat >"$workflow_smoke_dir/terrain_matches.jsonl" <<'EOF'
{"sequence": 1, "result": {"status": "accepted", "timestamp_us": 1000000, "measurement": {"frame": "local_enu", "x_m": 1.0, "y_m": 2.0, "covariance": {"x_m2": 1.0, "y_m2": 1.0}}}}
EOF
cat >"$workflow_smoke_dir/runtime_status.json" <<'EOF'
{"schema_version":"vision_nav_runtime_status_v1","active_map":{"bundle_id":"preflight"},"output":{"output_dir":"preflight-output","log_path":"preflight-output/terrain_matches.jsonl"},"last_match":{"status":"accepted"},"estimator":{"health":"healthy"},"external_position_health":{"status":"not_configured"}}
EOF
mkdir -p "$workflow_smoke_dir/terrain-match"
cat >"$workflow_smoke_dir/terrain-match/rosbag2-cli-review.json" <<EOF
{
  "schema_version": "vision_nav_rosbag2_cli_review_v1",
  "status": "degraded",
  "artifact_path": "$workflow_smoke_dir/terrain-match/rosbag2-native",
  "validation_status": "passed",
  "validation_format": "vision_nav_rosbag2_v1",
  "validation_report": {
    "schema_version": "vision_nav_rosbag_export_validation_v1",
    "status": "passed",
    "format": "vision_nav_rosbag2_v1",
    "message_count": 2,
    "topic_count": 2
  },
  "ros2_cli": {
    "status": "skipped",
    "command": ["ros2", "bag", "info", "$workflow_smoke_dir/terrain-match/rosbag2-native"],
    "stdout": "",
    "stderr": "",
    "exit_code": null
  },
  "issues": [
    {
      "severity": "warning",
      "message": "ROS 2 CLI review was skipped."
    }
  ]
}
EOF
cat >"$workflow_smoke_dir/px4_sitl_capture_prereqs.json" <<'EOF'
{
  "schema_version": "vision_nav_px4_sitl_capture_prereqs_v1",
  "status": "failed",
  "checks": [
    {
      "name": "px4_autopilot_dir",
      "status": "failed",
      "message": "PX4-Autopilot checkout was not found."
    }
  ],
  "markers": {
    "__VISION_NAV_PX4_SITL_PREREQS__": "px4_sitl_capture_prereqs.json"
  }
}
EOF
mkdir -p "$workflow_smoke_dir/px4-sitl-session"
cat >"$workflow_smoke_dir/px4-sitl-session/px4_sitl_evidence_session.json" <<EOF
{
  "schema_version": "vision_nav_px4_sitl_evidence_session_v1",
  "session_dir": "$workflow_smoke_dir/px4-sitl-session",
  "receiver_report": "receiver_evidence.json",
  "message_type": "odometry",
  "rate_hz": 5.0
}
EOF
VISION_NAV_PYTHON=python3 \
VISION_NAV_EVIDENCE_WORKFLOW_DIR="$workflow_smoke_dir/workflow" \
VISION_NAV_EVIDENCE_WORKFLOW_REPORT="$workflow_smoke_dir/workflow/autonomy_evidence_workflow.json" \
VISION_NAV_FIELD_TEMPLATE="$workflow_smoke_dir/field_manifest.template.json" \
VISION_NAV_FIELD_MANIFEST="$workflow_smoke_dir/field_manifest.json" \
VISION_NAV_FIELD_COLLECTION_PLAN="$workflow_smoke_dir/replay-cases/field_collection_plan.json" \
VISION_NAV_FIELD_COLLECTION_PLAN_MD="$workflow_smoke_dir/replay-cases/field_collection_plan.md" \
VISION_NAV_FIELD_SITE_NAME=preflight-workflow \
VISION_NAV_FIELD_BUNDLE=preflight-bundle \
VISION_NAV_BUNDLE=preflight-bundle \
VISION_NAV_FIELD_LOG="$workflow_smoke_dir/terrain_matches.jsonl" \
VISION_NAV_ROSBAG_EXPORT_DIR="$workflow_smoke_dir/terrain-match/rosbag-jsonl" \
VISION_NAV_ROSBAG_EXPORT_VALIDATION="$workflow_smoke_dir/terrain-match/rosbag-jsonl-validation.json" \
VISION_NAV_ROSBAG2_CLI_REVIEW="$workflow_smoke_dir/terrain-match/rosbag2-cli-review.json" \
VISION_NAV_ROSBAG_INCLUDE_FRAME_TOPIC=0 \
VISION_NAV_FIELD_EVIDENCE_REPORT="$workflow_smoke_dir/replay-cases/field_evidence_report.json" \
VISION_NAV_FIELD_CASE_REPORT_DIR="$workflow_smoke_dir/replay-cases/field_evidence_cases" \
VISION_NAV_FEATURE_METHOD_BENCHMARK="$workflow_smoke_dir/feature-method-bench" \
VISION_NAV_THRESHOLD_TUNING_REPORT="$workflow_smoke_dir/replay-cases/threshold_tuning_report.json" \
VISION_NAV_THRESHOLD_CASE_REPORT_DIR="$workflow_smoke_dir/replay-cases/threshold_tuning_cases" \
VISION_NAV_SUPPORT_OUTPUT_DIR="$workflow_smoke_dir/support-bundles" \
VISION_NAV_AUTONOMY_READINESS_REPORT="$workflow_smoke_dir/replay-cases/autonomy_readiness_report.json" \
VISION_NAV_AUTONOMY_HANDOFF="$workflow_smoke_dir/replay-cases/autonomy_readiness_report.md" \
VISION_NAV_AUTONOMY_EVIDENCE_PACKAGE="$workflow_smoke_dir/replay-cases/autonomy_readiness_report.evidence.zip" \
VISION_NAV_PX4_SITL_SESSION="$workflow_smoke_dir/px4-sitl-session" \
VISION_NAV_PX4_SITL_REPORT="$workflow_smoke_dir/missing-receiver_evidence.json" \
VISION_NAV_PX4_SITL_PREREQS="$workflow_smoke_dir/px4_sitl_capture_prereqs.json" \
./scripts/pi/run_autonomy_evidence_workflow.sh >"$evidence_workflow_output" 2>&1
grep -q "__VISION_NAV_EVIDENCE_WORKFLOW_REPORT__=" "$evidence_workflow_output"
grep -q "__VISION_NAV_EVIDENCE_WORKFLOW_VALIDATION__=" "$evidence_workflow_output"
test -f "$workflow_smoke_dir/workflow/autonomy_evidence_workflow.json"
test -f "$workflow_smoke_dir/workflow/autonomy_evidence_workflow.validation.json"
test -f "$workflow_smoke_dir/replay-cases/field_collection_plan.json"
test -f "$workflow_smoke_dir/replay-cases/field_collection_plan.md"
python3 - "$workflow_smoke_dir/workflow/autonomy_evidence_workflow.json" <<'PY'
from __future__ import annotations

import json
import sys
import tarfile
from pathlib import Path

report = json.loads(Path(sys.argv[1]).read_text())
assert report["schema_version"] == "vision_nav_autonomy_evidence_workflow_v1"
steps = {step["name"]: step for step in report["steps"]}
assert "create_field_evidence_template" in steps
assert "create_field_collection_plan" in steps
assert "capture_field_terrain_log" in steps
assert steps["capture_field_terrain_log"]["status"] == "passed"
assert "parseable with" in steps["capture_field_terrain_log"]["notes"]
assert "Runtime status snapshot is usable" in steps["capture_field_terrain_log"]["notes"]
assert "validate_rosbag_export" in steps
assert "check_native_rosbag2_review" in steps
assert "check_px4_receiver_proof" in steps
assert "run_autonomy_readiness_audit" in steps
assert steps["check_native_rosbag2_review"]["status"] == "degraded"
assert steps["check_px4_receiver_proof"]["status"] == "skipped"
assert steps["run_autonomy_readiness_audit"]["status"] == "failed"
assert steps["run_autonomy_readiness_audit"]["readiness_report_status"] == "failed"
assert "__VISION_NAV_EVIDENCE_WORKFLOW_LOGS__" in report["markers"]
assert "__VISION_NAV_EVIDENCE_WORKFLOW_VALIDATION__" in report["markers"]
assert "__VISION_NAV_SUPPORT_ZIP__" in report["markers"]
assert "__VISION_NAV_FIELD_COLLECTION_PLAN__" in report["markers"]
assert "__VISION_NAV_FIELD_COLLECTION_PLAN_MD__" in report["markers"]
assert "__VISION_NAV_TERRAIN_LOG__" in report["markers"]
assert "__VISION_NAV_RUNTIME_STATUS__" in report["markers"]
assert "__VISION_NAV_ROSBAG_EXPORT_VALIDATION__" in report["markers"]
assert "__VISION_NAV_ROSBAG2_CLI_REVIEW__" in report["markers"]
assert "__VISION_NAV_PX4_SITL_PREREQS__" in report["markers"]
assert "__VISION_NAV_PX4_SITL_SESSION__" in report["markers"]
assert "__VISION_NAV_PX4_SITL_REPORT__" not in report["markers"]
assert report["status"] == "failed"
assert Path(report["markers"]["__VISION_NAV_ROSBAG_EXPORT_VALIDATION__"]).exists()
assert Path(report["markers"]["__VISION_NAV_ROSBAG2_CLI_REVIEW__"]).exists()
log_archive = Path(report["markers"]["__VISION_NAV_EVIDENCE_WORKFLOW_LOGS__"])
assert log_archive.exists()
with tarfile.open(log_archive, "r:gz") as archive:
    names = set(archive.getnames())
assert "logs/create_field_evidence_template.log" in names
assert "logs/create_field_collection_plan.log" in names
assert "logs/capture_field_terrain_log.log" in names
assert "logs/validate_rosbag_export.log" in names
assert "logs/check_native_rosbag2_review.log" in names
assert "logs/check_px4_receiver_proof.log" in names
assert "logs/run_autonomy_readiness_audit.log" in names
PY
PYTHONPATH=src python3 -m vision_nav.autonomy_evidence_workflow \
  --report "$workflow_smoke_dir/workflow/autonomy_evidence_workflow.json" \
  --output "$workflow_smoke_dir/workflow/autonomy_evidence_workflow.validation.json" \
  >"$evidence_workflow_validation_output"
grep -q "__VISION_NAV_EVIDENCE_WORKFLOW_VALIDATION__=" "$evidence_workflow_validation_output"
python3 - "$workflow_smoke_dir/workflow/autonomy_evidence_workflow.validation.json" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

validation = json.loads(Path(sys.argv[1]).read_text())
assert validation["schema_version"] == "vision_nav_autonomy_evidence_workflow_validation_v1"
assert validation["status"] in {"passed", "degraded"}
checks = {check["name"]: check["status"] for check in validation["checks"]}
details = {check["name"]: check.get("details") or {} for check in validation["checks"]}
assert checks["log_archive"] == "passed"
assert checks["required_step_results"] == "degraded"
assert details["required_step_results"]["non_passed_count"] > 0
PY
invalid_workflow_dir="$field_smoke_dir/workflow-invalid-log"
mkdir -p "$invalid_workflow_dir"
cat >"$invalid_workflow_dir/terrain_matches.jsonl" <<'EOF'
{"sequence": 1, "result": {"status": "unknown"}}
EOF
VISION_NAV_PYTHON=python3 \
VISION_NAV_EVIDENCE_WORKFLOW_DIR="$invalid_workflow_dir/workflow" \
VISION_NAV_EVIDENCE_WORKFLOW_REPORT="$invalid_workflow_dir/workflow/autonomy_evidence_workflow.json" \
VISION_NAV_FIELD_TEMPLATE="$invalid_workflow_dir/field_manifest.template.json" \
VISION_NAV_FIELD_MANIFEST="$invalid_workflow_dir/field_manifest.json" \
VISION_NAV_FIELD_COLLECTION_PLAN="$invalid_workflow_dir/replay-cases/field_collection_plan.json" \
VISION_NAV_FIELD_COLLECTION_PLAN_MD="$invalid_workflow_dir/replay-cases/field_collection_plan.md" \
VISION_NAV_FIELD_SITE_NAME=preflight-invalid-log \
VISION_NAV_FIELD_BUNDLE=preflight-bundle \
VISION_NAV_BUNDLE="$invalid_workflow_dir/missing-bundle" \
VISION_NAV_FIELD_LOG="$invalid_workflow_dir/terrain_matches.jsonl" \
VISION_NAV_ROSBAG_EXPORT_DIR="$invalid_workflow_dir/terrain-match/rosbag-jsonl" \
VISION_NAV_ROSBAG_EXPORT_VALIDATION="$invalid_workflow_dir/terrain-match/rosbag-jsonl-validation.json" \
VISION_NAV_ROSBAG2_CLI_REVIEW="$invalid_workflow_dir/terrain-match/rosbag2-cli-review.json" \
VISION_NAV_FIELD_EVIDENCE_REPORT="$invalid_workflow_dir/replay-cases/field_evidence_report.json" \
VISION_NAV_FIELD_CASE_REPORT_DIR="$invalid_workflow_dir/replay-cases/field_evidence_cases" \
VISION_NAV_FEATURE_METHOD_BENCHMARK="$invalid_workflow_dir/feature-method-bench" \
VISION_NAV_THRESHOLD_TUNING_REPORT="$invalid_workflow_dir/replay-cases/threshold_tuning_report.json" \
VISION_NAV_THRESHOLD_CASE_REPORT_DIR="$invalid_workflow_dir/replay-cases/threshold_tuning_cases" \
VISION_NAV_SUPPORT_OUTPUT_DIR="$invalid_workflow_dir/support-bundles" \
VISION_NAV_AUTONOMY_READINESS_REPORT="$invalid_workflow_dir/replay-cases/autonomy_readiness_report.json" \
VISION_NAV_AUTONOMY_HANDOFF="$invalid_workflow_dir/replay-cases/autonomy_readiness_report.md" \
VISION_NAV_AUTONOMY_EVIDENCE_PACKAGE="$invalid_workflow_dir/replay-cases/autonomy_readiness_report.evidence.zip" \
VISION_NAV_PX4_SITL_SESSION="$invalid_workflow_dir/px4-sitl-session" \
VISION_NAV_PX4_SITL_REPORT="$invalid_workflow_dir/px4-sitl-session/receiver_evidence.json" \
VISION_NAV_PX4_SITL_PREREQS="$invalid_workflow_dir/px4-sitl-session/px4_sitl_capture_prereqs.json" \
./scripts/pi/run_autonomy_evidence_workflow.sh >"$invalid_evidence_workflow_output" 2>&1
python3 - "$invalid_workflow_dir/workflow/autonomy_evidence_workflow.json" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

report = json.loads(Path(sys.argv[1]).read_text())
steps = {step["name"]: step for step in report["steps"]}
assert steps["capture_field_terrain_log"]["status"] == "failed"
assert "no accepted/rejected/degraded" in steps["capture_field_terrain_log"]["notes"]
assert steps["register_field_replay_case"]["status"] == "skipped"
assert "not validated" in steps["register_field_replay_case"]["notes"]
assert steps["run_feature_method_benchmark"]["status"] == "skipped"
assert "not validated" in steps["run_feature_method_benchmark"]["notes"]
assert steps["validate_rosbag_export"]["status"] == "skipped"
assert "not validated" in steps["validate_rosbag_export"]["notes"]
assert report["status"] == "failed"
PY
bad_status_workflow_dir="$field_smoke_dir/workflow-bad-status"
mkdir -p "$bad_status_workflow_dir"
cat >"$bad_status_workflow_dir/terrain_matches.jsonl" <<'EOF'
{"sequence": 1, "result": {"status": "accepted", "timestamp_us": 1000000, "measurement": {"frame": "local_enu", "x_m": 1.0, "y_m": 2.0, "covariance": {"x_m2": 1.0, "y_m2": 1.0}}}}
EOF
cat >"$bad_status_workflow_dir/runtime_status.json" <<'EOF'
{"schema_version":"vision_nav_runtime_status_v1","active_map":{"bundle_id":"preflight"},"last_match":{"status":"accepted"}}
EOF
VISION_NAV_PYTHON=python3 \
VISION_NAV_EVIDENCE_WORKFLOW_DIR="$bad_status_workflow_dir/workflow" \
VISION_NAV_EVIDENCE_WORKFLOW_REPORT="$bad_status_workflow_dir/workflow/autonomy_evidence_workflow.json" \
VISION_NAV_FIELD_TEMPLATE="$bad_status_workflow_dir/field_manifest.template.json" \
VISION_NAV_FIELD_MANIFEST="$bad_status_workflow_dir/field_manifest.json" \
VISION_NAV_FIELD_COLLECTION_PLAN="$bad_status_workflow_dir/replay-cases/field_collection_plan.json" \
VISION_NAV_FIELD_COLLECTION_PLAN_MD="$bad_status_workflow_dir/replay-cases/field_collection_plan.md" \
VISION_NAV_FIELD_SITE_NAME=preflight-bad-status \
VISION_NAV_FIELD_BUNDLE=preflight-bundle \
VISION_NAV_BUNDLE="$bad_status_workflow_dir/missing-bundle" \
VISION_NAV_FIELD_LOG="$bad_status_workflow_dir/terrain_matches.jsonl" \
VISION_NAV_ROSBAG_EXPORT_DIR="$bad_status_workflow_dir/terrain-match/rosbag-jsonl" \
VISION_NAV_ROSBAG_EXPORT_VALIDATION="$bad_status_workflow_dir/terrain-match/rosbag-jsonl-validation.json" \
VISION_NAV_ROSBAG2_CLI_REVIEW="$bad_status_workflow_dir/terrain-match/rosbag2-cli-review.json" \
VISION_NAV_FIELD_EVIDENCE_REPORT="$bad_status_workflow_dir/replay-cases/field_evidence_report.json" \
VISION_NAV_FIELD_CASE_REPORT_DIR="$bad_status_workflow_dir/replay-cases/field_evidence_cases" \
VISION_NAV_FEATURE_METHOD_BENCHMARK="$bad_status_workflow_dir/feature-method-bench" \
VISION_NAV_THRESHOLD_TUNING_REPORT="$bad_status_workflow_dir/replay-cases/threshold_tuning_report.json" \
VISION_NAV_THRESHOLD_CASE_REPORT_DIR="$bad_status_workflow_dir/replay-cases/threshold_tuning_cases" \
VISION_NAV_SUPPORT_OUTPUT_DIR="$bad_status_workflow_dir/support-bundles" \
VISION_NAV_AUTONOMY_READINESS_REPORT="$bad_status_workflow_dir/replay-cases/autonomy_readiness_report.json" \
VISION_NAV_AUTONOMY_HANDOFF="$bad_status_workflow_dir/replay-cases/autonomy_readiness_report.md" \
VISION_NAV_AUTONOMY_EVIDENCE_PACKAGE="$bad_status_workflow_dir/replay-cases/autonomy_readiness_report.evidence.zip" \
VISION_NAV_PX4_SITL_SESSION="$bad_status_workflow_dir/px4-sitl-session" \
VISION_NAV_PX4_SITL_REPORT="$bad_status_workflow_dir/px4-sitl-session/receiver_evidence.json" \
VISION_NAV_PX4_SITL_PREREQS="$bad_status_workflow_dir/px4-sitl-session/px4_sitl_capture_prereqs.json" \
./scripts/pi/run_autonomy_evidence_workflow.sh >"$bad_status_evidence_workflow_output" 2>&1
python3 - "$bad_status_workflow_dir/workflow/autonomy_evidence_workflow.json" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

report = json.loads(Path(sys.argv[1]).read_text())
steps = {step["name"]: step for step in report["steps"]}
assert steps["capture_field_terrain_log"]["status"] == "degraded"
assert "missing output/log path metadata" in steps["capture_field_terrain_log"]["notes"]
assert steps["validate_rosbag_export"]["status"] == "passed"
assert "__VISION_NAV_ROSBAG_EXPORT_VALIDATION__" in report["markers"]
assert "__VISION_NAV_RUNTIME_STATUS__" in report["markers"]
assert report["status"] == "failed"
PY
support_autodetect_home="$workflow_smoke_dir/support-autodetect-home"
support_autodetect_dir="$workflow_smoke_dir/support-autodetect-bundles"
mkdir -p "$support_autodetect_home/px4-sitl-evidence"
mkdir -p "$support_autodetect_home/DroneTransfer/outgoing/replay-cases/logs"
cat >"$support_autodetect_home/px4-sitl-evidence/receiver_evidence.json" <<'EOF'
{
  "status": "passed",
  "expected_message": "odometry",
  "listener": {"sample_count": 2, "observed_rate_hz": 5.0},
  "config": {"expected_rate_hz": 5.0},
  "issues": []
}
EOF
cat >"$support_autodetect_home/px4-sitl-evidence/px4_sitl_capture_prereqs.json" <<'EOF'
{
  "schema_version": "vision_nav_px4_sitl_capture_prereqs_v1",
  "status": "failed",
  "session_dir": "px4-sitl-evidence",
  "px4_dir": "/missing/PX4-Autopilot",
  "px4_target": "px4_sitl gz_x500",
  "tmux_session": "vision-nav-px4-sitl",
  "receiver_report": "receiver_evidence.json",
  "checks": [
    {"name": "tmux_installed", "status": "passed", "message": "tmux is installed."},
    {"name": "px4_autopilot_dir", "status": "failed", "message": "PX4-Autopilot directory not found."}
  ],
  "next_actions": ["Set VISION_NAV_PX4_AUTOPILOT_DIR."],
  "fix_commands": [
    {
      "label": "Point the harness at an existing PX4 checkout",
      "command": "export VISION_NAV_PX4_AUTOPILOT_DIR=/path/to/PX4-Autopilot",
      "condition": "px4_autopilot_dir"
    }
  ]
}
EOF
cat >"$support_autodetect_home/DroneTransfer/outgoing/replay-cases/field_manifest.json" <<'EOF'
{
  "version": "0.1.0",
  "description": "Local support-bundle auto-detect smoke manifest.",
  "cases": [
    {
      "case_name": "field-good-texture-smoke",
      "expected": "good_map",
      "dataset_type": "field",
      "conditions": ["good_texture"],
      "bundle": "smoke-bundle",
      "log": "logs/field-good-texture-smoke.jsonl",
      "notes": "Synthetic local-preflight log used only to prove default field manifest auto-detection."
    }
  ]
}
EOF
cat >"$support_autodetect_home/DroneTransfer/outgoing/replay-cases/logs/field-good-texture-smoke.jsonl" <<'EOF'
{"sequence":1,"timestamp_us":1000000,"result":{"status":"accepted","confidence":0.82,"inliers":34,"reprojection_error_px":1.6,"scale_confidence":0.74,"local_enu_m":{"x":0.0,"y":0.0,"z":null},"covariance":{"x_m2":4.0,"y_m2":4.0,"z_m2":null,"yaw_rad2":null}}}
{"sequence":2,"timestamp_us":2000000,"result":{"status":"accepted","confidence":0.84,"inliers":36,"reprojection_error_px":1.4,"scale_confidence":0.76,"local_enu_m":{"x":2.0,"y":1.0,"z":null},"covariance":{"x_m2":4.0,"y_m2":4.0,"z_m2":null,"yaw_rad2":null}}}
{"sequence":3,"timestamp_us":3000000,"result":{"status":"accepted","confidence":0.8,"inliers":31,"reprojection_error_px":1.9,"scale_confidence":0.71,"local_enu_m":{"x":4.0,"y":2.0,"z":null},"covariance":{"x_m2":5.0,"y_m2":5.0,"z_m2":null,"yaw_rad2":null}}}
EOF
cat >"$support_autodetect_home/px4.params" <<'EOF'
1 1 EKF2_EV_CTRL 1 6
1 1 EKF2_HGT_REF 0 6
1 1 EKF2_GPS_CTRL 7 6
1 1 EKF2_EV_NOISE_MD 0 6
1 1 EKF2_EV_DELAY 80 9
1 1 EKF2_EV_POS_X 0.0 9
1 1 EKF2_EV_POS_Y 0.0 9
1 1 EKF2_EV_POS_Z 0.0 9
EOF
cat >"$support_autodetect_home/ardupilot.params" <<'EOF'
EK3_ENABLE,1
EK2_ENABLE,0
AHRS_EKF_TYPE,3
VISO_TYPE,3
VISO_POS_X,0.02
VISO_POS_Y,0.01
VISO_POS_Z,-0.04
EK3_SRC1_POSXY,6
EK3_SRC1_VELXY,0
EK3_SRC1_POSZ,1
EK3_SRC1_VELZ,0
EK3_SRC1_YAW,1
EK3_SRC_OPTIONS,0
GPS_TYPE,0
RC8_OPTION,90
EOF
env -u VISION_NAV_PX4_SITL_SESSION -u VISION_NAV_PX4_SITL_PREREQS -u VISION_NAV_PX4_SITL_REPORT -u VISION_NAV_PX4_PARAMS -u VISION_NAV_ARDUPILOT_PARAMS -u VISION_NAV_REPLAY_CASE_MANIFEST \
HOME="$support_autodetect_home" \
VISION_NAV_PYTHON=python3 \
VISION_NAV_SUPPORT_OUTPUT_DIR="$support_autodetect_dir" \
./scripts/pi/create_support_bundle.sh >"$support_autodetect_output" 2>&1
grep -q "__VISION_NAV_SUPPORT_ZIP__=" "$support_autodetect_output"
python3 - "$support_autodetect_dir" <<'PY'
from __future__ import annotations

import json
import sys
import zipfile
from pathlib import Path

support_dir = Path(sys.argv[1])
zips = sorted(support_dir.glob("*.zip"))
assert len(zips) == 1
manifest = json.loads((support_dir / zips[0].stem / "support_manifest.json").read_text())
assert manifest["px4_sitl_evidence"]["status"] == "passed"
assert manifest["px4_sitl_evidence"]["source"] == "px4_sitl_report"
assert manifest["px4_sitl_evidence"]["source_report_path"].endswith("px4-sitl-evidence/receiver_evidence.json")
assert manifest["px4_sitl_prereqs"]["status"] == "failed"
assert manifest["px4_sitl_prereqs"]["source_path"].endswith("px4-sitl-evidence/px4_sitl_capture_prereqs.json")
assert manifest["px4_sitl_prereqs"]["fix_commands"][0]["condition"] == "px4_autopilot_dir"
assert manifest["px4_params"]["status"] in {"passed", "degraded"}
assert manifest["px4_params"]["param_copy"]["source"].endswith("px4.params")
assert manifest["ardupilot_params"]["status"] == "passed"
assert manifest["ardupilot_params"]["param_copy"]["source"].endswith("ardupilot.params")
assert manifest["replay_gates"]["status"] == "passed"
assert manifest["replay_gates"]["case_count"] == 1
assert manifest["replay_gates"]["sources"][0].endswith("DroneTransfer/outgoing/replay-cases/field_manifest.json")
with zipfile.ZipFile(zips[0]) as archive:
    names = set(archive.namelist())
assert "summaries/px4_sitl_evidence/receiver_evidence.json" in names
assert "summaries/px4_sitl_prereqs/px4_sitl_capture_prereqs.json" in names
assert "extras/px4_sitl_evidence/receiver_evidence.json" in names
assert "extras/px4_sitl_prereqs/px4_sitl_capture_prereqs.json" in names
assert "extras/px4_params/px4.params" in names
assert "extras/ardupilot_params/ardupilot.params" in names
assert "summaries/replay_gates/field-good-texture-smoke.gate.json" in names
PY
rosbag_smoke_dir="$workflow_smoke_dir/rosbag-smoke"
mkdir -p "$rosbag_smoke_dir"
cat >"$rosbag_smoke_dir/terrain_matches.jsonl" <<'EOF'
{"sequence": 1, "result": {"status": "accepted", "timestamp_us": 1000000, "measurement": {"frame": "local_enu", "x_m": 1.0, "y_m": 2.0, "covariance": {"x_m2": 1.0, "y_m2": 1.0}}}}
EOF
VISION_NAV_PYTHON=python3 \
VISION_NAV_ROSBAG_SOURCE_LOG="$rosbag_smoke_dir/terrain_matches.jsonl" \
VISION_NAV_ROSBAG_EXPORT_DIR="$rosbag_smoke_dir/rosbag-jsonl" \
VISION_NAV_ROSBAG_EXPORT_VALIDATION="$rosbag_smoke_dir/rosbag-jsonl-validation.json" \
VISION_NAV_ROSBAG_INCLUDE_FRAME_TOPIC=0 \
./scripts/pi/run_rosbag_export_validation.sh >"$rosbag_validation_output" 2>&1
grep -q "__VISION_NAV_ROSBAG_EXPORT_VALIDATION__=" "$rosbag_validation_output"
VISION_NAV_ROSBAG2_DRY_RUN=1 \
VISION_NAV_ROSBAG_SOURCE_LOG="$rosbag_smoke_dir/terrain_matches.jsonl" \
VISION_NAV_ROSBAG2_EXPORT_DIR="$rosbag_smoke_dir/rosbag2-native" \
VISION_NAV_ROSBAG2_CLI_REVIEW="$rosbag_smoke_dir/rosbag2-cli-review.json" \
./scripts/dev/run_rosbag2_cli_review.sh >"$rosbag2_review_output" 2>&1
grep -q "__VISION_NAV_ROSBAG2_EXPORT_DIR__=" "$rosbag2_review_output"
grep -q "__VISION_NAV_ROSBAG2_CLI_REVIEW__=" "$rosbag2_review_output"
python3 - "$rosbag_smoke_dir/rosbag-jsonl-validation.json" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

report = json.loads(Path(sys.argv[1]).read_text())
assert report["schema_version"] == "vision_nav_rosbag_export_validation_v1"
assert report["status"] == "passed"
topics = {topic["name"]: topic["message_count"] for topic in report["topics"]}
assert topics["/vision_nav/odometry"] == 1
assert topics["/diagnostics"] == 1
PY
rm -rf "$field_smoke_dir"

echo "[6/8] Checking autonomy-readiness fail-closed behavior"
autonomy_readiness_output="$preflight_tmp_dir/autonomy_readiness_preflight.txt"
if PYTHONPATH=src python3 -m vision_nav.autonomy_readiness --json >"$autonomy_readiness_output"; then
  echo "Expected autonomy readiness to fail before support bundle, PX4, field, feature, and threshold evidence exist." >&2
  exit 1
fi
tail -n 18 "$autonomy_readiness_output"
goal_status_output="$preflight_tmp_dir/autonomy_goal_status_preflight.txt"
if VISION_NAV_SKIP_CONVENTIONAL_PX4_SITL=1 ./scripts/dev/autonomy_goal_status.sh >"$goal_status_output" 2>&1; then
  echo "Expected autonomy goal status to fail before final proof evidence exists." >&2
  exit 1
fi
grep -q "Autonomy goal status: failed" "$goal_status_output"
grep -q "Proof phases:" "$goal_status_output"
grep -q "method_thresholds \\[blocked\\]" "$goal_status_output"
grep -q "ros2_replay \\[blocked\\]" "$goal_status_output"
grep -q "waiting on: field_dataset=action_required" "$goal_status_output"
grep -q "External proof blockers:" "$goal_status_output"
grep -q "Bench evidence preview:" "$goal_status_output"
grep -q "runtime terrain log and runtime_status.json snapshot" "$goal_status_output"
grep -q "PX4 ODOMETRY receiver evidence report" "$goal_status_output"
grep -q "suggested collection order:" "$goal_status_output"
grep -q "VISION_NAV_COUNT=30 ./scripts/pi/run_terrain_nav_loop.sh" "$goal_status_output"
grep -q "Module Setup > PX4 Prereq Setup" "$goal_status_output"
grep -q "Module Setup > PX4 SITL Receiver Capture" "$goal_status_output"
grep -q "Field collection preview:" "$goal_status_output"
grep -q "Good texture, matching map (good_texture), expected good_map" "$goal_status_output"
grep -q "Wrong-map rejection (wrong_map), expected wrong_map" "$goal_status_output"
grep -q "Guided workflow option:" "$goal_status_output"
grep -q "./scripts/pi/create_field_evidence_template.sh && ./scripts/pi/create_field_collection_plan.sh" "$goal_status_output"
grep -q "Module Setup > Load Next Field Condition" "$goal_status_output"
grep -q "./scripts/pi/run_autonomy_evidence_workflow.sh" "$goal_status_output"
grep -q "Next commands:" "$goal_status_output"
grep -q "Blocked follow-up commands:" "$goal_status_output"
grep -q "support_bundle_bench_readiness" "$goal_status_output"
grep -q "./scripts/pi/run_rosbag_export_validation.sh" "$goal_status_output"
grep -q "./scripts/dev/run_rosbag2_cli_review.sh" "$goal_status_output"
python3 - "$goal_status_output" <<'PY'
import sys
text = open(sys.argv[1], encoding="utf-8").read()
bench_order = text.index("suggested collection order:")
px4_prereq_app = text.index("Module Setup > PX4 Prereq Setup", bench_order)
px4_capture_app = text.index("Module Setup > PX4 SITL Receiver Capture", bench_order)
create_plan_app = text.index("Module Setup > Create Plan", bench_order)
load_next_app = text.index("Module Setup > Load Next Field Condition", bench_order)
evidence_workflow_app = text.index("Module Setup > Evidence Workflow", bench_order)
assert px4_prereq_app < px4_capture_app
assert create_plan_app < load_next_app < evidence_workflow_app
next_commands = text.index("Next commands:")
px4 = text.index("VISION_NAV_SITL_SMOKE_DIR=$PWD/px4-sitl-evidence ./scripts/dev/run_px4_sitl_external_vision_capture.sh", next_commands)
support = text.index("./scripts/pi/create_support_bundle.sh", next_commands)
field_plan = text.index("./scripts/pi/create_field_evidence_template.sh && ./scripts/pi/create_field_collection_plan.sh", next_commands)
workflow = text.index("./scripts/pi/run_autonomy_evidence_workflow.sh", next_commands)
assert px4 < support
assert support < field_plan < workflow
blocked_followups = text.index("Blocked follow-up commands:")
assert "./scripts/pi/run_rosbag_export_validation.sh" not in text[next_commands:blocked_followups]
assert "./scripts/pi/run_rosbag_export_validation.sh" in text[blocked_followups:]
assert "./scripts/dev/run_rosbag2_cli_review.sh" in text[blocked_followups:]
PY
local_autonomy_output="$preflight_tmp_dir/local_autonomy_readiness_preflight.txt"
local_audit_dir="$(mktemp -d "$preflight_tmp_dir/local-audit.XXXXXX")"
mkdir -p "$local_audit_dir/feature-method-bench"
mkdir -p "$local_audit_dir/px4-sitl-evidence"
mkdir -p "$local_audit_dir/replay-cases"
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
cat >"$local_audit_dir/px4-sitl-evidence/px4_sitl_capture_prereqs.json" <<'EOF'
{
  "schema_version": "vision_nav_px4_sitl_capture_prereqs_v1",
  "status": "failed",
  "session_dir": "preflight-px4-sitl-evidence",
  "px4_dir": "preflight-missing-px4",
  "px4_target": "px4_sitl gz_x500",
  "tmux_session": "vision-nav-px4-sitl",
  "receiver_report": "receiver_evidence.json",
  "checks": [
    {"name": "tmux_installed", "status": "passed", "message": "tmux is installed."},
    {"name": "px4_autopilot_dir", "status": "failed", "message": "PX4-Autopilot directory not found."}
  ],
  "next_actions": ["PX4-Autopilot directory not found."],
  "fix_commands": [
    {
      "label": "Point the harness at an existing PX4 checkout",
      "command": "export VISION_NAV_PX4_AUTOPILOT_DIR=/path/to/PX4-Autopilot",
      "condition": "px4_autopilot_dir"
    }
  ]
}
EOF
cat >"$local_audit_dir/replay-cases/field_collection_plan.json" <<'EOF'
{
  "schema_version": "vision_nav_field_collection_plan_v1",
  "status": "degraded",
  "summary": {
    "required_count": 8,
    "registered_count": 0,
    "registered_missing_log_count": 0,
    "placeholder_count": 8,
    "missing_count": 0
  },
  "conditions": []
}
EOF
cat >"$local_audit_dir/replay-cases/field_collection_plan.md" <<'EOF'
# Field Evidence Collection Plan
EOF
scanned_goal_status_output="$preflight_tmp_dir/autonomy_goal_status_scanned_preflight.txt"
if VISION_NAV_LOCAL_SUPPORT_DIR="$local_audit_dir/support-bundles" \
VISION_NAV_LOCAL_REPLAY_DIR="$local_audit_dir/replay-cases" \
VISION_NAV_LOCAL_FEATURE_BENCH_DIR="$local_audit_dir/feature-method-bench" \
VISION_NAV_PX4_SITL_REPORT="$local_audit_dir/px4-sitl-evidence/receiver_evidence.json" \
VISION_NAV_SKIP_CONVENTIONAL_PX4_SITL=1 \
VISION_NAV_AUTONOMY_GOAL_STATUS_QUIET_EXIT=1 \
./scripts/dev/autonomy_goal_status.sh >"$scanned_goal_status_output" 2>&1; then
  echo "Expected scanned autonomy goal status to fail before final proof evidence exists." >&2
  exit 1
fi
grep -q "Evidence inputs:" "$scanned_goal_status_output"
grep -q "feature_method_benchmark_report" "$scanned_goal_status_output"
grep -q "field_collection_plan" "$scanned_goal_status_output"
grep -q "px4_sitl_report" "$scanned_goal_status_output"
grep -q "px4_sitl_prereqs" "$scanned_goal_status_output"
grep -q "Diagnostics:" "$scanned_goal_status_output"
grep -q "px4_autopilot_dir" "$scanned_goal_status_output"
grep -q "PX4-Autopilot directory not found." "$scanned_goal_status_output"
grep -q "fix command (Point the harness at an existing PX4 checkout)" "$scanned_goal_status_output"
grep -q "Immediate prerequisite fixes:" "$scanned_goal_status_output"
grep -q "export VISION_NAV_PX4_AUTOPILOT_DIR=/path/to/PX4-Autopilot" "$scanned_goal_status_output"
grep -q "Field collection preview:" "$scanned_goal_status_output"
grep -q "Good texture, matching map (good_texture), expected good_map" "$scanned_goal_status_output"
grep -q "Bench evidence preview:" "$scanned_goal_status_output"
grep -q "field evidence report covering all required real-world conditions" "$scanned_goal_status_output"
grep -q "suggested collection order:" "$scanned_goal_status_output"
grep -q "Module Setup > PX4 Prereq Setup" "$scanned_goal_status_output"
grep -q "Module Setup > Load Next Field Condition" "$scanned_goal_status_output"
grep -q "Module Setup > Bench Report" "$scanned_goal_status_output"
grep -q "./scripts/pi/create_field_evidence_template.sh && ./scripts/pi/create_field_collection_plan.sh" "$scanned_goal_status_output"
python3 - "$scanned_goal_status_output" <<'PY'
from pathlib import Path
import sys

text = Path(sys.argv[1]).read_text()
fixes = text.index("Immediate prerequisite fixes:")
guided = text.index("Guided workflow option:")
bench_order = text.index("suggested collection order:")
px4_prereq_app = text.index("Module Setup > PX4 Prereq Setup", bench_order)
px4_capture_app = text.index("Module Setup > PX4 SITL Receiver Capture", bench_order)
create_plan_app = text.index("Module Setup > Create Plan", bench_order)
load_next_app = text.index("Module Setup > Load Next Field Condition", bench_order)
evidence_workflow_app = text.index("Module Setup > Evidence Workflow", bench_order)
next_commands = text.index("Next commands:")
field_plan = text.index("./scripts/pi/create_field_evidence_template.sh && ./scripts/pi/create_field_collection_plan.sh", next_commands)
workflow = text.index("./scripts/pi/run_autonomy_evidence_workflow.sh", next_commands)
assert fixes < guided < next_commands
assert px4_prereq_app < px4_capture_app
assert create_plan_app < load_next_app < evidence_workflow_app
assert field_plan < workflow
PY
VISION_NAV_LOCAL_SUPPORT_DIR="$local_audit_dir/support-bundles" \
VISION_NAV_LOCAL_REPLAY_DIR="$local_audit_dir/replay-cases" \
VISION_NAV_LOCAL_FEATURE_BENCH_DIR="$local_audit_dir/feature-method-bench" \
VISION_NAV_PX4_SITL_REPORT="$local_audit_dir/px4-sitl-evidence/receiver_evidence.json" \
VISION_NAV_SKIP_CONVENTIONAL_PX4_SITL=1 \
VISION_NAV_AUTONOMY_ALLOW_FAILED=1 \
./scripts/dev/run_local_autonomy_readiness_audit.sh >"$local_autonomy_output" 2>&1
grep -q "No support bundle ZIP found" "$local_autonomy_output"
grep -q "__VISION_NAV_AUTONOMY_REPORT__=" "$local_autonomy_output"
grep -q "__VISION_NAV_AUTONOMY_HANDOFF__=" "$local_autonomy_output"
grep -q "__VISION_NAV_AUTONOMY_EVIDENCE_PACKAGE__=" "$local_autonomy_output"
grep -q "__VISION_NAV_PX4_SITL_REPORT__=" "$local_autonomy_output"
grep -q "__VISION_NAV_PX4_SITL_PREREQS__=" "$local_autonomy_output"
grep -q "__VISION_NAV_FIELD_COLLECTION_PLAN__=" "$local_autonomy_output"
grep -q "__VISION_NAV_FIELD_COLLECTION_PLAN_MD__=" "$local_autonomy_output"
grep -q "proof:support_bundle_bench_readiness" "$local_autonomy_output"
test -f "$local_audit_dir/replay-cases/autonomy_readiness_report.json"
test -f "$local_audit_dir/replay-cases/autonomy_readiness_report.md"
test -f "$local_audit_dir/replay-cases/autonomy_readiness_report.evidence.zip"
grep -q "preflight_feature_benchmark.json" "$local_audit_dir/replay-cases/autonomy_readiness_report.json"
grep -q "receiver_evidence.json" "$local_audit_dir/replay-cases/autonomy_readiness_report.json"
grep -q "px4_sitl_capture_prereqs.json" "$local_audit_dir/replay-cases/autonomy_readiness_report.json"
grep -q "PX4 Capture Prerequisites" "$local_audit_dir/replay-cases/autonomy_readiness_report.md"
grep -q '"next_actions"' "$local_audit_dir/replay-cases/autonomy_readiness_report.json"
grep -q '"fix_commands"' "$local_audit_dir/replay-cases/autonomy_readiness_report.json"
grep -q '"prerequisite_fix_commands"' "$local_audit_dir/replay-cases/autonomy_readiness_report.json"
grep -q "Prerequisite fix commands" "$local_audit_dir/replay-cases/autonomy_readiness_report.md"
grep -q "Autonomy Readiness Handoff" "$local_audit_dir/replay-cases/autonomy_readiness_report.md"
python3 - "$local_audit_dir/replay-cases/autonomy_readiness_report.evidence.zip" <<'PY'
import sys, zipfile
with zipfile.ZipFile(sys.argv[1]) as archive:
    names = set(archive.namelist())
    assert "manifest.json" in names
    assert "reports/autonomy_readiness_report.json" in names
    assert "reports/autonomy_readiness_report.md" in names
    assert any(name.startswith("artifacts/input_px4_sitl_prereqs-") for name in names)
    assert any(name.startswith("artifacts/input_field_collection_plan-") for name in names)
    assert any(name.startswith("artifacts/input_field_collection_plan_markdown-") for name in names)
PY
pi_audit_dir="$(mktemp -d "$preflight_tmp_dir/pi-audit.XXXXXX")"
mkdir -p "$pi_audit_dir/support-bundles" "$pi_audit_dir/feature-method-bench" "$pi_audit_dir/replay-cases" "$pi_audit_dir/terrain-match"
pi_autonomy_output="$preflight_tmp_dir/pi_autonomy_readiness_preflight.txt"
VISION_NAV_PYTHON=python3 \
VISION_NAV_SUPPORT_OUTPUT_DIR="$pi_audit_dir/support-bundles" \
VISION_NAV_FEATURE_METHOD_BENCHMARK="$pi_audit_dir/feature-method-bench" \
VISION_NAV_FIELD_EVIDENCE_REPORT="$pi_audit_dir/replay-cases/field_evidence_report.json" \
VISION_NAV_FIELD_COLLECTION_PLAN="$pi_audit_dir/replay-cases/field_collection_plan.json" \
VISION_NAV_THRESHOLD_TUNING_REPORT="$pi_audit_dir/replay-cases/threshold_tuning_report.json" \
VISION_NAV_ROSBAG_EXPORT_VALIDATION="$pi_audit_dir/terrain-match/rosbag-jsonl-validation.json" \
VISION_NAV_ROSBAG2_CLI_REVIEW="$pi_audit_dir/terrain-match/rosbag2-cli-review.json" \
VISION_NAV_EVIDENCE_WORKFLOW_REPORT="$pi_audit_dir/replay-cases/autonomy-evidence-workflow/autonomy_evidence_workflow.json" \
VISION_NAV_AUTONOMY_READINESS_REPORT="$pi_audit_dir/replay-cases/autonomy_readiness_report.json" \
VISION_NAV_AUTONOMY_ALLOW_FAILED=1 \
./scripts/pi/run_autonomy_readiness_audit.sh >"$pi_autonomy_output" 2>&1
grep -q "No support bundle ZIP found" "$pi_autonomy_output"
grep -q "__VISION_NAV_AUTONOMY_REPORT__=" "$pi_autonomy_output"
grep -q "__VISION_NAV_AUTONOMY_HANDOFF__=" "$pi_autonomy_output"
grep -q "__VISION_NAV_AUTONOMY_EVIDENCE_PACKAGE__=" "$pi_autonomy_output"
grep -q "proof:support_bundle_bench_readiness" "$pi_autonomy_output"
test -f "$pi_audit_dir/replay-cases/autonomy_readiness_report.json"
test -f "$pi_audit_dir/replay-cases/autonomy_readiness_report.md"
test -f "$pi_audit_dir/replay-cases/autonomy_readiness_report.evidence.zip"
grep -q '"support_bundle_bench_readiness"' "$pi_audit_dir/replay-cases/autonomy_readiness_report.json"
rm -rf "$pi_audit_dir"
rm -rf "$local_audit_dir"

echo "[7/8] Preparing PX4 SITL evidence session dry-run"
smoke_output="$preflight_tmp_dir/px4_sitl_smoke_dry_run.txt"
capture_output="$preflight_tmp_dir/px4_sitl_capture_dry_run.txt"
capture_prereq_output="$preflight_tmp_dir/px4_sitl_capture_missing_prereq.txt"
px4_prereq_setup_output="$preflight_tmp_dir/px4_sitl_prereq_setup_dry_run.txt"
session_missing_capture_output="$preflight_tmp_dir/px4_sitl_session_missing_capture.txt"
smoke_dir="$(mktemp -d "$preflight_tmp_dir/sitl-smoke.XXXXXX")"
VISION_NAV_PX4_AUTOPILOT_DIR="$preflight_tmp_dir/missing-px4-autopilot" \
./scripts/dev/setup_px4_sitl_prereqs.sh --clone-px4 >"$px4_prereq_setup_output"
grep -q "PX4 SITL prerequisite setup dry run" "$px4_prereq_setup_output"
grep -q "git clone https://github.com/PX4/PX4-Autopilot.git" "$px4_prereq_setup_output"
grep -q "Dry run only" "$px4_prereq_setup_output"
VISION_NAV_SITL_DRY_RUN=1 \
VISION_NAV_SITL_SMOKE_DIR="$smoke_dir" \
./scripts/dev/px4_sitl_external_vision_smoke.sh >"$smoke_output"
grep -q "__VISION_NAV_PX4_SITL_SESSION__=" "$smoke_output"
grep -q "__VISION_NAV_PX4_SITL_REPORT__=" "$smoke_output"
test -f "$smoke_dir/px4_sitl_evidence_session.json"
test -f "$smoke_dir/receiver_capture/README.md"
test -f "$smoke_dir/synthetic_external_vision.jsonl"
capture_smoke_dir="$(mktemp -d "$preflight_tmp_dir/sitl-capture.XXXXXX")"
VISION_NAV_SITL_CAPTURE_DRY_RUN=1 \
VISION_NAV_SITL_SMOKE_DIR="$capture_smoke_dir" \
./scripts/dev/run_px4_sitl_external_vision_capture.sh >"$capture_output"
grep -q "__VISION_NAV_PX4_SITL_SESSION__=" "$capture_output"
grep -q "__VISION_NAV_PX4_SITL_PREREQS__=" "$capture_output"
grep -q "__VISION_NAV_PX4_SITL_REPORT__=" "$capture_output"
test -f "$capture_smoke_dir/px4_sitl_evidence_session.json"
test -f "$capture_smoke_dir/px4_sitl_capture_prereqs.json"
test -f "$capture_smoke_dir/receiver_capture/README.md"
python3 - "$capture_smoke_dir/px4_sitl_capture_prereqs.json" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

report = json.loads(Path(sys.argv[1]).read_text())
assert report["schema_version"] == "vision_nav_px4_sitl_capture_prereqs_v1"
assert report["status"] == "not_checked"
assert "__VISION_NAV_PX4_SITL_PREREQS__" in report["markers"]
assert any(command["condition"] == "rerun_capture" for command in report["fix_commands"])
PY
capture_prereq_dir="$(mktemp -d "$preflight_tmp_dir/sitl-capture-prereq.XXXXXX")"
if VISION_NAV_PX4_AUTOPILOT_DIR="$preflight_tmp_dir/missing-px4-autopilot" \
  VISION_NAV_SITL_SMOKE_DIR="$capture_prereq_dir" \
  ./scripts/dev/run_px4_sitl_external_vision_capture.sh >"$capture_prereq_output" 2>&1; then
  echo "Expected PX4 SITL receiver capture to fail when prerequisites are missing." >&2
  exit 1
fi
grep -q "PX4 SITL receiver capture prerequisites are not ready" "$capture_prereq_output"
grep -q "__VISION_NAV_PX4_SITL_SESSION__=" "$capture_prereq_output"
grep -q "__VISION_NAV_PX4_SITL_PREREQS__=" "$capture_prereq_output"
grep -q "__VISION_NAV_PX4_SITL_REPORT__=" "$capture_prereq_output"
test -f "$capture_prereq_dir/px4_sitl_evidence_session.json"
test -f "$capture_prereq_dir/px4_sitl_capture_prereqs.json"
test -f "$capture_prereq_dir/receiver_capture/README.md"
python3 - "$capture_prereq_dir/px4_sitl_capture_prereqs.json" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

report = json.loads(Path(sys.argv[1]).read_text())
checks = {check["name"]: check for check in report["checks"]}
assert report["schema_version"] == "vision_nav_px4_sitl_capture_prereqs_v1"
assert report["status"] == "failed"
assert checks["px4_autopilot_dir"]["status"] == "failed"
assert "__VISION_NAV_PX4_SITL_PREREQS__" in report["markers"]
assert report["next_actions"]
conditions = {command["condition"] for command in report["fix_commands"]}
commands = [command["command"] for command in report["fix_commands"]]
assert "px4_sitl_prereqs" in conditions
assert "px4_autopilot_dir" in conditions
assert "rerun_capture" in conditions
assert any("setup_px4_sitl_prereqs.sh" in command for command in commands)
PY
if VISION_NAV_ALLOW_DEGRADED=1 ./scripts/dev/evaluate_px4_sitl_session.sh "$smoke_dir" >"$session_missing_capture_output"; then
  echo "Expected PX4 SITL session evaluation to fail before receiver captures exist." >&2
  exit 1
fi
rm -rf "$smoke_dir"
rm -rf "$capture_smoke_dir"
rm -rf "$capture_prereq_dir"

echo "[8/8] Checking unrelated agent/chatbot scope is absent"
scope_pattern="M""CP|L""LM|Chat""GPT"
if rg -n "$scope_pattern" .; then
  echo "Found unrelated agent/chatbot scope text. Remove it before committing." >&2
  exit 1
fi

echo "Local preflight passed."
