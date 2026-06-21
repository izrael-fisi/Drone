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
    min_weak_match_covariance_sigma_xy_m: float = 12.0
    max_motion_jump_m: float = 75.0
    max_motion_speed_mps: float = 35.0


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


def result_from_record(record: dict[str, Any]) -> dict[str, Any]:
    result = record.get("result")
    if not isinstance(result, dict):
        return record
    merged = dict(result)
    for key in ("timestamp_us", "sequence"):
        if key in record and key not in merged:
            merged[key] = record[key]
    return merged


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


def local_xy_m(result: dict[str, Any]) -> tuple[float, float] | None:
    local = result.get("local_enu_m")
    if not isinstance(local, dict):
        measurement = result.get("measurement")
        if isinstance(measurement, dict):
            local = measurement.get("local_enu_m")
    if not isinstance(local, dict):
        return None
    x = local.get("x")
    y = local.get("y")
    if x is None or y is None:
        return None
    return float(x), float(y)


def timestamp_s(result: dict[str, Any]) -> float | None:
    timestamp_us = result.get("timestamp_us")
    if timestamp_us is None:
        return None
    return float(timestamp_us) / 1_000_000.0


def accepted_motion_metrics(accepted: list[dict[str, Any]]) -> list[dict[str, float | None]]:
    samples = []
    for index, result in enumerate(accepted):
        xy = local_xy_m(result)
        if xy is None:
            continue
        samples.append({"index": float(index + 1), "x": xy[0], "y": xy[1], "timestamp_s": timestamp_s(result)})
    if len(samples) < 2:
        return []
    if all(sample["timestamp_s"] is not None for sample in samples):
        samples.sort(key=lambda sample: float(sample["timestamp_s"] or 0.0))

    motions: list[dict[str, float | None]] = []
    for previous, current in zip(samples, samples[1:]):
        jump_m = math.hypot(float(current["x"]) - float(previous["x"]), float(current["y"]) - float(previous["y"]))
        dt_s = None
        speed_mps = None
        if previous["timestamp_s"] is not None and current["timestamp_s"] is not None:
            dt_s = max(float(current["timestamp_s"]) - float(previous["timestamp_s"]), 0.0)
            if dt_s > 0:
                speed_mps = jump_m / dt_s
        motions.append(
            {
                "from_index": previous["index"],
                "to_index": current["index"],
                "jump_m": jump_m,
                "dt_s": dt_s,
                "speed_mps": speed_mps,
            }
        )
    return motions


def add_accepted_motion_issues(
    issues: list[dict[str, str]],
    accepted: list[dict[str, Any]],
    config: ReplayGateConfig,
) -> list[dict[str, float | None]]:
    motions = accepted_motion_metrics(accepted)
    for motion in motions:
        jump_m = float(motion["jump_m"] or 0.0)
        speed_mps = motion["speed_mps"]
        if jump_m > config.max_motion_jump_m:
            add_issue(
                issues,
                "error",
                f"Accepted records {int(motion['from_index'] or 0)}->{int(motion['to_index'] or 0)} jump {jump_m:.1f} m exceeds motion gate.",
            )
        if speed_mps is not None and speed_mps > config.max_motion_speed_mps:
            add_issue(
                issues,
                "error",
                f"Accepted records {int(motion['from_index'] or 0)}->{int(motion['to_index'] or 0)} speed {speed_mps:.1f} m/s exceeds motion gate.",
            )
    return motions


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
    motions = add_accepted_motion_issues(issues, accepted, config) if expected in {"good_map", "degraded"} else []

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
            confidence_raw = result.get("confidence")
            inliers_raw = result.get("inliers")
            reprojection_error = result.get("reprojection_error_px")
            scale_confidence_raw = result.get("scale_confidence")
            sigma_xy = covariance_sigma_xy_m(result)
            if confidence_raw is None:
                add_issue(issues, "error", f"Accepted record {index} is missing confidence.")
            if inliers_raw is None:
                add_issue(issues, "error", f"Accepted record {index} is missing inlier count.")
            if reprojection_error is None:
                add_issue(issues, "error", f"Accepted record {index} is missing reprojection error.")
            if scale_confidence_raw is None:
                add_issue(issues, "error", f"Accepted record {index} is missing scale confidence.")
            if sigma_xy is None:
                add_issue(issues, "error", f"Accepted record {index} is missing XY covariance.")
            confidence = float(confidence_raw or 0.0)
            inlier_count = int(inliers_raw or 0)
            scale_confidence = float(scale_confidence_raw or 0.0)
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
        for index, result in enumerate(accepted, start=1):
            confidence = float(result.get("confidence", 0.0))
            scale_confidence = float(result.get("scale_confidence", 0.0))
            sigma_xy = covariance_sigma_xy_m(result)
            if sigma_xy is None:
                add_issue(issues, "error", f"Degraded accepted record {index} is missing XY covariance.")
            elif (
                confidence < config.min_confidence or scale_confidence < config.min_scale_confidence
            ) and sigma_xy < config.min_weak_match_covariance_sigma_xy_m:
                add_issue(
                    issues,
                    "error",
                    f"Degraded accepted record {index} covariance sigma {sigma_xy:.3f} m was not inflated for a weak match.",
                )
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
            "motion_jump_max_m": max((float(motion["jump_m"] or 0.0) for motion in motions), default=None),
            "motion_speed_max_mps": max(
                (float(motion["speed_mps"]) for motion in motions if motion["speed_mps"] is not None),
                default=None,
            ),
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
