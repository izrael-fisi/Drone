"""Reproducible process comparison, not a machine-specific timing assertion."""

import importlib.util
import json
from pathlib import Path

import pytest

from test_joint_alignment_transaction import PROVIDER
from test_pnp_window_alignment import fixture

SCRIPT = Path(__file__).resolve().parents[1]/"scripts/research/benchmark_joint_transaction.py"
spec = importlib.util.spec_from_file_location("joint_benchmark", SCRIPT)
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def test_cli_verifies_exact_parity_and_leaves_no_live_worker(tmp_path):
    request, _ = fixture()
    request.pop("joint_uncertainty")
    source, output = tmp_path/"request.json", tmp_path/"report.json"
    source.write_text(json.dumps(request)+"\n")
    args = ["--request", str(source), "--provider", str(PROVIDER), "--output", str(output), "--iterations", "3"]
    benchmark.main(args)
    report = json.loads(output.read_text())
    assert report["exact_result_and_noise_parity"]
    assert report["completed_transactions_including_warmup"] == 15
    assert not report["engine"]["process_alive"] and report["engine"]["returncode"] == 0
    for metrics in report["operations"].values():
        assert len(metrics["samples_ms"]) == 3 and metrics["p99_ms"] >= metrics["p50_ms"] > 0
    with pytest.raises(SystemExit):
        benchmark.main(args)


@pytest.mark.parametrize("count", [0, 2, 101, True])
def test_invalid_iterations_do_not_launch_work(count):
    with pytest.raises(ValueError):
        benchmark.measure(None, {}, count)


def test_rejects_replacing_noise_in_an_already_bound_request():
    request, _ = fixture()
    with pytest.raises(ValueError, match="without joint noise"):
        benchmark.measure(None, request, 3)
