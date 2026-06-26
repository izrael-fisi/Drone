from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import json
import math
import socket
from typing import Any


@dataclass(frozen=True)
class GpsHealthConfig:
    min_fix_type: int = 3
    min_satellites: int = 6
    max_eph_m: float = 3.0
    max_h_acc_m: float = 3.0


@dataclass
class FixCadenceTracker:
    last_vision_fix_utc: str | None = None
    last_vision_fix_sequence: int | None = None
    last_vision_lat_lon: dict[str, float | None] | None = None
    last_vision_local_enu_m: dict[str, float | None] | None = None
    seconds_since_vision_fix: float | None = None
    meters_since_vision_fix: float | None = None
    vision_fix_interval_m: float | None = None

    def snapshot(self) -> dict[str, Any]:
        return {
            "last_vision_fix_utc": self.last_vision_fix_utc,
            "last_vision_fix_sequence": self.last_vision_fix_sequence,
            "seconds_since_vision_fix": self.seconds_since_vision_fix,
            "meters_since_vision_fix": self.meters_since_vision_fix,
            "vision_fix_interval_m": self.vision_fix_interval_m,
        }

    def note_packet(
        self,
        *,
        sequence: int,
        timestamp_utc: str,
        source_state: str,
        lat_lon: dict[str, Any],
        local_enu: dict[str, Any],
    ) -> dict[str, Any]:
        timestamp = parse_datetime(timestamp_utc)
        previous_time = parse_datetime(self.last_vision_fix_utc)
        self.seconds_since_vision_fix = (
            max(0.0, (timestamp - previous_time).total_seconds())
            if timestamp is not None and previous_time is not None
            else None
        )
        self.meters_since_vision_fix = distance_from_last_fix(self.last_vision_lat_lon, self.last_vision_local_enu_m, lat_lon, local_enu)
        if source_state == "vision_correction":
            self.vision_fix_interval_m = distance_from_last_fix(
                self.last_vision_lat_lon,
                self.last_vision_local_enu_m,
                lat_lon,
                local_enu,
            )
            self.last_vision_fix_utc = timestamp_utc
            self.last_vision_fix_sequence = int(sequence)
            self.last_vision_lat_lon = {
                "lat": first_number(lat_lon.get("lat")),
                "lon": first_number(lat_lon.get("lon")),
            }
            self.last_vision_local_enu_m = {
                "x": first_number(local_enu.get("x")),
                "y": first_number(local_enu.get("y")),
                "z": first_number(local_enu.get("z")),
            }
            self.seconds_since_vision_fix = 0.0
            self.meters_since_vision_fix = 0.0
        return self.snapshot()


class UdpPositionBroadcaster:
    def __init__(self, target: str) -> None:
        host, port = parse_udp_target(target)
        self.host = host
        self.port = port
        self._socket = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        self._socket.setsockopt(socket.SOL_SOCKET, socket.SO_BROADCAST, 1)

    def close(self) -> None:
        self._socket.close()

    def send(self, packet: dict[str, Any]) -> None:
        payload = json.dumps(packet, separators=(",", ":"), sort_keys=True).encode("utf-8")
        self._socket.sendto(payload, (self.host, self.port))


def parse_udp_target(target: str) -> tuple[str, int]:
    value = str(target or "").strip()
    if not value:
        raise ValueError("Position telemetry UDP target is empty")
    if ":" not in value:
        raise ValueError("Position telemetry UDP target must look like host:port")
    host, port_text = value.rsplit(":", 1)
    host = host.strip() or "255.255.255.255"
    try:
        port = int(port_text)
    except ValueError as exc:
        raise ValueError(f"Invalid position telemetry UDP port: {port_text}") from exc
    if not (1 <= port <= 65535):
        raise ValueError(f"Position telemetry UDP port out of range: {port}")
    return host, port


def build_position_update(
    *,
    sequence: int,
    timestamp_utc: str | None,
    result: dict[str, Any],
    telemetry_samples: list[dict[str, Any]],
    gps_config: GpsHealthConfig | None = None,
    fix_tracker: FixCadenceTracker | None = None,
) -> dict[str, Any]:
    gps_config = gps_config or GpsHealthConfig()
    timestamp_utc = timestamp_utc or datetime.now(timezone.utc).isoformat()
    gps = latest_gps_sample(telemetry_samples)
    gps_health = gps_health_report(gps, gps_config)
    vision = vision_position_report(result)

    if gps is not None and gps_health["healthy"]:
        source = "gps"
        source_state = "gps_primary"
        source_transition_reason = "healthy_gps"
        status = "accepted"
        lat_lon = {"lat": gps.get("gps_lat"), "lon": gps.get("gps_lon")}
        altitude_m = first_number(gps.get("gps_alt_m"), gps.get("global_alt_m"), gps.get("relative_altitude_m"))
        confidence = gps_health["confidence"]
        covariance = {
            "x_m2": gps_health.get("horizontal_accuracy_m2"),
            "y_m2": gps_health.get("horizontal_accuracy_m2"),
            "z_m2": gps_health.get("vertical_accuracy_m2"),
            "yaw_rad2": None,
        }
        local_enu = local_enu_from_telemetry(gps)
    elif vision["available"]:
        source = "vision"
        source_state = "vision_correction"
        source_transition_reason = "vision_available_gps_unhealthy_or_missing"
        status = "accepted" if result.get("status") == "accepted" else "degraded"
        lat_lon = vision["lat_lon"]
        altitude_m = first_number((vision.get("local_enu_m") or {}).get("z"))
        confidence = vision["confidence"]
        covariance = result.get("covariance") if isinstance(result.get("covariance"), dict) else {}
        local_enu = vision.get("local_enu_m")
    elif gps is not None:
        source = "gps_degraded"
        source_state = "gps_degraded"
        source_transition_reason = gps_health.get("reason") or "gps_degraded_no_vision"
        status = "degraded"
        lat_lon = {"lat": gps.get("gps_lat"), "lon": gps.get("gps_lon")}
        altitude_m = first_number(gps.get("gps_alt_m"), gps.get("global_alt_m"), gps.get("relative_altitude_m"))
        confidence = gps_health["confidence"]
        covariance = {
            "x_m2": gps_health.get("horizontal_accuracy_m2"),
            "y_m2": gps_health.get("horizontal_accuracy_m2"),
            "z_m2": gps_health.get("vertical_accuracy_m2"),
            "yaw_rad2": None,
        }
        local_enu = local_enu_from_telemetry(gps)
    elif fix_tracker is not None and fix_tracker.last_vision_lat_lon is not None:
        source = "vision"
        source_state = "dead_reckoning_between_fixes"
        source_transition_reason = "no_current_position_reusing_last_vision_fix"
        status = "degraded"
        lat_lon = {
            "lat": fix_tracker.last_vision_lat_lon.get("lat"),
            "lon": fix_tracker.last_vision_lat_lon.get("lon"),
        }
        altitude_m = first_number((fix_tracker.last_vision_local_enu_m or {}).get("z"))
        confidence = 0.15
        covariance = {"x_m2": 400.0, "y_m2": 400.0, "z_m2": None, "yaw_rad2": None}
        local_enu = fix_tracker.last_vision_local_enu_m or {"x": None, "y": None, "z": None}
    else:
        source = "none"
        source_state = "no_position"
        source_transition_reason = "no_position_sources"
        status = "unavailable"
        lat_lon = {"lat": None, "lon": None}
        altitude_m = None
        confidence = 0.0
        covariance = {"x_m2": None, "y_m2": None, "z_m2": None, "yaw_rad2": None}
        local_enu = {"x": None, "y": None, "z": None}

    fix_cadence = fix_tracker.note_packet(
        sequence=sequence,
        timestamp_utc=timestamp_utc,
        source_state=source_state,
        lat_lon=lat_lon,
        local_enu=local_enu,
    ) if fix_tracker is not None else empty_fix_cadence()
    dead_reckoning_active = source_state == "dead_reckoning_between_fixes"

    return {
        "schema_version": "vision_nav_position_update_v2",
        "timestamp_utc": timestamp_utc,
        "sequence": int(sequence),
        "status": status,
        "source": source,
        "source_state": source_state,
        "source_transition_reason": source_transition_reason,
        "source_priority": "gps_primary_vision_fallback",
        "lat_lon": lat_lon,
        "altitude_m": altitude_m,
        "local_enu_m": local_enu,
        "confidence": confidence,
        "covariance": covariance,
        "last_vision_fix_utc": fix_cadence.get("last_vision_fix_utc"),
        "seconds_since_vision_fix": fix_cadence.get("seconds_since_vision_fix"),
        "meters_since_vision_fix": fix_cadence.get("meters_since_vision_fix"),
        "vision_fix_interval_m": fix_cadence.get("vision_fix_interval_m"),
        "dead_reckoning_active": dead_reckoning_active,
        "fix_cadence": fix_cadence,
        "gps_health": gps_health,
        "vision_health": {
            "available": vision["available"],
            "status": result.get("status"),
            "confidence": vision["confidence"],
            "tile_id": result.get("tile_id"),
            "inliers": result.get("inliers"),
            "reprojection_error_px": result.get("reprojection_error_px"),
        },
    }


def latest_gps_sample(telemetry_samples: list[dict[str, Any]]) -> dict[str, Any] | None:
    for sample in reversed(telemetry_samples):
        if not isinstance(sample, dict):
            continue
        lat = sample.get("gps_lat")
        lon = sample.get("gps_lon")
        if is_finite_number(lat) and is_finite_number(lon):
            return sample
    return None


def gps_health_report(sample: dict[str, Any] | None, config: GpsHealthConfig) -> dict[str, Any]:
    if sample is None:
        return {
            "healthy": False,
            "reason": "missing",
            "fix_type": None,
            "satellites_visible": None,
            "eph_m": None,
            "epv_m": None,
            "h_acc_m": None,
            "v_acc_m": None,
            "confidence": 0.0,
            "horizontal_accuracy_m2": None,
            "vertical_accuracy_m2": None,
        }

    fix_type = as_int(sample.get("gps_fix_type"))
    satellites = as_int(sample.get("gps_satellites_visible"))
    eph_m = first_number(sample.get("gps_eph_m"))
    epv_m = first_number(sample.get("gps_epv_m"))
    h_acc_m = first_number(sample.get("gps_h_acc_m"), eph_m)
    v_acc_m = first_number(sample.get("gps_v_acc_m"), epv_m)
    issues: list[str] = []
    if fix_type is None or fix_type < config.min_fix_type:
        issues.append("fix_type_low")
    if satellites is not None and satellites < config.min_satellites:
        issues.append("satellites_low")
    if h_acc_m is not None and h_acc_m > config.max_h_acc_m:
        issues.append("horizontal_accuracy_weak")
    elif h_acc_m is None and eph_m is not None and eph_m > config.max_eph_m:
        issues.append("eph_weak")
    confidence = gps_confidence(fix_type=fix_type, satellites=satellites, h_acc_m=h_acc_m or eph_m)
    return {
        "healthy": not issues,
        "reason": "healthy" if not issues else ",".join(issues),
        "fix_type": fix_type,
        "satellites_visible": satellites,
        "eph_m": eph_m,
        "epv_m": epv_m,
        "h_acc_m": h_acc_m,
        "v_acc_m": v_acc_m,
        "confidence": confidence,
        "horizontal_accuracy_m2": square_or_none(h_acc_m or eph_m),
        "vertical_accuracy_m2": square_or_none(v_acc_m or epv_m),
    }


def gps_confidence(*, fix_type: int | None, satellites: int | None, h_acc_m: float | None) -> float:
    confidence = 0.0
    if fix_type is not None:
        confidence += min(max((fix_type - 1) / 4.0, 0.0), 1.0) * 0.45
    if satellites is not None:
        confidence += min(max(satellites / 12.0, 0.0), 1.0) * 0.25
    if h_acc_m is not None and h_acc_m > 0:
        confidence += min(max(1.0 - (h_acc_m / 10.0), 0.0), 1.0) * 0.30
    return round(confidence, 3)


def vision_position_report(result: dict[str, Any]) -> dict[str, Any]:
    lat_lon = result.get("lat_lon") if isinstance(result.get("lat_lon"), dict) else {}
    local_enu = result.get("local_enu_m") if isinstance(result.get("local_enu_m"), dict) else {}
    available = is_finite_number(lat_lon.get("lat")) and is_finite_number(lat_lon.get("lon"))
    return {
        "available": available,
        "lat_lon": {"lat": lat_lon.get("lat"), "lon": lat_lon.get("lon")},
        "local_enu_m": local_enu or {"x": None, "y": None, "z": None},
        "confidence": first_number(result.get("position_confidence"), result.get("confidence")) or 0.0,
    }


def local_enu_from_telemetry(sample: dict[str, Any]) -> dict[str, Any]:
    north = sample.get("local_north_m")
    east = sample.get("local_east_m")
    down = sample.get("local_down_m")
    return {
        "x": east if is_finite_number(east) else None,
        "y": north if is_finite_number(north) else None,
        "z": -down if is_finite_number(down) else None,
    }


def first_number(*values: Any) -> float | None:
    for value in values:
        if is_finite_number(value):
            return float(value)
    return None


def is_finite_number(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False


def as_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def square_or_none(value: float | None) -> float | None:
    if value is None:
        return None
    return round(float(value) * float(value), 6)


def empty_fix_cadence() -> dict[str, Any]:
    return {
        "last_vision_fix_utc": None,
        "last_vision_fix_sequence": None,
        "seconds_since_vision_fix": None,
        "meters_since_vision_fix": None,
        "vision_fix_interval_m": None,
    }


def parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def distance_from_last_fix(
    last_lat_lon: dict[str, Any] | None,
    last_local: dict[str, Any] | None,
    lat_lon: dict[str, Any],
    local_enu: dict[str, Any],
) -> float | None:
    local_distance = local_distance_m(last_local, local_enu)
    if local_distance is not None:
        return round(local_distance, 3)
    if not last_lat_lon:
        return None
    lat1 = first_number(last_lat_lon.get("lat"))
    lon1 = first_number(last_lat_lon.get("lon"))
    lat2 = first_number(lat_lon.get("lat"))
    lon2 = first_number(lat_lon.get("lon"))
    if lat1 is None or lon1 is None or lat2 is None or lon2 is None:
        return None
    return round(haversine_m(lat1, lon1, lat2, lon2), 3)


def local_distance_m(last_local: dict[str, Any] | None, local_enu: dict[str, Any]) -> float | None:
    if not last_local:
        return None
    x1 = first_number(last_local.get("x"))
    y1 = first_number(last_local.get("y"))
    x2 = first_number(local_enu.get("x"))
    y2 = first_number(local_enu.get("y"))
    if x1 is None or y1 is None or x2 is None or y2 is None:
        return None
    return math.hypot(x2 - x1, y2 - y1)


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_m = 6_371_000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2
    return radius_m * 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))
