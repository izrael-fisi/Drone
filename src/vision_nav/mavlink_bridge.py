from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
import time
from pathlib import Path
from typing import Any

from vision_nav.external_position import (
    OdometryResetTracker,
    build_odometry_payload,
    build_vision_position_estimate_payload,
    external_position_from_match_result,
)


@dataclass(frozen=True)
class MavlinkSendResult:
    sent: bool
    reason: str | None = None
    message: str | None = None
    details: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sent": self.sent,
            "reason": self.reason,
            "message": self.message,
            "details": self.details or {},
        }


@dataclass(frozen=True)
class MavlinkTelemetrySample:
    message_type: str
    timestamp_us: int
    roll_rad: float | None = None
    pitch_rad: float | None = None
    yaw_rad: float | None = None
    local_north_m: float | None = None
    local_east_m: float | None = None
    local_down_m: float | None = None
    pressure_altitude_m: float | None = None
    relative_altitude_m: float | None = None
    pressure_hpa: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_type": self.message_type,
            "timestamp_us": self.timestamp_us,
            "roll_rad": self.roll_rad,
            "pitch_rad": self.pitch_rad,
            "yaw_rad": self.yaw_rad,
            "local_north_m": self.local_north_m,
            "local_east_m": self.local_east_m,
            "local_down_m": self.local_down_m,
            "pressure_altitude_m": self.pressure_altitude_m,
            "relative_altitude_m": self.relative_altitude_m,
            "pressure_hpa": self.pressure_hpa,
        }


def parse_mavlink_endpoint(endpoint: str) -> tuple[str, int | None]:
    value = endpoint.strip()
    if not value:
        raise ValueError("MAVLink endpoint is empty")

    if value.startswith("serial:"):
        parts = value.split(":")
        if len(parts) != 3:
            raise ValueError("serial endpoint must look like serial:/dev/ttyAMA0:921600")
        return parts[1], int(parts[2])

    if value.startswith(("udpout:", "udpin:", "tcp:", "tcpin:")):
        return value, None

    if value.startswith("udp:"):
        parts = value.split(":")
        if len(parts) == 2:
            return f"udpout:127.0.0.1:{parts[1]}", None
        if len(parts) == 3:
            return f"udpout:{parts[1]}:{parts[2]}", None
        raise ValueError("udp endpoint must look like udp:14550 or udp:host:14550")

    raise ValueError(
        "Unsupported MAVLink endpoint. Use serial:/dev/ttyAMA0:921600, udp:14550, udp:host:14550, or tcp:host:port."
    )


class MavlinkVisionBridge:
    def __init__(
        self,
        endpoint: str,
        *,
        system_id: int = 1,
        component_id: int = 197,
        source_system: int = 42,
        source_component: int = 197,
        ev_delay_ms: int = 50,
    ) -> None:
        self.endpoint = endpoint
        self.system_id = system_id
        self.component_id = component_id
        self.source_system = source_system
        self.source_component = source_component
        self.ev_delay_ms = ev_delay_ms
        self._conn = None
        self._last_heartbeat_s = 0.0
        self._odometry_reset_tracker = OdometryResetTracker()

    def connect(self) -> None:
        try:
            from pymavlink import mavutil
        except ImportError as exc:
            raise RuntimeError("pymavlink is required for MAVLink output. Install drone-vision-nav[mavlink].") from exc

        conn_str, baud = parse_mavlink_endpoint(self.endpoint)
        kwargs: dict[str, Any] = {
            "source_system": self.source_system,
            "source_component": self.source_component,
        }
        if baud is not None:
            kwargs["baud"] = baud
        self._conn = mavutil.mavlink_connection(conn_str, **kwargs)

    def close(self) -> None:
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def send_heartbeat_if_due(self) -> None:
        if self._conn is None:
            return
        now = time.monotonic()
        if now - self._last_heartbeat_s < 1.0:
            return
        from pymavlink import mavutil

        self._conn.mav.heartbeat_send(
            mavutil.mavlink.MAV_TYPE_ONBOARD_CONTROLLER,
            mavutil.mavlink.MAV_AUTOPILOT_INVALID,
            0,
            0,
            0,
        )
        self._last_heartbeat_s = now

    def try_read_telemetry(self, timeout_s: float = 0.0) -> MavlinkTelemetrySample | None:
        """Read one non-blocking PX4 telemetry sample when the link is bidirectional.

        Terrain matching does not require inbound MAVLink, but this helper lets
        runtime loops opportunistically consume attitude/local-state messages.
        """

        if self._conn is None:
            return None
        message = self._conn.recv_match(
            type=["ATTITUDE", "LOCAL_POSITION_NED", "ALTITUDE", "SCALED_PRESSURE"],
            blocking=timeout_s > 0.0,
            timeout=timeout_s,
        )
        if message is None:
            return None
        message_type = message.get_type()
        timestamp_us = int(time.time() * 1_000_000)
        if message_type == "ATTITUDE":
            return MavlinkTelemetrySample(
                message_type=message_type,
                timestamp_us=timestamp_us,
                roll_rad=_as_optional_float(getattr(message, "roll", None)),
                pitch_rad=_as_optional_float(getattr(message, "pitch", None)),
                yaw_rad=_as_optional_float(getattr(message, "yaw", None)),
            )
        if message_type == "LOCAL_POSITION_NED":
            return MavlinkTelemetrySample(
                message_type=message_type,
                timestamp_us=timestamp_us,
                local_north_m=_as_optional_float(getattr(message, "x", None)),
                local_east_m=_as_optional_float(getattr(message, "y", None)),
                local_down_m=_as_optional_float(getattr(message, "z", None)),
            )
        if message_type == "ALTITUDE":
            return MavlinkTelemetrySample(
                message_type=message_type,
                timestamp_us=timestamp_us,
                pressure_altitude_m=_as_optional_float(getattr(message, "altitude_monotonic", None)),
                relative_altitude_m=_as_optional_float(getattr(message, "altitude_relative", None)),
            )
        if message_type == "SCALED_PRESSURE":
            return MavlinkTelemetrySample(
                message_type=message_type,
                timestamp_us=timestamp_us,
                pressure_hpa=_as_optional_float(getattr(message, "press_abs", None)),
            )
        return MavlinkTelemetrySample(message_type=message_type, timestamp_us=timestamp_us)

    def send_match_result(
        self,
        result: dict[str, Any],
        *,
        message_type: str = "vision_position_estimate",
    ) -> MavlinkSendResult:
        if message_type == "odometry":
            return self.send_odometry_match_result(result)
        if message_type != "vision_position_estimate":
            return MavlinkSendResult(False, reason="unsupported_mavlink_message")
        if self._conn is None:
            return MavlinkSendResult(False, reason="not_connected")
        self.send_heartbeat_if_due()

        if result.get("status") != "accepted":
            return MavlinkSendResult(False, reason="match_not_accepted")

        time_usec = int(time.time() * 1_000_000) - int(self.ev_delay_ms * 1000)
        estimate, reason = external_position_from_match_result(result)
        if estimate is None:
            return MavlinkSendResult(False, reason=reason)
        payload = build_vision_position_estimate_payload(estimate, time_usec=time_usec)
        self._conn.mav.vision_position_estimate_send(*payload.to_mavlink_args())
        return MavlinkSendResult(True, message="VISION_POSITION_ESTIMATE")

    def send_odometry_match_result(self, result: dict[str, Any]) -> MavlinkSendResult:
        if self._conn is None:
            return MavlinkSendResult(False, reason="not_connected")
        self.send_heartbeat_if_due()

        estimate, reason = external_position_from_match_result(result)
        if estimate is None:
            return MavlinkSendResult(False, reason=reason)

        time_usec = int(time.time() * 1_000_000) - int(self.ev_delay_ms * 1000)
        reset_counter = self._odometry_reset_tracker.update_from_result(result)
        payload = build_odometry_payload(estimate, time_usec=time_usec, reset_counter=reset_counter)
        self._conn.mav.odometry_send(
            payload.time_usec,
            _mavlink_enum_value(payload.frame_id),
            _mavlink_enum_value(payload.child_frame_id),
            payload.x_m,
            payload.y_m,
            payload.z_m,
            list(payload.q),
            payload.vx_mps,
            payload.vy_mps,
            payload.vz_mps,
            payload.rollspeed_radps,
            payload.pitchspeed_radps,
            payload.yawspeed_radps,
            payload.pose_covariance_urt,
            payload.velocity_covariance_urt,
            payload.reset_counter,
            _mavlink_enum_value(payload.estimator_type),
            payload.quality,
        )
        return MavlinkSendResult(True, message="ODOMETRY", details={"reset_counter": reset_counter})

def _as_optional_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        output = float(value)
        return output if math.isfinite(output) else None
    except (TypeError, ValueError):
        return None


def _mavlink_enum_value(name: str) -> int:
    defaults = {
        "MAV_FRAME_LOCAL_FRD": 20,
        "MAV_FRAME_BODY_FRD": 12,
        "MAV_ESTIMATOR_TYPE_VISION": 2,
    }
    try:
        from pymavlink import mavutil
    except ImportError:
        return defaults[name]
    return int(getattr(mavutil.mavlink, name, defaults[name]))


def send_log_once(log_path: str, endpoint: str, ev_delay_ms: int = 50) -> dict[str, Any]:
    bridge = MavlinkVisionBridge(endpoint, ev_delay_ms=ev_delay_ms)
    bridge.connect()
    sent = 0
    skipped = 0
    try:
        for line in Path(log_path).read_text().splitlines():
            if not line.strip():
                continue
            record = json.loads(line)
            result = record.get("result", record)
            send_result = bridge.send_match_result(result)
            if send_result.sent:
                sent += 1
            else:
                skipped += 1
    finally:
        bridge.close()
    return {"log_path": log_path, "endpoint": endpoint, "sent": sent, "skipped": skipped}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Send accepted vision-nav match log entries over MAVLink once.")
    parser.add_argument("--log", required=True, help="matches.jsonl path.")
    parser.add_argument("--endpoint", required=True, help="MAVLink endpoint such as udp:14550 or serial:/dev/ttyAMA0:921600.")
    parser.add_argument("--ev-delay-ms", type=int, default=50)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    print(json.dumps(send_log_once(args.log, args.endpoint, args.ev_delay_ms), indent=2))


if __name__ == "__main__":
    main()
