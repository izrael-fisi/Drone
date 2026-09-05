"""Materialize training-ready frame/telemetry samples from a DatasetRelease."""

from __future__ import annotations

import argparse
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from vision_nav.training_capture import sha256_file, validate_training_session
from vision_nav.capture_timing import audit_capture_timing

from .dataset_release import load_dataset_release, _digest


TRAINING_INDEX_SCHEMA = "vision_nav_training_index_v1"
GPS_RAW_PREFERENCE_WINDOW_MS = 500.0
EXPOSURE_TIMING = "exposure_native_attitude_v1"
LEGACY_TIMING = "legacy_host_receipt"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def _nearest(records: list[dict[str, Any]], timestamp_ns: int, message_type: str) -> dict[str, Any] | None:
    candidates = [record for record in records if record.get("sample", {}).get("message_type") == message_type]
    if not candidates:
        return None
    return min(candidates, key=lambda record: abs(int(record.get("host_monotonic_ns", 0)) - timestamp_ns))


def _gps_reference(records: list[dict[str, Any]], timestamp_ns: int) -> dict[str, Any] | None:
    candidates = [
        record
        for record in records
        if record.get("sample", {}).get("message_type") in {"GPS_RAW_INT", "GLOBAL_POSITION_INT"}
        and record.get("sample", {}).get("gps_lat") is not None
        and record.get("sample", {}).get("gps_lon") is not None
    ]
    if not candidates:
        return None
    def age_ns(record: dict[str, Any]) -> int:
        return abs(int(record.get("host_monotonic_ns", 0)) - timestamp_ns)

    # GPS_RAW_INT is the only one of these two common PX4 messages that carries
    # fix type and accuracy. Prefer a contemporaneous raw fix to avoid losing
    # label-quality evidence to a nearer GLOBAL_POSITION_INT update.
    raw_candidates = [
        record for record in candidates
        if record.get("sample", {}).get("message_type") == "GPS_RAW_INT"
        and age_ns(record) <= int(GPS_RAW_PREFERENCE_WINDOW_MS * 1_000_000.0)
    ]
    if raw_candidates:
        nearest = min(
            raw_candidates,
            key=lambda record: (
                0 if isinstance(record["sample"].get("gps_fix_type"), int) and record["sample"]["gps_fix_type"] >= 3 else 1,
                age_ns(record),
            ),
        )
    else:
        nearest = min(candidates, key=age_ns)
    sample = nearest["sample"]
    horizontal_accuracy_m = sample.get("gps_h_acc_m")
    if horizontal_accuracy_m is None:
        horizontal_accuracy_m = sample.get("gps_eph_m")
    return {
        "lat": sample.get("gps_lat"),
        "lon": sample.get("gps_lon"),
        "alt_m": sample.get("gps_alt_m") if sample.get("gps_alt_m") is not None else sample.get("global_alt_m"),
        "fix_type": sample.get("gps_fix_type"),
        "horizontal_accuracy_m": horizontal_accuracy_m,
        "vertical_accuracy_m": sample.get("gps_v_acc_m") if sample.get("gps_v_acc_m") is not None else sample.get("gps_epv_m"),
        "satellites_visible": sample.get("gps_satellites_visible"),
        "source_message_type": sample.get("message_type"),
        "timestamp_ns": nearest.get("host_monotonic_ns"),
        "host_delta_ms": round(age_ns(nearest) / 1_000_000.0, 3),
        "policy": "offline_training_only",
        "timestamp_reference": "host_receipt_unverified_measurement_time",
    }


def build_training_index(
    dataset_release_path: str | Path,
    output_dir: str | Path,
    *,
    require_attitude: bool = True,
    require_sensor_timestamps: bool = True,
    timing_policy: str = EXPOSURE_TIMING,
) -> dict[str, Any]:
    if timing_policy not in {EXPOSURE_TIMING, LEGACY_TIMING}:
        raise ValueError("Unsupported training timing policy")
    if timing_policy == EXPOSURE_TIMING and not (require_attitude and require_sensor_timestamps):
        raise ValueError("Exposure timing requires attitude and sensor timestamps; use explicit legacy_host_receipt for old research data")
    release_path = Path(dataset_release_path).expanduser().resolve()
    release = load_dataset_release(release_path)
    root = Path(output_dir).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError(f"Training index output directory must be empty: {root}")
    root.mkdir(parents=True, exist_ok=True)

    samples: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for item in release["capture_sessions"]:
        session_dir = Path(item["path"]).expanduser().resolve()
        validation = validate_training_session(
            session_dir,
            require_attitude=require_attitude and timing_policy == LEGACY_TIMING,
            require_sensor_timestamps=require_sensor_timestamps,
        )
        if validation["status"] != "valid":
            raise ValueError(f"Capture session is no longer valid: {session_dir}: {'; '.join(validation['errors'])}")
        manifest = validation["manifest"]
        if timing_policy == LEGACY_TIMING and manifest.get("timebase", {}).get("frame_timestamp_reference") == "first_row_exposure_start":
            raise ValueError("Exposure-timestamped capture requires exposure_native_attitude_v1, not legacy midpoint timing")
        if _digest(manifest) != item["manifest_sha256"]:
            raise ValueError("Capture manifest changed since DatasetRelease was created")
        telemetry = _read_jsonl(session_dir / manifest["telemetry_path"])
        frame_hashes = {frame["sequence"]: sha256_file(session_dir / frame["path"]) for frame in manifest["frames"]}
        timing_rows = {}
        audit_reference = None
        if timing_policy == EXPOSURE_TIMING:
            audit = audit_capture_timing(manifest["frames"], telemetry, manifest.get("mavlink", {}).get("timesync", {}))
            audit.update(session_id=manifest["session_id"], manifest_sha256=sha256_file(session_dir / "manifest.json"),
                         telemetry_sha256=sha256_file(session_dir / manifest["telemetry_path"]))
            for row in audit["frames"]:
                row["frame_sha256"] = frame_hashes[row["sequence"]]
            timing_path = root / "timing" / f"{_digest(audit)}.json"
            timing_path.parent.mkdir(exist_ok=True)
            timing_path.write_text(json.dumps(audit, indent=2, sort_keys=True) + "\n", encoding="utf-8")
            audit_reference = {"path": str(timing_path), "sha256": sha256_file(timing_path)}
            for row in audit["frames"]:
                if row["sequence"] in timing_rows:
                    raise ValueError("Duplicate frame sequence in timing audit")
                timing_rows[row["sequence"]] = row
        telemetry_by_index = {row.get("index"): row for row in telemetry}
        if timing_policy == EXPOSURE_TIMING and len(telemetry_by_index) != len(telemetry):
            raise ValueError("Duplicate telemetry indices prevent unambiguous timing alignment")
        for frame in manifest["frames"]:
            timestamp_ns = int(frame["host_monotonic_ns"])
            alignment = frame.get("attitude_alignment", {})
            if timing_policy == EXPOSURE_TIMING:
                alignment = timing_rows[frame["sequence"]]
                if alignment["status"] != "paired":
                    skipped.append({"session_id": manifest["session_id"], "sequence": frame["sequence"],
                                    "reason": alignment.get("reason", "exposure_alignment_rejected")})
                    continue
                timestamp_ns = alignment["camera"]["host_monotonic_ns"]
                attitude_record = telemetry_by_index[alignment["telemetry_index"]]
            else:
                attitude_record = _nearest(telemetry, timestamp_ns, "ATTITUDE")
            if require_attitude and attitude_record is None:
                skipped.append({"session_id": manifest["session_id"], "sequence": frame["sequence"], "reason": "no_nearby_attitude"})
                continue
            camera_metadata = deepcopy(frame.get("camera_metadata", {}))
            if frame.get("image_quality"):
                camera_metadata["quality_provenance"] = {
                    "source": "offline_retained_image_measurement", "frame_sha256": frame_hashes[frame["sequence"]],
                    "original_capture_quality": camera_metadata.get("quality")}
                camera_metadata["quality"] = deepcopy(frame["image_quality"])
            samples.append(
                {
                    "schema_version": TRAINING_INDEX_SCHEMA,
                    "sample_id": f"{manifest['session_id']}:{int(frame['sequence']):06d}",
                    "frame_path": str((session_dir / frame["path"]).resolve()),
                    "frame_sha256": frame_hashes[frame["sequence"]],
                    "timestamp_ns": timestamp_ns,
                    "timestamp_reference": "first_row_exposure_start" if timing_policy == EXPOSURE_TIMING else "host_capture_midpoint",
                    "timing_policy": timing_policy,
                    "timing_audit": {**audit_reference, "sequence": frame["sequence"]} if audit_reference else None,
                    "capture_timing": {
                        "host_capture_started_monotonic_ns": frame.get("host_capture_started_monotonic_ns"),
                        "host_capture_midpoint_monotonic_ns": frame.get("host_monotonic_ns"),
                        "host_capture_finished_monotonic_ns": frame.get("host_capture_finished_monotonic_ns"),
                        "sensor_timestamp_ns": (frame.get("camera_metadata") or {}).get("sensor_timestamp_ns"),
                    },
                    "capture_session_id": manifest["session_id"],
                    "calibration_id": manifest["calibration"]["calibration_id"],
                    "map_release_id": manifest["map_release_id"],
                    "map_reference": manifest.get("map_reference", {"release_id": manifest["map_release_id"], "kind": "legacy_unspecified"}),
                    "camera_metadata": camera_metadata,
                    "camera_to_body": manifest.get("camera_to_body", {}),
                    "attitude": attitude_record.get("sample") if attitude_record else None,
                    "attitude_timestamp_ns": attitude_record.get("host_monotonic_ns") if attitude_record else None,
                    "attitude_timestamp_reference": "host_receipt",
                    "attitude_alignment": alignment,
                    "gps_reference": _gps_reference(telemetry, timestamp_ns),
                }
            )

    index_path = root / "samples.jsonl"
    with index_path.open("w", encoding="utf-8") as stream:
        for sample in samples:
            stream.write(json.dumps(sample, sort_keys=True) + "\n")
    manifest = {
        "schema_version": TRAINING_INDEX_SCHEMA,
        "created_utc": _utc_now(),
        "status": "prepared" if samples else "no_eligible_samples",
        "dataset_release_id": release["release_id"],
        "dataset_release_sha256": sha256_file(release_path),
        "sample_count": len(samples),
        "skipped_count": len(skipped),
        "requires_attitude": require_attitude,
        "requires_sensor_timestamps": require_sensor_timestamps,
        "timing_policy": timing_policy,
        "sensor_delay_calibrated": False,
        "gps_timing_calibrated": False,
        "gps_reference_policy": "offline_training_only",
        "index_path": "samples.jsonl",
        "index_sha256": sha256_file(index_path),
        "skipped": skipped,
    }
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"output_dir": str(root), "manifest": manifest}


def main() -> None:
    parser = argparse.ArgumentParser(description="Materialize auditable learned-navigation training samples from a DatasetRelease.")
    parser.add_argument("--dataset-release", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--allow-missing-attitude", action="store_true")
    parser.add_argument("--allow-missing-sensor-timestamps", action="store_true")
    parser.add_argument("--timing-policy", choices=(EXPOSURE_TIMING, LEGACY_TIMING), default=EXPOSURE_TIMING)
    args = parser.parse_args()
    try:
        result = build_training_index(
            args.dataset_release,
            args.output_dir,
            require_attitude=not args.allow_missing_attitude,
            require_sensor_timestamps=not args.allow_missing_sensor_timestamps,
            timing_policy=args.timing_policy,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result, indent=2, sort_keys=True))
    if result["manifest"]["sample_count"] == 0:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
