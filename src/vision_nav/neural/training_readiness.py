"""Evaluate whether a materialized training index is usable for a learned run."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
import math
from pathlib import Path
from typing import Any

from .training_dataset import EXPOSURE_TIMING, LEGACY_TIMING
from .training_timing import checked_exposure_frame


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def evaluate_training_readiness(
    index_dir: str | Path,
    *,
    min_samples: int = 100,
    min_capture_sessions: int = 2,
    require_gps_labels: bool = True,
    require_quality_metrics: bool = True,
    require_measured_extrinsics: bool = True,
    require_sensor_timestamps: bool = True,
    min_laplacian_variance: float = 20.0,
    max_saturated_fraction: float = 0.65,
    max_attitude_frame_delta_ms: float = 50.0,
) -> dict[str, Any]:
    root = Path(index_dir).expanduser().resolve()
    manifest_path = root / "manifest.json"
    index_path = root / "samples.jsonl"
    errors: list[str] = []
    recommendations: list[str] = []
    if not manifest_path.is_file() or not index_path.is_file():
        return {
            "status": "not_ready",
            "index_dir": str(root),
            "errors": ["Training index must contain manifest.json and samples.jsonl."],
            "recommendations": ["Run scripts/research/prepare_training_dataset.sh first."],
        }
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    if manifest.get("schema_version") != "vision_nav_training_index_v1":
        errors.append("unsupported_training_index_schema")
    if manifest.get("index_sha256") != _sha256(index_path):
        errors.append("training_index_checksum_mismatch")
    samples = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    sessions = sorted({sample.get("capture_session_id") for sample in samples if sample.get("capture_session_id")})
    map_releases = sorted({sample.get("map_release_id") for sample in samples if sample.get("map_release_id")})
    calibrations = sorted({sample.get("calibration_id") for sample in samples if sample.get("calibration_id")})
    extrinsic_ids = sorted({sample.get("camera_to_body", {}).get("extrinsic_id") for sample in samples if sample.get("camera_to_body", {}).get("extrinsic_id")})
    measured_extrinsics = sum(
        1 for sample in samples if sample.get("camera_to_body", {}).get("status") == "measured"
    )
    attitude_samples = sum(1 for sample in samples if sample.get("attitude"))
    attitude_alignment_deltas_ms, misaligned_samples = [], []
    timing_cache = {}
    for sample in samples:
        alignment = sample.get("attitude_alignment") or {}
        policy = sample.get("timing_policy", LEGACY_TIMING)
        delta = alignment.get("host_delta_ms")
        if policy == EXPOSURE_TIMING:
            try:
                row = checked_exposure_frame({**sample,
                    "sensor_timestamp_ns": (sample.get("capture_timing") or {}).get("sensor_timestamp_ns"),
                    "attitude_rpy_rad": [(sample.get("attitude") or {}).get(k) for k in ("roll_rad", "pitch_rad", "yaw_rad")]}, timing_cache)
                delta = row["total_timing_envelope_ms"]
            except (ValueError, OSError):
                delta = None
        elif policy != LEGACY_TIMING:
            delta = None
        if alignment.get("status") != "paired" or type(delta) not in (int, float) or not math.isfinite(delta) or not 0 <= delta <= max_attitude_frame_delta_ms:
            misaligned_samples.append(sample.get("sample_id", "unknown"))
        else:
            attitude_alignment_deltas_ms.append(float(delta))
    gps_labeled_samples = sum(1 for sample in samples if sample.get("gps_reference"))
    quality_measured_samples = sum(
        1 for sample in samples if sample.get("camera_metadata", {}).get("quality", {}).get("status") == "measured"
    )
    sensor_timestamp_samples = sum(
        1
        for sample in samples
        if isinstance(sample.get("capture_timing"), dict)
        and isinstance(sample["capture_timing"].get("sensor_timestamp_ns"), int)
    )
    sensor_timestamp_missing_sample_ids = [
        sample.get("sample_id", "unknown")
        for sample in samples
        if not isinstance(sample.get("capture_timing"), dict)
        or not isinstance(sample["capture_timing"].get("sensor_timestamp_ns"), int)
    ]
    unacceptable_quality_samples = [
        sample["sample_id"]
        for sample in samples
        if sample.get("camera_metadata", {}).get("quality", {}).get("status") == "measured"
        and (
            float(sample["camera_metadata"]["quality"].get("laplacian_variance", 0.0)) < min_laplacian_variance
            or float(sample["camera_metadata"]["quality"].get("saturated_fraction", 1.0)) > max_saturated_fraction
        )
    ]
    missing_frame_files = sum(1 for sample in samples if not Path(str(sample.get("frame_path", ""))).is_file())

    if len(samples) < min_samples:
        errors.append("insufficient_sample_count")
        recommendations.append(f"Capture at least {min_samples - len(samples)} additional synchronized frames.")
    if len(sessions) < min_capture_sessions:
        errors.append("insufficient_capture_session_diversity")
        recommendations.append("Capture separate sessions across routes, lighting, and motion conditions to avoid train/validation leakage.")
    if attitude_samples != len(samples):
        errors.append("missing_attitude_for_some_samples")
        recommendations.append("Rebuild the index only from sessions with PX4 ATTITUDE telemetry.")
    if max_attitude_frame_delta_ms <= 0.0:
        errors.append("invalid_max_attitude_frame_delta_ms")
    if misaligned_samples:
        errors.append("frame_attitude_timing_outside_limit")
        recommendations.append(
            "Improve MAVLink ATTITUDE cadence or reduce capture load until every image has an attitude sample within "
            f"{max_attitude_frame_delta_ms:g} ms."
        )
    if require_gps_labels and gps_labeled_samples == 0:
        errors.append("no_offline_geo_labels")
        recommendations.append("Record healthy GPS only as offline training/evaluation reference, or attach trusted survey labels.")
    if missing_frame_files:
        errors.append("training_index_references_missing_frames")
    if len(calibrations) != 1:
        errors.append("mixed_or_missing_calibration_ids")
        recommendations.append("Train one model release per calibrated camera configuration until cross-calibration support is validated.")
    if require_measured_extrinsics and measured_extrinsics != len(samples):
        errors.append("missing_or_unmeasured_camera_to_body_extrinsics")
        recommendations.append("Measure and record the camera-to-body transform for the final camera mount before IMU-aware training.")
    if len(map_releases) != 1:
        recommendations.append("Use map-release-aware train/validation splits and report performance per map release.")
    if gps_labeled_samples and gps_labeled_samples < len(samples):
        recommendations.append("Some samples have no GPS reference. Keep their use restricted to self-supervised or correspondence losses.")
    if require_quality_metrics and quality_measured_samples < len(samples):
        errors.append("missing_frame_quality_metrics")
    if require_sensor_timestamps and sensor_timestamp_samples < len(samples):
        errors.append("missing_camera_sensor_timestamps")
        recommendations.append(
            "Use Picamera2 capture with per-frame SensorTimestamp evidence; keep timestamp-less captures out of field model training."
        )
    if unacceptable_quality_samples:
        errors.append("frame_quality_below_training_threshold")
        recommendations.append("Review focus, shutter/exposure, camera vibration, and lighting before recapturing affected frames.")
    if quality_measured_samples < len(samples):
        recommendations.append("Some frames lack measured blur/exposure metrics. Inspect camera ingress before using them for model promotion.")

    return {
        "status": "ready" if not errors else "not_ready",
        "timing_policies": sorted({sample.get("timing_policy", LEGACY_TIMING) for sample in samples}),
        "sensor_delay_calibrated": False,
        "gps_timing_calibrated": False,
        "flight_ready": False,
        "generated_at_utc": datetime.now(timezone.utc).isoformat().replace("+00:00", "Z"),
        "index_dir": str(root),
        "dataset_release_id": manifest.get("dataset_release_id"),
        "requirements": {
            "min_samples": min_samples,
            "min_capture_sessions": min_capture_sessions,
            "require_gps_labels": require_gps_labels,
            "require_quality_metrics": require_quality_metrics,
            "require_measured_extrinsics": require_measured_extrinsics,
            "require_sensor_timestamps": require_sensor_timestamps,
            "min_laplacian_variance": min_laplacian_variance,
            "max_saturated_fraction": max_saturated_fraction,
            "max_attitude_frame_delta_ms": max_attitude_frame_delta_ms,
        },
        "metrics": {
            "sample_count": len(samples),
            "capture_session_count": len(sessions),
            "capture_session_ids": sessions,
            "map_release_ids": map_releases,
            "calibration_ids": calibrations,
            "extrinsic_ids": extrinsic_ids,
            "measured_extrinsics_sample_count": measured_extrinsics,
            "attitude_sample_count": attitude_samples,
            "attitude_alignment_sample_count": len(attitude_alignment_deltas_ms),
            "attitude_alignment_median_ms": round(sorted(attitude_alignment_deltas_ms)[len(attitude_alignment_deltas_ms) // 2], 3)
            if attitude_alignment_deltas_ms
            else None,
            "attitude_alignment_max_ms": round(max(attitude_alignment_deltas_ms), 3)
            if attitude_alignment_deltas_ms
            else None,
            "attitude_alignment_outside_limit_count": len(misaligned_samples),
            "attitude_alignment_outside_limit_sample_ids": misaligned_samples,
            "gps_labeled_sample_count": gps_labeled_samples,
            "gps_label_fraction": round(gps_labeled_samples / len(samples), 4) if samples else 0.0,
            "quality_measured_sample_count": quality_measured_samples,
            "quality_unacceptable_sample_count": len(unacceptable_quality_samples),
            "quality_unacceptable_sample_ids": unacceptable_quality_samples,
            "sensor_timestamp_sample_count": sensor_timestamp_samples,
            "sensor_timestamp_missing_sample_count": len(sensor_timestamp_missing_sample_ids),
            "sensor_timestamp_missing_sample_ids": sensor_timestamp_missing_sample_ids,
            "missing_frame_file_count": missing_frame_files,
        },
        "errors": errors,
        "recommendations": recommendations,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Gate learned-navigation training on verified frame, attitude, and label coverage.")
    parser.add_argument("--index-dir", required=True, type=Path)
    parser.add_argument("--min-samples", type=int, default=100)
    parser.add_argument("--min-capture-sessions", type=int, default=2)
    parser.add_argument("--allow-missing-gps-labels", action="store_true")
    parser.add_argument("--allow-missing-quality-metrics", action="store_true")
    parser.add_argument("--allow-missing-sensor-timestamps", action="store_true")
    parser.add_argument("--allow-missing-extrinsics", action="store_true")
    parser.add_argument("--min-laplacian-variance", type=float, default=20.0)
    parser.add_argument("--max-saturated-fraction", type=float, default=0.65)
    parser.add_argument("--max-attitude-frame-delta-ms", type=float, default=50.0)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    summary = evaluate_training_readiness(
        args.index_dir,
        min_samples=args.min_samples,
        min_capture_sessions=args.min_capture_sessions,
        require_gps_labels=not args.allow_missing_gps_labels,
        require_quality_metrics=not args.allow_missing_quality_metrics,
        require_sensor_timestamps=not args.allow_missing_sensor_timestamps,
        require_measured_extrinsics=not args.allow_missing_extrinsics,
        min_laplacian_variance=args.min_laplacian_variance,
        max_saturated_fraction=args.max_saturated_fraction,
        max_attitude_frame_delta_ms=args.max_attitude_frame_delta_ms,
    )
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(summary, indent=2, sort_keys=True))
    if summary["status"] != "ready":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
