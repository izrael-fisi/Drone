"""Paired estimator benchmarking must verify results, not just timing."""

from copy import deepcopy
from dataclasses import asdict
import importlib.util
import json
from pathlib import Path

import pytest

from test_window_marginalization import problem
from vision_nav.estimation.correlated_window import CorrelatedWindowAligner

SCRIPT = Path(__file__).resolve().parents[1]/"scripts/research/benchmark_window_alignment.py"
spec = importlib.util.spec_from_file_location("window_benchmark", SCRIPT)
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def request(n=8):
    config, row, batch = problem(n=n)
    config = asdict(config)
    for key in ("imu_T_camera", "map_common_left_covariance"):
        config[key] = config[key].tolist()
    return {"schema_version": "vision_nav_correlated_alignment_request_v1",
            "config": config, "native_record": row, "map_batch": batch}


def test_measures_both_paths_without_a_machine_specific_speed_threshold():
    results = benchmark.measure(request(), 3)
    assert results["parity_passed"]
    assert results["nuisance_elimination"]["marginalized_pose_count"] == 5
    for value in results["operations"].values():
        assert len(value["samples_ms"]) == 3 and value["p99_ms"] >= value["p50_ms"] > 0
    assert not results["position_authority"]


def test_cli_emits_provenance_bound_report_and_refuses_overwrite(tmp_path):
    source, output = tmp_path/"32-pose-request.json", tmp_path/"benchmark.json"
    source.write_text(json.dumps(request(32))+"\n")
    args = ["--request", str(source), "--output", str(output), "--iterations", "3"]
    benchmark.main(args)
    report = json.loads(output.read_text())
    assert report["parity_passed"] and str(SCRIPT) in report["source_sha256"]
    assert report["nuisance_elimination"]["optimized_pose_count"] == 3
    assert report["scope"].endswith("not_Pi_or_live_current_state_qualification")
    with pytest.raises(SystemExit):
        benchmark.main(args)


@pytest.mark.parametrize("field,value", [("status", "withheld"), ("pose_timestamp_ns", 1),
    ("map_T_imu", [[0]*4]*4), ("covariance_right_imu", [[0]*6]*6), ("residual_chi2", 100.)])
def test_benchmark_refuses_changed_mean_covariance_identity_or_decision(field, value):
    config, row, batch = problem()
    result = CorrelatedWindowAligner(config).solve(row, batch)
    changed = deepcopy(result)
    changed[field] = value
    with pytest.raises(ValueError):
        benchmark.compare(changed, result)


@pytest.mark.parametrize("count", [0, 2, 101, True])
def test_invalid_iteration_bounds(count):
    with pytest.raises(ValueError):
        benchmark.measure({}, count)
