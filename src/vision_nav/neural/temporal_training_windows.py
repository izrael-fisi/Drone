"""Build auditable, session-local temporal windows for learned cross-view training.

The cloud teacher needs consecutive UAV frames and matched PX4 attitude, while
the Pi must later consume a bounded temporal window with the same frame order.
This module derives that evidence from already verified map pairs. It never
creates a flight-runtime asset and never blends frames across a split/session.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import numpy as np

from vision_nav.training_capture import sha256_file

from .teacher_dataset import _read_image, _to_tensor, pose_context, validate_teacher_dataset
from .training_map_pairs import MAP_PAIR_SCHEMA


TEMPORAL_TRAINING_WINDOWS_SCHEMA = "vision_nav_temporal_training_windows_v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read {label}: {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return raw


def _read_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f"Missing {label}: {path}")
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{label}:{line_number} is not valid JSON: {exc}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{label}:{line_number} must be a JSON object.")
        rows.append(row)
    return rows


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as stream:
        for row in rows:
            stream.write(json.dumps(row, sort_keys=True) + "\n")


def _finite_vector(raw: Any, *, length: int) -> bool:
    if not isinstance(raw, list) or len(raw) != length:
        return False
    try:
        return all(np.isfinite(float(value)) for value in raw)
    except (TypeError, ValueError):
        return False


def _validate_window_record(
    record: dict[str, Any], *, split_name: str, require_pose_context: bool, max_frame_gap_ms: float,
) -> dict[str, Any]:
    if record.get("schema_version") != TEMPORAL_TRAINING_WINDOWS_SCHEMA:
        raise ValueError(f"{split_name} temporal record has unsupported schema.")
    window_id = record.get("window_id")
    session_id = record.get("capture_session_id")
    frames = record.get("frames")
    if not isinstance(window_id, str) or not window_id or not isinstance(session_id, str) or not session_id:
        raise ValueError(f"{split_name} temporal record is missing its window or capture-session ID.")
    if not isinstance(frames, list) or len(frames) < 2 or record.get("frame_count") != len(frames):
        raise ValueError(f"{split_name} temporal window {window_id} has invalid frame_count.")
    anchor_index = record.get("anchor_index")
    anchor = record.get("anchor") if isinstance(record.get("anchor"), dict) else {}
    if anchor_index != len(frames) - 1 or anchor.get("sample_id") != (frames[-1] or {}).get("sample_id"):
        raise ValueError(f"{split_name} temporal window {window_id} has an invalid supervised anchor.")
    supervision = anchor.get("supervision") if isinstance(anchor.get("supervision"), dict) else {}
    position = supervision.get("position_enu_m") if isinstance(supervision.get("position_enu_m"), dict) else {}
    if not all(isinstance(position.get(key), (int, float)) for key in ("east", "north", "up")):
        raise ValueError(f"{split_name} temporal window {window_id} anchor lacks ENU supervision.")
    positive = anchor.get("positive") if isinstance(anchor.get("positive"), dict) else {}
    if not isinstance(positive.get("image_path"), str) or not positive["image_path"]:
        raise ValueError(f"{split_name} temporal window {window_id} anchor lacks its positive map crop.")
    negatives = anchor.get("spatial_hard_negatives")
    if not isinstance(negatives, list) or not negatives or any(
        not isinstance(negative, dict) or not isinstance(negative.get("image_path"), str) or not negative["image_path"]
        for negative in negatives
    ):
        raise ValueError(f"{split_name} temporal window {window_id} anchor lacks valid hard-negative map crops.")
    map_ids = {record.get("map_release_id")}
    calibration_ids = {record.get("calibration_id")}
    host_times: list[int] = []
    sensor_times: list[int] = []
    for frame_index, frame in enumerate(frames):
        if not isinstance(frame, dict):
            raise ValueError(f"{split_name} temporal window {window_id} frame {frame_index} is not an object.")
        if frame.get("capture_session_id") != session_id:
            raise ValueError(f"{split_name} temporal window {window_id} crosses a capture-session boundary.")
        if not isinstance(frame.get("sample_id"), str) or not isinstance(frame.get("frame_path"), str):
            raise ValueError(f"{split_name} temporal window {window_id} frame {frame_index} lacks a sample or image path.")
        host_time, sensor_time = frame.get("timestamp_ns"), frame.get("sensor_timestamp_ns")
        if not isinstance(host_time, int) or host_time <= 0 or not isinstance(sensor_time, int) or sensor_time <= 0:
            raise ValueError(f"{split_name} temporal window {window_id} frame {frame_index} has invalid timestamps.")
        host_times.append(host_time)
        sensor_times.append(sensor_time)
        map_ids.add(frame.get("map_release_id"))
        calibration_ids.add(frame.get("calibration_id"))
        if require_pose_context and (
            not frame.get("attitude_valid")
            or not frame.get("camera_extrinsics_valid")
            or not _finite_vector(frame.get("attitude_rpy_rad"), length=3)
            or not _finite_vector(frame.get("camera_translation_body_frd_m"), length=3)
            or not _finite_vector(frame.get("camera_rotation_camera_optical_to_body_frd_xyzw"), length=4)
        ):
            raise ValueError(f"{split_name} temporal window {window_id} frame {frame_index} lacks measured pose context.")
    if len(map_ids) != 1 or None in map_ids or len(calibration_ids) != 1 or None in calibration_ids:
        raise ValueError(f"{split_name} temporal window {window_id} mixes map or calibration references.")
    if len({frame.get("timing_policy", "legacy_host_receipt") for frame in frames}) != 1:
        raise ValueError(f"{split_name} temporal window mixes timing policies")
    if any(later <= earlier for earlier, later in zip(host_times, host_times[1:])):
        raise ValueError(f"{split_name} temporal window {window_id} has non-monotonic host timestamps.")
    if any(later <= earlier for earlier, later in zip(sensor_times, sensor_times[1:])):
        raise ValueError(f"{split_name} temporal window {window_id} has non-monotonic sensor timestamps.")
    max_gap_ns = int(max_frame_gap_ms * 1_000_000.0)
    if any(later - earlier > max_gap_ns for earlier, later in zip(host_times, host_times[1:])):
        raise ValueError(f"{split_name} temporal window {window_id} exceeds the declared frame-gap limit.")
    if any(later - earlier > max_gap_ns for earlier, later in zip(sensor_times, sensor_times[1:])):
        raise ValueError(f"{split_name} temporal window {window_id} exceeds the sensor frame-gap limit.")
    return {
        "window_id": window_id,
        "frame_count": len(frames),
        "capture_session_id": session_id,
        "map_release_id": next(iter(map_ids)),
        "calibration_id": next(iter(calibration_ids)),
    }


def _frame_context(record: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
    sample = record.get("sample") if isinstance(record.get("sample"), dict) else {}
    sample_id = sample.get("sample_id")
    session_id = sample.get("capture_session_id")
    timestamp_ns = sample.get("timestamp_ns")
    timing = sample.get("capture_timing") if isinstance(sample.get("capture_timing"), dict) else {}
    sensor_timestamp_ns = timing.get("sensor_timestamp_ns")
    if not isinstance(sample_id, str) or not sample_id:
        return None, "missing_sample_id"
    if not isinstance(session_id, str) or not session_id:
        return None, "missing_capture_session_id"
    if not isinstance(timestamp_ns, int) or timestamp_ns <= 0:
        return None, "missing_host_monotonic_timestamp"
    if not isinstance(sensor_timestamp_ns, int) or sensor_timestamp_ns <= 0:
        return None, "missing_camera_sensor_timestamp"
    context = pose_context(sample)
    # ``pose_context`` is also used by PyTorch datasets, so it deliberately
    # returns NumPy vectors. Window manifests are durable JSON contracts and
    # must preserve only serializable metadata, never in-memory tensors.
    attitude = context["attitude_rpy_rad"]
    translation = context["camera_translation_body_frd_m"]
    rotation = context["camera_rotation_camera_optical_to_body_frd_xyzw"]
    return {
        "sample_id": sample_id,
        "capture_session_id": session_id,
        "frame_path": sample.get("frame_path"),
        "frame_sha256": sample.get("frame_sha256"),
        "timestamp_ns": timestamp_ns,
        "sensor_timestamp_ns": sensor_timestamp_ns,
        "timing_policy": sample.get("timing_policy", "legacy_host_receipt"),
        "timing_audit": sample.get("timing_audit"),
        "map_release_id": sample.get("map_release_id"),
        "calibration_id": sample.get("calibration_id"),
        "attitude_rpy_rad": [float(value) for value in attitude],
        "attitude_valid": bool(context["attitude_valid"]),
        "camera_translation_body_frd_m": [float(value) for value in translation],
        "camera_rotation_camera_optical_to_body_frd_xyzw": [float(value) for value in rotation],
        "camera_extrinsics_valid": bool(context["camera_extrinsics_valid"]),
    }, None


def _window_record(entries: list[dict[str, Any]], source_records: list[dict[str, Any]]) -> tuple[dict[str, Any] | None, str | None]:
    sample_ids = {entry["sample_id"] for entry in entries}
    source_by_id = {
        str((record.get("sample") or {}).get("sample_id")): record
        for record in source_records
        if isinstance(record.get("sample"), dict)
    }
    if len(source_by_id) != len(source_records) or set(source_by_id) != sample_ids:
        return None, "window_source_record_mismatch"
    map_ids = {entry.get("map_release_id") for entry in entries}
    calibration_ids = {entry.get("calibration_id") for entry in entries}
    if len(map_ids) != 1 or None in map_ids:
        return None, "mixed_or_missing_map_reference"
    if len(calibration_ids) != 1 or None in calibration_ids:
        return None, "mixed_or_missing_calibration"
    if len({entry.get("timing_policy", "legacy_host_receipt") for entry in entries}) != 1:
        return None, "mixed_timing_policies"
    anchor_record = source_by_id[entries[-1]["sample_id"]]
    anchor = {
        "sample_id": entries[-1]["sample_id"],
        "supervision": anchor_record.get("supervision"),
        "positive": anchor_record.get("positive"),
        "spatial_hard_negatives": anchor_record.get("spatial_hard_negatives"),
    }
    return {
        "schema_version": TEMPORAL_TRAINING_WINDOWS_SCHEMA,
        "window_id": f"{entries[0]['sample_id']}--{entries[-1]['sample_id']}",
        "capture_session_id": entries[0]["capture_session_id"],
        "frame_count": len(entries),
        "anchor_index": len(entries) - 1,
        "map_release_id": entries[-1]["map_release_id"],
        "calibration_id": entries[-1]["calibration_id"],
        "frames": entries,
        "anchor": anchor,
    }, None


def build_temporal_training_windows(
    pair_dir: str | Path,
    output_dir: str | Path,
    *,
    window_size: int = 4,
    stride: int = 1,
    max_frame_gap_ms: float = 1_000.0,
    require_pose_context: bool = True,
) -> dict[str, Any]:
    """Create consecutive, same-session windows from verified training map pairs."""

    if window_size < 2:
        raise ValueError("window_size must be at least 2 for temporal training.")
    if stride <= 0 or max_frame_gap_ms <= 0.0:
        raise ValueError("stride and max_frame_gap_ms must be positive.")
    source_root = Path(pair_dir).expanduser().resolve()
    validate_teacher_dataset(source_root, require_pose_context=require_pose_context)
    source_manifest_path = source_root / "manifest.json"
    source_manifest = _read_json(source_manifest_path, "training map-pair manifest")
    if source_manifest.get("schema_version") != MAP_PAIR_SCHEMA:
        raise ValueError("Temporal windows require a vision_nav_training_map_pairs_v1 package.")
    root = Path(output_dir).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError(f"Temporal-training output directory must be empty: {root}")
    root.mkdir(parents=True, exist_ok=True)

    split_outputs: dict[str, dict[str, Any]] = {}
    max_gap_ns = int(max_frame_gap_ms * 1_000_000.0)
    for split_name in ("train", "validation"):
        split_info = (source_manifest.get("splits") or {}).get(split_name) or {}
        source_path = source_root / str(split_info.get("path") or f"{split_name}.jsonl")
        expected_digest = split_info.get("sha256")
        if not isinstance(expected_digest, str) or sha256_file(source_path) != expected_digest:
            raise ValueError(f"Temporal windows source {split_name} map-pair checksum mismatch.")
        records = _read_jsonl(source_path, f"{split_name} map pairs")
        sessions: dict[str, list[tuple[dict[str, Any], dict[str, Any]]]] = {}
        rejected: dict[str, int] = {}
        for record in records:
            context, reason = _frame_context(record)
            if context is None:
                rejected[str(reason)] = rejected.get(str(reason), 0) + 1
                continue
            session_id = str((record.get("sample") or {}).get("capture_session_id"))
            sessions.setdefault(session_id, []).append((context, record))
        windows: list[dict[str, Any]] = []
        for session_id, session_rows in sorted(sessions.items()):
            session_rows.sort(key=lambda item: (item[0]["timestamp_ns"], item[0]["sample_id"]))
            for start in range(0, max(0, len(session_rows) - window_size + 1), stride):
                slice_rows = session_rows[start:start + window_size]
                entries = [item[0] for item in slice_rows]
                records_for_window = [item[1] for item in slice_rows]
                host_times = [entry["timestamp_ns"] for entry in entries]
                sensor_times = [entry["sensor_timestamp_ns"] for entry in entries]
                if any(later <= earlier for earlier, later in zip(host_times, host_times[1:])):
                    rejected["non_monotonic_host_timestamps"] = rejected.get("non_monotonic_host_timestamps", 0) + 1
                    continue
                if any(later <= earlier for earlier, later in zip(sensor_times, sensor_times[1:])):
                    rejected["non_monotonic_sensor_timestamps"] = rejected.get("non_monotonic_sensor_timestamps", 0) + 1
                    continue
                if any(later - earlier > max_gap_ns for earlier, later in zip(host_times, host_times[1:])):
                    rejected["frame_gap_exceeds_limit"] = rejected.get("frame_gap_exceeds_limit", 0) + 1
                    continue
                if any(later - earlier > max_gap_ns for earlier, later in zip(sensor_times, sensor_times[1:])):
                    rejected["sensor_frame_gap_exceeds_limit"] = rejected.get("sensor_frame_gap_exceeds_limit", 0) + 1
                    continue
                window, reason = _window_record(entries, records_for_window)
                if window is None:
                    rejected[str(reason)] = rejected.get(str(reason), 0) + 1
                    continue
                if window["capture_session_id"] != session_id:
                    raise ValueError("Temporal window unexpectedly crossed a capture-session boundary.")
                windows.append(window)
        if not windows:
            detail = ", ".join(f"{key}={value}" for key, value in sorted(rejected.items())) or "insufficient_frames_per_session"
            raise ValueError(f"Temporal windows have no valid {split_name} output: {detail}")
        output_path = root / f"{split_name}.jsonl"
        _write_jsonl(output_path, windows)
        split_outputs[split_name] = {
            "path": output_path.name,
            "sha256": sha256_file(output_path),
            "window_count": len(windows),
            "capture_session_ids": sorted(sessions),
            "rejection_counts": rejected,
        }
    manifest = {
        "schema_version": TEMPORAL_TRAINING_WINDOWS_SCHEMA,
        "created_utc": _utc_now(),
        "policy": "offline_training_only",
        "source_map_pairs": {
            "path": str(source_root),
            "manifest_sha256": sha256_file(source_manifest_path),
            "map_source": source_manifest.get("map_source"),
        },
        "temporal_policy": {
            "window_size": window_size,
            "stride": stride,
            "max_frame_gap_ms": max_frame_gap_ms,
            "require_pose_context": require_pose_context,
            "session_boundary": "required",
            "camera_sensor_timestamps": "strictly_monotonic_required",
        },
        "splits": split_outputs,
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"output_dir": str(root), "manifest_path": str(manifest_path), "manifest": manifest}


def validate_temporal_training_windows(
    window_dir: str | Path, *, require_pose_context: bool = True,
) -> dict[str, Any]:
    """Validate a portable temporal package before a cloud trainer reads it.

    Source map pairs may live in a different object store by this point, so the
    source manifest digest is retained as provenance rather than requiring its
    original absolute path to remain mounted. The temporal package itself is
    fully checked: split hashes, ordering, session boundaries, anchors, and
    camera/PX4 pose context all fail closed.
    """

    root = Path(window_dir).expanduser().resolve()
    manifest_path = root / "manifest.json"
    manifest = _read_json(manifest_path, "temporal-training manifest")
    if manifest.get("schema_version") != TEMPORAL_TRAINING_WINDOWS_SCHEMA:
        raise ValueError("Temporal-training manifest has unsupported schema.")
    if manifest.get("policy") != "offline_training_only":
        raise ValueError("Temporal-training package must be marked offline_training_only.")
    policy = manifest.get("temporal_policy") if isinstance(manifest.get("temporal_policy"), dict) else {}
    max_gap_ms = policy.get("max_frame_gap_ms")
    if not isinstance(max_gap_ms, (int, float)) or float(max_gap_ms) <= 0.0:
        raise ValueError("Temporal-training manifest has an invalid max_frame_gap_ms policy.")
    split_summary: dict[str, Any] = {}
    for split_name in ("train", "validation"):
        split = (manifest.get("splits") or {}).get(split_name)
        if not isinstance(split, dict):
            raise ValueError(f"Temporal-training manifest is missing {split_name} split metadata.")
        source = root / str(split.get("path") or f"{split_name}.jsonl")
        expected_digest = split.get("sha256")
        if not isinstance(expected_digest, str) or sha256_file(source) != expected_digest:
            raise ValueError(f"Temporal-training {split_name} checksum mismatch.")
        records = _read_jsonl(source, f"{split_name} temporal windows")
        if not records:
            raise ValueError(f"Temporal-training {split_name} split is empty.")
        checked = [
            _validate_window_record(
                record, split_name=split_name, require_pose_context=require_pose_context, max_frame_gap_ms=float(max_gap_ms),
            )
            for record in records
        ]
        if len({record["window_id"] for record in checked}) != len(checked):
            raise ValueError(f"Temporal-training {split_name} has duplicate window IDs.")
        declared_count = split.get("window_count")
        if declared_count != len(checked):
            raise ValueError(f"Temporal-training {split_name} window_count does not match its data.")
        split_summary[split_name] = {
            "window_count": len(checked),
            "frame_count_min": min(record["frame_count"] for record in checked),
            "frame_count_max": max(record["frame_count"] for record in checked),
            "capture_session_ids": sorted({record["capture_session_id"] for record in checked}),
            "map_release_ids": sorted({record["map_release_id"] for record in checked}),
            "calibration_ids": sorted({record["calibration_id"] for record in checked}),
        }
    return {
        "schema_version": TEMPORAL_TRAINING_WINDOWS_SCHEMA,
        "status": "valid",
        "window_dir": str(root),
        "manifest_sha256": sha256_file(manifest_path),
        "require_pose_context": require_pose_context,
        "splits": split_summary,
    }


class TemporalCrossViewTeacherDataset:
    """Tensor-ready temporal windows for a project-owned CUDA teacher adapter.

    The class intentionally owns no augmentation or model behavior. It gives a
    teacher one `[T,C,H,W]` RGB window, per-frame PX4/camera context, and the
    final-frame map supervision under an already validated release contract.
    """

    def __init__(self, window_dir: str | Path, *, split: str = "train", image_size: tuple[int, int] = (320, 320)) -> None:
        if split not in {"train", "validation"}:
            raise ValueError("Temporal teacher dataset split must be train or validation.")
        if image_size[0] <= 0 or image_size[1] <= 0:
            raise ValueError("Temporal teacher dataset image dimensions must be positive.")
        validate_temporal_training_windows(window_dir, require_pose_context=True)
        root = Path(window_dir).expanduser().resolve()
        manifest = _read_json(root / "manifest.json", "temporal-training manifest")
        split_path = root / str(manifest["splits"][split]["path"])
        self._records = _read_jsonl(split_path, f"{split} temporal windows")
        self._image_size = image_size

    def __len__(self) -> int:
        return len(self._records)

    def __getitem__(self, index: int) -> dict[str, Any]:
        try:
            import torch
        except ImportError as exc:
            raise RuntimeError("Temporal teacher tensors require drone-vision-nav[research] (PyTorch).") from exc
        record = self._records[index]
        frames = record["frames"]
        anchor = record["anchor"]
        supervision = anchor["supervision"]
        enu = supervision["position_enu_m"]
        return {
            "window_id": record["window_id"],
            "capture_session_id": record["capture_session_id"],
            "frame_rgb": torch.stack([_to_tensor(_read_image(frame["frame_path"], "temporal UAV frame"), self._image_size) for frame in frames]),
            "attitude_rpy_rad": torch.tensor([frame["attitude_rpy_rad"] for frame in frames], dtype=torch.float32),
            "camera_translation_body_frd_m": torch.tensor(
                [frame["camera_translation_body_frd_m"] for frame in frames], dtype=torch.float32,
            ),
            "camera_rotation_camera_optical_to_body_frd_xyzw": torch.tensor(
                [frame["camera_rotation_camera_optical_to_body_frd_xyzw"] for frame in frames], dtype=torch.float32,
            ),
            "positive_map_rgb": _to_tensor(_read_image(record["anchor"]["positive"]["image_path"], "temporal positive map crop"), self._image_size),
            "negative_map_rgb": torch.stack([
                _to_tensor(_read_image(negative["image_path"], "temporal negative map crop"), self._image_size)
                for negative in anchor.get("spatial_hard_negatives", [])
            ]),
            "position_enu_m": torch.tensor([enu[key] for key in ("east", "north", "up")], dtype=torch.float32),
            "heading_rad": supervision.get("heading_rad"),
            "heading_valid": supervision.get("heading_rad") is not None,
        }


class TemporalWindowDataset:
    """Tensor-ready frame/attitude windows without teacher-only map crops."""

    def __init__(self, window_dir: str | Path, *, split: str = "train", image_size: tuple[int, int] = (320, 320)) -> None:
        if split not in {"train", "validation"}:
            raise ValueError("Temporal window dataset split must be train or validation.")
        if image_size[0] <= 0 or image_size[1] <= 0:
            raise ValueError("Temporal window dataset image dimensions must be positive.")
        validate_temporal_training_windows(window_dir, require_pose_context=True)
        root = Path(window_dir).expanduser().resolve()
        manifest = _read_json(root / "manifest.json", "temporal-training manifest")
        split_path = root / str(manifest["splits"][split]["path"])
        self._records = _read_jsonl(split_path, f"{split} temporal windows")
        self._image_size = image_size

    def __len__(self) -> int:
        return len(self._records)

    def __getitem__(self, index: int) -> dict[str, Any]:
        try:
            import torch
        except ImportError as exc:
            raise RuntimeError("Temporal window tensors require drone-vision-nav[research] (PyTorch).") from exc
        record = self._records[index]
        frames = record["frames"]
        return {
            "window_id": record["window_id"],
            "capture_session_id": record["capture_session_id"],
            "frame_rgb": torch.stack([
                _to_tensor(_read_image(frame["frame_path"], "temporal UAV frame"), self._image_size)
                for frame in frames
            ]),
            "attitude_rpy_rad": torch.tensor([frame["attitude_rpy_rad"] for frame in frames], dtype=torch.float32),
        }


def main() -> None:
    parser = argparse.ArgumentParser(description="Build session-bounded temporal teacher windows from verified map pairs.")
    parser.add_argument("--pair-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--window-size", type=int, default=4)
    parser.add_argument("--stride", type=int, default=1)
    parser.add_argument("--max-frame-gap-ms", type=float, default=1_000.0)
    parser.add_argument("--allow-missing-pose-context", action="store_true")
    args = parser.parse_args()
    try:
        result = build_temporal_training_windows(
            args.pair_dir, args.output_dir, window_size=args.window_size, stride=args.stride,
            max_frame_gap_ms=args.max_frame_gap_ms, require_pose_context=not args.allow_missing_pose_context,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result["manifest"], indent=2, sort_keys=True))


def validate_main() -> None:
    parser = argparse.ArgumentParser(description="Validate temporal learned-teacher windows before a CUDA training run.")
    parser.add_argument("--window-dir", required=True, type=Path)
    parser.add_argument("--allow-missing-pose-context", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = validate_temporal_training_windows(
            args.window_dir, require_pose_context=not args.allow_missing_pose_context,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
