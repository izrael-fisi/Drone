from dataclasses import replace
from queue import Queue
import threading

import pytest

from vision_nav.mavlink_bridge import MavlinkTelemetrySample
from vision_nav.neural.attitude_collector import MavlinkAttitudeCollector


class Clock:
    def __init__(self):
        self.ns = 1_000_000_000
    def __call__(self):
        return self.ns


def attitude(stamp=1000, **changes):
    return replace(MavlinkTelemetrySample("ATTITUDE", 1, vehicle_timestamp_us=stamp,
                   vehicle_time_domain="px4_boot_us", roll_rad=0.1, pitch_rad=-0.2, yaw_rad=0.3,
                   source_system=1, source_component=1), **changes)


class ControlledLink:
    def __init__(self, clock):
        self.clock = clock
        self.owner = threading.get_ident()
        self.requests, self.commands = Queue(), Queue()
        self.closed = threading.Event()
        self.count = 0
    def connect(self):
        assert threading.get_ident() == self.owner
    def try_read_telemetry(self, timeout_s):
        assert threading.get_ident() == self.owner
        assert timeout_s == 0.05
        self.requests.put(self.count)
        self.count += 1
        command = self.commands.get(timeout=5)
        if isinstance(command, Exception):
            raise command
        host_ns, sample = command
        self.clock.ns = host_ns
        return sample
    def close(self):
        assert threading.get_ident() == self.owner
        self.closed.set()


def start(*, capacity=4):
    created, clock = Queue(), Clock()
    def factory():
        link = ControlledLink(clock)
        created.put(link)
        return link
    collector = MavlinkAttitudeCollector(factory, capacity=capacity, clock_ns=clock)
    collector.start()
    link = created.get(timeout=2)
    assert link.requests.get(timeout=2) == 0
    assert link.owner != threading.get_ident()
    return collector, link, clock


def send(link, sample, host_ns):
    link.commands.put((host_ns, sample))
    link.requests.get(timeout=2)


def finish(collector, link):
    assert collector.stop(timeout_s=0) is False
    assert collector.status()["status"] == "shutdown_timeout"
    assert not link.closed.is_set()
    link.commands.put((link.clock.ns, None))
    assert collector.stop(timeout_s=2)
    assert link.closed.is_set()
    assert not collector.status()["thread_alive"]


def test_reader_runs_without_consumer_and_reports_overflow_and_metadata():
    collector, link, clock = start(capacity=2)
    try:
        for n in range(1, 5):
            send(link, attitude(n * 1000), 1_000_000_000 + n * 10_000_000)
        status = collector.status()
        assert status["accepted_count"] == 4
        assert status["overflow_count"] == status["generation"] == 2
        assert status["pending_count"] == status["capacity"] == 2
        assert status["latest_attitude"]["vehicle_timestamp_us"] == 4000
        assert status["latest_attitude"]["source_system"] == 1
        assert status["sensor_timing_calibrated"] is False
        assert status["mavlink_transmission_enabled"] is False
        batch = collector.drain()
        assert len(batch["samples"]) == 2 and batch["health"]["status"] == "receiving"
        assert [sample["host_monotonic_ns"] for sample in batch["samples"]] == [1_030_000_000, 1_040_000_000]
        assert collector.status()["pending_count"] == 0
    finally:
        finish(collector, link)


def test_other_senders_and_anonymous_samples_never_enter_context():
    collector, link, clock = start()
    try:
        for changes in ({"source_system": 2}, {"source_component": 197}, {"source_system": None}):
            send(link, attitude(**changes), clock.ns + 1_000_000)
        assert collector.status()["filtered_source_count"] == 3
        assert collector.status()["status"] == "waiting"
        assert not collector.drain()["samples"]
        send(link, attitude(), clock.ns + 1_000_000)
        assert collector.status()["status"] == "receiving"
    finally:
        finish(collector, link)


@pytest.mark.parametrize("changes", [{"roll_rad": None}, {"pitch_rad": float("nan")},
    {"yaw_rad": float("inf")}, {"roll_rad": True}, {"vehicle_timestamp_us": None},
    {"vehicle_timestamp_us": -1}, {"vehicle_time_domain": "unix_epoch_us"}])
def test_invalid_expected_sender_breaks_context(changes):
    collector, link, clock = start()
    try:
        send(link, attitude(), clock.ns + 1_000_000)
        send(link, attitude(2000, **changes), clock.ns + 1_000_000)
        status = collector.status()
        assert status["status"] == "invalid"
        assert status["invalid_count"] == status["generation"] == 1
        assert not collector.drain()["samples"]
        send(link, attitude(3000), clock.ns + 1_000_000)
        batch = collector.drain()
        assert batch["health"]["status"] == "receiving" and len(batch["samples"]) == 1
        assert batch["health"]["generation"] == 1
    finally:
        finish(collector, link)


@pytest.mark.parametrize("fault", ["duplicate", "reordered", "host_clock"])
def test_timestamp_discontinuities_do_not_refresh_old_attitude(fault):
    collector, link, clock = start()
    try:
        send(link, attitude(3000), clock.ns + 1_000_000)
        stamp = 4000 if fault == "host_clock" else 3000 if fault == "duplicate" else 2000
        host_ns = clock.ns if fault == "host_clock" else clock.ns + 1_000_000
        send(link, attitude(stamp), host_ns)
        assert collector.status()["invalid_count"] == 1
        assert collector.status()["generation"] == 1
        assert collector.drain()["samples"] == []
    finally:
        finish(collector, link)


def test_stale_samples_discarded_and_recovery_marks_gap():
    collector, link, clock = start()
    try:
        send(link, attitude(), clock.ns + 1_000_000)
        clock.ns += 501_000_000
        batch = collector.drain()
        assert batch["health"]["status"] == "stale" and not batch["samples"]
        send(link, attitude(2000), clock.ns + 1_000_000)
        batch = collector.drain()
        assert batch["health"]["generation"] == 1
        assert batch["health"]["last_discontinuity_reason"] == "attitude_reception_gap"
        assert batch["health"]["status"] == "receiving" and len(batch["samples"]) == 1
    finally:
        finish(collector, link)


def test_connection_read_failure_visible_and_discards_pending_context():
    collector, link, clock = start()
    send(link, attitude(), clock.ns + 1_000_000)
    link.commands.put(OSError("USB disconnected"))
    assert link.closed.wait(2)
    assert collector.stop(timeout_s=2)
    batch = collector.drain()
    assert batch["health"]["status"] == "failed" and not batch["samples"]
    assert "USB disconnected" in batch["health"]["error"]
    assert batch["health"]["generation"] == 1


def test_factory_failure_and_lifecycle():
    def failure():
        raise RuntimeError("no serial device")
    collector = MavlinkAttitudeCollector(failure)
    collector.start()
    # Joining the owner gives deterministic failure completion without sleeps.
    assert collector.stop(timeout_s=2)
    assert "no serial device" in collector.status()["error"]
    assert collector.status()["status"] == "failed"
    with pytest.raises(RuntimeError, match="restarted"):
        collector.start()
    never_started = MavlinkAttitudeCollector(failure)
    assert never_started.stop()
    with pytest.raises(RuntimeError):
        never_started.start()


@pytest.mark.parametrize("failure_at", ["connect", "close"])
def test_link_lifecycle_errors_stay_on_owner_thread(failure_at):
    calls, created = [], threading.Event()
    class Link:
        def __init__(self):
            calls.append(threading.get_ident())
        def connect(self):
            calls.append(threading.get_ident())
            created.set()
            if failure_at == "connect":
                raise OSError("connect failure")
        def try_read_telemetry(self, timeout_s):
            return None
        def close(self):
            calls.append(threading.get_ident())
            if failure_at == "close":
                raise OSError("close failure")
    collector = MavlinkAttitudeCollector(Link)
    collector.start()
    assert created.wait(2)
    assert collector.stop(timeout_s=2)
    assert collector.status()["status"] == "failed"
    assert failure_at in collector.status()["error"]
    assert len(calls) == 3 and len(set(calls)) == 1 and calls[0] != threading.get_ident()


@pytest.mark.parametrize("kwargs", [{"capacity": 0}, {"capacity": 4097}, {"capacity": True},
    {"target_system": 0}, {"target_component": 256}, {"target_system": True},
    {"stale_after_ms": 0}, {"stale_after_ms": float("nan")}, {"stale_after_ms": True}])
def test_invalid_policies(kwargs):
    with pytest.raises(ValueError):
        MavlinkAttitudeCollector(lambda: None, **kwargs)
