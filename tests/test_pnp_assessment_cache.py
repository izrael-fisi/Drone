"""Exact fix reuse must never substitute for current joint uncertainty checks."""

from copy import deepcopy
from pathlib import Path

import numpy as np
import pytest

from vision_nav.estimation.alignment_worker import ProcessAlignmentEngine
from vision_nav.estimation.bounded_json import dumps
from vision_nav.estimation.correlated_window import payload_digest
from vision_nav.estimation.pnp_window_alignment import PnPAssessmentCache, align
from vision_nav.estimation.reprojection_uncertainty import assess
from test_current_alignment_refresh import provider, ready
from test_delayed_alignment import later_record, push
from test_pnp_window_alignment import fixture
from test_reprojection_uncertainty import fixture as pnp_fixture


def test_cached_alignment_is_exact_and_does_not_expose_cached_values(monkeypatch):
    import vision_nav.estimation.pnp_window_alignment as module
    request, _ = fixture()
    before = deepcopy(request)
    reference = align(request)
    original, assessed = module.assess, []

    def counted(req):
        assessed.append(payload_digest(req))
        return original(req)

    monkeypatch.setattr(module, "assess", counted)
    cache = PnPAssessmentCache()
    first = align(request, assessment_cache=cache)
    assert first == reference
    first["pnp_results"][0]["pose_input_sensitivity"][0][0] = 1e9
    second = align(request, assessment_cache=cache)
    assert second == reference and request == before
    second["pnp_results"][0]["scope"] = "forged"
    assert align(request, assessment_cache=cache) == reference
    assert len(assessed) == 2
    assert cache.status()["hits"] == 4 and cache.status()["misses"] == 2


@pytest.mark.parametrize("field", ["context", "noise", "geometry"])
def test_any_request_change_misses_cache_and_revalidates(field):
    request = pnp_fixture()[0]
    cache = PnPAssessmentCache()
    first = cache.assess(request)
    changed = deepcopy(request)
    if field == "context":
        changed["context"]["timestamp_ns"] += 1
    elif field == "noise":
        changed["uncertainty"]["joint_measurement_covariance"][0][0] += .01
    else:
        changed["attempt"]["geometry"]["position_authority"] = True
    if field == "geometry":
        with pytest.raises(ValueError):
            cache.assess(changed)
    else:
        result = cache.assess(changed)
        assert result["request_sha256"] != first["request_sha256"]
        assert result == assess(changed)
    assert cache.status()["misses"] == 2 and cache.status()["hits"] == 0
    assert cache.assess(request) == first


def test_bounded_lru_retains_no_mutable_request_or_result():
    request = pnp_fixture()[0]
    cache = PnPAssessmentCache(max_entries=2)
    cache.assess(request)
    second, third = deepcopy(request), deepcopy(request)
    second["context"]["timestamp_ns"] += 1
    third["context"]["timestamp_ns"] += 2
    cache.assess(second)
    cache.assess(request)  # Keep the original most-recently used.
    cache.assess(third)
    assert cache.status()["entries"] == 2 and cache.status()["evictions"] == 1
    cache.assess(request)
    cache.assess(second)
    assert cache.status()["misses"] == 4 and cache.status()["hits"] == 2
    second["context"]["timestamp_ns"] = -1
    with pytest.raises(ValueError):
        cache.assess(second)
    assert cache.status()["payload_bytes"] <= cache.status()["max_payload_bytes"]


def test_payload_budget_evicts_or_skips_without_changing_result():
    request = pnp_fixture()[0]
    reference = PnPAssessmentCache().assess(request)
    limit = len(dumps(reference, 2*1024*1024))
    cache = PnPAssessmentCache(max_bytes=limit)
    assert cache.assess(request) == reference
    newer = deepcopy(request)
    newer["context"]["timestamp_ns"] += 1
    cache.assess(newer)
    assert cache.status()["entries"] == 1 and cache.status()["evictions"] == 1
    assert cache.status()["payload_bytes"] <= limit
    tiny = PnPAssessmentCache(max_bytes=1)
    assert tiny.assess(request) == reference
    assert tiny.status()["entries"] == 0 and tiny.status()["uncached"] == 1


@pytest.mark.parametrize("policy", [{"max_entries": 0}, {"max_entries": 17}, {"max_entries": True},
                                    {"max_bytes": 0}, {"max_bytes": 2*1024*1024+1}])
def test_cache_limits_are_explicit(policy):
    with pytest.raises(ValueError):
        PnPAssessmentCache(**policy)


def test_withheld_uncertainty_is_not_cached_as_accepted_geometry():
    request = pnp_fixture()[0]
    covariance = request["uncertainty"]["joint_measurement_covariance"]
    request["uncertainty"]["joint_measurement_covariance"] = np.zeros_like(covariance).tolist()
    cache = PnPAssessmentCache()
    for _ in range(2):
        result = cache.assess(request)
        assert result["status"] == "withheld" and not result["position_authority"]
    assert cache.status()["entries"] == 0 and cache.status()["misses"] == 2
    assert cache.status()["uncached"] == 2


def test_arbitrary_assessment_provider_cannot_bypass_validation():
    with pytest.raises(TypeError):
        align(fixture()[0], assessment_cache=lambda _: {"status": "accepted"})


@pytest.mark.parametrize("fault", ["cross_missing", "cross_indefinite", "diagonal", "snapshot", "config"])
def test_cache_hit_does_not_skip_joint_or_provenance_validation(fault):
    request, _ = fixture()
    cache = PnPAssessmentCache()
    assert align(request, assessment_cache=cache)["status"] == "research_alignment"
    broken = deepcopy(request)
    if fault == "cross_missing":
        broken["joint_uncertainty"].pop("vio_input_cross_covariance")
    elif fault == "cross_indefinite":
        broken["joint_uncertainty"]["vio_input_cross_covariance"][0][0] = 1e4
    elif fault == "diagonal":
        broken["joint_uncertainty"]["joint_measurement_covariance"][0][0] += 1
    elif fault == "snapshot":
        broken["native_record"]["timestamp_ns"] += 1
    else:
        broken["config"]["calibration_id"] = "different"
    with pytest.raises((ValueError, KeyError)):
        align(broken, assessment_cache=cache)
    assert align(request, assessment_cache=cache)["status"] == "research_alignment"


def test_new_snapshot_reuses_only_fixed_geometry_and_produces_new_pose():
    coordinator, clock, original, _ = ready()
    first = coordinator.prepare()["request"]
    first["joint_uncertainty"] = provider(original)(first)
    cache = PnPAssessmentCache()
    before = align(first, assessment_cache=cache)
    push(coordinator, clock, later_record())
    later = coordinator.prepare()["request"]
    later["joint_uncertainty"] = provider(original)(later)
    result = align(later, assessment_cache=cache)
    assert result == align(later)
    assert cache.status()["hits"] == 2 and cache.status()["misses"] == 2
    assert result["alignment"]["pose_timestamp_ns"] == 3*10**9
    assert not np.allclose(result["alignment"]["map_T_imu"], before["alignment"]["map_T_imu"])
    assert result["request_sha256"] != before["request_sha256"]
    assert result["pnp_results"] == before["pnp_results"]
    assert not result["position_authority"] and result["px4_external_vision"] == "disabled"


def test_real_worker_cache_is_observable_and_limited_to_child_epoch():
    request, _ = fixture(shared=False, vio_cross=False)
    request.pop("joint_uncertainty")
    engine = ProcessAlignmentEngine(Path(__file__).parent/"fixtures/alignment_noise.py", operation_timeout_s=5.)
    try:
        engine.start()
        first = engine.align_with_noise(request)
        assert engine.status()["last_operation"]["pnp_assessment_cache"]["misses"] == 2
        second = engine.align_with_noise(request)
        state = engine.status()["last_operation"]["pnp_assessment_cache"]
        assert state["hits"] == 2 and state["misses"] == 2
        assert state["entries"] == 2 and state["payload_bytes"] <= state["max_payload_bytes"]
        assert first["result"] == second["result"]
        assert first["joint_uncertainty"] == second["joint_uncertainty"]
    finally:
        assert engine.stop()
    assert not engine.status()["process_alive"]
