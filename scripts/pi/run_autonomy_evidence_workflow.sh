#!/usr/bin/env bash
set -uo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
venv_python="${VISION_NAV_PYTHON:-$HOME/drone_vision_nav_venv/bin/python}"
workflow_dir="${VISION_NAV_EVIDENCE_WORKFLOW_DIR:-$HOME/DroneTransfer/outgoing/replay-cases/autonomy-evidence-workflow}"
report="${VISION_NAV_EVIDENCE_WORKFLOW_REPORT:-$workflow_dir/autonomy_evidence_workflow.json}"
log_dir="$workflow_dir/logs"
log_archive="${VISION_NAV_EVIDENCE_WORKFLOW_LOG_ARCHIVE:-${report%.json}.logs.tar.gz}"
validation_report="${VISION_NAV_EVIDENCE_WORKFLOW_VALIDATION:-${report%.json}.validation.json}"
allow_failed="${VISION_NAV_EVIDENCE_WORKFLOW_ALLOW_FAILED:-1}"
field_template="${VISION_NAV_FIELD_TEMPLATE:-$HOME/DroneTransfer/outgoing/replay-cases/field_manifest.template.json}"
field_manifest="${VISION_NAV_FIELD_MANIFEST:-$HOME/DroneTransfer/outgoing/replay-cases/field_manifest.json}"
field_collection_plan="${VISION_NAV_FIELD_COLLECTION_PLAN:-$(dirname "$field_manifest")/field_collection_plan.json}"
field_collection_plan_md="${VISION_NAV_FIELD_COLLECTION_PLAN_MD:-${field_collection_plan%.json}.md}"
field_log="${VISION_NAV_FIELD_LOG:-$HOME/DroneTransfer/outgoing/terrain-match/terrain_matches.jsonl}"
field_capture_output_dir="${VISION_NAV_FIELD_CAPTURE_OUTPUT_DIR:-$(dirname "$field_log")}"
field_capture_count="${VISION_NAV_EVIDENCE_WORKFLOW_CAPTURE_COUNT:-30}"
rosbag_export_dir="${VISION_NAV_ROSBAG_EXPORT_DIR:-$HOME/DroneTransfer/outgoing/terrain-match/rosbag-jsonl}"
rosbag_export_validation="${VISION_NAV_ROSBAG_EXPORT_VALIDATION:-$HOME/DroneTransfer/outgoing/terrain-match/rosbag-jsonl-validation.json}"
rosbag2_cli_review="${VISION_NAV_ROSBAG2_CLI_REVIEW:-$HOME/DroneTransfer/outgoing/terrain-match/rosbag2-cli-review.json}"
bundle="${VISION_NAV_BUNDLE:-$HOME/drone-data/map_bundles/mission_bundle}"
px4_sitl_session="${VISION_NAV_PX4_SITL_SESSION:-$HOME/px4-sitl-evidence}"
px4_sitl_report="${VISION_NAV_PX4_SITL_REPORT:-$px4_sitl_session/receiver_evidence.json}"
px4_sitl_prereqs="${VISION_NAV_PX4_SITL_PREREQS:-$px4_sitl_session/px4_sitl_capture_prereqs.json}"
autonomy_readiness_report="${VISION_NAV_AUTONOMY_READINESS_REPORT:-$HOME/DroneTransfer/outgoing/replay-cases/autonomy_readiness_report.json}"
steps_jsonl=""

export VISION_NAV_FIELD_COLLECTION_PLAN="$field_collection_plan"
export VISION_NAV_FIELD_COLLECTION_PLAN_MD="$field_collection_plan_md"
export VISION_NAV_ROSBAG_EXPORT_VALIDATION="$rosbag_export_validation"
export VISION_NAV_ROSBAG2_CLI_REVIEW="$rosbag2_cli_review"

usage() {
  cat >&2 <<EOF
Usage:
  ./scripts/pi/run_autonomy_evidence_workflow.sh

This wrapper attempts the ordered evidence collection path:
  1. create/seed the field evidence template
  2. create/update the field collection plan
  3. verify an existing terrain log or run a bounded terrain capture
  4. optionally register a field replay case when VISION_NAV_FIELD_CASE_NAME,
     VISION_NAV_FIELD_EXPECTED, and VISION_NAV_FIELD_CONDITION(S) are provided
  5. run feature-method benchmark when a replay log exists
  6. run threshold tuning when a field manifest exists
  7. export and validate ROS bag JSONL replay artifacts when a replay log exists
  8. check whether native rosbag2 CLI review proof is available
  9. check whether PX4 ODOMETRY receiver proof is available
  10. create a support bundle
  11. run the strict autonomy-readiness audit and evidence package

Common optional overrides:
  VISION_NAV_EVIDENCE_WORKFLOW_REPORT     Default: $report
  VISION_NAV_EVIDENCE_WORKFLOW_LOG_ARCHIVE Default: $log_archive
  VISION_NAV_EVIDENCE_WORKFLOW_VALIDATION Default: $validation_report
  VISION_NAV_EVIDENCE_WORKFLOW_ALLOW_FAILED=0  Exit nonzero when any required step fails
  VISION_NAV_FIELD_CASE_NAME / VISION_NAV_FIELD_EXPECTED / VISION_NAV_FIELD_CONDITION
  VISION_NAV_FIELD_COLLECTION_PLAN       Default: $field_collection_plan
  VISION_NAV_FIELD_COLLECTION_PLAN_MD    Default: $field_collection_plan_md
  VISION_NAV_FIELD_LOG                    Default: $field_log
  VISION_NAV_FIELD_CAPTURE_OUTPUT_DIR     Default: $field_capture_output_dir
  VISION_NAV_EVIDENCE_WORKFLOW_CAPTURE_COUNT=30
  VISION_NAV_ROSBAG_EXPORT_DIR            Default: $rosbag_export_dir
  VISION_NAV_ROSBAG_EXPORT_VALIDATION     Default: $rosbag_export_validation
  VISION_NAV_ROSBAG2_CLI_REVIEW           Default: $rosbag2_cli_review
  VISION_NAV_BUNDLE                       Default: $bundle
  VISION_NAV_PX4_SITL_SESSION             Default: $px4_sitl_session
  VISION_NAV_PX4_SITL_REPORT              Default: $px4_sitl_report
  VISION_NAV_PX4_SITL_PREREQS             Default: $px4_sitl_prereqs
EOF
}

if [[ "${1:-}" == "-h" || "${1:-}" == "--help" ]]; then
  usage
  exit 0
fi

if [[ "$venv_python" == */* ]]; then
  if [[ ! -x "$venv_python" ]]; then
    echo "Missing Python venv: $venv_python" >&2
    echo "Run ./scripts/pi/bootstrap_pi5.sh first, then reboot." >&2
    exit 1
  fi
elif ! command -v "$venv_python" >/dev/null 2>&1; then
  echo "Python command not found: $venv_python" >&2
  echo "Run ./scripts/pi/bootstrap_pi5.sh first, then reboot, or set VISION_NAV_PYTHON." >&2
  exit 1
fi

mkdir -p "$(dirname "$report")" "$log_dir"
steps_jsonl="$(mktemp "${TMPDIR:-/tmp}/vision-nav-evidence-steps.XXXXXX")"
trap '[[ -n "$steps_jsonl" ]] && rm -f "$steps_jsonl"' EXIT

record_step() {
  local name="$1"
  local status="$2"
  local exit_code="$3"
  local log_path="$4"
  local notes="$5"
  PYTHONPATH="$repo_root/src" "$venv_python" - "$steps_jsonl" "$name" "$status" "$exit_code" "$log_path" "$notes" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

steps_path, name, status, exit_code, log_path, notes = sys.argv[1:7]
log_file = Path(log_path).expanduser() if log_path else None
markers: dict[str, str] = {}
tail: list[str] = []
if log_file and log_file.exists():
    lines = log_file.read_text(errors="replace").splitlines()
    tail = lines[-24:]
    for line in lines:
        if line.startswith("__VISION_NAV_") and "=" in line:
            key, value = line.split("=", 1)
            markers[key] = value
record = {
    "name": name,
    "status": status,
    "exit_code": int(exit_code),
    "log_path": str(log_file) if log_file else None,
    "notes": notes,
    "markers": markers,
    "tail": tail,
}
with Path(steps_path).open("a", encoding="utf-8") as handle:
    handle.write(json.dumps(record, sort_keys=True) + "\n")
PY
}

write_report() {
  local final_status="$1"
  PYTHONPATH="$repo_root/src" "$venv_python" - "$steps_jsonl" "$report" "$final_status" "$repo_root" "$workflow_dir" <<'PY'
from __future__ import annotations

import json
import sys
from datetime import datetime, timezone
from pathlib import Path

steps_path, report_path, status, repo_root, workflow_dir = sys.argv[1:6]
steps = []
if Path(steps_path).exists():
    for line in Path(steps_path).read_text(errors="replace").splitlines():
        if not line.strip():
            continue
        steps.append(json.loads(line))
markers = {}
for step in steps:
    markers.update(step.get("markers") or {})
summary = {
    "passed": sum(1 for step in steps if step.get("status") == "passed"),
    "failed": sum(1 for step in steps if step.get("status") == "failed"),
    "skipped": sum(1 for step in steps if step.get("status") == "skipped"),
}
report = {
    "schema_version": "vision_nav_autonomy_evidence_workflow_v1",
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "status": status,
    "summary": summary,
    "repo_root": repo_root,
    "workflow_dir": workflow_dir,
    "steps": steps,
    "markers": markers,
}
path = Path(report_path).expanduser()
path.parent.mkdir(parents=True, exist_ok=True)
path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
}

write_log_archive() {
  PYTHONPATH="$repo_root/src" "$venv_python" - "$log_dir" "$log_archive" "$report" <<'PY'
from __future__ import annotations

import json
import sys
import tarfile
from pathlib import Path

log_dir = Path(sys.argv[1]).expanduser()
archive_path = Path(sys.argv[2]).expanduser()
report_path = Path(sys.argv[3]).expanduser()
archive_path.parent.mkdir(parents=True, exist_ok=True)
with tarfile.open(archive_path, "w:gz") as archive:
    if log_dir.exists():
        archive.add(log_dir, arcname="logs")
if report_path.exists():
    report = json.loads(report_path.read_text(encoding="utf-8"))
    markers = report.setdefault("markers", {})
    markers["__VISION_NAV_EVIDENCE_WORKFLOW_LOGS__"] = str(archive_path)
    report["log_archive"] = str(archive_path)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
}

write_validation_report() {
  PYTHONPATH="$repo_root/src" "$venv_python" -m vision_nav.autonomy_evidence_workflow \
    --report "$report" \
    --output "$validation_report"
  PYTHONPATH="$repo_root/src" "$venv_python" - "$report" "$validation_report" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

report_path = Path(sys.argv[1]).expanduser()
validation_path = Path(sys.argv[2]).expanduser()
if report_path.exists():
    report = json.loads(report_path.read_text(encoding="utf-8"))
    markers = report.setdefault("markers", {})
    markers["__VISION_NAV_EVIDENCE_WORKFLOW_VALIDATION__"] = str(validation_path)
    report["validation_report"] = str(validation_path)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
PY
}

run_step() {
  local name="$1"
  shift
  local log_path="$log_dir/${name}.log"
  echo
  echo "== $name =="
  echo "$ $*"
  set +e
  "$@" >"$log_path" 2>&1
  local exit_code=$?
  set -e
  cat "$log_path"
  if [[ "$exit_code" -eq 0 ]]; then
    record_step "$name" "passed" "$exit_code" "$log_path" ""
  else
    record_step "$name" "failed" "$exit_code" "$log_path" "Review the step log and rerun after collecting the missing prerequisite."
  fi
  return 0
}

skip_step() {
  local name="$1"
  local notes="$2"
  local log_path="$log_dir/${name}.log"
  shift 2 || true
  {
    printf '%s\n' "$notes"
    for line in "$@"; do
      printf '%s\n' "$line"
    done
  } >"$log_path"
  echo
  echo "== $name =="
  cat "$log_path"
  record_step "$name" "skipped" 0 "$log_path" "$notes"
}

pass_step() {
  local name="$1"
  local notes="$2"
  local log_path="$log_dir/${name}.log"
  shift 2 || true
  {
    printf '%s\n' "$notes"
    for line in "$@"; do
      printf '%s\n' "$line"
    done
  } >"$log_path"
  echo
  echo "== $name =="
  cat "$log_path"
  record_step "$name" "passed" 0 "$log_path" "$notes"
}

degraded_step() {
  local name="$1"
  local notes="$2"
  local log_path="$log_dir/${name}.log"
  shift 2 || true
  {
    printf '%s\n' "$notes"
    for line in "$@"; do
      printf '%s\n' "$line"
    done
  } >"$log_path"
  echo
  echo "== $name =="
  cat "$log_path"
  record_step "$name" "degraded" 0 "$log_path" "$notes"
}

fail_step() {
  local name="$1"
  local notes="$2"
  local log_path="$log_dir/${name}.log"
  shift 2 || true
  {
    printf '%s\n' "$notes"
    for line in "$@"; do
      printf '%s\n' "$line"
    done
  } >"$log_path"
  echo
  echo "== $name =="
  cat "$log_path"
  record_step "$name" "failed" 1 "$log_path" "$notes"
}

sync_readiness_step_status() {
  local readiness_report="$1"
  if [[ ! -f "$readiness_report" ]]; then
    return 0
  fi
  PYTHONPATH="$repo_root/src" "$venv_python" - "$steps_jsonl" "$readiness_report" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

steps_path = Path(sys.argv[1])
report_path = Path(sys.argv[2]).expanduser()
try:
    readiness = json.loads(report_path.read_text(encoding="utf-8"))
except Exception:
    raise SystemExit(0)
readiness_status = str(readiness.get("status") or "").strip()
if readiness_status not in {"passed", "degraded", "failed"}:
    raise SystemExit(0)
steps = []
if steps_path.exists():
    for line in steps_path.read_text(encoding="utf-8", errors="replace").splitlines():
        if line.strip():
            steps.append(json.loads(line))
for step in reversed(steps):
    if step.get("name") != "run_autonomy_readiness_audit":
        continue
    step["readiness_report_status"] = readiness_status
    step["readiness_report_path"] = str(report_path)
    if readiness_status != "passed":
        step["status"] = readiness_status
        step["notes"] = (
            "Final autonomy-readiness report status is "
            f"{readiness_status}; preserve the report, handoff, and evidence package for follow-up."
        )
        if readiness_status == "failed" and int(step.get("exit_code") or 0) == 0:
            step["exit_code"] = 1
    else:
        step["status"] = "passed"
    break
with steps_path.open("w", encoding="utf-8") as handle:
    for step in steps:
        handle.write(json.dumps(step, sort_keys=True) + "\n")
PY
}

if [[ -f "$field_template" && -f "$field_manifest" && "${VISION_NAV_EVIDENCE_WORKFLOW_REFRESH_TEMPLATE:-0}" != "1" ]]; then
  skip_step "create_field_evidence_template" "Field template and active manifest already exist. Set VISION_NAV_EVIDENCE_WORKFLOW_REFRESH_TEMPLATE=1 and template force variables if you need to regenerate them."
else
  run_step "create_field_evidence_template" ./scripts/pi/create_field_evidence_template.sh
fi

run_step "create_field_collection_plan" ./scripts/pi/create_field_collection_plan.sh

runtime_status="$(dirname "$field_log")/runtime_status.json"
if [[ -f "$field_log" ]]; then
  marker_lines=("__VISION_NAV_TERRAIN_LOG__=$field_log")
  if [[ -f "$runtime_status" ]]; then
    marker_lines+=("__VISION_NAV_RUNTIME_STATUS__=$runtime_status")
  fi
  terrain_log_eval="$(
    PYTHONPATH="$repo_root/src" "$venv_python" - "$field_log" <<'PY'
from __future__ import annotations

import sys
from pathlib import Path

from vision_nav.summarize_match_log import summarize_log


def emit(status: str, message: str) -> None:
    print(f"{status}|{message}")


path = Path(sys.argv[1]).expanduser()
try:
    summary = summarize_log(path)
except Exception as exc:
    emit("failed", f"Could not parse terrain runtime log: {exc}")
    raise SystemExit(0)
total = int(summary.get("total_records") or 0)
status_counts = summary.get("status_counts") if isinstance(summary.get("status_counts"), dict) else {}
known = sum(int(status_counts.get(name) or 0) for name in ("accepted", "rejected", "degraded"))
if total <= 0:
    emit("failed", "Terrain runtime log is empty; capture a bounded runtime log before using it as workflow evidence.")
elif known <= 0:
    emit("failed", f"Terrain runtime log has {total} records but no accepted/rejected/degraded match statuses.")
else:
    accepted = int(status_counts.get("accepted") or 0)
    rejected = int(status_counts.get("rejected") or 0)
    degraded = int(status_counts.get("degraded") or 0)
    emit(
        "passed",
        f"Terrain runtime log is parseable with {total} records "
        f"(accepted={accepted}, rejected={rejected}, degraded={degraded}).",
    )
PY
  )"
  terrain_log_status="${terrain_log_eval%%|*}"
  terrain_log_message="${terrain_log_eval#*|}"
  if [[ "$terrain_log_status" == "passed" && -f "$runtime_status" ]]; then
    pass_step "capture_field_terrain_log" "$terrain_log_message" "${marker_lines[@]}"
  elif [[ "$terrain_log_status" == "passed" ]]; then
    degraded_step "capture_field_terrain_log" \
      "$terrain_log_message Runtime status snapshot is missing; fetch or generate runtime_status.json before final bench evidence." \
      "${marker_lines[@]}"
  else
    fail_step "capture_field_terrain_log" "$terrain_log_message" "${marker_lines[@]}"
  fi
elif [[ -e "$bundle" ]]; then
  (
    export VISION_NAV_COUNT="$field_capture_count"
    export VISION_NAV_OUTPUT_DIR="$field_capture_output_dir"
    run_step "capture_field_terrain_log" ./scripts/pi/run_terrain_nav_loop.sh
  )
  captured_field_log="$field_capture_output_dir/terrain_matches.jsonl"
  if [[ -f "$captured_field_log" ]]; then
    field_log="$captured_field_log"
    export VISION_NAV_FIELD_LOG="$field_log"
  fi
else
  skip_step "capture_field_terrain_log" "Missing terrain replay log and bundle. Expected log: $field_log ; bundle: $bundle"
fi

if [[ -n "${VISION_NAV_FIELD_CASE_NAME:-}" && -n "${VISION_NAV_FIELD_EXPECTED:-}" && -n "${VISION_NAV_FIELD_CONDITIONS:-${VISION_NAV_FIELD_CONDITION:-}}" ]]; then
  VISION_NAV_FIELD_GATE_STRICT=0 run_step "register_field_replay_case" ./scripts/pi/register_field_replay_case.sh
else
  skip_step "register_field_replay_case" "Set VISION_NAV_FIELD_CASE_NAME, VISION_NAV_FIELD_EXPECTED, and VISION_NAV_FIELD_CONDITION(S) after a real field log exists to register evidence."
fi

if [[ -f "$field_log" && -e "$bundle" ]]; then
  VISION_NAV_FEATURE_BENCH_ALLOW_FAILED=1 run_step "run_feature_method_benchmark" ./scripts/pi/run_feature_method_benchmark.sh
else
  skip_step "run_feature_method_benchmark" "Missing replay log or bundle. Expected log: $field_log ; bundle: $bundle"
fi

if [[ -f "$field_manifest" ]]; then
  VISION_NAV_THRESHOLD_ALLOW_FAILED=1 run_step "run_threshold_tuning_report" ./scripts/pi/run_threshold_tuning_report.sh
else
  skip_step "run_threshold_tuning_report" "Missing field manifest: $field_manifest"
fi

if [[ -f "$field_log" ]]; then
  (
    export VISION_NAV_PYTHON="$venv_python"
    export VISION_NAV_ROSBAG_SOURCE_LOG="$field_log"
    export VISION_NAV_ROSBAG_EXPORT_DIR="$rosbag_export_dir"
    export VISION_NAV_ROSBAG_EXPORT_VALIDATION="$rosbag_export_validation"
    run_step "validate_rosbag_export" ./scripts/pi/run_rosbag_export_validation.sh
  )
else
  skip_step "validate_rosbag_export" "Missing terrain match replay log: $field_log"
fi

if [[ -f "$rosbag2_cli_review" ]]; then
  rosbag2_review_eval="$(
    PYTHONPATH="$repo_root/src" "$venv_python" - "$rosbag2_cli_review" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path


def emit(status: str, message: str) -> None:
    print(f"{status}|{message}")


path = Path(sys.argv[1]).expanduser()
try:
    report = json.loads(path.read_text(encoding="utf-8"))
except Exception as exc:
    emit("failed", f"Could not parse native rosbag2 CLI review JSON: {exc}")
    raise SystemExit(0)
if not isinstance(report, dict):
    emit("failed", "Native rosbag2 CLI review root is not a JSON object.")
    raise SystemExit(0)
if report.get("schema_version") != "vision_nav_rosbag2_cli_review_v1":
    emit("failed", "Native rosbag2 CLI review schema is not recognized.")
    raise SystemExit(0)

status = str(report.get("status") or "").lower()
validation_status = str(report.get("validation_status") or "").lower()
validation_format = str(report.get("validation_format") or "")
cli = report.get("ros2_cli") if isinstance(report.get("ros2_cli"), dict) else {}
cli_status = str(cli.get("status") or "").lower()
cli_exit_code = cli.get("exit_code")
if (
    status == "passed"
    and validation_status == "passed"
    and validation_format == "vision_nav_rosbag2_v1"
    and cli_status == "passed"
    and cli_exit_code == 0
):
    emit("passed", "Native rosbag2 CLI review passed and is usable for final-readiness evidence.")
elif status == "degraded" or validation_status == "degraded" or cli_status in {"degraded", "skipped"}:
    emit("degraded", "Native rosbag2 CLI review is degraded; preserve it for diagnostics, but rerun before final readiness.")
else:
    emit("failed", "Native rosbag2 CLI review did not pass; rerun the sourced workstation review before final readiness.")
PY
  )"
  rosbag2_review_status="${rosbag2_review_eval%%|*}"
  rosbag2_review_message="${rosbag2_review_eval#*|}"
  if [[ "$rosbag2_review_status" == "passed" ]]; then
    pass_step "check_native_rosbag2_review" \
      "$rosbag2_review_message" \
      "__VISION_NAV_ROSBAG2_CLI_REVIEW__=$rosbag2_cli_review"
  elif [[ "$rosbag2_review_status" == "degraded" ]]; then
    degraded_step "check_native_rosbag2_review" \
      "$rosbag2_review_message" \
      "__VISION_NAV_ROSBAG2_CLI_REVIEW__=$rosbag2_cli_review"
  else
    fail_step "check_native_rosbag2_review" \
      "$rosbag2_review_message" \
      "__VISION_NAV_ROSBAG2_CLI_REVIEW__=$rosbag2_cli_review"
  fi
else
  skip_step "check_native_rosbag2_review" \
    "Missing native rosbag2 CLI review artifact: $rosbag2_cli_review. Run Module Setup > Native rosbag2 Review on a sourced ROS 2 workstation, or run ./scripts/dev/run_rosbag2_cli_review.sh after syncing a field log."
fi

px4_proof_marker_lines=()
px4_diagnostic_marker_lines=()
if [[ -f "$px4_sitl_session/px4_sitl_evidence_session.json" ]]; then
  export VISION_NAV_PX4_SITL_SESSION="$px4_sitl_session"
  px4_diagnostic_marker_lines+=("__VISION_NAV_PX4_SITL_SESSION__=$px4_sitl_session")
fi
if [[ -f "$px4_sitl_report" ]]; then
  export VISION_NAV_PX4_SITL_REPORT="$px4_sitl_report"
  px4_proof_marker_lines+=("__VISION_NAV_PX4_SITL_REPORT__=$px4_sitl_report")
fi
if [[ -f "$px4_sitl_prereqs" ]]; then
  export VISION_NAV_PX4_SITL_PREREQS="$px4_sitl_prereqs"
  px4_diagnostic_marker_lines+=("__VISION_NAV_PX4_SITL_PREREQS__=$px4_sitl_prereqs")
fi

if ((${#px4_proof_marker_lines[@]} > 0)); then
  pass_step "check_px4_receiver_proof" \
    "PX4 external-vision receiver evidence is available for support-bundle and final-readiness evidence." \
    "${px4_proof_marker_lines[@]}" \
    "${px4_diagnostic_marker_lines[@]}"
elif ((${#px4_diagnostic_marker_lines[@]} > 0)); then
  skip_step "check_px4_receiver_proof" \
    "PX4 session or prerequisite diagnostics are available, but evaluated receiver proof is still missing. Capture ODOMETRY receiver evidence before treating the support bundle as bench-ready: VISION_NAV_SITL_SMOKE_DIR=\$PWD/px4-sitl-evidence ./scripts/dev/run_px4_sitl_external_vision_capture.sh" \
    "${px4_diagnostic_marker_lines[@]}"
else
  skip_step "check_px4_receiver_proof" \
    "Missing PX4 ODOMETRY receiver proof. Capture it before treating the support bundle as bench-ready: VISION_NAV_SITL_SMOKE_DIR=\$PWD/px4-sitl-evidence ./scripts/dev/run_px4_sitl_external_vision_capture.sh"
fi

run_step "create_support_bundle" ./scripts/pi/create_support_bundle.sh
VISION_NAV_AUTONOMY_ALLOW_FAILED=1 run_step "run_autonomy_readiness_audit" ./scripts/pi/run_autonomy_readiness_audit.sh
sync_readiness_step_status "$autonomy_readiness_report"

final_status="passed"
if grep -q '"status": "failed"' "$steps_jsonl"; then
  final_status="failed"
elif grep -Eq '"status": "(degraded|skipped)"' "$steps_jsonl"; then
  final_status="degraded"
fi

write_report "$final_status"
write_log_archive
write_validation_report

cat <<EOF

Autonomy evidence workflow output:
  report:      $report
  logs:        $log_dir
  log archive: $log_archive
  validation:  $validation_report

__VISION_NAV_EVIDENCE_WORKFLOW_REPORT__=$report
__VISION_NAV_EVIDENCE_WORKFLOW_LOGS__=$log_archive
__VISION_NAV_EVIDENCE_WORKFLOW_VALIDATION__=$validation_report
EOF

if [[ "$final_status" != "passed" && "$allow_failed" != "1" && "$allow_failed" != "true" ]]; then
  exit 1
fi
