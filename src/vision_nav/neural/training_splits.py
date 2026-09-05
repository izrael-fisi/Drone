"""Create deterministic, capture-session-disjoint learned-navigation splits."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any

from vision_nav.training_capture import sha256_file


TRAINING_SPLIT_SCHEMA = "vision_nav_training_split_v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    records = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not records:
        raise ValueError(f"Training index contains no samples: {path}")
    return records


def _group_by_capture_session(samples: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {}
    for sample in samples:
        session_id = sample.get("capture_session_id")
        if not isinstance(session_id, str) or not session_id:
            raise ValueError(f"Training sample lacks capture_session_id: {sample.get('sample_id')!r}")
        groups.setdefault(session_id, []).append(sample)
    return groups


def _ranked_session_ids(session_ids: list[str], seed: int) -> list[str]:
    return sorted(
        session_ids,
        key=lambda session_id: hashlib.sha256(f"{seed}:{session_id}".encode("utf-8")).hexdigest(),
    )


def create_training_splits(
    index_dir: str | Path,
    output_dir: str | Path,
    *,
    validation_fraction: float = 0.2,
    seed: int = 20260830,
) -> dict[str, Any]:
    """Materialize deterministic session-disjoint train and validation JSONL files."""

    if not 0.0 < validation_fraction < 1.0:
        raise ValueError("validation_fraction must be strictly between 0 and 1.")
    source_root = Path(index_dir).expanduser().resolve()
    source_manifest_path = source_root / "manifest.json"
    source_index_path = source_root / "samples.jsonl"
    if not source_manifest_path.is_file() or not source_index_path.is_file():
        raise ValueError("Training index must contain manifest.json and samples.jsonl.")
    source_manifest = json.loads(source_manifest_path.read_text(encoding="utf-8"))
    if source_manifest.get("schema_version") != "vision_nav_training_index_v1":
        raise ValueError("Unsupported training index schema.")
    if source_manifest.get("index_sha256") != sha256_file(source_index_path):
        raise ValueError("Training index checksum mismatch. Rebuild the index before splitting.")
    samples = _read_jsonl(source_index_path)
    groups = _group_by_capture_session(samples)
    session_ids = sorted(groups)
    if len(session_ids) < 2:
        raise ValueError("At least two capture sessions are required for a session-disjoint train/validation split.")
    validation_session_count = max(1, min(len(session_ids) - 1, round(len(session_ids) * validation_fraction)))
    ranked = _ranked_session_ids(session_ids, seed)
    validation_sessions = set(ranked[:validation_session_count])
    train_sessions = set(session_ids) - validation_sessions
    if not train_sessions or not validation_sessions:
        raise ValueError("Split must contain at least one train and one validation capture session.")
    train_samples = [sample for sample in samples if sample["capture_session_id"] in train_sessions]
    validation_samples = [sample for sample in samples if sample["capture_session_id"] in validation_sessions]

    root = Path(output_dir).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError(f"Training split output directory must be empty: {root}")
    root.mkdir(parents=True, exist_ok=True)
    train_path = root / "train.jsonl"
    validation_path = root / "validation.jsonl"
    for path, split_samples in ((train_path, train_samples), (validation_path, validation_samples)):
        with path.open("w", encoding="utf-8") as stream:
            for sample in split_samples:
                stream.write(json.dumps(sample, sort_keys=True) + "\n")
    manifest = {
        "schema_version": TRAINING_SPLIT_SCHEMA,
        "created_utc": _utc_now(),
        "source_training_index": {
            "path": str(source_root),
            "dataset_release_id": source_manifest.get("dataset_release_id"),
            "samples_sha256": sha256_file(source_index_path),
        },
        "split_policy": {
            "group_by": "capture_session_id",
            "validation_fraction": validation_fraction,
            "seed": seed,
            "session_disjoint": True,
        },
        "train": {
            "path": train_path.name,
            "sample_count": len(train_samples),
            "capture_session_ids": sorted(train_sessions),
            "sha256": sha256_file(train_path),
        },
        "validation": {
            "path": validation_path.name,
            "sample_count": len(validation_samples),
            "capture_session_ids": sorted(validation_sessions),
            "sha256": sha256_file(validation_path),
        },
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"output_dir": str(root), "manifest_path": str(manifest_path), "manifest": manifest}


def main() -> None:
    parser = argparse.ArgumentParser(description="Create deterministic capture-session-disjoint learned-navigation training splits.")
    parser.add_argument("--index-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--validation-fraction", type=float, default=0.2)
    parser.add_argument("--seed", type=int, default=20260830)
    args = parser.parse_args()
    try:
        result = create_training_splits(
            args.index_dir,
            args.output_dir,
            validation_fraction=args.validation_fraction,
            seed=args.seed,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result["manifest"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
