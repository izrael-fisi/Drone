"""Analytic and nonlinear synthetic checks, not field covariance calibration."""

import copy
import json

import cv2
import gtsam
import numpy as np
import pytest

from vision_nav.estimation.correlated_window import payload_digest
from vision_nav.estimation.reprojection_uncertainty import assess, main, projection_jacobians
from vision_nav.neural.shadow_geometry import verify_pnp_correspondences


def fixture():
    pose = gtsam.Pose3.Expmap(np.array([.15, -.2, .3, 3., 5., 2.])).matrix()
    local = np.array([[x, y, 10+.1*x*y] for y in [-3., -1., 1., 3.] for x in [-3., -1., 1., 3.]])
    points = local @ pose[:3, :3].T + pose[:3, 3]
    matrix = np.array([[300., 0., 159.5], [0., 310., 119.5], [0., 0., 1.]])
    pixels = projection_jacobians(pose, points, matrix)[0]
    n = len(points)
    geometry = verify_pnp_correspondences(query_keypoints=pixels, map_points_enu_m=points,
        match_pairs=np.c_[np.arange(n), np.arange(n)], scores=np.ones(n), camera_matrix=matrix,
        query_width=320, query_height=240, minimum_score=.5, min_inliers=10, min_inlier_ratio=.8,
        max_reprojection_error_px=1., min_grid_coverage=.2)
    assert geometry["status"] == "accepted"
    attempt = {"geometry": geometry, "position_authority": False, "matched_query_pixels": pixels.tolist(),
        "matched_map_points_enu_m": points.tolist(), "calibrated_camera_matrix": matrix.tolist()}
    cov = np.zeros((5*n+4, 5*n+4))
    cov[:2*n, :2*n] = np.eye(2*n)*.04
    request = {"schema_version": "vision_nav_pnp_uncertainty_request_v1", "attempt": attempt,
        "context": {"map_release_id": "map@1", "model_release_id": "xfeat@1", "calibration_id": "cam@1",
                    "clock_id": "camera@1", "uncertainty_model_id": "synthetic@1", "timestamp_ns": 1_000_000_000},
        "uncertainty": {"attempt_sha256": payload_digest(attempt),
            "measurement_order": "all_query_uv_then_all_map_xyz_then_fx_fy_cx_cy",
            "correspondences": "fixed_correct_inliers_conditional",
            "map_systematics": "included_in_joint_measurement_covariance",
            "rectification_error": "included_in_joint_measurement_covariance",
            "timing_error": "not_modeled", "camera_extrinsic_error": "not_modeled",
            "scope": "synthetic_contract_test", "noise_model_description": "Independent synthetic 0.2 pixel error; fixed K and map",
            "joint_measurement_covariance": cov.tolist()}}
    return request, pose, points, pixels, matrix


def rebind(request):
    request["uncertainty"]["attempt_sha256"] = payload_digest(request["attempt"])


def test_actual_pnp_inlier_identity_and_shadow_covariance():
    request, pose, points, pixels, matrix = fixture()
    before = copy.deepcopy(request)
    result = assess(request)
    assert result["status"] == "conditional_research_covariance"
    assert result == assess(request) and request == before
    assert result["publication_covariance"] is None and result["vio_cross_covariance"] is None
    assert result["navigation_mode"] == "NO_GLOBAL_FIX" and not result["position_authority"]
    assert not result["covariance_calibrated"] and result["px4_external_vision"] == "disabled"
    np.testing.assert_allclose(result["map_T_camera"], pose, atol=2e-6)
    h = projection_jacobians(result["map_T_camera"], points, matrix)[1]
    np.testing.assert_allclose(result["covariance_right_camera"], .04*np.linalg.inv(h.T @ h), rtol=2e-6, atol=1e-10)


def test_projection_jacobians_match_right_se3_and_input_finite_differences():
    _, pose, points, _, matrix = fixture()
    prediction, h, hm, hk = projection_jacobians(pose, points, matrix)
    eps = 1e-6
    numerical = []
    for i in range(6):
        delta = np.eye(6)[i]*eps
        plus = projection_jacobians(pose @ gtsam.Pose3.Expmap(delta).matrix(), points, matrix)[0]
        minus = projection_jacobians(pose @ gtsam.Pose3.Expmap(-delta).matrix(), points, matrix)[0]
        numerical.append(((plus-minus)/(2*eps)).reshape(-1))
    np.testing.assert_allclose(h, np.array(numerical).T, atol=1e-7)
    for axis in range(3):
        shift = np.eye(3)[axis]*eps
        plus = projection_jacobians(pose, points+shift, matrix)[0]
        minus = projection_jacobians(pose, points-shift, matrix)[0]
        np.testing.assert_allclose(hm[:, :, axis], (plus-minus)/(2*eps), atol=1e-7)
    for i, (row, col) in enumerate([(0, 0), (1, 1), (0, 2), (1, 2)]):
        plus_k, minus_k = matrix.copy(), matrix.copy()
        plus_k[row, col] += eps
        minus_k[row, col] -= eps
        plus = projection_jacobians(pose, points, plus_k)[0]
        minus = projection_jacobians(pose, points, minus_k)[0]
        np.testing.assert_allclose(hk[:, i], ((plus-minus)/(2*eps)).reshape(-1), atol=1e-7)


def test_shared_map_translation_does_not_average_away():
    request, _, points, _, matrix = fixture()
    baseline = assess(request)
    n = len(points)
    cov = np.array(request["uncertainty"]["joint_measurement_covariance"])
    shared = np.diag([.4, .5, .6])
    cov[2*n:5*n, 2*n:5*n] = np.kron(np.ones((n, n)), shared)
    request["uncertainty"]["joint_measurement_covariance"] = cov.tolist()
    result = assess(request)
    difference = np.array(result["covariance_right_camera"])-baseline["covariance_right_camera"]
    rotation = np.array(result["map_T_camera"])[:3, :3]
    expected = np.zeros((6, 6))
    expected[3:, 3:] = rotation.T @ shared @ rotation
    np.testing.assert_allclose(difference, expected, atol=1e-10)


def test_cross_measurement_terms_are_not_discarded():
    request, _, points, _, _ = fixture()
    n = len(points)
    cov = np.array(request["uncertainty"]["joint_measurement_covariance"])
    common = np.zeros(5*n+4)
    common[:2*n:2] = .4
    common[-2] = .4
    with_cross = cov+np.outer(common, common)
    request["uncertainty"]["joint_measurement_covariance"] = with_cross.tolist()
    # Common image-u and principal-point-x shifts cancel in the residual.
    result = assess(request)
    request["uncertainty"]["joint_measurement_covariance"] = cov.tolist()
    baseline = assess(request)
    np.testing.assert_allclose(result["covariance_right_camera"], baseline["covariance_right_camera"], atol=1e-10)
    without_cross = with_cross.copy()
    without_cross[:2*n, -4:] = 0
    without_cross[-4:, :2*n] = 0
    request["uncertainty"]["joint_measurement_covariance"] = without_cross.tolist()
    independent = assess(request)
    sensitivity = np.array(result["pose_input_sensitivity"])[:, -2]
    expected_increase = 2*.4**2*np.outer(sensitivity, sensitivity)
    np.testing.assert_allclose(np.array(independent["covariance_right_camera"])-result["covariance_right_camera"],
                               expected_increase, atol=1e-10)


def test_nonlinear_monte_carlo_matches_joint_noise_propagation():
    request, pose, points, pixels, matrix = fixture()
    n = len(points)
    # Small independent pixel/point error plus shared focal length and map shift.
    rng = np.random.default_rng(81)
    loading = np.zeros((5*n+4, 5*n+4+4))
    loading[:2*n, :2*n] = np.eye(2*n)*.03
    loading[2*n:5*n, 2*n:5*n] = np.eye(3*n)*.0005
    loading[-4, -4] = .03
    loading[-3, -4] = .03
    for i in range(n):
        loading[2*n+3*i:2*n+3*i+3, -3:] = np.eye(3)*.003
    cov = loading @ loading.T
    request["uncertainty"]["joint_measurement_covariance"] = cov.tolist()
    result = assess(request)
    assert result["status"] == "conditional_research_covariance"
    rvec = cv2.Rodrigues(pose[:3, :3].T)[0]
    tvec = -pose[:3, :3].T @ pose[:3, 3]
    errors = []
    for _ in range(1600):
        noise = loading @ rng.normal(size=loading.shape[1])
        k = matrix.copy()
        k[0, 0], k[1, 1], k[0, 2], k[1, 2] = matrix[0, 0]+noise[-4], matrix[1, 1]+noise[-3], matrix[0, 2]+noise[-2], matrix[1, 2]+noise[-1]
        r, t = cv2.solvePnPRefineLM(points+noise[2*n:5*n].reshape(n, 3),
            pixels+noise[:2*n].reshape(n, 2), k, None, rvec.copy(), tvec.copy())
        trial = np.eye(4)
        trial[:3, :3] = cv2.Rodrigues(r)[0].T
        trial[:3, 3] = -trial[:3, :3] @ t.reshape(3)
        errors.append(gtsam.Pose3.Logmap(gtsam.Pose3(pose).between(gtsam.Pose3(trial))))
    measured = np.cov(np.array(errors).T)
    predicted = np.array(result["covariance_right_camera"])
    np.testing.assert_allclose(np.diag(measured), np.diag(predicted), rtol=.13)
    assert np.linalg.norm(measured-predicted)/np.linalg.norm(predicted) < .1


@pytest.mark.parametrize("kind", ["digest", "missing_noise", "negative_noise", "duplicate_inlier", "wrong_solver", "scope", "assumptions"])
def test_invalid_contract_rejected(kind):
    request, *_ = fixture()
    if kind == "digest":
        request["uncertainty"]["attempt_sha256"] = "wrong"
    elif kind == "missing_noise":
        request["uncertainty"].pop("joint_measurement_covariance")
    elif kind == "negative_noise":
        request["uncertainty"]["joint_measurement_covariance"][0][0] = -1
    elif kind == "duplicate_inlier":
        request["attempt"]["geometry"]["inlier_match_indices"][0] = request["attempt"]["geometry"]["inlier_match_indices"][1]
        rebind(request)
    elif kind == "wrong_solver":
        request["attempt"]["geometry"]["pose_solver"] = "weighted_other_solver"
        rebind(request)
    elif kind == "scope":
        request["uncertainty"]["scope"] = "flight_validated"
    else:
        request["uncertainty"]["timing_error"] = "guessed_zero"
    with pytest.raises((ValueError, KeyError)):
        assess(request)


def test_nonstationary_pose_and_unobservable_geometry_withheld():
    request, *_ = fixture()
    request["attempt"]["geometry"]["camera_position_enu_m"][0] += .1
    rebind(request)
    assert assess(request)["reason"] == "pose_not_stationary_for_declared_inliers"
    request, pose, points, _, matrix = fixture()
    line = np.c_[np.linspace(-3, 3, len(points)), np.zeros(len(points)), np.full(len(points), 10)]
    points = line @ pose[:3, :3].T+pose[:3, 3]
    request["attempt"]["matched_map_points_enu_m"] = points.tolist()
    request["attempt"]["matched_query_pixels"] = projection_jacobians(pose, points, matrix)[0].tolist()
    rebind(request)
    assert assess(request)["status"] == "withheld"


def test_inlier_indices_refer_to_original_pairs_after_score_filtering():
    request, _, points, pixels, matrix = fixture()
    # One scored-out pair followed by shuffled valid matches and one outlier.
    order = np.arange(len(points))[::-1]
    query = np.vstack((pixels, [10, 10]))
    pairs = np.vstack(([0, 0], np.c_[order, order], [len(points), 0]))
    scores = np.r_[0., np.ones(len(points)+1)]
    result = verify_pnp_correspondences(query_keypoints=query, map_points_enu_m=points,
        match_pairs=pairs, scores=scores, camera_matrix=matrix, query_width=320, query_height=240,
        minimum_score=.5, min_inliers=10, min_inlier_ratio=.8, max_reprojection_error_px=1., min_grid_coverage=.2)
    assert result["status"] == "accepted"
    assert set(result["inlier_match_indices"]) == set(range(1, len(points)+1))


def test_cli_writes_new_evidence_only(tmp_path):
    request, *_ = fixture()
    source, target = tmp_path/"request.json", tmp_path/"result.json"
    source.write_text(json.dumps(request))
    assert main(["--request", str(source), "--output", str(target)]) == 0
    result = json.loads(target.read_text())
    assert result["request_sha256"] == payload_digest(request)
    with pytest.raises(FileExistsError):
        main(["--request", str(source), "--output", str(target)])


def test_excluded_match_has_zero_sensitivity_even_with_large_noise():
    request, _, points, _, _ = fixture()
    baseline = assess(request)
    attempt = request["attempt"]
    attempt["matched_query_pixels"].insert(0, [0., 0.])
    attempt["matched_map_points_enu_m"].insert(0, [1., 2., 3.])
    attempt["geometry"]["inlier_match_indices"] = [i+1 for i in attempt["geometry"]["inlier_match_indices"]]
    n = len(points)+1
    cov = np.zeros((5*n+4, 5*n+4))
    cov[:2*n, :2*n] = np.eye(2*n)*.04
    cov[:2, :2] = np.eye(2)*1e6
    cov[2*n:2*n+3, 2*n:2*n+3] = np.eye(3)*1e6
    request["uncertainty"]["joint_measurement_covariance"] = cov.tolist()
    rebind(request)
    result = assess(request)
    np.testing.assert_allclose(result["covariance_right_camera"], baseline["covariance_right_camera"], atol=1e-12)
    sensitivity = np.array(result["pose_input_sensitivity"])
    assert not sensitivity[:, :2].any() and not sensitivity[:, 2*n:2*n+3].any()


def test_zero_uncertainty_model_is_withheld():
    request, *_ = fixture()
    cov = np.array(request["uncertainty"]["joint_measurement_covariance"])
    request["uncertainty"]["joint_measurement_covariance"] = np.zeros_like(cov).tolist()
    result = assess(request)
    assert result["reason"] == "noise_model_leaves_zero_pose_uncertainty"
    assert result["covariance_right_camera"] is None


@pytest.mark.parametrize("kind", ["skew", "focal", "behind_camera", "missing_provenance"])
def test_unsupported_camera_or_context_is_rejected(kind):
    request, *_ = fixture()
    if kind == "skew":
        request["attempt"]["calibrated_camera_matrix"][0][1] = 1.
    elif kind == "focal":
        request["attempt"]["calibrated_camera_matrix"][0][0] = -1.
    elif kind == "behind_camera":
        request["attempt"]["geometry"]["camera_position_enu_m"][2] += 100.
    else:
        request["context"]["calibration_id"] = ""
    rebind(request)
    with pytest.raises(ValueError):
        assess(request)
