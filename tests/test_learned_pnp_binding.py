"""Evidence binding tests with synthetic PnP; no learned accuracy claims."""

import copy
from dataclasses import replace
import hashlib
import uuid

import numpy as np
import pytest

from vision_nav.estimation.correlated_window import payload_digest
from vision_nav.estimation.learned_pnp_binding import prepare, bind_uncertainty
from vision_nav.neural.candidate_groups import select_candidate_groups
from vision_nav.vio.live_worker import LiveFrameTicket
from test_reprojection_uncertainty import fixture


def envelope(req=None, *, worker_run_id=None):
    req = copy.deepcopy(fixture()[0] if req is None else req)
    pixels = np.frombuffer(bytes(300), dtype=np.uint8).reshape(10, 10, 3)
    ticket = LiveFrameTicket(worker_run_id or str(uuid.uuid4()), 2, req["context"]["timestamp_ns"],
                            hashlib.sha256(pixels.tobytes()).hexdigest(), "b"*64, pixels)
    provenance = {"worker_run_id": ticket.worker_run_id, "map_release_id": req["context"]["map_release_id"],
                  "calibration_id": req["context"]["calibration_id"], "model_sha256": "a"*64}
    attempt = req["attempt"]
    n = len(attempt["matched_query_pixels"])
    attempt.update(status="verified", tile_id="tile_1", image_sha256="c"*64,
                   feature_counts={"query": n, "map": n}, matched_query_feature_indices=list(range(n)),
                   matched_map_feature_indices=list(range(n)))
    row = {"schema_version": "vision_nav_live_learned_sensor_session_v1", "state": "MODEL_EVIDENCE",
        "position_authority": False, "navigation_mode": "NO_GLOBAL_FIX", "px4_external_vision": "disabled",
        "global_fusion_connected": False, "worker_run_id": ticket.worker_run_id,
        "provenance_sha256": payload_digest(provenance), "sequence": ticket.sequence,
        "anchor_vio_timestamp_ns": ticket.timestamp_ns, "anchor_pixel_sha256": ticket.pixel_sha256,
        "anchor_png_sha256": ticket.image_sha256,
        "window_frame_identities": [{"sequence": ticket.sequence, "timestamp_ns": ticket.timestamp_ns,
                                     "pixel_sha256": ticket.pixel_sha256}],
        "retrieval": [{"tile_id": "tile_1"}], "candidates": [{"tile_id": "tile_1", "map_image_sha256": "c"*64,
            **{key: provenance[key] for key in ("model_sha256", "map_release_id", "calibration_id")}}],
        "local_verification": {"complete": True, "position_authority": False, "px4_external_vision": "disabled",
            "geometry_mode": "surface_backed_pnp_camera_candidate", "attempts": [attempt],
            "candidate_selection": select_candidate_groups([attempt], complete=True)}}
    noise = {**req["uncertainty"], "attempt_sha256": payload_digest(attempt)}
    kwargs = {"frame_ticket": ticket, "provenance": provenance,
              **{key: req["context"][key] for key in ("model_release_id", "clock_id", "uncertainty_model_id")}}
    return row, kwargs, noise


def test_exact_original_attempt_becomes_estimator_request_only_with_explicit_noise():
    row, kwargs, noise = envelope()
    before = copy.deepcopy(row)
    draft = prepare(row, **kwargs)
    assert draft["status"] == "awaiting_joint_noise", draft
    request = draft["request_without_uncertainty"]
    assert request["attempt"] == row["local_verification"]["attempts"][0]
    assert request["context"]["frame_sha256"] == kwargs["frame_ticket"].pixel_sha256
    assert request["context"]["timestamp_ns"] == kwargs["frame_ticket"].timestamp_ns
    result = bind_uncertainty(draft, noise)
    assert result["status"] == "conditional_pnp_request", result
    assert result["assessment"]["publication_covariance"] is None
    assert not result["position_authority"] and not result["covariance_calibrated"]
    assert row == before
    row["local_verification"]["attempts"][0]["matched_query_pixels"][0][0] = -100
    assert draft["request_without_uncertainty"]["attempt"] == before["local_verification"]["attempts"][0]


def test_parent_execution_metadata_does_not_change_child_model_binding():
    row, kwargs, _ = envelope()
    kwargs["provenance"]["execution"] = {"worker_pid": 42}
    assert prepare(row, **kwargs)["status"] == "awaiting_joint_noise"


@pytest.mark.parametrize("failure", ["provenance", "worker", "pixel_hash", "png_hash", "time", "anchor", "state",
    "map", "model", "calibration", "map_image", "unverified", "partial", "planar", "selection", "unchecked_tile",
    "feature_duplicate", "feature_missing", "feature_range", "too_many", "mutable_pixels", "changed_pixels"])
def test_invalid_or_ambiguous_evidence_cannot_become_pnp_input(failure):
    row, kwargs, _ = envelope()
    attempt = row["local_verification"]["attempts"][0]
    if failure == "provenance":
        kwargs["provenance"]["model_sha256"] = "d"*64
    elif failure == "worker":
        row["worker_run_id"] = str(uuid.uuid4())
    elif failure in {"pixel_hash", "png_hash", "time"}:
        key = {"pixel_hash": "anchor_pixel_sha256", "png_hash": "anchor_png_sha256", "time": "anchor_vio_timestamp_ns"}[failure]
        row[key] = 1 if failure == "time" else "d"*64
    elif failure == "anchor":
        row["window_frame_identities"][-1]["timestamp_ns"] += 1
    elif failure == "state":
        row["state"] = "REJECTED"
    elif failure in {"map", "model", "calibration", "map_image"}:
        key = {"map": "map_release_id", "model": "model_sha256", "calibration": "calibration_id", "map_image": "map_image_sha256"}[failure]
        row["candidates"][0][key] = "other"
    elif failure == "unverified":
        attempt["status"] = "unavailable"
    elif failure == "partial":
        row["local_verification"]["complete"] = False
    elif failure == "planar":
        row["local_verification"]["geometry_mode"] = "planar_consistency_only"
    elif failure == "selection":
        row["local_verification"]["candidate_selection"]["selected_tile_id"] = "other"
    elif failure == "unchecked_tile":
        row["retrieval"].append({"tile_id": "not_checked"})
    elif failure == "feature_duplicate":
        attempt["matched_query_feature_indices"][1] = 0
    elif failure == "feature_missing":
        attempt.pop("matched_map_feature_indices")
    elif failure == "feature_range":
        attempt["matched_map_feature_indices"][0] = 999
    elif failure == "too_many":
        attempt["matched_query_pixels"] *= 10
    else:
        ticket = kwargs["frame_ticket"]
        pixels = ticket.pixels.copy()
        if failure == "changed_pixels":
            pixels[0, 0, 0] = 10
            pixels.setflags(write=False)
        kwargs["frame_ticket"] = replace(ticket, pixels=pixels)
    result = prepare(row, **kwargs)
    assert result["status"] == "withheld", result
    assert result["request_without_uncertainty"] is None


def test_two_geographic_hypotheses_are_not_reduced_to_best_inlier_count():
    row, kwargs, _ = envelope()
    other = copy.deepcopy(row["local_verification"]["attempts"][0])
    other["tile_id"] = "tile_2"
    other["geometry"]["camera_position_enu_m"][0] += 100
    row["local_verification"]["attempts"].append(other)
    row["local_verification"]["candidate_selection"] = select_candidate_groups(row["local_verification"]["attempts"], complete=True)
    assert prepare(row, **kwargs)["status"] == "withheld"


@pytest.mark.parametrize("failure", ["no_noise", "changed_draft", "changed_noise", "no_systematics", "nonstationary"])
def test_uncertainty_failures_withhold_request(failure):
    row, kwargs, noise = envelope()
    if failure == "nonstationary":
        row["local_verification"]["attempts"][0]["geometry"]["camera_position_enu_m"][0] += .1
        row["local_verification"]["candidate_selection"] = select_candidate_groups(row["local_verification"]["attempts"], complete=True)
        noise["attempt_sha256"] = payload_digest(row["local_verification"]["attempts"][0])
    draft = prepare(row, **kwargs)
    if failure == "no_noise":
        noise = None
    elif failure == "changed_draft":
        draft["request_without_uncertainty"]["context"]["timestamp_ns"] += 1
    elif failure == "changed_noise":
        noise["attempt_sha256"] = "f"*64
    elif failure == "no_systematics":
        noise.pop("map_systematics")
    result = bind_uncertainty(draft, noise)
    assert result["status"] == "withheld" and result["request"] is None


def test_bound_learned_requests_enter_delayed_joint_estimator():
    from test_delayed_alignment import setup, noise_for
    coordinator, _, original, expected = setup()
    for fix in original["fixes"]:
        row, kwargs, noise = envelope(fix["request"], worker_run_id=coordinator.worker_run_id)
        bound = bind_uncertainty(prepare(row, **kwargs), noise)
        assert bound["status"] == "conditional_pnp_request"
        context = bound["request"]["context"]
        ticket = coordinator.begin_query(context["timestamp_ns"], context["frame_sha256"])["ticket"]
        assert coordinator.complete_query(ticket, bound["request"])["status"] == "ready"
    result = coordinator.refresh_current(lambda req: noise_for({"request": req}, original), max_snapshot_age_ns=500_000_000)
    assert result["status"] == "research_current_alignment"
    np.testing.assert_allclose(result["current_estimate"]["map_T_imu"], expected, atol=1e-6)


def test_numerical_failure_never_exposes_a_request(monkeypatch):
    import vision_nav.estimation.learned_pnp_binding as module
    row, kwargs, noise = envelope()
    draft = prepare(row, **kwargs)

    def fail(_request):
        raise np.linalg.LinAlgError("nonconvergent covariance decomposition")

    monkeypatch.setattr(module, "assess", fail)
    result = bind_uncertainty(draft, noise)
    assert result["status"] == "withheld" and result["request"] is None
