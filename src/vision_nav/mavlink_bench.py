"""Read-only MAVLink evidence capture for prop-off Pi/Pixhawk bench work."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import time
from typing import Any, Callable

from vision_nav.mavlink_bridge import MavlinkTelemetrySample, MavlinkVisionBridge


MAVLINK_BENCH_SCHEMA = "vision_nav_mavlink_bench_v1"
DEFAULT_DURATION_S = 15.0
DEFAULT_MIN_ATTITUDE_RATE_HZ = 20.0
DEFAULT_MAX_ATTITUDE_GAP_MS = 150.0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    index = round((len(ordered) - 1) * percentile)
    return round(ordered[index], 3)


def _message_summary(records: list[dict[str, Any]], message_type: str) -> dict[str, Any]:
    selected = sorted(
        [record for record in records if record.get("sample", {}).get("message_type") == message_type],
        key=lambda record: int(record.get("host_monotonic_ns", 0)),
    )
    timestamps_ns = [int(record["host_monotonic_ns"]) for record in selected if isinstance(record.get("host_monotonic_ns"), int)]
    intervals_ms = [
        (timestamps_ns[index] - timestamps_ns[index - 1]) / 1_000_000.0
        for index in range(1, len(timestamps_ns))
    ]
    span_s = (timestamps_ns[-1] - timestamps_ns[0]) / 1_000_000_000.0 if len(timestamps_ns) > 1 else 0.0
    rate_hz = (len(timestamps_ns) - 1) / span_s if span_s > 0.0 else None
    vehicle_domains: dict[str, int] = {}
    for record in selected:
        domain = record.get("sample", {}).get("vehicle_time_domain")
        if isinstance(domain, str):
            vehicle_domains[domain] = vehicle_domains.get(domain, 0) + 1
    return {
        "sample_count": len(selected),
        "observed_span_s": round(span_s, 3),
        "observed_rate_hz": round(rate_hz, 3) if rate_hz is not None else None,
        "interarrival_ms": {
            "median": _percentile(intervals_ms, 0.5),
            "p95": _percentile(intervals_ms, 0.95),
            "max": round(max(intervals_ms), 3) if intervals_ms else None,
        },
        "vehicle_time_domains": dict(sorted(vehicle_domains.items())),
    }


def summarize_mavlink_bench(
    records: list[dict[str, Any]],
    *,
    duration_s: float,
    endpoint: str,
    min_attitude_rate_hz: float = DEFAULT_MIN_ATTITUDE_RATE_HZ,
    max_attitude_gap_ms: float = DEFAULT_MAX_ATTITUDE_GAP_MS,
) -> dict[str, Any]:
    """Create an auditable pass/fail report from raw read-only telemetry."""

    if duration_s <= 0.0:
        raise ValueError("duration_s must be positive.")
    if min_attitude_rate_hz <= 0.0:
        raise ValueError("min_attitude_rate_hz must be positive.")
    if max_attitude_gap_ms <= 0.0:
        raise ValueError("max_attitude_gap_ms must be positive.")

    message_types = sorted(
        {
            str(record.get("sample", {}).get("message_type"))
            for record in records
            if isinstance(record.get("sample", {}).get("message_type"), str)
        }
    )
    summaries = {message_type: _message_summary(records, message_type) for message_type in message_types}
    attitude = summaries.get("ATTITUDE", _message_summary([], "ATTITUDE"))
    errors: list[str] = []
    warnings: list[str] = []
    if not records:
        errors.append("no_mavlink_telemetry_received")
    if "HEARTBEAT" not in summaries:
        errors.append("missing_px4_heartbeat")
    if attitude["sample_count"] < 2:
        errors.append("insufficient_px4_attitude_samples")
    elif attitude["observed_rate_hz"] is None or attitude["observed_rate_hz"] < min_attitude_rate_hz:
        errors.append("attitude_rate_below_minimum")
    max_gap_ms = attitude["interarrival_ms"]["max"]
    if max_gap_ms is not None and max_gap_ms > max_attitude_gap_ms:
        errors.append("attitude_gap_exceeds_limit")
    if "LOCAL_POSITION_NED" not in summaries:
        warnings.append("local_position_ned_not_observed")
    if "GPS_RAW_INT" not in summaries and "GLOBAL_POSITION_INT" not in summaries:
        warnings.append("gps_or_global_position_not_observed")

    return {
        "schema_version": MAVLINK_BENCH_SCHEMA,
        "status": "passed" if not errors else "failed",
        "capture": {
            "endpoint": endpoint,
            "duration_s": round(duration_s, 3),
            "record_count": len(records),
            "read_only": True,
            "px4_commands_sent": False,
        },
        "requirements": {
            "min_attitude_rate_hz": min_attitude_rate_hz,
            "max_attitude_gap_ms": max_attitude_gap_ms,
            "require_heartbeat": True,
        },
        "message_summaries": summaries,
        "attitude": attitude,
        "errors": errors,
        "warnings": warnings,
    }


def capture_mavlink_bench(
    output_dir: str | Path,
    *,
    endpoint: str,
    duration_s: float = DEFAULT_DURATION_S,
    min_attitude_rate_hz: float = DEFAULT_MIN_ATTITUDE_RATE_HZ,
    max_attitude_gap_ms: float = DEFAULT_MAX_ATTITUDE_GAP_MS,
    require_timesync: bool = False,
    timesync_sample_count: int = 8,
    max_timesync_round_trip_ms: float = 50.0,
    source_system: int = 42,
    source_component: int = 197,
    reader_factory: Callable[[], Any] | None = None,
) -> dict[str, Any]:
    """Capture a bounded raw MAVLink evidence session without sending commands."""

    root = Path(output_dir).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError(f"MAVLink bench output directory must be empty: {root}")
    root.mkdir(parents=True, exist_ok=True)
    if duration_s <= 0.0:
        raise ValueError("duration_s must be positive.")
    if timesync_sample_count <= 0:
        raise ValueError("timesync_sample_count must be positive.")
    if max_timesync_round_trip_ms <= 0.0:
        raise ValueError("max_timesync_round_trip_ms must be positive.")

    bridge = reader_factory() if reader_factory is not None else MavlinkVisionBridge(
        endpoint, source_system=source_system, source_component=source_component
    )
    started_utc = _utc_now()
    started_monotonic_ns = time.monotonic_ns()
    records: list[dict[str, Any]] = []
    timesync: dict[str, Any]
    try:
        bridge.connect()
        timesync = bridge.capture_timesync_evidence(
            sample_count=timesync_sample_count,
            max_round_trip_ms=max_timesync_round_trip_ms,
        )
        deadline = time.monotonic() + duration_s
        while time.monotonic() < deadline:
            remaining_s = max(0.0, deadline - time.monotonic())
            sample: MavlinkTelemetrySample | None = bridge.try_read_telemetry(timeout_s=min(0.25, remaining_s))
            if sample is None:
                continue
            records.append(
                {
                    "index": len(records),
                    "host_utc": _utc_now(),
                    "host_monotonic_ns": time.monotonic_ns(),
                    "sample": sample.to_dict(),
                }
            )
    finally:
        bridge.close()
    completed_monotonic_ns = time.monotonic_ns()
    actual_duration_s = (completed_monotonic_ns - started_monotonic_ns) / 1_000_000_000.0
    report = summarize_mavlink_bench(
        records,
        duration_s=actual_duration_s,
        endpoint=endpoint,
        min_attitude_rate_hz=min_attitude_rate_hz,
        max_attitude_gap_ms=max_attitude_gap_ms,
    )
    report["timesync"] = timesync
    report["requirements"].update(
        {
            "require_timesync": require_timesync,
            "timesync_sample_count": timesync_sample_count,
            "max_timesync_round_trip_ms": max_timesync_round_trip_ms,
        }
    )
    if require_timesync and timesync.get("status") != "available":
        report["errors"].append("mavlink_timesync_unavailable")
        report["status"] = "failed"
    report["capture"].update(
        {
            "started_utc": started_utc,
            "completed_utc": _utc_now(),
            "started_monotonic_ns": started_monotonic_ns,
            "completed_monotonic_ns": completed_monotonic_ns,
            "raw_telemetry_path": "mavlink_telemetry.jsonl",
        }
    )
    telemetry_path = root / "mavlink_telemetry.jsonl"
    with telemetry_path.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, sort_keys=True) + "\n")
    report_path = root / "mavlink_bench_report.json"
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"output_dir": str(root), "report_path": str(report_path), "report": report}


def main() -> None:
    parser = argparse.ArgumentParser(description="Record a read-only, prop-off Pi/PX4 MAVLink telemetry evidence session.")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--duration-s", type=float, default=DEFAULT_DURATION_S)
    parser.add_argument("--min-attitude-rate-hz", type=float, default=DEFAULT_MIN_ATTITUDE_RATE_HZ)
    parser.add_argument("--max-attitude-gap-ms", type=float, default=DEFAULT_MAX_ATTITUDE_GAP_MS)
    parser.add_argument("--require-timesync", action="store_true")
    parser.add_argument("--timesync-sample-count", type=int, default=8)
    parser.add_argument("--max-timesync-round-trip-ms", type=float, default=50.0)
    parser.add_argument("--source-system", type=int, default=42)
    parser.add_argument("--source-component", type=int, default=197)
    args = parser.parse_args()
    try:
        result = capture_mavlink_bench(
            args.output_dir,
            endpoint=args.endpoint,
            duration_s=args.duration_s,
            min_attitude_rate_hz=args.min_attitude_rate_hz,
            max_attitude_gap_ms=args.max_attitude_gap_ms,
            require_timesync=args.require_timesync,
            timesync_sample_count=args.timesync_sample_count,
            max_timesync_round_trip_ms=args.max_timesync_round_trip_ms,
            source_system=args.source_system,
            source_component=args.source_component,
        )
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result["report"], indent=2, sort_keys=True))
    if result["report"]["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
