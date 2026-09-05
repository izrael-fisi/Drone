"""Explicit native-child accounting never falls back to the parent or a new PID."""

from collections import namedtuple
import os
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from vision_nav.runtime import process_resources as module


RUN_ID = "11111111-2222-4333-8444-555555555555"


def fake_process(monkeypatch):
    class ProbeError(Exception):
        pass

    class Process:
        born, parent, alive, state = 100., os.getpid(), True, "running"
        fail = None

        def ppid(self):
            return self.parent

        def is_running(self):
            return self.alive

        def create_time(self):
            return self.born

        def status(self):
            return self.state

        def cpu_times(self):
            if self.fail:
                raise ProbeError(self.fail)
            return namedtuple("CPU", "user system children_user children_system")(.2, .3, 20., 30.)

        def memory_info(self):
            return SimpleNamespace(rss=4096)

    process = Process()
    monkeypatch.setattr(module, "psutil", SimpleNamespace(Process=lambda _: process, Error=ProbeError,
                        STATUS_ZOMBIE="zombie", __version__="test"))
    monkeypatch.setattr(module, "process_io_counters", lambda _: {"status": "unavailable"})
    return process


def test_cpu_excludes_grandchildren_and_rss_is_child_current_value(monkeypatch):
    fake_process(monkeypatch)
    probe = module.OwnedProcessResources(99999, RUN_ID)
    result = probe.snapshot()
    assert result["status"] == "available"
    assert result["process_cpu_ns"] == 500_000_000
    assert result["rss_bytes"] == 4096
    assert result["identity"]["pid"] == 99999
    assert result["identity"]["parent_pid"] == os.getpid()
    assert not result["position_authority"]


@pytest.mark.parametrize("change", ["birth", "parent", "exit", "zombie", "permission", "during_read"])
def test_process_reuse_exit_and_permission_loss_withhold_counters(monkeypatch, change):
    process = fake_process(monkeypatch)
    probe = module.OwnedProcessResources(99999, RUN_ID)
    if change == "birth":
        process.born += 1
    elif change == "parent":
        process.parent += 1
    elif change == "exit":
        process.alive = False
    elif change == "zombie":
        process.state = "zombie"
    elif change == "permission":
        process.fail = "access denied"
    else:
        original = process.cpu_times

        def switch_pid():
            process.born += 1
            return original()

        process.cpu_times = switch_pid
    result = probe.snapshot()
    assert result["status"] == "unavailable"
    assert "rss_bytes" not in result and "process_cpu_ns" not in result


def test_dependency_missing_or_unrelated_process_never_uses_self_resources(monkeypatch):
    process = fake_process(monkeypatch)
    process.parent = os.getpid()+1
    assert module.OwnedProcessResources(99999, RUN_ID).snapshot()["status"] == "unavailable"
    monkeypatch.setattr(module, "psutil", None)
    result = module.OwnedProcessResources(99999, RUN_ID).snapshot()
    assert result["reason"] == "psutil_not_installed"
    assert "rss_bytes" not in result


def test_actual_owned_child_cpu_and_memory_are_measured_until_exit():
    pytest.importorskip("psutil")
    code = """import sys, time
data = bytearray(32*1024*1024)
print('ready', flush=True)
end = time.monotonic()+3
while time.monotonic() < end:
    sum(range(10000))
sys.stdin.readline()
"""
    child = subprocess.Popen([sys.executable, "-c", code], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
    try:
        probe = module.OwnedProcessResources(child.pid, RUN_ID)
        assert child.stdout.readline().strip() == "ready"
        first = probe.snapshot()
        time.sleep(.2)
        second = probe.snapshot()
        assert first["status"] == second["status"] == "available"
        assert second["process_cpu_ns"] > first["process_cpu_ns"]
        assert second["rss_bytes"] > 10*1024*1024
        assert first["identity"] == second["identity"]
    finally:
        child.terminate()
        child.wait(timeout=5)
        child.stdin.close()
        child.stdout.close()
    assert probe.snapshot()["status"] == "unavailable"
