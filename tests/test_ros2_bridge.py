import math
from pathlib import Path

from vision_nav.ros2_bridge import (
    DIAG_ERROR,
    DIAG_OK,
    diagnostic_status_from_health,
    diagnostic_status_from_result,
    odometry_dict_from_match_result,
    ros_record_from_runtime_record,
    ros_records_from_log,
)


def test_odometry_dict_from_match_result_uses_ros_enu_axes():
    odometry, reason = odometry_dict_from_match_result(
        {
            "status": "accepted",
            "timestamp_us": 1_234_567,
            "confidence": 0.8,
            "measurement": {
                "frame": "local_enu",
                "x_m": 4.0,
                "y_m": 7.0,
                "z_m": 3.0,
                "yaw_rad": math.pi / 2.0,
                "covariance": {"x_m2": 9.0, "y_m2": 16.0, "z_m2": 25.0, "yaw_rad2": 0.2},
            },
        }
    )

    assert reason is None
    assert odometry["header"]["stamp"] == {"sec": 1, "nanosec": 234_567_000}
    assert odometry["header"]["frame_id"] == "map"
    assert odometry["child_frame_id"] == "base_link"
    assert odometry["pose"]["pose"]["position"] == {"x": 4.0, "y": 7.0, "z": 3.0}
    assert math.isclose(odometry["pose"]["pose"]["orientation"]["z"], math.sin(math.pi / 4.0))
    assert math.isclose(odometry["pose"]["pose"]["orientation"]["w"], math.cos(math.pi / 4.0))
    assert odometry["pose"]["covariance"][0] == 9.0
    assert odometry["pose"]["covariance"][7] == 16.0
    assert odometry["pose"]["covariance"][14] == 25.0
    assert odometry["pose"]["covariance"][35] == 0.2


def test_diagnostic_status_maps_health_levels():
    healthy = diagnostic_status_from_health({"status": "healthy", "sent_count": 4, "message_type": "odometry"})
    degraded = diagnostic_status_from_health(
        {
            "status": "degraded",
            "sent_count": 0,
            "message_type": "odometry",
            "last_warnings": ["latency_high"],
        }
    )

    assert healthy["level"] == DIAG_OK
    assert healthy["values"]["message_type"] == "odometry"
    assert degraded["level"] == DIAG_ERROR
    assert degraded["values"]["warnings"] == "latency_high"


def test_diagnostic_status_from_result_without_stream_health():
    accepted = diagnostic_status_from_result({"status": "accepted", "confidence": 0.8, "tile_id": "tile_1"})
    rejected = diagnostic_status_from_result({"status": "rejected", "reason": "not_enough_inliers"})

    assert accepted["level"] == DIAG_OK
    assert accepted["values"]["tile_id"] == "tile_1"
    assert rejected["level"] == 1
    assert "not_enough_inliers" in rejected["message"]


def test_ros_record_from_runtime_record_without_mavlink_health():
    record = {
        "sequence": 1,
        "result": {
            "status": "accepted",
            "timestamp_us": 1_000_000,
            "measurement": {
                "frame": "local_enu",
                "x_m": 1.0,
                "y_m": 2.0,
                "covariance": {"x_m2": 4.0, "y_m2": 5.0},
            },
        },
    }
    ros_record = ros_record_from_runtime_record(record)
    assert ros_record["published"] is True
    assert ros_record["diagnostic"]["level"] == DIAG_OK
    assert ros_record["odometry"]["pose"]["pose"]["position"]["x"] == 1.0


def test_ros_records_from_log_skips_rejected_matches(tmp_path: Path):
    log_path = tmp_path / "terrain_matches.jsonl"
    log_path.write_text(
        "\n".join(
            [
                '{"sequence": 1, "external_position_health": {"status": "healthy", "sent_count": 1}, "result": {"status": "accepted", "timestamp_us": 1000000, "measurement": {"frame": "local_enu", "x_m": 1.0, "y_m": 2.0, "covariance": {"x_m2": 4.0, "y_m2": 5.0}}}}',
                '{"sequence": 2, "external_position_health": {"status": "degraded", "sent_count": 1}, "result": {"status": "rejected", "reason": "not_enough_inliers"}}',
            ]
        )
        + "\n"
    )

    records = ros_records_from_log(log_path)
    assert records[0]["published"] is True
    assert records[0]["odometry"]["pose"]["pose"]["position"]["x"] == 1.0
    assert records[1]["published"] is False
    assert records[1]["skip_reason"] == "match_not_accepted"
