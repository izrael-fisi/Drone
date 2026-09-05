"""A slow snapshot consumer must not hold the native acquisition lock."""

from collections import deque
from copy import deepcopy
import threading

import pytest

from vision_nav.vio.stream_worker import NativeVioStreamWorker
from vision_nav.vio.anchor_control import AnchorInputClosed


def worker():
    w = NativeVioStreamWorker.__new__(NativeVioStreamWorker)
    w._condition = threading.Condition()
    w._completed, w._result_taken, w._snapshot_generation = 1, 0, 0
    w._latest, w._latest_received_ns = {"timestamp_ns": 10, "data": [[1., 2.]]}, 100
    w.worker_run_id, w._state = "test-epoch", "processing"
    w._batch_timings, w._active_batch_timing = deque(maxlen=256), None
    return w


@pytest.mark.parametrize("transition", ["advance", "control", "failure"])
def test_copy_releases_native_lock_and_preserves_original_receipt(monkeypatch, transition):
    import vision_nav.vio.stream_worker as module
    w = worker()
    copying, release, changed = threading.Event(), threading.Event(), threading.Event()
    results, errors = [], []
    def slow(value):
        copying.set()
        if not release.wait(2):
            raise RuntimeError("test copy was not released")
        return deepcopy(value)
    monkeypatch.setattr(module, "deepcopy", slow)
    def consume():
        try:
            results.append(w.take_latest())
        except Exception as exc:
            errors.append(exc)
    def publish():
        with w._condition:
            w._latest, w._latest_received_ns, w._completed = {"timestamp_ns": 20, "data": [[3.]]}, 200, 2
            if transition == "control":
                w._snapshot_generation += 1
            elif transition == "failure":
                w._state = "failed"
        changed.set()
    reader = threading.Thread(target=consume)
    producer = threading.Thread(target=publish)
    reader.start()
    try:
        assert copying.wait(1)
        producer.start()
        assert changed.wait(1), "Snapshot copying blocked native publication"
    finally:
        release.set()
        reader.join(2)
        if producer.ident is not None:
            producer.join(2)
    assert not errors and not reader.is_alive() and not producer.is_alive()
    if transition != "advance":
        assert results == [None]
        return
    first = results[0]
    assert first["record"]["timestamp_ns"] == 10 and first["received_host_monotonic_ns"] == 100
    assert first["completed_batch_count"] == 1
    first["record"]["data"][0][0] = 999
    assert w._latest["data"] == [[3.]]
    second = w.take_latest()
    assert second["record"]["timestamp_ns"] == 20 and second["received_host_monotonic_ns"] == 200
    assert w.take_latest() is None


def test_timing_ring_is_bounded_and_does_not_expose_mutable_records():
    w = worker()
    for i in range(300):
        w._batch_timings.append({"sensor_timestamp_ns": i, "queue_wait_ms": 0.})
    w._completed = 300
    w._active_batch_timing = (301, 100, 110, "native_wait_and_decode", 120)
    snapshot = w.timing_snapshot()
    assert len(snapshot["samples"]) == 256 and snapshot["omitted_older_sample_count"] == 44
    assert snapshot["active_stage"]["phase"] == "native_wait_and_decode"
    snapshot["samples"][0]["sensor_timestamp_ns"] = -1
    assert w._batch_timings[0]["sensor_timestamp_ns"] == 44


@pytest.mark.parametrize("state,stopped,orderly", [
    ("waiting_for_sensors", False, True), ("complete", True, True),
    ("no_local_estimate", True, True), ("failed", False, False),
    ("stopped", True, False), ("shutdown_incomplete", False, False),
    ("waiting_for_sensors", True, False),
])
def test_closed_anchor_input_is_atomically_unsent_not_a_hidden_fault(state, stopped, orderly):
    w = worker()
    w.anchor_capacity, w._thread, w._closed = 4, object(), True
    w._state, w._stop = state, threading.Event()
    w._control_submitted, w._pending_controls, w._queue = 0, 0, deque()
    if stopped:
        w._stop.set()
    with pytest.raises(RuntimeError) as error:
        w.request_anchor("retain", 10, worker_run_id=w.worker_run_id)
    assert isinstance(error.value, AnchorInputClosed) is orderly
    assert w._control_submitted == w._pending_controls == 0 and not w._queue
