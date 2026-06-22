from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
from statistics import mean
from typing import Any

from vision_nav.replay_case_manifest import evaluate_replay_case_manifest
from vision_nav.replay_dataset_audit import audit_replay_dataset_coverage
from vision_nav.replay_gates import ReplayGateConfig


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Generate a replay-gate threshold tuning report from field replay cases.")
    parser.add_argument("--manifest", required=True, help="Replay case manifest JSON.")
    parser.add_argument("--output", help="Optional JSON report output path.")
    parser.add_argument("--case-output-dir", help="Optional directory for per-case gate reports.")
    parser.add_argument("--json", action="store_true", help="Emit JSON only.")
    parser.add_argument(
        "--allow-synthetic",
        action="store_true",
        help="Allow synthetic or bench logs to satisfy coverage. Use only for tooling smoke tests.",
    )
    parser.add_argument("--min-good-accepted-rate", type=float, default=ReplayGateConfig.min_good_accepted_rate)
    parser.add_argument("--max-wrong-map-accepted-rate", type=float, default=ReplayGateConfig.max_wrong_map_accepted_rate)
    parser.add_argument("--max-degraded-accepted-rate", type=float, default=ReplayGateConfig.max_degraded_accepted_rate)
    parser.add_argument("--min-confidence", type=float, default=ReplayGateConfig.min_confidence)
    parser.add_argument("--min-inliers", type=int, default=ReplayGateConfig.min_inliers)
    parser.add_argument("--max-reprojection-error-px", type=float, default=ReplayGateConfig.max_reprojection_error_px)
    parser.add_argument("--min-scale-confidence", type=float, default=ReplayGateConfig.min_scale_confidence)
    parser.add_argument("--max-covariance-sigma-xy-m", type=float, default=ReplayGateConfig.max_covariance_sigma_xy_m)
    parser.add_argument(
        "--min-weak-match-covariance-sigma-xy-m",
        type=float,
        default=ReplayGateConfig.min_weak_match_covariance_sigma_xy_m,
    )
    parser.add_argument("--max-motion-jump-m", type=float, default=ReplayGateConfig.max_motion_jump_m)
    parser.add_argument("--max-motion-speed-mps", type=float, default=ReplayGateConfig.max_motion_speed_mps)
    return parser.parse_args()


def config_from_args(args: argparse.Namespace) -> ReplayGateConfig:
    return ReplayGateConfig(
        min_good_accepted_rate=args.min_good_accepted_rate,
        max_wrong_map_accepted_rate=args.max_wrong_map_accepted_rate,
        max_degraded_accepted_rate=args.max_degraded_accepted_rate,
        min_confidence=args.min_confidence,
        min_inliers=args.min_inliers,
        max_reprojection_error_px=args.max_reprojection_error_px,
        min_scale_confidence=args.min_scale_confidence,
        max_covariance_sigma_xy_m=args.max_covariance_sigma_xy_m,
        min_weak_match_covariance_sigma_xy_m=args.min_weak_match_covariance_sigma_xy_m,
        max_motion_jump_m=args.max_motion_jump_m,
        max_motion_speed_mps=args.max_motion_speed_mps,
    )


def evaluate_threshold_tuning(
    manifest_path: str | Path,
    *,
    output_path: str | Path | None = None,
    case_output_dir: str | Path | None = None,
    config: ReplayGateConfig | None = None,
    require_field_logs: bool = True,
) -> dict[str, Any]:
    config = config or ReplayGateConfig()
    manifest = Path(manifest_path).expanduser()
    coverage = audit_replay_dataset_coverage(
        manifest,
        require_field_logs=require_field_logs,
        require_log_exists=True,
        require_capture_metadata=require_field_logs,
    )
    replay = evaluate_replay_case_manifest(
        manifest,
        output_dir=resolve_case_output_dir(output_path, case_output_dir),
        config=config,
    )
    covered_conditions = [
        requirement.get("key")
        for requirement in coverage.get("requirements", [])
        if isinstance(requirement, dict) and requirement.get("status") == "covered"
    ]
    report = {
        "status": combined_status(coverage, replay),
        "method": "field-replay-gate-threshold-audit",
        "manifest_path": str(manifest),
        "conditions": covered_conditions,
        "summary": {
            "coverage_status": coverage.get("status"),
            "replay_status": replay.get("status"),
            "case_count": replay.get("case_count"),
            "field_case_count": coverage.get("field_case_count"),
            "capture_metadata_issue_count": coverage.get("capture_metadata_issue_count"),
            "covered_conditions": covered_conditions,
            "tuned_conditions": covered_conditions,
        },
        "config": asdict(config),
        "metrics": summarize_gate_metrics(replay.get("reports") or [], config),
        "coverage": coverage,
        "replay_gates": replay,
    }
    report["recommendations"] = recommend_thresholds(report)
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
    return Path(output_path).expanduser().parent / "threshold_tuning_cases"


def combined_status(coverage: dict[str, Any], replay: dict[str, Any]) -> str:
    if coverage.get("status") != "passed" or replay.get("status") != "passed":
        return "failed"
    return "passed"


def summarize_gate_metrics(reports: list[dict[str, Any]], config: ReplayGateConfig) -> dict[str, Any]:
    by_expected: dict[str, dict[str, Any]] = {}
    for expected in ("good_map", "degraded", "wrong_map"):
        group = [report for report in reports if report.get("expected") == expected]
        accepted_rates = metric_values(group, "accepted_rate")
        by_expected[expected] = {
            "case_count": len(group),
            "passed_case_count": sum(1 for report in group if report.get("status") == "passed"),
            "accepted_rate_min": min(accepted_rates) if accepted_rates else None,
            "accepted_rate_max": max(accepted_rates) if accepted_rates else None,
            "accepted_rate_mean": mean(accepted_rates) if accepted_rates else None,
            "confidence_mean_min": metric_min(group, "confidence_mean"),
            "inliers_min": metric_min(group, "inliers_min"),
            "reprojection_error_max_px": metric_max(group, "reprojection_error_max_px"),
            "scale_confidence_min": metric_min(group, "scale_confidence_min"),
            "covariance_sigma_xy_max_m": metric_max(group, "covariance_sigma_xy_max_m"),
            "motion_jump_max_m": metric_max(group, "motion_jump_max_m"),
            "motion_speed_max_mps": metric_max(group, "motion_speed_max_mps"),
        }
    return {
        "by_expected": by_expected,
        "margins": {
            "good_map_accepted_rate": safe_subtract(by_expected["good_map"]["accepted_rate_min"], config.min_good_accepted_rate),
            "degraded_accepted_rate": safe_subtract(config.max_degraded_accepted_rate, by_expected["degraded"]["accepted_rate_max"]),
            "wrong_map_accepted_rate": safe_subtract(config.max_wrong_map_accepted_rate, by_expected["wrong_map"]["accepted_rate_max"]),
        },
    }


def metric_values(reports: list[dict[str, Any]], key: str) -> list[float]:
    values: list[float] = []
    for report in reports:
        metrics = report.get("metrics") or {}
        value = metrics.get(key)
        if value is not None:
            values.append(float(value))
    return values


def metric_min(reports: list[dict[str, Any]], key: str) -> float | None:
    values = metric_values(reports, key)
    return min(values) if values else None


def metric_max(reports: list[dict[str, Any]], key: str) -> float | None:
    values = metric_values(reports, key)
    return max(values) if values else None


def safe_subtract(left: Any, right: Any) -> float | None:
    if left is None or right is None:
        return None
    return float(left) - float(right)


def recommend_thresholds(report: dict[str, Any]) -> dict[str, Any]:
    status = report.get("status")
    recommendations = {
        "gate_config": report.get("config"),
        "notes": [],
    }
    notes = recommendations["notes"]
    if status != "passed":
        notes.append("Do not promote thresholds until coverage and replay gates both pass.")
    margins = ((report.get("metrics") or {}).get("margins") or {})
    for key, value in margins.items():
        if value is None:
            notes.append(f"{key} margin is unavailable because no matching cases were evaluated.")
        elif float(value) < 0.0:
            notes.append(f"{key} margin is negative; collect more data or adjust the gate before promotion.")
        elif float(value) < 0.05:
            notes.append(f"{key} margin is narrow; treat this threshold as provisional.")
    if not notes:
        notes.append("Current replay-gate thresholds pass the available field cases.")
    return recommendations


def print_human(report: dict[str, Any]) -> None:
    summary = report.get("summary") or {}
    print(f"Threshold tuning: {report['manifest_path']}")
    print(f"Status: {report['status']}")
    print(f"Coverage: {summary.get('coverage_status')} ({len(summary.get('covered_conditions') or [])} condition(s))")
    print(f"Replay gates: {summary.get('replay_status')} ({summary.get('case_count')} case(s))")
    for note in (report.get("recommendations") or {}).get("notes") or []:
        print(f"- {note}")


def main() -> None:
    args = parse_args()
    report = evaluate_threshold_tuning(
        args.manifest,
        output_path=args.output,
        case_output_dir=args.case_output_dir,
        config=config_from_args(args),
        require_field_logs=not args.allow_synthetic,
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human(report)
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
