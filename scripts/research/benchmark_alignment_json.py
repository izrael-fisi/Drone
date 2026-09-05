"""Offline, paired serialization benchmark; no model, sensor or aircraft access."""

import argparse
import hashlib
import json
import platform
from pathlib import Path
import statistics
import time
import tracemalloc

from vision_nav.estimation import bounded_json


LIMIT = 40*1024*1024


def former_owned(value, limit=LIMIT):
    """Frozen reference for the former incremental-encode/decode ownership path."""
    chunks, size = [], 0
    for chunk in json.JSONEncoder(sort_keys=True, allow_nan=False).iterencode(value):
        size += len(chunk.encode("utf-8"))
        if size > limit:
            raise ValueError("Delayed alignment input exceeds byte limit")
        chunks.append(chunk)
    return json.loads("".join(chunks))


def former_wire(value):
    return json.dumps(former_owned(value), sort_keys=True, allow_nan=False).encode("utf-8")


def load_case(path):
    """Select the first successful recorded update, not the fastest sample."""
    digest = hashlib.sha256()
    selected, total = None, 0
    with path.open("rb") as stream:
        for line_number in range(1, 100_001):
            raw = stream.readline(4*1024*1024+1)
            if not raw:
                break
            if len(raw) > 4*1024*1024:
                raise ValueError("Trace event exceeds 4 MiB")
            total += len(raw)
            if total > 256*1024*1024:
                raise ValueError("Trace exceeds 256 MiB")
            digest.update(raw)
            event = json.loads(raw)
            if selected is None and event.get("current_estimate") and event.get("replay_inputs"):
                selected = (line_number, event)
        else:
            raise ValueError("Trace exceeds event limit")
    if selected is None:
        raise ValueError("Trace has no successful update with numerical replay inputs")
    return digest.hexdigest(), *selected


def measure(value, iterations):
    operations = {
        "former_owned": former_owned,
        "bounded_owned": lambda v: bounded_json.owned(v, LIMIT),
        "former_wire": former_wire,
        "bounded_wire": lambda v: bounded_json.dumps(v, LIMIT).encode("ascii"),
    }
    expected = former_wire(value)
    for name, operation in operations.items():
        result = operation(value)
        actual = result if name.endswith("wire") else json.dumps(result, sort_keys=True, allow_nan=False).encode("ascii")
        if actual != expected:
            raise ValueError("Serialization bytes differ: "+name)
    samples = {name: [] for name in operations}
    for round_index in range(iterations+3):
        names = list(operations)
        # Rotate order to reduce the bias of always measuring one path first.
        names = names[round_index % len(names):]+names[:round_index % len(names)]
        for name in names:
            began = time.perf_counter_ns()
            result = operations[name](value)
            elapsed = (time.perf_counter_ns()-began)/1e6
            del result
            if round_index >= 3:
                samples[name].append(elapsed)
    metrics = {}
    for name, operation in operations.items():
        tracemalloc.start()
        operation(value)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        metrics[name] = {"samples_ms": samples[name], "median_ms": statistics.median(samples[name]),
                         "min_ms": min(samples[name]), "peak_python_allocation_bytes": peak}
    return {"encoded_bytes": len(expected), "encoded_sha256": hashlib.sha256(expected).hexdigest(),
            "byte_equivalence": True, "operations": metrics,
            "wire_median_speedup": metrics["former_wire"]["median_ms"]/metrics["bounded_wire"]["median_ms"],
            "owned_median_speedup": metrics["former_owned"]["median_ms"]/metrics["bounded_owned"]["median_ms"]}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trace", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=30)
    args = parser.parse_args()
    if args.output.exists() or not 3 <= args.iterations <= 1000:
        parser.error("Output must be new and iterations must be in 3..1000")
    trace_hash, line_number, event = load_case(args.trace)
    request = event["replay_inputs"][-1]
    cases = {"recorded_request": request, "recorded_evidence_event": event}
    report = {"schema_version": "vision_nav_alignment_json_benchmark_v1",
        "scope": "serialization_only_not_estimator_latency_or_Pi_qualification",
        "input_trace_sha256": trace_hash, "selected_event_line": line_number,
        "selection_policy": "first_successful_event_last_replay_input",
        "python": platform.python_version(), "platform": platform.system(), "machine": platform.machine(),
        "iterations": args.iterations, "byte_limit": LIMIT,
        "source_sha256": {p.name: hashlib.sha256(p.read_bytes()).hexdigest()
                          for p in [Path(__file__), Path(bounded_json.__file__)]},
        "cases": {name: measure(value, args.iterations) for name, value in cases.items()}}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"output": str(args.output), "scope": report["scope"],
        "cases": {name: {k: v for k, v in metrics.items() if k != "operations"}
                  for name, metrics in report["cases"].items()}}, indent=2))


if __name__ == "__main__":
    main()
