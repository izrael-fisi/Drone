"""Replay qualified metric factors with durable, shadow-only decision evidence."""

from __future__ import annotations

import argparse
from dataclasses import fields
import hashlib
import json
from pathlib import Path

from .pose_smoother import MetricPoseSmoother, SmootherConfig, timestamp

MAX_EVENT_BYTES = 1024 * 1024


def _reject_constant(value):
    raise ValueError(f"Non-finite JSON constant: {value}")


def _unique_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError(f"Duplicate JSON field: {key}")
        result[key] = value
    return result


def _loads(data):
    return json.loads(data, parse_constant=_reject_constant, object_pairs_hook=_unique_object)


def _dump(data):
    return json.dumps(data, sort_keys=True, allow_nan=False, separators=(",", ":"))


def replay(config_path: Path, log_path: Path, output: Path) -> dict:
    config_bytes = config_path.read_bytes()
    config_data = _loads(config_bytes)
    if not isinstance(config_data, dict) or set(config_data) - {f.name for f in fields(SmootherConfig)}:
        raise ValueError("Estimator configuration must contain only SmootherConfig fields")
    estimator = MetricPoseSmoother(SmootherConfig(**config_data))
    output.mkdir(parents=True, exist_ok=False)
    (output / "config.json").write_bytes(config_bytes)
    source_hashes = {path.name: hashlib.sha256(path.read_bytes()).hexdigest() for path in
                     (Path(__file__), Path(__file__).with_name("pose_smoother.py"))}
    counts = {"accepted": 0, "rejected": 0}
    log_hash, trace_hash = hashlib.sha256(), hashlib.sha256()
    receipt_ns = 1
    line_number = 0
    fatal_error = None
    with log_path.open("rb") as stream, (output / "decisions.jsonl").open("xb") as trace:
        while raw := stream.readline(MAX_EVENT_BYTES + 1):
            line_number += 1
            if len(raw) > MAX_EVENT_BYTES:
                fatal_error = "event_exceeds_size_limit"
                break
            log_hash.update(raw)
            event = None
            decision, reason = "accepted", None
            try:
                event = _loads(raw)
                if not isinstance(event, dict):
                    raise ValueError("Event must be a JSON object")
                received = timestamp(event["received_timestamp_ns"])
                if received < receipt_ns:
                    raise ValueError("Replay receipt clock went backwards")
                receipt_ns = received
                measurement = timestamp(event["timestamp_ns"])
                if measurement > receipt_ns:
                    raise ValueError("Measurement lies in the future of receipt clock")
                if event.get("type") == "start":
                    estimator.start(measurement)
                elif event.get("type") == "odometry":
                    estimator.add_odometry(event)
                elif event.get("type") == "map_fix":
                    estimator.add_map_fix(event)
                elif event.get("type") != "tick":
                    raise ValueError("Unknown estimator event type")
            except (ValueError, KeyError, TypeError, RuntimeError, OverflowError) as exc:
                decision, reason = "rejected", f"{type(exc).__name__}:{exc}"
            snapshot = estimator.snapshot(receipt_ns)
            if snapshot["fault"] and decision == "accepted":
                decision, reason = "rejected", snapshot["fault"]
            counts[decision] += 1
            record = {"line": line_number, "event_sha256": hashlib.sha256(raw).hexdigest(),
                      "decision": decision, "reason": reason, "snapshot": snapshot}
            encoded = (_dump(record) + "\n").encode()
            trace.write(encoded)
            trace.flush()
            trace_hash.update(encoded)
    complete = fatal_error is None
    summary = {"schema_version": "vision_nav_estimator_replay_v1", "complete": complete,
               "events_processed": sum(counts.values()), "decisions": counts, "fatal_error": fatal_error,
               "input_sha256": log_hash.hexdigest() if complete else None,
               "config_sha256": hashlib.sha256(config_bytes).hexdigest(), "source_sha256": source_hashes,
               "decision_trace_sha256": trace_hash.hexdigest(), "final_snapshot": estimator.snapshot(receipt_ns),
               "position_authority": False, "px4_external_vision": "disabled",
               "validation_scope": "factor_replay_only_not_sensor_or_flight_validation"}
    (output / "summary.json").write_text(_dump(summary) + "\n", encoding="utf-8")
    return summary


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, type=Path)
    parser.add_argument("--log", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path, help="New directory; never overwrite evidence")
    args = parser.parse_args(argv)
    try:
        result = replay(args.config, args.log, args.output)
    except (ValueError, OSError, TypeError, RuntimeError) as exc:
        parser.exit(2, f"Estimator replay failed: {exc}\n")
    print(json.dumps(result, indent=2, allow_nan=False))
    valid = (result["complete"] and not result["decisions"]["rejected"]
             and result["final_snapshot"]["estimator_state"] not in {"FAULT", "NOT_STARTED"})
    return 0 if valid else 2


if __name__ == "__main__":
    raise SystemExit(main())
