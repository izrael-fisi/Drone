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
field_log_was_explicit=0
field_capture_output_dir_was_explicit=0
if [[ -n "${VISION_NAV_FIELD_LOG+x}" ]]; then
  field_log_was_explicit=1
fi
if [[ -n "${VISION_NAV_FIELD_CAPTURE_OUTPUT_DIR+x}" ]]; then
  field_capture_output_dir_was_explicit=1
fi
field_log="${VISION_NAV_FIELD_LOG:-$HOME/DroneTransfer/outgoing/terrain-match/terrain_matches.jsonl}"
field_capture_output_dir="${VISION_NAV_FIELD_CAPTURE_OUTPUT_DIR:-$(dirname "$field_log")}"
field_capture_count="${VISION_NAV_EVIDENCE_WORKFLOW_CAPTURE_COUNT:-30}"
terrain_capture_command="${VISION_NAV_EVIDENCE_WORKFLOW_TERRAIN_CAPTURE_COMMAND:-./scripts/pi/run_terrain_nav_loop.sh}"
rosbag_export_dir="${VISION_NAV_ROSBAG_EXPORT_DIR:-$HOME/DroneTransfer/outgoing/terrain-match/rosbag-jsonl}"
rosbag_export_validation="${VISION_NAV_ROSBAG_EXPORT_VALIDATION:-$HOME/DroneTransfer/outgoing/terrain-match/rosbag-jsonl-validation.json}"
rosbag2_cli_review="${VISION_NAV_ROSBAG2_CLI_REVIEW:-$HOME/DroneTransfer/outgoing/terrain-match/rosbag2-cli-review.json}"
bundle="${VISION_NAV_BUNDLE:-$HOME/drone-data/map_bundles/mission_bundle}"
home_px4_sitl_dir="$HOME/px4-sitl-evidence"
repo_px4_sitl_dir="$repo_root/px4-sitl-evidence"
px4_sitl_session="${VISION_NAV_PX4_SITL_SESSION:-}"
if [[ -z "$px4_sitl_session" && -f "$home_px4_sitl_dir/px4_sitl_evidence_session.json" ]]; then
  px4_sitl_session="$home_px4_sitl_dir"
fi
if [[ -z "$px4_sitl_session" && ( -f "$home_px4_sitl_dir/receiver_evidence.json" || -f "$home_px4_sitl_dir/px4_sitl_capture_prereqs.json" ) ]]; then
  px4_sitl_session="$home_px4_sitl_dir"
fi
if [[ -z "$px4_sitl_session" && -f "$repo_px4_sitl_dir/px4_sitl_evidence_session.json" ]]; then
  px4_sitl_session="$repo_px4_sitl_dir"
fi
if [[ -z "$px4_sitl_session" && ( -f "$repo_px4_sitl_dir/receiver_evidence.json" || -f "$repo_px4_sitl_dir/px4_sitl_capture_prereqs.json" ) ]]; then
  px4_sitl_session="$repo_px4_sitl_dir"
fi
if [[ -z "$px4_sitl_session" ]]; then
  px4_sitl_session="$home_px4_sitl_dir"
fi
px4_sitl_report="${VISION_NAV_PX4_SITL_REPORT:-}"
if [[ -z "$px4_sitl_report" && -f "$px4_sitl_session/receiver_evidence.json" ]]; then
  px4_sitl_report="$px4_sitl_session/receiver_evidence.json"
fi
if [[ -z "$px4_sitl_report" ]]; then
  px4_sitl_report="$px4_sitl_session/receiver_evidence.json"
fi
px4_sitl_prereqs="${VISION_NAV_PX4_SITL_PREREQS:-}"
if [[ -z "$px4_sitl_prereqs" && -f "$px4_sitl_session/px4_sitl_capture_prereqs.json" ]]; then
  px4_sitl_prereqs="$px4_sitl_session/px4_sitl_capture_prereqs.json"
fi
if [[ -z "$px4_sitl_prereqs" ]]; then
  px4_sitl_prereqs="$px4_sitl_session/px4_sitl_capture_prereqs.json"
fi
autonomy_readiness_report="${VISION_NAV_AUTONOMY_READINESS_REPORT:-$HOME/DroneTransfer/outgoing/replay-cases/autonomy_readiness_report.json}"
steps_jsonl=""
field_log_ready=0

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
  4. load the next pending field collection condition when no explicit field
     case is provided
  5. register a field replay case only when capture metadata is complete
  6. refresh the field collection plan after capture or registration
  7. run feature-method benchmark when a replay log exists
  8. run threshold tuning when a field manifest exists
  9. export and validate ROS bag JSONL replay artifacts when a replay log exists
  10. check whether native rosbag2 CLI review proof is available
  11. check whether PX4 ODOMETRY receiver proof is available
  12. create a support bundle
  13. run the strict autonomy-readiness audit and evidence package

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
  VISION_NAV_EVIDENCE_WORKFLOW_TERRAIN_CAPTURE_COMMAND=$terrain_capture_command
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

import hashlib
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

from vision_nav.autonomy_evidence_workflow import REQUIRED_WORKFLOW_STEPS

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
repo_path = Path(repo_root)
script_path = repo_path / "scripts/pi/run_autonomy_evidence_workflow.sh"


def git_value(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_path), *args],
            check=False,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except Exception:
        return None
    if result.returncode != 0:
        return None
    value = result.stdout.strip()
    return value or None


script_sha256 = None
if script_path.is_file():
    script_sha256 = hashlib.sha256(script_path.read_bytes()).hexdigest()
dirty_raw = git_value("status", "--porcelain")
workflow_provenance = {
    "repo_commit": git_value("rev-parse", "HEAD"),
    "repo_dirty": bool(dirty_raw),
    "script_path": str(script_path),
    "script_sha256": script_sha256,
    "required_steps": list(REQUIRED_WORKFLOW_STEPS),
    "required_step_count": len(REQUIRED_WORKFLOW_STEPS),
}
report = {
    "schema_version": "vision_nav_autonomy_evidence_workflow_v1",
    "generated_at": datetime.now(timezone.utc).isoformat(),
    "status": status,
    "summary": summary,
    "repo_root": repo_root,
    "workflow_dir": workflow_dir,
    "workflow_provenance": workflow_provenance,
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

evaluate_terrain_log() {
  local log_path="$1"
  PYTHONPATH="$repo_root/src" "$venv_python" - "$log_path" <<'PY'
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
}

evaluate_runtime_status() {
  local status_path="$1"
  PYTHONPATH="$repo_root/src" "$venv_python" - "$status_path" <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path


def emit(status: str, message: str) -> None:
    print(f"{status}|{message}")


path = Path(sys.argv[1]).expanduser()
try:
    snapshot = json.loads(path.read_text(encoding="utf-8"))
except Exception as exc:
    emit("degraded", f"Runtime status snapshot is not parseable JSON: {exc}")
    raise SystemExit(0)
if not isinstance(snapshot, dict):
    emit("degraded", "Runtime status snapshot root is not a JSON object.")
    raise SystemExit(0)
active_map = snapshot.get("active_map") if isinstance(snapshot.get("active_map"), dict) else {}
last_match = snapshot.get("last_match") if isinstance(snapshot.get("last_match"), dict) else {}
output = snapshot.get("output") if isinstance(snapshot.get("output"), dict) else {}
active_map_id = active_map.get("bundle_id") or active_map.get("map_id")
last_match_status = last_match.get("status")
output_dir = output.get("output_dir") or snapshot.get("output_path")
log_path = output.get("log_path") or snapshot.get("log_path")
if not active_map_id:
    emit("degraded", "Runtime status snapshot is missing active-map state.")
elif not last_match_status:
    emit("degraded", "Runtime status snapshot is missing last-match state.")
elif not output_dir or not log_path:
    emit("degraded", "Runtime status snapshot is missing output/log path metadata.")
else:
    emit(
        "passed",
        f"Runtime status snapshot is usable for active map {active_map_id} "
        f"with last match status {last_match_status}.",
    )
PY
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

field_case_vars_present() {
  [[ -n "${VISION_NAV_FIELD_CASE_NAME:-}" && -n "${VISION_NAV_FIELD_EXPECTED:-}" && -n "${VISION_NAV_FIELD_CONDITIONS:-${VISION_NAV_FIELD_CONDITION:-}}" ]]
}

terrain_capture_command_marker() {
  printf 'VISION_NAV_BUNDLE=%s VISION_NAV_OUTPUT_DIR=%s VISION_NAV_COUNT=%s %s' \
    "$bundle" \
    "$field_capture_output_dir" \
    "$field_capture_count" \
    "$terrain_capture_command"
}

load_field_collection_condition() {
  if field_case_vars_present; then
    explicit_condition="${VISION_NAV_FIELD_CONDITIONS:-${VISION_NAV_FIELD_CONDITION:-}}"
    capture_command_marker="$(terrain_capture_command_marker)"
    pass_step "select_field_collection_condition" \
      "Using explicit field replay case environment from the operator." \
      "__VISION_NAV_FIELD_SELECTED_CONDITION__=$explicit_condition" \
      "__VISION_NAV_FIELD_SELECTED_CASE__=${VISION_NAV_FIELD_CASE_NAME:-}" \
      "__VISION_NAV_FIELD_SELECTED_LOG__=$field_log" \
      "__VISION_NAV_EXPECTED_TERRAIN_LOG__=$field_log" \
      "__VISION_NAV_TERRAIN_BUNDLE__=$bundle" \
      "__VISION_NAV_TERRAIN_CAPTURE_OUTPUT_DIR__=$field_capture_output_dir" \
      "__VISION_NAV_TERRAIN_CAPTURE_COMMAND__=$capture_command_marker"
    return 0
  fi

  if [[ ! -f "$field_collection_plan" ]]; then
    degraded_step "select_field_collection_condition" \
      "Field collection plan is unavailable, so the workflow cannot auto-select the next pending condition: $field_collection_plan"
    return 0
  fi

  set +e
  selection_exports="$(PYTHONPATH="$repo_root/src" "$venv_python" -m vision_nav.field_workflow_selection --plan "$field_collection_plan" --shell 2>&1)"
  selection_exit=$?
  set -e
  if [[ "$selection_exit" -ne 0 ]]; then
    fail_step "select_field_collection_condition" \
      "Could not load the next field collection condition from $field_collection_plan." \
      "$selection_exports"
    return 0
  fi

  eval "$selection_exports"
  if [[ "$field_log_was_explicit" == "1" ]]; then
    export VISION_NAV_FIELD_LOG="$field_log"
  else
    field_log="${VISION_NAV_FIELD_LOG:-$field_log}"
  fi
  if [[ "$field_capture_output_dir_was_explicit" == "1" || "$field_log_was_explicit" == "1" ]]; then
    export VISION_NAV_FIELD_CAPTURE_OUTPUT_DIR="$field_capture_output_dir"
  else
    field_capture_output_dir="${VISION_NAV_FIELD_CAPTURE_OUTPUT_DIR:-$field_capture_output_dir}"
  fi
  bundle="${VISION_NAV_BUNDLE:-$bundle}"

  if [[ "${VISION_NAV_FIELD_AUTO_SELECTION_STATUS:-}" == "no_pending_condition" ]]; then
    pass_step "select_field_collection_condition" \
      "Field collection plan has no pending condition to auto-load." \
      "__VISION_NAV_FIELD_COLLECTION_PLAN__=$field_collection_plan"
  elif [[ "${VISION_NAV_FIELD_AUTO_SELECTED:-0}" == "1" ]]; then
    capture_command_marker="$(terrain_capture_command_marker)"
    marker_lines=(
      "__VISION_NAV_FIELD_COLLECTION_PLAN__=$field_collection_plan"
      "__VISION_NAV_FIELD_SELECTED_CONDITION__=${VISION_NAV_FIELD_AUTO_SELECTED_CONDITION:-}"
      "__VISION_NAV_FIELD_SELECTED_CASE__=${VISION_NAV_FIELD_AUTO_SELECTED_CASE:-}"
      "__VISION_NAV_FIELD_SELECTED_LOG__=$field_log"
      "__VISION_NAV_EXPECTED_TERRAIN_LOG__=$field_log"
      "__VISION_NAV_TERRAIN_BUNDLE__=$bundle"
      "__VISION_NAV_TERRAIN_CAPTURE_OUTPUT_DIR__=$field_capture_output_dir"
      "__VISION_NAV_TERRAIN_CAPTURE_COMMAND__=$capture_command_marker"
    )
    if [[ -n "${VISION_NAV_FIELD_METADATA_UPDATE_COMMAND:-}" ]]; then
      marker_lines+=("__VISION_NAV_FIELD_METADATA_UPDATE_COMMAND__=${VISION_NAV_FIELD_METADATA_UPDATE_COMMAND:-}")
    fi
    if [[ "${VISION_NAV_FIELD_CAPTURE_METADATA_READY:-failed}" == "passed" ]]; then
      pass_step "select_field_collection_condition" \
        "Loaded next field collection condition ${VISION_NAV_FIELD_AUTO_SELECTED_CONDITION:-unknown}; capture metadata is complete." \
        "${marker_lines[@]}"
    else
      degraded_step "select_field_collection_condition" \
        "Loaded next field collection condition ${VISION_NAV_FIELD_AUTO_SELECTED_CONDITION:-unknown}; capture metadata still needs completion before registration." \
        "${marker_lines[@]}"
    fi
  else
    degraded_step "select_field_collection_condition" \
      "Field collection condition auto-selection did not choose a pending case. Status: ${VISION_NAV_FIELD_AUTO_SELECTION_STATUS:-unknown}"
  fi
}

evaluate_field_capture_metadata() {
  local conditions_raw="$1"
  local expected="$2"
  local metadata_json="$3"
  PYTHONPATH="$repo_root/src" "$venv_python" - "$conditions_raw" "$expected" "$metadata_json" <<'PY'
from __future__ import annotations

import sys

from vision_nav.field_capture_metadata import audit_capture_metadata, parse_capture_metadata_json
from vision_nav.replay_case_registry import normalize_conditions


def emit(status: str, message: str) -> None:
    print(f"{status}|{message}")


conditions_raw, expected, metadata_json = sys.argv[1:4]
try:
    metadata = parse_capture_metadata_json(metadata_json)
except Exception as exc:
    emit("degraded", f"Capture metadata JSON is not parseable: {exc}")
    raise SystemExit(0)
issues = audit_capture_metadata(
    metadata,
    conditions=normalize_conditions([conditions_raw]),
    expected=expected or None,
)
if issues:
    first = issues[0].get("message") or issues[0].get("field") or "metadata issue"
    emit("degraded", f"Capture metadata is incomplete ({len(issues)} issue(s)); first issue: {first}")
else:
    emit("passed", "Capture metadata is complete for field replay registration.")
PY
}

if [[ -f "$field_template" && -f "$field_manifest" && "${VISION_NAV_EVIDENCE_WORKFLOW_REFRESH_TEMPLATE:-0}" != "1" ]]; then
  pass_step "create_field_evidence_template" \
    "Field template and active manifest already exist; treating this idempotent prerequisite as satisfied." \
    "__VISION_NAV_FIELD_TEMPLATE__=$field_template" \
    "__VISION_NAV_FIELD_MANIFEST__=$field_manifest"
elif [[ -f "$field_template" && ! -f "$field_manifest" && "${VISION_NAV_EVIDENCE_WORKFLOW_REFRESH_TEMPLATE:-0}" != "1" ]]; then
  seed_log_path="$log_dir/create_field_evidence_template.log"
  set +e
  PYTHONPATH="$repo_root/src" "$venv_python" - "$field_template" "$field_manifest" >"$seed_log_path" 2>&1 <<'PY'
from __future__ import annotations

import json
import sys
from pathlib import Path

template_path = Path(sys.argv[1]).expanduser()
manifest_path = Path(sys.argv[2]).expanduser()
template = json.loads(template_path.read_text(encoding="utf-8"))
if not isinstance(template, dict) or not isinstance(template.get("cases"), list):
    raise SystemExit(f"Field template is not a replay manifest template with cases: {template_path}")
manifest_path.parent.mkdir(parents=True, exist_ok=True)
manifest_path.write_text(json.dumps(template, indent=2, sort_keys=True) + "\n", encoding="utf-8")
print(f"Seeded active field manifest from existing template: {manifest_path}")
print(f"__VISION_NAV_FIELD_TEMPLATE__={template_path}")
print(f"__VISION_NAV_FIELD_MANIFEST__={manifest_path}")
PY
  seed_exit=$?
  set -e
  echo
  echo "== create_field_evidence_template =="
  cat "$seed_log_path"
  if [[ "$seed_exit" -eq 0 ]]; then
    record_step "create_field_evidence_template" "passed" "$seed_exit" "$seed_log_path" "Field template already existed; seeded the missing active manifest from it."
  else
    record_step "create_field_evidence_template" "failed" "$seed_exit" "$seed_log_path" "Existing field template could not seed the missing active manifest."
  fi
else
  run_step "create_field_evidence_template" ./scripts/pi/create_field_evidence_template.sh
fi

run_step "create_field_collection_plan" ./scripts/pi/create_field_collection_plan.sh
load_field_collection_condition

runtime_status="$(dirname "$field_log")/runtime_status.json"
if [[ -f "$field_log" ]]; then
  marker_lines=(
    "__VISION_NAV_TERRAIN_LOG__=$field_log"
    "__VISION_NAV_EXPECTED_TERRAIN_LOG__=$field_log"
    "__VISION_NAV_TERRAIN_BUNDLE__=$bundle"
    "__VISION_NAV_TERRAIN_BUNDLE_STATUS__=available"
  )
  runtime_status_eval="degraded|Runtime status snapshot is missing; fetch or generate runtime_status.json before final bench evidence."
  if [[ -f "$runtime_status" ]]; then
    marker_lines+=("__VISION_NAV_RUNTIME_STATUS__=$runtime_status")
    runtime_status_eval="$(evaluate_runtime_status "$runtime_status")"
  fi
  runtime_status_status="${runtime_status_eval%%|*}"
  runtime_status_message="${runtime_status_eval#*|}"
  terrain_log_eval="$(evaluate_terrain_log "$field_log")"
  terrain_log_status="${terrain_log_eval%%|*}"
  terrain_log_message="${terrain_log_eval#*|}"
  if [[ "$terrain_log_status" == "passed" && "$runtime_status_status" == "passed" ]]; then
    field_log_ready=1
    pass_step "capture_field_terrain_log" "$terrain_log_message $runtime_status_message" "${marker_lines[@]}"
  elif [[ "$terrain_log_status" == "passed" ]]; then
    field_log_ready=1
    degraded_step "capture_field_terrain_log" \
      "$terrain_log_message $runtime_status_message" \
      "${marker_lines[@]}"
  else
    fail_step "capture_field_terrain_log" "$terrain_log_message" "${marker_lines[@]}"
  fi
elif [[ -e "$bundle" ]]; then
  capture_log_path="$log_dir/capture_field_terrain_log.log"
  capture_command_marker="$(terrain_capture_command_marker)"
  capture_context_lines=(
    "__VISION_NAV_EXPECTED_TERRAIN_LOG__=$field_capture_output_dir/terrain_matches.jsonl"
    "__VISION_NAV_TERRAIN_BUNDLE__=$bundle"
    "__VISION_NAV_TERRAIN_BUNDLE_STATUS__=available"
    "__VISION_NAV_TERRAIN_CAPTURE_OUTPUT_DIR__=$field_capture_output_dir"
    "__VISION_NAV_TERRAIN_CAPTURE_COMMAND__=$capture_command_marker"
  )
  echo
  echo "== capture_field_terrain_log =="
  echo "$ $terrain_capture_command"
  set +e
  (
    export VISION_NAV_COUNT="$field_capture_count"
    export VISION_NAV_OUTPUT_DIR="$field_capture_output_dir"
    export VISION_NAV_BUNDLE="$bundle"
    "$terrain_capture_command"
  ) >"$capture_log_path" 2>&1
  capture_exit=$?
  set -e
  cat "$capture_log_path"
  {
    echo
    echo "Capture context:"
    for line in "${capture_context_lines[@]}"; do
      echo "$line"
    done
  } >>"$capture_log_path"
  captured_field_log="$field_capture_output_dir/terrain_matches.jsonl"
  if [[ "$capture_exit" -ne 0 ]]; then
    record_step \
      "capture_field_terrain_log" \
      "failed" \
      "$capture_exit" \
      "$capture_log_path" \
      "Terrain runtime capture command failed; review the step log and rerun after fixing the runtime prerequisite."
  elif [[ ! -f "$captured_field_log" ]]; then
    {
      echo
      echo "Expected captured terrain log was not written: $captured_field_log"
    } >>"$capture_log_path"
    record_step \
      "capture_field_terrain_log" \
      "failed" \
      1 \
      "$capture_log_path" \
      "Terrain runtime capture completed but did not write terrain_matches.jsonl."
  else
    field_log="$captured_field_log"
    export VISION_NAV_FIELD_LOG="$field_log"
    captured_runtime_status="$field_capture_output_dir/runtime_status.json"
    captured_marker_lines=(
      "__VISION_NAV_TERRAIN_LOG__=$field_log"
      "${capture_context_lines[@]}"
    )
    captured_runtime_status_eval="degraded|Runtime status snapshot is missing; fetch or generate runtime_status.json before final bench evidence."
    if [[ -f "$captured_runtime_status" ]]; then
      captured_marker_lines+=("__VISION_NAV_RUNTIME_STATUS__=$captured_runtime_status")
      captured_runtime_status_eval="$(evaluate_runtime_status "$captured_runtime_status")"
    fi
    captured_runtime_status_status="${captured_runtime_status_eval%%|*}"
    captured_runtime_status_message="${captured_runtime_status_eval#*|}"
    captured_log_eval="$(evaluate_terrain_log "$field_log")"
    captured_log_status="${captured_log_eval%%|*}"
    captured_log_message="${captured_log_eval#*|}"
    {
      echo
      echo "Capture validation:"
      echo "$captured_log_message"
      echo "$captured_runtime_status_message"
      for line in "${captured_marker_lines[@]}"; do
        echo "$line"
      done
    } >>"$capture_log_path"
    if [[ "$captured_log_status" == "passed" && "$captured_runtime_status_status" == "passed" ]]; then
      field_log_ready=1
      record_step \
        "capture_field_terrain_log" \
        "passed" \
        0 \
        "$capture_log_path" \
        "$captured_log_message $captured_runtime_status_message"
    elif [[ "$captured_log_status" == "passed" ]]; then
      field_log_ready=1
      record_step \
        "capture_field_terrain_log" \
        "degraded" \
        0 \
        "$capture_log_path" \
        "$captured_log_message $captured_runtime_status_message"
    else
      record_step \
        "capture_field_terrain_log" \
        "failed" \
        1 \
        "$capture_log_path" \
        "$captured_log_message"
    fi
  fi
else
  capture_command_marker="$(terrain_capture_command_marker)"
  skip_step "capture_field_terrain_log" \
    "Missing terrain replay log and bundle. Expected log: $field_log ; bundle: $bundle" \
    "__VISION_NAV_EXPECTED_TERRAIN_LOG__=$field_log" \
    "__VISION_NAV_TERRAIN_BUNDLE__=$bundle" \
    "__VISION_NAV_TERRAIN_BUNDLE_STATUS__=missing" \
    "__VISION_NAV_TERRAIN_CAPTURE_OUTPUT_DIR__=$field_capture_output_dir" \
    "__VISION_NAV_TERRAIN_CAPTURE_COMMAND__=$capture_command_marker"
fi

if [[ "$field_log_ready" != "1" ]]; then
  skip_step "register_field_replay_case" "Terrain log was not validated in this workflow run; capture or provide a parseable log before registering field evidence."
elif [[ -n "${VISION_NAV_FIELD_CASE_NAME:-}" && -n "${VISION_NAV_FIELD_EXPECTED:-}" && -n "${VISION_NAV_FIELD_CONDITIONS:-${VISION_NAV_FIELD_CONDITION:-}}" ]]; then
  selected_conditions="${VISION_NAV_FIELD_CONDITIONS:-${VISION_NAV_FIELD_CONDITION:-}}"
  metadata_eval="$(evaluate_field_capture_metadata "$selected_conditions" "${VISION_NAV_FIELD_EXPECTED:-}" "${VISION_NAV_FIELD_CAPTURE_METADATA:-}")"
  metadata_status="${metadata_eval%%|*}"
  metadata_message="${metadata_eval#*|}"
  if [[ "$metadata_status" == "passed" ]]; then
    VISION_NAV_FIELD_GATE_STRICT=0 run_step "register_field_replay_case" ./scripts/pi/register_field_replay_case.sh
  else
    metadata_marker_lines=(
      "__VISION_NAV_FIELD_SELECTED_CONDITION__=$selected_conditions"
      "__VISION_NAV_FIELD_SELECTED_CASE__=${VISION_NAV_FIELD_CASE_NAME:-}"
      "__VISION_NAV_FIELD_SELECTED_LOG__=$field_log"
    )
    if [[ -n "${VISION_NAV_FIELD_METADATA_UPDATE_COMMAND:-}" ]]; then
      metadata_marker_lines+=("__VISION_NAV_FIELD_METADATA_UPDATE_COMMAND__=${VISION_NAV_FIELD_METADATA_UPDATE_COMMAND:-}")
    fi
    skip_step "register_field_replay_case" \
      "$metadata_message Complete the Field Evidence Case metadata in Module Setup, or run scripts/pi/update_field_capture_metadata.sh, then rerun the workflow or registration step." \
      "${metadata_marker_lines[@]}"
  fi
else
  skip_step "register_field_replay_case" "Set VISION_NAV_FIELD_CASE_NAME, VISION_NAV_FIELD_EXPECTED, and VISION_NAV_FIELD_CONDITION(S) after a real field log exists to register evidence."
fi

if [[ -f "$field_manifest" ]]; then
  run_step "refresh_field_collection_plan" ./scripts/pi/create_field_collection_plan.sh
else
  skip_step "refresh_field_collection_plan" "Missing field manifest, so the workflow cannot refresh field_collection_plan.json after capture or registration: $field_manifest"
fi

if [[ "$field_log_ready" == "1" && -e "$bundle" ]]; then
  VISION_NAV_FEATURE_BENCH_ALLOW_FAILED=1 run_step "run_feature_method_benchmark" ./scripts/pi/run_feature_method_benchmark.sh
elif [[ "$field_log_ready" != "1" ]]; then
  skip_step "run_feature_method_benchmark" "Terrain log was not validated in this workflow run. Expected a parseable log with accepted/rejected/degraded match statuses: $field_log"
else
  skip_step "run_feature_method_benchmark" "Missing replay log or bundle. Expected log: $field_log ; bundle: $bundle"
fi

if [[ -f "$field_manifest" ]]; then
  VISION_NAV_THRESHOLD_ALLOW_FAILED=1 run_step "run_threshold_tuning_report" ./scripts/pi/run_threshold_tuning_report.sh
else
  skip_step "run_threshold_tuning_report" "Missing field manifest: $field_manifest"
fi

if [[ "$field_log_ready" == "1" ]]; then
  (
    export VISION_NAV_PYTHON="$venv_python"
    export VISION_NAV_ROSBAG_SOURCE_LOG="$field_log"
    export VISION_NAV_ROSBAG_EXPORT_DIR="$rosbag_export_dir"
    export VISION_NAV_ROSBAG_EXPORT_VALIDATION="$rosbag_export_validation"
    run_step "validate_rosbag_export" ./scripts/pi/run_rosbag_export_validation.sh
  )
else
  skip_step "validate_rosbag_export" "Terrain log was not validated in this workflow run; ROS replay export waits for a parseable terrain match log: $field_log"
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
