"""Paired full-graph/optimized correlated replay; no sensors or aircraft access."""

import argparse
import hashlib
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import time

import numpy as np

from vision_nav.estimation.correlated_window import CorrelatedWindowAligner, WindowAlignmentConfig
from vision_nav.estimation.alignment_worker import _sources, _verify


def compare(reduced, full):
    for key in ("status", "reason", "fix_ids", "pose_timestamp_ns", "snapshot_sha256", "map_batch_sha256",
                "position_authority", "px4_external_vision", "residual_dof", "consistency_limit"):
        if reduced.get(key) != full.get(key):
            raise ValueError("Full/reduced decision or provenance differs: "+key)
    if reduced["status"] != "research_alignment":
        raise ValueError("Successful conditional alignment required for numerical benchmarking")
    deltas = {}
    for key in ("map_T_imu", "local_T_nominal_map", "covariance_right_imu", "conditional_covariance_right_imu"):
        a, b = np.asarray(reduced[key]), np.asarray(full[key])
        if not np.allclose(a, b, atol=2e-6, rtol=2e-5):
            raise ValueError("Full/reduced numerical results differ: "+key)
        deltas[key] = float(np.max(np.abs(a-b)))
    if not np.isclose(reduced["residual_chi2"], full["residual_chi2"], atol=1e-8, rtol=1e-5):
        raise ValueError("Full/reduced consistency residuals differ")
    return deltas


def measure(request, iterations):
    if type(iterations) is not int or not 3 <= iterations <= 100:
        raise ValueError("Iterations must be in 3..100")
    if request.get("schema_version") != "vision_nav_correlated_alignment_request_v1":
        raise ValueError("Expected a correlated alignment replay request")
    solver = CorrelatedWindowAligner(WindowAlignmentConfig(**request["config"]))
    samples, maximum = {"full": [], "reduced": []}, {}
    for round_index in range(iterations+2):
        results = {}
        # Alternate order; exclude two paired warmups, including cold SciPy import.
        for name in (("full", "reduced") if round_index % 2 else ("reduced", "full")):
            began = time.perf_counter_ns()
            results[name] = solver.solve(request["native_record"], request["map_batch"],
                                         marginalize_unused_poses=name == "reduced")
            elapsed = (time.perf_counter_ns()-began)/1e6
            if round_index >= 2:
                samples[name].append(elapsed)
        for key, delta in compare(results["reduced"], results["full"]).items():
            maximum[key] = max(maximum.get(key, 0.), delta)
    metrics = {name: {"samples_ms": values, **{f"p{p}_ms": float(np.percentile(values, p)) for p in (50, 95, 99)}}
               for name, values in samples.items()}
    return {"parity_passed": True, "maximum_absolute_difference": maximum,
        "tolerances": {"pose_covariance_atol": 2e-6, "pose_covariance_rtol": 2e-5,
                       "chi2_atol": 1e-8, "chi2_rtol": 1e-5},
        "operations": metrics, "median_speedup": metrics["full"]["p50_ms"]/metrics["reduced"]["p50_ms"],
        "nuisance_elimination": results["reduced"]["nuisance_elimination"],
        "solution_methods": {name: value["solution_method"] for name, value in results.items()},
        "position_authority": False, "px4_external_vision": "disabled"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--iterations", type=int, default=20)
    args = parser.parse_args(argv)
    if args.output.exists() or not 3 <= args.iterations <= 100:
        parser.error("Output must be new and iterations must be in 3..100")
    with args.request.open("rb") as stream:
        raw = stream.read(8*1024*1024+1)
    if len(raw) > 8*1024*1024:
        raise ValueError("Replay request exceeds 8 MiB")
    sources = _sources() | {str(Path(__file__).resolve()): hashlib.sha256(Path(__file__).read_bytes()).hexdigest()}
    _verify({"sources": sources})
    results = measure(json.loads(raw), args.iterations)
    _verify({"sources": sources})
    report = {"schema_version": "vision_nav_window_alignment_benchmark_v1",
        "scope": "single_snapshot_validation_and_solve_not_Pi_or_live_current_state_qualification",
        "input_sha256": hashlib.sha256(raw).hexdigest(), "source_sha256": sources,
        "python": platform.python_version(), "platform": platform.system(), "machine": platform.machine(),
        "versions": {name: importlib.metadata.version(name) for name in ("numpy", "scipy", "gtsam")},
        "thread_environment": {name: os.environ.get(name) for name in
            ("OMP_NUM_THREADS", "OPENBLAS_NUM_THREADS", "VECLIB_MAXIMUM_THREADS")},
        "iterations": args.iterations, "warmup_pairs": 2, **results}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"output": str(args.output), "scope": report["scope"], "parity_passed": True,
        "median_speedup": report["median_speedup"], "nuisance_elimination": report["nuisance_elimination"],
        "p50_ms": {name: values["p50_ms"] for name, values in report["operations"].items()}}, indent=2))


if __name__ == "__main__":
    main()
