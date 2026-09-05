"""Record and validate synchronized Pi camera/PX4 training capture sessions.

The recorder is intentionally separate from flight localization. It preserves raw
frames and nearby PX4 telemetry as training evidence, with GNSS marked strictly
for offline labels and evaluation. It never changes PX4 parameters or commands
the vehicle.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
import shutil
from statistics import median
import threading
import time
from typing import Any, Callable, Iterable
from uuid import uuid4

from vision_nav.camera import load_camera_calibration
from vision_nav.camera_ingress import CapturedFrame, CameraIngress, create_camera_ingress, frame_quality_metrics
from vision_nav.camera_extrinsics import load_camera_to_body, sha256_file as extrinsics_sha256_file
from vision_nav.capture_frame import capture_frame
from vision_nav.mavlink_bridge import MavlinkTelemetrySample, MavlinkVisionBridge
from vision_nav.inertial_capture import summarize_inertial_capture
from vision_nav.capture_timing import audit_capture_timing


TRAINING_CAPTURE_SCHEMA = "vision_nav_training_capture_v1"
CHECKSUM_FILENAME = "checksums.sha256"
SUPPORTED_IMAGE_SUFFIXES = {".bmp", ".jpeg", ".jpg", ".png", ".tif", ".tiff", ".webp"}
DEFAULT_MAX_ATTITUDE_FRAME_DELTA_MS = 50.0
DEFAULT_MIN_OFFLINE_GNSS_FIX_TYPE = 3
DEFAULT_MAX_OFFLINE_GNSS_HORIZONTAL_ACCURACY_M = 10.0
DEFAULT_MAX_OFFLINE_GNSS_FRAME_AGE_MS = 500.0
DEFAULT_TIMESYNC_SAMPLE_COUNT = 8
DEFAULT_MAX_TIMESYNC_ROUND_TRIP_MS = 50.0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _default_session_id() -> str:
    return f"capture-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')}-{uuid4().hex[:8]}"


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _relative(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def write_session_checksums(session_dir: str | Path) -> dict[str, Any]:
    root = Path(session_dir)
    checksum_path = root / CHECKSUM_FILENAME
    entries = [
        {"path": _relative(path, root), "sha256": sha256_file(path)}
        for path in sorted(root.rglob("*"))
        if path.is_file() and path.resolve() != checksum_path.resolve()
    ]
    checksum_path.write_text("".join(f"{entry['sha256']}  {entry['path']}\n" for entry in entries), encoding="utf-8")
    return {"checksum_file": str(checksum_path), "entry_count": len(entries), "entries": entries}


def _parse_checksums(path: Path) -> list[dict[str, str]]:
    entries: list[dict[str, str]] = []
    for line_number, raw in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        line = raw.strip()
        if not line:
            continue
        parts = line.split(maxsplit=1)
        if len(parts) != 2 or len(parts[0]) != 64:
            raise ValueError(f"{path}:{line_number}: expected '<sha256>  <path>'.")
        entries.append({"sha256": parts[0].lower(), "path": parts[1].strip()})
    return entries


def _drain_telemetry(
    reader: Callable[[], MavlinkTelemetrySample | None] | None,
    records: list[dict[str, Any]],
    *,
    limit: int = 64,
) -> tuple[int | None, int | None]:
    if reader is None:
        return None, None
    first: int | None = None
    last: int | None = None
    for _ in range(limit):
        sample = reader()
        if sample is None:
            break
        index = len(records)
        records.append(
            {
                "index": index,
                "host_utc": _utc_now(),
                "host_monotonic_ns": time.monotonic_ns(),
                "sample": sample.to_dict(),
            }
        )
        if first is None:
            first = index
        last = index
    return first, last


class _ContinuousTelemetryRecorder:
    """Read a real MAVLink link continuously while camera frames are captured.

    The training recorder only creates evidence; it sends neither MAVLink
    commands nor PX4 vision messages. Keeping reads in one dedicated thread
    avoids the old capture-boundary pattern, where an otherwise healthy 20 Hz
    ATTITUDE stream could be missed during a slow camera capture.
    """

    def __init__(self, reader: Callable[[], MavlinkTelemetrySample | None]) -> None:
        self._reader = reader
        self._records: list[dict[str, Any]] = []
        self._errors: list[str] = []
        self._lock = threading.Lock()
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._started_monotonic_ns: int | None = None
        self._stopped_monotonic_ns: int | None = None

    def start(self) -> None:
        if self._thread is not None:
            raise RuntimeError("Continuous telemetry recorder is already running.")
        self._started_monotonic_ns = time.monotonic_ns()
        self._thread = threading.Thread(target=self._run, name="vision-nav-mavlink-capture", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            if self._thread.is_alive():
                self._errors.append("telemetry_recorder_stop_timeout")
        self._stopped_monotonic_ns = time.monotonic_ns()

    def snapshot_bounds(self) -> tuple[int | None, int | None]:
        with self._lock:
            if not self._records:
                return None, None
            return int(self._records[0]["index"]), int(self._records[-1]["index"])

    def records(self) -> list[dict[str, Any]]:
        with self._lock:
            return list(self._records)

    def summary(self) -> dict[str, Any]:
        records = self.records()
        attitudes = [record for record in records if record["sample"].get("message_type") == "ATTITUDE"]
        attitude_times = [int(record["host_monotonic_ns"]) for record in attitudes]
        gaps_ms = [(later - earlier) / 1_000_000.0 for earlier, later in zip(attitude_times, attitude_times[1:])]
        return {
            "mode": "continuous_background_reader",
            "started_monotonic_ns": self._started_monotonic_ns,
            "stopped_monotonic_ns": self._stopped_monotonic_ns,
            "telemetry_record_count": len(records),
            "attitude_sample_count": len(attitudes),
            "attitude_interval_median_ms": _rounded_median(gaps_ms),
            "attitude_interval_max_ms": round(max(gaps_ms), 3) if gaps_ms else None,
            "errors": list(self._errors),
        }

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                sample = self._reader()
            except Exception as exc:  # A capture report must preserve reader failure evidence.
                with self._lock:
                    self._errors.append(f"telemetry_reader_failed:{type(exc).__name__}:{exc}")
                return
            if sample is None:
                # A nonblocking reader should not consume a CPU core while the
                # serial link has no message ready.
                self._stop.wait(0.002)
                continue
            record = {
                "host_utc": _utc_now(),
                "host_monotonic_ns": time.monotonic_ns(),
                "sample": sample.to_dict(),
            }
            with self._lock:
                record["index"] = len(self._records)
                self._records.append(record)


def _rounded_median(values: list[float]) -> float | None:
    return round(float(median(values)), 3) if values else None


def _capture_timesync_summary(
    pre_capture: dict[str, Any],
    post_capture: dict[str, Any],
    *,
    sample_count: int,
    max_round_trip_ms: float,
) -> dict[str, Any]:
    """Bind pre/post MAVLink TIMESYNC evidence to one capture session.

    A stable PX4/Pi offset is evidence for later timing calibration, not proof
    that the camera SensorTimestamp shares either clock domain. Keeping both
    snapshots makes slow timing drift visible in the immutable capture record.
    """

    requirements = {
        "requested_sample_count": sample_count,
        "min_accepted_sample_count": 3,
        "max_round_trip_ms": max_round_trip_ms,
    }
    if pre_capture.get("status") == "not_configured" and post_capture.get("status") == "not_configured":
        return {
            "schema_version": "vision_nav_training_capture_timesync_v1",
            "status": "not_configured",
            "policy": "capture_timing_evidence_only",
            "requirements": requirements,
            "pre_capture": pre_capture,
            "post_capture": post_capture,
            "errors": [],
        }

    pre_offset = pre_capture.get("px4_minus_host_midpoint_median_ns")
    post_offset = post_capture.get("px4_minus_host_midpoint_median_ns")
    available = pre_capture.get("status") == "available" and post_capture.get("status") == "available"
    errors: list[str] = []
    if not available:
        errors.append("pre_or_post_capture_timesync_unavailable")
    if not isinstance(pre_offset, int) or not isinstance(post_offset, int):
        errors.append("timesync_offset_summary_missing")
    drift_ns = post_offset - pre_offset if isinstance(pre_offset, int) and isinstance(post_offset, int) else None
    return {
        "schema_version": "vision_nav_training_capture_timesync_v1",
        "status": "available" if not errors else "unavailable",
        "policy": "capture_timing_evidence_only",
        "requirements": requirements,
        "pre_capture": pre_capture,
        "post_capture": post_capture,
        "px4_minus_host_midpoint_pre_capture_ns": pre_offset if isinstance(pre_offset, int) else None,
        "px4_minus_host_midpoint_post_capture_ns": post_offset if isinstance(post_offset, int) else None,
        "px4_minus_host_midpoint_drift_ns": drift_ns,
        "errors": errors,
    }


def _finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _offline_gnss_reference_summary(
    frame_records: list[dict[str, Any]],
    telemetry_records: list[dict[str, Any]],
    *,
    min_fix_type: int = DEFAULT_MIN_OFFLINE_GNSS_FIX_TYPE,
    max_horizontal_accuracy_m: float = DEFAULT_MAX_OFFLINE_GNSS_HORIZONTAL_ACCURACY_M,
    max_frame_age_ms: float = DEFAULT_MAX_OFFLINE_GNSS_FRAME_AGE_MS,
) -> dict[str, Any]:
    """Audit whether a capture can later receive offline GNSS training labels.

    These references are strictly training/evaluation evidence. This function
    must never be used by the vehicle localization runtime.
    """

    requirements = {
        "min_fix_type": min_fix_type,
        "max_horizontal_accuracy_m": max_horizontal_accuracy_m,
        "max_frame_age_ms": max_frame_age_ms,
        "policy": "offline_training_only",
    }
    gps_records = [
        record
        for record in telemetry_records
        if isinstance(record.get("sample"), dict)
        and record["sample"].get("message_type") in {"GPS_RAW_INT", "GLOBAL_POSITION_INT"}
        and isinstance(record.get("host_monotonic_ns"), int)
    ]
    message_counts = {
        "gps_raw_int": sum(1 for record in gps_records if record["sample"].get("message_type") == "GPS_RAW_INT"),
        "global_position_int": sum(1 for record in gps_records if record["sample"].get("message_type") == "GLOBAL_POSITION_INT"),
    }
    accepted = 0
    reasons: dict[str, int] = {}
    ages_ms: list[float] = []
    accuracies_m: list[float] = []
    fix_type_counts: dict[str, int] = {}
    frame_references: list[dict[str, Any]] = []

    for frame in frame_records:
        frame_time = frame.get("host_monotonic_ns")
        sequence = int(frame.get("sequence", -1))
        if not isinstance(frame_time, int):
            reason = "missing_frame_timestamp"
            reasons[reason] = reasons.get(reason, 0) + 1
            frame_references.append({"sequence": sequence, "status": "rejected", "reason": reason})
            continue
        coordinate_records = [
            record
            for record in gps_records
            if _finite_number(record["sample"].get("gps_lat")) and _finite_number(record["sample"].get("gps_lon"))
        ]
        nearby_raw = [
            record
            for record in coordinate_records
            if record["sample"].get("message_type") == "GPS_RAW_INT"
            and abs(frame_time - int(record["host_monotonic_ns"])) <= int(max_frame_age_ms * 1_000_000.0)
        ]
        candidates = nearby_raw or coordinate_records
        if not candidates:
            reason = "missing_gnss_coordinates"
            reasons[reason] = reasons.get(reason, 0) + 1
            frame_references.append({"sequence": sequence, "status": "rejected", "reason": reason})
            continue
        selected = min(
            candidates,
            key=lambda record: (
                0 if record["sample"].get("message_type") == "GPS_RAW_INT" and int(record["sample"].get("gps_fix_type") or -1) >= min_fix_type else 1,
                abs(frame_time - int(record["host_monotonic_ns"])),
            ),
        )
        sample = selected["sample"]
        age_ms = abs(frame_time - int(selected["host_monotonic_ns"])) / 1_000_000.0
        fix_type = sample.get("gps_fix_type")
        horizontal_accuracy = sample.get("gps_h_acc_m")
        if not _finite_number(horizontal_accuracy):
            horizontal_accuracy = sample.get("gps_eph_m")
        reference = {
            "sequence": sequence,
            "message_type": sample.get("message_type"),
            "host_age_ms": round(age_ms, 3),
            "fix_type": fix_type,
            "horizontal_accuracy_m": float(horizontal_accuracy) if _finite_number(horizontal_accuracy) else None,
        }
        if not isinstance(fix_type, int) or fix_type < min_fix_type:
            reason = "gnss_fix_type_below_training_policy"
        elif not _finite_number(horizontal_accuracy) or float(horizontal_accuracy) <= 0.0:
            reason = "missing_gnss_horizontal_accuracy"
        elif float(horizontal_accuracy) > max_horizontal_accuracy_m:
            reason = "gnss_horizontal_accuracy_below_training_policy"
        elif age_ms > max_frame_age_ms:
            reason = "gnss_reference_too_old"
        else:
            reason = None
        if reason is None:
            accepted += 1
            ages_ms.append(age_ms)
            accuracies_m.append(float(horizontal_accuracy))
            reference["status"] = "accepted"
        else:
            reasons[reason] = reasons.get(reason, 0) + 1
            reference.update(status="rejected", reason=reason)
        if isinstance(fix_type, int):
            key = str(fix_type)
            fix_type_counts[key] = fix_type_counts.get(key, 0) + 1
        frame_references.append(reference)

    return {
        "schema_version": "vision_nav_offline_gnss_reference_audit_v1",
        "status": "ready" if frame_records and accepted == len(frame_records) else "not_ready",
        "requirements": requirements,
        "message_counts": message_counts,
        "frame_count": len(frame_records),
        "accepted_frame_count": accepted,
        "rejected_frame_count": len(frame_records) - accepted,
        "rejection_counts": reasons,
        "fix_type_counts": fix_type_counts,
        "host_age_median_ms": _rounded_median(ages_ms),
        "host_age_max_ms": round(max(ages_ms), 3) if ages_ms else None,
        "horizontal_accuracy_median_m": _rounded_median(accuracies_m),
        "horizontal_accuracy_max_m": round(max(accuracies_m), 3) if accuracies_m else None,
        "frame_references": frame_references,
        "policy": "offline_training_only",
    }


def _frame_attitude_alignment(
    frame_records: list[dict[str, Any]], telemetry_records: list[dict[str, Any]]
) -> dict[str, Any]:
    """Attach nearest ATTITUDE evidence to each frame using host monotonic time.

    Camera and PX4 clocks are independent in the first field setup. Pairing on
    the receiver's monotonic clock is therefore explicit and auditable. Native
    PX4 message timestamps remain provenance only until a clock-sync protocol
    is added.
    """

    attitudes = [
        record
        for record in telemetry_records
        if record.get("sample", {}).get("message_type") == "ATTITUDE"
        and isinstance(record.get("host_monotonic_ns"), int)
    ]
    deltas_ms: list[float] = []
    paired = 0
    for frame in frame_records:
        frame_time_ns = frame.get("host_monotonic_ns")
        if not isinstance(frame_time_ns, int) or not attitudes:
            frame["attitude_alignment"] = {"status": "missing"}
            continue
        nearest = min(attitudes, key=lambda record: abs(int(record["host_monotonic_ns"]) - frame_time_ns))
        delta_ms = abs(int(nearest["host_monotonic_ns"]) - frame_time_ns) / 1_000_000.0
        sample = nearest.get("sample", {})
        frame["attitude_alignment"] = {
            "status": "paired",
            "telemetry_index": nearest.get("index"),
            "attitude_host_monotonic_ns": nearest.get("host_monotonic_ns"),
            "host_delta_ms": round(delta_ms, 3),
            "vehicle_timestamp_us": sample.get("vehicle_timestamp_us"),
            "vehicle_time_domain": sample.get("vehicle_time_domain"),
        }
        deltas_ms.append(delta_ms)
        paired += 1

    vehicle_domains: dict[str, list[float]] = {}
    for record in telemetry_records:
        sample = record.get("sample", {})
        vehicle_timestamp_us = sample.get("vehicle_timestamp_us")
        vehicle_time_domain = sample.get("vehicle_time_domain")
        host_monotonic_ns = record.get("host_monotonic_ns")
        if not isinstance(vehicle_timestamp_us, int) or not isinstance(vehicle_time_domain, str) or not isinstance(host_monotonic_ns, int):
            continue
        vehicle_domains.setdefault(vehicle_time_domain, []).append(host_monotonic_ns / 1_000.0 - vehicle_timestamp_us)
    domains = {
        name: {
            "sample_count": len(offsets),
            "host_minus_vehicle_median_us": _rounded_median(offsets),
            "host_minus_vehicle_min_us": round(min(offsets), 3),
            "host_minus_vehicle_max_us": round(max(offsets), 3),
        }
        for name, offsets in sorted(vehicle_domains.items())
        if offsets
    }
    return {
        "pairing_clock_domain": "host_monotonic_ns",
        "native_vehicle_timestamps_are_provenance_only": True,
        "frame_count": len(frame_records),
        "attitude_sample_count": len(attitudes),
        "paired_frame_count": paired,
        "missing_frame_count": len(frame_records) - paired,
        "min_host_delta_ms": round(min(deltas_ms), 3) if deltas_ms else None,
        "median_host_delta_ms": _rounded_median(deltas_ms),
        "max_host_delta_ms": round(max(deltas_ms), 3) if deltas_ms else None,
        "vehicle_time_domains": domains,
    }


def _frame_camera_timing(frame_records: list[dict[str, Any]]) -> dict[str, Any]:
    """Summarize sensor-timestamp evidence without assuming its clock domain.

    Picamera2's SensorTimestamp is valuable provenance, but it is not silently
    treated as synchronized to PX4. Frame-to-attitude pairing stays on the
    explicitly measured host-monotonic capture midpoint until a clock model is
    separately validated.
    """

    sensor_timestamps = [
        int(record["camera_metadata"]["sensor_timestamp_ns"])
        for record in frame_records
        if isinstance(record.get("camera_metadata"), dict)
        and isinstance(record["camera_metadata"].get("sensor_timestamp_ns"), int)
    ]
    durations = [
        float(record["capture_duration_ms"])
        for record in frame_records
        if isinstance(record.get("capture_duration_ms"), (int, float))
    ]
    monotonic = all(later > earlier for earlier, later in zip(sensor_timestamps, sensor_timestamps[1:]))
    sensor_intervals_ms = [
        (later - earlier) / 1_000_000.0
        for earlier, later in zip(sensor_timestamps, sensor_timestamps[1:])
    ]
    return {
        "pairing_timestamp_source": "host_capture_midpoint_monotonic_ns",
        "sensor_timestamp_clock_relation": "unverified_provenance_only",
        "frame_count": len(frame_records),
        "sensor_timestamp_frame_count": len(sensor_timestamps),
        "sensor_timestamp_missing_frame_count": len(frame_records) - len(sensor_timestamps),
        "sensor_timestamps_strictly_monotonic": monotonic if sensor_timestamps else None,
        "sensor_interval_median_ms": _rounded_median(sensor_intervals_ms),
        "sensor_interval_max_ms": round(max(sensor_intervals_ms), 3) if sensor_intervals_ms else None,
        "capture_duration_median_ms": _rounded_median(durations),
        "capture_duration_max_ms": round(max(durations), 3) if durations else None,
    }


def _input_frames(directory: Path) -> list[Path]:
    frames = sorted(path for path in directory.iterdir() if path.is_file() and path.suffix.lower() in SUPPORTED_IMAGE_SUFFIXES)
    if not frames:
        raise ValueError(f"No supported image frames found in {directory}.")
    return frames


def _write_json(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def record_training_session(
    output_dir: str | Path,
    *,
    calibration_id: str,
    map_release_id: str,
    map_reference_kind: str = "deployment_map_release",
    capture_count: int = 60,
    interval_s: float = 0.5,
    width: int = 1456,
    height: int = 1088,
    timeout_ms: int = 1000,
    session_id: str | None = None,
    camera_backend: str = "picamera2",
    input_frames_dir: str | Path | None = None,
    mavlink_endpoint: str | None = None,
    source_system: int = 42,
    source_component: int = 197,
    site_name: str | None = None,
    operator: str | None = None,
    conditions: Iterable[str] = (),
    gnss_truth_policy: str = "offline_training_only",
    require_offline_gnss_reference: bool = False,
    min_offline_gnss_fix_type: int = DEFAULT_MIN_OFFLINE_GNSS_FIX_TYPE,
    max_offline_gnss_horizontal_accuracy_m: float = DEFAULT_MAX_OFFLINE_GNSS_HORIZONTAL_ACCURACY_M,
    max_offline_gnss_frame_age_ms: float = DEFAULT_MAX_OFFLINE_GNSS_FRAME_AGE_MS,
    require_mavlink_timesync: bool = False,
    timesync_sample_count: int = DEFAULT_TIMESYNC_SAMPLE_COUNT,
    max_timesync_round_trip_ms: float = DEFAULT_MAX_TIMESYNC_ROUND_TRIP_MS,
    calibration_file: str | Path | None = None,
    allow_provisional_calibration: bool = False,
    camera_to_body_file: str | Path | None = None,
    allow_provisional_extrinsics: bool = False,
    capture_fn: Callable[[Path, int, int, int], Path | CapturedFrame] | None = None,
    camera_ingress: CameraIngress | None = None,
    telemetry_reader: Callable[[], MavlinkTelemetrySample | None] | None = None,
) -> dict[str, Any]:
    """Create a capture session without making any flight-control decision."""

    root = Path(output_dir).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError(f"Capture output directory must be empty: {root}")
    if not calibration_id.strip() or not map_release_id.strip():
        raise ValueError("calibration_id and map_release_id are required for a training capture.")
    if map_reference_kind not in {"deployment_map_release", "reference_map_release"}:
        raise ValueError("map_reference_kind must be deployment_map_release or reference_map_release.")
    if capture_count <= 0:
        raise ValueError("capture_count must be positive.")
    if interval_s < 0.0:
        raise ValueError("interval_s cannot be negative.")
    if gnss_truth_policy != "offline_training_only":
        raise ValueError("Training captures must mark GNSS as offline_training_only or omit it from telemetry.")
    if min_offline_gnss_fix_type < 0:
        raise ValueError("min_offline_gnss_fix_type cannot be negative.")
    if max_offline_gnss_horizontal_accuracy_m <= 0.0:
        raise ValueError("max_offline_gnss_horizontal_accuracy_m must be positive.")
    if max_offline_gnss_frame_age_ms <= 0.0:
        raise ValueError("max_offline_gnss_frame_age_ms must be positive.")
    if timesync_sample_count <= 0:
        raise ValueError("timesync_sample_count must be positive.")
    if max_timesync_round_trip_ms <= 0.0:
        raise ValueError("max_timesync_round_trip_ms must be positive.")

    calibration: dict[str, Any] = {"calibration_id": calibration_id}
    if calibration_file:
        calibration_path = Path(calibration_file).expanduser().resolve()
        if not calibration_path.is_file():
            raise ValueError(f"Camera calibration file does not exist: {calibration_path}")
        if "placeholder" in calibration_path.read_text(encoding="utf-8").lower() and not allow_provisional_calibration:
            raise ValueError("Camera calibration is marked placeholder. Calibrate the mounted camera before recording training data.")
        loaded_calibration = load_camera_calibration(calibration_path)
        if loaded_calibration.calibration_id and loaded_calibration.calibration_id != calibration_id:
            raise ValueError(
                "Camera calibration ID does not match the selected training-capture calibration ID: "
                f"file={loaded_calibration.calibration_id}, selected={calibration_id}."
            )
        if loaded_calibration.calibration_status != "measured" and not allow_provisional_calibration:
            raise ValueError(
                "Camera calibration is not marked measured. Run vision-nav-calibrate-camera with a real chessboard session first."
            )
        if (loaded_calibration.image_width, loaded_calibration.image_height) != (width, height):
            raise ValueError(
                "Capture resolution does not match camera calibration: "
                f"capture={width}x{height}, calibration={loaded_calibration.image_width}x{loaded_calibration.image_height}."
            )
        calibration.update(
            {
                "file_path": str(calibration_path),
                "file_sha256": sha256_file(calibration_path),
                "camera_name": loaded_calibration.camera_name,
                "image_width": loaded_calibration.image_width,
                "image_height": loaded_calibration.image_height,
                "calibration_status": loaded_calibration.calibration_status,
            }
        )

    extrinsics: dict[str, Any] = {}
    if camera_to_body_file:
        extrinsics_path = Path(camera_to_body_file).expanduser().resolve()
        if not extrinsics_path.is_file():
            raise ValueError(f"Camera-to-body extrinsics file does not exist: {extrinsics_path}")
        if "placeholder" in extrinsics_path.read_text(encoding="utf-8").lower() and not allow_provisional_extrinsics:
            raise ValueError("Camera-to-body extrinsics is marked placeholder. Measure the mounted camera transform before recording training data.")
        loaded_extrinsics = load_camera_to_body(extrinsics_path)
        if loaded_extrinsics.status != "measured" and not allow_provisional_extrinsics:
            raise ValueError("Camera-to-body extrinsics is not marked measured.")
        if loaded_extrinsics.camera_calibration_id and loaded_extrinsics.camera_calibration_id != calibration_id:
            raise ValueError(
                "Camera-to-body extrinsics calibration ID does not match the selected training-capture calibration ID: "
                f"file={loaded_extrinsics.camera_calibration_id}, selected={calibration_id}."
            )
        extrinsics = {
            **loaded_extrinsics.to_log_dict(),
            "file_sha256": extrinsics_sha256_file(extrinsics_path),
        }

    root.mkdir(parents=True, exist_ok=True)
    frames_dir = root / "frames"
    telemetry_path = root / "telemetry" / "px4.jsonl"
    frames_dir.mkdir(parents=True, exist_ok=True)
    telemetry_path.parent.mkdir(parents=True, exist_ok=True)

    bridge: MavlinkVisionBridge | None = None
    telemetry_state = "not_configured"
    continuous_telemetry: _ContinuousTelemetryRecorder | None = None
    timesync_not_configured: dict[str, Any] = {
        "schema_version": "vision_nav_mavlink_timesync_audit_v1",
        "status": "not_configured",
        "policy": "capture_timing_evidence_only",
        "requirements": {
            "requested_sample_count": timesync_sample_count,
            "min_accepted_sample_count": 3,
            "max_round_trip_ms": max_timesync_round_trip_ms,
        },
        "errors": [],
    }
    pre_capture_timesync = dict(timesync_not_configured)
    post_capture_timesync = dict(timesync_not_configured)
    during_capture_timesync: dict[str, Any] = {"status": "not_configured", "exchanges": []}
    if telemetry_reader is None and mavlink_endpoint:
        bridge = MavlinkVisionBridge(mavlink_endpoint, source_system=source_system, source_component=source_component)
        bridge.connect()
        pre_capture_timesync = bridge.capture_timesync_evidence(
            sample_count=timesync_sample_count,
            max_round_trip_ms=max_timesync_round_trip_ms,
        )
        bridge.start_timesync_recording()
        # Use a bounded blocking read in a dedicated thread. Camera capture no
        # longer controls when ATTITUDE samples are collected.
        telemetry_reader = lambda: bridge.try_read_telemetry(timeout_s=0.02)
        continuous_telemetry = _ContinuousTelemetryRecorder(telemetry_reader)
        telemetry_state = "connected_continuous"
    elif telemetry_reader is not None:
        telemetry_state = "injected"

    started_utc = _utc_now()
    started_monotonic_ns = time.monotonic_ns()
    telemetry_records: list[dict[str, Any]] = []
    frame_records: list[dict[str, Any]] = []
    input_frames = _input_frames(Path(input_frames_dir).expanduser()) if input_frames_dir else []
    if input_frames:
        capture_count = min(capture_count, len(input_frames))

    ingress = camera_ingress
    if not input_frames and capture_fn is None and ingress is None:
        ingress = create_camera_ingress(camera_backend, width=width, height=height, timeout_ms=timeout_ms)
    try:
        if continuous_telemetry is not None:
            continuous_telemetry.start()
        for sequence in range(capture_count):
            frame_started_utc = _utc_now()
            frame_started_monotonic_ns = time.monotonic_ns()
            if continuous_telemetry is not None:
                before_first, before_last = continuous_telemetry.snapshot_bounds()
            else:
                before_first, before_last = _drain_telemetry(telemetry_reader, telemetry_records)
            source_path = input_frames[sequence] if input_frames else None
            destination = frames_dir / f"frame_{sequence:06d}{source_path.suffix.lower() if source_path else '.jpg'}"
            if source_path is not None:
                shutil.copy2(source_path, destination)
                backend = "imported_frames"
                capture_metadata = {
                    "backend": backend,
                    "quality": frame_quality_metrics(destination),
                }
            else:
                if ingress is not None:
                    captured = ingress.capture(destination)
                else:
                    recorder = capture_fn or capture_frame
                    output = recorder(destination, width, height, timeout_ms)
                    captured = output if isinstance(output, CapturedFrame) else CapturedFrame(
                        path=Path(output), backend=camera_backend, quality=frame_quality_metrics(output)
                    )
                backend = captured.backend
                capture_metadata = captured.to_dict()
            frame_finished_monotonic_ns = time.monotonic_ns()
            frame_capture_midpoint_monotonic_ns = (frame_started_monotonic_ns + frame_finished_monotonic_ns) // 2
            if continuous_telemetry is not None:
                after_first, after_last = continuous_telemetry.snapshot_bounds()
            else:
                after_first, after_last = _drain_telemetry(telemetry_reader, telemetry_records)
            frame_records.append(
                {
                    "sequence": sequence,
                    "path": _relative(destination, root),
                    "source_path": str(source_path) if source_path else None,
                    "captured_utc": frame_started_utc,
                    "host_monotonic_ns": frame_capture_midpoint_monotonic_ns,
                    "host_capture_started_monotonic_ns": frame_started_monotonic_ns,
                    "host_capture_finished_monotonic_ns": frame_finished_monotonic_ns,
                    "capture_duration_ms": round((frame_finished_monotonic_ns - frame_started_monotonic_ns) / 1_000_000.0, 3),
                    "camera_backend": backend,
                    "camera_metadata": capture_metadata,
                    "telemetry_before": {"first": before_first, "last": before_last},
                    "telemetry_after": {"first": after_first, "last": after_last},
                }
            )
            if sequence < capture_count - 1 and interval_s:
                elapsed_s = (time.monotonic_ns() - frame_started_monotonic_ns) / 1_000_000_000.0
                time.sleep(max(0.0, interval_s - elapsed_s))
    finally:
        if continuous_telemetry is not None:
            continuous_telemetry.stop()
            telemetry_records = continuous_telemetry.records()
        if bridge is not None:
            during_capture_timesync = bridge.stop_timesync_recording()
            post_capture_timesync = bridge.capture_timesync_evidence(
                sample_count=timesync_sample_count,
                max_round_trip_ms=max_timesync_round_trip_ms,
            )
            bridge.close()
        if ingress is not None:
            ingress.close()

    with telemetry_path.open("w", encoding="utf-8") as stream:
        for record in telemetry_records:
            stream.write(json.dumps(record, sort_keys=True) + "\n")

    timesync_evidence = _capture_timesync_summary(
        pre_capture_timesync,
        post_capture_timesync,
        sample_count=timesync_sample_count,
        max_round_trip_ms=max_timesync_round_trip_ms,
    )
    timesync_evidence["during_capture"] = during_capture_timesync
    attitude_count = sum(1 for record in telemetry_records if record["sample"].get("message_type") == "ATTITUDE")
    attitude_alignment = _frame_attitude_alignment(frame_records, telemetry_records)
    offline_gnss_reference = _offline_gnss_reference_summary(
        frame_records,
        telemetry_records,
        min_fix_type=min_offline_gnss_fix_type,
        max_horizontal_accuracy_m=max_offline_gnss_horizontal_accuracy_m,
        max_frame_age_ms=max_offline_gnss_frame_age_ms,
    )
    camera_timing = _frame_camera_timing(frame_records)
    manifest = {
        "schema_version": TRAINING_CAPTURE_SCHEMA,
        "session_id": session_id or _default_session_id(),
        "status": "captured",
        "started_utc": started_utc,
        "completed_utc": _utc_now(),
        "started_monotonic_ns": started_monotonic_ns,
        "timebase": {"primary": "host_monotonic_ns", "utc": "host_utc"},
        "training_policy": {
            "gnss_truth_policy": gnss_truth_policy,
            "flight_runtime_gnss_dependency": "prohibited",
            "require_offline_gnss_reference": require_offline_gnss_reference,
            "require_mavlink_timesync": require_mavlink_timesync,
        },
        "calibration": calibration,
        "camera_to_body": extrinsics,
        "map_release_id": map_release_id,
        "map_reference": {"release_id": map_release_id, "kind": map_reference_kind},
        "camera": {
            "backend": camera_backend, "width": width, "height": height, "timeout_ms": timeout_ms,
            "frame_timing": camera_timing,
        },
        "mavlink": {
            "endpoint": mavlink_endpoint,
            "state": telemetry_state,
            "attitude_sample_count": attitude_count,
            "inertial_capture": summarize_inertial_capture(telemetry_records),
            "frame_attitude_alignment": attitude_alignment,
            "offline_gnss_reference": offline_gnss_reference,
            "timesync": timesync_evidence,
            "exposure_alignment": audit_capture_timing(frame_records, telemetry_records, timesync_evidence),
            "capture": (
                continuous_telemetry.summary()
                if continuous_telemetry is not None
                else {"mode": "capture_boundary_drain", "telemetry_record_count": len(telemetry_records)}
            ),
        },
        "operator_context": {"site_name": site_name, "operator": operator, "conditions": list(conditions)},
        "frames_path": "frames",
        "telemetry_path": _relative(telemetry_path, root),
        "frame_count": len(frame_records),
        "telemetry_count": len(telemetry_records),
        "frames": frame_records,
    }
    _write_json(root / "manifest.json", manifest)
    checksums = write_session_checksums(root)
    return {"session_dir": str(root), "manifest": manifest, "checksums": checksums}


def validate_training_session(
    session_dir: str | Path,
    *,
    require_attitude: bool = False,
    verify_checksums: bool = True,
    max_attitude_frame_delta_ms: float = DEFAULT_MAX_ATTITUDE_FRAME_DELTA_MS,
    require_sensor_timestamps: bool = False,
    require_offline_gnss_reference: bool = False,
    min_offline_gnss_fix_type: int = DEFAULT_MIN_OFFLINE_GNSS_FIX_TYPE,
    max_offline_gnss_horizontal_accuracy_m: float = DEFAULT_MAX_OFFLINE_GNSS_HORIZONTAL_ACCURACY_M,
    max_offline_gnss_frame_age_ms: float = DEFAULT_MAX_OFFLINE_GNSS_FRAME_AGE_MS,
    require_mavlink_timesync: bool = False,
    timesync_sample_count: int = DEFAULT_TIMESYNC_SAMPLE_COUNT,
    max_timesync_round_trip_ms: float = DEFAULT_MAX_TIMESYNC_ROUND_TRIP_MS,
    attitude_timing_policy: str = "legacy_host_receipt",
) -> dict[str, Any]:
    root = Path(session_dir).expanduser().resolve()
    errors: list[str] = []
    if attitude_timing_policy not in {"legacy_host_receipt", "exposure_native_attitude_v1"}:
        errors.append("Unknown attitude timing policy")
    manifest_path = root / "manifest.json"
    manifest: dict[str, Any] = {}
    if not manifest_path.exists():
        return {"status": "invalid", "session_dir": str(root), "errors": ["Missing manifest.json."], "manifest": None}
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {"status": "invalid", "session_dir": str(root), "errors": [f"Invalid manifest.json: {exc}"], "manifest": None}

    required = ("session_id", "calibration", "map_release_id", "training_policy", "frames", "frame_count", "telemetry_path")
    missing = [field for field in required if manifest.get(field) in (None, "", [], {})]
    if manifest.get("schema_version") != TRAINING_CAPTURE_SCHEMA:
        errors.append(f"Unsupported schema_version: {manifest.get('schema_version')!r}.")
    if missing:
        errors.append(f"Missing manifest fields: {', '.join(missing)}.")
    if manifest.get("training_policy", {}).get("gnss_truth_policy") != "offline_training_only":
        errors.append("GNSS truth policy must be offline_training_only.")
    if not manifest.get("calibration", {}).get("calibration_id"):
        errors.append("Missing calibration ID.")
    if max_attitude_frame_delta_ms <= 0.0:
        errors.append("max_attitude_frame_delta_ms must be positive.")
    if min_offline_gnss_fix_type < 0:
        errors.append("min_offline_gnss_fix_type cannot be negative.")
    if max_offline_gnss_horizontal_accuracy_m <= 0.0:
        errors.append("max_offline_gnss_horizontal_accuracy_m must be positive.")
    if max_offline_gnss_frame_age_ms <= 0.0:
        errors.append("max_offline_gnss_frame_age_ms must be positive.")
    if timesync_sample_count <= 0:
        errors.append("timesync_sample_count must be positive.")
    if max_timesync_round_trip_ms <= 0.0:
        errors.append("max_timesync_round_trip_ms must be positive.")
    frames = manifest.get("frames", [])
    if int(manifest.get("frame_count", 0)) != len(frames):
        errors.append("frame_count does not match frame records.")
    for record in frames:
        relative_path = record.get("path")
        if not relative_path or not (root / relative_path).is_file():
            errors.append(f"Missing frame file: {relative_path!r}.")
    telemetry_path = root / str(manifest.get("telemetry_path", "telemetry/px4.jsonl"))
    if not telemetry_path.is_file():
        errors.append(f"Missing telemetry file: {telemetry_path.relative_to(root)}.")
    attitude_count = int(manifest.get("mavlink", {}).get("attitude_sample_count", 0))
    mavlink_capture = manifest.get("mavlink", {}).get("capture", {})
    continuous_capture_errors: list[str] = []
    if isinstance(mavlink_capture, dict):
        raw_capture_errors = mavlink_capture.get("errors", [])
        if isinstance(raw_capture_errors, list):
            continuous_capture_errors = [str(error) for error in raw_capture_errors if str(error)]
        else:
            errors.append("MAVLink capture errors must be a list when provided.")
        if manifest.get("mavlink", {}).get("state") == "connected_continuous" and mavlink_capture.get("mode") != "continuous_background_reader":
            errors.append("Continuous MAVLink capture state is missing its continuous-reader provenance.")
    elif manifest.get("mavlink", {}).get("state") == "connected_continuous":
        errors.append("Continuous MAVLink capture state is missing capture provenance.")
    if continuous_capture_errors:
        errors.append("Continuous MAVLink telemetry capture failed: " + "; ".join(continuous_capture_errors))
    if require_attitude and attitude_count <= 0:
        errors.append("No PX4 ATTITUDE samples were captured.")
    alignment = manifest.get("mavlink", {}).get("frame_attitude_alignment", {})
    alignment_failures: list[int] = []
    if require_attitude and attitude_timing_policy == "exposure_native_attitude_v1":
        try:
            records = [json.loads(line) for line in telemetry_path.read_text(encoding="utf-8").splitlines() if line.strip()]
            alignment = audit_capture_timing(frames, records, manifest.get("mavlink", {}).get("timesync", {}))
            alignment_failures = [row["sequence"] for row in alignment["frames"]
                                  if row["status"] != "paired" or row["total_timing_envelope_ms"] > max_attitude_frame_delta_ms]
            if alignment["status"] != "available" or alignment_failures:
                errors.append("Exposure/native-attitude alignment is incomplete; inspect mavlink.exposure_alignment or run the timing audit")
        except (OSError, ValueError) as exc:
            errors.append(f"Cannot audit exposure/native-attitude alignment: {exc}")
    elif require_attitude:
        if not isinstance(alignment, dict):
            errors.append("Missing frame-to-attitude alignment evidence.")
        else:
            for record in frames:
                evidence = record.get("attitude_alignment", {})
                delta_ms = evidence.get("host_delta_ms") if isinstance(evidence, dict) else None
                if not isinstance(evidence, dict) or evidence.get("status") != "paired" or not isinstance(delta_ms, (float, int)):
                    alignment_failures.append(int(record.get("sequence", -1)))
                elif float(delta_ms) > max_attitude_frame_delta_ms:
                    alignment_failures.append(int(record.get("sequence", -1)))
            if alignment_failures:
                errors.append(
                    "Frame-to-attitude alignment exceeds "
                    f"{max_attitude_frame_delta_ms:g} ms or is missing for frame sequence(s): "
                    f"{', '.join(str(sequence) for sequence in alignment_failures[:10])}."
                )
    timing = manifest.get("camera", {}).get("frame_timing", {})
    sensor_timestamp_failures: list[int] = []
    if require_sensor_timestamps:
        if not isinstance(timing, dict):
            errors.append("Missing camera frame-timing evidence.")
        else:
            for record in frames:
                metadata = record.get("camera_metadata") if isinstance(record.get("camera_metadata"), dict) else {}
                if not isinstance(metadata.get("sensor_timestamp_ns"), int):
                    sensor_timestamp_failures.append(int(record.get("sequence", -1)))
            if sensor_timestamp_failures:
                errors.append(
                    "Camera sensor timestamp is missing for frame sequence(s): "
                    f"{', '.join(str(sequence) for sequence in sensor_timestamp_failures[:10])}."
                )
            elif timing.get("sensor_timestamps_strictly_monotonic") is not True:
                errors.append("Camera sensor timestamps are not strictly monotonic.")

    offline_gnss_reference = manifest.get("mavlink", {}).get("offline_gnss_reference", {})
    if require_offline_gnss_reference:
        if not isinstance(offline_gnss_reference, dict):
            errors.append("Missing offline GNSS reference audit for supervised training.")
        elif offline_gnss_reference.get("status") != "ready":
            errors.append("Offline GNSS references do not satisfy the supervised-training policy.")
        else:
            requirements = offline_gnss_reference.get("requirements", {})
            expected_requirements = {
                "min_fix_type": min_offline_gnss_fix_type,
                "max_horizontal_accuracy_m": max_offline_gnss_horizontal_accuracy_m,
                "max_frame_age_ms": max_offline_gnss_frame_age_ms,
                "policy": "offline_training_only",
            }
            if requirements != expected_requirements:
                errors.append("Offline GNSS reference audit requirements do not match the requested training policy.")

    timesync = manifest.get("mavlink", {}).get("timesync", {})
    if require_mavlink_timesync:
        if not isinstance(timesync, dict):
            errors.append("Missing MAVLink TIMESYNC evidence for field training.")
        elif timesync.get("schema_version") != "vision_nav_training_capture_timesync_v1":
            errors.append("MAVLink TIMESYNC evidence does not contain pre/post capture timing snapshots.")
        elif timesync.get("status") != "available":
            errors.append("MAVLink TIMESYNC evidence is unavailable for field training.")
        else:
            requirements = timesync.get("requirements", {})
            expected_requirements = {
                "requested_sample_count": timesync_sample_count,
                "min_accepted_sample_count": 3,
                "max_round_trip_ms": max_timesync_round_trip_ms,
            }
            if requirements != expected_requirements:
                errors.append("MAVLink TIMESYNC evidence requirements do not match the requested training policy.")
            for phase in ("pre_capture", "post_capture"):
                snapshot = timesync.get(phase)
                if not isinstance(snapshot, dict) or snapshot.get("status") != "available":
                    errors.append(f"MAVLink TIMESYNC {phase} snapshot is unavailable for field training.")

    checksum_summary: dict[str, Any] = {"status": "skipped"}
    if verify_checksums:
        checksum_path = root / CHECKSUM_FILENAME
        if not checksum_path.is_file():
            errors.append(f"Missing {CHECKSUM_FILENAME}.")
            checksum_summary = {"status": "missing"}
        else:
            try:
                entries = _parse_checksums(checksum_path)
                mismatched = []
                missing_paths = []
                expected_paths = {entry["path"] for entry in entries}
                actual_paths = {
                    _relative(path, root)
                    for path in root.rglob("*")
                    if path.is_file() and path.resolve() != checksum_path.resolve()
                }
                for entry in entries:
                    path = root / entry["path"]
                    if not path.is_file():
                        missing_paths.append(entry["path"])
                    elif sha256_file(path) != entry["sha256"]:
                        mismatched.append(entry["path"])
                extra_paths = sorted(actual_paths - expected_paths)
                checksum_summary = {
                    "status": "passed" if not (mismatched or missing_paths or extra_paths) else "failed",
                    "entry_count": len(entries),
                    "mismatched": mismatched,
                    "missing": missing_paths,
                    "extra": extra_paths,
                }
                if checksum_summary["status"] != "passed":
                    errors.append("Session checksum verification failed.")
            except ValueError as exc:
                errors.append(str(exc))
                checksum_summary = {"status": "invalid"}
    return {
        "status": "valid" if not errors else "invalid",
        "session_dir": str(root),
        "session_id": manifest.get("session_id"),
        "frame_count": manifest.get("frame_count", 0),
        "attitude_sample_count": attitude_count,
        "mavlink_capture": mavlink_capture,
        "continuous_mavlink_capture_errors": continuous_capture_errors,
        "frame_attitude_alignment": alignment,
        "max_attitude_frame_delta_ms": max_attitude_frame_delta_ms,
        "frame_attitude_alignment_failure_count": len(alignment_failures),
        "require_sensor_timestamps": require_sensor_timestamps,
        "attitude_timing_policy": attitude_timing_policy,
        "sensor_timestamp_failure_count": len(sensor_timestamp_failures),
        "camera_frame_timing": timing,
        "offline_gnss_reference": offline_gnss_reference,
        "require_offline_gnss_reference": require_offline_gnss_reference,
        "mavlink_timesync": timesync,
        "require_mavlink_timesync": require_mavlink_timesync,
        "checksums": checksum_summary,
        "errors": errors,
        "manifest": manifest,
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Record synchronized camera/PX4 evidence for learned-navigation training.")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--calibration-id", required=True)
    parser.add_argument("--map-release-id", required=True)
    parser.add_argument(
        "--map-reference-kind",
        choices=("deployment_map_release", "reference_map_release"),
        default="deployment_map_release",
        help="ReferenceMapRelease is the correct first-data choice before learned map assets exist.",
    )
    parser.add_argument("--frames", type=int, default=60)
    parser.add_argument("--interval-s", type=float, default=0.5)
    parser.add_argument("--width", type=int, default=1456)
    parser.add_argument("--height", type=int, default=1088)
    parser.add_argument("--timeout-ms", type=int, default=1000)
    parser.add_argument("--session-id")
    parser.add_argument(
        "--camera-backend",
        default="picamera2",
        choices=("auto", "picamera2", "rpicam_still"),
        help="Picamera2 is the field-training default because it preserves SensorTimestamp provenance.",
    )
    parser.add_argument("--calibration-file", type=Path, help="Mounted camera calibration YAML. Required by the Pi wrapper.")
    parser.add_argument("--allow-provisional-calibration", action="store_true", help="Bench-only override for a calibration file marked placeholder.")
    parser.add_argument("--camera-to-body-file", type=Path, help="Measured body-to-camera extrinsics YAML. Required by the Pi wrapper.")
    parser.add_argument("--allow-provisional-extrinsics", action="store_true", help="Bench-only override for placeholder or unmeasured camera extrinsics.")
    parser.add_argument("--input-frames-dir", type=Path, help="Import existing frames instead of accessing the Pi camera.")
    parser.add_argument("--mavlink-endpoint", help="Example: serial:/dev/ttyACM0:921600")
    parser.add_argument("--source-system", type=int, default=42)
    parser.add_argument("--source-component", type=int, default=197)
    parser.add_argument("--site-name")
    parser.add_argument("--operator")
    parser.add_argument("--condition", action="append", default=[])
    parser.add_argument("--require-mavlink-attitude", action="store_true")
    parser.add_argument(
        "--max-attitude-frame-delta-ms",
        type=float,
        default=DEFAULT_MAX_ATTITUDE_FRAME_DELTA_MS,
        help="Maximum allowed host-monotonic delay from a frame to its nearest PX4 ATTITUDE sample.",
    )
    parser.add_argument(
        "--require-sensor-timestamps",
        action="store_true",
        help="Require a strict per-frame camera sensor timestamp for this training capture.",
    )
    parser.add_argument(
        "--require-offline-gnss-reference",
        action="store_true",
        help="Require per-frame PX4 GNSS references suitable only for offline supervised labels, never flight runtime.",
    )
    parser.add_argument("--min-offline-gnss-fix-type", type=int, default=DEFAULT_MIN_OFFLINE_GNSS_FIX_TYPE)
    parser.add_argument(
        "--max-offline-gnss-horizontal-accuracy-m",
        type=float,
        default=DEFAULT_MAX_OFFLINE_GNSS_HORIZONTAL_ACCURACY_M,
    )
    parser.add_argument(
        "--max-offline-gnss-frame-age-ms",
        type=float,
        default=DEFAULT_MAX_OFFLINE_GNSS_FRAME_AGE_MS,
    )
    parser.add_argument(
        "--require-mavlink-timesync",
        action="store_true",
        help="Require bounded MAVLink TIMESYNC evidence for this field-training capture.",
    )
    parser.add_argument("--timesync-sample-count", type=int, default=DEFAULT_TIMESYNC_SAMPLE_COUNT)
    parser.add_argument("--max-timesync-round-trip-ms", type=float, default=DEFAULT_MAX_TIMESYNC_ROUND_TRIP_MS)
    return parser


def main() -> None:
    args = _parser().parse_args()
    try:
        result = record_training_session(
            args.output_dir,
            calibration_id=args.calibration_id,
            map_release_id=args.map_release_id,
            map_reference_kind=args.map_reference_kind,
            capture_count=args.frames,
            interval_s=args.interval_s,
            width=args.width,
            height=args.height,
            timeout_ms=args.timeout_ms,
            session_id=args.session_id,
            camera_backend=args.camera_backend,
            input_frames_dir=args.input_frames_dir,
            mavlink_endpoint=args.mavlink_endpoint,
            source_system=args.source_system,
            source_component=args.source_component,
            site_name=args.site_name,
            operator=args.operator,
            conditions=args.condition,
            require_offline_gnss_reference=args.require_offline_gnss_reference,
            min_offline_gnss_fix_type=args.min_offline_gnss_fix_type,
            max_offline_gnss_horizontal_accuracy_m=args.max_offline_gnss_horizontal_accuracy_m,
            max_offline_gnss_frame_age_ms=args.max_offline_gnss_frame_age_ms,
            require_mavlink_timesync=args.require_mavlink_timesync,
            timesync_sample_count=args.timesync_sample_count,
            max_timesync_round_trip_ms=args.max_timesync_round_trip_ms,
            calibration_file=args.calibration_file,
            allow_provisional_calibration=args.allow_provisional_calibration,
            camera_to_body_file=args.camera_to_body_file,
            allow_provisional_extrinsics=args.allow_provisional_extrinsics,
        )
        validation = validate_training_session(
            args.output_dir,
            require_attitude=args.require_mavlink_attitude,
            max_attitude_frame_delta_ms=args.max_attitude_frame_delta_ms,
            require_sensor_timestamps=args.require_sensor_timestamps,
            require_offline_gnss_reference=args.require_offline_gnss_reference,
            min_offline_gnss_fix_type=args.min_offline_gnss_fix_type,
            max_offline_gnss_horizontal_accuracy_m=args.max_offline_gnss_horizontal_accuracy_m,
            max_offline_gnss_frame_age_ms=args.max_offline_gnss_frame_age_ms,
            require_mavlink_timesync=args.require_mavlink_timesync,
            timesync_sample_count=args.timesync_sample_count,
            max_timesync_round_trip_ms=args.max_timesync_round_trip_ms,
        )
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps({"capture": result, "validation": validation}, indent=2, sort_keys=True))
    if validation["status"] != "valid":
        raise SystemExit(1)


def validate_main() -> None:
    parser = argparse.ArgumentParser(description="Validate an immutable learned-navigation training capture session.")
    parser.add_argument("--session", required=True, type=Path)
    parser.add_argument("--require-mavlink-attitude", action="store_true")
    parser.add_argument("--max-attitude-frame-delta-ms", type=float, default=DEFAULT_MAX_ATTITUDE_FRAME_DELTA_MS)
    parser.add_argument("--require-sensor-timestamps", action="store_true")
    parser.add_argument("--require-offline-gnss-reference", action="store_true")
    parser.add_argument("--min-offline-gnss-fix-type", type=int, default=DEFAULT_MIN_OFFLINE_GNSS_FIX_TYPE)
    parser.add_argument(
        "--max-offline-gnss-horizontal-accuracy-m",
        type=float,
        default=DEFAULT_MAX_OFFLINE_GNSS_HORIZONTAL_ACCURACY_M,
    )
    parser.add_argument(
        "--max-offline-gnss-frame-age-ms",
        type=float,
        default=DEFAULT_MAX_OFFLINE_GNSS_FRAME_AGE_MS,
    )
    parser.add_argument("--require-mavlink-timesync", action="store_true")
    parser.add_argument("--timesync-sample-count", type=int, default=DEFAULT_TIMESYNC_SAMPLE_COUNT)
    parser.add_argument("--max-timesync-round-trip-ms", type=float, default=DEFAULT_MAX_TIMESYNC_ROUND_TRIP_MS)
    args = parser.parse_args()
    summary = validate_training_session(
        args.session,
        require_attitude=args.require_mavlink_attitude,
        max_attitude_frame_delta_ms=args.max_attitude_frame_delta_ms,
        require_sensor_timestamps=args.require_sensor_timestamps,
        require_offline_gnss_reference=args.require_offline_gnss_reference,
        min_offline_gnss_fix_type=args.min_offline_gnss_fix_type,
        max_offline_gnss_horizontal_accuracy_m=args.max_offline_gnss_horizontal_accuracy_m,
        max_offline_gnss_frame_age_ms=args.max_offline_gnss_frame_age_ms,
        require_mavlink_timesync=args.require_mavlink_timesync,
        timesync_sample_count=args.timesync_sample_count,
        max_timesync_round_trip_ms=args.max_timesync_round_trip_ms,
    )
    print(json.dumps(summary, indent=2, sort_keys=True))
    if summary["status"] != "valid":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
