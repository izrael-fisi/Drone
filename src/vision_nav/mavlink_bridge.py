from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
import math
import time
from pathlib import Path
from typing import Any


@dataclass(frozen=True)
class MavlinkSendResult:
    sent: bool
    reason: str | None = None
    message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "sent": self.sent,
            "reason": self.reason,
            "message": self.message,
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

    def send_match_result(self, result: dict[str, Any]) -> MavlinkSendResult:
        if self._conn is None:
            return MavlinkSendResult(False, reason="not_connected")
        self.send_heartbeat_if_due()

        if result.get("status") != "accepted":
            return MavlinkSendResult(False, reason="match_not_accepted")

        measurement = result.get("measurement") or {}
        position = result.get("estimated_position") or {}
        if measurement.get("frame") != "local_enu":
            return MavlinkSendResult(False, reason="missing_local_enu_measurement")

        east_m = measurement.get("x_m", position.get("east_m"))
        north_m = measurement.get("y_m", position.get("north_m"))
        if east_m is None or north_m is None:
            return MavlinkSendResult(False, reason="missing_local_position")

        covariance = measurement.get("covariance") or {}
        east_var = _as_float(covariance.get("x_m2"), default=25.0)
        north_var = _as_float(covariance.get("y_m2"), default=east_var)
        horizontal_var = max(east_var, north_var)
        z_var = _as_float(covariance.get("z_m2"), default=max(horizontal_var * 4.0, 100.0))
        yaw_var = _as_float(covariance.get("yaw_rad2"), default=math.radians(30.0) ** 2)
        z_m = measurement.get("z_m")

        cov = [0.0] * 21
        cov[0] = north_var
        cov[6] = east_var
        cov[11] = z_var
        cov[20] = yaw_var

        time_usec = int(time.time() * 1_000_000) - int(self.ev_delay_ms * 1000)
        self._conn.mav.vision_position_estimate_send(
            time_usec,
            float(north_m),
            float(east_m),
            float(-(z_m or 0.0)),
            0.0,
            0.0,
            float(measurement.get("yaw_rad") or 0.0),
            cov,
        )
        return MavlinkSendResult(True, message="VISION_POSITION_ESTIMATE")


def _as_float(value: Any, *, default: float) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


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
