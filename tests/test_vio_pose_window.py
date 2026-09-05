"""Joint covariance conversion and correlated relative-pose diagnostics."""

import json

import numpy as np
import pytest

gtsam = pytest.importorskip("gtsam")

from vision_nav.vio.openvins_replay import REVISION, digest
from vision_nav.vio.pose_window import JointPoseWindow, SCHEMA, audit


def record(poses=None, covariance=None):
    if poses is None:
        poses = [np.eye(4), gtsam.Pose3(gtsam.Rot3(), np.array([1., 0., 0.])).matrix()]
    n = len(poses)
    return {"schema_version": "vision_nav_openvins_local_v1", "source_revision": REVISION,
            "timestamp_ns": n*1_000_000_000, "navigation_mode": "NO_GLOBAL_FIX", "position_authority": False,
            "px4_external_vision": "disabled", "local_state": "LOCAL_ESTIMATE", "pose_window": {
                "schema_version": SCHEMA, "posterior_timestamp_ns": n*1_000_000_000,
                "pose_timestamps_ns": [(i+1)*1_000_000_000 for i in range(n)],
                "local_T_imu": [p.tolist() for p in poses],
                "joint_covariance_native": (np.eye(6*n)*.01 if covariance is None else covariance).tolist(),
                "covariance_convention": "per_pose_theta_imu_delta_p_local",
                "linearization": "current_pose_means_with_native_fej_filter_covariance",
                "contains_cross_covariance": True, "independent_across_snapshots": False,
                "map_updates_fused": False, "covariance_calibrated": False, "position_authority": False}}


def test_common_translation_does_not_become_independent_odometry_noise():
    cov = np.eye(12)*.01
    for a in (3, 9):
        for b in (3, 9):
            cov[a:a+3, b:b+3] += np.eye(3)*4.
    window = JointPoseWindow.from_record(record(covariance=cov))
    result = window.relative_diagnostic(*window.timestamps_ns)
    assert np.isclose(result["covariance_right_local"][3][3], .02)
    independent = cov.copy()
    independent[:6, 6:] = independent[6:, :6] = 0
    wrong = JointPoseWindow.from_record(record(covariance=independent)).relative_diagnostic(*window.timestamps_ns)
    assert np.isclose(wrong["covariance_right_local"][3][3], 8.02)
    assert not result["independent_factor"] and not result["position_authority"]
    assert result["uncertainty_kind"].endswith("not_process_noise")


def test_native_to_right_tangent_preserves_all_cross_blocks():
    poses = [gtsam.Pose3.Expmap(np.array([.2, -.3, .8, 3., -2., 1.])).matrix(),
             gtsam.Pose3.Expmap(np.array([-.4, .2, -.5, -1., 1., 3.])).matrix()]
    rng = np.random.default_rng(141)
    factor = rng.normal(size=(12, 12))
    native = factor @ factor.T * 1e-5
    row = record(poses, native)
    window = JointPoseWindow.from_record(row)
    jac = np.eye(12)
    for i, pose in enumerate(poses):
        jac[6*i+3:6*i+6, 6*i+3:6*i+6] = pose[:3, :3].T
    np.testing.assert_allclose(window.covariance_right_local, jac @ native @ jac.T, atol=1e-15)
    assert np.linalg.norm(window.covariance_right_local[:6, 6:]) > 0
    with pytest.raises(ValueError):
        window.covariance_right_local[0, 0] = 0
    with pytest.raises(ValueError):
        window.local_T_imu[0][0, 0] = 0
    row["pose_window"]["local_T_imu"][0][0][3] = 100
    assert window.local_T_imu[0][0, 3] != 100


def test_relative_joint_covariance_matches_monte_carlo():
    poses = [gtsam.Pose3.Expmap(np.array([.2, -.1, .7, 1., 2., 3.])),
             gtsam.Pose3.Expmap(np.array([-.1, .3, -.2, 2., 1., 3.]))]
    rng = np.random.default_rng(14)
    factor = rng.normal(size=(12, 12))*1e-5
    covariance = factor @ factor.T
    window = JointPoseWindow.from_record(record([p.matrix() for p in poses], covariance))
    result = window.relative_diagnostic(*window.timestamps_ns)
    nominal = poses[0].between(poses[1])
    samples = rng.multivariate_normal(np.zeros(12), window.covariance_right_local, size=15000)
    errors = []
    for delta in samples:
        a = poses[0].compose(gtsam.Pose3.Expmap(delta[:6]))
        b = poses[1].compose(gtsam.Pose3.Expmap(delta[6:]))
        errors.append(gtsam.Pose3.Logmap(nominal.between(a.between(b))))
    measured = np.cov(np.asarray(errors).T)
    expected = np.array(result["covariance_right_local"])
    assert np.linalg.norm(measured-expected)/np.linalg.norm(expected) < .04


@pytest.mark.parametrize("failure", ["duplicate", "future", "nan", "negative", "asymmetric", "size", "rotation",
                                    "independent", "map_fused", "convention", "record_time"])
def test_bad_window_rejected(failure):
    row = record()
    w = row["pose_window"]
    if failure == "duplicate":
        w["pose_timestamps_ns"][1] = w["pose_timestamps_ns"][0]
    elif failure == "future":
        w["pose_timestamps_ns"][1] += 1
    elif failure in {"nan", "negative"}:
        w["joint_covariance_native"][0][0] = float("nan") if failure == "nan" else -1.
    elif failure == "asymmetric":
        w["joint_covariance_native"][0][1] = 1.
    elif failure == "size":
        w["joint_covariance_native"].pop()
    elif failure == "rotation":
        w["local_T_imu"][0][0][0] = -1.
    elif failure == "independent":
        w["independent_across_snapshots"] = True
    elif failure == "map_fused":
        w["map_updates_fused"] = True
    elif failure == "convention":
        w["covariance_convention"] = "unknown"
    else:
        row["timestamp_ns"] += 1
    with pytest.raises(ValueError):
        JointPoseWindow.from_record(row)


def test_missing_relative_timestamp_never_interpolates():
    window = JointPoseWindow.from_record(record())
    with pytest.raises(ValueError, match="absent"):
        window.relative_diagnostic(1_000_000_000, 1_500_000_000)
    with pytest.raises(ValueError, match="increasing"):
        window.relative_diagnostic(1_000_000_000, 1_000_000_000)


def test_audit_verifies_source_hash_and_reports_cross_blocks(tmp_path):
    row = record()
    states = tmp_path / "local_states.jsonl"
    states.write_text(json.dumps(row)+"\n")
    report = {"source_revision": REVISION, "pose_window_requested": True, "position_authority": False,
              "states_sha256": digest(states), "complete": True}
    (tmp_path / "report.json").write_text(json.dumps(report))
    result = audit(tmp_path)
    assert result["windows_validated"] == 1
    assert result["local_estimates_without_window"] == 0
    assert result["multi_pose_windows_without_cross_covariance"] == 1
    assert not result["position_authority"]
    states.write_text("{}\n")
    with pytest.raises(ValueError, match="checksum"):
        audit(tmp_path)
