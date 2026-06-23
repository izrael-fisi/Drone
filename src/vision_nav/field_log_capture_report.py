from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from vision_nav.summarize_match_log import load_records


SCHEMA_VERSION = "vision_nav_desktop_field_log_capture_v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a durable field log capture audit report.")
    parser.add_argument("--log", required=True, help="Captured terrain_matches.jsonl path.")
    parser.add_argument("--runtime-status", required=True, help="Companion runtime_status.json path.")
    parser.add_argument("--output", required=True, help="JSON report output path.")
    parser.add_argument("--bundle", help="Mission bundle used for capture.")
    parser.add_argument("--capture-output-dir", help="Capture output directory.")
    parser.add_argument("--condition", help="Field condition tag.")
    parser.add_argument("--case-name", help="Field replay case name.")
    parser.add_argument("--expected", help="Expected replay behavior.")
    parser.add_argument("--conditions", help="All condition tags.")
    parser.add_argument("--preflight", help="Optional field_capture_preflight.json path.")
    parser.add_argument("--command-source", default="pi terrain nav loop wrapper")
    parser.add_argument("--command", help="Command or wrapper that produced the capture.")
    parser.add_argument("--exit-code", type=int, default=0)
    parser.add_argument("--json", action="store_true", help="Print the full JSON report.")
    return parser.parse_args()


def create_field_log_capture_report(
    *,
    log_path: str | Path,
    runtime_status_path: str | Path,
    output_path: str | Path,
    bundle: str | None = None,
    capture_output_dir: str | None = None,
    condition: str | None = None,
    case_name: str | None = None,
    expected: str | None = None,
    conditions: str | None = None,
    preflight_path: str | Path | None = None,
    command_source: str = "pi terrain nav loop wrapper",
    command: str | None = None,
    exit_code: int = 0,
) -> dict[str, Any]:
    log = Path(log_path).expanduser()
    runtime = Path(runtime_status_path).expanduser()
    output = Path(output_path).expanduser()
    issues: list[str] = []
    record_count = 0
    status_counts: dict[str, int] = {}

    if log.exists():
        try:
            records = load_records(log)
            record_count = len(records)
            for record in records:
                result = record.get("result") if isinstance(record, dict) else None
                if not isinstance(result, dict):
                    result = record if isinstance(record, dict) else {}
                status = str(result.get("status") or "unknown")
                status_counts[status] = status_counts.get(status, 0) + 1
            if record_count == 0:
                issues.append("terrain log is empty")
        except Exception as exc:
            issues.append(f"terrain log is not parseable: {exc}")
    else:
        issues.append(f"terrain log is missing: {log}")

    runtime_status = read_runtime_status(runtime, issues)
    preflight = read_preflight(preflight_path)
    metadata_ready = None
    metadata_issues: list[str] = []
    if preflight is not None:
        metadata_ready = preflight.get("ready_for_registration") is True
        if metadata_ready is False:
            metadata_issues.append("capture metadata or registration inputs are incomplete")

    status = "passed"
    if exit_code != 0 or issues:
        status = "failed"
    elif runtime_status is None:
        status = "degraded"

    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "status": status,
        "host": None,
        "device_name": None,
        "command_source": command_source,
        "command": command,
        "exit_code": int(exit_code),
        "field_case": {
            "case_name": case_name,
            "expected": expected,
            "condition": condition,
            "conditions": conditions or condition,
            "field_log": str(log),
            "capture_output_dir": capture_output_dir or str(log.parent),
            "runtime_status_path": str(runtime),
            "site_name": None,
            "metadata_ready": metadata_ready,
            "metadata_issues": metadata_issues,
        },
        "preflight": compact_preflight(preflight),
        "artifacts": {
            "remote_terrain_log": str(log),
            "remote_runtime_status": str(runtime),
            "local_terrain_log": None,
            "local_runtime_status": None,
        },
        "next_actions": {
            "metadata_update_command": None,
            "register_command": None,
            "registration_ready": metadata_ready is True,
        },
        "runtime_status": compact_runtime_status(runtime_status),
        "summary": {
            "record_count": record_count,
            "status_counts": dict(sorted(status_counts.items())),
            "bundle": bundle,
            "issues": issues,
        },
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def read_runtime_status(path: Path, issues: list[str]) -> dict[str, Any] | None:
    if not path.exists():
        issues.append(f"runtime status is missing: {path}")
        return None
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(raw, dict):
            raise ValueError("runtime status root is not a JSON object")
        return raw
    except Exception as exc:
        issues.append(f"runtime status is not parseable: {exc}")
        return None


def read_preflight(path: str | Path | None) -> dict[str, Any] | None:
    if not path:
        return None
    candidate = Path(path).expanduser()
    if not candidate.exists():
        return None
    try:
        raw = json.loads(candidate.read_text(encoding="utf-8"))
        if isinstance(raw, dict) and raw.get("schema_version") == "vision_nav_field_capture_preflight_v1":
            return raw
    except Exception:
        return None
    return None


def compact_preflight(preflight: dict[str, Any] | None) -> dict[str, Any] | None:
    if preflight is None:
        return None
    return {
        "status": preflight.get("status"),
        "condition": preflight.get("condition"),
        "ready_for_capture": preflight.get("ready_for_capture"),
        "ready_for_registration": preflight.get("ready_for_registration"),
        "bundle_path": preflight.get("bundle_path"),
        "capture_output_dir": preflight.get("capture_output_dir"),
        "source_log": preflight.get("source_log"),
        "runtime_status_path": preflight.get("runtime_status_path"),
        "capture_script_path": preflight.get("capture_script_path"),
    }


def compact_runtime_status(status: dict[str, Any] | None) -> dict[str, Any] | None:
    if status is None:
        return None
    return {
        "schema_version": status.get("schema_version"),
        "updated_at_utc": status.get("updated_at_utc"),
        "active_map": status.get("active_map"),
        "output": status.get("output"),
        "last_match": status.get("last_match"),
        "estimator": status.get("estimator"),
        "external_position_health": status.get("external_position_health") or status.get("external_position"),
        "status_counts": status.get("status_counts"),
    }


def print_human(report: dict[str, Any], output_path: str | Path) -> None:
    summary = report.get("summary") or {}
    print(f"Field log capture report: {report.get('status')}")
    print(f"Report: {output_path}")
    print(f"Log: {(report.get('artifacts') or {}).get('remote_terrain_log')}")
    print(f"Runtime status: {(report.get('artifacts') or {}).get('remote_runtime_status')}")
    print(f"Records: {summary.get('record_count') or 0}")
    for issue in summary.get("issues") or []:
        print(f"[ISSUE] {issue}")


def main() -> None:
    args = parse_args()
    report = create_field_log_capture_report(
        log_path=args.log,
        runtime_status_path=args.runtime_status,
        output_path=args.output,
        bundle=args.bundle,
        capture_output_dir=args.capture_output_dir,
        condition=args.condition,
        case_name=args.case_name,
        expected=args.expected,
        conditions=args.conditions,
        preflight_path=args.preflight,
        command_source=args.command_source,
        command=args.command,
        exit_code=args.exit_code,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human(report, args.output)
        print(f"__VISION_NAV_FIELD_LOG_CAPTURE_REPORT__={Path(args.output).expanduser()}")
    if report["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
