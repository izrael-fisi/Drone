"""Combined uncertainty/solver transaction keeps exact request and epoch gates."""

from copy import deepcopy
from pathlib import Path
import threading

import pytest

from vision_nav.estimation.alignment_worker import ProcessAlignmentEngine
from vision_nav.estimation.correlated_window import payload_digest
from vision_nav.estimation.pnp_window_alignment import align
from test_current_alignment_refresh import ready, provider
from test_delayed_alignment import push, later_record
from test_pnp_window_alignment import fixture


PROVIDER = Path(__file__).parent/"fixtures/alignment_noise.py"


def executor(original, observed=None, before=None):
    def execute(request):
        if observed is not None:
            observed.append(deepcopy(request))
        if before:
            before(request)
        joint = provider(original)(request)
        result = align({**request, "joint_uncertainty": joint})
        return {"input_request_sha256": payload_digest(request), "joint_uncertainty": joint, "result": result}
    return execute


def refresh(coordinator, execute, **kwargs):
    return coordinator.refresh_current(joint_executor=execute, max_snapshot_age_ns=500_000_000, **kwargs)


@pytest.mark.parametrize("wire_format", ["json_v1", "msgpack_v1"])
def test_combined_real_child_matches_separate_operations_with_one_round_trip(wire_format):
    request, _ = fixture(shared=False, vio_cross=False)
    request.pop("joint_uncertainty")
    before = deepcopy(request)
    engine = ProcessAlignmentEngine(PROVIDER, operation_timeout_s=5., wire_format=wire_format)
    try:
        engine.start()
        combined = engine.align_with_noise(request)
        assert engine.status()["completed_requests"] == 1
        assert engine.status()["wire_format"] == wire_format
        last = engine.status()["last_operation"]
        assert last["operation"] == "align_with_noise" and last["request_bytes"] > 0 and last["elapsed_ms"] > 0
        profile = last["transport_profile"]
        assert profile["response_bytes"] > 0
        stages = [profile[key] for key in (
            "parent_verify_encode_ms", "parent_send_ms", "parent_receive_response_ms", "parent_final_verify_ms")]
        assert all(0 <= value < float("inf") for value in stages)
        assert sum(stages) == pytest.approx(last["elapsed_ms"])
        assert 0 <= profile["receive_wait_ms"] + profile["response_decode_ms"] <= profile["parent_receive_response_ms"]
        assert all(0 <= value < float("inf") for value in profile["child_timing_ms"].values())
        assert profile["scope"] == "host_elapsed_stages_overlap_child_work_not_sensor_latency"
        assert combined["input_request_sha256"] == payload_digest(request)
        noise = engine.joint_noise(request)
        full = {**request, "joint_uncertainty": noise}
        assert noise == combined["joint_uncertainty"]
        assert engine.align(full) == combined["result"] == align(full)
        assert all(v >= 0 for v in combined["timing_ms"].values())
        assert request == before
    finally:
        assert engine.stop()
    assert not engine.status()["process_alive"]


def test_combined_coordinator_preserves_numerical_replay_and_original_times():
    coordinator, _, request, _ = ready()
    seen = []
    result = refresh(coordinator, executor(request, seen))
    reference = align({**seen[0], "joint_uncertainty": provider(request)(seen[0])})
    assert result["status"] == "research_current_alignment"
    assert result["current_estimate"] == reference["alignment"]
    assert result["last_alignment"]["request_sha256"] == reference["request_sha256"]
    assert not result["position_authority"] and result["px4_external_vision"] == "disabled"


def test_new_posterior_during_transaction_requires_new_noise_and_real_solve():
    coordinator, clock, request, _ = ready()
    seen = []
    def before(_):
        if len(seen) == 1:
            push(coordinator, clock, later_record())
    result = refresh(coordinator, executor(request, seen, before))
    assert result["status"] == "research_current_alignment"
    assert [r["native_record"]["timestamp_ns"] for r in seen] == [2*10**9, 3*10**9]
    assert len(result["refresh_attempts"]) == 2
    assert result["current_estimate"]["pose_timestamp_ns"] == 3*10**9


def test_continuously_advancing_snapshot_still_withholds_on_retry_exhaustion():
    coordinator, clock, request, _ = ready()
    result = refresh(coordinator, executor(request, before=lambda _: push(coordinator, clock, later_record())), max_attempts=1)
    assert result["reason"] == "current_refresh_attempt_budget_exhausted"
    assert result["current_estimate"] is None


@pytest.mark.parametrize("fault", ["input_hash", "result_hash", "noise", "missing_noise", "policy", "not_object"])
def test_changed_transaction_outputs_never_become_current(fault):
    coordinator, _, request, _ = ready()
    original = executor(request)
    def corrupt(req):
        out = original(req)
        if fault == "input_hash":
            out["input_request_sha256"] = "0"*64
        elif fault == "result_hash":
            out["result"]["request_sha256"] = "0"*64
        elif fault == "noise":
            out["joint_uncertainty"]["joint_measurement_covariance"][0][0] += 1
        elif fault == "missing_noise":
            out.pop("joint_uncertainty")
        elif fault == "policy":
            out["result"]["position_authority"] = True
        else:
            return []
        return out
    result = refresh(coordinator, corrupt)
    assert result["reason"] == "invalid_joint_alignment"
    assert result["current_estimate"] is None and not coordinator.status()["current_refresh_running"]


@pytest.mark.parametrize("failure", ["expired", "stale", "invalidated", "retired"])
def test_original_lifetime_and_health_checks_cover_complete_transaction(failure):
    coordinator, clock, request, _ = ready()
    def before(_):
        if failure == "expired":
            clock.value += coordinator.max_age+1
        elif failure == "stale":
            clock.value += 500_000_001
        elif failure == "invalidated":
            coordinator.invalidate("sensor_lost")
        else:
            coordinator.retire_query(next(iter(coordinator._jobs)), "operator_retired")
    result = refresh(coordinator, executor(request, before=before))
    assert result["status"] == "withheld" and result["current_estimate"] is None


def test_ingestion_is_not_locked_during_combined_provider_and_solve():
    coordinator, clock, request, _ = ready()
    entered, release = threading.Event(), threading.Event()
    results = []
    def wait(_):
        entered.set()
        assert release.wait(5)
    worker = threading.Thread(target=lambda: results.append(refresh(coordinator, executor(request, before=wait), max_attempts=1)))
    worker.start()
    try:
        assert entered.wait(5)
        assert push(coordinator, clock, later_record())["status"] == "updated"
    finally:
        release.set()
        worker.join(5)
    assert not worker.is_alive()
    assert results[0]["reason"] == "current_refresh_attempt_budget_exhausted"


@pytest.mark.parametrize("fault", ["delay_s", "raise", "exit"])
def test_provider_failure_in_combined_transaction_is_terminal_and_reaped(fault):
    request, _ = fixture()
    request.pop("joint_uncertainty")
    engine = ProcessAlignmentEngine(PROVIDER,
        provider_config={"fault_stage": "noise", fault: 60 if fault == "delay_s" else True}, operation_timeout_s=.2)
    try:
        engine.start()
        with pytest.raises((RuntimeError, EOFError, TimeoutError)):
            engine.align_with_noise(request)
        assert engine.status()["state"] == "failed" and not engine.status()["process_alive"]
    finally:
        assert engine.stop()


def test_combined_worker_rejects_already_supplied_noise():
    request, _ = fixture()
    engine = ProcessAlignmentEngine(PROVIDER)
    try:
        engine.start()
        with pytest.raises(RuntimeError, match="unbound noise"):
            engine.align_with_noise(request)
        assert not engine.status()["process_alive"]
    finally:
        assert engine.stop()


def test_combined_provider_cannot_mutate_owned_request_or_rebind_cached_identity():
    request, _ = fixture()
    request.pop("joint_uncertainty")
    before = deepcopy(request)
    engine = ProcessAlignmentEngine(PROVIDER, provider_config={"mutate_snapshot": True})
    try:
        engine.start()
        with pytest.raises(RuntimeError, match="Joint noise must bind"):
            engine.align_with_noise(request)
        assert request == before
        assert engine.status()["state"] == "failed" and not engine.status()["process_alive"]
    finally:
        assert engine.stop()


def test_cannot_supply_both_callbacks_or_non_callable_executor():
    coordinator, _, request, _ = ready()
    with pytest.raises(ValueError):
        coordinator.refresh_current(provider(request), joint_executor=executor(request), max_snapshot_age_ns=1)
    with pytest.raises(ValueError):
        coordinator.refresh_current(joint_executor=True, max_snapshot_age_ns=1)
    draft = coordinator.prepare()
    with pytest.raises(ValueError):
        coordinator.solve(draft["draft_id"], {}, joint_executor=executor(request))
