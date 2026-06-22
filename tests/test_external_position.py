import math

from vision_nav.external_position import (
    ExternalPositionEstimate,
    OdometryResetTracker,
    build_odometry_payload,
    build_vision_position_estimate_payload,
    external_position_from_match_result,
    yaw_enu_to_ned,
)


def test_external_position_from_match_result_requires_accepted_local_enu():
    estimate, reason = external_position_from_match_result({"status": "rejected"})
    assert estimate is None
    assert reason == "match_not_accepted"

    estimate, reason = external_position_from_match_result({"status": "accepted", "measurement": {"frame": "local_ned"}})
    assert estimate is None
    assert reason == "missing_local_enu_measurement"


def test_enu_to_ned_axes_and_yaw_conversion():
    estimate, reason = external_position_from_match_result(
        {
            "status": "accepted",
            "timestamp_us": 123,
            "confidence": 0.8,
            "measurement": {
                "frame": "local_enu",
                "x_m": 4.0,
                "y_m": 7.0,
                "z_m": 3.0,
                "yaw_rad": 0.0,
                "velocity": {
                    "frame": "local_enu",
                    "east_mps": 1.0,
                    "north_mps": -2.0,
                    "up_mps": 0.5,
                    "covariance": {"east_m2": 0.2, "north_m2": 0.3, "up_m2": 0.4},
                },
                "covariance": {"x_m2": 9.0, "y_m2": 16.0, "z_m2": 25.0, "yaw_rad2": 0.2},
            },
        }
    )
    assert reason is None
    ned = estimate.to_local_ned()
    assert ned.north_m == 7.0
    assert ned.east_m == 4.0
    assert ned.down_m == -3.0
    assert ned.velocity_north_mps == -2.0
    assert ned.velocity_east_mps == 1.0
    assert ned.velocity_down_mps == -0.5
    assert math.isclose(ned.yaw_rad, math.pi / 2.0)
    assert math.isclose(yaw_enu_to_ned(math.pi / 2.0), 0.0)
    payload = build_odometry_payload(estimate, time_usec=1000)
    assert payload.velocity_covariance_urt[0] == 0.3
    assert payload.velocity_covariance_urt[6] == 0.2
    assert payload.velocity_covariance_urt[11] == 0.4


def test_vision_position_payload_maps_covariance_to_mavlink_urt():
    estimate = ExternalPositionEstimate(
        timestamp_us=123,
        east_m=4.0,
        north_m=7.0,
        up_m=None,
        yaw_enu_rad=None,
    )
    payload = build_vision_position_estimate_payload(estimate, time_usec=555)
    assert payload.to_mavlink_args()[0] == 555
    assert payload.x_north_m == 7.0
    assert payload.y_east_m == 4.0
    assert payload.z_down_m == 0.0
    assert payload.covariance_urt[0] == 25.0
    assert payload.covariance_urt[6] == 25.0
    assert payload.covariance_urt[11] == 100.0
    assert math.isclose(payload.covariance_urt[20], math.radians(30.0) ** 2)


def test_odometry_payload_is_ready_for_px4_external_vision_path():
    estimate = ExternalPositionEstimate(
        timestamp_us=123,
        east_m=1.0,
        north_m=2.0,
        up_m=3.0,
        yaw_enu_rad=0.0,
        confidence=0.73,
    )
    payload = build_odometry_payload(estimate, time_usec=999, reset_counter=4)
    assert payload.frame_id == "MAV_FRAME_LOCAL_FRD"
    assert payload.child_frame_id == "MAV_FRAME_BODY_FRD"
    assert payload.x_m == 2.0
    assert payload.y_m == 1.0
    assert payload.z_m == -3.0
    assert payload.reset_counter == 4
    assert payload.estimator_type == "MAV_ESTIMATOR_TYPE_VISION"
    assert payload.quality == 73
    assert math.isnan(payload.velocity_covariance_urt[0])
    assert math.isclose(payload.q[0], math.cos(math.pi / 4.0))
    assert math.isclose(payload.q[3], math.sin(math.pi / 4.0))


def test_odometry_reset_tracker_increments_on_discontinuities():
    tracker = OdometryResetTracker()
    assert tracker.update_from_result({"timestamp_us": 100, "map_id": "a", "estimator": {"reset_counter": 1}}) == 0
    assert tracker.update_from_result({"timestamp_us": 110, "map_id": "a", "estimator": {"reset_counter": 1}}) == 0
    assert tracker.update_from_result({"timestamp_us": 120, "map_id": "a", "estimator": {"reset_counter": 2}}) == 1
    assert tracker.update_from_result({"timestamp_us": 130, "map_id": "b", "estimator": {"reset_counter": 2}}) == 2
    assert tracker.update_from_result({"timestamp_us": 90, "map_id": "b", "estimator": {"reset_counter": 2}}) == 3
