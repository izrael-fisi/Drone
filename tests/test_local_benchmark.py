from dataclasses import replace
import json

import cv2
import numpy as np
import pytest

from vision_nav.neural.local_benchmark import LocalBenchmarkPolicy, benchmark_local, assess_limits, parse_throttled
from vision_nav.neural.learned_correspondence import LocalFeatures, LocalMatchingPolicy


def resource(temp=45, rss=1000000, throttle="throttled=0x0"):
    return {"rss_bytes": rss, "max_temperature_c": temp, "throttle": parse_throttled(throttle)}


def records():
    return [{"phase": "measured", "status": "completed", "attention_calls": 1, "latency_ms": {"total": 10}} for _ in range(20)]


def test_latched_throttle_is_not_misreported_as_current():
    value = parse_throttled("throttled=0x50000\n")
    assert value["current"] == []
    assert value["since_boot"] == ["undervoltage", "throttled"]
    value = parse_throttled("throttled=0x5")
    assert value["current"] == ["undervoltage", "throttled"]


@pytest.mark.parametrize("raw", ["throttled=0", "throttled=unknown", "0x0", "throttled=0x0 extra", "throttled=0x10"])
def test_invalid_throttle_not_healthy(raw):
    with pytest.raises(ValueError):
        parse_throttled(raw)


def test_complete_measurement_can_meet_only_explicit_performance_limits():
    result = assess_limits(records(), [resource(), resource()], 60, LocalBenchmarkPolicy(require_pi=True))
    assert result["status"] == "within_measured_limits"
    assert result["promotion_decision"] == "not_a_navigation_or_flight_acceptance"


@pytest.mark.parametrize("fault,expected", [("latency", "local_iteration_p95_exceeds_limit"),
    ("memory", "process_memory_exceeds_limit"), ("temperature", "temperature_exceeds_limit")])
def test_observed_limit_violation_is_not_incomplete_or_passed(fault, expected):
    rows, snapshots = records(), [resource()]
    if fault == "latency":
        for row in rows:
            row["latency_ms"]["total"] = 2000
    elif fault == "memory":
        snapshots[0]["rss_bytes"] = 3 * 1024 ** 3
    else:
        snapshots[0]["max_temperature_c"] = 90
    result = assess_limits(rows, snapshots, 60, LocalBenchmarkPolicy())
    assert result["status"] == "exceeded" and expected in result["exceeded"]


@pytest.mark.parametrize("missing", ["temp", "rss", "throttle", "duration", "iterations", "attention"])
def test_incomplete_measurement_never_passes(missing):
    snapshots, rows, elapsed = [resource(), resource()], records(), 60
    if missing == "temp":
        snapshots[1]["max_temperature_c"] = None
    elif missing == "rss":
        snapshots[1]["rss_bytes"] = None
    elif missing == "throttle":
        snapshots[1]["throttle"]["status"] = "unavailable"
    elif missing == "duration":
        elapsed = 59
    elif missing == "iterations":
        rows = rows[:1]
    else:
        for row in rows:
            row["attention_calls"] = 0
    assert assess_limits(rows, snapshots, elapsed, LocalBenchmarkPolicy(require_pi=True))["status"] == "incomplete"


def test_old_latched_flags_separate_from_new_or_current_fault():
    p = LocalBenchmarkPolicy()
    old = assess_limits(records(), [resource(throttle="throttled=0x40000")] * 2, 60, p)
    assert old["status"] == "within_measured_limits"
    assert old["preexisting_latched_flags"] == ["throttled"]
    new = assess_limits(records(), [resource(), resource(throttle="throttled=0x40000")], 60, p)
    assert "new_latched_power_or_throttle_flag" in new["exceeded"]
    now = assess_limits(records(), [resource(throttle="throttled=0x4")], 60, p)
    assert "current_power_or_throttle_flag" in now["exceeded"]


@pytest.mark.parametrize("changes", [{"duration_s": float("nan")}, {"duration_s": 0}, {"max_iterations": 0},
                                      {"minimum_iterations": 200000}, {"warmup_count": -1}, {"cache_mode": "automatic"}])
def test_invalid_benchmark_bounds_rejected(changes):
    with pytest.raises(ValueError):
        replace(LocalBenchmarkPolicy(), **changes).validate()


@pytest.fixture
def setup(tmp_path, monkeypatch):
    counts = {"extract": 0}
    class Matcher:
        policy = LocalMatchingPolicy(image_width=64, image_height=64, max_features=32)
        provenance = {"backend": "fixture_not_learned"}
        def extract(self, image):
            counts["extract"] += 1
            points = np.array([[x, y] for y in (8, 24, 40, 56) for x in (8, 24, 40, 56)], np.float32)
            return LocalFeatures(points, np.eye(16, 64, dtype=np.float32), np.ones(16, np.float32))
        def match(self, q, t):
            return np.c_[np.arange(16), np.arange(16)], np.ones(16, np.float32)
    monkeypatch.setattr("vision_nav.neural.local_benchmark.load_local_matcher", lambda path: Matcher())
    monkeypatch.setattr("vision_nav.neural.local_benchmark.hardware_identity", lambda: {"is_raspberry_pi": False})
    monkeypatch.setattr("vision_nav.neural.local_benchmark.resource_snapshot", lambda is_pi: resource())
    config = tmp_path / "local.json"
    config.write_text(json.dumps({"backend": "onnx_cpu"}))
    images = [tmp_path / "query.png", tmp_path / "map.png"]
    for path in images:
        cv2.imwrite(str(path), np.full((64, 64, 3), 120, np.uint8))
    return config, images, counts, Matcher


@pytest.mark.parametrize("cache_mode,expected", [("warm", 5), ("cold", 8)])
def test_cache_modes_and_warmup_exclusion(setup, tmp_path, cache_mode, expected):
    config, images, counts, _ = setup
    result = benchmark_local(config, images[:1], images[1:], tmp_path / "out",
        policy=LocalBenchmarkPolicy(duration_s=100, minimum_iterations=2, max_iterations=3, warmup_count=1, cache_mode=cache_mode))
    assert result["iteration_count"] == 3 and counts["extract"] == expected
    assert result["stage_latency_ms"]["total"]["count"] == 3
    assert ("map_preprocess_and_xfeat" in result["stage_latency_ms"]) == (cache_mode == "cold")
    assert result["limits"]["status"] == "incomplete"
    assert "requested_duration_not_reached" in result["limits"]["missing"]
    assert result["position_authority"] is False and result["px4_external_vision"] == "disabled"


def test_failure_is_saved_and_stops_load(setup, tmp_path):
    config, images, _, matcher = setup
    def fail(*args):
        raise RuntimeError("injected inference failure")
    matcher.match = fail
    result = benchmark_local(config, images[:1], images[1:], tmp_path / "out",
        policy=LocalBenchmarkPolicy(duration_s=100, max_iterations=3, minimum_iterations=2, warmup_count=0))
    assert result["status"] == "failed" and result["stop_reason"] == "inference_failure"
    assert result["iteration_count"] == 1
    row = json.loads((tmp_path / "out" / "iterations.jsonl").read_text())
    assert "injected inference failure" in row["reason"]


def test_hot_hardware_stops_before_model_load(setup, tmp_path, monkeypatch):
    config, images, counts, _ = setup
    monkeypatch.setattr("vision_nav.neural.local_benchmark.resource_snapshot", lambda is_pi: resource(temp=90))
    result = benchmark_local(config, images[:1], images[1:], tmp_path / "out")
    assert result["status"] == "failed" and counts["extract"] == 0
    assert "temperature_exceeds_limit" in result["limits"]["exceeded"]


def test_desktop_cannot_claim_pi_benchmark(setup, tmp_path):
    config, images, _, _ = setup
    with pytest.raises(ValueError, match="needs a Raspberry Pi"):
        benchmark_local(config, images[:1], images[1:], tmp_path / "out", policy=LocalBenchmarkPolicy(require_pi=True))


def test_output_is_not_overwritten(setup, tmp_path):
    config, images, _, _ = setup
    with pytest.raises(ValueError, match="already exists"):
        benchmark_local(config, images[:1], images[1:], tmp_path)
