"""Current-snapshot refresh uses original fixes, not restamped old solutions."""

import copy
import threading

import numpy as np
import pytest

from vision_nav.estimation.pnp_window_alignment import align
from test_delayed_alignment import setup, dispatch, complete, push, later_record, noise_for


def ready():
    coordinator, clock, request, expected = setup()
    complete(coordinator, dispatch(coordinator, request), request)
    return coordinator, clock, request, expected


def provider(original):
    return lambda request: noise_for({"request": request}, original)


def refresh(coordinator, noise, **kwargs):
    return coordinator.refresh_current(noise, max_snapshot_age_ns=500_000_000, **kwargs)


def test_original_delayed_fixes_correct_newest_unobserved_pose():
    coordinator, clock, request, _ = ready()
    baseline = refresh(coordinator, provider(request))
    newer = later_record()
    # Preserve genuine nonzero within-window correlation in the synthetic model.
    p = np.asarray(newer["pose_window"]["joint_covariance_native"])
    p += np.tile(np.eye(6)*.005, (3, 3))
    newer["pose_window"]["joint_covariance_native"] = p.tolist()
    push(coordinator, clock, newer)
    captured = []

    def noise(req):
        captured.append(copy.deepcopy(req))
        return provider(request)(req)

    result = refresh(coordinator, noise)
    assert result["status"] == "research_current_alignment"
    assert result["current_scope"] == "latest_snapshot_at_check_only"
    estimate = result["current_estimate"]
    assert estimate["pose_timestamp_ns"] == 3*10**9
    assert [f["request"]["context"]["timestamp_ns"] for f in captured[0]["fixes"]] == [10**9, 2*10**9]
    direct = align({**captured[0], "joint_uncertainty": provider(request)(captured[0])})
    assert estimate == direct["alignment"]
    assert not np.allclose(estimate["map_T_imu"], baseline["current_estimate"]["map_T_imu"])
    assert not estimate["position_authority"] and not result["position_authority"]
    assert result["navigation_mode"] == "NO_GLOBAL_FIX" and result["px4_external_vision"] == "disabled"
    assert not result["sensor_clock_is_host_clock"]
    assert refresh(coordinator, noise)["current_estimate"] == estimate


def test_new_snapshot_during_solve_forces_new_noise_and_real_resolve(monkeypatch):
    import vision_nav.estimation.delayed_alignment as module

    coordinator, clock, request, _ = ready()
    original = module.align
    snapshots = []

    def slow(req):
        snapshots.append(req["native_record"]["timestamp_ns"])
        if len(snapshots) == 1:
            push(coordinator, clock, later_record())
        return original(req)

    monkeypatch.setattr(module, "align", slow)
    result = refresh(coordinator, provider(request))
    assert result["status"] == "research_current_alignment"
    assert snapshots == [2*10**9, 3*10**9]
    assert len(result["refresh_attempts"]) == 2
    assert result["refresh_attempts"][0]["request_sha256"] != result["refresh_attempts"][1]["request_sha256"]
    assert result["current_estimate"]["pose_timestamp_ns"] == 3*10**9
    assert not coordinator.status()["current_refresh_running"]


def test_retry_budget_never_returns_old_solution_as_current(monkeypatch):
    import vision_nav.estimation.delayed_alignment as module

    coordinator, clock, request, _ = ready()
    original = module.align

    def advancing(req):
        push(coordinator, clock, later_record())
        return original(req)

    monkeypatch.setattr(module, "align", advancing)
    result = refresh(coordinator, provider(request), max_attempts=1)
    assert result["reason"] == "current_refresh_attempt_budget_exhausted"
    assert result["current_estimate"] is None
    assert result["refresh_attempts"][0]["status"] == "research_alignment"


def test_old_noise_cannot_be_rebound_to_new_snapshot_implicitly():
    coordinator, clock, request, _ = ready()
    saved = []

    def stale(req):
        if not saved:
            saved.append(provider(request)(req))
            push(coordinator, clock, later_record())
        return saved[0]

    result = refresh(coordinator, stale)
    assert result["reason"] == "invalid_joint_alignment"
    assert result["current_estimate"] is None
    assert len(result["refresh_attempts"]) == 2


def test_snapshot_digest_owned_once_and_new_snapshot_gets_new_identity(monkeypatch):
    import vision_nav.estimation.delayed_alignment as module

    coordinator, clock, request, _ = ready()
    original_digest, records = module.payload_digest, []
    def counted(value):
        if value.get("schema_version") == "vision_nav_openvins_local_v1":
            records.append(value["timestamp_ns"])
        return original_digest(value)
    monkeypatch.setattr(module, "payload_digest", counted)
    assert refresh(coordinator, provider(request))["current_estimate"]
    # Direct callbacks get one post-call mutation check; the private snapshot
    # and subsequent freshness comparisons do not re-encode the same record.
    assert records == [2*10**9]
    later = later_record()
    push(coordinator, clock, later)
    assert records == [2*10**9, later["timestamp_ns"]]
    latest_hash = original_digest(later)
    # The native producer and public prepared draft do not alias the private record.
    later["pose_window"]["joint_covariance_native"][0][0] = -1
    draft = coordinator.prepare()
    assert draft["native_record_sha256"] == latest_hash
    draft["request"]["native_record"]["timestamp_ns"] = 42
    result = refresh(coordinator, provider(request))
    assert result["status"] == "research_current_alignment"
    assert result["refresh_attempts"][-1]["snapshot_sha256"] == latest_hash
    assert records == [2*10**9, 3*10**9, 3*10**9]


def test_snapshot_digest_cannot_survive_invalid_epoch():
    coordinator, clock, request, _ = ready()
    assert coordinator._snapshot_sha256
    broken = later_record()
    broken["pose_window"]["joint_covariance_native"][0][0] = -1
    assert push(coordinator, clock, broken)["reason"] == "invalid_vio_snapshot"
    assert coordinator._snapshot_sha256 is None
    assert refresh(coordinator, provider(request))["current_estimate"] is None


def test_callback_cannot_reuse_cached_identity_after_changing_request_snapshot():
    coordinator, _, request, _ = ready()
    def mutating(req):
        result = align(req)
        req["native_record"]["pose_window"]["local_T_imu"][-1][0][3] += 10
        return result
    coordinator._solver = mutating
    result = refresh(coordinator, provider(request))
    assert result["reason"] == "invalid_joint_alignment"
    assert result["current_estimate"] is None
    assert "changed the bound native snapshot" in result["last_alignment"]["detail"]


@pytest.mark.parametrize("when", ["before", "provider", "solve"])
def test_stale_snapshot_withheld_even_after_numerically_valid_solution(monkeypatch, when):
    import vision_nav.estimation.delayed_alignment as module

    coordinator, clock, request, _ = ready()
    original = module.align
    if when == "before":
        clock.value += 500_000_001

    def noise(req):
        if when == "provider":
            clock.value += 500_000_001
        return provider(request)(req)

    def slow(req):
        if when == "solve":
            clock.value += 500_000_001
        return original(req)

    monkeypatch.setattr(module, "align", slow)
    result = refresh(coordinator, noise)
    assert result["reason"] == "vio_snapshot_receipt_stale"
    assert result["current_estimate"] is None


def test_snapshot_without_pose_at_posterior_is_not_current():
    coordinator, clock, request, _ = ready()
    newer = later_record()
    newer["timestamp_ns"] = newer["pose_window"]["posterior_timestamp_ns"] = 4*10**9
    push(coordinator, clock, newer)
    result = refresh(coordinator, provider(request))
    assert result["reason"] == "posterior_has_no_exact_pose"
    assert result["current_estimate"] is None


def test_provider_failure_cleans_reservation_and_never_uses_previous_pose():
    coordinator, _, request, _ = ready()
    assert refresh(coordinator, provider(request))["current_estimate"] is not None

    def broken(req):
        raise RuntimeError("no measured joint model")

    result = refresh(coordinator, broken)
    assert result["reason"] == "joint_uncertainty_provider_failed"
    assert result["current_estimate"] is None
    assert not coordinator.status()["current_refresh_running"]
    assert refresh(coordinator, provider(request))["current_estimate"] is not None


def test_provider_does_not_block_ingestion_and_epoch_failure_vetoes_result():
    coordinator, clock, request, _ = ready()
    entered, release = threading.Event(), threading.Event()
    output = []

    def slow(req):
        entered.set()
        assert release.wait(10)
        return provider(request)(req)

    thread = threading.Thread(target=lambda: output.append(refresh(coordinator, slow)))
    thread.start()
    try:
        assert entered.wait(5)
        assert coordinator.status()["current_refresh_running"]
        assert refresh(coordinator, slow)["reason"] == "current_refresh_already_running"
        assert push(coordinator, clock, later_record())["status"] == "updated"
        coordinator.invalidate("native_worker_failed")
    finally:
        release.set()
        thread.join(10)
    assert not thread.is_alive()
    assert output[0]["reason"] == "epoch_invalidated_during_refresh"
    assert output[0]["current_estimate"] is None


@pytest.mark.parametrize("kwargs", [{"max_attempts": 0}, {"max_attempts": 5}, {"max_attempts": True},
                                    {"max_snapshot_age_ns": 0}, {"max_snapshot_age_ns": True}])
def test_invalid_refresh_budgets(kwargs):
    coordinator, _, request, _ = ready()
    with pytest.raises(ValueError):
        coordinator.refresh_current(provider(request), **{"max_snapshot_age_ns": 500_000_000, **kwargs})
