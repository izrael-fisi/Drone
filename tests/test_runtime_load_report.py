from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest

from vision_nav.neural.runtime_load_report import JsonlSource, distribution, summarize_runtime_load, main


RUN_ID = "11111111-2222-4333-8444-555555555555"


def metrics():
    rows = [{"record_type": "header", "schema_version": "vision_nav_runtime_metrics_v1", "run_id": RUN_ID,
             "interval_s": 1., "max_samples": 100, "hardware": {"is_raspberry_pi": True,
             "rss_measurement": "current_proc_vmrss"}, "position_authority": False}]
    for index in range(3):
        rows.append({"record_type": "sample", "run_id": RUN_ID, "sample_index": index,
            "host_monotonic_ns": (index + 1) * 1_000_000_000, "process_cpu_ns": index * 2_000_000_000,
            "probe_duration_ms": 2., "resources": {"rss_bytes": (index + 1) * 1000, "max_temperature_c": 45 + index,
                "throttle": {"status": "available", "current": [], "since_boot": ["throttled"]}},
            "process_io": {"status": "available", "read_bytes": index * 100,
                           "write_bytes": index * 200, "cancelled_write_bytes": index * 10},
            "activity": {"phase": "inference", "map_release_id": "map@1", "model_release_id": "model@1",
                "attitude": {"schema_version": "vision_nav_attitude_context_v1", "status": "receiving",
                    "target_system": 1, "target_component": 1, "received_attitude_count": 10 + index,
                    "accepted_count": 10 + index, "filtered_source_count": 0, "invalid_count": 0,
                    "overflow_count": 0, "generation": 0, "last_attitude_age_ms": 20., "stale_after_ms": 500.,
                    "last_discontinuity_reason": None, "time_basis": "host_receive_unqualified",
                    "sensor_timing_calibrated": False, "position_authority": False, "mavlink_transmission_enabled": False},
                "camera": {"capture_worker": {"captured_count": 4 * index, "superseded_count": 2 * index,
                                              "delivered_count": index}}}, "position_authority": False})
    rows.append({"record_type": "footer", "run_id": RUN_ID, "sample_count": 3, "status": "stopped",
                 "host_monotonic_ns": 3_000_000_001, "position_authority": False})
    return rows


def decision(sequence, age_ms):
    captured = (sequence + 1) * 1_000_000_000
    return {"schema_version": "vision_nav_runtime_status_v3", "runtime_run_id": RUN_ID, "sequence": sequence,
        "shadow_mode": True, "px4_publication": {"enabled": False},
        "publication_decision": {"published": False, "reason": "shadow_mode"},
        "active_map": {"release_id": "map@1"}, "active_model": {"release_id": "model@1"},
        "inference": {"latency_ms": {"local_features": 10 + sequence}, "frame_probe": {
            "stages": [{"role": role, "status": "executed"} for role in
                       ("global_retriever", "local_features", "local_matcher", "temporal_student")],
            "result_timing": {"status": "available" if age_ms < 1000 else "rejected", "age_ms": age_ms,
                "camera": {"status": "available", "host_monotonic_ns": captured},
                "observed_host_monotonic_ns": captured + int(age_ms * 1e6)}}}}


def write(path, rows):
    path.write_text("".join(json.dumps(row) + "\n" for row in rows))
    return path


def test_report_joins_one_run_recomputes_timing_and_never_promotes(tmp_path):
    resource = write(tmp_path / "metrics.jsonl", metrics())
    other = decision(0, 9000)
    other["runtime_run_id"] = "different-run"
    traces = write(tmp_path / "decisions.jsonl", [other, decision(0, 100), decision(1, 200), decision(2, 1500)])
    report = summarize_runtime_load(resource, traces)
    assert report["evidence_status"] == "summarized_measurements"
    assert report["decisions"]["count"] == 3
    assert report["decisions"]["ignored_other_or_unbound_run_count"] == 1
    timing = report["decisions"]["capture_to_result_ms"]
    assert timing["p50"] == 200 and timing["max"] == 1500
    assert timing["p95"] == pytest.approx(1370)
    within = report["decisions"]["within_resource_window_capture_to_result_ms"]
    assert within["count"] == 2 and within["p50"] == 150
    assert report["decisions"]["outside_or_unverified_resource_window_count"] == 1
    assert report["resource_distributions"]["cpu_percent_one_core_scale"]["p50"] == 200
    assert report["resource_distributions"]["rss_bytes"]["max"] == 3000
    assert report["process_io"]["interval_byte_totals"]["write_bytes"] == 400
    assert report["process_io"]["physical_flash_writes"] is None
    assert report["camera"]["observed_counter_change"]["superseded_count"] == 4
    assert report["camera"]["hardware_dropped_frames"] is None
    assert report["throttle"]["current_flag_sample_counts"] == {}
    assert report["throttle"]["since_boot_flags_observed"] == ["throttled"]
    assert report["throttle"]["new_latched_flags_after_baseline"] == []
    assert report["attitude"]["status_sample_counts"] == {"receiving": 3}
    assert report["attitude"]["observed_counter_change"]["accepted_count"] == 2
    assert report["attitude"]["raw_imu_rate_hz"] is None
    assert report["inputs"]["metrics"]["sha256"] == hashlib.sha256(resource.read_bytes()).hexdigest()
    assert report["position_authority"] is report["px4_publication_enabled"] is False
    assert report["promotion_decision"].startswith("not_a_")
    assert not report["native_vio_process"]["requested"]
    assert report["parent_process_scope"] == "all_threads_excludes_children"


def native_metrics():
    rows = metrics()
    rows[0].update(native_vio_measurement_requested=True, process_id=1234)
    for i, row in enumerate(rows[1:-1]):
        row["native_vio_process"] = {"schema_version": "vision_nav_owned_process_resources_v1",
            "role": "native_vio", "status": "available", "position_authority": False,
            "cpu_scope": "process_user_plus_system_all_threads_excludes_descendants",
            "rss_scope": "current_process_rss_shared_pages_not_deduplicated",
            "identity": {"pid": 1235, "parent_pid": 1234, "created_at_epoch_s": 100., "worker_run_id": RUN_ID},
            "sample_started_host_monotonic_ns": row["host_monotonic_ns"]-1_000_000,
            "observed_host_monotonic_ns": row["host_monotonic_ns"]-500_000,
            "process_cpu_ns": i*1_000_000_000, "rss_bytes": (i+1)*4000,
            "process_io": {"status": "available", "read_bytes": i*10, "write_bytes": i*30,
                           "cancelled_write_bytes": 0}}
    return rows


def test_native_counters_are_separate_and_recomputed_not_summed_as_unique_ram(tmp_path):
    report = summarize_runtime_load(write(tmp_path/"metrics", native_metrics()))
    child = report["native_vio_process"]
    assert child["requested"] and child["identity"]["pid"] == 1235
    assert child["resource_distributions"]["cpu_percent_one_core_scale"]["p50"] == 100.
    assert child["resource_distributions"]["rss_bytes"]["max"] == 12000
    assert child["resource_distributions"]["parent_sample_skew_ms"]["max"] == .5
    assert child["process_io"]["interval_byte_totals"]["write_bytes"] == 60
    assert child["parent_and_child_unique_rss_bytes"] is child["parent_and_child_cpu_percent"] is None
    assert report["resource_distributions"]["cpu_percent_one_core_scale"]["p50"] == 200.


@pytest.mark.parametrize("fault", ["birth", "pid", "worker", "parent", "future", "outside_bracket", "scope", "undeclared"])
def test_invalid_native_identity_or_sampling_contract_rejects_report(tmp_path, fault):
    rows = native_metrics()
    row = rows[2]["native_vio_process"]
    if fault == "birth":
        row["identity"]["created_at_epoch_s"] += 1
    elif fault == "pid":
        row["identity"]["pid"] += 1
    elif fault == "worker":
        row["identity"]["worker_run_id"] = "12345678-1234-4234-8234-123456789012"
    elif fault == "parent":
        row["identity"]["parent_pid"] += 1
    elif fault == "future":
        row["observed_host_monotonic_ns"] += 1_000_000
    elif fault == "outside_bracket":
        row["sample_started_host_monotonic_ns"] -= 10_000_000
    elif fault == "scope":
        row["cpu_scope"] = "includes_children"
    else:
        rows[0]["native_vio_measurement_requested"] = False
    with pytest.raises(ValueError):
        summarize_runtime_load(write(tmp_path/"metrics", rows))


def test_native_gap_does_not_bridge_missing_intervals_or_fabricate_zero_usage(tmp_path):
    rows = native_metrics()
    rows[2]["native_vio_process"] = {"status": "unavailable", "position_authority": False}
    report = summarize_runtime_load(write(tmp_path/"metrics", rows))
    child = report["native_vio_process"]
    assert child["resource_distributions"]["cpu_percent_one_core_scale"]["count"] == 0
    assert child["resource_distributions"]["rss_bytes"]["count"] == 2
    assert child["process_io"]["interval_byte_totals"] == {}
    assert "native_vio_cpu_intervals_incomplete" in report["evidence_gaps"]


@pytest.mark.parametrize("fault", ["footer", "temperature", "rss", "io", "hardware", "camera", "cap"])
def test_missing_measurements_are_explicit_not_zero_filled(tmp_path, fault):
    rows = metrics()
    if fault == "footer":
        rows.pop()
    elif fault in {"temperature", "rss"}:
        rows[2]["resources"]["max_temperature_c" if fault == "temperature" else "rss_bytes"] = None
    elif fault == "io":
        rows[2]["process_io"] = {"status": "unavailable"}
    elif fault == "hardware":
        rows[0]["hardware"] = {"is_raspberry_pi": False, "rss_measurement": "process_lifetime_peak_ru_maxrss"}
    elif fault == "camera":
        for row in rows[1:-1]:
            row["activity"].pop("camera")
    else:
        rows[0]["max_samples"] = 3
        rows[-1]["status"] = "sample_limit_reached"
    report = summarize_runtime_load(write(tmp_path / "metrics", rows))
    assert report["evidence_status"] == "incomplete"
    assert len(report["evidence_gaps"]) >= 3
    assert report["decisions"]["capture_to_result_ms"]["p95"] is None


@pytest.mark.parametrize("fault", ["mixed_run", "sequence", "clock", "footer_count", "after_footer", "camera_reset", "map_change", "authority"])
def test_corrupted_metrics_cannot_produce_a_report(tmp_path, fault):
    rows = metrics()
    if fault == "mixed_run":
        rows[1]["run_id"] = "other"
    elif fault == "sequence":
        rows[2]["sample_index"] = 0
    elif fault == "clock":
        rows[2]["host_monotonic_ns"] = rows[1]["host_monotonic_ns"]
    elif fault == "footer_count":
        rows[-1]["sample_count"] = 99
    elif fault == "after_footer":
        rows.append(deepcopy(rows[1]))
    elif fault == "camera_reset":
        rows[3]["activity"]["camera"]["capture_worker"]["captured_count"] = 1
    elif fault == "map_change":
        rows[2]["activity"]["map_release_id"] = "other"
    else:
        rows[2]["position_authority"] = True
    with pytest.raises(ValueError):
        summarize_runtime_load(write(tmp_path / "metrics", rows))


@pytest.mark.parametrize("fault", ["duplicate", "wrong_model", "wrong_age", "heartbeat", "publication", "nonfinite", "duplicate_json_key"])
def test_invalid_decision_or_json_data_is_rejected(tmp_path, fault):
    resource = write(tmp_path / "metrics", metrics())
    rows = [decision(0, 100), decision(1, 200)]
    if fault == "duplicate":
        rows[1]["sequence"] = 0
    elif fault == "wrong_model":
        rows[1]["active_model"]["release_id"] = "other"
    elif fault == "wrong_age":
        rows[1]["inference"]["frame_probe"]["result_timing"]["age_ms"] += 5
    elif fault == "heartbeat":
        rows[1]["emission_kind"] = "heartbeat"
    elif fault == "publication":
        rows[1]["px4_publication"]["enabled"] = True
    elif fault == "nonfinite":
        rows[1]["inference"]["latency_ms"]["local_features"] = float("nan")
    path = write(tmp_path / "trace", rows)
    if fault == "duplicate_json_key":
        path.write_text('{"runtime_run_id": "one", "runtime_run_id": "two"}\n')
    with pytest.raises(ValueError):
        summarize_runtime_load(resource, path)


def test_jsonl_bounds_and_mutation_detection(tmp_path):
    path = write(tmp_path / "source", [{"a": 1}, {"a": 2}])
    with pytest.raises(ValueError):
        list(JsonlSource(path, max_line_bytes=3, max_records=10))
    with pytest.raises(ValueError):
        list(JsonlSource(path, max_line_bytes=100, max_records=1))
    reader = iter(JsonlSource(path, max_line_bytes=100, max_records=10))
    next(reader)
    with path.open("a") as handle:
        handle.write('{"a": 3}\n')
    with pytest.raises(ValueError, match="changed"):
        list(reader)


@pytest.mark.parametrize("fault", ["sender", "counter_reset", "transmission", "freshness", "timing_claim"])
def test_attitude_contradictions_rejected(tmp_path, fault):
    rows = metrics()
    attitude = rows[2]["activity"]["attitude"]
    if fault == "sender":
        attitude["target_system"] = 2
    elif fault == "counter_reset":
        attitude["accepted_count"] = 1
    elif fault == "transmission":
        attitude["mavlink_transmission_enabled"] = True
    elif fault == "freshness":
        attitude["last_attitude_age_ms"] = 1000
    else:
        attitude["sensor_timing_calibrated"] = True
    with pytest.raises(ValueError):
        summarize_runtime_load(write(tmp_path / "metrics", rows))


def test_load_report_retains_dropouts_and_new_throttle_flags(tmp_path):
    rows = metrics()
    rows[2]["activity"]["attitude"].update(status="stale", last_attitude_age_ms=600.)
    rows[3]["activity"]["attitude"].update(generation=1, last_discontinuity_reason="attitude_reception_gap")
    rows[3]["resources"]["throttle"].update(current=["undervoltage"], since_boot=["throttled", "undervoltage"])
    rows[2]["activity"].pop("camera")
    report = summarize_runtime_load(write(tmp_path / "metrics", rows), write(tmp_path / "trace", [decision(0, 100)]))
    assert report["attitude"]["status_sample_counts"] == {"receiving": 2, "stale": 1}
    assert report["attitude"]["observed_counter_change"]["generation"] == 1
    assert report["attitude"]["last_discontinuity_reason_sample_counts"] == {"attitude_reception_gap": 1}
    assert "attitude_unhealthy_samples_observed" in report["evidence_gaps"]
    assert "live_camera_counter_samples_incomplete" in report["evidence_gaps"]
    assert report["throttle"]["new_latched_flags_after_baseline"] == ["undervoltage"]


def test_resource_window_excludes_results_after_measurement_cap(tmp_path):
    rows = metrics()
    rows[0]["max_samples"] = 3
    rows[-1]["status"] = "sample_limit_reached"
    report = summarize_runtime_load(write(tmp_path / "metrics", rows),
                                    write(tmp_path / "trace", [decision(0, 100), decision(3, 3000)]))
    assert report["decisions"]["capture_to_result_ms"]["max"] == 3000
    assert report["decisions"]["within_resource_window_capture_to_result_ms"]["max"] == 100
    assert "measurement_stopped_at_sample_limit_runtime_may_have_continued" in report["evidence_gaps"]
    assert "decision_sequence_gaps" in report["evidence_gaps"]


def test_small_distributions_and_cli_preserves_input(tmp_path, monkeypatch, capsys):
    assert distribution([])["p95"] is None
    assert distribution([3])["p99"] == 3
    resource = write(tmp_path / "metrics", metrics())
    output = tmp_path / "report.json"
    monkeypatch.setattr("sys.argv", ["report", "--metrics", str(resource), "--output", str(output)])
    main()
    assert json.loads(output.read_text())["evidence_status"] == "incomplete"
    original = resource.read_bytes()
    monkeypatch.setattr("sys.argv", ["report", "--metrics", str(resource), "--output", str(resource)])
    with pytest.raises(SystemExit):
        main()
    assert resource.read_bytes() == original


def test_pi_report_wrapper_from_another_directory(tmp_path):
    resource = write(tmp_path / "resource evidence.jsonl", metrics())
    output = tmp_path / "load report.json"
    wrapper = Path(__file__).resolve().parents[1] / "scripts/pi/report_runtime_load.sh"
    result = subprocess.run(["bash", str(wrapper), "--metrics", str(resource), "--output", str(output)],
        cwd=tmp_path, env={**os.environ, "VISION_NAV_PYTHON": sys.executable},
        text=True, capture_output=True, timeout=20)
    assert result.returncode == 0, result.stderr
    report = json.loads(output.read_text())
    assert json.loads(result.stdout) == report
    assert report["evidence_status"] == "incomplete"
    assert "no_run_bound_decisions" in report["evidence_gaps"]
