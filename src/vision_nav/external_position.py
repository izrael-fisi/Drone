from __future__ import annotations

from dataclasses import dataclass
import math
import time
from typing import Any


DEFAULT_HORIZONTAL_VARIANCE_M2 = 25.0
DEFAULT_VERTICAL_VARIANCE_M2 = 100.0
DEFAULT_YAW_VARIANCE_RAD2 = math.radians(30.0) ** 2


@dataclass(frozen=True)
class ExternalPositionCovariance:
    east_m2: float | None = None
    north_m2: float | None = None
    up_m2: float | None = None
    yaw_rad2: float | None = None

    @classmethod
    def from_local_enu_mapping(cls, covariance: dict[str, Any] | None) -> "ExternalPositionCovariance":
        covariance = covariance or {}
        return cls(
            east_m2=_optional_float(covariance.get("x_m2")),
            north_m2=_optional_float(covariance.get("y_m2")),
            up_m2=_optional_float(covariance.get("z_m2")),
            yaw_rad2=_optional_float(covariance.get("yaw_rad2")),
        )

    def with_mavlink_defaults(self) -> "ExternalPositionCovariance":
        east_m2 = _defaulted_variance(self.east_m2, DEFAULT_HORIZONTAL_VARIANCE_M2)
        north_m2 = _defaulted_variance(self.north_m2, east_m2)
        horizontal_m2 = max(east_m2, north_m2)
        up_m2 = _defaulted_variance(self.up_m2, max(horizontal_m2 * 4.0, DEFAULT_VERTICAL_VARIANCE_M2))
        yaw_rad2 = _defaulted_variance(self.yaw_rad2, DEFAULT_YAW_VARIANCE_RAD2)
        return ExternalPositionCovariance(
            east_m2=east_m2,
            north_m2=north_m2,
            up_m2=up_m2,
            yaw_rad2=yaw_rad2,
        )

    def to_mavlink_pose_covariance_urt(self) -> list[float]:
        """Return MAVLink upper-right-triangle covariance for local NED/FRD axes."""

        cov = self.with_mavlink_defaults()
        output = [0.0] * 21
        output[0] = float(cov.north_m2)
        output[6] = float(cov.east_m2)
        output[11] = float(cov.up_m2)
        output[20] = float(cov.yaw_rad2)
        return output


@dataclass(frozen=True)
class ExternalVelocityCovariance:
    east_m2ps2: float | None = None
    north_m2ps2: float | None = None
    up_m2ps2: float | None = None

    @classmethod
    def from_local_enu_mapping(cls, covariance: dict[str, Any] | None) -> "ExternalVelocityCovariance":
        covariance = covariance or {}
        return cls(
            east_m2ps2=_optional_variance(
                _first_present_keys(covariance, "x_m2", "east_m2", "vx_m2ps2", "east_m2ps2")
            ),
            north_m2ps2=_optional_variance(
                _first_present_keys(covariance, "y_m2", "north_m2", "vy_m2ps2", "north_m2ps2")
            ),
            up_m2ps2=_optional_variance(
                _first_present_keys(covariance, "z_m2", "up_m2", "vz_m2ps2", "up_m2ps2")
            ),
        )

    def has_any(self) -> bool:
        return self.east_m2ps2 is not None or self.north_m2ps2 is not None or self.up_m2ps2 is not None

    def to_mavlink_velocity_covariance_urt(self) -> list[float]:
        """Return MAVLink velocity covariance for local NED/FRD axes, preserving unknowns as NaN."""

        output = [math.nan] * 21
        if self.north_m2ps2 is not None:
            output[0] = float(self.north_m2ps2)
        if self.east_m2ps2 is not None:
            output[6] = float(self.east_m2ps2)
        if self.up_m2ps2 is not None:
            output[11] = float(self.up_m2ps2)
        return output


@dataclass(frozen=True)
class LocalNedEstimate:
    north_m: float
    east_m: float
    down_m: float | None = None
    yaw_rad: float | None = None
    covariance: ExternalPositionCovariance = ExternalPositionCovariance()
    velocity_north_mps: float | None = None
    velocity_east_mps: float | None = None
    velocity_down_mps: float | None = None
    velocity_covariance: ExternalVelocityCovariance = ExternalVelocityCovariance()


@dataclass(frozen=True)
class ExternalPositionEstimate:
    timestamp_us: int | None
    east_m: float
    north_m: float
    up_m: float | None = None
    yaw_enu_rad: float | None = None
    covariance: ExternalPositionCovariance = ExternalPositionCovariance()
    velocity_east_mps: float | None = None
    velocity_north_mps: float | None = None
    velocity_up_mps: float | None = None
    velocity_covariance: ExternalVelocityCovariance = ExternalVelocityCovariance()
    confidence: float | None = None
    source: str = "terrain_vision"

    def to_local_ned(self) -> LocalNedEstimate:
        return LocalNedEstimate(
            north_m=self.north_m,
            east_m=self.east_m,
            down_m=None if self.up_m is None else -self.up_m,
            yaw_rad=yaw_enu_to_ned(self.yaw_enu_rad),
            covariance=self.covariance,
            velocity_north_mps=self.velocity_north_mps,
            velocity_east_mps=self.velocity_east_mps,
            velocity_down_mps=None if self.velocity_up_mps is None else -self.velocity_up_mps,
            velocity_covariance=self.velocity_covariance,
        )


@dataclass(frozen=True)
class VisionPositionEstimatePayload:
    time_usec: int
    x_north_m: float
    y_east_m: float
    z_down_m: float
    roll_rad: float
    pitch_rad: float
    yaw_rad: float
    covariance_urt: list[float]

    def to_mavlink_args(self) -> tuple[Any, ...]:
        return (
            self.time_usec,
            self.x_north_m,
            self.y_east_m,
            self.z_down_m,
            self.roll_rad,
            self.pitch_rad,
            self.yaw_rad,
            self.covariance_urt,
        )


@dataclass(frozen=True)
class OdometryPayload:
    time_usec: int
    frame_id: str
    child_frame_id: str
    x_m: float
    y_m: float
    z_m: float
    q: tuple[float, float, float, float]
    vx_mps: float
    vy_mps: float
    vz_mps: float
    rollspeed_radps: float
    pitchspeed_radps: float
    yawspeed_radps: float
    pose_covariance_urt: list[float]
    velocity_covariance_urt: list[float]
    reset_counter: int
    estimator_type: str
    quality: int


@dataclass
class OdometryResetTracker:
    reset_counter: int = 0
    last_timestamp_us: int | None = None
    last_map_id: str | None = None
    last_estimator_reset_epoch: int | None = None

    def update_from_result(self, result: dict[str, Any]) -> int:
        timestamp_us = _optional_int(result.get("timestamp_us"))
        map_id = _optional_str(result.get("map_id"))
        reset_epoch = _reset_epoch_from_result(result)
        should_increment = False

        if self.last_timestamp_us is not None and timestamp_us is not None and timestamp_us < self.last_timestamp_us:
            should_increment = True
        if self.last_map_id is not None and map_id is not None and map_id != self.last_map_id:
            should_increment = True
        if (
            self.last_estimator_reset_epoch is not None
            and reset_epoch is not None
            and reset_epoch != self.last_estimator_reset_epoch
        ):
            should_increment = True

        if should_increment:
            self.reset_counter = (self.reset_counter + 1) % 256

        if timestamp_us is not None:
            self.last_timestamp_us = timestamp_us
        if map_id is not None:
            self.last_map_id = map_id
        if reset_epoch is not None:
            self.last_estimator_reset_epoch = reset_epoch
        return self.reset_counter


def external_position_from_match_result(result: dict[str, Any]) -> tuple[ExternalPositionEstimate | None, str | None]:
    if result.get("status") != "accepted":
        return None, "match_not_accepted"

    measurement = result.get("measurement") or {}
    position = result.get("estimated_position") or {}
    if measurement.get("frame") != "local_enu":
        return None, "missing_local_enu_measurement"

    east_m = _optional_float(_first_present(measurement, position, "x_m", "east_m"))
    north_m = _optional_float(_first_present(measurement, position, "y_m", "north_m"))
    if east_m is None or north_m is None:
        return None, "missing_local_position"

    up_m = _optional_float(measurement.get("z_m"))
    yaw_enu_rad = _optional_float(measurement.get("yaw_rad"))
    confidence = _optional_float(result.get("position_confidence", result.get("confidence")))
    timestamp_us = _optional_int(result.get("timestamp_us"))
    velocity_east_mps = _local_enu_velocity_component(result, "east")
    velocity_north_mps = _local_enu_velocity_component(result, "north")
    velocity_up_mps = _local_enu_velocity_component(result, "up")
    velocity_covariance = _local_enu_velocity_covariance(result)
    return (
        ExternalPositionEstimate(
            timestamp_us=timestamp_us,
            east_m=east_m,
            north_m=north_m,
            up_m=up_m,
            yaw_enu_rad=yaw_enu_rad,
            covariance=ExternalPositionCovariance.from_local_enu_mapping(measurement.get("covariance")),
            velocity_east_mps=velocity_east_mps,
            velocity_north_mps=velocity_north_mps,
            velocity_up_mps=velocity_up_mps,
            velocity_covariance=velocity_covariance,
            confidence=confidence,
            source=str(measurement.get("source") or result.get("source") or "terrain_vision"),
        ),
        None,
    )


def build_vision_position_estimate_payload(
    estimate: ExternalPositionEstimate,
    *,
    time_usec: int | None = None,
) -> VisionPositionEstimatePayload:
    ned = estimate.to_local_ned()
    return VisionPositionEstimatePayload(
        time_usec=int(time_usec if time_usec is not None else current_time_us()),
        x_north_m=float(ned.north_m),
        y_east_m=float(ned.east_m),
        z_down_m=float(ned.down_m or 0.0),
        roll_rad=0.0,
        pitch_rad=0.0,
        yaw_rad=float(ned.yaw_rad or 0.0),
        covariance_urt=ned.covariance.to_mavlink_pose_covariance_urt(),
    )


def build_odometry_payload(
    estimate: ExternalPositionEstimate,
    *,
    time_usec: int | None = None,
    reset_counter: int = 0,
) -> OdometryPayload:
    ned = estimate.to_local_ned()
    yaw_rad = float(ned.yaw_rad or 0.0)
    quality = -1 if estimate.confidence is None else int(max(0.0, min(estimate.confidence, 1.0)) * 100.0)
    return OdometryPayload(
        time_usec=int(time_usec if time_usec is not None else current_time_us()),
        frame_id="MAV_FRAME_LOCAL_FRD",
        child_frame_id="MAV_FRAME_BODY_FRD",
        x_m=float(ned.north_m),
        y_m=float(ned.east_m),
        z_m=float(ned.down_m or 0.0),
        q=yaw_to_quaternion_wxyz(yaw_rad),
        vx_mps=_nan_if_none(ned.velocity_north_mps),
        vy_mps=_nan_if_none(ned.velocity_east_mps),
        vz_mps=_nan_if_none(ned.velocity_down_mps),
        rollspeed_radps=math.nan,
        pitchspeed_radps=math.nan,
        yawspeed_radps=math.nan,
        pose_covariance_urt=ned.covariance.to_mavlink_pose_covariance_urt(),
        velocity_covariance_urt=ned.velocity_covariance.to_mavlink_velocity_covariance_urt(),
        reset_counter=int(reset_counter),
        estimator_type="MAV_ESTIMATOR_TYPE_VISION",
        quality=quality,
    )


def yaw_enu_to_ned(yaw_enu_rad: float | None) -> float | None:
    if yaw_enu_rad is None or not math.isfinite(yaw_enu_rad):
        return None
    return wrap_pi(math.pi / 2.0 - float(yaw_enu_rad))


def yaw_to_quaternion_wxyz(yaw_rad: float) -> tuple[float, float, float, float]:
    half = float(yaw_rad) / 2.0
    return (math.cos(half), 0.0, 0.0, math.sin(half))


def wrap_pi(angle_rad: float) -> float:
    return (float(angle_rad) + math.pi) % (2.0 * math.pi) - math.pi


def current_time_us() -> int:
    return int(time.time() * 1_000_000)


def _first_present(primary: dict[str, Any], fallback: dict[str, Any], primary_key: str, fallback_key: str) -> Any:
    if primary_key in primary:
        return primary.get(primary_key)
    return fallback.get(fallback_key)


def _first_present_keys(mapping: dict[str, Any], *keys: str) -> Any:
    for key in keys:
        if key in mapping:
            return mapping.get(key)
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


def _optional_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _optional_variance(value: Any) -> float | None:
    output = _optional_float(value)
    if output is None or output < 0.0:
        return None
    return output


def _local_enu_velocity_component(result: dict[str, Any], component: str) -> float | None:
    direct_keys = {
        "east": ("velocity_east_mps", "east_velocity_mps", "ve_mps", "vx_mps"),
        "north": ("velocity_north_mps", "north_velocity_mps", "vn_mps", "vy_mps"),
        "up": ("velocity_up_mps", "up_velocity_mps", "vu_mps", "vz_mps"),
    }[component]
    nested_keys = {
        "east": ("east_mps", "velocity_east_mps", "vx_mps", "x_mps", "ve_mps"),
        "north": ("north_mps", "velocity_north_mps", "vy_mps", "y_mps", "vn_mps"),
        "up": ("up_mps", "velocity_up_mps", "vz_mps", "z_mps", "vu_mps"),
    }[component]
    measurement = result.get("measurement") if isinstance(result.get("measurement"), dict) else {}
    position = result.get("estimated_position") if isinstance(result.get("estimated_position"), dict) else {}
    for source in (measurement, position, result):
        if not isinstance(source, dict):
            continue
        for key in direct_keys:
            value = _optional_float(source.get(key))
            if value is not None:
                return value
        for nested_key in ("velocity", "linear_velocity", "twist_linear"):
            nested = source.get(nested_key)
            if not isinstance(nested, dict):
                continue
            frame = _optional_str(nested.get("frame"))
            if frame is not None and frame != "local_enu":
                continue
            for key in nested_keys:
                value = _optional_float(nested.get(key))
                if value is not None:
                    return value
    return None


def _local_enu_velocity_covariance(result: dict[str, Any]) -> ExternalVelocityCovariance:
    measurement = result.get("measurement") if isinstance(result.get("measurement"), dict) else {}
    position = result.get("estimated_position") if isinstance(result.get("estimated_position"), dict) else {}
    for source in (measurement, position, result):
        if not isinstance(source, dict):
            continue
        direct = source.get("velocity_covariance")
        if isinstance(direct, dict):
            frame = _optional_str(direct.get("frame"))
            if frame is None or frame == "local_enu":
                covariance = ExternalVelocityCovariance.from_local_enu_mapping(direct)
                if covariance.has_any():
                    return covariance
        for nested_key in ("velocity", "linear_velocity", "twist_linear"):
            nested = source.get(nested_key)
            if not isinstance(nested, dict):
                continue
            frame = _optional_str(nested.get("frame"))
            if frame is not None and frame != "local_enu":
                continue
            covariance = nested.get("covariance")
            if isinstance(covariance, dict):
                frame = _optional_str(covariance.get("frame"))
                if frame is not None and frame != "local_enu":
                    continue
                parsed = ExternalVelocityCovariance.from_local_enu_mapping(covariance)
                if parsed.has_any():
                    return parsed
    return ExternalVelocityCovariance()


def _reset_epoch_from_result(result: dict[str, Any]) -> int | None:
    for value in (
        result.get("reset_counter"),
        result.get("estimator_reset_counter"),
        (result.get("estimator") or {}).get("reset_counter") if isinstance(result.get("estimator"), dict) else None,
        (result.get("estimator") or {}).get("reset_epoch") if isinstance(result.get("estimator"), dict) else None,
    ):
        output = _optional_int(value)
        if output is not None:
            return output
    return None


def _defaulted_variance(value: float | None, default: float) -> float:
    if value is None or not math.isfinite(value) or value < 0.0:
        return float(default)
    return float(value)


def _nan_if_none(value: float | None) -> float:
    if value is None or not math.isfinite(value):
        return math.nan
    return float(value)
