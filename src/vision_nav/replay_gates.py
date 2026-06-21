from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
from statistics import mean
from typing import Any

from vision_nav.summarize_match_log import load_records


@dataclass(frozen=True)
class ReplayGateConfig:
    min_good_accepted_rate: float = 0.5
    max_wrong_map_accepted_rate: float = 0.0
    max_degraded_accepted_rate: float = 0.4
    min_confidence: float = 0.55
    min_inliers: int = 18
    max_reprojection_error_px: float = 5.0
    min_scale_confidence: float = 0.35
    max_covariance_sigma_xy_m: float = 12.0


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate replay logs against vision-navigation acceptance gates.")
    parser.add_argument("--log", required=True, help="Runtime/replay JSONL log to evaluate.")
    parser.add_argument("--case-name", default="replay-case", help="Human-readable case name.")
    parser.add_argument(
        "--expected",
        choices=["good_map", "degraded", "wrong_map"],
        required=True,
        help="Expected behavior for this replay case.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON only.")
    parser.add_argument("--min-good-accepted-rate", type=float, default=ReplayGateConfig.min_good_accepted_rate)
    parser.add_argument("--max-wrong-map-accepted-rate", type=float, default=ReplayGateConfig.max_wrong_map_accepted_rate)
    parser.add_argument("--max-degraded-accepted-rate", type=float, default=ReplayGateConfig.max_degraded_accepted_rate)
    parser.add_argument("--min-confidence", type=float, default=ReplayGateConfig.min_confidence)
    parser.add_argument("--min-inliers", type=int, default=ReplayGateConfig.min_inliers)
    parser.add_argument("--max-reprojection-error-px", type=float, default=ReplayGateConfig.max_reprojection_error_px)
    parser.add_argument("--min-scale-confidence", type=float, default=ReplayGateConfig.min_scale_confidence)
    parser.add_argument("--max-covariance-sigma-xy-m", type=float, default=ReplayGateConfig.max_covariance_sigma_xy_m)
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
    )


def result_from_record(record: dict[str, Any]) -> dict[str, Any]:
    result = record.get("result")
    return result if isinstance(result, dict) else record


def covariance_sigma_xy_m(result: dict[str, Any]) -> float | None:
    measurement = result.get("measurement") or {}
    covariance = measurement.get("covariance") or result.get("covariance") or {}
    if covariance.get("sigma_xy_m") is not None:
        return float(covariance["sigma_xy_m"])
    x_m2 = covariance.get("x_m2")
    y_m2 = covariance.get("y_m2")
    if x_m2 is None or y_m2 is None:
        return None
    return math.sqrt(max(float(x_m2) + float(y_m2), 0.0) / 2.0)


def numeric_values(results: list[dict[str, Any]], key: str) -> list[float]:
    values: list[float] = []
    for result in results:
        value = result.get(key)
        if value is not None:
            values.append(float(value))
    return values


def add_issue(issues: list[dict[str, str]], severity: str, message: str) -> None:
    issues.append({"severity": severity, "message": message})


def evaluate_replay_records(
    records: list[dict[str, Any]],
    *,
    case_name: str,
    expected: str,
    config: ReplayGateConfig | None = None,
) -> dict[str, Any]:
    config = config or ReplayGateConfig()
    issues: list[dict[str, str]] = []
    results = [result_from_record(record) for record in records]
    accepted = [result for result in results if result.get("status") == "accepted"]
    rejected = [result for result in results if result.get("status") == "rejected"]
    failed = [result for result in results if result.get("status") == "failed"]
    accepted_rate = len(accepted) / len(results) if results else 0.0

    confidences = numeric_values(accepted, "confidence")
    inliers = numeric_values(accepted, "inliers")
    reprojection_errors = numeric_values(accepted, "reprojection_error_px")
    scale_confidences = numeric_values(accepted, "scale_confidence")
    sigmas = [value for value in (covariance_sigma_xy_m(result) for result in accepted) if value is not None]

    if not results:
        add_issue(issues, "error", "Replay log has no records.")

    if expected == "good_map":
        if accepted_rate < config.min_good_accepted_rate:
            add_issue(
                issues,
                "error",
                f"Good-map accepted rate {accepted_rate:.3f} is below {config.min_good_accepted_rate:.3f}.",
            )
        for index, result in enumerate(accepted, start=1):
            confidence = float(result.get("confidence", 0.0))
            inlier_count = int(result.get("inliers", 0))
            reprojection_error = result.get("reprojection_error_px")
            scale_confidence = float(result.get("scale_confidence", 1.0))
            sigma_xy = covariance_sigma_xy_m(result)
            if confidence < config.min_confidence:
                add_issue(issues, "error", f"Accepted record {index} confidence {confidence:.3f} is below threshold.")
            if inlier_count < config.min_inliers:
                add_issue(issues, "error", f"Accepted record {index} inliers {inlier_count} is below threshold.")
            if reprojection_error is not None and float(reprojection_error) > config.max_reprojection_error_px:
                add_issue(issues, "error", f"Accepted record {index} reprojection error is too high.")
            if scale_confidence < config.min_scale_confidence:
                add_issue(issues, "error", f"Accepted record {index} scale confidence is too low.")
            if sigma_xy is not None and sigma_xy > config.max_covariance_sigma_xy_m:
                add_issue(issues, "error", f"Accepted record {index} covariance sigma is too high.")
    elif expected == "wrong_map":
        if accepted_rate > config.max_wrong_map_accepted_rate:
            add_issue(
                issues,
                "error",
                f"Wrong-map accepted rate {accepted_rate:.3f} exceeds {config.max_wrong_map_accepted_rate:.3f}.",
            )
        for index, result in enumerate(accepted, start=1):
            sigma_xy = covariance_sigma_xy_m(result)
            add_issue(
                issues,
                "error",
                f"Wrong-map record {index} was accepted with confidence {float(result.get('confidence', 0.0)):.3f}.",
            )
            if sigma_xy is not None and sigma_xy <= config.max_covariance_sigma_xy_m:
                add_issue(issues, "error", f"Wrong-map record {index} produced low covariance sigma {sigma_xy:.3f} m.")
    elif expected == "degraded":
        if accepted_rate > config.max_degraded_accepted_rate:
            add_issue(
                issues,
                "error",
                f"Degraded-case accepted rate {accepted_rate:.3f} exceeds {config.max_degraded_accepted_rate:.3f}.",
            )
        confident = [result for result in accepted if float(result.get("confidence", 0.0)) >= config.min_confidence]
        if confident:
            add_issue(issues, "error", f"Degraded case has {len(confident)} high-confidence accepted record(s).")
    else:
        add_issue(issues, "error", f"Unsupported expected behavior: {expected}")

    status = "failed" if any(issue["severity"] == "error" for issue in issues) else "passed"
    return {
        "case_name": case_name,
        "expected": expected,
        "status": status,
        "config": asdict(config),
        "metrics": {
            "total_records": len(results),
            "accepted_records": len(accepted),
            "rejected_records": len(rejected),
            "failed_records": len(failed),
            "accepted_rate": accepted_rate,
            "confidence_mean": mean(confidences) if confidences else None,
            "inliers_min": min(inliers) if inliers else None,
            "reprojection_error_max_px": max(reprojection_errors) if reprojection_errors else None,
            "scale_confidence_min": min(scale_confidences) if scale_confidences else None,
            "covariance_sigma_xy_max_m": max(sigmas) if sigmas else None,
        },
        "issues": issues,
    }


def evaluate_replay_log(
    log_path: str | Path,
    *,
    case_name: str,
    expected: str,
    config: ReplayGateConfig | None = None,
) -> dict[str, Any]:
    path = Path(log_path).expanduser()
    report = evaluate_replay_records(load_records(path), case_name=case_name, expected=expected, config=config)
    report["log_path"] = str(path)
    return report


def print_human(report: dict[str, Any]) -> None:
    metrics = report["metrics"]
    print(f"Replay gate: {report['case_name']}")
    print(f"Expected: {report['expected']}")
    print(f"Status: {report['status']}")
    print(f"Records: {metrics['total_records']}")
    print(f"Accepted rate: {metrics['accepted_rate']:.3f}")
    for issue in report["issues"]:
        print(f"[{issue['severity'].upper()}] {issue['message']}")


def main() -> None:
    args = parse_args()
    report = evaluate_replay_log(
        args.log,
        case_name=args.case_name,
        expected=args.expected,
        config=config_from_args(args),
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human(report)
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
