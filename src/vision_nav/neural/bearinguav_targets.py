"""Training-only Bearing-UAV point targets for the map-conditioned student.

Teacher regression is not ground truth, a probability distribution, heading
supervision, or a measurement covariance. Negative contexts retain supervised
no-match labels and never receive an invented teacher confidence.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

import numpy as np

from vision_nav.training_capture import sha256_file
from . import bearinguav_reference as reference
from . import bearinguav_training as training
from .crossview_dataset import load_crossview_dataset


SCHEMA = "vision_nav_bearinguav_point_targets_v1"


def candidate_identity(manifest: dict, row: dict, index: int) -> dict:
    candidate = row["candidates"][index]
    return {"window_id": row["window_id"], "session_id": row["session_id"],
            "candidate_index": index, "tile_id": candidate["tile_id"],
            "map_sha256": manifest["assets_sha256"][candidate["image"]],
            "query_sha256": manifest["assets_sha256"][row["frames"][-1]],
            "calibration_sha256": manifest["assets_sha256"][row["calibration"]],
            "pixel_geometry": candidate["pixel_geometry"],
            "positive_context": bool(candidate["match"])}


def point_target(prediction, *, positive: bool) -> dict:
    if not positive:
        return {"status": "excluded", "reason": "negative_context_not_distilled",
                "relative_position_xy": None, "target_uv": None}
    raw = np.asarray(prediction)
    if raw.shape != (2,) or raw.dtype.kind not in "fiu":
        raise ValueError("Teacher position must have two finite coordinates")
    xy = raw.astype(np.float64)
    if not np.isfinite(xy).all():
        raise ValueError("Teacher position must have two finite coordinates")
    uv = xy / 4.0 + 0.5
    inside = bool(np.all(uv >= 0) and np.all(uv < 1))
    return {"status": "usable" if inside else "withheld",
            "reason": "uncalibrated_teacher_point" if inside else "teacher_point_outside_map",
            "relative_position_xy": xy.tolist(), "target_uv": uv.tolist() if inside else None}


def _training_rows(rows: dict) -> dict:
    result = {row["window_id"]: row for row in rows["train"]}
    if len(result) != len(rows["train"]):
        raise ValueError("Duplicate training window IDs")
    return result


def build(dataset: Path, converted: Path, source: Path, configuration: Path, output: Path,
          *, checkpoint: Path | None = None, training_run: Path | None = None,
          device: str = "cpu", threads: int = 2) -> dict:
    import torch
    import torchvision

    if (checkpoint is None) == (training_run is None):
        raise ValueError("Choose exactly one official checkpoint or completed training run")
    if type(threads) is not int or not 1 <= threads <= 16:
        raise ValueError("threads must be an integer from 1 to 16")
    dataset, converted, output = dataset.resolve(), converted.resolve(), output.resolve()
    if output.exists() or any(output.is_relative_to(root) for root in (dataset, converted)):
        raise ValueError("Target output must be new and outside both immutable datasets")
    if training_run is not None and output.is_relative_to(training_run.resolve()):
        raise ValueError("Target output must be outside the immutable teacher training run")
    manifest, rows = load_crossview_dataset(dataset)
    audited = training.audit_dataset(converted)
    source_digest = sha256_file(dataset / "manifest.json")
    if audited["manifest"]["source_manifest_sha256"] != source_digest:
        raise ValueError("Converted teacher dataset comes from a different student dataset")
    windows = _training_rows(rows)
    bound = {}
    # Bind every converted case to the original bytes and candidate ordering.
    for item in audited["train"]:
        sample, row = item["sample"], item["row"]
        conversion = sample["conversion"]
        window = windows.get(conversion["source_window_id"])
        if window is None:
            raise ValueError("Teacher case is not a training window")
        matches = [index for index in range(len(window["candidates"]))
                   if sample["sample_id"] == f"{window['window_id']}:context-{index}"]
        if len(matches) != 1:
            raise ValueError("Teacher sample cannot be bound to one source candidate")
        index = matches[0]
        identity = candidate_identity(manifest, window, index)
        if (row["session_id"] != identity["session_id"]
                or row["context_match"] != identity["positive_context"]
                or sample["source_query"]["sha256"] != identity["query_sha256"]
                or conversion["map_source_sha256"] != identity["map_sha256"]
                or conversion["calibration_sha256"] != identity["calibration_sha256"]
                or conversion["map_pixel_geometry"] != identity["pixel_geometry"]
                or conversion["source_manifest_sha256"] != source_digest):
            raise ValueError("Teacher case does not match the source query/map/calibration")
        key = (window["window_id"], index)
        if key in bound:
            raise ValueError("Duplicate teacher case")
        bound[key] = (item, identity)
    expected = {(row["window_id"], i) for row in windows.values() for i in range(len(row["candidates"]))}
    if set(bound) != expected:
        raise ValueError("Teacher conversion must cover exactly all training candidates")
    digest = reference.CHECKPOINT_SHA256
    trained_provenance = None
    if training_run is not None:
        checkpoint, digest, trained = training.checkpoint_from_run(
            training_run, membership_sha256=audited["membership_sha256"])
        if trained.get("dataset_manifest_sha256") != audited["manifest_sha256"]:
            raise ValueError("Fine-tuned teacher is bound to a different converted dataset")
        trained_provenance = {"training_run_sha256": sha256_file(training_run / "training_run.json"),
                              "dataset_manifest_sha256": audited["manifest_sha256"]}
    torch.set_num_threads(threads)
    torch.manual_seed(0)
    torch.use_deterministic_algorithms(True)
    model = reference.load_model(source, checkpoint, configuration, device, checkpoint_sha256=digest)
    records = []
    for key in sorted(bound):
        item, identity = bound[key]
        prediction, timings = None, []
        if identity["positive_context"]:
            tensor, _ = reference.load_patches(item["sample"], converted)
            full_prediction, timings = reference.predict(model, tensor, device)
            prediction = full_prediction[:2]
        records.append({**identity, **point_target(prediction, positive=identity["positive_context"]),
                        "teacher_sample_sha256": item["row"]["sample"]["sha256"],
                        "inference_ms": timings})
    result = {"schema_version": SCHEMA, "status": "completed", "split": "train",
              "source_manifest_sha256": source_digest,
              "converted_manifest_sha256": audited["manifest_sha256"],
              "input_kind": audited["manifest"]["input_kind"],
              "coordinate_convention": reference.DATASET_CONVENTION,
              "teacher": {"source_revision": reference.SOURCE_COMMIT, "checkpoint_sha256": digest,
                          "configuration_sha256": reference.CONFIG_SHA256,
                          "adapter_sha256": sha256_file(Path(reference.__file__)),
                          "producer_sha256": sha256_file(Path(__file__)),
                          "origin": "local_finetune" if trained_provenance else "official_reference",
                          "training_provenance": trained_provenance,
                          "torch": str(torch.__version__), "torchvision": str(torchvision.__version__),
                          "numpy": np.__version__, "device": device, "threads": threads},
              "records": records, "usable_count": sum(r["status"] == "usable" for r in records),
              "position_authority": False, "px4_external_vision": "disabled",
              "heading_distillation": False, "teacher_confidence_calibrated": False,
              "rights_verified": False, "context_policy": "oracle_positive_training_only"}
    output.parent.mkdir(parents=True, exist_ok=True)
    with output.open("x") as stream:
        json.dump(result, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
    return result


def load_targets(path: Path, dataset: Path) -> tuple[dict, dict]:
    with path.open("rb") as stream:
        payload = stream.read(64 * 1024 * 1024 + 1)
    if len(payload) > 64 * 1024 * 1024:
        raise ValueError("Teacher target file exceeds 64 MiB")
    document = json.loads(payload)
    manifest, rows = load_crossview_dataset(dataset)
    if (document.get("schema_version") != SCHEMA or document.get("status") != "completed"
            or document.get("split") != "train"
            or document.get("source_manifest_sha256") != sha256_file(dataset / "manifest.json")
            or document.get("coordinate_convention") != reference.DATASET_CONVENTION
            or document.get("context_policy") != "oracle_positive_training_only"
            or document.get("position_authority") is not False
            or document.get("px4_external_vision") != "disabled"
            or document.get("heading_distillation") is not False
            or document.get("teacher_confidence_calibrated") is not False):
        raise ValueError("Targets must be shadow training points bound to this dataset")
    teacher = document.get("teacher", {})
    if teacher.get("source_revision") != reference.SOURCE_COMMIT or teacher.get("configuration_sha256") != reference.CONFIG_SHA256:
        raise ValueError("Wrong teacher source/configuration")
    for key in ("checkpoint_sha256", "adapter_sha256", "producer_sha256"):
        value = teacher.get(key)
        if not isinstance(value, str) or len(value) != 64 or any(c not in "0123456789abcdef" for c in value):
            raise ValueError("Missing teacher digest provenance")
    if teacher.get("origin") == "official_reference":
        if teacher["checkpoint_sha256"] != reference.CHECKPOINT_SHA256 or teacher.get("training_provenance") is not None:
            raise ValueError("Official teacher checkpoint provenance mismatch")
    elif teacher.get("origin") == "local_finetune":
        lineage = teacher.get("training_provenance")
        if (not isinstance(lineage, dict)
                or lineage.get("dataset_manifest_sha256") != document.get("converted_manifest_sha256")
                or not isinstance(lineage.get("training_run_sha256"), str)
                or len(lineage["training_run_sha256"]) != 64):
            raise ValueError("Missing fine-tuned teacher lineage")
    else:
        raise ValueError("Unknown teacher origin")
    windows = _training_rows(rows)
    expected = {(row["window_id"], i): candidate_identity(manifest, row, i)
                for row in windows.values() for i in range(len(row["candidates"]))}
    records = document.get("records")
    if not isinstance(records, list) or len(records) != len(expected):
        raise ValueError("Teacher target coverage differs from training candidates")
    targets, seen, usable = {}, set(), 0
    for record in records:
        if type(record.get("candidate_index")) is not int:
            raise ValueError("Invalid teacher candidate index")
        key = (record.get("window_id"), record["candidate_index"])
        if key not in expected or key in seen or any(record.get(k) != v for k, v in expected[key].items()):
            raise ValueError("Teacher target candidate identity mismatch or duplicate")
        seen.add(key)
        point = point_target(record.get("relative_position_xy"), positive=expected[key]["positive_context"])
        if any(record.get(k) != v for k, v in point.items()):
            raise ValueError("Teacher target coordinate mapping/status mismatch")
        if point["status"] == "usable":
            targets.setdefault(key[0], {})[key[1]] = point["target_uv"]
            usable += 1
    if usable != document.get("usable_count") or usable == 0:
        raise ValueError("Teacher targets require a correct nonzero usable count")
    provenance = {"schema_version": SCHEMA, "targets_sha256": hashlib.sha256(payload).hexdigest(),
                  "teacher": teacher, "usable_count": usable,
                  "source_manifest_sha256": document["source_manifest_sha256"],
                  "converted_manifest_sha256": document["converted_manifest_sha256"],
                  "scope": "positive_context_point_regression_train_only"}
    return provenance, targets


def point_loss(location_logits, points: dict[int, list[float]], grid_size: int):
    """Point matching on spatial expectation; no fabricated teacher distribution."""
    import torch
    if type(grid_size) is not int or grid_size <= 0:
        raise ValueError("Positive integer student grid size required")
    indices = sorted(points)
    if not indices:
        return location_logits.sum() * 0
    cells = grid_size ** 2
    if location_logits.ndim != 2 or location_logits.shape[1] != cells + 1:
        raise ValueError("Student location logits do not match its grid")
    if any(type(i) is not int or not 0 <= i < location_logits.shape[0] for i in indices):
        raise ValueError("Teacher candidate index outside student batch")
    targets = location_logits.new_tensor([points[i] for i in indices])
    if targets.shape != (len(indices), 2) or not torch.isfinite(targets).all() or (targets < 0).any() or (targets >= 1).any():
        raise ValueError("Teacher points must be inside the candidate map")
    axis = (torch.arange(grid_size, device=location_logits.device, dtype=location_logits.dtype) + .5) / grid_size
    y, x = torch.meshgrid(axis, axis, indexing="ij")
    coordinates = torch.stack((x, y), dim=-1).reshape(cells, 2)
    expected_uv = torch.softmax(location_logits[indices, :cells], dim=-1) @ coordinates
    return torch.nn.functional.smooth_l1_loss(expected_uv, targets, beta=.1)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "converted", "source", "configuration", "output"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    model = parser.add_mutually_exclusive_group(required=True)
    model.add_argument("--checkpoint", type=Path)
    model.add_argument("--training-run", type=Path)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--threads", type=int, default=2)
    args = parser.parse_args()
    result = build(args.dataset, args.converted, args.source, args.configuration, args.output,
                   checkpoint=args.checkpoint, training_run=args.training_run, device=args.device, threads=args.threads)
    print(json.dumps({"status": result["status"], "usable_count": result["usable_count"]}))


if __name__ == "__main__":
    main()
