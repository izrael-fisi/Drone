import time

from vision_nav.mavlink_bridge import MavlinkSendResult, MavlinkVisionBridge, parse_mavlink_endpoint, send_records_once


def test_parse_mavlink_endpoint_aliases():
    assert parse_mavlink_endpoint("udp:14550") == ("udpout:127.0.0.1:14550", None)
    assert parse_mavlink_endpoint("udp:192.168.1.10:14550") == ("udpout:192.168.1.10:14550", None)
    assert parse_mavlink_endpoint("serial:/dev/ttyAMA0:921600") == ("/dev/ttyAMA0", 921600)


def test_send_match_result_uses_px4_ned_axes():
    calls = []

    class FakeMav:
        def vision_position_estimate_send(self, *args):
            calls.append(args)

    class FakeConnection:
        mav = FakeMav()

    bridge = MavlinkVisionBridge("udp:14550")
    bridge._conn = FakeConnection()
    bridge._last_heartbeat_s = time.monotonic()

    result = bridge.send_match_result(
        {
            "status": "accepted",
            "measurement": {
                "frame": "local_enu",
                "x_m": 4.0,
                "y_m": 7.0,
                "z_m": None,
                "yaw_rad": None,
                "covariance": {
                    "x_m2": 9.0,
                    "y_m2": 16.0,
                    "z_m2": None,
                    "yaw_rad2": None,
                },
            },
        },
        message_type="vision_position_estimate",
    )

    assert result.sent is True
    assert calls
    _, x_north, y_east, z_down, *_rest, covariance = calls[0]
    assert x_north == 7.0
    assert y_east == 4.0
    assert z_down == -0.0
    assert covariance[0] == 16.0
    assert covariance[6] == 9.0


def test_send_match_result_maps_optional_z_to_px4_down():
    calls = []

    class FakeMav:
        def vision_position_estimate_send(self, *args):
            calls.append(args)

    class FakeConnection:
        mav = FakeMav()

    bridge = MavlinkVisionBridge("udp:14550")
    bridge._conn = FakeConnection()
    bridge._last_heartbeat_s = time.monotonic()

    result = bridge.send_match_result(
        {
            "status": "accepted",
            "measurement": {
                "frame": "local_enu",
                "x_m": 1.0,
                "y_m": 2.0,
                "z_m": 3.0,
                "yaw_rad": 0.25,
                "covariance": {
                    "x_m2": 4.0,
                    "y_m2": 5.0,
                    "z_m2": 6.0,
                    "yaw_rad2": 0.1,
                },
            },
        },
        message_type="vision_position_estimate",
    )

    assert result.sent is True
    _, _x_north, _y_east, z_down, *_rest, covariance = calls[0]
    assert z_down == -3.0
    assert covariance[11] == 6.0
    assert covariance[20] == 0.1


def test_send_odometry_match_result_uses_payload_for_px4_path():
    calls = []

    class FakeMav:
        def odometry_send(self, *args):
            calls.append(args)

    class FakeConnection:
        mav = FakeMav()

    bridge = MavlinkVisionBridge("udp:14550")
    bridge._conn = FakeConnection()
    bridge._last_heartbeat_s = time.monotonic()

    result = bridge.send_odometry_match_result(
        {
            "status": "accepted",
            "confidence": 0.5,
            "measurement": {
                "frame": "local_enu",
                "x_m": 1.0,
                "y_m": 2.0,
                "z_m": 3.0,
                "covariance": {"x_m2": 4.0, "y_m2": 5.0, "z_m2": 6.0, "yaw_rad2": 0.1},
            },
        }
    )

    assert result.sent is True
    assert calls
    _, frame_id, child_frame_id, x_north, y_east, z_down, *_rest = calls[0]
    assert frame_id == 20
    assert child_frame_id == 12
    assert x_north == 2.0
    assert y_east == 1.0
    assert z_down == -3.0


def test_send_odometry_match_result_increments_reset_counter():
    calls = []

    class FakeMav:
        def odometry_send(self, *args):
            calls.append(args)

    class FakeConnection:
        mav = FakeMav()

    bridge = MavlinkVisionBridge("udp:14550")
    bridge._conn = FakeConnection()
    bridge._last_heartbeat_s = time.monotonic()

    first = bridge.send_odometry_match_result(
        {
            "status": "accepted",
            "timestamp_us": 100,
            "map_id": "a",
            "estimator": {"reset_counter": 1},
            "measurement": {"frame": "local_enu", "x_m": 1.0, "y_m": 2.0},
        }
    )
    second = bridge.send_odometry_match_result(
        {
            "status": "accepted",
            "timestamp_us": 110,
            "map_id": "a",
            "estimator": {"reset_counter": 2},
            "measurement": {"frame": "local_enu", "x_m": 1.0, "y_m": 2.0},
        }
    )
    third = bridge.send_odometry_match_result(
        {
            "status": "accepted",
            "timestamp_us": 120,
            "map_id": "b",
            "estimator": {"reset_counter": 2},
            "measurement": {"frame": "local_enu", "x_m": 1.0, "y_m": 2.0},
        }
    )

    assert first.details["reset_counter"] == 0
    assert second.details["reset_counter"] == 1
    assert third.details["reset_counter"] == 2
    assert calls[0][15] == 0
    assert calls[1][15] == 1
    assert calls[2][15] == 2


def test_send_match_result_dispatches_odometry_mode():
    calls = []

    class FakeMav:
        def odometry_send(self, *args):
            calls.append(args)

    class FakeConnection:
        mav = FakeMav()

    bridge = MavlinkVisionBridge("udp:14550")
    bridge._conn = FakeConnection()
    bridge._last_heartbeat_s = time.monotonic()

    result = bridge.send_match_result(
        {
            "status": "accepted",
            "measurement": {
                "frame": "local_enu",
                "x_m": 1.0,
                "y_m": 2.0,
            },
        },
        message_type="odometry",
    )

    assert result.sent is True
    assert result.message == "ODOMETRY"
    assert calls


def test_send_records_once_uses_selected_message_type_and_reports_skips():
    calls = []

    class FakeBridge:
        def send_match_result(self, result, *, message_type="vision_position_estimate"):
            calls.append((result, message_type))
            if result.get("status") != "accepted":
                return MavlinkSendResult(False, reason="match_not_accepted")
            return MavlinkSendResult(True, message="ODOMETRY" if message_type == "odometry" else "VISION_POSITION_ESTIMATE")

    report = send_records_once(
        [
            {"result": {"status": "accepted", "measurement": {"frame": "local_enu", "x_m": 1.0, "y_m": 2.0}}},
            {"result": {"status": "rejected", "reason": "low_inliers"}},
        ],
        FakeBridge(),
        message_type="odometry",
        repeat=2,
    )

    assert report["message_type"] == "odometry"
    assert report["sent"] == 2
    assert report["skipped"] == 2
    assert report["skip_reasons"] == {"match_not_accepted": 2}
    assert {message_type for _result, message_type in calls} == {"odometry"}
