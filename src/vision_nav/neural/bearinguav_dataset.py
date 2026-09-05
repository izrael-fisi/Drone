"""Adapt our portable cross-view captures to Bearing-UAV oracle-context research.

The published metadata uses row-major map patches and pixel-down position labels
with one unit equal to a quarter block. The model's hardcoded coordinate
embeddings are not a reliable description of the actual dataset patch order.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import shutil
import tempfile

import cv2
import numpy as np

from vision_nav.camera import load_camera_calibration
from . import bearinguav_evaluation as evaluation
from .bearinguav_reference import DATASET_CONVENTION, PATCH_ORDER, REQUEST_SCHEMA, checked_bytes
from .crossview_dataset import load_crossview_dataset, _member
from .crossview_images import image_tensor, PREPROCESSING_ID
from .training_dataset import LEGACY_TIMING


CONVENTION = DATASET_CONVENTION


def split_block(image: np.ndarray) -> list[np.ndarray]:
    height, width = image.shape[:2]
    if min(height, width) < 64 or height % 2 or width % 2:
        raise ValueError("Map block must have even dimensions of at least 64 pixels")
    return [image[y:y + height // 2, x:x + width // 2]
            for y, x in ((0, 0), (0, width // 2), (height // 2, 0), (height // 2, width // 2))]


def normalized_position(uv) -> list[float]:
    value = np.asarray(uv, dtype=float)
    if value.shape != (2,) or not np.isfinite(value).all() or np.any(value < 0) or np.any(value >= 1):
        raise ValueError("Positive target must lie inside the source map block")
    return (4.0 * (value - 0.5)).tolist()


def build(dataset: Path, scenes_path: Path, output: Path, *, release_id: str,
          input_kind: str, image_size: int = 256, allow_legacy_timing: bool = False) -> dict:
    if input_kind not in {"recorded", "synthetic"} or not release_id.strip():
        raise ValueError("Explicit release ID and recorded/synthetic classification are required")
    if type(image_size) is not int or not 32 <= image_size <= 512:
        raise ValueError("Model patch size must be between 32 and 512")
    source, output = dataset.resolve(), output.resolve()
    if output.exists() or output.is_relative_to(source):
        raise ValueError("Output must be new and outside the immutable input dataset")
    manifest, rows = load_crossview_dataset(source)
    if LEGACY_TIMING in manifest["timing_policies"] and not allow_legacy_timing:
        raise ValueError("Legacy timing requires explicit research-only opt-in")
    scenes_bytes = scenes_path.read_bytes()
    scenes = json.loads(scenes_bytes)
    source_digest = hashlib.sha256((source / "manifest.json").read_bytes()).hexdigest()
    for split in rows:
        for row in rows[split]:
            evaluation._text(scenes, row["session_id"])
            if sum(candidate["match"] for candidate in row["candidates"]) != 1:
                raise ValueError("Each window must have exactly one positive map block")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".bearing-build-", dir=output.parent))
    assets, json_members, records, membership = {}, {}, {"train": [], "validation": []}, []

    def asset(data: bytes, suffix: str) -> dict:
        digest = hashlib.sha256(data).hexdigest()
        name = f"assets/{digest}{suffix}"
        (staging / "assets").mkdir(exist_ok=True)
        if name not in assets:
            (staging / name).write_bytes(data)
            assets[name] = digest
        return {"path": name, "sha256": digest}

    def encoded(image):
        ok, data = cv2.imencode(".png", image)
        if not ok:
            raise ValueError("Could not encode generated patch")
        return asset(data.tobytes(), ".png")

    def source_asset(name):
        path = _member(source, name)
        return asset(checked_bytes(path, manifest["assets_sha256"][name], 64 * 1024 * 1024), path.suffix.lower())

    def write_json(name, value):
        data = (json.dumps(value, sort_keys=True, indent=2, allow_nan=False) + "\n").encode()
        (staging / name).write_bytes(data)
        json_members[name] = hashlib.sha256(data).hexdigest()
        return {"path": name, "sha256": json_members[name]}

    try:
        for split in rows:
            for index, row in enumerate(rows[split]):
                raw = source_asset(row["frames"][-1])
                raw_path = staging / raw["path"]
                saved_calibration = source_asset(row["calibration"])
                calibration_path = staging / saved_calibration["path"]
                calibration = load_camera_calibration(calibration_path)
                if calibration.calibration_status != "measured" or calibration.calibration_id != row["calibration_id"]:
                    raise ValueError("Measured matching camera calibration is required")
                query_rgb = image_tensor(raw_path, image_size, calibration=calibration)
                query_bgr = cv2.cvtColor(np.rint(query_rgb.transpose(1, 2, 0) * 255).astype(np.uint8), cv2.COLOR_RGB2BGR)
                query = encoded(query_bgr)
                window_id = evaluation._text(row, "window_id")
                if split == "train":
                    membership.append({"sample_id": window_id, "session_id": row["session_id"],
                        "scene_id": scenes[row["session_id"]], "uav_sha256": raw["sha256"]})
                for candidate_index, candidate in enumerate(row["candidates"]):
                    saved_map = source_asset(candidate["image"])
                    map_path = staging / saved_map["path"]
                    block = cv2.imread(str(map_path))
                    if block is None or list(block.shape[1::-1]) != candidate["pixel_geometry"]["image_size"]:
                        raise ValueError("Map block dimensions differ from pinned pixel geometry")
                    patches = {role: encoded(cv2.resize(patch, (image_size, image_size), interpolation=cv2.INTER_LINEAR))
                               for role, patch in zip(PATCH_ORDER[:4], split_block(block))}
                    patches["uav"] = query
                    case_id = f"{window_id}:context-{candidate_index}"
                    sample = {"schema_version": REQUEST_SCHEMA, "sample_id": case_id,
                        "dataset_release_id": release_id, "split": split, "input_kind": input_kind,
                        "patches": patches, "source_query": raw,
                        "conversion": {"convention": CONVENTION, "query_preprocessing": PREPROCESSING_ID,
                            "calibration_sha256": manifest["assets_sha256"][row["calibration"]],
                            "calibration": saved_calibration, "source_map": saved_map,
                            "map_source_sha256": manifest["assets_sha256"][candidate["image"]],
                            "map_pixel_geometry": candidate["pixel_geometry"],
                            "source_manifest_sha256": source_digest, "source_window_id": window_id,
                            "position_reference": row["position_reference"],
                            "query_resize_is_not_viewpoint_rectification": True}}
                    item = write_json(f"{split}-{index:06d}-{candidate_index:03d}.json", sample)
                    truth = None
                    if candidate["match"]:
                        truth = {"coordinate_frame": "official_normalized_satellite_block_not_ENU",
                            "position_xy": normalized_position(candidate["target_uv"]), "direction_xy": None,
                            "direction_missing_reason": "PX4 body yaw is not a qualified camera/map direction label",
                            "provenance": f"crossview:{source_digest}:window:{window_id}:reference:{row['position_reference']}"}
                    records[split].append({"sample": item, "session_id": row["session_id"],
                        "scene_id": scenes[row["session_id"]], "context_match": bool(candidate["match"]),
                        "ground_truth": truth})
        train_membership = write_json("training-membership.json", {
            "schema_version": evaluation.MEMBERSHIP_SCHEMA, "training_release_id": f"{release_id}:train",
            "provenance": f"crossview_anchor_queries:{source_digest}",
            "scene_registry_sha256": hashlib.sha256(scenes_bytes).hexdigest(), "records": membership})
        write_json("train-cases.json", {"schema_version": "vision_nav_bearinguav_training_cases_v1",
                                       "records": records["train"], "policy": "offline_training_only"})
        evaluation_manifest = {"schema_version": evaluation.SCHEMA, "dataset_release_id": release_id,
            "input_kind": input_kind, "split": "validation", "context_policy": "oracle_context",
            "require_scene_disjoint": False, "label_protocol": CONVENTION,
            "training_membership": train_membership, "records": records["validation"]}
        write_json("evaluation.json", evaluation_manifest)
        evaluation.audit(staging / "evaluation.json")
        report = {"schema_version": "vision_nav_bearinguav_dataset_build_v1", "release_id": release_id,
            "producer_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "source_manifest_sha256": source_digest, "scene_registry_sha256": hashlib.sha256(scenes_bytes).hexdigest(),
            "convention": CONVENTION, "context_policy": "oracle_context", "input_kind": input_kind,
            "train_case_count": len(records["train"]), "validation_case_count": len(records["validation"]),
            "image_size": image_size, "assets_sha256": assets, "heading_labels_qualified": False,
            "checksums_sha256": {**assets, **json_members},
            "metric_pose": None, "position_authority": False, "px4_external_vision": "disabled",
            "timing_policies": manifest["timing_policies"], "allow_legacy_timing": allow_legacy_timing,
            "official_dataset_reproduction": False, "weights_and_dataset_rights_verified": False}
        write_json("manifest.json", report)
        staging.rename(output)
        return report
    except BaseException:
        shutil.rmtree(staging)
        raise


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--dataset", required=True, type=Path)
    parser.add_argument("--scenes", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--release-id", required=True)
    parser.add_argument("--input-kind", choices=("recorded", "synthetic"), required=True)
    parser.add_argument("--image-size", type=int, default=256)
    parser.add_argument("--allow-legacy-timing", action="store_true")
    args = parser.parse_args()
    report = build(args.dataset, args.scenes, args.output, release_id=args.release_id,
                   input_kind=args.input_kind, image_size=args.image_size, allow_legacy_timing=args.allow_legacy_timing)
    print(json.dumps({key: report[key] for key in ("release_id", "train_case_count", "validation_case_count")}))


if __name__ == "__main__":
    main()
