"""Portable camera/map-conditioned training inputs derived from temporal windows."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
from typing import Any

import cv2
import numpy as np

from vision_nav.camera import load_camera_calibration, validate_image_size
from vision_nav.training_capture import sha256_file
from .map_pixel_geometry import reference_enu_to_tile_uv, tile_metric_axes
from .temporal_training_windows import validate_temporal_training_windows, _read_json, _read_jsonl
from .training_timing import checked_exposure_frame
from .training_dataset import EXPOSURE_TIMING, LEGACY_TIMING
from .crossview_images import image_tensor


CROSSVIEW_DATASET_SCHEMA = "vision_nav_map_conditioned_dataset_v1"


def _member(root: Path, name: str) -> Path:
    path = (root / name).resolve()
    if not path.is_relative_to(root.resolve()) or not path.is_file():
        raise ValueError(f"Missing or escaped cross-view dataset member: {name}")
    return path


def _camera_context(calibration: Any, frames: list[dict[str, Any]]) -> list[float]:
    if calibration.calibration_status != "measured" or calibration.distortion_model not in {"plumb_bob", "rational_polynomial"}:
        raise ValueError("Map-conditioned training requires measured pinhole calibration.")
    matrix = calibration.camera_matrix
    width, height = calibration.image_size
    if width <= 0 or height <= 0 or not np.isfinite(matrix).all() or min(matrix[0, 0], matrix[1, 1]) <= 0:
        raise ValueError("Invalid measured camera intrinsics.")
    if not np.isfinite(calibration.distortion_coefficients).all():
        raise ValueError("Nonfinite camera distortion.")
    extrinsics = np.asarray([f["camera_rotation_camera_optical_to_body_frd_xyzw"] + f["camera_translation_body_frd_m"] for f in frames], dtype=float)
    if not np.isfinite(extrinsics).all() or not np.allclose(extrinsics, extrinsics[-1], atol=1e-6):
        raise ValueError("Moving/unmeasured camera mounts require a separate synchronized extrinsic interface.")
    if not np.isclose(np.linalg.norm(extrinsics[-1, :4]), 1, atol=1e-3):
        raise ValueError("Camera-to-body quaternion is not unit length.")
    return [float(matrix[0, 0] / width), float(matrix[1, 1] / height),
            float((matrix[0, 2] + 0.5) / width), float((matrix[1, 2] + 0.5) / height), *extrinsics[-1].tolist()]


def build_crossview_dataset(window_dirs: list[str | Path], calibration_registry: str | Path, output_dir: str | Path,
                            *, allow_legacy_timing: bool = False) -> dict[str, Any]:
    """Materialize frame/map bytes, exact crop labels and independent no-match pairs.

    Labels retain the existing offline reference-point semantics. No GNSS data,
    absolute ENU, tile ID or absolute yaw is included in model inputs.
    """
    registry_path = Path(calibration_registry).expanduser().resolve()
    registry = _read_json(registry_path, "calibration registry")
    root = Path(output_dir).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError("Cross-view dataset output must be empty.")
    if not window_dirs:
        raise ValueError("At least one temporal-window package is required.")
    rows = {"train": [], "validation": []}
    assets: dict[str, str] = {}
    copied: dict[Path, str] = {}
    sources = []
    timing_cache: dict[tuple[str, str], dict[str, Any]] = {}
    root.mkdir(parents=True, exist_ok=True)
    (root / "assets").mkdir()

    def copy_asset(value: str | Path) -> str:
        source = Path(value).expanduser().resolve()
        if source not in copied:
            if not source.is_file():
                raise ValueError(f"Missing training asset: {source}")
            digest = sha256_file(source)
            name = f"assets/{digest}{source.suffix.lower()}"
            if not (root / name).exists():
                shutil.copyfile(source, root / name)
            assets[name] = digest
            copied[source] = name
        return copied[source]

    for directory in window_dirs:
        source = Path(directory).expanduser().resolve()
        audit = validate_temporal_training_windows(source, require_pose_context=True)
        sources.append({"manifest_sha256": audit["manifest_sha256"], "path": str(source)})
        manifest = _read_json(source / "manifest.json", "temporal windows")
        for split in rows:
            for window in _read_jsonl(source / manifest["splits"][split]["path"], "temporal windows"):
                calibration_id = window["calibration_id"]
                if calibration_id not in registry or not isinstance(registry[calibration_id], str):
                    raise ValueError(f"Missing calibration registry entry: {calibration_id}")
                calibration = load_camera_calibration(registry_path.parent / registry[calibration_id])
                if calibration.calibration_id != calibration_id:
                    raise ValueError("Camera calibration ID differs from the temporal window.")
                frames = window["frames"]
                context = _camera_context(calibration, frames)
                paths = []
                timing = []
                for frame in frames:
                    if frame.get("timing_policy", LEGACY_TIMING) == EXPOSURE_TIMING:
                        checked_exposure_frame(frame, timing_cache)
                        if sha256_file(Path(frame["frame_path"])) != frame["frame_sha256"]:
                            raise ValueError("Camera image bytes differ from exposure audit")
                        reference = frame["timing_audit"]
                        timing.append({"policy": EXPOSURE_TIMING, "audit": copy_asset(reference["path"]),
                                       "sha256": reference["sha256"], "sequence": reference["sequence"],
                                       "timestamp_ns": frame["timestamp_ns"], "sensor_timestamp_ns": frame["sensor_timestamp_ns"],
                                       "attitude_rpy_rad": frame["attitude_rpy_rad"]})
                    elif frame.get("timing_policy", LEGACY_TIMING) != LEGACY_TIMING:
                        raise ValueError("Unknown frame timing policy")
                    elif not allow_legacy_timing:
                        raise ValueError("Map-conditioned training requires exposure-native timing; use --allow-legacy-timing only for research baselines")
                    else:
                        timing.append({"policy": LEGACY_TIMING})
                    image = cv2.imread(frame["frame_path"])
                    if image is None:
                        raise ValueError("Unreadable training frame.")
                    validate_image_size(image, calibration)
                    paths.append(copy_asset(frame["frame_path"]))
                anchor = window["anchor"]
                target = [anchor["supervision"]["position_enu_m"][k] for k in ("east", "north", "up")]
                candidates = []
                for index, tile in enumerate([anchor["positive"], *anchor["spatial_hard_negatives"]]):
                    geometry = tile.get("pixel_geometry")
                    if not isinstance(geometry, dict):
                        raise ValueError("Map pair lacks exact pixel_geometry; rebuild its tile layout and map pairs.")
                    uv = reference_enu_to_tile_uv(target, geometry)
                    inside = bool(np.all(uv >= 0) and np.all(uv < 1))
                    if inside != (index == 0):
                        raise ValueError("Positive is outside the map or a hard negative contains the reference position.")
                    image = cv2.imread(tile["image_path"])
                    if image is None or list(image.shape[1::-1]) != geometry["image_size"]:
                        raise ValueError("Map crop dimensions do not match pixel geometry.")
                    candidates.append({"tile_id": tile["tile_id"], "image": copy_asset(tile["image_path"]),
                                       "match": inside, "target_uv": uv.tolist() if inside else None,
                                       "metric_axes_en_m": tile_metric_axes(geometry),
                                       "pixel_geometry": geometry})
                times = np.asarray([f["sensor_timestamp_ns"] for f in frames], dtype=np.int64)
                offsets = (times - times[-1]).astype(float) / 1e9
                heading = anchor["supervision"].get("heading_rad")
                if heading is not None and not np.isfinite(heading):
                    raise ValueError("Invalid heading supervision.")
                rows[split].append({
                    "window_id": window["window_id"], "session_id": window["capture_session_id"],
                    "map_release_id": window["map_release_id"], "calibration_id": calibration_id,
                    "calibration": copy_asset(calibration.path), "camera_context": context,
                    "frames": paths, "time_offsets_s": offsets.tolist(),
                    "timing": timing,
                    "roll_pitch_rad": [f["attitude_rpy_rad"][:2] for f in frames], "candidates": candidates,
                    "heading_rad": heading, "heading_source": anchor["supervision"].get("heading_source", "unspecified"),
                    "position_reference": anchor["supervision"].get("position_reference", "offline_reference_unspecified_lever_arm"),
                })
    sessions = {split: {r["session_id"] for r in entries} for split, entries in rows.items()}
    if sessions["train"] & sessions["validation"]:
        raise ValueError("Capture session leakage across train and validation.")
    frame_sets = {split: {f for row in entries for f in row["frames"]} for split, entries in rows.items()}
    if frame_sets["train"] & frame_sets["validation"]:
        raise ValueError("Identical camera image bytes occur in both splits.")
    splits = {}
    for split, entries in rows.items():
        if not entries or len({r["window_id"] for r in entries}) != len(entries):
            raise ValueError("Empty split or duplicate cross-view window IDs.")
        path = root / f"{split}.jsonl"
        path.write_text("".join(json.dumps(row, sort_keys=True) + "\n" for row in entries), encoding="utf-8")
        splits[split] = {"path": path.name, "sha256": sha256_file(path), "window_count": len(entries),
                         "session_ids": sorted(sessions[split]), "map_release_ids": sorted({r["map_release_id"] for r in entries})}
    manifest = {"schema_version": CROSSVIEW_DATASET_SCHEMA, "policy": "offline_training_only", "sources": sources,
                "splits": splits, "assets_sha256": assets, "absolute_yaw_input": False,
                "location_semantics": "tile_relative_offline_reference_position_not_camera_ray_footprint",
                "geographic_holdout_verified": False, "px4_external_vision": "disabled"}
    manifest["allow_legacy_timing"] = allow_legacy_timing
    manifest["timing_policies"] = sorted({entry["policy"] for entries in rows.values() for row in entries for entry in row["timing"]})
    manifest["sensor_delay_calibrated"] = False
    manifest["gps_timing_calibrated"] = False
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def load_crossview_dataset(root: str | Path) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
    root = Path(root).expanduser().resolve()
    manifest = _read_json(root / "manifest.json", "cross-view dataset")
    if manifest.get("schema_version") != CROSSVIEW_DATASET_SCHEMA or manifest.get("policy") != "offline_training_only":
        raise ValueError("Unsupported map-conditioned dataset contract.")
    for name, digest in manifest["assets_sha256"].items():
        if sha256_file(_member(root, name)) != digest:
            raise ValueError(f"Cross-view asset checksum mismatch: {name}")
    rows = {}
    timing_cache: dict[tuple[str, str], dict[str, Any]] = {}
    for split in ("train", "validation"):
        info = manifest["splits"][split]
        path = _member(root, info["path"])
        if sha256_file(path) != info["sha256"]:
            raise ValueError("Cross-view split checksum mismatch.")
        rows[split] = _read_jsonl(path, split)
        if len(rows[split]) != info["window_count"] or not rows[split]:
            raise ValueError("Cross-view split count is invalid.")
        for row in rows[split]:
            count = len(row["frames"])
            times = np.asarray(row["time_offsets_s"], dtype=float)
            attitude = np.asarray(row["roll_pitch_rad"], dtype=float)
            context = np.asarray(row["camera_context"], dtype=float)
            if (count < 2 or times.shape != (count,) or not np.isfinite(times).all()
                    or not np.all(np.diff(times) > 0) or not np.isclose(times[-1], 0)
                    or attitude.shape != (count, 2) or not np.isfinite(attitude).all()
                    or context.shape != (11,) or not np.isfinite(context).all()):
                raise ValueError("Invalid frame timing, attitude or camera context in dataset.")
            timing = row.get("timing", [])
            if len(timing) != count:
                raise ValueError("Cross-view dataset lacks per-frame timing provenance; rebuild it")
            for index, entry in enumerate(timing):
                if entry.get("policy") == EXPOSURE_TIMING:
                    if manifest["assets_sha256"].get(entry.get("audit")) != entry.get("sha256"):
                        raise ValueError("Unchecked timing audit in cross-view dataset")
                    checked_exposure_frame({"timing_policy": EXPOSURE_TIMING, "capture_session_id": row["session_id"],
                        "timing_audit": {"path": str(_member(root, entry["audit"])), "sha256": entry["sha256"], "sequence": entry["sequence"]},
                        "timestamp_ns": entry["timestamp_ns"], "sensor_timestamp_ns": entry["sensor_timestamp_ns"],
                        "attitude_rpy_rad": entry["attitude_rpy_rad"],
                        "frame_sha256": manifest["assets_sha256"].get(row["frames"][index])}, timing_cache)
                    if not np.allclose(attitude[index], entry["attitude_rpy_rad"][:2], rtol=0, atol=1e-6):
                        raise ValueError("Model attitude input differs from timing audit")
                elif entry.get("policy") != LEGACY_TIMING or manifest.get("allow_legacy_timing") is not True:
                    raise ValueError("Legacy timing not authorized for this research dataset")
            if all(entry["policy"] == EXPOSURE_TIMING for entry in timing):
                stamps = [entry["sensor_timestamp_ns"] for entry in timing]
                if not np.allclose(times, [(stamp - stamps[-1]) / 1e9 for stamp in stamps], rtol=0, atol=1e-9):
                    raise ValueError("Model time offsets differ from exposure audit")
            if not row["candidates"] or not any(c["match"] for c in row["candidates"]) or not any(not c["match"] for c in row["candidates"]):
                raise ValueError("Each cross-view window needs positive and no-match candidates.")
            for candidate in row["candidates"]:
                axes = np.asarray(candidate["metric_axes_en_m"], dtype=float)
                if axes.shape != (4,) or not np.isfinite(axes).all() or abs(np.linalg.det(axes.reshape(2, 2))) < 1e-6:
                    raise ValueError("Invalid candidate map orientation/scale context.")
                if candidate["match"]:
                    uv = np.asarray(candidate["target_uv"], dtype=float)
                    if uv.shape != (2,) or not np.isfinite(uv).all() or np.any(uv < 0) or np.any(uv >= 1):
                        raise ValueError("Invalid tile-relative label.")
                elif candidate["target_uv"] is not None:
                    raise ValueError("No-match candidate must not have a location target.")
            for name in [*row["frames"], row["calibration"], *[c["image"] for c in row["candidates"]]]:
                if name not in manifest["assets_sha256"]:
                    raise ValueError("Dataset row references an unchecked asset.")
    if {r["session_id"] for r in rows["train"]} & {r["session_id"] for r in rows["validation"]}:
        raise ValueError("Cross-view dataset session leakage.")
    if {f for r in rows["train"] for f in r["frames"]} & {f for r in rows["validation"] for f in r["frames"]}:
        raise ValueError("Cross-view dataset image leakage.")
    policies = sorted({entry["policy"] for entries in rows.values() for row in entries for entry in row["timing"]})
    if policies != manifest.get("timing_policies"):
        raise ValueError("Dataset timing-policy summary differs from frame records")
    return manifest, rows


class MapConditionedDataset:
    def __init__(self, root: str | Path, *, split: str, image_size: int, grid_size: int, heading_bins: int):
        self.root = Path(root).expanduser().resolve()
        self.manifest, records = load_crossview_dataset(self.root)
        if split not in records or image_size <= 0 or grid_size <= 0 or heading_bins < 4:
            raise ValueError("Invalid map-conditioned dataset configuration.")
        self.rows = records[split]
        self.image_size, self.grid_size, self.heading_bins = image_size, grid_size, heading_bins

    def __len__(self) -> int:
        return len(self.rows)

    def __getitem__(self, index: int) -> dict[str, Any]:
        import torch
        row = self.rows[index]
        calibration = load_camera_calibration(_member(self.root, row["calibration"]))
        def image(name: str, camera: bool = False):
            return torch.from_numpy(image_tensor(_member(self.root, name), self.image_size,
                                                calibration=calibration if camera else None))
        count = len(row["candidates"])
        frames = torch.stack([image(name, True) for name in row["frames"]])
        labels = []
        for candidate in row["candidates"]:
            if candidate["match"]:
                u, v = candidate["target_uv"]
                if not 0 <= u < 1 or not 0 <= v < 1:
                    raise ValueError("Invalid tile-relative location label.")
                labels.append(int(v * self.grid_size) * self.grid_size + int(u * self.grid_size))
            else:
                labels.append(self.grid_size ** 2)
        heading = row["heading_rad"]
        heading_class = int((heading % (2 * np.pi)) / (2 * np.pi) * self.heading_bins) if heading is not None else -1
        return {"inputs": {
            "frame_window": frames.unsqueeze(0).expand(count, -1, -1, -1, -1),
            "candidate_map": torch.stack([image(c["image"]) for c in row["candidates"]]),
            "roll_pitch": torch.tensor(row["roll_pitch_rad"], dtype=torch.float32).unsqueeze(0).expand(count, -1, -1),
            "time_offsets_s": torch.tensor(row["time_offsets_s"], dtype=torch.float32).unsqueeze(0).expand(count, -1),
            "camera_context": torch.tensor(row["camera_context"], dtype=torch.float32).unsqueeze(0).expand(count, -1),
            "map_context": torch.tensor([c["metric_axes_en_m"] for c in row["candidates"]], dtype=torch.float32),
        }, "location_target": torch.tensor(labels),
            "heading_target": torch.tensor([heading_class if c["match"] else -1 for c in row["candidates"]]),
            "window_id": row["window_id"]}


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare portable map-conditioned student training inputs.")
    parser.add_argument("--window-dir", action="append", required=True, type=Path)
    parser.add_argument("--calibration-registry", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--allow-legacy-timing", action="store_true", help="Research baseline only: accept old receipt-time attitude context")
    args = parser.parse_args()
    result = build_crossview_dataset(args.window_dir, args.calibration_registry, args.output_dir, allow_legacy_timing=args.allow_legacy_timing)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
