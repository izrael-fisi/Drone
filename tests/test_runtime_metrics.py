import json
from pathlib import Path
import threading

import pytest

from vision_nav.neural.hardware_resources import _process_rss_bytes, _thermal_snapshot, process_io_counters
from vision_nav.neural.runtime_metrics import RuntimeMetricsRecorder


RUN_ID = "11111111-2222-4333-8444-555555555555"
HARDWARE = {"is_raspberry_pi": False, "rss_measurement": "test_only"}


class Clock:
    ns = 1_000_000_000
    cpu_ns = 100_000_000
    def __call__(self):
        return self.ns
    def cpu(self):
        return self.cpu_ns


def recorder(tmp_path, **kwargs):
    return RuntimeMetricsRecorder(tmp_path, run_id=RUN_ID, activity=lambda: {"phase": "inference"},
        hardware=HARDWARE, probe=kwargs.pop("probe", lambda: {"rss_bytes": 1234, "max_temperature_c": None}),
        io_probe=kwargs.pop("io_probe", lambda: {"status": "unavailable"}), **kwargs)


def test_cpu_and_io_deltas_are_interval_measurements_not_physical_writes(tmp_path):
    clock = Clock()
    io = {"status": "available", "read_bytes": 10, "write_bytes": 100, "cancelled_write_bytes": 0}
    worker = recorder(tmp_path, clock_ns=clock, cpu_clock_ns=clock.cpu, io_probe=lambda: io)
    first = worker._sample()
    assert first["process_cpu_percent_one_core_scale"] is None
    assert first["process_io_delta"] is None
    clock.ns += 1_000_000_000
    clock.cpu_ns += 2_000_000_000
    io.update({"read_bytes": 20, "write_bytes": 150, "cancelled_write_bytes": 8})
    second = worker._sample()
    assert second["process_cpu_percent_one_core_scale"] == 200
    assert second["sample_interval_ms"] == 1000
    assert second["process_io_delta"] == {"read_bytes": 10, "write_bytes": 50, "cancelled_write_bytes": 8}
    assert first["process_io"]["write_bytes"] == 100
    clock.ns += 1_000_000_000
    io["write_bytes"] = 1
    third = worker._sample()
    assert third["process_io_delta"] is None
    assert third["interval_reason"] == "nonmonotonic_process_io_counter"


def test_nonmonotonic_cpu_time_does_not_produce_negative_usage(tmp_path):
    clock = Clock()
    worker = recorder(tmp_path, clock_ns=clock, cpu_clock_ns=clock.cpu)
    worker._sample()
    clock.ns += 1_000_000_000
    clock.cpu_ns -= 1
    result = worker._sample()
    assert result["process_cpu_percent_one_core_scale"] is None
    assert result["interval_reason"] == "nonmonotonic_resource_counter"


def test_recorder_is_independent_bounded_and_preserves_sample_age(tmp_path):
    finished, owners = threading.Event(), []
    clock = Clock()
    def probe():
        owners.append(threading.get_ident())
        if len(owners) == 3:
            finished.set()
        return {"rss_bytes": 5000, "max_temperature_c": None}
    worker = recorder(tmp_path, probe=probe, interval_s=0.001, max_samples=3,
                      clock_ns=clock, cpu_clock_ns=clock.cpu)
    worker.start()
    assert finished.wait(2)
    assert worker.stop()
    assert worker.status()["sample_count"] == 3
    assert worker.status()["status"] == "sample_limit_reached"
    assert len(set(owners)) == 1 and owners[0] != threading.get_ident()
    rows = [json.loads(line) for line in worker.path.read_text().splitlines()]
    assert [row["record_type"] for row in rows] == ["header", "sample", "sample", "sample", "footer"]
    assert rows[0]["hardware"] == HARDWARE
    assert rows[-1]["sample_count"] == 3
    assert rows[1]["activity"]["phase"] == "inference"
    clock.ns += 2_000_000_000
    assert worker.status()["sample_age_ms"] == 2000
    assert "activity" not in worker.status()["latest"]
    assert all(row["position_authority"] is False for row in rows)
    with pytest.raises(RuntimeError, match="restarted"):
        worker.start()


def test_blocked_probe_does_not_hold_status_lock_or_fake_shutdown(tmp_path):
    entered, release = threading.Event(), threading.Event()
    def probe():
        entered.set()
        assert release.wait(2)
        return {"rss_bytes": None}
    worker = recorder(tmp_path, probe=probe)
    worker.start()
    assert entered.wait(2)
    assert worker.status()["status"] == "running"
    assert worker.status()["latest"] is None
    assert worker.stop(timeout_s=0) is False
    assert worker.status()["status"] == "shutdown_timeout"
    release.set()
    assert worker.stop()


def test_existing_log_is_never_overwritten_and_probe_errors_are_visible(tmp_path):
    worker = recorder(tmp_path)
    worker.path.write_text("existing evidence\n")
    worker.start()
    assert worker.stop()
    assert worker.status()["status"] == "failed"
    assert "FileExistsError" in worker.status()["error"]
    assert worker.path.read_text() == "existing evidence\n"
    entered = threading.Event()
    def probe():
        entered.set()
        raise OSError("sensor unavailable")
    other = recorder(tmp_path / "other", probe=probe)
    other.start()
    assert entered.wait(2)
    assert other.stop()
    assert other.status()["status"] == "failed"
    assert "sensor unavailable" in other.status()["error"]
    assert other.status()["sample_count"] == 0


@pytest.mark.parametrize("kwargs", [{"interval_s": 0}, {"interval_s": True}, {"interval_s": float("nan")},
    {"max_samples": 0}, {"max_samples": 36001}, {"max_samples": True}])
def test_invalid_recorder_limits(tmp_path, kwargs):
    with pytest.raises(ValueError):
        recorder(tmp_path, **kwargs)


def test_proc_rss_unavailable_is_not_a_zero_or_peak_measurement(tmp_path):
    path = tmp_path / "status"
    path.write_text("VmRSS:\t1234 kB\n")
    assert _process_rss_bytes(path) == 1234 * 1024
    for raw in ("VmRSS: -1 kB\n", "VmRSS: 1234 bytes\n", "VmRSS: invalid kB\n", "missing\n"):
        path.write_text(raw)
        assert _process_rss_bytes(path) is None


def test_thermal_sysfs_uses_millidegrees_even_for_low_or_negative_values(tmp_path):
    for index, value in enumerate(("50000", "200", "-5000", "nan")):
        directory = tmp_path / f"thermal_zone{index}"
        directory.mkdir()
        (directory / "temp").write_text(value)
        (directory / "type").write_text("test")
    result = _thermal_snapshot(tmp_path)
    assert result["max_temperature_c"] == 50
    assert [sample["temperature_c"] for sample in result["samples"]] == [50, 0.2, -5]
    assert _thermal_snapshot(tmp_path / "missing")["status"] == "unavailable"


def test_io_probe_never_invents_counters(tmp_path):
    path = tmp_path / "io"
    assert process_io_counters(path)["status"] == "unavailable"
    path.write_text("read_bytes: 3\nwrite_bytes: 7\ncancelled_write_bytes: 2\n")
    assert process_io_counters(path) == {"status": "available", "read_bytes": 3, "write_bytes": 7, "cancelled_write_bytes": 2}
    path.write_text("read_bytes: 3\nwrite_bytes: 7\n")
    assert process_io_counters(path)["status"] == "unavailable"
