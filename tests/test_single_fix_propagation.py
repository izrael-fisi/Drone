"""Exact delayed-anchor propagation versus full graphs and independent SE(3)."""

from copy import deepcopy
from dataclasses import asdict

import numpy as np
import pytest

gtsam = pytest.importorskip("gtsam")
from vision_nav.estimation.correlated_window import CorrelatedWindowAligner, WindowAlignmentConfig, payload_digest
from test_window_marginalization import problem, single_fix_covariance


@pytest.mark.parametrize("seed", [9, 22, 55])
@pytest.mark.parametrize("n,anchor", [(1, 0), (8, 0), (8, 3), (8, 7), (32, 0)])
def test_closed_form_equals_full_graph_with_correlated_noise(seed, n, anchor):
    config, row, batch = problem(n=n, chosen=(anchor,), seed=seed, residual_scale=.05)
    before = deepcopy((row, batch))
    solver = CorrelatedWindowAligner(config)
    direct = solver.solve(row, batch)
    graph = solver.solve(row, batch, marginalize_unused_poses=False)
    assert direct["status"] == graph["status"] == "research_alignment"
    for key in ("map_T_imu", "local_T_nominal_map", "conditional_covariance_right_imu", "covariance_right_imu"):
        np.testing.assert_allclose(direct[key], graph[key], atol=1e-8, rtol=2e-6)
    for key in ("fix_ids", "snapshot_sha256", "map_batch_sha256", "pose_timestamp_ns", "posterior_timestamp_ns",
                "residual_dof", "consistency_test", "consistency_limit", "raw_local_T_imu", "map_common_left_covariance"):
        assert direct[key] == graph[key]
    if anchor != n-1:
        np.testing.assert_allclose(direct["conditional_covariance_right_imu"],
            single_fix_covariance(solver, row, batch), atol=1e-8, rtol=2e-6)
    assert direct["solution_method"] == "single_fix_closed_form"
    assert direct["optimizer_iterations"] == 0
    assert direct["residual_chi2"] == 0 and direct["confidence"] is None
    assert not direct["position_authority"] and direct["navigation_mode"] == "NO_GLOBAL_FIX"
    assert direct["px4_external_vision"] == "disabled" and not direct["covariance_calibrated"]
    assert before == (row, batch)


def test_current_anchor_cancels_vio_noise_exactly():
    config, row, batch = problem(n=5, chosen=(4,))
    solver = CorrelatedWindowAligner(config)
    result = solver.solve(row, batch)
    ext = gtsam.Pose3(config.imu_T_camera)
    expected = ext.AdjointMap() @ np.array(batch["map_joint_covariance"]) @ ext.AdjointMap().T
    np.testing.assert_allclose(result["conditional_covariance_right_imu"], expected, atol=1e-12)
    expected_pose = gtsam.Pose3(np.array(batch["fixes"][0]["map_T_camera"])).compose(ext.inverse())
    np.testing.assert_allclose(result["map_T_imu"], expected_pose.matrix(), atol=1e-12)


def test_uses_cross_covariance_not_independent_pose_difference():
    config, row, batch = problem(n=5, chosen=(0,))
    solver = CorrelatedWindowAligner(config)
    correlated = solver.solve(row, batch)
    independent = deepcopy(batch)
    independent["vio_map_cross_covariance"] = np.zeros((30, 6)).tolist()
    wrong = solver.solve(row, independent)
    assert np.linalg.norm(np.array(correlated["covariance_right_imu"])-wrong["covariance_right_imu"]) > 1e-4


def test_single_path_has_no_optimizer_but_multiple_fixes_still_do(monkeypatch):
    config, row, batch = problem(chosen=(0,))
    solver = CorrelatedWindowAligner(config)
    def forbidden(*args):
        raise AssertionError("graph invoked")
    monkeypatch.setattr(solver, "_graph", forbidden)
    assert solver.solve(row, batch)["status"] == "research_alignment"
    with pytest.raises(AssertionError, match="graph invoked"):
        solver.solve(row, batch, marginalize_unused_poses=False)
    _, row, batch = problem(chosen=(0, 2))
    with pytest.raises(AssertionError, match="graph invoked"):
        solver.solve(row, batch)


@pytest.mark.parametrize("fault", ["omitted_covariance", "omitted_pose", "cross", "binding", "missing_time"])
def test_single_path_keeps_complete_input_validation(fault):
    config, row, batch = problem(chosen=(0,))
    if fault == "omitted_covariance":
        row["pose_window"]["joint_covariance_native"][12][12] = -1
    elif fault == "omitted_pose":
        row["pose_window"]["local_T_imu"][2][0][0] = 5
    elif fault == "cross":
        batch["vio_map_cross_covariance"][12][0] = 100
    elif fault == "missing_time":
        batch["fixes"][0]["timestamp_ns"] += 1
    batch["snapshot_sha256"] = "0"*64 if fault == "binding" else payload_digest(row)
    with pytest.raises(ValueError):
        CorrelatedWindowAligner(config).solve(row, batch)


def test_large_motion_extrinsic_and_common_map_floor():
    config, row, batch = problem(n=8, chosen=(0,))
    ext = gtsam.Pose3.Expmap(np.array([1.3, -.6, .4, 2., 1., -3.]))
    config = WindowAlignmentConfig(**{**asdict(config), "imu_T_camera": ext.matrix(),
        "map_common_left_covariance": np.diag([.01, .02, .03, 4., 5., 6.])})
    solver = CorrelatedWindowAligner(config)
    result = solver.solve(row, batch)
    expected = single_fix_covariance(solver, row, batch)
    np.testing.assert_allclose(result["conditional_covariance_right_imu"], expected, atol=1e-7)
    adj = gtsam.Pose3(np.array(result["map_T_imu"])).inverse().AdjointMap()
    np.testing.assert_allclose(result["covariance_right_imu"], expected+adj @ config.map_common_left_covariance @ adj.T, atol=1e-7)


def test_single_fix_does_not_claim_to_detect_wrong_global_anchor():
    config, row, batch = problem(chosen=(0,))
    batch["fixes"][0]["map_T_camera"][0][3] += 1000
    result = CorrelatedWindowAligner(config).solve(row, batch)
    assert result["status"] == "research_alignment"
    assert result["consistency_test"] == "untestable_single_map_fix"
    assert result["confidence"] is None and not result["position_authority"]
