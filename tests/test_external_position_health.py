from vision_nav.external_position_health import ExternalPositionHealthConfig, ExternalPositionStreamHealth


def accepted_result(timestamp_us=1_000_000, x_m2=4.0, y_m2=5.0):
    return {
        "status": "accepted",
        "timestamp_us": timestamp_us,
        "measurement": {
            "frame": "local_enu",
            "x_m": 1.0,
            "y_m": 2.0,
            "covariance": {"x_m2": x_m2, "y_m2": y_m2, "z_m2": None, "yaw_rad2": None},
        },
    }


def test_stream_health_warms_up_then_becomes_healthy():
    health = ExternalPositionStreamHealth(ExternalPositionHealthConfig(min_rate_hz=0.5, max_latency_ms=200.0))

    first = health.update(
        result=accepted_result(timestamp_us=1_000_000),
        mavlink_result={"sent": True},
        message_type="odometry",
        now_monotonic_s=10.0,
        now_time_us=1_050_000,
    ).to_dict()
    assert first["status"] == "warming_up"
    assert first["last_latency_ms"] == 50.0

    second = health.update(
        result=accepted_result(timestamp_us=2_000_000),
        mavlink_result={"sent": True},
        message_type="odometry",
        now_monotonic_s=11.0,
        now_time_us=2_050_000,
    ).to_dict()
    assert second["status"] == "healthy"
    assert second["send_rate_hz"] == 1.0


def test_stream_health_reports_stale_high_covariance_and_skip_reasons():
    health = ExternalPositionStreamHealth(
        ExternalPositionHealthConfig(
            min_rate_hz=1.0,
            max_latency_ms=100.0,
            max_horizontal_variance_m2=10.0,
        )
    )

    snapshot = health.update(
        result=accepted_result(timestamp_us=1_000_000, x_m2=20.0, y_m2=5.0),
        mavlink_result={"sent": False, "reason": "not_connected"},
        message_type="vision_position_estimate",
        now_monotonic_s=10.0,
        now_time_us=1_250_000,
    ).to_dict()

    assert snapshot["status"] == "degraded"
    assert snapshot["sent_count"] == 0
    assert snapshot["skipped_count"] == 1
    assert snapshot["skip_reasons"]["not_connected"] == 1
    assert "latency_high" in snapshot["last_warnings"]
    assert "horizontal_covariance_high" in snapshot["last_warnings"]
    assert "send_skipped:not_connected" in snapshot["last_warnings"]


def test_stream_health_reports_rejected_matches_without_sending():
    health = ExternalPositionStreamHealth()
    snapshot = health.update(
        result={"status": "rejected", "reason": "not_enough_inliers"},
        mavlink_result={"sent": False, "reason": "match_not_accepted"},
        message_type="vision_position_estimate",
        now_monotonic_s=10.0,
        now_time_us=1_000_000,
    ).to_dict()

    assert snapshot["status"] == "degraded"
    assert snapshot["skip_reasons"]["match_not_accepted"] == 1
    assert snapshot["last_warnings"] == ["match_not_accepted"]
