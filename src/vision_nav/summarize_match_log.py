from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from statistics import mean, median
from typing import Any


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Summarize vision navigation JSONL match logs.")
    parser.add_argument("logs", nargs="+", help="One or more matches.jsonl/replay_matches.jsonl files.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    return parser.parse_args()


def numeric_summary(values: list[float]) -> dict[str, float] | None:
    if not values:
        return None
    sorted_values = sorted(values)
    return {
        "min": float(sorted_values[0]),
        "mean": float(mean(sorted_values)),
        "median": float(median(sorted_values)),
        "max": float(sorted_values[-1]),
    }


def load_records(log_path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with log_path.expanduser().open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{log_path}:{line_number}: invalid JSONL record") from exc
    return records


def summarize_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    statuses: Counter[str] = Counter()
    reasons: Counter[str] = Counter()
    confidences: list[float] = []
    position_confidences: list[float] = []
    georef_confidences: list[float] = []
    inliers: list[float] = []
    inlier_ratios: list[float] = []
    reprojection_errors: list[float] = []
    geometry_scale_values: list[float] = []
    geometry_rotation_values: list[float] = []
    geometry_anisotropy_values: list[float] = []
    geometry_perspective_values: list[float] = []
    capture_durations: list[float] = []
    match_durations: list[float] = []
    sharpness_values: list[float] = []
    entropy_values: list[float] = []
    covariance_sigma_values: list[float] = []
    latitudes: list[float] = []
    longitudes: list[float] = []
    external_position_statuses: Counter[str] = Counter()
    external_position_messages: Counter[str] = Counter()
    external_position_skip_reasons: Counter[str] = Counter()
    external_position_warnings: Counter[str] = Counter()
    external_position_rates: list[float] = []
    external_position_latencies_ms: list[float] = []
    mavlink_reset_counters: list[float] = []

    for record in records:
        result = record.get("result") or {}
        status = str(result.get("status", "unknown"))
        statuses[status] += 1
        if result.get("reason"):
            reasons[str(result["reason"])] += 1

        for key, values in [
            ("confidence", confidences),
            ("position_confidence", position_confidences),
            ("inliers", inliers),
            ("inlier_ratio", inlier_ratios),
            ("reprojection_error_px", reprojection_errors),
        ]:
            value = result.get(key)
            if value is not None:
                values.append(float(value))

        map_georef = result.get("map_georef") or {}
        if map_georef.get("confidence") is not None:
            georef_confidences.append(float(map_georef["confidence"]))

        geometry = result.get("geometry") or {}
        if geometry.get("scale_mean") is not None:
            geometry_scale_values.append(float(geometry["scale_mean"]))
        if geometry.get("rotation_deg") is not None:
            geometry_rotation_values.append(float(geometry["rotation_deg"]))
        if geometry.get("scale_anisotropy") is not None:
            geometry_anisotropy_values.append(float(geometry["scale_anisotropy"]))
        if geometry.get("perspective_norm") is not None:
            geometry_perspective_values.append(float(geometry["perspective_norm"]))

        if record.get("capture_duration_s") is not None:
            capture_durations.append(float(record["capture_duration_s"]))
        if record.get("match_duration_s") is not None:
            match_durations.append(float(record["match_duration_s"]))

        quality = result.get("frame_quality") or {}
        if quality.get("sharpness_laplacian_var") is not None:
            sharpness_values.append(float(quality["sharpness_laplacian_var"]))
        if quality.get("entropy_bits") is not None:
            entropy_values.append(float(quality["entropy_bits"]))

        measurement = result.get("measurement") or {}
        covariance = measurement.get("covariance") or {}
        if covariance.get("sigma_xy_m") is not None:
            covariance_sigma_values.append(float(covariance["sigma_xy_m"]))

        position = result.get("estimated_position") or {}
        if position.get("latitude") is not None and position.get("longitude") is not None:
            latitudes.append(float(position["latitude"]))
            longitudes.append(float(position["longitude"]))

        external_health = record.get("external_position_health") or {}
        if external_health:
            external_position_statuses[str(external_health.get("status", "unknown"))] += 1
            external_position_messages[str(external_health.get("message_type", "unknown"))] += 1
            if external_health.get("send_rate_hz") is not None:
                external_position_rates.append(float(external_health["send_rate_hz"]))
            if external_health.get("last_latency_ms") is not None:
                external_position_latencies_ms.append(float(external_health["last_latency_ms"]))
            for reason, count in (external_health.get("skip_reasons") or {}).items():
                external_position_skip_reasons[str(reason)] += int(count)
            for warning in external_health.get("last_warnings") or []:
                external_position_warnings[str(warning)] += 1

        mavlink = record.get("mavlink") or {}
        mavlink_details = mavlink.get("details") or {}
        if mavlink_details.get("reset_counter") is not None:
            mavlink_reset_counters.append(float(mavlink_details["reset_counter"]))

    total = len(records)
    accepted = statuses.get("accepted", 0)
    summary: dict[str, Any] = {
        "total_records": total,
        "status_counts": dict(sorted(statuses.items())),
        "reason_counts": dict(sorted(reasons.items())),
        "accepted_rate": float(accepted / total) if total else 0.0,
        "confidence": numeric_summary(confidences),
        "position_confidence": numeric_summary(position_confidences),
        "georef_confidence": numeric_summary(georef_confidences),
        "inliers": numeric_summary(inliers),
        "inlier_ratio": numeric_summary(inlier_ratios),
        "reprojection_error_px": numeric_summary(reprojection_errors),
        "geometry_scale_mean": numeric_summary(geometry_scale_values),
        "geometry_rotation_deg": numeric_summary(geometry_rotation_values),
        "geometry_scale_anisotropy": numeric_summary(geometry_anisotropy_values),
        "geometry_perspective_norm": numeric_summary(geometry_perspective_values),
        "capture_duration_s": numeric_summary(capture_durations),
        "match_duration_s": numeric_summary(match_durations),
        "sharpness_laplacian_var": numeric_summary(sharpness_values),
        "entropy_bits": numeric_summary(entropy_values),
        "covariance_sigma_xy_m": numeric_summary(covariance_sigma_values),
        "external_position": {
            "status_counts": dict(sorted(external_position_statuses.items())),
            "message_counts": dict(sorted(external_position_messages.items())),
            "skip_reasons": dict(sorted(external_position_skip_reasons.items())),
            "warning_counts": dict(sorted(external_position_warnings.items())),
            "send_rate_hz": numeric_summary(external_position_rates),
            "latency_ms": numeric_summary(external_position_latencies_ms),
            "reset_counter": numeric_summary(mavlink_reset_counters),
            "last_reset_counter": int(mavlink_reset_counters[-1]) if mavlink_reset_counters else None,
        },
    }

    if latitudes and longitudes:
        summary["estimated_position"] = {
            "count": len(latitudes),
            "latitude_min": min(latitudes),
            "latitude_max": max(latitudes),
            "longitude_min": min(longitudes),
            "longitude_max": max(longitudes),
        }
    return summary


def summarize_log(path: str | Path) -> dict[str, Any]:
    log_path = Path(path).expanduser()
    records = load_records(log_path)
    summary = summarize_records(records)
    summary["log_path"] = str(log_path)
    return summary


def format_metric(name: str, values: dict[str, float] | None) -> str:
    if values is None:
        return f"{name}: n/a"
    return (
        f"{name}: min={values['min']:.3f} "
        f"mean={values['mean']:.3f} "
        f"median={values['median']:.3f} "
        f"max={values['max']:.3f}"
    )


def print_human(summary: dict[str, Any]) -> None:
    print(f"Log: {summary['log_path']}")
    print(f"Records: {summary['total_records']}")
    print(f"Statuses: {summary['status_counts']}")
    print(f"Reasons: {summary['reason_counts']}")
    print(f"Accepted rate: {summary['accepted_rate']:.3f}")
    print(format_metric("Confidence", summary["confidence"]))
    print(format_metric("Position confidence", summary["position_confidence"]))
    print(format_metric("Georef confidence", summary["georef_confidence"]))
    print(format_metric("Inliers", summary["inliers"]))
    print(format_metric("Inlier ratio", summary["inlier_ratio"]))
    print(format_metric("Reprojection error px", summary["reprojection_error_px"]))
    print(format_metric("Geometry scale mean", summary["geometry_scale_mean"]))
    print(format_metric("Geometry rotation deg", summary["geometry_rotation_deg"]))
    print(format_metric("Geometry scale anisotropy", summary["geometry_scale_anisotropy"]))
    print(format_metric("Geometry perspective norm", summary["geometry_perspective_norm"]))
    print(format_metric("Capture duration s", summary["capture_duration_s"]))
    print(format_metric("Match duration s", summary["match_duration_s"]))
    print(format_metric("Sharpness Laplacian var", summary["sharpness_laplacian_var"]))
    print(format_metric("Entropy bits", summary["entropy_bits"]))
    print(format_metric("Covariance sigma xy m", summary["covariance_sigma_xy_m"]))
    external_position = summary.get("external_position") or {}
    if external_position.get("status_counts"):
        print(f"External position statuses: {external_position['status_counts']}")
        print(f"External position messages: {external_position['message_counts']}")
        print(f"External position skip reasons: {external_position['skip_reasons']}")
        print(f"External position warnings: {external_position['warning_counts']}")
        print(format_metric("External position send rate hz", external_position["send_rate_hz"]))
        print(format_metric("External position latency ms", external_position["latency_ms"]))
        print(format_metric("External position reset counter", external_position["reset_counter"]))
        if external_position.get("last_reset_counter") is not None:
            print(f"External position last reset counter: {external_position['last_reset_counter']}")
    position = summary.get("estimated_position")
    if position:
        print(
            "Estimated positions: "
            f"count={position['count']} "
            f"lat=[{position['latitude_min']:.7f}, {position['latitude_max']:.7f}] "
            f"lon=[{position['longitude_min']:.7f}, {position['longitude_max']:.7f}]"
        )


def main() -> None:
    args = parse_args()
    summaries = [summarize_log(path) for path in args.logs]
    if args.json:
        print(json.dumps(summaries, indent=2, sort_keys=True))
        return

    for index, summary in enumerate(summaries):
        if index:
            print()
        print_human(summary)


if __name__ == "__main__":
    main()
