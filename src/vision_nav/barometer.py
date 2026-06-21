from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any


STANDARD_PRESSURE_HPA = 1013.25


@dataclass(frozen=True)
class BarometerSample:
    timestamp_us: int | None = None
    altitude_m: float | None = None
    relative_altitude_m: float | None = None
    pressure_hpa: float | None = None
    temperature_c: float | None = None
    source: str = "unknown"

    @classmethod
    def from_mapping(cls, value: dict[str, Any] | None) -> "BarometerSample | None":
        if not value:
            return None
        pressure_hpa = _optional_float(_first_present(value, "pressure_hpa", "press_abs_hpa"))
        altitude_m = _optional_float(_first_present(value, "altitude_m", "pressure_altitude_m"))
        if altitude_m is None and pressure_hpa is not None:
            altitude_m = pressure_to_altitude_m(pressure_hpa)
        return cls(
            timestamp_us=_optional_int(value.get("timestamp_us")),
            altitude_m=altitude_m,
            relative_altitude_m=_optional_float(_first_present(value, "relative_altitude_m", "baro_relative_m")),
            pressure_hpa=pressure_hpa,
            temperature_c=_optional_float(value.get("temperature_c")),
            source=str(value.get("source") or "mapping"),
        )


@dataclass
class BarometerState:
    baseline_altitude_m: float | None = None
    altitude_m: float | None = None
    relative_altitude_m: float | None = None
    health: str = "unavailable"
    source: str | None = None
    last_timestamp_us: int | None = None

    @property
    def usable(self) -> bool:
        return self.relative_altitude_m is not None and self.health in {"healthy", "degraded"}

    def to_dict(self) -> dict[str, Any]:
        return {
            "altitude_m": self.altitude_m,
            "relative_altitude_m": self.relative_altitude_m,
            "health": self.health,
            "source": self.source,
            "last_timestamp_us": self.last_timestamp_us,
        }


class BarometerTracker:
    def __init__(self, *, max_jump_m: float = 8.0) -> None:
        self.state = BarometerState()
        self.max_jump_m = max_jump_m

    def update(self, sample: BarometerSample | None) -> BarometerState:
        if sample is None:
            return self.state
        relative = sample.relative_altitude_m
        altitude = sample.altitude_m
        if relative is None and altitude is not None:
            if self.state.baseline_altitude_m is None:
                self.state.baseline_altitude_m = altitude
            relative = altitude - self.state.baseline_altitude_m

        if relative is None:
            self.state.health = "unavailable"
            self.state.source = sample.source
            self.state.last_timestamp_us = sample.timestamp_us
            return self.state

        previous = self.state.relative_altitude_m
        self.state.health = "healthy"
        if previous is not None and abs(relative - previous) > self.max_jump_m:
            self.state.health = "degraded"
        self.state.altitude_m = altitude
        self.state.relative_altitude_m = relative
        self.state.source = sample.source
        self.state.last_timestamp_us = sample.timestamp_us
        return self.state


def pressure_to_altitude_m(pressure_hpa: float, sea_level_hpa: float = STANDARD_PRESSURE_HPA) -> float:
    if pressure_hpa <= 0 or sea_level_hpa <= 0:
        raise ValueError("pressure_hpa and sea_level_hpa must be positive")
    return float(44330.0 * (1.0 - (pressure_hpa / sea_level_hpa) ** 0.190294957))


def _first_present(value: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in value and value[key] is not None:
            return value[key]
    return None


def _optional_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        output = float(value)
        return output if math.isfinite(output) else None
    except (TypeError, ValueError):
        return None


def _optional_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None
