"""Offline camera/PX4 timing evidence. No clock adjustment or flight output."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from bisect import bisect_left
import argparse
import hashlib
import json
import math
from pathlib import Path
import time
from typing import Any

import numpy as np


@dataclass(frozen=True)
class TimingPolicy:
    min_samples: int = 6
    min_span_s: float = 1.0
    max_sample_gap_s: float = 30.0
    max_round_trip_ms: float = 10.0
    max_drift_ppm: float = 200.0
    residual_slack_ms: float = 0.5
    max_conversion_uncertainty_ms: float = 5.0
    max_host_bracket_ms: float = 1.0
    max_host_offset_step_ms: float = 0.1
    max_camera_delivery_age_ms: float = 1000.0
    max_attitude_delta_ms: float = 25.0

    def __post_init__(self) -> None:
        if type(self.min_samples) is not int or self.min_samples < 3:
            raise ValueError("min_samples must be an integer >= 3")
        for key, value in asdict(self).items():
            if key != "min_samples" and (isinstance(value, bool) or not math.isfinite(value) or value <= 0):
                raise ValueError(f"{key} must be finite and positive")


def _positive_int(value: Any) -> bool:
    return type(value) is int and value > 0


def sample_host_clocks() -> dict[str, Any]:
    """Bracket Linux BOOTTIME with MONOTONIC; never substitute wall time."""
    if not hasattr(time, "CLOCK_BOOTTIME"):
        return {"status": "unavailable", "reason": "linux_boottime_unavailable"}
    try:
        before = time.monotonic_ns()
        boot = time.clock_gettime_ns(time.CLOCK_BOOTTIME)
        after = time.monotonic_ns()
    except (OSError, ValueError) as exc:
        return {"status": "unavailable", "reason": f"clock_sample_failed:{type(exc).__name__}"}
    return {"status": "observed", "monotonic_before_ns": before,
            "boottime_ns": boot, "monotonic_after_ns": after}


def camera_exposure_time(metadata: dict[str, Any], policy: TimingPolicy) -> dict[str, Any]:
    """Map first-row exposure start into host time, not capture-call midpoint.

    This does not model sensor latency or rolling-shutter row timing. The result
    is an API-clock observation, not a calibrated full-frame exposure timestamp.
    """
    def reject(reason: str) -> dict[str, Any]:
        return {"status": "rejected", "reason": reason}

    sensor = metadata.get("sensor_timestamp_ns")
    if metadata.get("sensor_clock_domain") != "linux_boottime_ns" or not _positive_int(sensor):
        return reject("camera_sensor_clock_unverified")
    if metadata.get("sensor_timestamp_reference") != "first_row_exposure_start":
        return reject("camera_timestamp_reference_unverified")
    samples = metadata.get("host_clock_samples")
    if not isinstance(samples, list) or len(samples) != 2:
        return reject("camera_host_clock_brackets_missing")
    offsets, widths, ends, boots = [], [], [], []
    for sample in samples:
        if not isinstance(sample, dict):
            return reject("invalid_host_clock_sample")
        before, boot, after = (sample.get(key) for key in
                              ("monotonic_before_ns", "boottime_ns", "monotonic_after_ns"))
        if not all(_positive_int(value) for value in (before, boot, after)) or after < before:
            return reject("invalid_host_clock_sample")
        if after - before > policy.max_host_bracket_ms * 1e6:
            return reject("host_clock_bracket_too_wide")
        offsets.append(boot - (before + after) // 2)
        widths.append((after - before) / 2)
        ends.append((before, after))
        boots.append(boot)
    if ends[1][0] < ends[0][1] or boots[1] <= boots[0]:
        return reject("host_clock_samples_not_ordered")
    offset_step = abs(offsets[1] - offsets[0])
    if offset_step > policy.max_host_offset_step_ms * 1e6 + sum(widths):
        return reject("host_suspend_or_clock_step")
    delivery_age = boots[-1] - sensor
    if delivery_age < -widths[-1] or delivery_age > policy.max_camera_delivery_age_ms * 1e6:
        return reject("camera_sensor_timestamp_outside_delivery_window")
    host = sensor - offsets[-1]
    if host <= 0:
        return reject("camera_converted_timestamp_invalid")
    return {"status": "available", "host_monotonic_ns": host,
            "sensor_timestamp_ns": sensor,
            "clock_uncertainty_ns": int(math.ceil(max(widths) + offset_step)),
            "timestamp_reference": "first_row_exposure_start",
            "boottime_minus_monotonic_ns": offsets[-1],
            "sensor_timing_calibrated": False, "full_frame_timing_available": False,
            "delivery_age_ms": delivery_age / 1e6}


@dataclass(frozen=True)
class Px4ClockFit:
    host_origin_ns: int
    offset_at_origin_ns: float
    offset_slope: float
    host_samples_ns: tuple[int, ...]
    envelope_ns: float
    source: tuple[int, int]
    policy: TimingPolicy

    def convert(self, host_ns: int, host_uncertainty_ns: int = 0) -> dict[str, Any]:
        if not _positive_int(host_ns) or type(host_uncertainty_ns) is not int or host_uncertainty_ns < 0:
            return {"status": "rejected", "reason": "invalid_conversion_timestamp"}
        if host_ns - host_uncertainty_ns < self.host_samples_ns[0] or host_ns + host_uncertainty_ns > self.host_samples_ns[-1]:
            return {"status": "rejected", "reason": "outside_clock_fit_interval"}
        nearest = min(abs(host_ns - stamp) for stamp in self.host_samples_ns)
        # An engineering envelope, not a covariance or a measured sensor delay.
        uncertainty = self.envelope_ns + nearest * self.policy.max_drift_ppm * 1e-6
        uncertainty += host_uncertainty_ns * (1 + abs(self.offset_slope))
        if uncertainty > self.policy.max_conversion_uncertainty_ms * 1e6:
            return {"status": "rejected", "reason": "clock_uncertainty_exceeds_policy",
                    "clock_uncertainty_ns": int(math.ceil(uncertainty))}
        remote = host_ns + round(self.offset_at_origin_ns + self.offset_slope * (host_ns - self.host_origin_ns))
        if remote <= 0:
            return {"status": "rejected", "reason": "invalid_remote_timestamp"}
        return {"status": "available", "px4_timestamp_ns": remote,
                "clock_uncertainty_ns": int(math.ceil(uncertainty)),
                "clock_domain": "px4_timesync_ns", "source_system": self.source[0],
                "source_component": self.source[1]}


def fit_px4_clock(exchanges: list[dict[str, Any]], policy: TimingPolicy | None = None
                  ) -> tuple[Px4ClockFit | None, dict[str, Any]]:
    """Fit remote-host offset drift using bounded RTT observations in order.

    High RTT/malformed samples are excluded with counts. Source ambiguity,
    clock resets, excessive drift/residual and long unobserved gaps reject the
    fit as a whole. Midpoint symmetry remains an explicit approximation.
    """
    policy = policy or TimingPolicy()
    report: dict[str, Any] = {
        "schema_version": "vision_nav_px4_clock_fit_v1", "status": "rejected",
        "policy": asdict(policy), "observed_sample_count": len(exchanges),
        "evidence_sha256": hashlib.sha256(json.dumps(exchanges, sort_keys=True).encode()).hexdigest(),
        "uncertainty_semantics": "rtt_residual_drift_envelope_not_calibrated_covariance",
        "clock_domain_assumption": "PX4 TIMESYNC uses the ATTITUDE boot clock; verify pinned firmware",
        "flight_ready": False, "errors": [], "discarded_samples": [],
    }
    mids, offsets, half_rtts, sources = [], [], [], set()
    remote_times = []
    for index, exchange in enumerate(exchanges):
        sent, received, remote = (exchange.get(key) for key in
                                 ("host_sent_monotonic_ns", "host_received_monotonic_ns", "px4_timestamp_ns"))
        if not all(_positive_int(value) for value in (sent, received, remote)) or received < sent:
            report["discarded_samples"].append({"index": index, "reason": "invalid_exchange"})
            continue
        source = (exchange.get("source_system"), exchange.get("source_component"))
        if not all(_positive_int(value) and value <= 255 for value in source):
            report["errors"].append("timesync_source_identity_missing")
            continue
        sources.add(source)
        if (received - sent) > policy.max_round_trip_ms * 1e6:
            report["discarded_samples"].append({"index": index, "reason": "round_trip_exceeds_policy"})
            continue
        midpoint = (sent + received) // 2
        mids.append(midpoint)
        offsets.append(remote - midpoint)
        half_rtts.append((received - sent) / 2)
        remote_times.append(remote)
    report["accepted_sample_count"] = len(mids)
    if len(sources) != 1:
        report["errors"].append("timesync_source_ambiguous_or_missing")
    if len(mids) < policy.min_samples:
        report["errors"].append("insufficient_clock_samples")
    if any(b <= a for a, b in zip(mids, mids[1:])) or any(b <= a for a, b in zip(remote_times, remote_times[1:])):
        report["errors"].append("clock_reset_duplicate_or_reordering")
    if len(mids) >= 2:
        report["span_s"] = (mids[-1] - mids[0]) / 1e9
        report["max_sample_gap_s"] = max(b - a for a, b in zip(mids, mids[1:])) / 1e9
        if report["span_s"] < policy.min_span_s:
            report["errors"].append("clock_fit_span_too_short")
        if report["max_sample_gap_s"] > policy.max_sample_gap_s:
            report["errors"].append("clock_observation_gap_too_large")
    if report["errors"]:
        report["errors"] = sorted(set(report["errors"]))
        return None, report
    origin = mids[0]
    x = np.asarray([mid - origin for mid in mids], dtype=np.float64) / 1e9
    # Center offsets too, avoiding loss of precision on long-running hosts.
    y0 = offsets[0]
    y = np.asarray([offset - y0 for offset in offsets], dtype=np.float64)
    weights = 1 / np.maximum(np.asarray(half_rtts), 1000.0)
    design = np.column_stack((np.ones_like(x), x))
    intercept, drift_ns_per_s = np.linalg.lstsq(design * weights[:, None], y * weights, rcond=None)[0]
    residuals = np.abs(y - design @ [intercept, drift_ns_per_s])
    slope = drift_ns_per_s / 1e9
    report.update(drift_ppm=float(slope * 1e6), max_residual_ms=float(residuals.max() / 1e6),
                  host_interval_ns=[mids[0], mids[-1]], source_system=next(iter(sources))[0],
                  source_component=next(iter(sources))[1])
    if abs(slope * 1e6) > policy.max_drift_ppm:
        report["errors"].append("clock_drift_exceeds_policy")
    if np.any(residuals > np.asarray(half_rtts) + policy.residual_slack_ms * 1e6):
        report["errors"].append("clock_step_or_inconsistent_offset")
    if report["errors"]:
        return None, report
    envelope = max(half_rtts) + float(residuals.max()) + policy.residual_slack_ms * 1e6
    fit = Px4ClockFit(origin, float(intercept + y0), float(slope), tuple(mids), envelope,
                     next(iter(sources)), policy)
    report.update(status="available", offset_at_origin_ns=fit.offset_at_origin_ns,
                  host_origin_ns=origin, envelope_ns=envelope)
    return fit, report


def audit_capture_timing(frames: list[dict[str, Any]], telemetry: list[dict[str, Any]],
                         timesync: dict[str, Any], policy: TimingPolicy | None = None) -> dict[str, Any]:
    """Add exposure/native-attitude pairing alongside unchanged legacy evidence."""
    policy = policy or TimingPolicy()
    exchanges = []
    for phase in ("pre_capture", "during_capture", "post_capture"):
        snapshot = timesync.get(phase, {})
        if isinstance(snapshot, dict):
            exchanges.extend(snapshot.get("exchanges", []))
    fit, clock_report = fit_px4_clock(exchanges, policy)
    frames_out = []
    attitudes = []
    if fit:
        for record in telemetry:
            sample = record.get("sample", {})
            if sample.get("message_type") != "ATTITUDE":
                continue
            if (sample.get("source_system"), sample.get("source_component")) != fit.source:
                continue
            stamp = sample.get("vehicle_timestamp_us")
            if sample.get("vehicle_time_domain") != "px4_boot_us" or not _positive_int(stamp):
                continue
            angles = [sample.get(key) for key in ("roll_rad", "pitch_rad", "yaw_rad")]
            if not all(type(a) in (int, float) and math.isfinite(a) for a in angles):
                continue
            attitudes.append((stamp * 1000, record))
    attitude_order_valid = all(b[0] > a[0] for a, b in zip(attitudes, attitudes[1:]))
    attitude_stamps = [row[0] for row in attitudes]
    previous_sensor = None
    previous_clock_offset = None
    host_clock_stepped = False
    for frame in frames:
        camera = camera_exposure_time(frame.get("camera_metadata", {}), policy)
        result = {"sequence": frame.get("sequence"), "status": "rejected", "camera": camera}
        sensor = frame.get("camera_metadata", {}).get("sensor_timestamp_ns")
        ordered = _positive_int(sensor) and (previous_sensor is None or sensor > previous_sensor)
        if _positive_int(sensor):
            previous_sensor = max(previous_sensor or 0, sensor)
        if camera["status"] == "available":
            offset = camera["boottime_minus_monotonic_ns"]
            if previous_clock_offset is not None and abs(offset - previous_clock_offset) > policy.max_host_offset_step_ms * 1e6 + camera["clock_uncertainty_ns"]:
                host_clock_stepped = True
            previous_clock_offset = offset
        if not ordered:
            result["reason"] = "camera_timestamp_missing_duplicate_or_reordered"
        elif camera["status"] != "available":
            result["reason"] = camera["reason"]
        elif host_clock_stepped:
            result["reason"] = "host_suspend_or_clock_step_between_frames"
        elif not fit:
            result["reason"] = "px4_clock_fit_unavailable"
        else:
            converted = fit.convert(camera["host_monotonic_ns"], camera["clock_uncertainty_ns"])
            result["conversion"] = converted
            if converted["status"] != "available":
                result["reason"] = converted["reason"]
            elif not attitude_order_valid:
                result["reason"] = "attitude_clock_reset_duplicate_or_reordering"
            elif not attitudes:
                result["reason"] = "compatible_native_attitude_missing"
            else:
                target = converted["px4_timestamp_ns"]
                index = bisect_left(attitude_stamps, target)
                stamp, nearest = min(attitudes[max(0, index - 1):index + 1], key=lambda item: abs(item[0] - target))
                delta = abs(stamp - target)
                # ATTITUDE time_boot_ms has millisecond quantization.
                uncertainty = converted["clock_uncertainty_ns"] + 1_000_000
                if delta + uncertainty > policy.max_attitude_delta_ms * 1e6:
                    result["reason"] = "native_attitude_age_exceeds_policy"
                else:
                    result.update(status="paired", telemetry_index=nearest.get("index"),
                                  native_delta_ms=delta / 1e6, total_timing_envelope_ms=(delta + uncertainty) / 1e6,
                                  attitude_timestamp_us=stamp // 1000,
                                  attitude_rpy_rad=[nearest["sample"][key] for key in ("roll_rad", "pitch_rad", "yaw_rad")],
                                  interpolation="none_nearest_native_sample")
        frames_out.append(result)
    paired = sum(row["status"] == "paired" for row in frames_out)
    return {"schema_version": "vision_nav_exposure_alignment_v1",
            "status": "available" if frames_out and paired == len(frames_out) else "incomplete",
            "clock_fit": clock_report, "frames": frames_out, "paired_frame_count": paired,
            "frame_count": len(frames_out), "vio_ready": False, "flight_ready": False,
            "legacy_pairing_replaced": False,
            "limitations": ["first_row_start_not_full_frame_exposure", "sensor_delay_not_calibrated",
                            "nearest_attitude_not_raw_imu_interpolation", "offline_fit_not_live_clock_service"]}


def audit_training_session_timing(session_dir: str | Path) -> dict[str, Any]:
    from vision_nav.training_capture import sha256_file, validate_training_session

    root = Path(session_dir).expanduser().resolve()
    validation = validate_training_session(root, verify_checksums=True)
    if validation["status"] != "valid":
        raise ValueError("Invalid capture session: " + "; ".join(validation["errors"]))
    manifest = validation["manifest"]
    path = (root / manifest["telemetry_path"]).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Telemetry must be inside the capture session")
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    report = audit_capture_timing(manifest["frames"], records, manifest.get("mavlink", {}).get("timesync", {}))
    report.update(session_id=manifest["session_id"], manifest_sha256=sha256_file(root / "manifest.json"),
                  telemetry_sha256=sha256_file(path), source_checksums="passed")
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Audit exposure/PX4 clock alignment without changing a capture.")
    parser.add_argument("--session", type=Path, required=True)
    args = parser.parse_args(argv)
    try:
        report = audit_training_session_timing(args.session)
    except (ValueError, OSError) as exc:
        print(json.dumps({"status": "invalid", "error": str(exc)}))
        return 2
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "available" else 2


if __name__ == "__main__":
    raise SystemExit(main())
