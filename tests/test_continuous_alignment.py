"""Independent refresh with actual isolated estimation and synthetic sensors/noise."""

from copy import deepcopy
import json
from pathlib import Path
import threading
import time

import numpy as np
import pytest

from vision_nav.estimation.alignment_worker import ProcessAlignmentEngine
from vision_nav.estimation.live_alignment import LiveAlignmentObserver
from vision_nav.estimation.pnp_window_alignment import align
from test_delayed_alignment import later_record
from test_learned_pnp_binding import envelope
from test_live_alignment_observer import Source, wait_for
from test_pnp_window_alignment import fixture


@pytest.fixture
def setup(tmp_path):
    observers = []

    def create(*, age_ns=5_000_000_000, provider_config=None, timeout_s=2.):
        request, _ = fixture(shared=False, vio_cross=False)
        source = Source(request["config"])
        engine = ProcessAlignmentEngine(Path(__file__).parent / "fixtures/alignment_noise.py",
            provider_config=provider_config, operation_timeout_s=timeout_s)
        observer = LiveAlignmentObserver(source, request["config"], process_engine=engine,
            continuous_refresh_interval_s=.02, poll_interval_s=.005, max_snapshot_age_ns=age_ns)
        observers.append(observer)
        row, binding, _ = envelope(request["fixes"][0]["request"], worker_run_id=source.native.worker_run_id)
        observer.start(binding["provenance"], evidence_path=tmp_path / f"alignment-{len(observers)}.jsonl")
        source.state, source.native_state = "collecting", "running"
        source.push(request["native_record"])
        wait_for(lambda: observer.status()["counts"].get("native_snapshots") == 1)
        return source, observer, row, {"tickets": [binding["frame_ticket"]]}, request

    yield create
    for observer in observers:
        assert observer.stop()
        assert not observer.status()["continuous_refresh"]["thread_alive"]
        assert not observer.engine.status()["process_alive"]


def current(observer):
    return observer.status()["continuous_refresh"]["latest_snapshot_estimate"]


def rows(observer):
    return [json.loads(line) for line in Path(observer.status()["continuous_refresh"]["evidence_path"]).read_text().splitlines()]


def test_new_vio_poses_refresh_without_another_learned_frame(setup):
    source, observer, row, item, _ = setup()
    queued = observer.observe(item, row)
    assert queued["status"] == "queued_for_alignment" and queued["current_estimate"] is None
    wait_for(lambda: current(observer) is not None)
    first = current(observer)
    assert first["pose_timestamp_ns"] == 2*10**9
    source.push(later_record())
    wait_for(lambda: (estimate := current(observer)) is not None and estimate["pose_timestamp_ns"] == 3*10**9)
    second = current(observer)
    assert not np.allclose(first["map_T_imu"], second["map_T_imu"])
    assert observer.coordinator.status()["ready_queries"] == 1
    assert observer.finish() and observer.stop()
    updates = [r for r in rows(observer) if r["status"] == "research_current_alignment"]
    assert {r["current_estimate"]["pose_timestamp_ns"] for r in updates} == {2*10**9, 3*10**9}
    for update in updates:
        assert update["map_evidence_timestamp_ns"] == 10**9
        assert update["map_evidence_age_at_pose_ns"] == update["current_estimate"]["pose_timestamp_ns"]-10**9
        assert update["position_source"] == "research_vio_between_fixes"
        assert len(update["replay_inputs"][-1]["fixes"]) == 1
        assert align(update["replay_inputs"][-1])["alignment"] == update["current_estimate"]
        assert not update["position_authority"] and not update["covariance_calibrated"]
        assert update["navigation_mode"] == "NO_GLOBAL_FIX" and update["px4_external_vision"] == "disabled"
    assert current(observer) is None


def test_same_snapshot_is_not_repeatedly_counted_as_new_information(setup):
    _, observer, row, item, _ = setup()
    observer.observe(item, row)
    wait_for(lambda: current(observer) is not None and not observer.status()["continuous_refresh"]["busy"])
    initial = observer.status()["continuous_refresh"]["event_count"]
    requests = observer.engine.status()["submitted_requests"]
    time.sleep(.15)
    assert observer.status()["continuous_refresh"]["event_count"] == initial
    assert observer.engine.status()["submitted_requests"] == requests
    assert observer.observe(item, row)["reason"] == "duplicate_or_reordered_alignment_submission"


def test_expired_map_anchor_withholds_instead_of_restamping_last_pose(setup):
    source, observer, row, item, request = setup()
    observer.observe(item, row)
    wait_for(lambda: current(observer) is not None)
    newer = deepcopy(request["native_record"])
    newer["timestamp_ns"] = newer["pose_window"]["posterior_timestamp_ns"] = 4*10**9
    newer["pose_window"]["pose_timestamps_ns"] = [3*10**9, 4*10**9]
    source.push(newer)
    wait_for(lambda: observer.status()["continuous_refresh"]["last_event"]["reason"] == "no_completed_retained_queries")
    assert current(observer) is None and observer.coordinator.status()["ready_queries"] == 0
    assert observer.finish() and observer.stop()
    assert rows(observer)[-1]["current_estimate"] is None


def test_stale_snapshot_is_withheld_without_any_new_sensor_or_map_input(setup):
    _, observer, row, item, _ = setup(age_ns=2_000_000_000)
    observer.observe(item, row)
    wait_for(lambda: current(observer) is not None)
    wait_for(lambda: observer.status()["continuous_refresh"]["last_event"]["reason"] == "vio_snapshot_receipt_stale")
    assert current(observer) is None


def test_pending_map_drafts_are_latest_only_while_estimator_is_busy(setup):
    source, observer, row, item, request = setup(provider_config={"fault_stage": "bind", "delay_s": .3})
    observer.observe(item, row)
    wait_for(lambda: observer.engine.status()["submitted_requests"] == 1)
    source.push(later_record())
    wait_for(lambda: observer.status()["counts"].get("native_snapshots") == 2)
    req2 = deepcopy(request["fixes"][1]["request"])
    row2, binding2, _ = envelope(req2, worker_run_id=source.native.worker_run_id)
    second = observer.observe({"tickets": [binding2["frame_ticket"]]}, row2)
    req2["context"]["timestamp_ns"] = 3*10**9
    row3, binding3, _ = envelope(req2, worker_run_id=source.native.worker_run_id)
    third = observer.observe({"tickets": [binding3["frame_ticket"]]}, row3)
    assert second["status"] == third["status"] == "queued_for_alignment"
    assert third["superseded_draft_sha256"] == second["draft_sha256"]
    assert observer.status()["continuous_refresh"]["pending_drafts"] <= 1
    assert observer.finish() and observer.stop()
    events = rows(observer)
    assert not any(r["draft_sha256"] == second["draft_sha256"] for r in events)
    assert any(r["draft_sha256"] == third["draft_sha256"] for r in events)
    assert observer.status()["counts"]["superseded_pending_drafts"] == 1


@pytest.mark.parametrize("fault", ["sensor", "timeout", "stop"])
def test_failure_and_shutdown_cannot_leave_an_authoritative_or_live_estimate(setup, fault):
    source, observer, row, item, _ = setup(provider_config={"fault_stage": "bind", "delay_s": 60}, timeout_s=.2)
    observer.observe(item, row)
    wait_for(lambda: observer.engine.status()["submitted_requests"] == 1)
    if fault == "sensor":
        source.state = "failed"
    elif fault == "stop":
        assert observer.stop()
    if fault != "stop":
        wait_for(lambda: observer.status()["error"] is not None)
    assert observer.stop()
    assert current(observer) is None
    assert not observer.status()["continuous_refresh"]["thread_alive"]
    assert not observer.engine.status()["process_alive"]
    assert all(r["current_estimate"] is None for r in rows(observer))


@pytest.mark.parametrize("interval", [0, -1, .001, 6, True, float("nan")])
def test_invalid_cadence_and_unbounded_providers_are_rejected(interval):
    request, _ = fixture()
    source = Source(request["config"])
    engine = ProcessAlignmentEngine(Path(__file__).parent / "fixtures/alignment_noise.py")
    with pytest.raises(ValueError):
        LiveAlignmentObserver(source, request["config"], process_engine=engine, continuous_refresh_interval_s=interval)
    assert engine.stop()


def test_status_rejects_expired_member_of_a_cached_multi_fix_solution(setup):
    source, observer, row, item, request = setup()
    observer.observe(item, row)
    wait_for(lambda: current(observer) is not None)
    second, binding, _ = envelope(request["fixes"][1]["request"], worker_run_id=source.native.worker_run_id)
    observer.observe({"tickets": [binding["frame_ticket"]]}, second)
    wait_for(lambda: current(observer) is not None and len(current(observer)["fix_ids"]) == 2)
    # Explicitly expire just the older job without changing the latest pose or
    # latest map timestamp; status must compare the entire retained evidence set.
    with observer._condition, observer.coordinator._lock:
        first = next(iter(observer.coordinator._jobs.values()))
        first["ticket"]["dispatched_host_monotonic_ns"] -= observer.coordinator.max_age+1
        assert current(observer) is None


def test_cli_rejects_continuous_refresh_without_a_noise_provider():
    from vision_nav.neural.live_sensor_inference import main

    args = ["--propellers-removed", "--endpoint", "synthetic", "--alignment-refresh-interval-s", ".1"]
    for name in ("binary", "checkout", "sensor-profile", "output", "export-dir", "reference-map",
                 "tile-layout", "calibration", "camera-to-body", "retrieval-export", "retrieval-index"):
        args += ["--"+name, "not-opened"]
    with pytest.raises(SystemExit) as error:
        main(args)
    assert error.value.code == 2


def test_idle_estimator_exit_invalidates_cached_pose_and_monitor(setup):
    _, observer, row, item, _ = setup()
    observer.observe(item, row)
    wait_for(lambda: current(observer) is not None and not observer.status()["continuous_refresh"]["busy"])
    process = observer.engine._process
    process.terminate()
    process.join(2.)
    assert current(observer) is None
    wait_for(lambda: observer.status()["error"] is not None)
    assert observer.stop()
    assert not observer.status()["continuous_refresh"]["thread_alive"]


def test_journal_failure_is_terminal_not_silent_loss_of_evidence(setup, monkeypatch):
    _, observer, row, item, _ = setup()
    wait_for(lambda: not observer.status()["continuous_refresh"]["busy"])
    def failed_write(*args, **kwargs):
        raise OSError("synthetic full evidence volume")
    monkeypatch.setattr(observer, "_write_update", failed_write)
    observer.observe(item, row)
    wait_for(lambda: observer.status()["error"] is not None)
    assert "synthetic full evidence volume" in observer.status()["error"]
    assert current(observer) is None
    assert observer.stop()


@pytest.mark.parametrize("fail", [False, True])
def test_compact_status_does_not_wait_for_full_replay_write(setup, fail):
    source, observer, row, item, _ = setup()
    wait_for(lambda: not observer.status()["continuous_refresh"]["busy"])
    entered, release = threading.Event(), threading.Event()
    stream = observer._updates_stream

    class DelayedStream:
        def tell(self):
            return stream.tell()

        def write(self, text):
            if json.loads(text).get("current_estimate") is not None and not entered.is_set():
                entered.set()
                assert release.wait(5.)
                if fail:
                    raise OSError("synthetic delayed evidence failure")
            return stream.write(text)

        def flush(self):
            return stream.flush()

        def close(self):
            return stream.close()

    observer._updates_stream = DelayedStream()
    observer.observe(item, row)
    try:
        assert entered.wait(5.)
        status = observer.status()["continuous_refresh"]
        assert status["latest_snapshot_estimate"]["pose_timestamp_ns"] == 2_000_000_000
        pending = status["latest_snapshot_evidence"]
        assert pending["state"] == "pending"
        # A caller cannot modify the private status sample or its covariance.
        status["latest_snapshot_estimate"]["map_T_imu"][0][3] += 100
        assert status["latest_snapshot_estimate"] != current(observer)
        source.push(later_record())
        wait_for(lambda: observer.coordinator.status()["posterior_timestamp_ns"] == 3_000_000_000)
        assert current(observer) is None
        assert observer.status()["continuous_refresh"]["latest_snapshot_evidence"] is None
        # Ensure the start-to-start rate limit has elapsed before releasing the
        # old write. New sensor work should then bypass the idle wait.
        time.sleep(.04)
    finally:
        release.set()
    if fail:
        wait_for(lambda: observer.status()["error"] is not None)
        assert "synthetic delayed evidence failure" in observer.status()["error"]
        assert current(observer) is None
    else:
        wait_for(lambda: (estimate := current(observer)) is not None and estimate["pose_timestamp_ns"] == 3_000_000_000)
        wait_for(lambda: observer.status()["continuous_refresh"]["latest_snapshot_evidence"]["state"] == "written")
        assert observer.status()["counts"]["native_refresh_wakeups"] >= 1
        assert observer.finish()
    assert observer.stop()
    records = rows(observer)
    assert [r["event_sequence"] for r in records] == list(range(1, len(records)+1))
    assert any(r["event_sequence"] == pending["event_sequence"] for r in records) is not fail


def test_continuous_mode_requires_owned_process_and_new_trace(tmp_path):
    request, _ = fixture()
    source = Source(request["config"])
    with pytest.raises(ValueError, match="owned estimator"):
        LiveAlignmentObserver(source, request["config"], continuous_refresh_interval_s=.1)
    engine = ProcessAlignmentEngine(Path(__file__).parent / "fixtures/alignment_noise.py")
    observer = LiveAlignmentObserver(source, request["config"], process_engine=engine, continuous_refresh_interval_s=.1)
    _, binding, _ = envelope(request["fixes"][0]["request"], worker_run_id=source.native.worker_run_id)
    with pytest.raises(ValueError, match="evidence file"):
        observer.start(binding["provenance"])
    path = tmp_path / "existing.jsonl"
    path.write_text("preserve")
    with pytest.raises(FileExistsError):
        observer.start(binding["provenance"], evidence_path=path)
    assert path.read_text() == "preserve"
    assert observer.stop()
