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
bundle="${VISION_NAV_BUNDLE:-$HOME/drone-data/map_bundles/mission_bundle}"
steps_jsonl=""

export VISION_NAV_FIELD_COLLECTION_PLAN="$field_collection_plan"
export VISION_NAV_FIELD_COLLECTION_PLAN_MD="$field_collection_plan_md"

usage() {
  cat >&2 <<EOF
Usage:
  ./scripts/pi/run_autonomy_evidence_workflow.sh

This wrapper attempts the ordered evidence collection path:
  1. create/seed the field evidence template
  2. optionally register a field replay case when VISION_NAV_FIELD_CASE_NAME,
     VISION_NAV_FIELD_EXPECTED, and VISION_NAV_FIELD_CONDITION(S) are provided
  3. run feature-method benchmark when a replay log exists
  4. run threshold tuning when a field manifest exists
  5. create a support bundle
  6. run the strict autonomy-readiness audit and evidence package

Common optional overrides:
  VISION_NAV_EVIDENCE_WORKFLOW_REPORT     Default: $report
  VISION_NAV_EVIDENCE_WORKFLOW_LOG_ARCHIVE Default: $log_archive
  VISION_NAV_EVIDENCE_WORKFLOW_VALIDATION Default: $validation_report
  VISION_NAV_EVIDENCE_WORKFLOW_ALLOW_FAILED=0  Exit nonzero when any required step fails
  VISION_NAV_FIELD_CASE_NAME / VISION_NAV_FIELD_EXPECTED / VISION_NAV_FIELD_CONDITION
  VISION_NAV_FIELD_COLLECTION_PLAN       Default: $field_collection_plan
  VISION_NAV_FIELD_COLLECTION_PLAN_MD    Default: $field_collection_plan_md
  VISION_NAV_FIELD_LOG                    Default: $field_log
  VISION_NAV_BUNDLE                       Default: $bundle
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
  printf '%s\n' "$notes" >"$log_path"
  echo
  echo "== $name =="
  echo "skipped: $notes"
  record_step "$name" "skipped" 0 "$log_path" "$notes"
}

if [[ -f "$field_template" && -f "$field_manifest" && "${VISION_NAV_EVIDENCE_WORKFLOW_REFRESH_TEMPLATE:-0}" != "1" ]]; then
  skip_step "create_field_evidence_template" "Field template and active manifest already exist. Set VISION_NAV_EVIDENCE_WORKFLOW_REFRESH_TEMPLATE=1 and template force variables if you need to regenerate them."
else
  run_step "create_field_evidence_template" ./scripts/pi/create_field_evidence_template.sh
fi

run_step "create_field_collection_plan" ./scripts/pi/create_field_collection_plan.sh

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

run_step "create_support_bundle" ./scripts/pi/create_support_bundle.sh
VISION_NAV_AUTONOMY_ALLOW_FAILED=1 run_step "run_autonomy_readiness_audit" ./scripts/pi/run_autonomy_readiness_audit.sh

final_status="passed"
if grep -q '"status": "failed"' "$steps_jsonl"; then
  final_status="failed"
elif grep -q '"status": "skipped"' "$steps_jsonl"; then
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
