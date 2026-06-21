from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from vision_nav.replay_case_schema import EXPECTED_BEHAVIORS, evaluate_replay_case_schema
from vision_nav.replay_gates import ReplayGateConfig, evaluate_replay_log


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Evaluate a replay-case manifest against vision-navigation gates."
    )
    parser.add_argument("--manifest", required=True, help="Replay case manifest JSON.")
    parser.add_argument("--output-dir", help="Directory for per-case gate reports and summary.json.")
    parser.add_argument(
        "--schema-only",
        action="store_true",
        help="Validate manifest schema without evaluating replay logs.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON only.")
    return parser.parse_args()


def sanitize_filename(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in value)
    return safe.strip("-") or "replay-case"


def load_replay_case_manifest(path: str | Path) -> dict[str, Any]:
    manifest_path = Path(path).expanduser()
    raw = json.loads(manifest_path.read_text())
    schema = evaluate_replay_case_schema(raw, manifest_path=manifest_path)
    if not isinstance(raw, dict):
        raw = {}
    cases: list[dict[str, Any]] = []
    for index, case in enumerate(raw.get("cases") or [], start=1):
        if not isinstance(case, dict):
            continue
        normalized = dict(case)
        normalized["case_name"] = str(normalized.get("case_name") or f"replay-case-{index}")
        normalized["expected"] = str(normalized.get("expected") or "")
        if normalized.get("log"):
            log_path = Path(str(normalized["log"])).expanduser()
            if not log_path.is_absolute():
                log_path = manifest_path.parent / log_path
            normalized["log"] = str(log_path)
        normalized["source"] = str(manifest_path)
        cases.append(normalized)
    return {
        "version": raw.get("version"),
        "manifest_path": str(manifest_path),
        "schema": schema,
        "cases": cases,
    }


def evaluate_replay_case_manifest(
    manifest_path: str | Path,
    *,
    output_dir: str | Path | None = None,
    config: ReplayGateConfig | None = None,
    schema_only: bool = False,
) -> dict[str, Any]:
    manifest = load_replay_case_manifest(manifest_path)
    reports: list[dict[str, Any]] = []
    output_path = Path(output_dir).expanduser() if output_dir is not None else None
    if output_path is not None:
        output_path.mkdir(parents=True, exist_ok=True)

    for case in [] if schema_only else manifest["cases"]:
        case_name = str(case.get("case_name") or "replay-case")
        expected = str(case.get("expected") or "")
        log_path = case.get("log")
        if expected not in EXPECTED_BEHAVIORS:
            report = {
                "case_name": case_name,
                "expected": expected,
                "status": "failed",
                "log_path": str(log_path) if log_path else None,
                "issues": [{"severity": "error", "message": f"Unsupported expected behavior: {expected}"}],
            }
        elif not log_path or not Path(str(log_path)).expanduser().exists():
            report = {
                "case_name": case_name,
                "expected": expected,
                "status": "failed",
                "log_path": str(log_path) if log_path else None,
                "issues": [{"severity": "error", "message": "Replay case log is missing."}],
            }
        else:
            report = evaluate_replay_log(
                str(log_path),
                case_name=case_name,
                expected=expected,
                config=config or ReplayGateConfig(),
            )
        report["notes"] = case.get("notes")
        report["source"] = case.get("source")
        if output_path is not None:
            report_path = output_path / f"{sanitize_filename(case_name)}.gate.json"
            report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
            report["report_path"] = str(report_path)
        reports.append(report)

    summary = {
        "manifest_path": manifest["manifest_path"],
        "version": manifest.get("version"),
        "schema": manifest.get("schema"),
        "case_count": len(manifest["cases"]),
        "schema_only": schema_only,
        "status": "failed"
        if any(report.get("status") == "failed" for report in reports)
        or (manifest.get("schema") or {}).get("status") == "failed"
        else "passed",
        "reports": reports,
    }
    if output_path is not None:
        summary_path = output_path / "summary.json"
        summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
        summary["summary_path"] = str(summary_path)
    return summary


def print_human(summary: dict[str, Any]) -> None:
    print(f"Replay manifest: {summary['manifest_path']}")
    print(f"Status: {summary['status']}")
    print(f"Cases: {summary['case_count']}")
    if summary.get("schema_only"):
        print("Mode: schema only")
    schema = summary.get("schema") or {}
    if schema.get("status") != "passed":
        print(f"Schema: {schema.get('status')} ({schema.get('issue_count', 0)} issues)")
        for issue in schema.get("issues") or []:
            print(f"  [{str(issue.get('severity', 'info')).upper()}] {issue.get('path')}: {issue.get('message')}")
    for report in summary["reports"]:
        metrics = report.get("metrics") or {}
        accepted_rate = metrics.get("accepted_rate")
        rate_text = f"{float(accepted_rate):.3f}" if accepted_rate is not None else "n/a"
        print(f"- {report['case_name']}: {report['status']} expected={report['expected']} accepted_rate={rate_text}")
        for issue in report.get("issues") or []:
            print(f"  [{str(issue.get('severity', 'info')).upper()}] {issue.get('message')}")


def main() -> None:
    args = parse_args()
    summary = evaluate_replay_case_manifest(
        args.manifest,
        output_dir=args.output_dir,
        schema_only=args.schema_only,
    )
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print_human(summary)
    if summary["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
