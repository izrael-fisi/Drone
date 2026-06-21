import time

from vision_nav.mavlink_bridge import MavlinkVisionBridge, parse_mavlink_endpoint


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
        }
    )

    assert result.sent is True
    assert calls
    _, x_north, y_east, z_down, *_rest, covariance = calls[0]
    assert x_north == 7.0
    assert y_east == 4.0
    assert z_down == -0.0
    assert covariance[0] == 16.0
    assert covariance[6] == 9.0
