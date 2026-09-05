"""Connected PnP/noise/VIO replay contracts with synthetic map observations."""

import copy
import json

import numpy as np
import pytest

from vision_nav.estimation.correlated_window import payload_digest
from vision_nav.estimation.pnp_window_alignment import align, main
from vision_nav.estimation.reprojection_uncertainty import projection_jacobians
from vision_nav.neural.shadow_geometry import verify_pnp_correspondences
from test_reprojection_uncertainty import fixture as pnp_fixture
from test_vio_pose_window import record


def fixture(native=None, shared=True, vio_cross=True):
    native = record() if native is None else native
    proto, anchor, points, _, matrix = pnp_fixture()
    local_poses = native["pose_window"]["local_T_imu"]
    times = native["pose_window"]["pose_timestamps_ns"]
    # Two exact retained clones, each fitted against the same synthetic landmarks.
    chosen = [0, len(times)-1]
    requests, expected = [], []
    first = np.array(local_poses[0])
    map_T_local = anchor @ np.linalg.inv(first)
    for i in chosen:
        req = copy.deepcopy(proto)
        pose = map_T_local @ np.array(local_poses[i])
        pixels = projection_jacobians(pose, points, matrix)[0]
        n = len(points)
        geometry = verify_pnp_correspondences(query_keypoints=pixels, map_points_enu_m=points,
            match_pairs=np.c_[np.arange(n), np.arange(n)], scores=np.ones(n), camera_matrix=matrix,
            query_width=320, query_height=240, minimum_score=.5, min_inliers=10, min_inlier_ratio=.8,
            max_reprojection_error_px=1., min_grid_coverage=.1)
        assert geometry["status"] == "accepted"
        req["attempt"].update(geometry=geometry, matched_query_pixels=pixels.tolist())
        req["uncertainty"]["attempt_sha256"] = payload_digest(req["attempt"])
        req["context"]["timestamp_ns"] = times[i]
        requests.append(req)
        expected.append(pose)
    d = len(proto["uncertainty"]["joint_measurement_covariance"])
    joint = np.zeros((2*d, 2*d))
    for i, req in enumerate(requests):
        joint[i*d:(i+1)*d, i*d:(i+1)*d] = req["uncertainty"]["joint_measurement_covariance"]
    loading = np.zeros((2*d, 3))
    for i in range(2):
        for j in range(n):
            loading[i*d+2*n+3*j:i*d+2*n+3*j+3] = np.diag(np.sqrt([.04, .05, .06]))
    if shared:
        joint += loading @ loading.T
    for i, req in enumerate(requests):
        req["uncertainty"].update(joint_measurement_covariance=joint[i*d:(i+1)*d, i*d:(i+1)*d].tolist(),
            noise_model_description="Synthetic pixel noise and declared shared map translation; fixed K")
    config = {k: proto["context"][k] for k in ("map_release_id", "model_release_id", "calibration_id", "clock_id", "uncertainty_model_id")}
    config.update(imu_T_camera=np.eye(4).tolist(), map_common_left_covariance=np.zeros((6, 6)).tolist())
    cross = np.zeros((6*len(times), 2*d))
    if vio_cross:
        cross[0, 0] = .001
        cross[0, d] = .001
    out = {"schema_version": "vision_nav_joint_pnp_alignment_request_v1", "native_record": native,
           "config": config, "fixes": [{"fix_id": f"frame-{i}", "request": req} for i, req in enumerate(requests)],
           "joint_uncertainty": {"snapshot_sha256": payload_digest(native),
              "pnp_request_sha256s": [payload_digest(req) for req in requests],
              "error_convention": "estimated_vio_right_pose_and_additive_pnp_inputs",
              "measurement_order": "concatenated_pnp_request_input_vectors",
              "map_error_ownership": "joint_measurement_covariance_only_no_extra_floor",
              "snapshot_policy": "replace_do_not_accumulate",
              "cross_covariance_model": "Synthetic shared map shift and explicitly declared VIO/pixel correlation",
              "joint_measurement_covariance": joint.tolist(), "vio_input_cross_covariance": cross.tolist()}}
    return out, expected[-1]


def rebind(request):
    for item in request["fixes"]:
        item["request"]["uncertainty"]["attempt_sha256"] = payload_digest(item["request"]["attempt"])
    request["joint_uncertainty"]["pnp_request_sha256s"] = [payload_digest(i["request"]) for i in request["fixes"]]


def test_connected_pnp_joint_uncertainty_and_alignment():
    request, expected = fixture()
    before = copy.deepcopy(request)
    result = align(request)
    assert result["status"] == "research_alignment"
    assert result == align(request) and request == before
    np.testing.assert_allclose(result["alignment"]["map_T_imu"], expected, atol=1e-6)
    assert result["navigation_mode"] == "NO_GLOBAL_FIX" and not result["position_authority"]
    assert result["px4_external_vision"] == "disabled" and not result["covariance_calibrated"]
    q = np.array(result["map_batch"]["map_joint_covariance"])
    assert np.linalg.norm(q[:6, 6:]) > 0
    c = np.array(result["map_batch"]["vio_map_cross_covariance"])
    assert np.linalg.norm(c) > 0
    s = np.zeros((12, 168))
    for i, pnp in enumerate(result["pnp_results"]):
        s[6*i:6*i+6, 84*i:84*i+84] = pnp["pose_input_sensitivity"]
    np.testing.assert_allclose(q, s @ request["joint_uncertainty"]["joint_measurement_covariance"] @ s.T, atol=1e-12)
    np.testing.assert_allclose(c, np.array(request["joint_uncertainty"]["vio_input_cross_covariance"]) @ s.T, atol=1e-12)
    # The separate map floor is zero because this error is already inside Q.
    np.testing.assert_array_equal(result["alignment"]["map_common_left_covariance"], np.zeros((6, 6)))
    np.testing.assert_allclose(result["alignment"]["covariance_right_imu"], result["alignment"]["conditional_covariance_right_imu"])


def test_common_map_error_reaches_output_exactly_once():
    request, expected = fixture(shared=False, vio_cross=False)
    baseline = align(request)
    request, _ = fixture(shared=True, vio_cross=False)
    result = align(request)
    difference = np.array(result["alignment"]["covariance_right_imu"])-baseline["alignment"]["covariance_right_imu"]
    common = np.zeros((6, 6))
    common[3:, 3:] = expected[:3, :3].T @ np.diag([.04, .05, .06]) @ expected[:3, :3]
    np.testing.assert_allclose(difference, common, atol=1e-7)


@pytest.mark.parametrize("kind", ["snapshot", "reordered", "duplicate", "time", "calibration", "floor", "missing_cross",
                                   "diagonal", "indefinite", "scope", "convention"])
def test_invalid_joint_models_are_not_silently_repaired(kind):
    req, _ = fixture()
    if kind == "snapshot":
        req["joint_uncertainty"]["snapshot_sha256"] = "wrong"
    elif kind == "reordered":
        req["fixes"].reverse()
    elif kind == "duplicate":
        req["fixes"][1]["fix_id"] = req["fixes"][0]["fix_id"]
    elif kind == "time":
        req["fixes"][1]["request"]["context"]["timestamp_ns"] += 1
        rebind(req)
    elif kind == "calibration":
        req["fixes"][1]["request"]["context"]["calibration_id"] = "other"
        rebind(req)
    elif kind == "floor":
        req["config"]["map_common_left_covariance"][3][3] = .1
    elif kind == "missing_cross":
        req["joint_uncertainty"].pop("vio_input_cross_covariance")
    elif kind == "diagonal":
        req["joint_uncertainty"]["joint_measurement_covariance"][0][0] += .1
    elif kind == "indefinite":
        req["joint_uncertainty"]["vio_input_cross_covariance"][0][0] = 1.
    elif kind == "scope":
        req["fixes"][1]["request"]["uncertainty"]["scope"] = "unvalidated_research_model"
        rebind(req)
    else:
        req["joint_uncertainty"]["error_convention"] = "unspecified"
    with pytest.raises((ValueError, KeyError)):
        align(req)


def test_failed_pnp_uncertainty_withholds_whole_set():
    req, _ = fixture()
    req["fixes"][1]["request"]["attempt"]["geometry"]["camera_position_enu_m"][0] += .1
    rebind(req)
    result = align(req)
    assert result["reason"] == "incomplete_conditional_pnp_uncertainty"
    assert result["alignment"] is None and result["map_batch"] is None
    assert len(result["pnp_results"]) == 2


def test_individually_valid_wrong_location_is_rejected_by_joint_motion():
    req, _ = fixture()
    attempt = req["fixes"][1]["request"]["attempt"]
    # Translating the complete second map keeps its PnP residuals unchanged,
    # but contradicts the VIO motion between these two camera observations.
    points = np.array(attempt["matched_map_points_enu_m"])
    points[:, 0] += 30.
    attempt["matched_map_points_enu_m"] = points.tolist()
    attempt["geometry"]["camera_position_enu_m"][0] += 30.
    rebind(req)
    result = align(req)
    assert all(p["status"] == "conditional_research_covariance" for p in result["pnp_results"])
    assert result["status"] == "withheld"
    assert result["reason"] == "inconsistent_joint_map_vio_batch"
    assert result["alignment"]["map_T_imu"] is None
    assert not result["position_authority"]


def test_cli_creates_bound_replay_evidence_without_overwriting(tmp_path):
    req, _ = fixture()
    source, output = tmp_path/"request.json", tmp_path/"result.json"
    source.write_text(json.dumps(req))
    assert main(["--request", str(source), "--output", str(output)]) == 0
    result = json.loads(output.read_text())
    assert result["request_sha256"] == payload_digest(req)
    with pytest.raises(FileExistsError):
        main(["--request", str(source), "--output", str(output)])
