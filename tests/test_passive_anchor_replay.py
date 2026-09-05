"""Native retained-pose replay; map observations/noise remain explicitly synthetic."""

from copy import deepcopy
import json
import os
from pathlib import Path

import numpy as np
import pytest

from vision_nav.vio.openvins_replay import load_anchor_schedule, make_synthetic, run, digest
from vision_nav.vio.pose_window import JointPoseWindow, clone_retention_metadata, ANCHOR_SCHEMA


def schedule(actions, capacity=2):
    return {"schema_version": "vision_nav_openvins_passive_anchor_schedule_v1", "capacity": capacity, "actions": actions}


def action(at, anchor=None, operation="retain"):
    return {"at_timestamp_ns": at, "anchor_timestamp_ns": at if anchor is None else anchor, "operation": operation}


@pytest.fixture(scope="module")
def replay(tmp_path_factory):
    root = Path(__file__).resolve().parents[1]
    location = os.environ.get("VISION_NAV_OPENVINS_BINARY")
    if not location:
        pytest.skip("Set actual native binary for passive anchor replay")
    binary = Path(location).resolve()
    tmp = tmp_path_factory.mktemp("passive-anchor")
    capture = tmp / "capture"
    make_synthetic(root / "research/vendor/open_vins", capture)
    baseline = tmp / "baseline"
    assert run(binary, capture, baseline, pose_window=True)["complete"]
    plain = [json.loads(line) for line in (baseline / "local_states.jsonl").read_text().splitlines()]
    start = next(row["timestamp_ns"] for row in plain if row["local_state"] == "LOCAL_ESTIMATE")
    spec = tmp / "schedule.json"
    spec.write_text(json.dumps(schedule([action(start)])))
    output = tmp / "retained"
    report = run(binary, capture, output, pose_window=True, anchor_schedule=spec)
    assert report["complete"], (report, (output / "native.log").read_text())
    kept = [json.loads(line) for line in (output / "local_states.jsonl").read_text().splitlines()]
    return binary, capture, tmp, plain, kept, start, report


def test_native_passive_pose_survives_normal_window_with_joint_covariance(replay):
    from vision_nav.vio.pose_window import audit

    _, _, tmp, plain, kept, anchor, report = replay
    last = kept[-1]
    window = JointPoseWindow.from_record(last)
    assert anchor not in plain[-1]["pose_window"]["pose_timestamps_ns"]
    assert anchor in window.timestamps_ns
    assert last["pose_window"]["normal_clone_timestamps_ns"] == plain[-1]["pose_window"]["pose_timestamps_ns"]
    assert last["pose_window"]["passive_anchor_timestamps_ns"] == [anchor]
    assert window.posterior_ns-anchor > 500_000_000
    assert len(window.timestamps_ns) == len(plain[-1]["pose_window"]["pose_timestamps_ns"])+1
    assert np.linalg.norm(window.covariance_right_local[:6, -6:]) > 0
    assert clone_retention_metadata(last, anchor)["anchor_retention_kind"] == "passive_stochastic_clone"
    assert report["passive_anchors"]["schedule_sha256"] == digest(tmp / "retained/anchor_schedule.json")
    assert not report["position_authority"]
    audited = audit(tmp / "retained")
    assert audited["passive_anchor_schedule_verified"] and audited["passive_anchor_actions_verified"] == 1
    assert audited["replay_complete"] and not audited["local_estimates_without_window"]


def test_adding_passive_state_does_not_change_native_tracking(replay):
    _, _, _, plain, kept, _, _ = replay
    assert len(plain) == len(kept) == 160
    for before, after in zip(plain, kept):
        assert before["timestamp_ns"] == after["timestamp_ns"] and before["local_state"] == after["local_state"]
        if before["local_state"] == "LOCAL_ESTIMATE":
            for field in ("local_T_imu", "velocity_local_m_s", "gyro_bias_rad_s", "accel_bias_m_s2", "state_covariance"):
                np.testing.assert_allclose(after[field], before[field], rtol=1e-8, atol=1e-10)
            assert after["msckf_used_features"] == before["msckf_used_features"]


def test_unchanged_original_map_observation_aligns_a_pose_after_normal_anchor_expiry(replay):
    from vision_nav.estimation.correlated_window import payload_digest
    from vision_nav.estimation.pnp_window_alignment import align
    from test_pnp_window_alignment import fixture as map_fixture

    _, _, _, plain, kept, anchor, _ = replay
    initial = next(row for row in kept if row["timestamp_ns"] == anchor)
    request, _ = map_fixture(initial, shared=False, vio_cross=False)
    original = deepcopy(request["fixes"][-1])
    assert original["request"]["context"]["timestamp_ns"] == anchor
    request["fixes"] = [original]
    request["native_record"] = kept[-1]
    d = len(original["request"]["uncertainty"]["joint_measurement_covariance"])
    request["joint_uncertainty"].update(snapshot_sha256=payload_digest(kept[-1]),
        pnp_request_sha256s=[payload_digest(original["request"])],
        joint_measurement_covariance=original["request"]["uncertainty"]["joint_measurement_covariance"],
        vio_input_cross_covariance=np.zeros((6*len(kept[-1]["pose_window"]["pose_timestamps_ns"]), d)).tolist())
    before = deepcopy(request)
    result = align(request)
    assert result["status"] == "research_alignment", result
    assert result["alignment"]["pose_timestamp_ns"] == kept[-1]["timestamp_ns"]
    assert result["alignment"]["fix_ids"] == [original["fix_id"]]
    assert not result["position_authority"] and not result["covariance_calibrated"]
    assert result["navigation_mode"] == "NO_GLOBAL_FIX" and request == before
    (replay[2] / "retained-alignment.json").write_text(json.dumps({"request": request, "result": result}, allow_nan=False)+"\n")
    # The same original observation cannot be fused against the ordinary window.
    request["native_record"] = plain[-1]
    request["joint_uncertainty"]["snapshot_sha256"] = payload_digest(plain[-1])
    with pytest.raises(ValueError):
        align(request)


def test_explicit_release_removes_passive_state_without_retaining_stale_copy(replay):
    binary, capture, tmp, plain, _, anchor, _ = replay
    spec = tmp / "release.json"
    spec.write_text(json.dumps(schedule([action(anchor), action(plain[-1]["timestamp_ns"], anchor, "release")])))
    output = tmp / "released"
    report = run(binary, capture, output, pose_window=True, anchor_schedule=spec)
    assert report["complete"]
    last = json.loads((output / "local_states.jsonl").read_text().splitlines()[-1])
    assert last["pose_window"]["passive_anchor_timestamps_ns"] == []
    assert JointPoseWindow.from_record(last).timestamps_ns == JointPoseWindow.from_record(plain[-1]).timestamps_ns
    assert clone_retention_metadata(last, anchor)["anchor_listed"] is False


def test_multiple_passive_anchors_preserve_identity_and_release_only_selected_pose(replay):
    binary, capture, tmp, plain, _, first, _ = replay
    second = plain[-20]["timestamp_ns"]
    last = plain[-1]["timestamp_ns"]
    spec = tmp / "multiple.json"
    spec.write_text(json.dumps(schedule([action(first), action(second), action(last, first, "release")])))
    output = tmp / "multiple"
    assert run(binary, capture, output, pose_window=True, anchor_schedule=spec)["complete"]
    rows = [json.loads(line) for line in (output / "local_states.jsonl").read_text().splitlines()]
    before = JointPoseWindow.from_record(rows[-2])
    assert rows[-2]["pose_window"]["passive_anchor_timestamps_ns"] == [first, second]
    i, j = before.timestamps_ns.index(first), before.timestamps_ns.index(second)
    assert np.linalg.norm(before.covariance_right_local[6*i:6*i+6, 6*j:6*j+6]) > 0
    after = JointPoseWindow.from_record(rows[-1])
    assert first not in after.timestamps_ns and second in after.timestamps_ns
    assert rows[-1]["pose_window"]["passive_anchor_timestamps_ns"] == [second]


@pytest.mark.parametrize("kind", ["not_camera", "before_initialization", "future_eof", "expired_clone"])
def test_unexecutable_schedule_fails_native_replay(replay, kind):
    binary, capture, tmp, plain, _, anchor, _ = replay
    at, target = anchor, anchor
    if kind == "not_camera":
        at = anchor+1
    elif kind == "before_initialization":
        at = target = plain[0]["timestamp_ns"]
    elif kind == "future_eof":
        at = plain[-1]["timestamp_ns"]+1
    else:
        at = plain[-1]["timestamp_ns"]
    spec = tmp / f"invalid-{kind}.json"
    spec.write_text(json.dumps(schedule([action(at, target)])))
    report = run(binary, capture, tmp / f"failed-{kind}", pose_window=True, anchor_schedule=spec)
    assert report["returncode"] == 2 and not report["complete"]


@pytest.mark.parametrize("kind", ["capacity", "full", "duplicate", "unknown_release", "operation", "time", "order", "extra", "oversize"])
def test_schedule_rejects_invalid_bounded_actions(tmp_path, kind):
    data = schedule([action(10), action(20)], capacity=2)
    if kind == "capacity": data["capacity"] = True
    elif kind == "full": data["capacity"] = 1
    elif kind == "duplicate": data["actions"][1] = action(20, 10)
    elif kind == "unknown_release": data["actions"][0]["operation"] = "release"
    elif kind == "operation": data["actions"][0]["operation"] = "guess"
    elif kind == "time": data["actions"][0]["at_timestamp_ns"] = True
    elif kind == "order": data["actions"].reverse()
    elif kind == "extra": data["actions"][0]["noise"] = 0
    path = tmp_path / "schedule.json"
    path.write_text(" "*131073 if kind == "oversize" else json.dumps(data))
    with pytest.raises(ValueError): load_anchor_schedule(path)


@pytest.mark.parametrize("kind", ["capacity", "policy", "missing", "duplicate", "order", "membership", "normal"])
def test_v2_pose_metadata_cannot_invent_or_duplicate_retained_information(kind):
    from test_vio_pose_window import record

    data = record()
    window = data["pose_window"]
    times = window["pose_timestamps_ns"]
    window.update(schema_version=ANCHOR_SCHEMA, normal_clone_timestamps_ns=times[1:],
                  passive_anchor_timestamps_ns=times[:1], passive_anchor_capacity=1,
                  retention_policy="native_passive_stochastic_clones_explicit_release")
    assert JointPoseWindow.from_record(data)
    if kind == "capacity": window["passive_anchor_capacity"] = 5
    elif kind == "policy": window["retention_policy"] = "cached_old_posterior"
    elif kind == "missing": window["passive_anchor_timestamps_ns"] = []
    elif kind == "duplicate": window["passive_anchor_timestamps_ns"] = [times[0]]*2
    elif kind == "order": window["normal_clone_timestamps_ns"] = list(reversed(times))
    elif kind == "membership": window["passive_anchor_timestamps_ns"] = [times[0]-1]
    else: window["normal_clone_timestamps_ns"] = []
    with pytest.raises(ValueError): JointPoseWindow.from_record(data)
    assert clone_retention_metadata(data, times[0])["status"] == "unavailable"


def test_v2_window_supports_only_bounded_extra_passive_poses():
    from test_vio_pose_window import record

    data = record(poses=[np.eye(4) for _ in range(36)])
    w = data["pose_window"]
    t = w["pose_timestamps_ns"]
    w.update(schema_version=ANCHOR_SCHEMA, normal_clone_timestamps_ns=t[4:],
             passive_anchor_timestamps_ns=t[:4], passive_anchor_capacity=4,
             retention_policy="native_passive_stochastic_clones_explicit_release")
    assert len(JointPoseWindow.from_record(data).timestamps_ns) == 36
    w["normal_clone_timestamps_ns"] = t[3:]
    with pytest.raises(ValueError, match="metadata"):
        JointPoseWindow.from_record(data)


def test_default_native_output_is_unchanged_from_previous_binary(replay):
    binary = os.environ.get("VISION_NAV_OPENVINS_BASELINE_BINARY")
    if not binary:
        pytest.skip("Previous native binary needed for byte-for-byte compatibility")
    _, capture, tmp, _, _, _, _ = replay
    previous = tmp / "previous"
    assert run(Path(binary), capture, previous, pose_window=True)["complete"]
    assert (previous / "local_states.jsonl").read_bytes() == (tmp / "baseline/local_states.jsonl").read_bytes()


@pytest.mark.parametrize("kind", ["schedule", "actions", "membership", "provenance", "uninitialized"])
def test_anchor_audit_rejects_modified_provenance_or_native_evidence(replay, tmp_path, kind):
    from vision_nav.vio.pose_window import audit

    source = replay[2] / "retained"
    for name in ("report.json", "anchor_schedule.json", "local_states.jsonl"):
        (tmp_path / name).write_bytes((source / name).read_bytes())
    if kind == "schedule":
        (tmp_path / "anchor_schedule.json").write_text(json.dumps(schedule([action(1)])))
    else:
        report = json.loads((tmp_path / "report.json").read_text())
        if kind == "provenance":
            report.pop("passive_anchors")
        else:
            rows = [json.loads(line) for line in (tmp_path / "local_states.jsonl").read_text().splitlines()]
            if kind == "actions": rows[-1]["passive_anchor_actions"] = [action(1)]
            elif kind == "uninitialized":
                first = next(row for row in rows if row["passive_anchor_actions"])
                first["local_state"] = "INITIALIZING_OR_NO_UPDATE"
                first["pose_window"] = None
            else: rows[-1]["pose_window"]["passive_anchor_capacity"] = 3
            (tmp_path / "local_states.jsonl").write_text("".join(json.dumps(row)+"\n" for row in rows))
            report["states_sha256"] = digest(tmp_path / "local_states.jsonl")
        (tmp_path / "report.json").write_text(json.dumps(report))
    with pytest.raises(ValueError): audit(tmp_path)
