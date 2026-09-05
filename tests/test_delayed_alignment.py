"""Delayed learned/PnP results use exact VIO state and joint error ownership."""

import copy
import threading
import uuid

import numpy as np
import pytest

from vision_nav.estimation.correlated_window import payload_digest
from vision_nav.estimation.delayed_alignment import DelayedPnPAlignment
from vision_nav.estimation.pnp_window_alignment import align
from test_pnp_window_alignment import fixture
from test_vio_pose_window import record


class Clock:
    value = 1_000_000_000_000

    def __call__(self):
        return self.value


def setup(capacity=8, **kwargs):
    request, expected = fixture()
    clock = Clock()
    run_id = str(uuid.uuid4())
    coordinator = DelayedPnPAlignment(request["config"], run_id, capacity=capacity, monotonic_ns=clock, **kwargs)
    push(coordinator, clock, request["native_record"])
    return coordinator, clock, request, expected


def push(coordinator, clock, native):
    return coordinator.update_snapshot({"record": native, "worker_run_id": coordinator.worker_run_id,
                                        "received_host_monotonic_ns": clock(), "position_authority": False})


def dispatch(coordinator, request):
    tickets = []
    for i, fix in enumerate(request["fixes"]):
        fix["request"]["context"]["frame_sha256"] = f"{i+1:064x}"
        response = coordinator.begin_query(fix["request"]["context"]["timestamp_ns"], f"{i+1:064x}")
        assert response["status"] == "dispatched"
        tickets.append(response["ticket"])
    return tickets


def complete(coordinator, tickets, request):
    for ticket, fix in zip(tickets, request["fixes"]):
        assert coordinator.complete_query(ticket, fix["request"])["status"] == "ready"


def noise_for(draft, original):
    noise = copy.deepcopy(original["joint_uncertainty"])
    noise["snapshot_sha256"] = payload_digest(draft["request"]["native_record"])
    noise["pnp_request_sha256s"] = [payload_digest(f["request"]) for f in draft["request"]["fixes"]]
    n = len(draft["request"]["native_record"]["pose_window"]["pose_timestamps_ns"])
    if n != 2:
        prior = np.asarray(noise["vio_input_cross_covariance"])
        cross = np.zeros((6*n, prior.shape[1]))
        cross[:len(prior)] = prior
        noise["vio_input_cross_covariance"] = cross.tolist()
    return noise


def later_record():
    poses = [np.eye(4) for _ in range(3)]
    for i, pose in enumerate(poses):
        pose[0, 3] = float(i)
    return record(poses)


def test_accepted_evidence_uses_original_sensor_horizon_not_inference_deadline():
    coordinator, clock, request, _ = setup(max_accepted_age_ns=1_500_000_000)
    tickets = dispatch(coordinator, request)
    complete(coordinator, tickets, request)
    draft = coordinator.prepare()
    assert coordinator.solve(draft["draft_id"], noise_for(draft, request))["status"] == "research_alignment"
    accepted = coordinator.status()["queries"]
    clock.value += 20_000_000_000
    assert coordinator.status()["accepted_query_ids"] == sorted(t["query_id"] for t in tickets)
    assert coordinator.status()["queries"] == accepted  # No timestamp or covariance reset.
    update = push(coordinator, clock, later_record())
    assert update["retired_queries"] == [{"query_id": tickets[0]["query_id"], "reason": "accepted_evidence_horizon_exceeded"}]
    assert coordinator.status()["accepted_query_ids"] == [tickets[1]["query_id"]]


def test_unaccepted_ready_evidence_still_expires_on_dispatch_deadline():
    coordinator, clock, request, _ = setup(max_accepted_age_ns=30_000_000_000)
    tickets = dispatch(coordinator, request)
    complete(coordinator, tickets, request)
    clock.value += 11_000_000_000
    assert coordinator.status()["outstanding_queries"] == 0


def test_first_acceptance_cannot_revive_evidence_beyond_sensor_horizon():
    coordinator, clock, request, _ = setup(max_accepted_age_ns=1_500_000_000)
    tickets = dispatch(coordinator, request)
    complete(coordinator, tickets, request)
    draft = coordinator.prepare()
    def advance_during_solve(req):
        push(coordinator, clock, later_record())
        return {"status": "research_alignment", "reason": "synthetic"}
    coordinator._solver = advance_during_solve
    result = coordinator.solve(draft["draft_id"], {})
    assert result["reason"] == "accepted_evidence_horizon_exceeded"
    assert tickets[0]["query_id"] not in {q["query_id"] for q in coordinator.status()["queries"]}
    assert not coordinator.status()["accepted_query_ids"]


def test_pre_inference_dispatch_time_cannot_be_reset_by_binding():
    coordinator, clock, request, _ = setup()
    sent = clock.value-9_000_000_000
    event = coordinator.begin_query(10**9, "a"*64, dispatched_host_monotonic_ns=sent)
    assert event["ticket"]["dispatched_host_monotonic_ns"] == sent
    clock.value += 2_000_000_000
    assert coordinator.status()["outstanding_queries"] == 0
    assert coordinator.begin_query(2*10**9, "b"*64,
        dispatched_host_monotonic_ns=sent)["reason"] == "query_deadline_exceeded_before_binding"
    with pytest.raises(ValueError, match="future"):
        coordinator.begin_query(2*10**9, "b"*64, dispatched_host_monotonic_ns=clock.value+1)


def test_owner_retirement_invalidates_prepared_and_running_solve():
    coordinator, _, request, _ = setup()
    tickets = dispatch(coordinator, request)
    complete(coordinator, tickets, request)
    draft = coordinator.prepare()
    assert coordinator.retire_query(tickets[0]["query_id"], "superseded")["status"] == "retired"
    assert coordinator.solve(draft["draft_id"], {})["reason"] == "unknown_or_retired_draft"
    draft = coordinator.prepare()
    def solve_then_retire(req):
        coordinator.retire_query(tickets[1]["query_id"], "owner_stopped")
        return {"status": "research_alignment", "reason": "synthetic"}
    coordinator._solver = solve_then_retire
    assert coordinator.solve(draft["draft_id"], {})["reason"] == "queries_retired_during_alignment"


@pytest.mark.parametrize("horizon", [0, -1, True, 1.5, 300_000_000_001])
def test_accepted_horizon_is_explicit_and_bounded(horizon):
    with pytest.raises(ValueError, match="horizon"):
        setup(max_accepted_age_ns=horizon)


def test_out_of_order_arrivals_align_exact_older_frames_to_newer_joint_state():
    coordinator, clock, request, expected = setup()
    tickets = dispatch(coordinator, request)
    clock.value += 100_000_000
    assert push(coordinator, clock, later_record())["status"] == "updated"
    # Delayed model completion is independent of sensor order.
    complete(coordinator, list(reversed(tickets)), {"fixes": list(reversed(request["fixes"]))})
    draft = coordinator.prepare()
    assert [f["request"]["context"]["timestamp_ns"] for f in draft["request"]["fixes"]] == [10**9, 2*10**9]
    noise = noise_for(draft, request)
    result = coordinator.solve(draft["draft_id"], noise)
    direct = align({**draft["request"], "joint_uncertainty": noise})
    assert result["result"] == direct
    assert result["status"] == "research_alignment"
    assert result["result"]["alignment"]["pose_timestamp_ns"] == 3*10**9
    assert result["snapshot_is_latest"]
    assert not result["sensor_clock_is_host_clock"]
    assert not result["position_authority"] and result["px4_external_vision"] == "disabled"
    assert result["estimate_scope"] == "historical_snapshot_only"
    assert coordinator.solve(draft["draft_id"], noise)["reason"] == "unknown_or_retired_draft"


def test_expired_clone_is_not_restored_by_cached_query_or_old_snapshot():
    coordinator, clock, request, _ = setup()
    tickets = dispatch(coordinator, request)
    newer = record()
    newer["timestamp_ns"] = newer["pose_window"]["posterior_timestamp_ns"] = 4*10**9
    newer["pose_window"]["pose_timestamps_ns"] = [3*10**9, 4*10**9]
    update = push(coordinator, clock, newer)
    assert len(update["retired_queries"]) == 2
    assert all(r["reason"] == "camera_state_expired" for r in update["retired_queries"])
    assert coordinator.complete_query(tickets[0], request["fixes"][0]["request"])["reason"] == "unknown_or_retired_query"
    assert coordinator.prepare()["reason"] == "no_completed_retained_queries"
    assert push(coordinator, clock, request["native_record"])["reason"] == "invalid_vio_snapshot"
    assert coordinator.status()["fault"] == "invalid_vio_snapshot"


def test_full_queue_does_not_consume_query_order_watermark():
    coordinator, clock, request, _ = setup(capacity=1)
    assert coordinator.begin_query(10**9, "a"*64)["status"] == "dispatched"
    assert coordinator.begin_query(2*10**9, "b"*64)["reason"] == "query_capacity_reached"
    newer = record()
    newer["timestamp_ns"] = newer["pose_window"]["posterior_timestamp_ns"] = 3*10**9
    newer["pose_window"]["pose_timestamps_ns"] = [2*10**9, 3*10**9]
    push(coordinator, clock, newer)
    assert coordinator.begin_query(2*10**9, "b"*64)["status"] == "dispatched"
    assert coordinator.begin_query(2*10**9, "b"*64)["reason"] == "duplicate_or_reordered_query"
    assert coordinator.begin_query(2*10**9+1, "c"*64)["reason"] == "camera_state_not_retained"


@pytest.mark.parametrize("field", ["timestamp_ns", "frame_sha256", "map_release_id", "model_release_id",
                                  "calibration_id", "clock_id", "uncertainty_model_id"])
def test_wrong_result_identity_or_provenance_retires_query(field):
    coordinator, _, request, _ = setup()
    tickets = dispatch(coordinator, request)
    pnp = request["fixes"][0]["request"]
    pnp["context"][field] = 123 if field == "timestamp_ns" else "other"
    assert coordinator.complete_query(tickets[0], pnp)["reason"] == "invalid_pnp_binding"
    assert coordinator.status()["outstanding_queries"] == 1


def test_owned_ticket_snapshot_request_and_duplicate_result():
    coordinator, clock, request, _ = setup()
    tickets = dispatch(coordinator, request)
    tampered = {**tickets[0], "frame_sha256": "a"*64}
    assert coordinator.complete_query(tampered, request["fixes"][0]["request"])["reason"] == "query_ticket_mismatch"
    complete(coordinator, tickets, request)
    assert coordinator.complete_query(tickets[0], request["fixes"][0]["request"])["reason"] == "duplicate_query_result"
    draft = coordinator.prepare()
    noise = noise_for(draft, request)
    draft["request"]["native_record"]["pose_window"]["local_T_imu"][0][0][3] = 5000.
    request["fixes"][0]["request"]["attempt"]["matched_query_pixels"][0][0] += 100.
    assert coordinator.solve(draft["draft_id"], noise)["status"] == "research_alignment"


def test_latest_snapshot_noise_cannot_be_replaced_by_old_or_missing_cross_terms():
    coordinator, clock, request, _ = setup()
    tickets = dispatch(coordinator, request)
    complete(coordinator, tickets, request)
    draft = coordinator.prepare()
    noise = noise_for(draft, request)
    noise.pop("vio_input_cross_covariance")
    assert coordinator.solve(draft["draft_id"], noise)["reason"] == "invalid_joint_alignment"
    push(coordinator, clock, later_record())
    later = coordinator.prepare()
    old_noise = noise_for(draft, request)
    assert coordinator.solve(later["draft_id"], old_noise)["reason"] == "invalid_joint_alignment"


def test_query_timeout_applies_when_no_new_sensor_snapshot_arrives():
    coordinator, clock, request, _ = setup()
    tickets = dispatch(coordinator, request)
    clock.value += coordinator.max_age + 1
    result = coordinator.complete_query(tickets[0], request["fixes"][0]["request"])
    assert result["reason"] == "unknown_or_retired_query"
    assert all(r["reason"] == "query_deadline_exceeded" for r in result["retired_queries"])
    assert coordinator.status()["outstanding_queries"] == 0


@pytest.mark.parametrize("failure", ["epoch", "receipt_future", "no_update", "bad_covariance", "duplicate"])
def test_invalid_sensor_input_fails_epoch_closed(failure):
    coordinator, clock, request, _ = setup()
    tickets = dispatch(coordinator, request)
    snapshot = {"record": later_record(), "worker_run_id": coordinator.worker_run_id,
                "received_host_monotonic_ns": clock(), "position_authority": False}
    if failure == "epoch":
        snapshot["worker_run_id"] = str(uuid.uuid4())
    elif failure == "receipt_future":
        snapshot["received_host_monotonic_ns"] += 1
    elif failure == "no_update":
        snapshot["record"]["local_state"] = "INITIALIZING_OR_NO_UPDATE"
    elif failure == "bad_covariance":
        snapshot["record"]["pose_window"]["joint_covariance_native"][0][0] = -1.
    else:
        snapshot["record"] = request["native_record"]
    assert coordinator.update_snapshot(snapshot)["reason"] == "invalid_vio_snapshot"
    assert coordinator.status()["outstanding_queries"] == 0
    assert coordinator.complete_query(tickets[0], request["fixes"][0]["request"])["status"] == "withheld"
    assert push(coordinator, clock, later_record())["reason"] == "epoch_invalidated"


@pytest.mark.parametrize("during", ["new_snapshot", "invalidate", "timeout"])
def test_alignment_does_not_block_vio_and_never_restamps_old_pose(monkeypatch, during):
    import vision_nav.estimation.delayed_alignment as module

    coordinator, clock, request, _ = setup()
    tickets = dispatch(coordinator, request)
    complete(coordinator, tickets, request)
    draft = coordinator.prepare()
    noise = noise_for(draft, request)
    entered, release = threading.Event(), threading.Event()
    original = module.align

    def slow(req):
        entered.set()
        assert release.wait(5.)
        return original(req)

    monkeypatch.setattr(module, "align", slow)
    output = []
    thread = threading.Thread(target=lambda: output.append(coordinator.solve(draft["draft_id"], noise)))
    thread.start()
    try:
        assert entered.wait(5.)
        assert coordinator.status()["alignment_running"]
        assert coordinator.prepare()["reason"] == "alignment_already_running"
        if during == "new_snapshot":
            assert push(coordinator, clock, later_record())["status"] == "updated"
        elif during == "invalidate":
            coordinator.invalidate("native_worker_failed")
        else:
            clock.value += coordinator.max_age+1
    finally:
        release.set()
        thread.join(10.)
    assert not thread.is_alive()
    result = output[0]
    assert result["result"]["alignment"]["pose_timestamp_ns"] == 2*10**9
    if during == "new_snapshot":
        assert result["status"] == "research_alignment" and not result["snapshot_is_latest"]
        assert result["latest_vio_timestamp_ns"] == 3*10**9
    else:
        assert result["status"] == "withheld"
        assert result["reason"] == ("epoch_invalidated_during_alignment" if during == "invalidate"
                                    else "query_deadline_exceeded_during_alignment")
    assert not result["position_authority"]


def test_host_clock_regression_invalidates_epoch():
    coordinator, clock, _, _ = setup()
    clock.value -= 1
    with pytest.raises(ValueError, match="regressed"):
        coordinator.status()
    clock.value += 2
    assert coordinator.status()["fault"] == "host_clock_regressed"


@pytest.mark.parametrize("kind", ["snapshot", "context", "pixels", "noise", "oversized", "uncertainty", "geometry"])
def test_malformed_inputs_fail_closed_without_retaining_large_objects(kind):
    coordinator, clock, request, _ = setup()
    if kind == "snapshot":
        assert coordinator.update_snapshot([])["reason"] == "invalid_vio_snapshot"
        return
    tickets = dispatch(coordinator, request)
    if kind in {"context", "pixels", "oversized", "uncertainty", "geometry"}:
        pnp = request["fixes"][0]["request"]
        if kind == "context":
            pnp["context"] = []
        elif kind == "pixels":
            pnp["attempt"]["matched_query_pixels"] = None
        elif kind == "uncertainty":
            pnp["uncertainty"] = []
        elif kind == "geometry":
            pnp["attempt"]["geometry"] = []
        else:
            pnp["extra"] = "a"*(2*1024*1024)
        assert coordinator.complete_query(tickets[0], pnp)["reason"] == "invalid_pnp_binding"
        assert coordinator.status()["outstanding_queries"] == 1
    else:
        complete(coordinator, tickets, request)
        draft = coordinator.prepare()
        assert coordinator.solve(draft["draft_id"], [])["reason"] == "invalid_joint_alignment"
        assert not coordinator.status()["alignment_running"]


def test_replaced_draft_cannot_be_reused_and_snapshots_never_accumulate():
    coordinator, clock, request, _ = setup()
    tickets = dispatch(coordinator, request)
    complete(coordinator, tickets, request)
    first = coordinator.prepare()
    second = coordinator.prepare()
    assert coordinator.solve(first["draft_id"], noise_for(first, request))["reason"] == "unknown_or_retired_draft"
    a = coordinator.solve(second["draft_id"], noise_for(second, request))
    third = coordinator.prepare()
    b = coordinator.solve(third["draft_id"], noise_for(third, request))
    assert a["result"] == b["result"]
    assert len(b["result"]["map_batch"]["fixes"]) == 2
