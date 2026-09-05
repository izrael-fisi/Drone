"""Single-snapshot map alignment must retain joint uncertainty and ownership."""

import copy
from dataclasses import asdict
import json

import numpy as np
import pytest

gtsam = pytest.importorskip("gtsam")
from vision_nav.estimation.correlated_window import CorrelatedWindowAligner, WindowAlignmentConfig, main, payload_digest
from test_vio_pose_window import record


def fixture():
    config = WindowAlignmentConfig("map@1", "model@1", "cam@1", "fixture-clock", "synthetic-noise@1",
                                   np.eye(4), np.eye(6)*1e-6)
    row = record()
    map_T_local = gtsam.Pose3(gtsam.Rot3(), np.array([10., 20., 30.]))
    fixes = [{"fix_id": f"fix-{i}", "timestamp_ns": ns,
              "map_T_camera": map_T_local.compose(gtsam.Pose3(p)).matrix().tolist()}
             for i, (ns, p) in enumerate(zip(row["pose_window"]["pose_timestamps_ns"], row["pose_window"]["local_T_imu"]))]
    batch = {"schema_version": "vision_nav_correlated_map_batch_v1", "snapshot_sha256": payload_digest(row),
             **{k: getattr(config, k) for k in ("map_release_id", "model_release_id", "calibration_id", "clock_id", "uncertainty_model_id")},
             "covariance_convention": "joint_right_local_pose_residuals", "covariance_scope": "synthetic_contract_test",
             "cross_covariance_model": "explicit synthetic residual correlation",
             "common_map_error_independent_of_joint_residuals": True, "extrinsics_treated_as_fixed": True,
             "fixes": fixes, "map_joint_covariance": (np.eye(12)*.02).tolist(),
             "vio_map_cross_covariance": np.zeros((12, 12)).tolist()}
    return config, row, batch


def test_recovers_alignment_and_repeated_snapshot_does_not_accumulate():
    config, row, batch = fixture()
    aligner = CorrelatedWindowAligner(config)
    before = copy.deepcopy(row)
    first, second = aligner.solve(row, batch), aligner.solve(row, batch)
    assert first == second
    assert first["status"] == "research_alignment"
    assert first["snapshot_policy"] == "replace_do_not_accumulate"
    assert first["navigation_mode"] == "NO_GLOBAL_FIX" and not first["position_authority"]
    assert first["confidence"] is None and not first["covariance_calibrated"]
    np.testing.assert_allclose(np.array(first["map_T_imu"])[:3, 3], [11, 20, 30], atol=1e-9)
    assert before == row


def test_camera_lever_arm_and_rotation_are_in_the_measurement_model():
    config, row, batch = fixture()
    ext = gtsam.Pose3.Expmap(np.array([.2, -.3, .7, .4, -.2, .1]))
    config = WindowAlignmentConfig(**{**asdict(config), "imu_T_camera": ext.matrix()})
    for fix in batch["fixes"]:
        fix["map_T_camera"] = gtsam.Pose3(np.array(fix["map_T_camera"])).compose(ext).matrix().tolist()
    result = CorrelatedWindowAligner(config).solve(row, batch)
    assert result["status"] == "research_alignment"
    np.testing.assert_allclose(np.array(result["map_T_imu"])[:3, 3], [11., 20., 30.], atol=1e-8)


def test_joint_covariance_matches_linear_gaussian_reference():
    config, row, batch = fixture()
    # Translation-x is decoupled on this straight, unrotated fixture. Compare
    # against an independent dense Gaussian calculation, including cross terms.
    local = np.array([[.4, .2], [.2, .5]])
    map_cov = np.array([[.8, .5], [.5, .9]])
    cross = np.array([[.1, .06], [.08, .12]])
    full = np.block([[local, cross], [cross.T, map_cov]])
    p, q, c = np.eye(12)*1e-5, np.eye(12)*1e-5, np.zeros((12, 12))
    ids = [3, 9]
    p[np.ix_(ids, ids)] = local
    q[np.ix_(ids, ids)] = map_cov
    c[np.ix_(ids, ids)] = cross
    row["pose_window"]["joint_covariance_native"] = p.tolist()
    batch.update(snapshot_sha256=payload_digest(row), map_joint_covariance=q.tolist(), vio_map_cross_covariance=c.tolist())
    result = CorrelatedWindowAligner(config).solve(row, batch)
    assert result["status"] == "research_alignment"
    h = np.array([[1, 0, 0], [0, 1, 0], [1, 0, -1], [0, 1, -1.]])
    cov = np.linalg.inv(h.T @ np.linalg.solve(full, h))
    output = np.array([0, 1, -1.])
    expected = output @ cov @ output
    assert result["conditional_covariance_right_imu"][3][3] == pytest.approx(expected, rel=1e-5)


def test_shared_map_noise_does_not_average_away():
    config, row, batch = fixture()
    p = np.eye(12)*1e-8
    row["pose_window"]["joint_covariance_native"] = p.tolist()
    q = np.eye(12)
    for a in (3, 9):
        for b in (3, 9):
            q[a:a+3, b:b+3] += np.eye(3)*4
    batch.update(snapshot_sha256=payload_digest(row), map_joint_covariance=q.tolist())
    result = CorrelatedWindowAligner(config).solve(row, batch)
    assert result["status"] == "research_alignment"
    assert result["conditional_covariance_right_imu"][3][3] == pytest.approx(4.5, abs=1e-5)


def test_delayed_fix_updates_unobserved_current_pose_with_joint_gaussian_covariance():
    config, _, batch = fixture()
    config = WindowAlignmentConfig(**{**asdict(config), "map_common_left_covariance": np.zeros((6, 6))})
    poses = [np.eye(4) for _ in range(3)]
    for i, pose in enumerate(poses):
        pose[0, 3] = float(i)
    # Independent reference: estimate x0,x1,x2 and map/local offset a jointly.
    # Only x0 and x1 have map measurements; x2 must retain all their correlations.
    local = np.array([[.4, .2, .15], [.2, .5, .3], [.15, .3, .8]])
    map_cov = np.array([[.8, .5], [.5, .9]])
    cross = np.array([[.1, .06], [.08, .12], [.04, .07]])
    full = np.block([[local, cross], [cross.T, map_cov]])
    p, q, c = np.eye(18)*1e-6, np.eye(12)*1e-6, np.zeros((18, 12))
    p[np.ix_([3, 9, 15], [3, 9, 15])] = local
    q[np.ix_([3, 9], [3, 9])] = map_cov
    c[np.ix_([3, 9, 15], [3, 9])] = cross
    row = record(poses, p)
    batch.update(snapshot_sha256=payload_digest(row), map_joint_covariance=q.tolist(), vio_map_cross_covariance=c.tolist())
    # Nonzero residual tests conditional mean as well as uncertainty propagation.
    batch["fixes"][1]["map_T_camera"][0][3] += .1
    result = CorrelatedWindowAligner(config).solve(row, batch)
    assert result["status"] == "research_alignment"
    h = np.array([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0],
                  [1, 0, 0, -1], [0, 1, 0, -1.]])
    observations = np.array([0, 1, 2, 10, 11.1])
    joint_posterior = np.linalg.inv(h.T @ np.linalg.solve(full, h))
    mean = joint_posterior @ h.T @ np.linalg.solve(full, observations)
    output = np.array([0, 0, 1, -1.])
    assert result["map_T_imu"][0][3] == pytest.approx(output @ mean, abs=1e-5)
    assert result["conditional_covariance_right_imu"][3][3] == pytest.approx(output @ joint_posterior @ output, abs=1e-5)
    assert result["pose_timestamp_ns"] == row["timestamp_ns"] == 3*10**9
    assert result["clock_id"] == config.clock_id
    assert result["body_frame"] == "imu"


def test_large_inconsistent_fix_set_is_withheld():
    config, row, batch = fixture()
    batch["fixes"][1]["map_T_camera"][0][3] += 30.
    result = CorrelatedWindowAligner(config).solve(row, batch)
    assert result["status"] == "withheld"
    assert result["reason"] == "inconsistent_joint_map_vio_batch"
    assert result["map_T_imu"] is None and result["covariance_right_imu"] is None


def test_single_delayed_fix_is_not_a_consistency_test():
    config, row, batch = fixture()
    batch["fixes"] = batch["fixes"][:1]
    batch["map_joint_covariance"] = (np.eye(6)*.02).tolist()
    batch["vio_map_cross_covariance"] = np.zeros((12, 6)).tolist()
    result = CorrelatedWindowAligner(config).solve(row, batch)
    assert result["status"] == "research_alignment"
    assert result["consistency_test"] == "untestable_single_map_fix"
    assert result["residual_dof"] == 0
    assert result["pose_timestamp_ns"] == 2_000_000_000


@pytest.mark.parametrize("failure", ["snapshot", "identity", "duplicate", "missing_time", "cross_missing", "indefinite", "convention"])
def test_invalid_batch_rejected(failure):
    config, row, batch = fixture()
    if failure == "snapshot":
        batch["snapshot_sha256"] = "0"*64
    elif failure == "identity":
        batch["calibration_id"] = "wrong"
    elif failure == "duplicate":
        batch["fixes"][1] = batch["fixes"][0]
    elif failure == "missing_time":
        batch["fixes"][0]["timestamp_ns"] += 1
    elif failure == "cross_missing":
        batch["vio_map_cross_covariance"] = None
    elif failure == "indefinite":
        batch["vio_map_cross_covariance"] = np.ones((12, 12)).tolist()
    else:
        batch["covariance_convention"] = "unknown"
    with pytest.raises(ValueError):
        CorrelatedWindowAligner(config).solve(row, batch)


def test_factor_jacobians_with_nonzero_residual_and_nonidentity_extrinsics():
    config, row, batch = fixture()
    config = WindowAlignmentConfig(**{**asdict(config), "imu_T_camera": gtsam.Pose3.Expmap(np.array([.2,.1,-.3,.3,.2,.1])).matrix()})
    aligner = CorrelatedWindowAligner(config)
    prepared = aligner._prepare(row, batch)
    _, values, alignment, keys, residual = aligner._graph(*prepared)
    all_keys = [alignment, *keys]
    values.update(alignment, values.atPose3(alignment).compose(gtsam.Pose3.Expmap(np.array([.05,-.1,.15,.1,-.2,.3]))))
    jacobians = [None]*len(all_keys)
    residual(None, values, jacobians)
    for index, key in enumerate(all_keys):
        numerical = np.zeros_like(jacobians[index])
        for axis in range(6):
            delta = np.zeros(6)
            delta[axis] = 1e-6
            plus, minus = gtsam.Values(values), gtsam.Values(values)
            plus.update(key, values.atPose3(key).compose(gtsam.Pose3.Expmap(delta)))
            minus.update(key, values.atPose3(key).compose(gtsam.Pose3.Expmap(-delta)))
            numerical[:, axis] = (residual(None, plus, None)-residual(None, minus, None))/2e-6
        np.testing.assert_allclose(jacobians[index], numerical, rtol=1e-5, atol=1e-7)


def test_cli_writes_a_new_research_result(tmp_path):
    config, row, batch = fixture()
    cfg = asdict(config)
    for key in ("imu_T_camera", "map_common_left_covariance"):
        cfg[key] = cfg[key].tolist()
    request = tmp_path / "request.json"
    request.write_text(json.dumps({"schema_version": "vision_nav_correlated_alignment_request_v1",
                                   "config": cfg, "native_record": row, "map_batch": batch}))
    output = tmp_path / "result.json"
    assert main(["--request", str(request), "--output", str(output)]) == 0
    result = json.loads(output.read_text())
    assert not result["position_authority"] and result["request_sha256"]
    with pytest.raises(FileExistsError):
        main(["--request", str(request), "--output", str(output)])
