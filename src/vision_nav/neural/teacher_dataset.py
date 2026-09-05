"""Validated camera/map-pair input contract for cloud teacher training.

This module is deliberately model-neutral. Video2BEV, Bearing-UAV, and other
teacher adapters can consume the same verified camera frame, positive map crop,
spatial hard-negative crops, ENU target, and PX4 heading target without taking
runtime dependencies into the aircraft code.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from vision_nav.training_capture import sha256_file
from vision_nav.camera_extrinsics import BODY_FRAME_ID, CAMERA_OPTICAL_FRAME_ID, TRANSFORM_DIRECTION

from .training_map_pairs import MAP_PAIR_SCHEMA


TEACHER_DATASET_SCHEMA = "vision_nav_teacher_dataset_v1"


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Could not read {label}: {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return raw


def _read_jsonl(path: Path, label: str) -> list[dict[str, Any]]:
    if not path.is_file():
        raise ValueError(f"Missing {label}: {path}")
    records: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{label}:{line_number} is invalid JSON: {exc}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"{label}:{line_number} must be a JSON object.")
        records.append(record)
    return records


def _read_image(path_value: Any, label: str) -> np.ndarray:
    if not isinstance(path_value, str) or not path_value:
        raise ValueError(f"{label} is missing an image path.")
    path = Path(path_value).expanduser()
    if not path.is_file():
        raise ValueError(f"{label} does not exist: {path}")
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None or image.ndim != 3 or image.shape[0] <= 0 or image.shape[1] <= 0:
        raise ValueError(f"{label} is not a readable color image: {path}")
    return image


def _finite_vector(raw: Any, keys: tuple[str, ...], *, length: int) -> np.ndarray | None:
    if not isinstance(raw, dict):
        return None
    try:
        values = np.asarray([float(raw[key]) for key in keys], dtype=np.float32)
    except (KeyError, TypeError, ValueError):
        return None
    if values.shape != (length,) or not np.all(np.isfinite(values)):
        return None
    return values


def pose_context(sample: dict[str, Any]) -> dict[str, Any]:
    """Extract explicit PX4/camera geometry for an IMU-aware teacher batch.

    Invalid values use zero/identity placeholders plus validity flags. A
    training job consuming this context must request strict validation; the
    base contract remains usable for public image-only upstream datasets.
    """

    attitude = sample.get("attitude") if isinstance(sample.get("attitude"), dict) else {}
    attitude_rpy = _finite_vector(attitude, ("roll_rad", "pitch_rad", "yaw_rad"), length=3)
    extrinsics = sample.get("camera_to_body") if isinstance(sample.get("camera_to_body"), dict) else {}
    translation = _finite_vector(extrinsics.get("translation_m"), ("x", "y", "z"), length=3)
    rotation = _finite_vector(extrinsics.get("rotation_quat_xyzw"), ("x", "y", "z", "w"), length=4)
    rotation_valid = rotation is not None and abs(float(np.linalg.norm(rotation)) - 1.0) <= 1e-3
    extrinsics_valid = (
        extrinsics.get("status") == "measured"
        and extrinsics.get("frame_id") == BODY_FRAME_ID
        and extrinsics.get("child_frame_id") == CAMERA_OPTICAL_FRAME_ID
        and extrinsics.get("transform_direction") == TRANSFORM_DIRECTION
        and translation is not None
        and rotation_valid
    )
    return {
        "attitude_rpy_rad": attitude_rpy if attitude_rpy is not None else np.zeros(3, dtype=np.float32),
        "attitude_valid": attitude_rpy is not None,
        "camera_translation_body_frd_m": translation if translation is not None else np.zeros(3, dtype=np.float32),
        "camera_rotation_camera_optical_to_body_frd_xyzw": rotation if rotation_valid else np.asarray([0.0, 0.0, 0.0, 1.0], dtype=np.float32),
        "camera_extrinsics_valid": extrinsics_valid,
    }


def _validate_record(record: dict[str, Any], *, split_name: str) -> dict[str, Any]:
    if record.get("schema_version") != MAP_PAIR_SCHEMA:
        raise ValueError(f"{split_name} pair record has unsupported schema.")
    sample = record.get("sample") if isinstance(record.get("sample"), dict) else {}
    supervision = record.get("supervision") if isinstance(record.get("supervision"), dict) else {}
    target = supervision.get("position_enu_m") if isinstance(supervision.get("position_enu_m"), dict) else {}
    if not isinstance(sample.get("sample_id"), str) or not sample["sample_id"]:
        raise ValueError(f"{split_name} pair record is missing sample.sample_id.")
    for key in ("east", "north", "up"):
        if not isinstance(target.get(key), (int, float)):
            raise ValueError(f"{split_name} pair {sample['sample_id']} is missing ENU {key} target.")
    positive = record.get("positive") if isinstance(record.get("positive"), dict) else {}
    negatives = record.get("spatial_hard_negatives")
    if not isinstance(negatives, list) or not negatives:
        raise ValueError(f"{split_name} pair {sample['sample_id']} has no spatial hard negatives.")
    _read_image(sample.get("frame_path"), f"{split_name} UAV frame for {sample['sample_id']}")
    _read_image(positive.get("image_path"), f"{split_name} positive map crop for {sample['sample_id']}")
    for index, negative in enumerate(negatives):
        if not isinstance(negative, dict):
            raise ValueError(f"{split_name} pair {sample['sample_id']} has an invalid negative at {index}.")
        _read_image(negative.get("image_path"), f"{split_name} negative map crop {index} for {sample['sample_id']}")
    context = pose_context(sample)
    return {
        "sample_id": sample["sample_id"],
        "frame_path": sample["frame_path"],
        "positive_image_path": positive["image_path"],
        "negative_count": len(negatives),
        "heading_available": isinstance(supervision.get("heading_rad"), (int, float)),
        "attitude_available": bool(context["attitude_valid"]),
        "camera_extrinsics_available": bool(context["camera_extrinsics_valid"]),
    }


def validate_teacher_dataset(
    pair_dir: str | Path, *, require_heading: bool = False, require_pose_context: bool = False,
) -> dict[str, Any]:
    """Validate every image and contract field before a cloud trainer opens it."""

    root = Path(pair_dir).expanduser().resolve()
    manifest_path = root / "manifest.json"
    manifest = _read_json(manifest_path, "training map-pair manifest")
    if manifest.get("schema_version") != MAP_PAIR_SCHEMA:
        raise ValueError("Training map-pair manifest has unsupported schema.")
    if manifest.get("policy") != "offline_training_only":
        raise ValueError("Teacher dataset must be marked offline_training_only.")
    split_summaries: dict[str, Any] = {}
    for split_name in ("train", "validation"):
        split_info = manifest.get("splits") if isinstance(manifest.get("splits"), dict) else {}
        split = split_info.get(split_name) if isinstance(split_info.get(split_name), dict) else {}
        source = root / str(split.get("path") or f"{split_name}.jsonl")
        expected_digest = split.get("sha256")
        if not isinstance(expected_digest, str) or sha256_file(source) != expected_digest:
            raise ValueError(f"Teacher dataset {split_name} checksum mismatch.")
        records = _read_jsonl(source, f"{split_name} map pairs")
        if not records:
            raise ValueError(f"Teacher dataset {split_name} map-pair file is empty.")
        checked = [_validate_record(record, split_name=split_name) for record in records]
        if require_heading and any(not record["heading_available"] for record in checked):
            raise ValueError(f"Teacher dataset {split_name} has samples without PX4 heading targets.")
        if require_pose_context and any(
            not record["attitude_available"] or not record["camera_extrinsics_available"] for record in checked
        ):
            raise ValueError(
                f"Teacher dataset {split_name} has samples without measured PX4 attitude and body_frd_T_camera_optical context."
            )
        split_summaries[split_name] = {
            "pair_count": len(checked),
            "negative_count_min": min(record["negative_count"] for record in checked),
            "negative_count_max": max(record["negative_count"] for record in checked),
            "heading_available_count": sum(1 for record in checked if record["heading_available"]),
            "attitude_available_count": sum(1 for record in checked if record["attitude_available"]),
            "camera_extrinsics_available_count": sum(1 for record in checked if record["camera_extrinsics_available"]),
            "sample_ids": [record["sample_id"] for record in checked],
        }
    return {
        "schema_version": TEACHER_DATASET_SCHEMA,
        "status": "valid",
        "pair_dir": str(root),
        "manifest_sha256": sha256_file(manifest_path),
        "policy": manifest["policy"],
        "require_heading": require_heading,
        "require_pose_context": require_pose_context,
        "splits": split_summaries,
    }


def _to_tensor(image_bgr: np.ndarray, image_size: tuple[int, int]) -> Any:
    try:
        import torch
    except ImportError as exc:
        raise RuntimeError("Teacher dataset tensors require drone-vision-nav[research] (PyTorch).") from exc
    width, height = image_size
    resized = cv2.resize(image_bgr, (width, height), interpolation=cv2.INTER_AREA)
    rgb = cv2.cvtColor(resized, cv2.COLOR_BGR2RGB)
    return torch.from_numpy(np.ascontiguousarray(rgb.transpose(2, 0, 1))).float().div_(255.0)


@dataclass(frozen=True)
class TeacherDatasetConfig:
    image_width: int = 320
    image_height: int = 320
    include_negatives: bool = True
    require_pose_context: bool = True


class CrossViewTeacherDataset:
    """PyTorch Dataset-style view of one verified frame/map-pair split."""

    def __init__(self, pair_dir: str | Path, *, split: str = "train", config: TeacherDatasetConfig | None = None) -> None:
        if split not in {"train", "validation"}:
            raise ValueError("Teacher dataset split must be train or validation.")
        self._config = config or TeacherDatasetConfig()
        if self._config.image_width <= 0 or self._config.image_height <= 0:
            raise ValueError("Teacher dataset image dimensions must be positive.")
        validate_teacher_dataset(pair_dir, require_pose_context=self._config.require_pose_context)
        root = Path(pair_dir).expanduser().resolve()
        manifest = _read_json(root / "manifest.json", "training map-pair manifest")
        split_path = root / str(manifest["splits"][split]["path"])
        self._records = _read_jsonl(split_path, f"{split} map pairs")

    def __len__(self) -> int:
        return len(self._records)

    def __getitem__(self, index: int) -> dict[str, Any]:
        record = self._records[index]
        sample = record["sample"]
        supervision = record["supervision"]
        image_size = (self._config.image_width, self._config.image_height)
        item: dict[str, Any] = {
            "sample_id": sample["sample_id"],
            "uav_rgb": _to_tensor(_read_image(sample["frame_path"], "UAV frame"), image_size),
            "positive_map_rgb": _to_tensor(_read_image(record["positive"]["image_path"], "positive map crop"), image_size),
            "position_enu_m": np.asarray(
                [supervision["position_enu_m"][key] for key in ("east", "north", "up")], dtype=np.float32
            ),
            "heading_rad": supervision.get("heading_rad"),
            "heading_valid": supervision.get("heading_rad") is not None,
            **pose_context(sample),
        }
        if self._config.include_negatives:
            item["negative_map_rgb"] = [
                _to_tensor(_read_image(negative["image_path"], "negative map crop"), image_size)
                for negative in record["spatial_hard_negatives"]
            ]
        return item


def main() -> None:
    parser = argparse.ArgumentParser(description="Validate a tensor-ready learned teacher camera/map-pair dataset.")
    parser.add_argument("--pair-dir", required=True, type=Path)
    parser.add_argument("--require-heading", action="store_true")
    parser.add_argument("--require-pose-context", action="store_true")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = validate_teacher_dataset(
            args.pair_dir, require_heading=args.require_heading, require_pose_context=args.require_pose_context,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
