"""Reproducible serialization evidence, not a timing threshold test."""

import hashlib
import importlib.util
import json
from pathlib import Path
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/research/benchmark_alignment_json.py"
spec = importlib.util.spec_from_file_location("alignment_json_benchmark", SCRIPT)
benchmark = importlib.util.module_from_spec(spec)
spec.loader.exec_module(benchmark)


def test_selection_is_first_success_and_hash_covers_complete_trace(tmp_path):
    path = tmp_path / "trace.jsonl"
    records = [{"current_estimate": None, "replay_inputs": []},
               {"current_estimate": {"pose": 1}, "replay_inputs": [{"input": 1}, {"input": 2}]},
               {"current_estimate": {"pose": 2}, "replay_inputs": [{"input": 3}]}]
    raw = "".join(json.dumps(value)+"\n" for value in records).encode()
    path.write_bytes(raw)
    digest, line, event = benchmark.load_case(path)
    assert digest == hashlib.sha256(raw).hexdigest() and line == 2 and event == records[1]


def test_benchmark_reports_equivalent_bytes_and_separate_timing_allocation_samples():
    value = {"floats": [[.2, -0.0, 1e-308]], "unicode": "\u00e9"}
    result = benchmark.measure(value, 3)
    expected = json.dumps(value, sort_keys=True, allow_nan=False).encode("ascii")
    assert result["byte_equivalence"] and result["encoded_bytes"] == len(expected)
    assert result["encoded_sha256"] == hashlib.sha256(expected).hexdigest()
    for metrics in result["operations"].values():
        assert len(metrics["samples_ms"]) == 3
        assert metrics["median_ms"] > 0 and metrics["peak_python_allocation_bytes"] > 0


def test_cli_writes_scoped_report_and_refuses_overwrite(tmp_path, monkeypatch):
    trace, output = tmp_path / "trace.jsonl", tmp_path / "report.json"
    trace.write_text(json.dumps({"current_estimate": {"synthetic": True}, "replay_inputs": [{"synthetic": [1.]}]})+"\n")
    monkeypatch.setattr(sys, "argv", [str(SCRIPT), "--trace", str(trace), "--output", str(output), "--iterations", "3"])
    benchmark.main()
    record = json.loads(output.read_text())
    assert record["scope"] == "serialization_only_not_estimator_latency_or_Pi_qualification"
    assert set(record["cases"]) == {"recorded_request", "recorded_evidence_event"}
    assert SCRIPT.name in record["source_sha256"]
    with pytest.raises(SystemExit):
        benchmark.main()


@pytest.mark.parametrize("content,reason", [(b"{}\n", "no successful"), (b"x"*(4*1024*1024+1), "4 MiB")])
def test_invalid_or_oversized_trace_is_not_benchmarked(tmp_path, content, reason):
    path = tmp_path / "trace.jsonl"
    path.write_bytes(content)
    with pytest.raises(ValueError, match=reason):
        benchmark.load_case(path)
