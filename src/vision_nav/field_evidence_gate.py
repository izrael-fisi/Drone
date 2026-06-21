from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from vision_nav.replay_case_manifest import evaluate_replay_case_manifest
from vision_nav.replay_dataset_audit import audit_replay_dataset_coverage


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate real field replay evidence for terrain vision navigation.")
    parser.add_argument("--manifest", required=True, help="Replay case manifest JSON with real field log paths.")
    parser.add_argument("--output", help="Optional JSON report output path.")
    parser.add_argument(
        "--case-output-dir",
        help="Optional directory for per-case replay gate reports. Defaults next to --output when set.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON only.")
    return parser.parse_args()


def evaluate_field_evidence_gate(
    manifest_path: str | Path,
    *,
    output_path: str | Path | None = None,
    case_output_dir: str | Path | None = None,
) -> dict[str, Any]:
    manifest = Path(manifest_path).expanduser()
    case_output = resolve_case_output_dir(output_path, case_output_dir)
    coverage = audit_replay_dataset_coverage(
        manifest,
        require_field_logs=True,
        require_log_exists=True,
    )
    replay = evaluate_replay_case_manifest(manifest, output_dir=case_output)
    report = {
        "status": combined_status(coverage, replay),
        "manifest_path": str(manifest),
        "coverage": coverage,
        "replay_gates": replay,
        "summary": {
            "coverage_status": coverage.get("status"),
            "replay_status": replay.get("status"),
            "required_conditions": [
                requirement.get("key")
                for requirement in coverage.get("requirements", [])
                if isinstance(requirement, dict)
            ],
            "covered_conditions": [
                requirement.get("key")
                for requirement in coverage.get("requirements", [])
                if isinstance(requirement, dict) and requirement.get("status") == "covered"
            ],
            "case_count": replay.get("case_count"),
            "field_case_count": coverage.get("field_case_count"),
        },
    }
    if output_path is not None:
        destination = Path(output_path).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        report["output_path"] = str(destination)
    return report


def resolve_case_output_dir(output_path: str | Path | None, case_output_dir: str | Path | None) -> Path | None:
    if case_output_dir is not None:
        return Path(case_output_dir).expanduser()
    if output_path is None:
        return None
    return Path(output_path).expanduser().parent / "field_evidence_cases"


def combined_status(coverage: dict[str, Any], replay: dict[str, Any]) -> str:
    if coverage.get("status") != "passed" or replay.get("status") != "passed":
        return "failed"
    return "passed"


def print_human(report: dict[str, Any]) -> None:
    print(f"Field evidence gate: {report['manifest_path']}")
    print(f"Status: {report['status']}")
    summary = report.get("summary") or {}
    print(f"Coverage: {summary.get('coverage_status')} ({len(summary.get('covered_conditions') or [])}/{len(summary.get('required_conditions') or [])})")
    print(f"Replay gates: {summary.get('replay_status')} ({summary.get('case_count')} case(s))")
    coverage = report.get("coverage") or {}
    for requirement in coverage.get("requirements") or []:
        print(f"- {requirement.get('key')}: {requirement.get('status')}")
    replay = report.get("replay_gates") or {}
    for case in replay.get("reports") or []:
        metrics = case.get("metrics") or {}
        accepted_rate = metrics.get("accepted_rate")
        rate_text = f"{float(accepted_rate):.3f}" if accepted_rate is not None else "n/a"
        print(f"  {case.get('case_name')}: {case.get('status')} accepted={rate_text}")


def main() -> None:
    args = parse_args()
    report = evaluate_field_evidence_gate(
        args.manifest,
        output_path=args.output,
        case_output_dir=args.case_output_dir,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human(report)
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
