"""Clone membership/ownership, not delayed-fix covariance or flight eligibility."""

from copy import deepcopy
import json
import os
from pathlib import Path
import time

import pytest

from vision_nav.vio.openvins_replay import REVISION, checked_max_clones, make_synthetic, read_yaml
from vision_nav.vio.pose_window import SCHEMA, JointPoseWindow, clone_retention_metadata
from vision_nav.vio.stream_replay import sensor_batches
from vision_nav.vio.stream_worker import NativeVioStreamWorker


def record():
    return {"schema_version": "vision_nav_openvins_local_v1", "source_revision": REVISION,
        "navigation_mode": "NO_GLOBAL_FIX", "position_authority": False, "px4_external_vision": "disabled",
        "timestamp_ns": 500_000_000, "local_state": "LOCAL_ESTIMATE", "pose_window": {
            "schema_version": SCHEMA, "posterior_timestamp_ns": 500_000_000,
            "pose_timestamps_ns": [300_000_000, 400_000_000, 500_000_000],
            "position_authority": False, "map_updates_fused": False,
            "contains_cross_covariance": True, "independent_across_snapshots": False}}


@pytest.mark.parametrize("stamp,expected", [(300_000_000, "retained"), (500_000_000, "retained"),
    (200_000_000, "before_retained_window"), (350_000_000, "processed_without_clone"),
    (600_000_000, "not_yet_processed")])
def test_exact_clone_membership_without_interpolation_or_fusion_claim(stamp, expected):
    row = record()
    before = deepcopy(row)
    result = clone_retention_metadata(row, stamp)
    assert result["status"] == expected
    assert result["anchor_listed"] == (expected == "retained")
    assert result["clone_count"] == 3 and result["clone_span_ms"] == 200.
    assert result["posterior_minus_anchor_ms"] == (500_000_000-stamp)/1e6
    assert not result["position_authority"] and not result["fusion_eligible"]
    assert result["covariance_validation"] == "not_performed"
    assert row == before


def test_missing_initialization_is_not_reported_as_expired_measurement():
    assert clone_retention_metadata(None, 1)["reason"] == "no_native_record"
    row = record()
    row.update(local_state="INITIALIZING_OR_NO_UPDATE", pose_window=None)
    result = clone_retention_metadata(row, 100_000_000)
    assert result["reason"] == "no_local_estimate" and result["anchor_listed"] is None


@pytest.mark.parametrize("fault", ["foreign", "authority", "time", "order", "duplicate", "bool_time",
    "unbounded", "future_clone", "missing_window", "independent", "already_fused"])
def test_invalid_metadata_is_unavailable_not_a_retained_claim(fault):
    row = record()
    window = row["pose_window"]
    if fault == "foreign":
        row["source_revision"] = "different"
    elif fault == "authority":
        row["position_authority"] = True
    elif fault == "time":
        window["posterior_timestamp_ns"] += 1
    elif fault == "order":
        window["pose_timestamps_ns"].reverse()
    elif fault == "duplicate":
        window["pose_timestamps_ns"] = [300_000_000] * 3
    elif fault == "bool_time":
        window["pose_timestamps_ns"][0] = True
    elif fault == "unbounded":
        window["pose_timestamps_ns"] = list(range(1, 34))
    elif fault == "future_clone":
        window["pose_timestamps_ns"][-1] = 600_000_000
    elif fault == "missing_window":
        row["pose_window"] = None
    elif fault == "independent":
        window["independent_across_snapshots"] = True
    else:
        window["map_updates_fused"] = True
    result = clone_retention_metadata(row, 400_000_000)
    assert result["status"] == "unavailable" and result["anchor_listed"] is None
    assert result["reason"] == "invalid_native_retention_metadata"


@pytest.mark.parametrize("value", [True, 1, 33, 11.0, "11", float("inf")])
def test_clone_count_limit_rejects_nonintegers_and_unbounded_history(value, tmp_path):
    with pytest.raises(ValueError, match="max_clones"):
        checked_max_clones(value)
    with pytest.raises(ValueError, match="max_clones"):
        make_synthetic(Path("missing-checkout"), tmp_path / "unused", max_clones=value)
    assert not (tmp_path / "unused").exists()


@pytest.mark.parametrize("maximum,expected", [(11, "before_retained_window"), (24, "retained"), (32, "retained")])
def test_real_native_history_matches_config_and_does_not_consume_snapshot(maximum, expected, tmp_path):
    root = Path(__file__).resolve().parents[1]
    binary = os.environ.get("VISION_NAV_OPENVINS_BINARY")
    if not binary or not (root / "research/vendor/open_vins/.git").exists():
        pytest.skip("Pinned native build required")
    capture = tmp_path / "capture"
    make_synthetic(root / "research/vendor/open_vins", capture, max_clones=maximum)
    assert read_yaml(capture / "estimator_config.yaml")["max_clones"] == maximum
    worker = NativeVioStreamWorker(Path(binary), capture, tmp_path / "native")
    anchor = 7_950_000_000  # One second before the last camera in this fixture.
    assert worker.clone_retention_snapshot(anchor)["status"] == "unavailable"
    worker.start()
    try:
        for batch in sensor_batches(capture / "events.jsonl"):
            deadline = time.monotonic() + 10
            while not worker.submit(batch):
                assert time.monotonic() < deadline
                time.sleep(.005)
        worker.finish()
        assert worker.join(15.)
        result = worker.clone_retention_snapshot(anchor)
        assert result["status"] == expected
        assert result["clone_count"] == maximum
        assert result["native_worker_state"] == "complete"
        assert result["worker_run_id"] == worker.worker_run_id
        assert result["clone_span_ms"] == pytest.approx((maximum-1)*50.)
        # The diagnostic does not steal the posterior from an estimator consumer.
        snapshot = worker.take_latest()
        window = JointPoseWindow.from_record(snapshot["record"])
        assert (anchor in window.timestamps_ns) == (expected == "retained")
        again = worker.clone_retention_snapshot(anchor)
        assert again["native_result_received_host_monotonic_ns"] == result["native_result_received_host_monotonic_ns"]
        assert again["observed_host_monotonic_ns"] > result["observed_host_monotonic_ns"]
        assert worker.take_latest() is None
        # Bind the observation to its exact durable native row, not a later one.
        lines = (tmp_path / "native/local_states.jsonl").read_text().splitlines()
        assert json.loads(lines[result["completed_batch_count"]-1])["timestamp_ns"] == result["posterior_timestamp_ns"]
    finally:
        assert worker.stop()
