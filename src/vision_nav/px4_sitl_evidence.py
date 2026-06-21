from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
import math
from pathlib import Path
import re
from typing import Any


@dataclass(frozen=True)
class Px4SitlEvidenceConfig:
    min_samples: int = 2
    max_sample_age_s: float = 5.0
    require_position: bool = True
    require_covariance: bool = True


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate captured PX4 SITL external-vision receiver evidence.")
    parser.add_argument("--listener", required=True, help="Text captured from `listener vehicle_visual_odometry 5`.")
    parser.add_argument("--mavlink-status", help="Optional text captured from `mavlink status`.")
    parser.add_argument(
        "--expected-message",
        choices=["odometry", "vision_position_estimate"],
        default="odometry",
        help="Message path that was sent by the smoke script. PX4 maps both into vehicle_visual_odometry.",
    )
    parser.add_argument("--min-samples", type=int, default=Px4SitlEvidenceConfig.min_samples)
    parser.add_argument("--max-sample-age-s", type=float, default=Px4SitlEvidenceConfig.max_sample_age_s)
    parser.add_argument("--json", action="store_true", help="Emit JSON only.")
    parser.add_argument("--allow-degraded", action="store_true", help="Exit zero for warning-only degraded evidence.")
    return parser.parse_args()


def evaluate_px4_sitl_evidence(
    *,
    listener_text: str,
    mavlink_status_text: str | None = None,
    expected_message: str = "odometry",
    config: Px4SitlEvidenceConfig | None = None,
) -> dict[str, Any]:
    config = config or Px4SitlEvidenceConfig()
    listener = parse_vehicle_visual_odometry_listener(listener_text)
    mavlink_status = parse_mavlink_status(mavlink_status_text or "") if mavlink_status_text is not None else None
    issues: list[dict[str, str]] = []

    if not listener["topic_seen"]:
        add_issue(issues, "error", "vehicle_visual_odometry topic was not found in listener capture.")
    if listener["not_published"]:
        add_issue(issues, "error", "PX4 reported vehicle_visual_odometry was never published.")
    if listener["sample_count"] < config.min_samples:
        add_issue(
            issues,
            "error",
            f"Listener capture has {listener['sample_count']} sample(s), expected at least {config.min_samples}.",
        )
    if config.require_position and listener["position_count"] == 0:
        add_issue(issues, "error", "Listener capture has no local position vector.")
    if config.require_covariance and listener["position_variance_count"] == 0:
        add_issue(issues, "error", "Listener capture has no position variance/covariance vector.")

    latest_age_s = listener["latest_sample_age_s"]
    if latest_age_s is not None and latest_age_s > config.max_sample_age_s:
        add_issue(issues, "error", f"Latest listener sample age {latest_age_s:.3f}s exceeds {config.max_sample_age_s:.3f}s.")

    if listener["orientation_count"] == 0:
        add_issue(issues, "warning", "Listener capture has no orientation quaternion.")
    if expected_message == "odometry" and listener["quality_count"] == 0:
        add_issue(issues, "warning", "ODOMETRY receiver evidence has no quality field.")
    if listener["reset_counter_count"] == 0:
        add_issue(issues, "warning", "Listener capture has no reset_counter field.")

    if mavlink_status is not None:
        if not mavlink_status["mavlink_seen"]:
            add_issue(issues, "warning", "mavlink status capture does not look like PX4 MAVLink status output.")
        if mavlink_status["mavlink_version"] is not None and mavlink_status["mavlink_version"] < 2:
            add_issue(issues, "warning", "MAVLink status reports a version below 2.")
        if not mavlink_status["udp_ports"]:
            add_issue(issues, "warning", "MAVLink status capture does not show a UDP link/port.")

    status = "failed" if any(issue["severity"] == "error" for issue in issues) else "passed"
    if status == "passed" and any(issue["severity"] == "warning" for issue in issues):
        status = "degraded"

    return {
        "status": status,
        "expected_message": expected_message,
        "config": asdict(config),
        "listener": listener,
        "mavlink_status": mavlink_status,
        "issues": issues,
    }


def parse_vehicle_visual_odometry_listener(text: str) -> dict[str, Any]:
    lower = text.lower()
    timestamp_matches = list(re.finditer(r"(?im)^\s*timestamp\s*[:=]\s*(\d+)(?:\s*\(([-+0-9.eE]+)\s+seconds ago\))?", text))
    timestamp_sample_matches = re.findall(r"(?im)^\s*timestamp_sample\s*[:=]\s*(\d+)", text)
    positions = _parse_named_vectors(text, "position")
    position_variances = _parse_named_vectors(text, "position_variance")
    orientation_variances = _parse_named_vectors(text, "orientation_variance")
    orientations = _parse_named_vectors(text, "q")
    qualities = _parse_named_numbers(text, "quality")
    reset_counters = _parse_named_numbers(text, "reset_counter")
    ages = [_optional_float(match.group(2)) for match in timestamp_matches if match.group(2) is not None]
    finite_positions = [vector for vector in positions if all(math.isfinite(value) for value in vector)]

    return {
        "topic_seen": "vehicle_visual_odometry" in lower,
        "not_published": "never published" in lower or "not published" in lower,
        "sample_count": len(timestamp_matches),
        "timestamp_sample_count": len(timestamp_sample_matches),
        "latest_timestamp": int(timestamp_matches[-1].group(1)) if timestamp_matches else None,
        "latest_sample_age_s": ages[-1] if ages else None,
        "position_count": len(positions),
        "finite_position_count": len(finite_positions),
        "last_position": finite_positions[-1] if finite_positions else None,
        "position_variance_count": len(position_variances),
        "last_position_variance": position_variances[-1] if position_variances else None,
        "orientation_count": len(orientations),
        "orientation_variance_count": len(orientation_variances),
        "quality_count": len(qualities),
        "last_quality": qualities[-1] if qualities else None,
        "reset_counter_count": len(reset_counters),
        "last_reset_counter": int(reset_counters[-1]) if reset_counters else None,
    }


def parse_mavlink_status(text: str) -> dict[str, Any]:
    lower = text.lower()
    version_match = re.search(r"(?im)mavlink\s+version\s*[:=]\s*(\d+)", text)
    ports = sorted({int(port) for port in re.findall(r"(?i)\bUDP\b[^\n]*?(\d{4,5})", text)})
    rx_rates = _parse_rate_lines(text, "rx")
    tx_rates = _parse_rate_lines(text, "tx")
    return {
        "mavlink_seen": "mavlink" in lower,
        "mavlink_version": int(version_match.group(1)) if version_match else None,
        "udp_ports": ports,
        "has_udp_14550": 14550 in ports,
        "rx_rate_samples": rx_rates,
        "tx_rate_samples": tx_rates,
        "accepting_commands": bool(re.search(r"(?im)accepting commands\s*[:=]\s*YES", text)),
    }


def _parse_named_vectors(text: str, name: str) -> list[list[float]]:
    pattern = re.compile(rf"(?im)^\s*{re.escape(name)}\s*[:=]\s*\[([^\]]+)\]")
    vectors: list[list[float]] = []
    for match in pattern.finditer(text):
        values = [_optional_float(part) for part in re.split(r"[,\s]+", match.group(1).strip()) if part]
        vectors.append([float(value) for value in values if value is not None])
    return vectors


def _parse_named_numbers(text: str, name: str) -> list[float]:
    pattern = re.compile(rf"(?im)^\s*{re.escape(name)}\s*[:=]\s*([-+0-9.eE]+)")
    return [float(match.group(1)) for match in pattern.finditer(text)]


def _parse_rate_lines(text: str, name: str) -> list[float]:
    pattern = re.compile(rf"(?im)^\s*{re.escape(name)}\s*[:=]\s*([-+0-9.eE]+)\s*(?:[kK]?[bB]/s)?")
    return [float(match.group(1)) for match in pattern.finditer(text)]


def _optional_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        output = float(value)
        return output if math.isfinite(output) else None
    except (TypeError, ValueError):
        return None


def add_issue(issues: list[dict[str, str]], severity: str, message: str) -> None:
    issues.append({"severity": severity, "message": message})


def print_human(report: dict[str, Any]) -> None:
    listener = report["listener"]
    print("PX4 SITL external-vision receiver evidence")
    print(f"Status: {report['status']}")
    print(f"Expected sender message: {report['expected_message']}")
    print(f"Listener samples: {listener['sample_count']}")
    print(f"Last sample age: {listener['latest_sample_age_s']}")
    print(f"Last position: {listener['last_position']}")
    print(f"Last position variance: {listener['last_position_variance']}")
    mavlink_status = report.get("mavlink_status")
    if mavlink_status is not None:
        print(f"MAVLink version: {mavlink_status['mavlink_version']}")
        print(f"UDP ports: {mavlink_status['udp_ports']}")
    for issue in report["issues"]:
        print(f"[{issue['severity'].upper()}] {issue['message']}")


def main() -> None:
    args = parse_args()
    report = evaluate_px4_sitl_evidence(
        listener_text=Path(args.listener).expanduser().read_text(),
        mavlink_status_text=Path(args.mavlink_status).expanduser().read_text() if args.mavlink_status else None,
        expected_message=args.expected_message,
        config=Px4SitlEvidenceConfig(
            min_samples=args.min_samples,
            max_sample_age_s=args.max_sample_age_s,
        ),
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human(report)
    if report["status"] == "failed" or (report["status"] == "degraded" and not args.allow_degraded):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
