"""Prepare immutable teacher-training inputs from split samples and offline labels."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

from vision_nav.training_capture import sha256_file

from .training_labels import TRAINING_LABEL_SCHEMA
from .training_splits import TRAINING_SPLIT_SCHEMA


EXPERIMENT_INPUT_SCHEMA = "vision_nav_teacher_experiment_input_v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


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
            value = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{label}:{line_number} is not valid JSON: {exc}") from exc
        if not isinstance(value, dict):
            raise ValueError(f"{label}:{line_number} must be a JSON object.")
        records.append(value)
    return records


def _assert_file_digest(path: Path, expected: Any, label: str) -> None:
    if not isinstance(expected, str) or not expected:
        raise ValueError(f"{label} is missing a SHA-256 digest.")
    actual = sha256_file(path)
    if actual != expected:
        raise ValueError(f"{label} checksum mismatch: expected={expected}, actual={actual}.")


def _write_jsonl(path: Path, records: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as stream:
        for record in records:
            stream.write(json.dumps(record, sort_keys=True) + "\n")


def prepare_teacher_experiment(
    split_dir: str | Path,
    labels_dir: str | Path,
    output_dir: str | Path,
    *,
    require_heading: bool = False,
) -> dict[str, Any]:
    """Join accepted offline labels to a session-disjoint train/validation split.

    This does not train or export a model. It gives a CUDA trainer immutable,
    verified JSONL inputs and an artifact manifest that names the exact source
    captures, split policy, GNSS-label policy, and rejected samples.
    """

    split_root = Path(split_dir).expanduser().resolve()
    labels_root = Path(labels_dir).expanduser().resolve()
    split_manifest_path = split_root / "manifest.json"
    labels_manifest_path = labels_root / "manifest.json"
    split_manifest = _read_json(split_manifest_path, "training split manifest")
    labels_manifest = _read_json(labels_manifest_path, "training label manifest")
    if split_manifest.get("schema_version") != TRAINING_SPLIT_SCHEMA:
        raise ValueError("Unsupported training split schema.")
    if labels_manifest.get("schema_version") != TRAINING_LABEL_SCHEMA:
        raise ValueError("Unsupported training label schema.")
    if labels_manifest.get("policy") != "offline_training_only":
        raise ValueError("Teacher experiment labels must be marked offline_training_only.")
    source_index = split_manifest.get("source_training_index") if isinstance(split_manifest.get("source_training_index"), dict) else {}
    label_source_index = labels_manifest.get("source_training_index") if isinstance(labels_manifest.get("source_training_index"), dict) else {}
    split_index_digest = source_index.get("samples_sha256")
    if split_index_digest != label_source_index.get("samples_sha256"):
        raise ValueError("Training split and labels were derived from different training-index checksums.")

    train_path = split_root / str((split_manifest.get("train") or {}).get("path") or "train.jsonl")
    validation_path = split_root / str((split_manifest.get("validation") or {}).get("path") or "validation.jsonl")
    labels_path = labels_root / str(labels_manifest.get("label_path") or "labels.jsonl")
    _assert_file_digest(train_path, (split_manifest.get("train") or {}).get("sha256"), "Training split train.jsonl")
    _assert_file_digest(validation_path, (split_manifest.get("validation") or {}).get("sha256"), "Training split validation.jsonl")
    _assert_file_digest(labels_path, labels_manifest.get("label_sha256"), "Training labels")
    train_samples = _read_jsonl(train_path, "train split")
    validation_samples = _read_jsonl(validation_path, "validation split")
    labels = _read_jsonl(labels_path, "training labels")

    labels_by_id: dict[str, dict[str, Any]] = {}
    for label in labels:
        sample_id = label.get("sample_id")
        if not isinstance(sample_id, str) or not sample_id:
            raise ValueError("Training labels contain a missing sample_id.")
        if sample_id in labels_by_id:
            raise ValueError(f"Training labels contain duplicate sample_id: {sample_id}")
        if label.get("policy") != "offline_training_only":
            raise ValueError(f"Training label {sample_id} is not offline_training_only.")
        labels_by_id[sample_id] = label

    def join(split_name: str, samples: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, int]]:
        joined: list[dict[str, Any]] = []
        counts = {"source": len(samples), "accepted": 0, "rejected": 0, "missing": 0, "missing_heading": 0}
        for sample in samples:
            sample_id = sample.get("sample_id")
            label = labels_by_id.get(sample_id) if isinstance(sample_id, str) else None
            if label is None:
                counts["missing"] += 1
                continue
            if label.get("status") != "accepted" or not isinstance(label.get("target"), dict):
                counts["rejected"] += 1
                continue
            if require_heading and label["target"].get("heading_rad") is None:
                counts["missing_heading"] += 1
                continue
            joined.append({"sample": sample, "supervision": label["target"], "label": label})
            counts["accepted"] += 1
        return joined, counts

    train_joined, train_counts = join("train", train_samples)
    validation_joined, validation_counts = join("validation", validation_samples)
    if not train_joined or not validation_joined:
        raise ValueError("Teacher experiment requires at least one accepted labeled sample in both train and validation.")
    train_sessions = {entry["sample"].get("capture_session_id") for entry in train_joined}
    validation_sessions = {entry["sample"].get("capture_session_id") for entry in validation_joined}
    overlap = sorted(str(item) for item in train_sessions.intersection(validation_sessions) if item)
    if overlap:
        raise ValueError(f"Teacher experiment violates capture-session split isolation: {', '.join(overlap)}")

    root = Path(output_dir).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError(f"Teacher experiment output directory must be empty: {root}")
    root.mkdir(parents=True, exist_ok=True)
    train_output = root / "train.jsonl"
    validation_output = root / "validation.jsonl"
    _write_jsonl(train_output, train_joined)
    _write_jsonl(validation_output, validation_joined)
    manifest = {
        "schema_version": EXPERIMENT_INPUT_SCHEMA,
        "created_utc": _utc_now(),
        "policy": "offline_training_only",
        "source_training_index_sha256": split_index_digest,
        "dataset_release_id": source_index.get("dataset_release_id"),
        "split": {"path": str(split_root), "manifest_sha256": sha256_file(split_manifest_path)},
        "labels": {"path": str(labels_root), "manifest_sha256": sha256_file(labels_manifest_path)},
        "require_heading": require_heading,
        "train": {"path": train_output.name, "sha256": sha256_file(train_output), "counts": train_counts, "capture_session_ids": sorted(str(item) for item in train_sessions if item)},
        "validation": {"path": validation_output.name, "sha256": sha256_file(validation_output), "counts": validation_counts, "capture_session_ids": sorted(str(item) for item in validation_sessions if item)},
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"output_dir": str(root), "manifest_path": str(manifest_path), "manifest": manifest}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Prepare verified, session-disjoint learned teacher inputs from training splits and offline labels."
    )
    parser.add_argument("--split-dir", required=True, type=Path)
    parser.add_argument("--labels-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--require-heading", action="store_true")
    args = parser.parse_args()
    try:
        result = prepare_teacher_experiment(
            args.split_dir, args.labels_dir, args.output_dir, require_heading=args.require_heading
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result["manifest"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
