from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from statistics import median
from typing import Any


def summarize_flight_evidence_logs(paths: list[str | Path]) -> dict[str, Any]:
    records: list[dict[str, Any]] = []
    missing: list[str] = []
    for raw_path in paths:
        path = Path(raw_path).expanduser()
        if not path.exists():
            missing.append(str(path))
            continue
        records.extend(read_jsonl_records(path))
    summary = summarize_flight_evidence_records(records)
    summary["log_count"] = len(paths)
    summary["missing_logs"] = missing
    return summary


def read_jsonl_records(path: Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8", errors="replace") as handle:
        for line in handle:
            text = line.strip()
            if not text:
                continue
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                continue
            if isinstance(value, dict):
                records.append(value)
    return records


def summarize_flight_evidence_records(records: list[dict[str, Any]]) -> dict[str, Any]:
    tracker = FlightEvidenceTracker()
    for record in records:
        tracker.add_record(record)
    return tracker.summary()


@dataclass
class FlightEvidenceTracker:
    total_records: int = 0
    accepted_vision_fix_count: int = 0
    rejected_vision_fix_count: int = 0
    gps_sample_count: int = 0
    gps_vs_vision_distances_m: list[float] = field(default_factory=list)
    vision_fix_times: list[datetime] = field(default_factory=list)
    vision_fix_distance_intervals_m: list[float] = field(default_factory=list)
    source_transition_timeline: list[dict[str, Any]] = field(default_factory=list)
    positions: list[tuple[datetime | None, float | None, float | None, float | None]] = field(default_factory=list)
    max_altitude_m: float | None = None
    first_timestamp: datetime | None = None
    last_timestamp: datetime | None = None
    last_source_state: str | None = None
    dead_reckoning_seconds: float = 0.0
    last_record_timestamp: datetime | None = None

    def add_record(self, record: dict[str, Any]) -> None:
        self.total_records += 1
        timestamp = parse_datetime(record.get("timestamp_utc"))
        if timestamp is None:
            timestamp = parse_datetime((record.get("position_update") or {}).get("timestamp_utc"))
        self._update_time_bounds(timestamp)

        result = record.get("result") if isinstance(record.get("result"), dict) else {}
        position_update = record.get("position_update") if isinstance(record.get("position_update"), dict) else {}
        telemetry = record.get("telemetry") if isinstance(record.get("telemetry"), list) else []

        source_state = str(position_update.get("source_state") or legacy_source_state(position_update.get("source")))
        self._track_source_transition(timestamp, source_state, position_update)
        if source_state == "dead_reckoning_between_fixes" and timestamp and self.last_record_timestamp:
            self.dead_reckoning_seconds += max(0.0, (timestamp - self.last_record_timestamp).total_seconds())
        if timestamp:
            self.last_record_timestamp = timestamp

        if has_gps_sample(telemetry):
            self.gps_sample_count += 1

        result_status = result.get("status")
        vision_available = bool((position_update.get("vision_health") or {}).get("available")) or result_has_position(result)
        if source_state == "vision_correction" and result_status == "accepted":
            self.accepted_vision_fix_count += 1
            self._track_vision_fix(timestamp, result)
            gps_distance = gps_vs_vision_distance(telemetry, result)
            if gps_distance is not None:
                self.gps_vs_vision_distances_m.append(gps_distance)
        elif vision_available and result_status != "accepted":
            self.rejected_vision_fix_count += 1

        lat_lon = position_update.get("lat_lon") if isinstance(position_update.get("lat_lon"), dict) else {}
        altitude_m = first_number(position_update.get("altitude_m"), (position_update.get("local_enu_m") or {}).get("z") if isinstance(position_update.get("local_enu_m"), dict) else None)
        if altitude_m is not None:
            self.max_altitude_m = altitude_m if self.max_altitude_m is None else max(self.max_altitude_m, altitude_m)
        self.positions.append(
            (
                timestamp,
                first_number(lat_lon.get("lat")),
                first_number(lat_lon.get("lon")),
                altitude_m,
            )
        )

    def _update_time_bounds(self, timestamp: datetime | None) -> None:
        if timestamp is None:
            return
        self.first_timestamp = timestamp if self.first_timestamp is None else min(self.first_timestamp, timestamp)
        self.last_timestamp = timestamp if self.last_timestamp is None else max(self.last_timestamp, timestamp)

    def _track_source_transition(
        self,
        timestamp: datetime | None,
        source_state: str,
        position_update: dict[str, Any],
    ) -> None:
        if source_state == self.last_source_state:
            return
        self.source_transition_timeline.append(
            {
                "timestamp_utc": timestamp.isoformat() if timestamp else position_update.get("timestamp_utc"),
                "source_state": source_state,
                "reason": position_update.get("source_transition_reason"),
            }
        )
        self.last_source_state = source_state

    def _track_vision_fix(self, timestamp: datetime | None, result: dict[str, Any]) -> None:
        if timestamp:
            self.vision_fix_times.append(timestamp)
        local = result.get("local_enu_m") if isinstance(result.get("local_enu_m"), dict) else {}
        previous = getattr(self, "_last_vision_local", None)
        current = local_xy(local)
        if previous and current:
            self.vision_fix_distance_intervals_m.append(distance_2d(previous, current))
        self._last_vision_local = current

    def summary(self) -> dict[str, Any]:
        fix_intervals_s = [
            max(0.0, (later - earlier).total_seconds())
            for earlier, later in zip(self.vision_fix_times, self.vision_fix_times[1:])
        ]
        total_distance_m = path_distance_m(self.positions)
        duration_s = (
            max(0.0, (self.last_timestamp - self.first_timestamp).total_seconds())
            if self.first_timestamp and self.last_timestamp
            else None
        )
        return {
            "schema_version": "vision_nav_flight_evidence_summary_v1",
            "total_records": self.total_records,
            "total_distance_m": round(total_distance_m, 3) if total_distance_m is not None else None,
            "max_altitude_m": round(self.max_altitude_m, 3) if self.max_altitude_m is not None else None,
            "flight_duration_s": round(duration_s, 3) if duration_s is not None else None,
            "accepted_vision_fix_count": self.accepted_vision_fix_count,
            "rejected_vision_fix_count": self.rejected_vision_fix_count,
            "gps_sample_count": self.gps_sample_count,
            "gps_vs_vision_sample_count": len(self.gps_vs_vision_distances_m),
            "gps_vs_vision_median_distance_m": round(median(self.gps_vs_vision_distances_m), 3)
            if self.gps_vs_vision_distances_m
            else None,
            "fix_interval_stats_s": stats(fix_intervals_s),
            "vision_fix_interval_m": stats(self.vision_fix_distance_intervals_m),
            "dead_reckoning_duration_s": round(self.dead_reckoning_seconds, 3),
            "source_transition_timeline": self.source_transition_timeline,
        }


def legacy_source_state(source: Any) -> str:
    if source == "gps":
        return "gps_primary"
    if source == "vision":
        return "vision_correction"
    if source == "gps_degraded":
        return "gps_degraded"
    return "no_position"


def has_gps_sample(samples: list[Any]) -> bool:
    return any(isinstance(sample, dict) and is_finite(sample.get("gps_lat")) and is_finite(sample.get("gps_lon")) for sample in samples)


def result_has_position(result: dict[str, Any]) -> bool:
    lat_lon = result.get("lat_lon") if isinstance(result.get("lat_lon"), dict) else {}
    return is_finite(lat_lon.get("lat")) and is_finite(lat_lon.get("lon"))


def gps_vs_vision_distance(telemetry: list[Any], result: dict[str, Any]) -> float | None:
    vision = result.get("lat_lon") if isinstance(result.get("lat_lon"), dict) else {}
    v_lat = first_number(vision.get("lat"))
    v_lon = first_number(vision.get("lon"))
    if v_lat is None or v_lon is None:
        return None
    for sample in reversed(telemetry):
        if not isinstance(sample, dict):
            continue
        g_lat = first_number(sample.get("gps_lat"))
        g_lon = first_number(sample.get("gps_lon"))
        if g_lat is not None and g_lon is not None:
            return haversine_m(g_lat, g_lon, v_lat, v_lon)
    return None


def path_distance_m(positions: list[tuple[datetime | None, float | None, float | None, float | None]]) -> float | None:
    total = 0.0
    previous: tuple[float, float] | None = None
    used = 0
    for _, lat, lon, _alt in positions:
        if lat is None or lon is None:
            continue
        current = (lat, lon)
        if previous is not None:
            total += haversine_m(previous[0], previous[1], current[0], current[1])
            used += 1
        previous = current
    return total if used else None


def stats(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "min": None, "median": None, "max": None}
    return {
        "count": len(values),
        "min": round(min(values), 3),
        "median": round(median(values), 3),
        "max": round(max(values), 3),
    }


def parse_datetime(value: Any) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    radius_m = 6_371_000.0
    p1 = math.radians(lat1)
    p2 = math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2.0) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2.0) ** 2
    return radius_m * 2.0 * math.atan2(math.sqrt(a), math.sqrt(1.0 - a))


def local_xy(local: dict[str, Any]) -> tuple[float, float] | None:
    x = first_number(local.get("x"))
    y = first_number(local.get("y"))
    if x is None or y is None:
        return None
    return (x, y)


def distance_2d(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def first_number(*values: Any) -> float | None:
    for value in values:
        if is_finite(value):
            return float(value)
    return None


def is_finite(value: Any) -> bool:
    try:
        return math.isfinite(float(value))
    except (TypeError, ValueError):
        return False
