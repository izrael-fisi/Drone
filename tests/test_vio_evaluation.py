"""Metric evaluation must not hide scale drift or missing pose coverage."""

import json

import numpy as np
import pytest

pytest.importorskip("evo")
from scipy.spatial.transform import Rotation

from vision_nav.vio.evaluate_replay import associate, evaluate, ground_truth_csv, pose_matrix, trajectory_metrics
from vision_nav.vio.openvins_replay import REVISION, digest


def trajectory():
    seconds = np.linspace(0., 8., 161)
    poses = np.repeat(np.eye(4)[None], len(seconds), axis=0)
    poses[:, :3, 3] = np.column_stack([seconds, np.sin(seconds), .2*np.cos(seconds)])
    poses[:, :3, :3] = Rotation.from_euler("z", seconds[:, None]*.1).as_matrix()
    return seconds, poses


def test_rigid_gauge_does_not_count_as_drift():
    seconds, truth = trajectory()
    gauge = np.eye(4)
    gauge[:3, :3] = Rotation.from_euler("xyz", [.3, -.2, .7]).as_matrix()
    gauge[:3, 3] = [10., -5., 2.]
    result = trajectory_metrics(truth, gauge @ truth, seconds)
    for key in ("initial_pose_alignment", "full_trajectory_se3_alignment"):
        assert result[key]["position_m"]["rmse"] < 1e-10
        assert result[key]["orientation_deg"]["rmse"] < 1e-5
    assert result["scale_correction"] is False
    assert result["rpe_reference_1m_translation_m"]["rmse"] < 1e-10


def test_scale_error_is_not_aligned_away():
    seconds, truth = trajectory()
    estimate = truth.copy()
    estimate[:, :3, 3] *= 2
    result = trajectory_metrics(truth, estimate, seconds)
    assert result["alignment"]["scale"] == 1
    assert result["full_trajectory_se3_alignment"]["position_m"]["rmse"] > 2
    assert result["rpe_reference_1m_translation_m"]["rmse"] > .8


def test_static_alignment_is_explicitly_unavailable():
    poses = np.repeat(np.eye(4)[None], 5, axis=0)
    result = trajectory_metrics(poses, poses, np.arange(5.))
    assert result["full_trajectory_se3_alignment"] is None
    assert result["initial_pose_alignment"]["position_m"]["rmse"] == 0
    assert result["rpe_reference_1m_translation_m"] is None


def test_relative_error_does_not_span_association_dropouts():
    seconds = np.r_[np.arange(7)*.05, 2+np.arange(7)*.05]
    poses = np.repeat(np.eye(4)[None], len(seconds), axis=0)
    poses[:, 0, 3] = np.arange(len(seconds))*.1
    result = trajectory_metrics(poses, poses, seconds)
    assert result["rpe_reference_1m_translation_m"] is None


def test_association_keeps_integer_clock_and_does_not_bridge_gaps():
    origin = 1520530300000000000
    ns = np.array([origin, origin+10_000_000, origin+100_000_000], dtype=np.int64)
    poses = np.repeat(np.eye(4)[None], 3, axis=0)
    poses[1, 0, 3] = 1.
    poses[1, :3, :3] = Rotation.from_euler("z", 90, degrees=True).as_matrix()
    ids, ref = associate([origin-1, origin+5_000_000, origin+50_000_000, origin+100_000_001], ns, poses, 20_000_000)
    assert ids.tolist() == [1]
    assert ref[0][0, 3] == .5
    assert np.allclose(ref[0][:3, :3], Rotation.from_euler("z", 45, degrees=True).as_matrix())


@pytest.mark.parametrize("bad", [np.zeros((4, 4)), np.diag([-1., 1., 1., 1.]), np.full((4, 4), np.nan)])
def test_invalid_pose_rejected(bad):
    with pytest.raises(ValueError, match="SE3"):
        pose_matrix(bad)


def fixture(tmp_path):
    seconds, poses = trajectory()
    ns = 1_520_530_300_000_000_000 + np.rint(seconds*1e9).astype(np.int64)
    truth = tmp_path / "truth.csv"
    quats = Rotation.from_matrix(poses[:, :3, :3]).as_quat()
    truth.write_text("#timestamp,p_x,p_y,p_z,q_w,q_x,q_y,q_z\n" + "".join(
        ",".join(map(str, [t, *p[:3, 3], q[3], *q[:3]])) + "\n" for t, p, q in zip(ns, poses, quats)))
    rows = [{"timestamp_ns": int(t), "source_revision": REVISION, "position_authority": False,
             "navigation_mode": "NO_GLOBAL_FIX", "px4_external_vision": "disabled",
             "local_state": "INITIALIZING_OR_NO_UPDATE" if i < 20 else "LOCAL_ESTIMATE",
             "local_T_imu": None if i < 20 else p.tolist()} for i, (t, p) in enumerate(zip(ns, poses))]
    states = tmp_path / "local_states.jsonl"
    states.write_text("".join(json.dumps(r)+"\n" for r in rows))
    report = {"source_revision": REVISION, "position_authority": False, "complete": True, "states_sha256": digest(states)}
    (tmp_path / "report.json").write_text(json.dumps(report))
    return truth, states, report


def test_reports_initialization_and_coverage(tmp_path):
    truth, _, _ = fixture(tmp_path)
    result = evaluate(tmp_path, truth)
    assert result["camera_frames"] == 161
    assert result["local_estimate_frames"] == result["associated_frames"] == 141
    assert result["first_local_update_s"] == 1.
    assert result["max_local_update_gap_s"] == .05
    assert result["ground_truth_used_by_frontend"] is False
    assert result["metrics"]["initial_pose_alignment"]["position_m"]["rmse"] < 1e-9


def test_tampered_states_rejected(tmp_path):
    truth, states, _ = fixture(tmp_path)
    states.write_text(states.read_text() + "{}\n")
    with pytest.raises(ValueError, match="checksum"):
        evaluate(tmp_path, truth)


def test_reordered_ground_truth_rejected(tmp_path):
    truth, _, _ = fixture(tmp_path)
    rows = truth.read_text().splitlines()
    rows[1], rows[2] = rows[2], rows[1]
    truth.write_text("\n".join(rows))
    with pytest.raises(ValueError, match="timestamp"):
        ground_truth_csv(truth)


def test_no_initialization_does_not_produce_metrics(tmp_path):
    truth, states, report = fixture(tmp_path)
    rows = [json.loads(line) for line in states.read_text().splitlines()]
    for row in rows:
        row.update(local_state="INITIALIZING_OR_NO_UPDATE", local_T_imu=None)
    states.write_text("".join(json.dumps(r)+"\n" for r in rows))
    report.update(complete=False, states_sha256=digest(states))
    (tmp_path / "report.json").write_text(json.dumps(report))
    result = evaluate(tmp_path, truth)
    assert result["metrics"] is None
    assert result["local_update_fraction"] == 0
    assert result["replay_complete"] is False
