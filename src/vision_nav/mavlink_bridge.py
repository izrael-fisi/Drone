from __future__ import annotations

from dataclasses import dataclass
import json
import math
import os
from statistics import median
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
    # Host Unix time captured when the message was received. This is useful for
    # audit logs, but host monotonic time is used for camera/telemetry pairing.
    timestamp_us: int
    # PX4/MAVLink message time is retained verbatim with its stated domain. It
    # is not silently converted to wall time because that would imply a clock
    # synchronization guarantee we do not have on a bench or field vehicle.
    vehicle_timestamp_us: int | None = None
    vehicle_time_domain: str | None = None
    roll_rad: float | None = None
    pitch_rad: float | None = None
    yaw_rad: float | None = None
    local_north_m: float | None = None
    local_east_m: float | None = None
    local_down_m: float | None = None
    gps_lat: float | None = None
    gps_lon: float | None = None
    gps_alt_m: float | None = None
    gps_fix_type: int | None = None
    gps_satellites_visible: int | None = None
    gps_eph_m: float | None = None
    gps_epv_m: float | None = None
    gps_h_acc_m: float | None = None
    gps_v_acc_m: float | None = None
    global_alt_m: float | None = None
    pressure_altitude_m: float | None = None
    relative_altitude_m: float | None = None
    pressure_hpa: float | None = None
    acceleration_m_s2: tuple[float, float, float] | None = None
    angular_velocity_rad_s: tuple[float, float, float] | None = None
    imu_sensor_id: int | None = None
    imu_frame: str | None = None
    imu_fields_updated: int | None = None
    source_system: int | None = None
    source_component: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "message_type": self.message_type,
            "timestamp_us": self.timestamp_us,
            "vehicle_timestamp_us": self.vehicle_timestamp_us,
            "vehicle_time_domain": self.vehicle_time_domain,
            "roll_rad": self.roll_rad,
            "pitch_rad": self.pitch_rad,
            "yaw_rad": self.yaw_rad,
            "local_north_m": self.local_north_m,
            "local_east_m": self.local_east_m,
            "local_down_m": self.local_down_m,
            "gps_lat": self.gps_lat,
            "gps_lon": self.gps_lon,
            "gps_alt_m": self.gps_alt_m,
            "gps_fix_type": self.gps_fix_type,
            "gps_satellites_visible": self.gps_satellites_visible,
            "gps_eph_m": self.gps_eph_m,
            "gps_epv_m": self.gps_epv_m,
            "gps_h_acc_m": self.gps_h_acc_m,
            "gps_v_acc_m": self.gps_v_acc_m,
            "global_alt_m": self.global_alt_m,
            "pressure_altitude_m": self.pressure_altitude_m,
            "relative_altitude_m": self.relative_altitude_m,
            "pressure_hpa": self.pressure_hpa,
            "acceleration_m_s2": list(self.acceleration_m_s2) if self.acceleration_m_s2 is not None else None,
            "angular_velocity_rad_s": list(self.angular_velocity_rad_s) if self.angular_velocity_rad_s is not None else None,
            "imu_sensor_id": self.imu_sensor_id,
            "imu_frame": self.imu_frame,
            "imu_fields_updated": self.imu_fields_updated,
            "source_system": self.source_system,
            "source_component": self.source_component,
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
        self._timesync_recording: dict[str, Any] | None = None

    def connect(self) -> None:
        try:
            mavutil = _load_mavutil()
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
        self._timesync_recording = None
        if self._conn is not None:
            self._conn.close()
            self._conn = None

    def send_heartbeat_if_due(self) -> None:
        if self._conn is None:
            return
        now = time.monotonic()
        if now - self._last_heartbeat_s < 1.0:
            return
        mavutil = _load_mavutil()

        self._conn.mav.heartbeat_send(
            mavutil.mavlink.MAV_TYPE_ONBOARD_CONTROLLER,
            mavutil.mavlink.MAV_AUTOPILOT_INVALID,
            0,
            0,
            0,
        )
        self._last_heartbeat_s = now

    def start_timesync_recording(self, *, interval_s: float = 1.0, timeout_s: float = 0.25,
                                max_exchanges: int = 36000) -> None:
        """Enable diagnostic probes multiplexed by the sole telemetry reader.

        Call before starting the reader thread. This creates no second serial
        reader and sends no vehicle commands or parameter changes.
        """
        if self._timesync_recording is not None:
            raise RuntimeError("TIMESYNC recording already active")
        if not all(math.isfinite(v) and v > 0 for v in (interval_s, timeout_s)) or timeout_s >= interval_s:
            raise ValueError("TIMESYNC requires 0 < timeout_s < interval_s")
        if type(max_exchanges) is not int or max_exchanges <= 0:
            raise ValueError("max_exchanges must be a positive integer")
        self._timesync_recording = {"exchanges": [], "interval_ns": round(interval_s * 1e9),
                                    "timeout_ns": round(timeout_s * 1e9), "pending_ns": None,
                                    "last_request_ns": None, "max_exchanges": max_exchanges,
                                    "capacity_reached": False}

    def _poll_timesync_recording(self, now_ns: int) -> None:
        state = self._timesync_recording
        if state is None:
            return
        pending = state["pending_ns"]
        if pending is not None and now_ns - pending >= state["timeout_ns"]:
            state["exchanges"].append({"status": "rejected", "reason": "timesync_response_timeout",
                                       "host_sent_monotonic_ns": pending})
            state["pending_ns"] = None
        if len(state["exchanges"]) >= state["max_exchanges"]:
            state["capacity_reached"] = True
            return
        if state["pending_ns"] is not None or (state["last_request_ns"] is not None and
                                               now_ns - state["last_request_ns"] < state["interval_ns"]):
            return
        state["last_request_ns"] = now_ns
        try:
            self._conn.mav.timesync_send(0, now_ns)
        except Exception as exc:
            state["exchanges"].append({"status": "rejected", "reason": f"timesync_request_failed:{type(exc).__name__}",
                                       "host_sent_monotonic_ns": now_ns})
        else:
            state["pending_ns"] = now_ns

    def _consume_timesync_response(self, message: Any, received_ns: int) -> None:
        state = self._timesync_recording
        if state is None or state["pending_ns"] is None:
            return
        sent = state["pending_ns"]
        if getattr(message, "ts1", None) != sent:
            return
        target_system, target_component = getattr(message, "target_system", 0), getattr(message, "target_component", 0)
        if target_system not in (0, self.source_system) or target_component not in (0, self.source_component):
            return
        remote = _as_optional_int(getattr(message, "tc1", None))
        if remote is None or remote <= 0:
            return
        state["exchanges"].append({"status": "observed", "host_sent_monotonic_ns": sent,
            "host_received_monotonic_ns": received_ns, "px4_timestamp_ns": remote,
            "source_system": message.get_srcSystem() if hasattr(message, "get_srcSystem") else None,
            "source_component": message.get_srcComponent() if hasattr(message, "get_srcComponent") else None,
            "response_target_system": target_system, "response_target_component": target_component})
        state["pending_ns"] = None

    def stop_timesync_recording(self) -> dict[str, Any]:
        """Snapshot after joining the reader thread; unfinished probes stay explicit."""
        state = self._timesync_recording
        self._timesync_recording = None
        if state is None:
            return {"status": "not_configured", "exchanges": []}
        if state["pending_ns"] is not None:
            state["exchanges"].append({"status": "rejected", "reason": "capture_ended_before_response",
                                       "host_sent_monotonic_ns": state["pending_ns"]})
        return {"schema_version": "vision_nav_mavlink_periodic_timesync_v1", "status": "recorded",
                "interval_s": state["interval_ns"] / 1e9, "exchanges": state["exchanges"],
                "capacity_reached": state["capacity_reached"], "max_exchanges": state["max_exchanges"]}

    def read_timesync_recording(self, cursor: int = 0) -> dict[str, Any]:
        """Copy new exchanges on the sole telemetry reader's thread.

        This sends nothing and does not reset the pending request or truncate
        evidence retained for stop_timesync_recording. Not a second serial read.
        """
        state = self._timesync_recording
        if state is None:
            raise RuntimeError("TIMESYNC recording is not active")
        if type(cursor) is not int or not 0 <= cursor <= len(state["exchanges"]):
            raise ValueError("Invalid TIMESYNC evidence cursor")
        return {"next_cursor": len(state["exchanges"]),
                "exchanges": [dict(row) for row in state["exchanges"][cursor:]],
                "capacity_reached": state["capacity_reached"]}

    def capture_timesync_evidence(
        self,
        *,
        sample_count: int = 8,
        timeout_s: float = 0.25,
        max_round_trip_ms: float = 50.0,
    ) -> dict[str, Any]:
        """Measure PX4-to-host clock-offset evidence with MAVLink TIMESYNC.

        This is a bounded diagnostics exchange, not a vehicle command. It
        follows the MAVLink v2 request/response sequence: the Pi sends its
        monotonic timestamp in ``ts1`` with ``tc1=0``; PX4 mirrors ``ts1`` and
        returns its timestamp in ``tc1``. The result is deliberately evidence,
        not a claim that the camera sensor clock has been synchronized to PX4.
        """

        if sample_count <= 0:
            raise ValueError("sample_count must be positive.")
        if timeout_s <= 0.0:
            raise ValueError("timeout_s must be positive.")
        if max_round_trip_ms <= 0.0:
            raise ValueError("max_round_trip_ms must be positive.")
        if self._conn is None:
            return summarize_timesync_exchanges(
                [],
                requested_sample_count=sample_count,
                max_round_trip_ms=max_round_trip_ms,
                transport_error="mavlink_not_connected",
            )
        if not hasattr(self._conn.mav, "timesync_send"):
            return summarize_timesync_exchanges(
                [],
                requested_sample_count=sample_count,
                max_round_trip_ms=max_round_trip_ms,
                transport_error="timesync_message_unsupported_by_mavlink_dialect",
            )

        exchanges: list[dict[str, Any]] = []
        for _ in range(sample_count):
            host_sent_ns = time.monotonic_ns()
            try:
                self._conn.mav.timesync_send(0, host_sent_ns)
            except Exception as exc:
                exchanges.append(
                    {
                        "status": "rejected",
                        "reason": f"timesync_request_failed:{type(exc).__name__}",
                        "host_sent_monotonic_ns": host_sent_ns,
                    }
                )
                continue

            deadline = time.monotonic() + timeout_s
            accepted_response = False
            while time.monotonic() < deadline:
                remaining_s = max(0.0, deadline - time.monotonic())
                message = self._conn.recv_match(type="TIMESYNC", blocking=True, timeout=remaining_s)
                if message is None:
                    break
                host_received_ns = time.monotonic_ns()
                returned_ts1 = _as_optional_int(getattr(message, "ts1", None))
                remote_tc1 = _as_optional_int(getattr(message, "tc1", None))
                if returned_ts1 != host_sent_ns:
                    # Other MAVLink clients can have an in-flight TIMESYNC
                    # request. It is not evidence for this Pi request.
                    continue
                response_target_system = getattr(message, "target_system", 0)
                response_target_component = getattr(message, "target_component", 0)
                if response_target_system not in (0, self.source_system) or response_target_component not in (0, self.source_component):
                    continue
                if remote_tc1 is None or remote_tc1 <= 0:
                    exchanges.append(
                        {
                            "status": "rejected",
                            "reason": "timesync_response_missing_remote_timestamp",
                            "host_sent_monotonic_ns": host_sent_ns,
                            "host_received_monotonic_ns": host_received_ns,
                        }
                    )
                else:
                    exchanges.append(
                        {
                            "status": "observed",
                            "host_sent_monotonic_ns": host_sent_ns,
                            "host_received_monotonic_ns": host_received_ns,
                            "px4_timestamp_ns": remote_tc1,
                            "source_system": message.get_srcSystem() if hasattr(message, "get_srcSystem") else None,
                            "source_component": message.get_srcComponent() if hasattr(message, "get_srcComponent") else None,
                            "response_target_system": response_target_system,
                            "response_target_component": response_target_component,
                        }
                    )
                accepted_response = True
                break
            if not accepted_response:
                exchanges.append(
                    {
                        "status": "rejected",
                        "reason": "timesync_response_timeout",
                        "host_sent_monotonic_ns": host_sent_ns,
                    }
                )
        return summarize_timesync_exchanges(
            exchanges,
            requested_sample_count=sample_count,
            max_round_trip_ms=max_round_trip_ms,
        )

    def try_read_telemetry(self, timeout_s: float = 0.0) -> MavlinkTelemetrySample | None:
        """Read one non-blocking PX4 telemetry sample when the link is bidirectional.

        Terrain matching does not require inbound MAVLink, but this helper lets
        runtime loops opportunistically consume attitude/local-state messages.
        """

        if self._conn is None:
            return None
        self._poll_timesync_recording(time.monotonic_ns())
        message_types = ["HEARTBEAT", "ATTITUDE", "LOCAL_POSITION_NED", "GLOBAL_POSITION_INT", "GPS_RAW_INT", "ALTITUDE", "SCALED_PRESSURE",
                         "HIGHRES_IMU", "SCALED_IMU", "SCALED_IMU2", "SCALED_IMU3"]
        if self._timesync_recording is not None:
            message_types.append("TIMESYNC")
        message = self._conn.recv_match(
            type=message_types,
            blocking=timeout_s > 0.0,
            timeout=timeout_s,
        )
        if message is None:
            return None
        message_type = message.get_type()
        if message_type == "TIMESYNC":
            self._consume_timesync_response(message, time.monotonic_ns())
            return None
        timestamp_us = int(time.time() * 1_000_000)
        vehicle_timestamp_us, vehicle_time_domain = _message_vehicle_timestamp(message, message_type)
        if message_type in {"HIGHRES_IMU", "SCALED_IMU", "SCALED_IMU2", "SCALED_IMU3"}:
            highres = message_type == "HIGHRES_IMU"
            def vector(suffix: str, scale: float) -> tuple[float, float, float] | None:
                values = [_as_optional_float(getattr(message, axis + suffix, None)) for axis in "xyz"]
                return tuple(value * scale for value in values) if all(value is not None for value in values) else None

            return MavlinkTelemetrySample(
                message_type=message_type, timestamp_us=timestamp_us, vehicle_timestamp_us=vehicle_timestamp_us,
                vehicle_time_domain=vehicle_time_domain,
                acceleration_m_s2=vector("acc", 1.0 if highres else 9.80665 / 1000),
                angular_velocity_rad_s=vector("gyro", 1.0 if highres else 1.0 / 1000),
                imu_sensor_id=(_as_optional_int(getattr(message, "id", None)) if highres else
                               {"SCALED_IMU": 0, "SCALED_IMU2": 1, "SCALED_IMU3": 2}[message_type]),
                imu_frame="body_frd" if highres else "sender_axes_unverified",
                imu_fields_updated=_as_optional_int(getattr(message, "fields_updated", None)) if highres else None,
                source_system=message.get_srcSystem() if hasattr(message, "get_srcSystem") else None,
                source_component=message.get_srcComponent() if hasattr(message, "get_srcComponent") else None,
            )
        if message_type == "ATTITUDE":
            return MavlinkTelemetrySample(
                message_type=message_type,
                timestamp_us=timestamp_us,
                vehicle_timestamp_us=vehicle_timestamp_us,
                vehicle_time_domain=vehicle_time_domain,
                roll_rad=_as_optional_float(getattr(message, "roll", None)),
                pitch_rad=_as_optional_float(getattr(message, "pitch", None)),
                yaw_rad=_as_optional_float(getattr(message, "yaw", None)),
                source_system=message.get_srcSystem() if hasattr(message, "get_srcSystem") else None,
                source_component=message.get_srcComponent() if hasattr(message, "get_srcComponent") else None,
            )
        if message_type == "LOCAL_POSITION_NED":
            return MavlinkTelemetrySample(
                message_type=message_type,
                timestamp_us=timestamp_us,
                vehicle_timestamp_us=vehicle_timestamp_us,
                vehicle_time_domain=vehicle_time_domain,
                local_north_m=_as_optional_float(getattr(message, "x", None)),
                local_east_m=_as_optional_float(getattr(message, "y", None)),
                local_down_m=_as_optional_float(getattr(message, "z", None)),
            )
        if message_type == "GLOBAL_POSITION_INT":
            return MavlinkTelemetrySample(
                message_type=message_type,
                timestamp_us=timestamp_us,
                vehicle_timestamp_us=vehicle_timestamp_us,
                vehicle_time_domain=vehicle_time_domain,
                gps_lat=_scaled_int(getattr(message, "lat", None), 1e7),
                gps_lon=_scaled_int(getattr(message, "lon", None), 1e7),
                global_alt_m=_scaled_int(getattr(message, "alt", None), 1000.0),
                relative_altitude_m=_scaled_int(getattr(message, "relative_alt", None), 1000.0),
            )
        if message_type == "GPS_RAW_INT":
            return MavlinkTelemetrySample(
                message_type=message_type,
                timestamp_us=timestamp_us,
                vehicle_timestamp_us=vehicle_timestamp_us,
                vehicle_time_domain=vehicle_time_domain,
                gps_lat=_scaled_int(getattr(message, "lat", None), 1e7),
                gps_lon=_scaled_int(getattr(message, "lon", None), 1e7),
                gps_alt_m=_scaled_int(getattr(message, "alt", None), 1000.0),
                gps_fix_type=_as_optional_int(getattr(message, "fix_type", None)),
                gps_satellites_visible=_as_optional_int(getattr(message, "satellites_visible", None)),
                gps_eph_m=_scaled_int(getattr(message, "eph", None), 100.0),
                gps_epv_m=_scaled_int(getattr(message, "epv", None), 100.0),
                gps_h_acc_m=_scaled_int(getattr(message, "h_acc", None), 1000.0),
                gps_v_acc_m=_scaled_int(getattr(message, "v_acc", None), 1000.0),
            )
        if message_type == "ALTITUDE":
            return MavlinkTelemetrySample(
                message_type=message_type,
                timestamp_us=timestamp_us,
                vehicle_timestamp_us=vehicle_timestamp_us,
                vehicle_time_domain=vehicle_time_domain,
                pressure_altitude_m=_as_optional_float(getattr(message, "altitude_monotonic", None)),
                relative_altitude_m=_as_optional_float(getattr(message, "altitude_relative", None)),
            )
        if message_type == "SCALED_PRESSURE":
            return MavlinkTelemetrySample(
                message_type=message_type,
                timestamp_us=timestamp_us,
                vehicle_timestamp_us=vehicle_timestamp_us,
                vehicle_time_domain=vehicle_time_domain,
                pressure_hpa=_as_optional_float(getattr(message, "press_abs", None)),
            )
        return MavlinkTelemetrySample(
            message_type=message_type,
            timestamp_us=timestamp_us,
            vehicle_timestamp_us=vehicle_timestamp_us,
            vehicle_time_domain=vehicle_time_domain,
        )

    def send_match_result(
        self,
        result: dict[str, Any],
        *,
        message_type: str = "odometry",
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
        if not hasattr(self._conn.mav, "odometry_send"):
            try:
                _load_mavutil(require_odometry=True)
            except (ImportError, RuntimeError) as exc:
                return MavlinkSendResult(False, reason="mavlink_odometry_unsupported", message=str(exc))
        if not hasattr(self._conn.mav, "odometry_send"):
            return MavlinkSendResult(
                False,
                reason="mavlink_odometry_unsupported",
                message="Connected MAVLink dialect does not support ODOMETRY. Set MAVLINK20=1 before connecting.",
            )
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
        return MavlinkSendResult(
            True,
            message="ODOMETRY",
            details={
                "reset_counter": reset_counter,
                "has_velocity": any(math.isfinite(value) for value in (payload.vx_mps, payload.vy_mps, payload.vz_mps)),
                "has_velocity_covariance": any(math.isfinite(value) for value in payload.velocity_covariance_urt),
            },
        )


def summarize_timesync_exchanges(
    exchanges: list[dict[str, Any]],
    *,
    requested_sample_count: int,
    max_round_trip_ms: float,
    min_accepted_sample_count: int = 3,
    transport_error: str | None = None,
) -> dict[str, Any]:
    """Summarize raw TIMESYNC exchanges into immutable capture evidence."""

    if requested_sample_count <= 0:
        raise ValueError("requested_sample_count must be positive.")
    if min_accepted_sample_count <= 0:
        raise ValueError("min_accepted_sample_count must be positive.")
    if max_round_trip_ms <= 0.0:
        raise ValueError("max_round_trip_ms must be positive.")

    accepted: list[dict[str, Any]] = []
    rejection_counts: dict[str, int] = {}
    normalized: list[dict[str, Any]] = []
    for exchange in exchanges:
        sent = exchange.get("host_sent_monotonic_ns")
        received = exchange.get("host_received_monotonic_ns")
        px4_timestamp = exchange.get("px4_timestamp_ns")
        if not all(isinstance(value, int) and value > 0 for value in (sent, received, px4_timestamp)):
            reason = str(exchange.get("reason") or "invalid_timesync_exchange")
            rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
            normalized.append({"status": "rejected", "reason": reason})
            continue
        if received < sent:
            reason = "timesync_negative_round_trip"
            rejection_counts[reason] = rejection_counts.get(reason, 0) + 1
            normalized.append({"status": "rejected", "reason": reason})
            continue
        round_trip_ms = (received - sent) / 1_000_000.0
        midpoint_ns = (sent + received) // 2
        remote_minus_host_midpoint_ns = px4_timestamp - midpoint_ns
        entry = {
            "status": "accepted" if round_trip_ms <= max_round_trip_ms else "rejected",
            "host_sent_monotonic_ns": sent,
            "host_received_monotonic_ns": received,
            "px4_timestamp_ns": px4_timestamp,
            "round_trip_ms": round(round_trip_ms, 3),
            "px4_minus_host_midpoint_ns": remote_minus_host_midpoint_ns,
            "source_system": exchange.get("source_system"),
            "source_component": exchange.get("source_component"),
            "response_target_system": exchange.get("response_target_system"),
            "response_target_component": exchange.get("response_target_component"),
        }
        if round_trip_ms > max_round_trip_ms:
            entry["reason"] = "timesync_round_trip_exceeds_limit"
            rejection_counts[entry["reason"]] = rejection_counts.get(entry["reason"], 0) + 1
        else:
            accepted.append(entry)
        normalized.append(entry)

    round_trips = [float(entry["round_trip_ms"]) for entry in accepted]
    offsets = [int(entry["px4_minus_host_midpoint_ns"]) for entry in accepted]
    offset_median_ns = int(median(offsets)) if offsets else None
    offset_max_deviation_ns = max(abs(value - offset_median_ns) for value in offsets) if offsets and offset_median_ns is not None else None
    errors: list[str] = []
    if transport_error:
        errors.append(transport_error)
    if len(accepted) < min_accepted_sample_count:
        errors.append("insufficient_low_latency_timesync_samples")
    return {
        "schema_version": "vision_nav_mavlink_timesync_audit_v1",
        "status": "available" if not errors else "unavailable",
        "policy": "capture_timing_evidence_only",
        "requirements": {
            "requested_sample_count": requested_sample_count,
            "min_accepted_sample_count": min_accepted_sample_count,
            "max_round_trip_ms": max_round_trip_ms,
        },
        "observed_sample_count": len(exchanges),
        "accepted_sample_count": len(accepted),
        "rejected_sample_count": len(normalized) - len(accepted),
        "rejection_counts": rejection_counts,
        "round_trip_median_ms": round(float(median(round_trips)), 3) if round_trips else None,
        "round_trip_max_ms": round(max(round_trips), 3) if round_trips else None,
        "px4_minus_host_midpoint_median_ns": offset_median_ns,
        "px4_minus_host_midpoint_max_deviation_ns": offset_max_deviation_ns,
        "exchanges": normalized,
        "errors": errors,
    }


def _message_vehicle_timestamp(message: Any, message_type: str) -> tuple[int | None, str | None]:
    """Return MAVLink-native time without inventing a clock conversion.

    PX4 commonly supplies ``time_boot_ms`` for attitude/local-state messages.
    Some MAVLink messages expose ``time_usec`` whose epoch is explicitly
    ambiguous. The magnitude tells us when it is Unix epoch time; otherwise we
    preserve the ambiguity so downstream capture tooling can still retain the
    evidence without treating it as synchronized wall time.
    """

    boot_ms = _as_optional_int(getattr(message, "time_boot_ms", None))
    if boot_ms is not None and boot_ms >= 0:
        return boot_ms * 1_000, "px4_boot_us"

    time_usec = _as_optional_int(getattr(message, "time_usec", None))
    if time_usec is None or time_usec < 0:
        return None, None
    if time_usec >= 100_000_000_000_000:
        return time_usec, "unix_epoch_us"
    return time_usec, f"{message_type.lower()}_boot_or_unknown_us"


def _as_optional_float(value: Any) -> float | None:
    try:
        if value is None:
            return None
        output = float(value)
        return output if math.isfinite(output) else None
    except (TypeError, ValueError):
        return None


def _as_optional_int(value: Any) -> int | None:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _scaled_int(value: Any, scale: float) -> float | None:
    number = _as_optional_float(value)
    if number is None:
        return None
    if abs(number) >= 2147483647:
        return None
    return number / scale


def _load_mavutil(*, require_odometry: bool = False) -> Any:
    os.environ.setdefault("MAVLINK20", "1")
    from pymavlink import mavutil

    if require_odometry and not hasattr(mavutil.mavlink.MAVLink, "odometry_send"):
        mavutil.set_dialect(os.environ.get("MAVLINK_DIALECT", "ardupilotmega"))
    if require_odometry and not hasattr(mavutil.mavlink.MAVLink, "odometry_send"):
        raise RuntimeError(
            "MAVLink ODOMETRY output requires the MAVLink 2 pymavlink dialect. "
            "Run this process with MAVLINK20=1 before importing pymavlink."
        )
    return mavutil


def _mavlink_enum_value(name: str) -> int:
    defaults = {
        "MAV_FRAME_LOCAL_FRD": 20,
        "MAV_FRAME_BODY_FRD": 12,
        "MAV_ESTIMATOR_TYPE_VISION": 2,
    }
    try:
        mavutil = _load_mavutil()
    except ImportError:
        return defaults[name]
    return int(getattr(mavutil.mavlink, name, defaults[name]))


def load_match_log_records(log_path: str | Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(Path(log_path).read_text().splitlines(), start=1):
        if not line.strip():
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError as exc:
            raise ValueError(f"{log_path}:{line_number}: invalid JSONL record") from exc
    return records


def send_records_once(
    records: list[dict[str, Any]],
    bridge: MavlinkVisionBridge,
    *,
    message_type: str = "vision_position_estimate",
    rate_hz: float = 0.0,
    repeat: int = 1,
) -> dict[str, Any]:
    if message_type not in {"vision_position_estimate", "odometry"}:
        raise ValueError("message_type must be vision_position_estimate or odometry")
    repeat_count = max(int(repeat), 1)
    period_s = 1.0 / float(rate_hz) if rate_hz and rate_hz > 0 else 0.0
    sent = 0
    skipped = 0
    reasons: dict[str, int] = {}
    started_s = time.monotonic()
    for _ in range(repeat_count):
        for record in records:
            loop_start = time.monotonic()
            result = record.get("result", record)
            send_result = bridge.send_match_result(result, message_type=message_type)
            if send_result.sent:
                sent += 1
            else:
                skipped += 1
                reason = send_result.reason or "unknown"
                reasons[reason] = reasons.get(reason, 0) + 1
            if period_s > 0:
                sleep_s = period_s - (time.monotonic() - loop_start)
                if sleep_s > 0:
                    time.sleep(sleep_s)
    return {
        "message_type": message_type,
        "record_count": len(records),
        "repeat": repeat_count,
        "sent": sent,
        "skipped": skipped,
        "skip_reasons": reasons,
        "duration_s": round(time.monotonic() - started_s, 3),
        "target_rate_hz": rate_hz,
    }


def send_log_once(
    log_path: str,
    endpoint: str,
    ev_delay_ms: int = 50,
    *,
    message_type: str = "odometry",
    rate_hz: float = 0.0,
    repeat: int = 1,
) -> dict[str, Any]:
    records = load_match_log_records(log_path)
    bridge = MavlinkVisionBridge(endpoint, ev_delay_ms=ev_delay_ms)
    bridge.connect()
    try:
        result = send_records_once(
            records,
            bridge,
            message_type=message_type,
            rate_hz=rate_hz,
            repeat=repeat,
        )
    finally:
        bridge.close()
    result.update({"log_path": log_path, "endpoint": endpoint})
    return result


def main() -> None:
    raise SystemExit(
        "Direct replay-to-PX4 sending is retired. Bind the versioned "
        "VisionNavigationRuntime to Px4VisionBridge after the required safety validation."
    )


if __name__ == "__main__":
    main()
