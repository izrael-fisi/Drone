"""Audit captured inertial streams without treating receive time as sensor time."""

from collections import defaultdict
import math
from statistics import median
from typing import Any


def summarize_inertial_capture(records: list[dict[str, Any]]) -> dict[str, Any]:
    groups = defaultdict(list)
    for record in records:
        sample = record.get("sample", {})
        if sample.get("message_type") in {"HIGHRES_IMU", "SCALED_IMU", "SCALED_IMU2", "SCALED_IMU3"}:
            key = tuple(sample.get(k) for k in ("source_system", "source_component", "message_type", "imu_sensor_id", "vehicle_time_domain"))
            groups[key].append(sample)
    streams = []
    for key, samples in groups.items():
        timestamps = [s.get("vehicle_timestamp_us") for s in samples]
        valid_times = [t for t in timestamps if isinstance(t, int) and t > 0]
        gaps = [b - a for a, b in zip(valid_times, valid_times[1:])]
        positive_gaps = [g for g in gaps if g > 0]
        def finite_vector(value: Any) -> bool:
            return isinstance(value, (tuple, list)) and len(value) == 3 and all(isinstance(v, (int, float)) and math.isfinite(v) for v in value)
        complete = sum(finite_vector(s.get("acceleration_m_s2")) and finite_vector(s.get("angular_velocity_rad_s")) for s in samples)
        fresh = sum(isinstance(s.get("imu_fields_updated"), int) and (s["imu_fields_updated"] & 0x3f) == 0x3f for s in samples)
        streams.append({
            "source_system": key[0], "source_component": key[1], "message_type": key[2], "sensor_id": key[3], "time_domain": key[4],
            "sample_count": len(samples), "finite_vector_pair_count": complete,
            "all_six_axes_updated_count": fresh if key[2] == "HIGHRES_IMU" else None,
            "missing_timestamp_count": len(samples) - len(valid_times),
            "nonmonotonic_timestamp_count": sum(g <= 0 for g in gaps),
            "median_sensor_interval_ms": median(positive_gaps) / 1000 if positive_gaps else None,
            "max_sensor_interval_ms": max(positive_gaps) / 1000 if positive_gaps else None,
            "observed_sensor_rate_hz": 1_000_000 / median(positive_gaps) if positive_gaps else None,
        })
    return {"schema_version": "vision_nav_inertial_capture_audit_v1", "status": "observed" if streams else "absent",
            "sample_count": sum(s["sample_count"] for s in streams), "streams": streams,
            "vio_ready": False,
            "reason": "requires_sensor_noise_extrinsics_and_exposure_clock_validation" if streams else "no_inertial_samples_recorded"}
