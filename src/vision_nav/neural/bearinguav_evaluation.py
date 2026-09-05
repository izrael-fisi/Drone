"""Audited known-context Bearing-UAV benchmark; no global recovery claims."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import re
import sys

import numpy as np

from . import bearinguav_reference as reference


SCHEMA = "vision_nav_bearinguav_evaluation_v1"
MEMBERSHIP_SCHEMA = "vision_nav_training_membership_v1"
SHA256 = re.compile(r"^[0-9a-f]{64}$")


def _text(row: dict, key: str) -> str:
    value = row.get(key) if isinstance(row, dict) else None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"Missing nonempty {key}")
    return value


def _member(root: Path, record: dict, limit: int) -> tuple[Path, bytes]:
    name = _text(record, "path")
    digest = _text(record, "sha256")
    path = (root / name).resolve()
    if Path(name).is_absolute() or not path.is_relative_to(root.resolve()) or not SHA256.fullmatch(digest):
        raise ValueError("Evaluation members require contained relative paths and SHA-256 digests")
    return path, reference.checked_bytes(path, digest, limit)


def _vector(value, shape: tuple[int, ...], name: str) -> np.ndarray:
    array = np.asarray(value, dtype=float)
    if array.shape != shape or not np.isfinite(array).all():
        raise ValueError(f"Invalid finite {name}")
    return array


def validate_labels(row: dict) -> None:
    if type(row.get("context_match")) is not bool:
        raise ValueError("Every sample must declare whether the supplied map context matches")
    truth = row.get("ground_truth")
    if not row["context_match"]:
        if truth is not None:
            raise ValueError("Wrong-context samples must not carry relative-coordinate targets")
        return
    if not isinstance(truth, dict):
        raise ValueError("Matching-context samples require explicit ground truth")
    _text(truth, "provenance")
    if truth.get("coordinate_frame") != "official_normalized_satellite_block_not_ENU":
        raise ValueError("Ground truth must use the official block convention")
    _vector(truth.get("position_xy"), (2,), "block position")
    if truth.get("direction_xy") is None:
        _text(truth, "direction_missing_reason")
    else:
        direction = _vector(truth["direction_xy"], (2,), "direction")
        if abs(np.linalg.norm(direction) - 1) > 1e-4:
            raise ValueError("Ground truth direction must be unit length")
    metric = truth.get("metric_error_basis")
    if metric is not None:
        if not isinstance(metric, dict):
            raise ValueError("Metric conversion must include its provenance")
        _text(metric, "provenance")
        if metric.get("scope") != "local_linear_error_conversion_not_global_pose":
            raise ValueError("Metric error conversion scope is missing")
        axes = _vector(metric.get("en_m_per_normalized_xy"), (2, 2), "metric axes")
        if abs(np.linalg.det(axes)) < 1e-9 or np.linalg.cond(axes) > 100:
            raise ValueError("Degenerate metric error basis")


def audit(manifest_path: Path) -> dict:
    if manifest_path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("Evaluation manifest exceeds 8 MiB")
    data = manifest_path.read_bytes()
    manifest = json.loads(data)
    if not isinstance(manifest, dict) or manifest.get("schema_version") != SCHEMA:
        raise ValueError(f"Expected {SCHEMA}")
    release = _text(manifest, "dataset_release_id")
    _text(manifest, "label_protocol")
    if manifest.get("context_policy") not in {"known_context", "oracle_context"}:
        raise ValueError("This evaluator only measures explicitly known/oracle map contexts")
    if manifest.get("input_kind") not in {"recorded", "synthetic"}:
        raise ValueError("Declare one input kind; do not mix real and synthetic metrics")
    if manifest.get("split") not in {"validation", "test"}:
        raise ValueError("Declare a single validation or test split")
    if type(manifest.get("require_scene_disjoint")) is not bool:
        raise ValueError("Declare whether scene-disjoint evaluation is required")
    records = manifest.get("records")
    if not isinstance(records, list) or not 1 <= len(records) <= 10000:
        raise ValueError("Evaluation requires 1..10000 records")
    root = manifest_path.resolve().parent
    _, membership_data = _member(root, manifest["training_membership"], 64 * 1024 * 1024)
    membership = json.loads(membership_data)
    if membership.get("schema_version") != MEMBERSHIP_SCHEMA:
        raise ValueError("Unsupported training membership manifest")
    _text(membership, "training_release_id")
    _text(membership, "provenance")
    training = membership.get("records")
    if not isinstance(training, list) or not 1 <= len(training) <= 100000:
        raise ValueError("Training membership must list the declared comparison population")
    train_ids, train_sessions, train_images, train_scenes = set(), set(), set(), set()
    for row in training:
        train_ids.add(_text(row, "sample_id"))
        train_sessions.add(_text(row, "session_id"))
        train_scenes.add(_text(row, "scene_id"))
        digest = _text(row, "uav_sha256")
        if not SHA256.fullmatch(digest):
            raise ValueError("Invalid training image digest")
        train_images.add(digest)
    ids, image_contexts, positive_images, scene_overlap, checked = set(), set(), set(), set(), []
    query_sessions = {}
    for row in records:
        session, scene = _text(row, "session_id"), _text(row, "scene_id")
        sample_path, sample_data = _member(root, row["sample"], 64 * 1024)
        sample = json.loads(sample_data)
        # Decode and check all imagery before expensive model construction.
        reference.load_patches(sample, sample_path.parent)
        if sample["dataset_release_id"] != release or sample["input_kind"] != manifest["input_kind"]:
            raise ValueError("Sample dataset release or real/synthetic classification mismatch")
        if sample["split"] != manifest["split"]:
            raise ValueError("Sample split differs from the requested validation/test split")
        sample_id, digest = sample["sample_id"], sample["patches"]["uav"]["sha256"]
        if sample.get("source_query") is not None:
            _member(sample_path.parent, sample["source_query"], 16 * 1024 * 1024)
            digest = sample["source_query"]["sha256"]
        validate_labels(row)
        context = tuple(sample["patches"][role]["sha256"] for role in reference.PATCH_ORDER[:4])
        if sample_id in ids or (digest, context) in image_contexts:
            raise ValueError("Duplicate evaluation sample or query/context pair would distort metrics")
        if row["context_match"] and digest in positive_images:
            raise ValueError("Repeated positive query image would distort localization metrics")
        if digest in query_sessions and query_sessions[digest] != session:
            raise ValueError("One query image cannot claim different capture sessions")
        if sample_id in train_ids or session in train_sessions or digest in train_images:
            raise ValueError("Train/evaluation overlap in sample ID, session or query bytes")
        ids.add(sample_id)
        image_contexts.add((digest, context))
        query_sessions[digest] = session
        if row["context_match"]:
            positive_images.add(digest)
        if scene in train_scenes:
            scene_overlap.add(scene)
            if manifest["require_scene_disjoint"]:
                raise ValueError("Train/evaluation scene overlap violates the requested policy")
        checked.append({"row": row, "sample": sample, "root": sample_path.parent})
    return {"manifest": manifest, "checked": checked,
            "manifest_sha256": hashlib.sha256(data).hexdigest(),
            "training_membership_sha256": hashlib.sha256(membership_data).hexdigest(),
            "training_release_id": membership["training_release_id"],
            "scene_overlap": sorted(scene_overlap), "distinct_query_count": len(query_sessions)}


def membership_from_training_pairs(pair_dir: Path, scene_registry: Path, release_id: str) -> dict:
    """Export actual training-query membership from the existing pair pipeline.

    Scene IDs are an explicit human-reviewed mapping, not inferred from map IDs
    (different map releases may cover the same geography).
    """
    from .training_map_pairs import MAP_PAIR_SCHEMA

    _text({"release_id": release_id}, "release_id")
    root = pair_dir.resolve()
    manifest_path = root / "manifest.json"
    if manifest_path.stat().st_size > 1024 * 1024 or scene_registry.stat().st_size > 1024 * 1024:
        raise ValueError("Training manifest or scene registry exceeds 1 MiB")
    manifest_data, scenes_data = manifest_path.read_bytes(), scene_registry.read_bytes()
    manifest, scenes = json.loads(manifest_data), json.loads(scenes_data)
    if manifest.get("schema_version") != MAP_PAIR_SCHEMA or manifest.get("policy") != "offline_training_only":
        raise ValueError("Expected offline training map-pair package")
    _, split_data = _member(root, manifest["splits"]["train"], 64 * 1024 * 1024)
    pairs = [json.loads(line) for line in split_data.splitlines() if line.strip()]
    if not 1 <= len(pairs) <= 100000:
        raise ValueError("Training membership requires 1..100000 training pairs")
    records, ids = [], set()
    for pair in pairs:
        if pair.get("schema_version") != MAP_PAIR_SCHEMA:
            raise ValueError("Invalid training pair schema")
        sample = pair["sample"]
        sample_id, session_id = _text(sample, "sample_id"), _text(sample, "capture_session_id")
        if sample_id in ids:
            raise ValueError("Duplicate training sample ID")
        ids.add(sample_id)
        scene_id = _text(scenes, session_id)
        digest = _text(sample, "frame_sha256")
        if not SHA256.fullmatch(digest):
            raise ValueError("Training frame digest missing or invalid; rebuild the training index")
        frame_path = Path(_text(sample, "frame_path")).expanduser()
        if not frame_path.is_absolute():
            raise ValueError("Existing training pair frame paths must be absolute")
        reference.checked_bytes(frame_path, digest, 16 * 1024 * 1024)
        records.append({"sample_id": sample_id, "session_id": session_id,
                        "scene_id": scene_id, "uav_sha256": digest})
    return {"schema_version": MEMBERSHIP_SCHEMA, "training_release_id": release_id,
            "provenance": "verified_training_map_pairs_with_explicit_scene_registry",
            "source_manifest_sha256": hashlib.sha256(manifest_data).hexdigest(),
            "source_train_split_sha256": hashlib.sha256(split_data).hexdigest(),
            "scene_registry_sha256": hashlib.sha256(scenes_data).hexdigest(), "records": records}


def membership_main() -> None:
    parser = argparse.ArgumentParser(description="Export verified training membership for held-out evaluation")
    parser.add_argument("--pairs", required=True, type=Path)
    parser.add_argument("--scene-registry", required=True, type=Path)
    parser.add_argument("--training-release-id", required=True)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; training membership is immutable")
    report = membership_from_training_pairs(args.pairs, args.scene_registry, args.training_release_id)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({"training_release_id": report["training_release_id"], "count": len(report["records"])}))


def score(prediction: np.ndarray, row: dict) -> dict:
    """Score only after inference. No target or map-selection hint reaches the model."""
    validate_labels(row)
    prediction = _vector(prediction, (4,), "model prediction")
    result = {"context_match": row["context_match"], "position_error_normalized": None,
              "position_error_m": None, "direction_error_deg": None,
              "direction_label_available": bool(row["context_match"] and row["ground_truth"].get("direction_xy") is not None),
              "invalid_direction": bool(np.linalg.norm(prediction[2:]) <= 1e-8)}
    if row["context_match"]:
        truth = row["ground_truth"]
        delta = prediction[:2] - np.asarray(truth["position_xy"])
        result["position_error_normalized"] = float(np.linalg.norm(delta))
        if not result["invalid_direction"] and result["direction_label_available"]:
            direction = prediction[2:] / np.linalg.norm(prediction[2:])
            result["direction_error_deg"] = float(np.degrees(np.arccos(np.clip(
                direction @ np.asarray(truth["direction_xy"]), -1, 1))))
        metric = truth.get("metric_error_basis")
        if metric is not None:
            result["position_error_m"] = float(np.linalg.norm(np.asarray(metric["en_m_per_normalized_xy"]) @ delta))
    return result


def summarize(rows: list[dict]) -> dict:
    def stats(key):
        values = [row[key] for row in rows if row[key] is not None]
        return {"count": len(values), "mean": float(np.mean(values)) if values else None,
                "median": float(np.median(values)) if values else None,
                "p95": float(np.percentile(values, 95)) if values else None}

    return {"sample_count": len(rows), "matching_context_count": sum(r["context_match"] for r in rows),
            "wrong_context_finite_output_count": sum(not r["context_match"] for r in rows),
            "invalid_direction_count": sum(r["invalid_direction"] for r in rows),
            "direction_label_count": sum(r["direction_label_available"] for r in rows),
            "position_error_normalized": stats("position_error_normalized"),
            "position_error_m": stats("position_error_m"), "direction_error_deg": stats("direction_error_deg"),
            "aggregation": "sample_weighted_not_official_RSI_macro_average",
            "global_recovery_recall": None, "false_global_fix_rate": None,
            "learned_no_match_decision_available": False}


def run(manifest_path: Path, source: Path, checkpoint: Path | None, configuration: Path,
        *, device: str = "cpu", threads: int = 2, training_run: Path | None = None) -> dict:
    import torch
    import torchvision

    if type(threads) is not int or not 1 <= threads <= 16:
        raise ValueError("threads must be between 1 and 16")
    torch.set_num_threads(threads)
    torch.manual_seed(0)
    torch.use_deterministic_algorithms(True)
    audited = audit(manifest_path)
    training_provenance = None
    checkpoint_digest = reference.CHECKPOINT_SHA256
    if training_run is not None:
        from .bearinguav_training import checkpoint_from_run
        if checkpoint is not None:
            raise ValueError("Choose official checkpoint or completed training run, not both")
        checkpoint, checkpoint_digest, training_provenance = checkpoint_from_run(
            training_run, membership_sha256=audited["training_membership_sha256"])
        model = reference.load_model(source, checkpoint, configuration, device,
                                     checkpoint_sha256=checkpoint_digest)
    else:
        if checkpoint is None:
            raise ValueError("An official checkpoint or completed training run is required")
        model = reference.load_model(source, checkpoint, configuration, device)
    rows = []
    for item in audited["checked"]:
        tensor, _ = reference.load_patches(item["sample"], item["root"])
        prediction, timings = reference.predict(model, tensor, device)
        rows.append({"sample_id": item["sample"]["sample_id"],
                     "sample_sha256": item["row"]["sample"]["sha256"],
                     "prediction": prediction.tolist(), "inference_ms": timings,
                     **score(prediction, item["row"])})
    manifest = audited["manifest"]
    return {"schema_version": "vision_nav_bearinguav_evaluation_report_v1",
            "status": "research_evaluation_only", "navigation_mode": "NO_GLOBAL_FIX",
            "position_authority": False, "px4_external_vision": "disabled", "field_ready": False,
            "dataset_release_id": manifest["dataset_release_id"], "input_kind": manifest["input_kind"],
            "split": manifest["split"], "require_scene_disjoint": manifest["require_scene_disjoint"],
            "context_policy": manifest["context_policy"], "label_protocol": manifest["label_protocol"],
            "manifest_sha256": audited["manifest_sha256"],
            "training_membership_sha256": audited["training_membership_sha256"],
            "training_release_id": audited["training_release_id"],
            "overlapping_training_scenes": audited["scene_overlap"],
            "distinct_query_count": audited["distinct_query_count"],
            "split_audit_scope": "declared_membership_only_original_checkpoint_pretraining_not_verified",
            "weights_license_status": "unverified", "source_revision": reference.SOURCE_COMMIT,
            "checkpoint_sha256": checkpoint_digest, "configuration_sha256": reference.CONFIG_SHA256,
            "checkpoint_origin": "local_research_finetune" if training_run else "official_reference",
            "training_run_sha256": hashlib.sha256((training_run / "training_run.json").read_bytes()).hexdigest() if training_run else None,
            "training_provenance": training_provenance,
            "adapter_sha256": hashlib.sha256(Path(reference.__file__).read_bytes()).hexdigest(),
            "evaluator_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "environment": {"torch": torch.__version__, "torchvision": torchvision.__version__,
                            "numpy": np.__version__, "python": sys.version.split()[0],
                            "device": device, "threads": torch.get_num_threads()},
            "metrics": summarize(rows), "samples": rows}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--manifest", required=True, type=Path)
    parser.add_argument("--source", required=True, type=Path)
    weights = parser.add_mutually_exclusive_group(required=True)
    weights.add_argument("--checkpoint", type=Path)
    weights.add_argument("--training-run", type=Path)
    parser.add_argument("--configuration", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Output already exists; evaluation evidence is immutable")
    report = run(args.manifest, args.source, args.checkpoint, args.configuration,
                 device=args.device, threads=args.threads, training_run=args.training_run)
    with args.output.open("x") as stream:
        json.dump(report, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps(report["metrics"], indent=2))


if __name__ == "__main__":
    main()
