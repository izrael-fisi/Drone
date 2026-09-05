"""Fragment reuse preserves the existing canonical JSON identity exactly."""

from copy import deepcopy
import random

import pytest

from vision_nav.estimation.canonical_identity import encode, object_digest
from vision_nav.estimation.correlated_window import payload_digest
from test_current_alignment_refresh import ready
from test_delayed_alignment import later_record, push
from test_joint_alignment_transaction import executor, refresh


@pytest.mark.parametrize("value", [
    {}, {"a": None}, {"quoted\"key": "\n\u2603\u0000", "\\": "\ud800"},
    {"z": [0., -0., 5e-324, 1.7976931348623157e308], "a": True},
    {"native_record": {"timestamps": [2**63-1, 2**64]}, "joint_uncertainty": [[1., .1], [.1, 2.]]},
])
def test_fragment_composition_matches_existing_hash(value):
    fields = {key: encode(child) for key, child in value.items()}
    assert object_digest(fields) == payload_digest(value)


def test_noise_insertion_reuses_bytes_but_not_previous_noise_identity():
    rng = random.Random(183)
    native = {"poses": [[rng.uniform(-100, 100) for _ in range(6)] for _ in range(20)]}
    original = {"config": {"frame": "ENU"}, "native_record": native,
                "fixes": [{"fix_id": "one"}], "schema_version": "example"}
    fields = {key: encode(value) for key, value in original.items()}
    for _ in range(20):
        noise = {"covariance": [[rng.random() for _ in range(6)] for _ in range(6)]}
        assert object_digest({**fields, "joint_uncertainty": encode(noise)}) == payload_digest(
            {**original, "joint_uncertainty": noise})
    # The cached bytes are not references to mutable caller values.
    expected = object_digest(fields)
    native["poses"][0][0] += 10
    assert object_digest(fields) == expected != payload_digest(original)


@pytest.mark.parametrize("fields", [{"a": []}, {1: b"null"}, {"a": bytearray(b"null")}])
def test_fragment_contract_is_private_owned_bytes(fields):
    with pytest.raises(TypeError):
        object_digest(fields)


def test_public_draft_remains_json_and_does_not_expose_cached_fields():
    coordinator, _, _, _ = ready()
    draft = coordinator.prepare()
    assert "encoded_fields" not in draft
    assert encode(draft)
    assert draft["request_sha256"] == payload_digest(draft["request"])
    draft["request"]["config"]["map_release_id"] = "changed externally"
    assert coordinator._prepared["request"]["config"]["map_release_id"] != "changed externally"


def test_new_snapshot_during_solve_does_not_rebind_old_encoded_fields():
    coordinator, clock, request, _ = ready()
    drafts = []
    def before(req):
        drafts.append(deepcopy(req))
        if len(drafts) == 1:
            push(coordinator, clock, later_record())
    result = refresh(coordinator, executor(request, before=before))
    assert len(result["refresh_attempts"]) == 2
    assert result["current_estimate"]["pose_timestamp_ns"] == 3_000_000_000
    assert result["refresh_attempts"][0]["snapshot_sha256"] == payload_digest(drafts[0]["native_record"])
    assert result["refresh_attempts"][1]["snapshot_sha256"] == payload_digest(drafts[1]["native_record"])
    assert result["last_alignment"]["request_sha256"] == result["last_alignment"]["result"]["request_sha256"]


def test_combined_callback_cannot_change_private_fragments_or_request():
    coordinator, _, request, _ = ready()
    perform = executor(request)
    def mutating(req):
        req["native_record"]["pose_window"]["local_T_imu"][-1][0][3] += 10
        return perform(req)
    result = refresh(coordinator, mutating)
    assert result["current_estimate"] is None
    assert result["reason"] == "invalid_joint_alignment"
    assert coordinator._snapshot_encoded == encode(coordinator._record)
    assert refresh(coordinator, perform)["current_estimate"] is not None


def test_fragment_cache_is_retired_with_epoch():
    coordinator, _, _, _ = ready()
    assert coordinator._snapshot_encoded is not None
    coordinator.invalidate("sensor_lost")
    assert coordinator._snapshot_encoded is None
    assert coordinator._prepared is None
