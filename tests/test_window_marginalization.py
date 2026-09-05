"""Gaussian nuisance elimination versus full graphs and an independent solve."""

from copy import deepcopy
from dataclasses import asdict

import numpy as np
import pytest

gtsam = pytest.importorskip("gtsam")
from vision_nav.estimation.correlated_window import CorrelatedWindowAligner, WindowAlignmentConfig, payload_digest, _sqrt_information
from test_correlated_window import fixture
from test_vio_pose_window import record


def problem(n=8, chosen=(1, 4), seed=41, residual_scale=.01):
    config, _, batch = fixture()
    ext = gtsam.Pose3.Expmap(np.array([.2, -.1, .3, .1, .2, -.1]))
    config = WindowAlignmentConfig(**{**asdict(config), "imu_T_camera": ext.matrix()})
    poses = [gtsam.Pose3.Expmap(np.array([.01*i, -.02*i, .03*i, .2*i, .1*i, .03*i])) for i in range(n)]
    origin = gtsam.Pose3.Expmap(np.array([.2, .3, -.4, 10., 20., 30.]))
    rng = np.random.default_rng(seed)
    d = 6*(n+len(chosen))
    factor = rng.normal(size=(d, d))
    cov = (factor @ factor.T/d+np.eye(d))*.01
    native_jacobian = np.eye(6*n)
    for i, pose in enumerate(poses):
        native_jacobian[6*i+3:6*i+6, 6*i+3:6*i+6] = pose.rotation().matrix()
    row = record([p.matrix() for p in poses], native_jacobian @ cov[:6*n, :6*n] @ native_jacobian.T)
    batch.update(snapshot_sha256=payload_digest(row),
        fixes=[{"fix_id": f"original-{i}", "timestamp_ns": row["pose_window"]["pose_timestamps_ns"][i],
                "map_T_camera": origin.compose(poses[i]).compose(ext).compose(
                    gtsam.Pose3.Expmap(rng.normal(size=6)*residual_scale)).matrix().tolist()} for i in chosen],
        map_joint_covariance=cov[6*n:, 6*n:].tolist(), vio_map_cross_covariance=cov[:6*n, 6*n:].tolist())
    return config, row, batch


@pytest.mark.parametrize("seed", [1, 4, 16, 21])
@pytest.mark.parametrize("chosen", [(0,), (1, 4), (0, 3, 5, 7)])
def test_nonlinear_full_graph_parity_with_cross_correlations(seed, chosen):
    config, row, batch = problem(seed=seed, chosen=chosen)
    original = deepcopy((row, batch))
    solver = CorrelatedWindowAligner(config)
    reduced = solver.solve(row, batch)
    full = solver.solve(row, batch, marginalize_unused_poses=False)
    assert reduced["status"] == full["status"] == "research_alignment"
    assert reduced["reason"] == full["reason"]
    for key in ("map_T_imu", "local_T_nominal_map", "conditional_covariance_right_imu", "covariance_right_imu"):
        np.testing.assert_allclose(reduced[key], full[key], atol=2e-6, rtol=2e-5)
    assert reduced["residual_chi2"] == pytest.approx(full["residual_chi2"], abs=1e-8)
    for key in ("fix_ids", "snapshot_sha256", "map_batch_sha256", "pose_timestamp_ns", "posterior_timestamp_ns",
                "residual_dof", "consistency_limit", "raw_local_T_imu", "map_common_left_covariance"):
        assert reduced[key] == full[key]
    assert reduced["nuisance_elimination"]["optimized_pose_count"] == len(set(chosen) | {7})
    assert reduced["nuisance_elimination"]["complete_joint_model_validated"]
    assert not reduced["position_authority"] and reduced["px4_external_vision"] == "disabled"
    assert (row, batch) == original


def test_principal_covariance_is_not_conditioning_unused_poses_to_zero():
    config, row, batch = problem()
    solver = CorrelatedWindowAligner(config)
    window, indices, _, cov = solver._prepare(row, batch)
    _, remapped, principal, retained, omitted = solver._reduce(window, indices, cov, True)
    np.testing.assert_array_equal(principal, cov[np.ix_(retained, retained)])
    assert remapped == [0, 1]
    wrong = principal-cov[np.ix_(retained, omitted)] @ np.linalg.solve(
        cov[np.ix_(omitted, omitted)], cov[np.ix_(omitted, retained)])
    assert np.linalg.norm(principal-wrong) > .001


def single_fix_covariance(solver, row, batch):
    """Independent finite-difference propagation, without a graph or whitening."""
    window, indices, measurements, cov = solver._prepare(row, batch)
    assert len(indices) == 1 and indices[0] != len(window.timestamps_ns)-1
    poses = [gtsam.Pose3(window.local_T_imu[indices[0]]), gtsam.Pose3(window.local_T_imu[-1]), measurements[0]]
    ext = gtsam.Pose3(solver.config.imu_T_camera)
    def output(ps):
        return ps[2].compose(ext.inverse()).compose(ps[0].between(ps[1]))
    nominal, jacobian = output(poses), np.zeros((6, 18))
    for axis in range(18):
        delta = np.zeros(6)
        delta[axis % 6] = 1e-5
        plus, minus = list(poses), list(poses)
        plus[axis//6] = poses[axis//6].compose(gtsam.Pose3.Expmap(delta))
        minus[axis//6] = poses[axis//6].compose(gtsam.Pose3.Expmap(-delta))
        jacobian[:, axis] = (gtsam.Pose3.Logmap(nominal.between(output(plus)))
                            -gtsam.Pose3.Logmap(nominal.between(output(minus))))/2e-5
    n = len(window.timestamps_ns)
    selected = [6*indices[0]+j for j in range(6)]+list(range(6*(n-1), 6*n))+list(range(6*n, len(cov)))
    return jacobian @ cov[np.ix_(selected, selected)] @ jacobian.T


def test_highly_correlated_window_matches_independent_single_fix_propagation():
    config, row, batch = problem(n=12, chosen=(0,), residual_scale=0)
    config = WindowAlignmentConfig(**{**asdict(config), "map_common_left_covariance": np.zeros((6, 6))})
    rng = np.random.default_rng(215)
    basis, _ = np.linalg.qr(rng.normal(size=(78, 78)))
    cov = (basis*np.geomspace(1e-11, .05, 78)) @ basis.T
    # Use identity rotations to bind the constructed covariance without a tangent conversion.
    row["pose_window"]["local_T_imu"] = [np.eye(4).tolist() for _ in range(12)]
    row["pose_window"]["joint_covariance_native"] = cov[:72, :72].tolist()
    batch.update(snapshot_sha256=payload_digest(row), map_joint_covariance=cov[72:, 72:].tolist(),
        vio_map_cross_covariance=cov[:72, 72:].tolist())
    solver = CorrelatedWindowAligner(config)
    expected = single_fix_covariance(solver, row, batch)
    for enabled in (False, True):
        result = solver.solve(row, batch, marginalize_unused_poses=enabled)
        assert result["status"] == "research_alignment", result
        np.testing.assert_allclose(result["covariance_right_imu"], expected, atol=1e-8, rtol=1e-5)
        assert result["covariance_solver"] == ("analytic_joint_se3_pushforward" if enabled else "whitened_jacobian_qr")


def test_sqrt_information_preserves_highly_correlated_noise():
    rng = np.random.default_rng(515)
    basis, _ = np.linalg.qr(rng.normal(size=(72, 72)))
    cov = (basis*np.geomspace(1e-11, .1, 72)) @ basis.T
    root = _sqrt_information(cov)
    check = root @ np.linalg.cholesky(cov)
    np.testing.assert_allclose(check @ check.T, np.eye(len(cov)), atol=1e-6)
    assert np.all(np.diag(root) > 0)


def test_mean_covariance_and_chi_square_match_independent_linear_gaussian_solve():
    config, _, batch = fixture()
    config = WindowAlignmentConfig(**{**asdict(config), "map_common_left_covariance": np.zeros((6, 6))})
    n, chosen = 5, [0, 2]
    rng = np.random.default_rng(88)
    factor = rng.normal(size=(7, 7))
    noise = (factor @ factor.T/7+np.eye(7))*.1
    p, q, cross = np.eye(6*n)*1e-7, np.eye(12)*1e-7, np.zeros((6*n, 12))
    x = list(range(3, 6*n, 6))
    p[np.ix_(x, x)] = noise[:n, :n]
    q[np.ix_([3, 9], [3, 9])] = noise[n:, n:]
    cross[np.ix_(x, [3, 9])] = noise[:n, n:]
    poses = [gtsam.Pose3(gtsam.Rot3(), np.array([float(i), 0., 0.])).matrix() for i in range(n)]
    row = record(poses, p)
    observations = np.array([0., 1., 2., 3., 4., 10., 12.1])
    for j, i in enumerate(chosen):
        batch["fixes"][j].update(timestamp_ns=row["pose_window"]["pose_timestamps_ns"][i],
            map_T_camera=gtsam.Pose3(gtsam.Rot3(), np.array([observations[n+j], 20., 30.])).matrix().tolist())
    batch.update(snapshot_sha256=payload_digest(row), map_joint_covariance=q.tolist(), vio_map_cross_covariance=cross.tolist())
    h = np.zeros((n+2, n+1))
    h[:n, :n] = np.eye(n)
    for j, i in enumerate(chosen):
        h[n+j, i], h[n+j, -1] = 1, -1
    posterior = np.linalg.inv(h.T @ np.linalg.solve(noise, h))
    mean = posterior @ h.T @ np.linalg.solve(noise, observations)
    out = np.array([0., 0., 0., 0., 1., -1.])
    residual = h @ mean-observations
    result = CorrelatedWindowAligner(config).solve(row, batch)
    assert result["status"] == "research_alignment"
    assert result["map_T_imu"][0][3] == pytest.approx(out @ mean, abs=2e-5)
    assert result["conditional_covariance_right_imu"][3][3] == pytest.approx(out @ posterior @ out, abs=2e-5)
    assert result["residual_chi2"] == pytest.approx(residual @ np.linalg.solve(noise, residual), abs=1e-6)


@pytest.mark.parametrize("fault", ["pose", "local_covariance", "cross_covariance"])
def test_invalid_omitted_blocks_are_still_rejected(fault):
    config, row, batch = problem()
    if fault == "pose":
        row["pose_window"]["local_T_imu"][2][0][0] = 2.
    elif fault == "local_covariance":
        row["pose_window"]["joint_covariance_native"][12][12] = -1.
    else:
        batch["vio_map_cross_covariance"][12][0] = 100.
    batch["snapshot_sha256"] = payload_digest(row)
    with pytest.raises(ValueError):
        CorrelatedWindowAligner(config).solve(row, batch)


def test_wrong_map_remains_withheld_in_both_graphs():
    config, row, batch = problem()
    batch["fixes"][1]["map_T_camera"][0][3] += 100
    solver = CorrelatedWindowAligner(config)
    for enabled in (True, False):
        result = solver.solve(row, batch, marginalize_unused_poses=enabled)
        assert result["status"] == "withheld" and result["map_T_imu"] is None


def test_eliminated_rotation_outside_log_chart_is_withheld():
    config, row, batch = problem(n=3, chosen=(0, 2), residual_scale=0)
    loading = np.zeros(30)
    loading[6], loading[21], loading[27] = 200, .1, -.1
    cov = np.eye(30)*.01+np.outer(loading, loading)
    # Native and right-local rotations have the same sign/convention here.
    row["pose_window"]["joint_covariance_native"] = cov[:18, :18].tolist()
    batch.update(snapshot_sha256=payload_digest(row), map_joint_covariance=cov[18:, 18:].tolist(),
        vio_map_cross_covariance=cov[:18, 18:].tolist())
    fix = gtsam.Pose3(np.array(batch["fixes"][1]["map_T_camera"]))
    batch["fixes"][1]["map_T_camera"] = fix.compose(gtsam.Pose3.Expmap(np.array([0., 0., 0., .2, 0., 0.]))).matrix().tolist()
    result = CorrelatedWindowAligner(config).solve(row, batch)
    assert result["status"] == "withheld", result
    assert result["reason"] == "eliminated_pose_outside_principal_log_chart"
    assert result["nuisance_elimination"]["max_eliminated_rotation_rad"] > np.pi


@pytest.mark.parametrize("count", [32, 36])
def test_maximum_window_keeps_original_identity_and_latest_pose(count):
    config, row, batch = problem(n=count, chosen=(0, 2), residual_scale=0)
    if count == 36:
        window = row["pose_window"]
        window.update(schema_version="vision_nav_openvins_pose_window_v2", passive_anchor_capacity=4,
            retention_policy="native_passive_stochastic_clones_explicit_release",
            passive_anchor_timestamps_ns=window["pose_timestamps_ns"][:4],
            normal_clone_timestamps_ns=window["pose_timestamps_ns"][4:])
        batch["snapshot_sha256"] = payload_digest(row)
    result = CorrelatedWindowAligner(config).solve(row, batch)
    assert result["status"] == "research_alignment"
    assert result["nuisance_elimination"]["optimized_pose_count"] == 3
    assert result["nuisance_elimination"]["marginalized_pose_count"] == count-3
    assert result["pose_timestamp_ns"] == row["timestamp_ns"]
    assert result["snapshot_sha256"] == payload_digest(row)


def test_all_observed_poses_need_no_reduction():
    config, row, batch = fixture()
    solver = CorrelatedWindowAligner(config)
    assert solver.solve(row, batch) == solver.solve(row, batch, marginalize_unused_poses=False)


@pytest.mark.parametrize("value", [None, 0, 1, "false"])
def test_reduction_flag_is_not_truthiness(value):
    config, row, batch = fixture()
    with pytest.raises(ValueError):
        CorrelatedWindowAligner(config).solve(row, batch, marginalize_unused_poses=value)
