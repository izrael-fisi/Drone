"""Materialize immutable temporal-teacher targets for Pi student distillation.

This records supervised teacher evidence only. It does not create an ONNX
artifact, promote a ModelRelease, or make a PX4-publishable navigation claim.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

import numpy as np

from vision_nav.training_capture import sha256_file

from .temporal_teacher_baseline import (
    TEMPORAL_TEACHER_BASELINE_SCHEMA,
    TemporalTeacherBaselineConfig,
    _single_batch,
    build_temporal_teacher_model,
    choose_torch_device,
)
from .temporal_training_windows import (
    TEMPORAL_TRAINING_WINDOWS_SCHEMA,
    TemporalCrossViewTeacherDataset,
    TemporalWindowDataset,
    validate_temporal_training_windows,
)


TEACHER_TARGETS_SCHEMA = "vision_nav_temporal_teacher_targets_v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _load_json(path: Path, label: str) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read {label}: {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return raw


def _target_stem(window_id: str) -> str:
    return hashlib.sha256(window_id.encode("utf-8")).hexdigest()[:20]


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


def _validate_target_npz(path: Path, *, label: str) -> dict[str, Any]:
    try:
        with np.load(path, allow_pickle=False) as archive:
            values = {name: np.asarray(archive[name]) for name in archive.files}
    except (OSError, ValueError) as exc:
        raise ValueError(f"Could not read {label}: {path}: {exc}") from exc
    expected = {"temporal_feature", "position_enu_m", "heading_sin_cos"}
    if set(values) != expected:
        raise ValueError(f"{label} has invalid target tensor names: {sorted(values)}.")
    if values["temporal_feature"].ndim != 1 or values["temporal_feature"].size <= 0:
        raise ValueError(f"{label} temporal_feature must be a non-empty vector.")
    if values["position_enu_m"].shape != (3,) or values["heading_sin_cos"].shape != (2,):
        raise ValueError(f"{label} has invalid position or heading target shape.")
    if any(not np.isfinite(value).all() for value in values.values()):
        raise ValueError(f"{label} contains non-finite targets.")
    return {"temporal_feature_dim": int(values["temporal_feature"].size)}


def validate_teacher_targets(target_dir: str | Path, *, window_dir: str | Path) -> dict[str, Any]:
    """Verify teacher target files stay bound to the exact source windows."""

    source_root = Path(window_dir).expanduser().resolve()
    source_audit = validate_temporal_training_windows(source_root, require_pose_context=True)
    source_manifest_digest = sha256_file(source_root / "manifest.json")
    root = Path(target_dir).expanduser().resolve()
    manifest_path = root / "manifest.json"
    manifest = _load_json(manifest_path, "teacher-target manifest")
    if manifest.get("schema_version") != TEACHER_TARGETS_SCHEMA or manifest.get("status") != "completed":
        raise ValueError("Teacher-target manifest has unsupported schema or is incomplete.")
    source = manifest.get("input") if isinstance(manifest.get("input"), dict) else {}
    if source.get("temporal_windows_schema") != TEMPORAL_TRAINING_WINDOWS_SCHEMA:
        raise ValueError("Teacher targets do not name the expected temporal-window schema.")
    if source.get("temporal_windows_manifest_sha256") != source_manifest_digest:
        raise ValueError("Teacher targets do not match the supplied temporal-window manifest.")
    splits = manifest.get("splits") if isinstance(manifest.get("splits"), dict) else {}
    summary: dict[str, Any] = {}
    for split_name in ("train", "validation"):
        split = splits.get(split_name)
        if not isinstance(split, dict):
            raise ValueError(f"Teacher targets are missing {split_name} split metadata.")
        index_path = root / str(split.get("path") or f"{split_name}.jsonl")
        expected_digest = split.get("sha256")
        if not isinstance(expected_digest, str) or sha256_file(index_path) != expected_digest:
            raise ValueError(f"Teacher target {split_name} index checksum mismatch.")
        records = _read_jsonl(index_path, f"{split_name} teacher targets")
        if not records or split.get("target_count") != len(records):
            raise ValueError(f"Teacher targets have invalid {split_name} target_count.")
        window_ids: set[str] = set()
        dimensions: set[int] = set()
        for record in records:
            window_id = record.get("window_id")
            target_path_value = record.get("target_npz")
            if not isinstance(window_id, str) or not window_id or window_id in window_ids:
                raise ValueError(f"Teacher targets {split_name} contains invalid or duplicate window IDs.")
            if not isinstance(target_path_value, str) or not target_path_value:
                raise ValueError(f"Teacher target {window_id} has no target_npz path.")
            target_path = root / target_path_value
            expected_target_digest = record.get("target_sha256")
            if not target_path.is_file() or not isinstance(expected_target_digest, str) or sha256_file(target_path) != expected_target_digest:
                raise ValueError(f"Teacher target {window_id} checksum mismatch.")
            dimensions.add(_validate_target_npz(target_path, label=f"Teacher target {window_id}")["temporal_feature_dim"])
            window_ids.add(window_id)
        if len(dimensions) != 1:
            raise ValueError(f"Teacher targets {split_name} mix temporal feature dimensions.")
        source_split_path = source_root / f"{split_name}.jsonl"
        source_window_ids = {record.get("window_id") for record in _read_jsonl(source_split_path, f"{split_name} temporal windows")}
        if window_ids != source_window_ids:
            raise ValueError(f"Teacher targets {split_name} do not cover exactly the source temporal windows.")
        summary[split_name] = {"target_count": len(records), "temporal_feature_dim": dimensions.pop()}
    if summary["train"]["temporal_feature_dim"] != summary["validation"]["temporal_feature_dim"]:
        raise ValueError("Teacher targets mix temporal feature dimensions across train and validation.")
    return {
        "schema_version": TEACHER_TARGETS_SCHEMA,
        "status": "valid",
        "target_dir": str(root),
        "manifest_sha256": sha256_file(manifest_path),
        "temporal_windows_manifest_sha256": source_manifest_digest,
        "source_validation": source_audit,
        "splits": summary,
    }


class TeacherTargetDataset:
    """Pi-input windows aligned to teacher targets via immutable window IDs."""

    def __init__(
        self, window_dir: str | Path, target_dir: str | Path, *, split: str = "train", image_size: tuple[int, int] = (320, 320),
    ) -> None:
        if split not in {"train", "validation"}:
            raise ValueError("Teacher target dataset split must be train or validation.")
        validate_teacher_targets(target_dir, window_dir=window_dir)
        self._windows = TemporalWindowDataset(window_dir, split=split, image_size=image_size)
        root = Path(target_dir).expanduser().resolve()
        records = _read_jsonl(root / f"{split}.jsonl", f"{split} teacher targets")
        self._target_paths = {str(record["window_id"]): root / str(record["target_npz"]) for record in records}

    def __len__(self) -> int:
        return len(self._windows)

    def __getitem__(self, index: int) -> dict[str, Any]:
        try:
            import torch
        except ImportError as exc:
            raise RuntimeError("Teacher target tensors require drone-vision-nav[research] (PyTorch).") from exc
        item = self._windows[index]
        path = self._target_paths.get(str(item["window_id"]))
        if path is None:
            raise ValueError(f"Teacher targets are missing temporal window {item['window_id']}.")
        with np.load(path, allow_pickle=False) as archive:
            item["teacher_temporal_feature"] = torch.tensor(archive["temporal_feature"], dtype=torch.float32)
            item["teacher_position_enu_m"] = torch.tensor(archive["position_enu_m"], dtype=torch.float32)
            item["teacher_heading_sin_cos"] = torch.tensor(archive["heading_sin_cos"], dtype=torch.float32)
        return item


def build_teacher_target_record(
    *, checkpoint_path: str | Path, window_dir: str | Path, device: str,
) -> dict[str, Any]:
    """Create target-export provenance before loading a cloud checkpoint."""

    checkpoint = Path(checkpoint_path).expanduser().resolve()
    if not checkpoint.is_file():
        raise ValueError(f"Temporal teacher checkpoint does not exist: {checkpoint}")
    audit = validate_temporal_training_windows(window_dir, require_pose_context=True)
    root = Path(window_dir).expanduser().resolve()
    manifest_path = root / "manifest.json"
    return {
        "schema_version": TEACHER_TARGETS_SCHEMA,
        "created_utc": _utc_now(),
        "status": "initialized",
        "publication_mode": "research_only",
        "px4_external_vision": "disabled",
        "teacher_checkpoint": {"path": str(checkpoint), "sha256": sha256_file(checkpoint)},
        "input": {
            "temporal_windows_dir": str(root),
            "temporal_windows_schema": TEMPORAL_TRAINING_WINDOWS_SCHEMA,
            "temporal_windows_manifest_sha256": sha256_file(manifest_path),
            "validation": audit,
        },
        "device": device,
        "target_contract": {
            "temporal_feature": "teacher normalized temporal feature vector",
            "position_enu_m": "teacher regression output for an offline distillation loss",
            "heading_sin_cos": "teacher heading output for an offline distillation loss",
        },
    }


def _load_teacher(checkpoint_path: Path, *, device: str, expected_window_digest: str) -> tuple[Any, TemporalTeacherBaselineConfig, Any]:
    try:
        import torch
    except ImportError as exc:
        raise RuntimeError("Teacher-target export requires drone-vision-nav[research] (PyTorch).") from exc
    try:
        payload = torch.load(checkpoint_path, map_location=device, weights_only=True)
    except TypeError:
        payload = torch.load(checkpoint_path, map_location=device)
    if not isinstance(payload, dict) or payload.get("schema_version") != TEMPORAL_TEACHER_BASELINE_SCHEMA:
        raise ValueError("Teacher-target export requires a temporal teacher baseline checkpoint.")
    if payload.get("input_temporal_windows_manifest_sha256") != expected_window_digest:
        raise ValueError("Teacher checkpoint does not match the supplied temporal-window manifest.")
    raw_config = payload.get("config")
    if not isinstance(raw_config, dict):
        raise ValueError("Teacher checkpoint is missing its model configuration.")
    try:
        config = TemporalTeacherBaselineConfig(**raw_config)
        config.validate()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Teacher checkpoint has invalid model configuration: {exc}") from exc
    model = build_temporal_teacher_model(config).to(device)
    model.load_state_dict(payload.get("state_dict", {}), strict=True)
    model.eval()
    return model, config, torch


def materialize_teacher_targets(
    checkpoint_path: str | Path,
    window_dir: str | Path,
    output_dir: str | Path,
    *,
    device: str = "auto",
    max_batches_per_split: int | None = None,
) -> dict[str, Any]:
    """Write per-window teacher target NPZ files with split checksums."""

    if max_batches_per_split is not None and max_batches_per_split <= 0:
        raise ValueError("max_batches_per_split must be positive when specified.")
    resolved_device = choose_torch_device(device)
    report = build_teacher_target_record(checkpoint_path=checkpoint_path, window_dir=window_dir, device=resolved_device)
    source_root = Path(window_dir).expanduser().resolve()
    checkpoint = Path(checkpoint_path).expanduser().resolve()
    root = Path(output_dir).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError(f"Teacher-target output directory must be empty: {root}")
    model, config, torch = _load_teacher(
        checkpoint, device=resolved_device, expected_window_digest=report["input"]["temporal_windows_manifest_sha256"],
    )
    root.mkdir(parents=True, exist_ok=True)
    split_summaries: dict[str, Any] = {}
    with torch.no_grad():
        for split_name in ("train", "validation"):
            dataset = TemporalCrossViewTeacherDataset(
                source_root, split=split_name, image_size=(config.image_width, config.image_height),
            )
            target_root = root / "targets" / split_name
            target_root.mkdir(parents=True, exist_ok=True)
            records: list[dict[str, Any]] = []
            for index in range(len(dataset)):
                if max_batches_per_split is not None and index >= max_batches_per_split:
                    break
                item = dataset[index]
                # The teacher's map branch needs the positive/hard-negative crop
                # bank. Reuse the training batch constructor to preserve order.
                batch = _single_batch(item, resolved_device)
                output = model(batch["frame_rgb"], batch["attitude_rpy_rad"], batch["map_crops_rgb"])
                window_id = str(item["window_id"])
                relative_npz = Path("targets") / split_name / f"{_target_stem(window_id)}.npz"
                npz_path = root / relative_npz
                np.savez_compressed(
                    npz_path,
                    temporal_feature=output["temporal_feature"].detach().cpu().numpy()[0].astype(np.float32),
                    position_enu_m=output["position_enu_m"].detach().cpu().numpy()[0].astype(np.float32),
                    heading_sin_cos=output["heading_sin_cos"].detach().cpu().numpy()[0].astype(np.float32),
                )
                records.append({
                    "window_id": window_id,
                    "capture_session_id": item["capture_session_id"],
                    "target_npz": relative_npz.as_posix(),
                    "target_sha256": sha256_file(npz_path),
                })
            if not records:
                raise ValueError(f"Teacher-target export has no {split_name} windows.")
            index_path = root / f"{split_name}.jsonl"
            index_path.write_text("".join(json.dumps(record, sort_keys=True) + "\n" for record in records), encoding="utf-8")
            split_summaries[split_name] = {
                "path": index_path.name,
                "sha256": sha256_file(index_path),
                "target_count": len(records),
            }
    report.update({
        "status": "completed",
        "completed_utc": _utc_now(),
        "teacher_config": {field: value for field, value in vars(config).items()},
        "splits": split_summaries,
        "next_action": "Train a Pi-targeted student from these research-only teacher targets, then prove ONNX parity before any shadow deployment review.",
    })
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"output_dir": str(root), "manifest_path": str(manifest_path), "manifest": report}


def main() -> None:
    parser = argparse.ArgumentParser(description="Materialize immutable teacher targets for Pi student distillation.")
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--window-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--max-batches-per-split", type=int)
    args = parser.parse_args()
    try:
        result = materialize_teacher_targets(
            args.checkpoint, args.window_dir, args.output_dir, device=args.device,
            max_batches_per_split=args.max_batches_per_split,
        )
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result["manifest"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
