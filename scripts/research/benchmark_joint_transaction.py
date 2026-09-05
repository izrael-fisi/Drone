"""Compare two-call and combined estimator IPC with one explicit noise provider."""

import argparse
import hashlib
import json
from pathlib import Path
import platform
import time

import numpy as np

from vision_nav.estimation.alignment_worker import ProcessAlignmentEngine
from vision_nav.estimation.correlated_window import payload_digest


def measure(engine, request, iterations):
    if type(iterations) is not int or not 3 <= iterations <= 100:
        raise ValueError("Iterations must be in 3..100")
    if request.get("schema_version") != "vision_nav_joint_pnp_alignment_request_v1" or "joint_uncertainty" in request:
        raise ValueError("Expected an alignment request without joint noise; the explicit provider supplies it")
    samples = {"separate": [], "combined": []}
    initial = engine.status()["completed_requests"]
    for round_index in range(iterations+2):
        results = {}
        for name in (("separate", "combined") if round_index % 2 else ("combined", "separate")):
            began = time.perf_counter_ns()
            if name == "separate":
                noise = engine.joint_noise(request)
                result = engine.align({**request, "joint_uncertainty": noise})
            else:
                reply = engine.align_with_noise(request)
                if reply["input_request_sha256"] != payload_digest(request):
                    raise ValueError("Combined request identity differs")
                noise, result = reply["joint_uncertainty"], reply["result"]
            elapsed = (time.perf_counter_ns()-began)/1e6
            results[name] = {"joint_uncertainty": noise, "result": result}
            if round_index >= 2:
                samples[name].append(elapsed)
        if results["separate"] != results["combined"]:
            raise ValueError("Separate and combined uncertainty/results differ")
    metrics = {name: {"samples_ms": values, **{f"p{p}_ms": float(np.percentile(values, p)) for p in (50, 95, 99)}}
               for name, values in samples.items()}
    completed = engine.status()["completed_requests"]-initial
    if completed != 3*(iterations+2):
        raise ValueError("Unexpected estimator transaction count")
    return {"exact_result_and_noise_parity": True, "operations": metrics,
        "median_speedup": metrics["separate"]["p50_ms"]/metrics["combined"]["p50_ms"],
        "completed_transactions_including_warmup": completed,
        "result_status": result["status"], "position_authority": False, "px4_external_vision": "disabled"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--provider", type=Path, required=True, help="Explicit trusted local research-noise provider")
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=20)
    args = parser.parse_args(argv)
    if args.output.exists() or not 3 <= args.iterations <= 100:
        parser.error("Output must be new and iterations in 3..100")
    with args.request.open("rb") as stream:
        raw = stream.read(32*1024*1024+1)
    if len(raw) > 32*1024*1024:
        raise ValueError("Input request exceeds 32 MiB")
    script_digest = hashlib.sha256(Path(__file__).read_bytes()).hexdigest()
    engine = ProcessAlignmentEngine(args.provider, operation_timeout_s=10.)
    try:
        engine.start()
        result = measure(engine, json.loads(raw), args.iterations)
    finally:
        if not engine.stop():
            raise RuntimeError("Benchmark estimator process did not stop")
    if hashlib.sha256(Path(__file__).read_bytes()).hexdigest() != script_digest:
        raise ValueError("Benchmark source changed during execution")
    report = {"schema_version": "vision_nav_joint_transaction_benchmark_v1",
        "scope": "local_process_noise_and_alignment_not_live_current_state_or_Pi_qualification",
        "input_sha256": hashlib.sha256(raw).hexdigest(), "benchmark_source_sha256": script_digest,
        "python": platform.python_version(), "platform": platform.system(), "machine": platform.machine(),
        "iterations": args.iterations, "warmup_pairs": 2, "engine": engine.status(), **result}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"output": str(args.output), "scope": report["scope"],
        "exact_result_and_noise_parity": True, "median_speedup": report["median_speedup"],
        "p50_ms": {name: metrics["p50_ms"] for name, metrics in report["operations"].items()}}, indent=2))


if __name__ == "__main__":
    main()
