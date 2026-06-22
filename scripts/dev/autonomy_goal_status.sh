#!/usr/bin/env bash
set -euo pipefail

repo_root="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
python_bin="${VISION_NAV_PYTHON:-python3}"
json_copy="${VISION_NAV_AUTONOMY_GOAL_STATUS_JSON:-}"
quiet_exit="${VISION_NAV_AUTONOMY_GOAL_STATUS_QUIET_EXIT:-0}"
tmp_dir="$(mktemp -d "${TMPDIR:-/tmp}/vision-nav-goal-status.XXXXXX")"
tmp_report="$tmp_dir/report.json"
trap 'rm -rf "$tmp_dir"' EXIT

set +e
PYTHONPATH="$repo_root/src" "$python_bin" -m vision_nav.autonomy_readiness --json >"$tmp_report"
audit_status=$?
set -e

if [[ -n "$json_copy" ]]; then
  mkdir -p "$(dirname "$json_copy")"
  cp "$tmp_report" "$json_copy"
fi

"$python_bin" - "$tmp_report" <<'PY'
import json
import sys

report_path = sys.argv[1]
with open(report_path, "r", encoding="utf-8") as handle:
    report = json.load(handle)

summary = report.get("summary") or {}
evidence = report.get("evidence_manifest") or {}
proof_items = evidence.get("proof_items") or []
external_blockers = evidence.get("external_blockers") or []
completion_blockers = evidence.get("completion_blockers") or []
runbook = report.get("proof_runbook") or {}
runbook_summary = runbook.get("summary") or {}
metadata = report.get("metadata") or {}
repo = metadata.get("repo") or {}

passed = sum(1 for item in proof_items if item.get("status") == "passed")
print(f"Autonomy goal status: {report.get('status', 'unknown')}")
if repo.get("detected"):
    dirty = "dirty" if repo.get("dirty") else "clean"
    print(f"Repo: {repo.get('branch', 'unknown')} @ {str(repo.get('commit') or '')[:8]} ({dirty})")
print(
    "Checks: "
    f"{summary.get('passed', 0)} passed, "
    f"{summary.get('degraded', 0)} degraded, "
    f"{summary.get('failed', 0)} failed"
)
print(f"Proof items: {passed}/{len(proof_items)} passed")
print(f"Completion blockers: {len(completion_blockers)}")
print(f"External proof blockers: {len(external_blockers)}")
if runbook_summary:
    print(
        "Proof runbook: "
        f"{runbook_summary.get('passed', 0)} passed, "
        f"{runbook_summary.get('action_required', 0)} action-required, "
        f"{runbook_summary.get('blocked', 0)} blocked"
    )

if external_blockers:
    print()
    print("External blockers:")
    for blocker in external_blockers[:12]:
        name = blocker.get("name") or "unknown"
        status = blocker.get("status") or "unknown"
        message = blocker.get("message") or ""
        print(f"- {name} [{status}]: {message}")
        missing = blocker.get("missing_conditions") or []
        if missing:
            visible = ", ".join(str(item) for item in missing[:8])
            extra = "" if len(missing) <= 8 else f" +{len(missing) - 8}"
            print(f"  missing conditions: {visible}{extra}")
    if len(external_blockers) > 12:
        print(f"- ... {len(external_blockers) - 12} more")

next_actions = report.get("next_actions") or []
if next_actions:
    print()
    print("Next commands:")
    seen = set()
    count = 0
    for action in next_actions:
        command = action.get("command")
        if not command or command in seen:
            continue
        seen.add(command)
        count += 1
        title = action.get("title") or action.get("check") or "next action"
        print(f"{count}. {title}")
        print(f"   {command}")
        if count >= 8:
            break
PY

if [[ "$audit_status" -ne 0 ]]; then
  if [[ "$quiet_exit" != "1" && "$quiet_exit" != "true" ]]; then
    echo
    echo "Autonomy goal is not complete yet; run the commands above to collect the missing proof artifacts." >&2
  fi
  exit "$audit_status"
fi
