from vision_nav.barometer import BarometerSample, BarometerTracker, pressure_to_altitude_m
from vision_nav.terrain_estimator import TerrainEstimator


def test_pressure_to_altitude_at_sea_level_is_near_zero():
    assert abs(pressure_to_altitude_m(1013.25)) < 0.01


def test_barometer_tracker_baselines_absolute_altitude():
    tracker = BarometerTracker()
    first = tracker.update(BarometerSample(altitude_m=100.0, source="unit"))
    second = tracker.update(BarometerSample(altitude_m=103.0, source="unit"))

    assert first.relative_altitude_m == 0.0
    assert second.relative_altitude_m == 3.0
    assert second.health == "healthy"


def test_estimator_adds_optional_barometer_vertical_fields():
    estimator = TerrainEstimator()
    result = estimator.update_from_match(
        {
            "timestamp_us": 1,
            "status": "accepted",
            "local_enu_m": {"x": 1.0, "y": 2.0, "z": None},
            "confidence": 0.8,
            "scale_confidence": 0.5,
            "covariance": {"x_m2": 1.0, "y_m2": 1.0, "z_m2": None, "yaw_rad2": None},
            "measurement": {
                "frame": "local_enu",
                "x_m": 1.0,
                "y_m": 2.0,
                "z_m": None,
                "covariance": {"x_m2": 1.0, "y_m2": 1.0, "z_m2": None, "yaw_rad2": None},
            },
        },
        barometer_sample={"relative_altitude_m": 4.0, "source": "unit"},
    )

    assert result["altitude_source"] == "barometer"
    assert result["baro_health"] == "healthy"
    assert result["local_enu_m"]["z"] == 4.0
    assert result["measurement"]["z_m"] == 4.0
    assert result["covariance"]["z_m2"] == 4.0
