from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

from vision_nav.barometer import BarometerSample, BarometerState, BarometerTracker


@dataclass
class TerrainEstimatorState:
    east_m: float | None = None
    north_m: float | None = None
    yaw_rad: float | None = None
    covariance_x_m2: float | None = None
    covariance_y_m2: float | None = None
    last_timestamp_us: int | None = None
    confidence: float = 0.0
    scale_confidence: float = 0.0

    @property
    def initialized(self) -> bool:
        return self.east_m is not None and self.north_m is not None


class TerrainEstimator:
    """Small prototype state estimator for map fixes plus IMU/flow propagation.

    This is intentionally conservative. It does not invent vertical position,
    and covariance grows whenever propagation happens without a fresh map fix.
    """

    def __init__(self, *, process_noise_m2_per_s: float = 4.0) -> None:
        self.state = TerrainEstimatorState()
        self.process_noise_m2_per_s = process_noise_m2_per_s
        self.barometer = BarometerTracker()

    def propagate_time(self, timestamp_us: int) -> TerrainEstimatorState:
        if self.state.last_timestamp_us is None:
            self.state.last_timestamp_us = timestamp_us
            return self.state
        dt_s = max((timestamp_us - self.state.last_timestamp_us) / 1_000_000.0, 0.0)
        growth = self.process_noise_m2_per_s * dt_s
        self._inflate_covariance(growth)
        self.state.last_timestamp_us = timestamp_us
        return self.state

    def update_attitude(self, *, yaw_rad: float | None = None) -> TerrainEstimatorState:
        if yaw_rad is not None and math.isfinite(yaw_rad):
            self.state.yaw_rad = float(yaw_rad)
        return self.state

    def propagate_optical_flow(
        self,
        *,
        delta_x_px: float,
        delta_y_px: float,
        gsd_m: float,
        yaw_rad: float | None = None,
        confidence: float = 0.5,
    ) -> TerrainEstimatorState:
        if not self.state.initialized:
            return self.state
        yaw = self.state.yaw_rad if yaw_rad is None else yaw_rad
        if yaw is None:
            yaw = 0.0
        east_body = float(delta_x_px) * float(gsd_m)
        north_body = -float(delta_y_px) * float(gsd_m)
        cos_y = math.cos(yaw)
        sin_y = math.sin(yaw)
        east = east_body * cos_y - north_body * sin_y
        north = east_body * sin_y + north_body * cos_y
        self.state.east_m = float(self.state.east_m) + east
        self.state.north_m = float(self.state.north_m) + north
        self.state.confidence = min(self.state.confidence, max(0.0, min(float(confidence), 1.0)))
        self._inflate_covariance(max(float(gsd_m), 0.1) ** 2 * (1.0 + 4.0 * (1.0 - self.state.confidence)))
        return self.state

    def update_barometer(self, sample: BarometerSample | dict[str, Any] | None) -> BarometerState:
        if isinstance(sample, dict):
            sample = BarometerSample.from_mapping(sample)
        return self.barometer.update(sample)

    def update_from_match(
        self,
        result: dict[str, Any],
        *,
        barometer_sample: BarometerSample | dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        baro = self.update_barometer(barometer_sample)
        timestamp_us = int(result.get("timestamp_us") or 0)
        if timestamp_us:
            self.propagate_time(timestamp_us)
        self._apply_barometer_fields(result, baro)

        if result.get("status") != "accepted":
            self._inflate_covariance(4.0)
            result["estimator"] = self.to_dict()
            return result

        position = result.get("local_enu_m") or {}
        east_m = position.get("x")
        north_m = position.get("y")
        if east_m is None or north_m is None:
            result["estimator"] = self.to_dict()
            return result

        covariance = result.get("covariance") or {}
        self.state.east_m = float(east_m)
        self.state.north_m = float(north_m)
        self.state.covariance_x_m2 = _optional_float(covariance.get("x_m2"))
        self.state.covariance_y_m2 = _optional_float(covariance.get("y_m2"))
        self.state.confidence = float(result.get("position_confidence", result.get("confidence", 0.0)) or 0.0)
        self.state.scale_confidence = float(result.get("scale_confidence", 0.0) or 0.0)
        if baro.usable:
            self.state.scale_confidence = min(1.0, self.state.scale_confidence + 0.10)
        result["estimator"] = self.to_dict()
        return result

    def to_dict(self) -> dict[str, Any]:
        return {
            "initialized": self.state.initialized,
            "local_enu_m": {
                "x": self.state.east_m,
                "y": self.state.north_m,
                "z": None,
            },
            "yaw_rad": self.state.yaw_rad,
            "covariance": {
                "x_m2": self.state.covariance_x_m2,
                "y_m2": self.state.covariance_y_m2,
                "z_m2": None,
                "yaw_rad2": None,
            },
            "confidence": self.state.confidence,
            "scale_confidence": self.state.scale_confidence,
            "barometer": self.barometer.state.to_dict(),
            "last_timestamp_us": self.state.last_timestamp_us,
        }

    def _inflate_covariance(self, growth_m2: float) -> None:
        if not self.state.initialized:
            return
        growth = max(float(growth_m2), 0.0)
        self.state.covariance_x_m2 = (self.state.covariance_x_m2 or 25.0) + growth
        self.state.covariance_y_m2 = (self.state.covariance_y_m2 or 25.0) + growth
        self.state.confidence = max(0.0, self.state.confidence * 0.98)
        self.state.scale_confidence = max(0.0, self.state.scale_confidence * 0.96)

    def _apply_barometer_fields(self, result: dict[str, Any], baro: BarometerState) -> None:
        result["altitude_source"] = "barometer" if baro.usable else "unset"
        result["baro_altitude_m"] = baro.altitude_m
        result["baro_relative_m"] = baro.relative_altitude_m
        result["baro_health"] = baro.health

        if not baro.usable:
            result.setdefault("local_enu_m", {}).setdefault("z", None)
            result.setdefault("covariance", {}).setdefault("z_m2", None)
            measurement = result.get("measurement")
            if isinstance(measurement, dict):
                measurement["z_m"] = None
                measurement.setdefault("covariance", {})["z_m2"] = None
            return

        z_m = baro.relative_altitude_m
        z_var = 4.0 if baro.health == "healthy" else 25.0
        result.setdefault("local_enu_m", {})["z"] = z_m
        result.setdefault("covariance", {})["z_m2"] = z_var
        result["scale_confidence"] = min(1.0, float(result.get("scale_confidence", 0.0) or 0.0) + 0.10)
        measurement = result.get("measurement")
        if isinstance(measurement, dict):
            measurement["z_m"] = z_m
            measurement.setdefault("covariance", {})["z_m2"] = z_var


def _optional_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        output = float(value)
        return output if math.isfinite(output) else None
    except (TypeError, ValueError):
        return None
