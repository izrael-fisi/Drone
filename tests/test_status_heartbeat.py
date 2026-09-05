import json
from pathlib import Path
from queue import Queue
import threading

import pytest

from vision_nav.neural import status_heartbeat as module
from vision_nav.neural.status_heartbeat import ShadowStatusHeartbeat, write_status_snapshot


def base():
    return {"schema_version": "vision_nav_runtime_status_v3", "shadow_mode": True,
            "sequence": 0, "inference": {"provider": "onnxruntime_cpu", "frame_probe": {}},
            "active_map": {"release_id": "map@1"}, "active_model": {"release_id": "model@1"},
            "position": {"shadow_tracking_candidate": {"status": "candidate"}},
            "px4_publication": {"enabled": False}}


class Clock:
    def __init__(self):
        self.now = 1_000_000_000
    def __call__(self):
        return self.now


def monitor(emit=lambda _: None, **kwargs):
    return ShadowStatusHeartbeat(base(), emit, camera_status=lambda: {"status": "running"},
                                 attitude_status=lambda: {"status": "receiving"}, **kwargs)


def test_heartbeat_ages_result_without_refreshing_position_authority():
    clock = Clock()
    heartbeat = monitor(clock_ns=clock)
    status = base()
    status.update({"updated_at_utc": "2026-09-05T00:00:00Z", "sequence": 7,
                   "publication_decision": {"reason": "test_candidate"}})
    status["inference"] = {"latency_ms": {"local": 30., "bad": float("nan")},
                           "frame_probe": {"capture_timing": {"host_monotonic_ns": 100_000_000},
                                           "huge_details": "x" * 100_000}}
    heartbeat.set_phase("inference", iteration=7, capture_ns=100_000_000)
    heartbeat.completed(status)
    first = heartbeat.snapshot()
    assert first["inference"]["last_completed_result"]["capture_within_diagnostic_age_limit"]
    clock.now += 2_000_000_000
    current = heartbeat.snapshot()
    result = current["inference"]["last_completed_result"]
    assert result["sequence"] == 7 and result["completion_age_ms"] == 2000
    assert result["capture_age_ms"] == 2900 and not result["capture_within_diagnostic_age_limit"]
    assert result["capture_host_monotonic_ns"] == 100_000_000
    assert result["latency_ms"] == {"local": 30.}
    assert current["inference"]["frame_probe"] == {}
    assert current["position"]["shadow_tracking_candidate"]["status"] == "withheld"
    assert current["position"]["navigation_mode"] == "NO_GLOBAL_FIX"
    assert current["px4_publication"]["enabled"] is False
    assert current["publication_decision"]["published"] is False
    assert len(json.dumps(current)) < 6000
    status["active_map"]["release_id"] = "changed"
    assert heartbeat.snapshot()["active_map"]["release_id"] == "map@1"


def test_phase_duration_reports_overdue_without_canceling_or_faking_result():
    clock = Clock()
    heartbeat = monitor(clock_ns=clock)
    heartbeat.set_phase("inference", iteration=3, capture_ns=clock.now)
    clock.now += 2_001_000_000
    status = heartbeat.snapshot()
    assert status["heartbeat"]["inference_overdue"] is True
    assert status["heartbeat"]["phase_age_ms"] == 2001
    assert status["heartbeat"]["current_capture_age_ms"] == 2001
    assert status["inference"]["last_completed_result"] is None
    heartbeat.set_phase("waiting_for_frame", iteration=4)
    assert not heartbeat.snapshot()["heartbeat"]["inference_overdue"]


def test_independent_publication_live_health_and_single_writer():
    emitted = Queue()
    health = {"status": "running", "count": 1}
    owners = []
    def emit(status):
        owners.append(threading.get_ident())
        emitted.put(status)
        return None
    heartbeat = ShadowStatusHeartbeat(base(), emit, camera_status=lambda: health,
        attitude_status=lambda: {"status": "receiving"}, interval_s=0.005)
    heartbeat.start()
    first = emitted.get(timeout=2)
    health.update({"status": "failed", "count": 2})
    heartbeat.set_phase("inference", iteration=9)
    # No main-thread inference completion is required for the next heartbeat.
    current = emitted.get(timeout=2)
    for _ in range(5):
        if current["heartbeat"]["phase"] == "inference":
            break
        current = emitted.get(timeout=2)
    assert current["heartbeat"]["phase"] == "inference"
    assert current["camera_health"]["status"] == "failed"
    assert current["sequence"] > first["sequence"]
    assert current["heartbeat"]["run_id"] == first["heartbeat"]["run_id"]
    assert current["heartbeat"]["result_iteration"] == 9
    assert heartbeat.stop()
    assert len(set(owners)) == 1 and owners[0] != threading.get_ident()
    last = current
    while not emitted.empty():
        last = emitted.get_nowait()
    assert last["heartbeat"]["phase"] == "stopped"
    with pytest.raises(RuntimeError, match="restarted"):
        heartbeat.start()


def test_health_probe_failure_visible_without_stopping_heartbeat():
    def failed():
        raise OSError("camera unavailable")
    heartbeat = ShadowStatusHeartbeat(base(), lambda _: None, camera_status=failed,
                                     attitude_status=lambda: {"status": "receiving"})
    status = heartbeat.snapshot()
    assert status["camera_health"]["status"] == "probe_failed"
    assert "camera unavailable" in status["camera_health"]["error"]
    assert status["attitude_health"]["status"] == "receiving"


def test_transport_errors_reported_on_next_emission_then_clear():
    results = []
    def emit(status):
        results.append(status)
        return "UDP unavailable" if len(results) == 1 else None
    heartbeat = monitor(emit)
    heartbeat._publish()
    heartbeat._publish()
    heartbeat._publish()
    assert results[1]["heartbeat"]["previous_transport_error"] == "UDP unavailable"
    assert results[2]["heartbeat"]["previous_transport_error"] is None


def test_fatal_writer_failure_and_blocked_shutdown_are_not_success():
    entered, release = threading.Event(), threading.Event()
    def emit(_):
        entered.set()
        assert release.wait(2)
        raise OSError("disk full")
    heartbeat = monitor(emit)
    heartbeat.start()
    assert entered.wait(2)
    assert heartbeat.stop(timeout_s=0) is False
    release.set()
    assert heartbeat.stop()
    with pytest.raises(RuntimeError, match="disk full"):
        heartbeat.check()
    assert "disk full" in heartbeat.snapshot()["heartbeat"]["publisher_error"]


def test_oversized_heartbeat_is_rejected_not_silently_truncated():
    data = base()
    data["active_map"]["unexpected"] = "x" * 60_000
    called = []
    heartbeat = ShadowStatusHeartbeat(data, lambda _: called.append(True),
        camera_status=lambda: {}, attitude_status=lambda: {})
    with pytest.raises(ValueError, match="transport bound"):
        heartbeat._publish()
    assert not called


@pytest.mark.parametrize("kwargs", [{"interval_s": 0}, {"interval_s": True},
    {"inference_warning_ms": -1}, {"inference_warning_ms": float("nan")}, {"max_frame_age_ms": float("inf")}])
def test_heartbeat_invalid_timing_policy(kwargs):
    with pytest.raises(ValueError):
        monitor(**kwargs)


def test_status_snapshot_replace_is_atomic_and_preserves_previous_on_failure(tmp_path, monkeypatch):
    path = tmp_path / "runtime_status.json"
    write_status_snapshot(path, {"counter": 1})
    original = module.os.replace
    seen = []
    def inspect(source, target):
        seen.append((json.loads(path.read_text()), json.loads(Path(source).read_text())))
        original(source, target)
    monkeypatch.setattr(module.os, "replace", inspect)
    write_status_snapshot(path, {"counter": 2})
    assert seen == [({"counter": 1}, {"counter": 2})]
    assert json.loads(path.read_text()) == {"counter": 2}
    def fail(*_):
        raise OSError("replace failed")
    monkeypatch.setattr(module.os, "replace", fail)
    with pytest.raises(OSError):
        write_status_snapshot(path, {"counter": 3})
    assert json.loads(path.read_text()) == {"counter": 2}
    assert not list(tmp_path.glob(".runtime_status.json-*"))
    with pytest.raises(ValueError):
        write_status_snapshot(path, {"bad": float("nan")})
    assert json.loads(path.read_text()) == {"counter": 2}


def test_compact_heartbeat_udp_and_local_snapshot(tmp_path, monkeypatch):
    import socket
    from vision_nav.neural import shadow_runtime as runtime

    status = monitor().snapshot()
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as receiver:
        receiver.bind(("127.0.0.1", 0))
        receiver.settimeout(1.0)
        target = f"127.0.0.1:{receiver.getsockname()[1]}"
        assert runtime._emit_heartbeat(tmp_path, target, status) is None
        payload, _ = receiver.recvfrom(65_535)
    assert len(payload) <= module.MAX_HEARTBEAT_BYTES
    assert json.loads(payload) == json.loads((tmp_path / "runtime_status.json").read_text()) == status
    def failed_send(*_):
        raise OSError("network unavailable")
    monkeypatch.setattr(runtime, "_send_udp", failed_send)
    assert "network unavailable" in runtime._emit_heartbeat(tmp_path, target, status)
    assert json.loads((tmp_path / "runtime_status.json").read_text()) == status


def test_pi_status_readout_distinguishes_heartbeat_and_result(tmp_path):
    import os
    import subprocess

    clock = Clock()
    heartbeat = monitor(clock_ns=clock)
    heartbeat.set_phase("inference", iteration=9)
    clock.now += 2_001_000_000
    write_status_snapshot(tmp_path / "runtime_status.json", heartbeat.snapshot())
    script = Path(__file__).resolve().parents[1] / "scripts/pi/read_runtime_status.sh"
    result = subprocess.run(["bash", str(script)], env={**os.environ, "VISION_NAV_RUNTIME_STATUS_ROOTS": str(tmp_path)},
                            text=True, capture_output=True, timeout=10, check=True)
    assert "heartbeat: phase=inference" in result.stdout
    assert "iteration=9 phase_age_ms=2001.0 overdue=True" in result.stdout
    assert "last completed result: iteration=None" in result.stdout
    assert "PX4 publication: enabled=False" in result.stdout
