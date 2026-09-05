"""Offline conversion/gate tests, with no vehicle or MAVLink connection."""

import asyncio
from dataclasses import replace

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from vision_nav.contracts import CompatibilityReport
from vision_nav.px4.bridge import Px4PublicationPolicy, Px4VisionBridge
from vision_nav.types import NavEstimate, NavigationMode, Pose3


def estimate():
    return NavEstimate(
        10., Pose3(np.array([2., 3., 4.]), np.array([0., 0., 0., 1.])),
        np.zeros(3), np.eye(6), .9, NavigationMode.TRACKING, "tile",
        calibration_id="camera@1", map_version="map@1", model_version="model@1",
        body_frame="FRD", covariance_convention="position_then_left_rotation_world",
        clock_domain="host_monotonic",
    )


COMPATIBLE = CompatibilityReport(True, camera_calibration_id="camera@1",
                                 map_version="map@1", model_version="model@1")
NED_FROM_ENU = np.array([[0., 1., 0.], [1., 0., 0.], [0., 0., -1.]])


def decision(nav, now=10.1, compatibility=COMPATIBLE):
    return Px4VisionBridge().publication_decision(
        nav, compatibility=compatibility, transport_healthy=True, now_s=now)


@pytest.mark.parametrize("angles", [[0, 0, 0], [10, 20, 30], [180, 0, 90],
                                   [-45, 89, 175], [70, -80, -160]])
def test_orientation_rotates_world_not_just_quaternion_storage(angles):
    rotation = Rotation.from_euler("xyz", angles, degrees=True)
    nav = replace(estimate(), pose_map_body=Pose3(np.array([2., 3., 4.]), rotation.as_quat()))
    msg = Px4VisionBridge().to_message(nav)
    actual = Rotation.from_quat(msg.quaternion_wxyz[[1, 2, 3, 0]]).as_matrix()
    np.testing.assert_allclose(actual, NED_FROM_ENU @ rotation.as_matrix(), atol=1e-14)
    np.testing.assert_allclose(msg.position_ned_m, [3, 2, -4])
    assert msg.frame_id == "NED"
    assert msg.body_frame == "FRD"


def test_cross_covariance_and_perturbations_use_same_world_rotation():
    rng = np.random.default_rng(74)
    matrix = rng.normal(size=(6, 6))
    covariance = matrix @ matrix.T
    nav = replace(estimate(), covariance=covariance)
    msg = Px4VisionBridge().to_message(nav)
    order = [1, 0, 2, 4, 3, 5]
    signs = np.array([1, 1, -1, 1, 1, -1])
    expected = covariance[np.ix_(order, order)] * np.outer(signs, signs)
    np.testing.assert_allclose(msg.pose_covariance, expected, atol=1e-14)
    assert msg.pose_covariance.shape == (6, 6)
    # Independently verify the left rotation perturbation, not Euler RPY errors.
    delta = np.array([.001, -.002, .003])
    r = Rotation.from_euler("xyz", [20, 30, 40], degrees=True).as_matrix()
    changed = NED_FROM_ENU @ Rotation.from_rotvec(delta).as_matrix() @ r
    converted = Rotation.from_rotvec(NED_FROM_ENU @ delta).as_matrix() @ NED_FROM_ENU @ r
    np.testing.assert_allclose(changed, converted, atol=1e-14)
    np.testing.assert_allclose(np.linalg.eigvalsh(msg.pose_covariance),
                               np.linalg.eigvalsh(covariance), atol=1e-13)


@pytest.mark.parametrize("timestamp,reason", [(float("nan"), "invalid_monotonic_timestamp"),
    (float("inf"), "invalid_monotonic_timestamp"), (0., "invalid_monotonic_timestamp"),
    (-1., "invalid_monotonic_timestamp"), (10.2, "estimate_in_future"), (9., "estimate_stale")])
def test_invalid_future_stale_timestamps_withhold(timestamp, reason):
    assert decision(replace(estimate(), timestamp_s=timestamp)).reason == reason


@pytest.mark.parametrize("now", [float("nan"), float("inf"), 0., -1.])
def test_current_clock_must_be_valid(now):
    assert decision(estimate(), now).reason == "invalid_current_timestamp"


@pytest.mark.parametrize("field,value,reason", [
    ("body_frame", None, "unsupported_body_frame"),
    ("body_frame", "FLU", "unsupported_body_frame"),
    ("covariance_convention", "right_se3", "unsupported_covariance_convention"),
    ("clock_domain", "px4_boot", "unsupported_clock_domain"),
    ("diagnostics", {"publication_withheld": True}, "estimator_publication_withheld"),
    ("diagnostics", {"position_authority": False}, "estimator_publication_withheld"),
])
def test_undeclared_conventions_and_estimator_veto_fail_closed(field, value, reason):
    nav = replace(estimate(), **{field: value})
    assert decision(nav).reason == reason
    assert Px4VisionBridge().to_message(nav) is None


@pytest.mark.parametrize("field", ["map_version", "model_version", "calibration_id"])
@pytest.mark.parametrize("value", [None, "wrong-release"])
def test_compatibility_report_is_bound_to_this_estimate(field, value):
    assert decision(replace(estimate(), **{field: value})).reason == "estimate_compatibility_binding_mismatch"


@pytest.mark.parametrize("kind", ["nonfinite", "asymmetric", "indefinite", "negative", "wrong_shape"])
def test_invalid_covariance_withholds(kind):
    covariance = np.eye(6)
    if kind == "nonfinite":
        covariance[0, 1] = np.nan
    elif kind == "asymmetric":
        covariance[0, 1] = .5
    elif kind == "indefinite":
        covariance[0, 1] = covariance[1, 0] = 2.
    elif kind == "negative":
        covariance[0, 0] = -1.
    else:
        covariance = np.eye(5)
    nav = replace(estimate(), covariance=covariance)
    assert not decision(nav).publish
    assert Px4VisionBridge().to_message(nav) is None


def test_mutable_pose_arrays_revalidated_at_boundary():
    nav = estimate()
    nav.pose_map_body.quaternion_xyzw[:] *= 2
    assert decision(nav).reason == "nonunit_pose_quaternion"


def test_malformed_numeric_data_rejected_without_conversion_exception():
    assert decision(replace(estimate(), covariance=[["bad"]])).reason == "invalid_numeric_pose_data"
    for timestamp in ["10.0", None, True]:
        assert decision(replace(estimate(), timestamp_s=timestamp)).reason == "invalid_monotonic_timestamp"


@pytest.mark.parametrize("limit", [float("nan"), float("inf"), -1., 0.])
def test_invalid_policy_rejected(limit):
    with pytest.raises(ValueError):
        Px4PublicationPolicy(max_estimate_age_s=limit)
    with pytest.raises(ValueError):
        Px4PublicationPolicy(max_covariance_trace_m2=limit)


def test_even_eligible_estimate_cannot_send_to_px4():
    assert decision(estimate()).publish
    with pytest.raises(NotImplementedError):
        asyncio.run(Px4VisionBridge().publish_mavsdk(
            estimate(), object(), compatibility=COMPATIBLE, transport_healthy=True, now_s=10.1))
