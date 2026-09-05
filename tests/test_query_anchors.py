"""Query ownership contracts; fake native receipts are not flight evidence."""

from copy import deepcopy
from dataclasses import replace
import json
import threading
import time
from types import SimpleNamespace
import uuid

import numpy as np
import pytest

from vision_nav.estimation.delayed_alignment import DelayedPnPAlignment
from vision_nav.estimation.query_anchors import QueryAnchorOwner
from vision_nav.vio.anchor_control import AnchorCommand, AnchorInputClosed
from test_delayed_alignment import push
from test_learned_pnp_binding import envelope
from test_pnp_window_alignment import fixture
from test_vio_pose_window import record


class Native:
    """Deterministic command transport double; coordinator remains real."""
    anchor_capacity, timeout = 4, .02

    def __init__(self, coordinator):
        self.coordinator, self.worker_run_id = coordinator, coordinator.worker_run_id
        self.active, self.commands = set(), []
        self.ended, self.closed, self.backpressure = False, False, False
        self.next_status, self.pending = None, None
        self.publish()

    def publish(self):
        poses = [np.eye(4) for _ in range(6)]
        self.row = record(poses)
        self.row["timestamp_ns"] += len(self.commands)*1_000_000
        w = self.row["pose_window"]
        w["pose_timestamps_ns"][-1] = w["posterior_timestamp_ns"] = self.row["timestamp_ns"]
        w.update(schema_version="vision_nav_openvins_pose_window_v2", passive_anchor_capacity=4,
            retention_policy="native_passive_stochastic_clones_explicit_release",
            passive_anchor_timestamps_ns=sorted(self.active), normal_clone_timestamps_ns=w["pose_timestamps_ns"][:])
        assert push(self.coordinator, time.monotonic_ns, self.row)["status"] == "updated"

    def status(self):
        return {"input_closed": self.closed, "native_returncode": 0 if self.ended else None,
            "thread_alive": not self.ended, "anchor_control": {"submitted": len(self.commands)},
            "status": "complete" if self.ended else "running"}

    def join(self, timeout_s):
        return self.ended

    def command(self, frame, operation):
        if self.backpressure:
            return None
        req = {"worker_run_id": self.worker_run_id, "request_id": len(self.commands)+1,
            "operation": operation, "anchor_timestamp_ns": frame.timestamp_ns}
        command = AnchorCommand(req, time.monotonic()+self.timeout)
        self.commands.append(command)
        status, self.next_status = self.next_status or "applied", None
        if status == "pending":
            self.pending = command
            return command
        if status == "applied":
            if operation == "retain": self.active.add(frame.timestamp_ns)
            else: self.active.remove(frame.timestamp_ns)
        command._settle({**req, "status": status, "reason": "synthetic_receipt", "position_authority": False})
        self.publish()
        return command

    def retain_frame(self, frame): return self.command(frame, "retain")
    def release_frame(self, frame): return self.command(frame, "release")


@pytest.fixture
def owned(tmp_path):
    request, _ = fixture()
    coordinator = DelayedPnPAlignment(request["config"], str(uuid.uuid4()), max_accepted_age_ns=30_000_000_000)
    native = Native(coordinator)
    owner = QueryAnchorOwner(native, coordinator, snapshot_wait_s=.01)
    owner.start(tmp_path/"ownership.jsonl")
    yield owner, native, coordinator, request
    native.ended = True
    assert owner.close()


def item(owner, ns=10**9):
    _, kwargs, _ = envelope(worker_run_id=owner.native.worker_run_id)
    ticket = replace(kwargs["frame_ticket"], timestamp_ns=ns)
    return {"worker_run_id": ticket.worker_run_id, "tickets": [ticket]}


def accept_ready(coordinator, request, ticket):
    req = deepcopy(request["fixes"][0]["request"])
    req["context"].update(timestamp_ns=ticket["timestamp_ns"], frame_sha256=ticket["frame_sha256"])
    assert coordinator.complete_query(ticket, req)["status"] == "ready"
    coordinator._solver = lambda _: {"status": "research_alignment", "reason": "synthetic_lifecycle_test_only"}
    draft = coordinator.prepare()
    assert coordinator.solve(draft["draft_id"], {})["status"] == "research_alignment"


def test_exact_anchor_precedes_dispatch_and_release_retires_query(owned):
    owner, native, coordinator, _ = owned
    event = owner.acquire(item(owner))
    assert event["status"] == "dispatched" and native.active == {10**9}
    assert coordinator.status()["queries"][0]["state"] == "pending"
    assert owner.query_for({"timestamp_ns": 10**9, "frame_sha256": event["ticket"]["frame_sha256"]}) == event["ticket"]
    assert owner.query_for({"timestamp_ns": 10**9, "frame_sha256": "f"*64}) is None
    assert owner.acquire(item(owner))["reason"] == "duplicate_or_reordered_query_frame"
    owner.reject(10**9, "wrong_map")
    assert not native.active and coordinator.status()["outstanding_queries"] == 0
    assert owner.close()
    events = [json.loads(line) for line in owner._path.read_text().splitlines()]
    assert [e["status"] for e in events] == ["started", "submitted", "acknowledged", "dispatched", "withheld", "submitted", "acknowledged", "released", "closed"]
    assert all(not e["position_authority"] for e in events)


def test_capacity_never_evicts_inflight_and_accepted_trim_is_explicit(owned):
    owner, native, coordinator, request = owned
    tickets = [owner.acquire(item(owner, i*10**9))["ticket"] for i in range(1, 5)]
    assert owner.acquire(item(owner, 5*10**9))["reason"] == "query_anchor_capacity_reached"
    assert native.active == {i*10**9 for i in range(1, 5)}
    for ticket in tickets[:3]:
        accept_ready(coordinator, request, ticket)
    owner.reconcile()
    assert native.active == {2*10**9, 3*10**9, 4*10**9}
    assert tickets[0]["query_id"] not in coordinator.status()["accepted_query_ids"]
    assert owner.status()["counts"]["accepted_anchor_capacity_retirement"] == 1


def test_expired_or_rejected_query_releases_native_anchor(owned):
    owner, native, coordinator, _ = owned
    ticket = owner.acquire(item(owner))["ticket"]
    coordinator.retire_query(ticket["query_id"], "deadline")
    owner.reconcile()
    assert not native.active and not owner.status()["owned_timestamps_ns"]


def test_backpressure_never_invents_retention_or_loses_release_ownership(owned):
    owner, native, coordinator, _ = owned
    native.backpressure = True
    assert owner.acquire(item(owner))["reason"] == "query_anchor_not_acquired"
    assert not owner.status()["owned_timestamps_ns"]
    native.backpressure = False
    owner.acquire(item(owner, 2*10**9))
    native.backpressure = True
    owner.reject(2*10**9, "no_match")
    assert native.active == {2*10**9} and coordinator.status()["outstanding_queries"] == 0
    native.backpressure = False
    owner.reconcile()
    assert not native.active


def test_wait_timeout_keeps_same_receipt_until_confirmed_epoch_exit(owned):
    owner, native, _, _ = owned
    native.next_status = "pending"
    with pytest.raises(TimeoutError):
        owner.acquire(item(owner))
    assert len(native.commands) == 1
    assert not owner.close()
    assert len(native.commands) == 1  # No duplicate retain on timeout.
    native.ended = True
    assert owner.close()


def test_indeterminate_command_fails_closed_and_preserves_cleanup(owned):
    owner, native, _, _ = owned
    native.next_status = "indeterminate"
    with pytest.raises(RuntimeError, match="indeterminate"):
        owner.acquire(item(owner))
    assert not owner.close()
    native.ended = True
    assert owner.close()


def test_closed_input_requires_real_exit_not_a_wait_timeout(owned):
    owner, native, _, _ = owned
    owner.acquire(item(owner))
    native.closed = True
    assert not owner.close()
    assert len(native.commands) == 1
    native.ended = True
    assert owner.close()


def test_retain_close_race_withholds_without_creating_query(owned, monkeypatch):
    owner, native, coordinator, _ = owned
    def close_before_submit(frame):
        native.closed = True
        raise AnchorInputClosed("closed before submission")
    monkeypatch.setattr(native, "retain_frame", close_before_submit)
    result = owner.acquire(item(owner))
    assert result["reason"] == "native_input_not_accepting_queries"
    assert result["status"] == "withheld" and not native.commands
    assert not owner.status()["owned_timestamps_ns"]
    assert coordinator.status()["outstanding_queries"] == 0


@pytest.mark.parametrize("exited", [False, True])
def test_release_close_race_requires_confirmed_epoch_exit(owned, monkeypatch, exited):
    owner, native, coordinator, _ = owned
    owner.acquire(item(owner))
    def close_before_submit(frame):
        native.closed = True
        raise AnchorInputClosed("closed before submission")
    def join(timeout_s):
        native.ended = exited
        return exited
    monkeypatch.setattr(native, "release_frame", close_before_submit)
    monkeypatch.setattr(native, "join", join)
    if exited:
        owner.reject(10**9, "finished")
        assert not owner.status()["owned_timestamps_ns"]
    else:
        with pytest.raises(RuntimeError, match="without confirmed epoch exit"):
            owner.reject(10**9, "finished")
        assert owner.status()["owned_timestamps_ns"] == [10**9]
    assert len(native.commands) == 1
    assert coordinator.status()["outstanding_queries"] == 0


def test_retain_transport_failure_is_not_mistaken_for_orderly_closure(owned, monkeypatch):
    owner, native, _, _ = owned
    def fail(frame):
        native.closed = True
        raise RuntimeError("unexpected transport failure")
    monkeypatch.setattr(native, "retain_frame", fail)
    with pytest.raises(RuntimeError, match="unexpected transport failure"):
        owner.acquire(item(owner))


def test_status_does_not_block_on_pending_control(owned):
    owner, _, _, _ = owned
    entered, release = threading.Event(), threading.Event()
    def hold():
        with owner._lock:
            entered.set()
            assert release.wait(2.)
    worker = threading.Thread(target=hold)
    worker.start()
    assert entered.wait(2.)
    try:
        began = time.monotonic()
        assert owner.status()["busy"]
        assert time.monotonic()-began < .1
    finally:
        release.set()
        worker.join(2.)


def test_native_rejection_and_foreign_ticket_do_not_dispatch(owned):
    owner, native, coordinator, _ = owned
    bad = item(owner)
    bad["worker_run_id"] = str(uuid.uuid4())
    with pytest.raises(ValueError, match="epoch"):
        owner.acquire(bad)
    native.next_status = "rejected"
    assert owner.acquire(item(owner))["status"] == "withheld"
    assert coordinator.status()["outstanding_queries"] == 0


def test_owner_reserves_native_budget_for_release(owned):
    owner, native, _, _ = owned
    native.commands = [None]*1023
    assert owner.acquire(item(owner))["reason"] == "query_anchor_command_budget_reserved"
