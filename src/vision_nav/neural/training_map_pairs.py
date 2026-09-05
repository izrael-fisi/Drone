"""Build release-bound UAV-frame to map-tile pairs for learned training."""

from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
from typing import Any

from vision_nav.training_capture import sha256_file

from .map_tile_layout import MAP_TILE_LAYOUT_SCHEMA
from .reference_map import load_reference_map
from .training_experiment import EXPERIMENT_INPUT_SCHEMA


MAP_PAIR_SCHEMA = "vision_nav_training_map_pairs_v1"


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
    rows: list[dict[str, Any]] = []
    for line_number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{label}:{line_number} is invalid JSON: {exc}") from exc
        if not isinstance(row, dict):
            raise ValueError(f"{label}:{line_number} must be a JSON object.")
        rows.append(row)
    return rows


def _resolve(root: Path, uri: Any, label: str) -> Path:
    if not isinstance(uri, str) or not uri:
        raise ValueError(f"{label} is missing a relative file URI.")
    path = Path(uri)
    if not path.is_absolute():
        path = root / path
    path = path.resolve()
    if not path.is_file():
        raise ValueError(f"{label} does not exist: {path}")
    return path


def _normalize_tiles(raw_tiles: Any, *, map_root: Path, label: str) -> list[dict[str, Any]]:
    if not isinstance(raw_tiles, list):
        raise ValueError(f"{label} tiles must be a list.")
    tiles: list[dict[str, Any]] = []
    for raw in raw_tiles:
        if not isinstance(raw, dict):
            raise ValueError(f"{label} tile entries must be objects.")
        tile_id = raw.get("tile_id")
        center = raw.get("center_enu_m")
        bounds = raw.get("bounds_enu_m")
        if not isinstance(tile_id, str) or not tile_id:
            raise ValueError(f"{label} tile is missing tile_id.")
        if not isinstance(center, list) or len(center) != 3 or not all(isinstance(value, (int, float)) for value in center):
            raise ValueError(f"{label} tile {tile_id} has invalid center_enu_m.")
        if not isinstance(bounds, list) or len(bounds) != 4 or not all(isinstance(value, (int, float)) for value in bounds):
            raise ValueError(f"{label} tile {tile_id} needs bounds_enu_m=[min_east,min_north,max_east,max_north] for training.")
        min_east, min_north, max_east, max_north = (float(value) for value in bounds)
        if not min_east < max_east or not min_north < max_north:
            raise ValueError(f"{label} tile {tile_id} has invalid ENU bounds.")
        image_path = _resolve(map_root, raw.get("image_uri"), f"{label} tile {tile_id} image_uri")
        tiles.append(
            {
                "tile_id": tile_id,
                "image_path": str(image_path),
                "center_enu_m": [float(value) for value in center],
                "bounds_enu_m": [min_east, min_north, max_east, max_north],
                "pixel_geometry": raw.get("pixel_geometry"),
            }
        )
    if not tiles:
        raise ValueError(f"{label} contains no training tile entries.")
    if len({tile["tile_id"] for tile in tiles}) != len(tiles):
        raise ValueError(f"{label} contains duplicate tile_id values.")
    return tiles


def _tile_catalog(metadata_path: Path, *, map_root: Path, map_release_id: str, model_release_id: str) -> list[dict[str, Any]]:
    metadata = _read_json(metadata_path, "retrieval metadata")
    if metadata.get("schema_version") != "vision_nav_retrieval_index_v1":
        raise ValueError("Training map pairs require vision_nav_retrieval_index_v1 metadata.")
    if metadata.get("map_release_id") != map_release_id or metadata.get("model_release_id") != model_release_id:
        raise ValueError("Retrieval metadata does not match the selected MapRelease/model release.")
    return _normalize_tiles(metadata.get("tiles"), map_root=map_root, label="Retrieval metadata")


def _reference_tile_catalog(reference_map_path: Path, tile_layout_path: Path) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    """Load first-training tile crops without requiring a learned MapRelease.

    The tile layout must be derived from the immutable imagery pinned by the
    ReferenceMapRelease. This gives the first teacher an auditable source map,
    but deliberately does not produce any Pi deployment asset.
    """

    reference = load_reference_map(reference_map_path)
    layout = _read_json(tile_layout_path, "reference map tile layout")
    if layout.get("schema_version") != MAP_TILE_LAYOUT_SCHEMA:
        raise ValueError(f"Reference map tile layout must use {MAP_TILE_LAYOUT_SCHEMA}.")
    cog = layout.get("cog") if isinstance(layout.get("cog"), dict) else {}
    imagery_uri = str(reference["imagery_uri"])
    expected_digest = reference["checksums_sha256"].get(imagery_uri)
    if cog.get("sha256") != expected_digest:
        raise ValueError("Reference tile layout COG checksum does not match the selected ReferenceMapRelease imagery.")
    origin = layout.get("geodetic_origin_wgs84") if isinstance(layout.get("geodetic_origin_wgs84"), dict) else {}
    expected_origin = reference["geodetic_origin_wgs84"]
    for layout_key, reference_key in (("lat_deg", "lat_deg"), ("lon_deg", "lon_deg"), ("altitude_m", "altitude_m")):
        value = origin.get(layout_key)
        expected = expected_origin.get(reference_key)
        if not isinstance(value, (int, float)) or not isinstance(expected, (int, float)) or abs(float(value) - float(expected)) > 1e-6:
            raise ValueError("Reference tile layout ENU origin does not match the selected ReferenceMapRelease.")
    tiles = _normalize_tiles(layout.get("tiles"), map_root=tile_layout_path.parent, label="Reference map tile layout")
    return str(reference["release_id"]), tiles, {
        "kind": "reference_map_release",
        "release_id": reference["release_id"],
        "manifest_path": str(reference_map_path),
        "manifest_sha256": sha256_file(reference_map_path),
        "tile_layout_path": str(tile_layout_path),
        "tile_layout_sha256": sha256_file(tile_layout_path),
    }


def _distance_xy(a: tuple[float, float], b: tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _positive_tile(
    target: tuple[float, float], tiles: list[dict[str, Any]], *, max_center_distance_m: float
) -> tuple[dict[str, Any] | None, float | None]:
    containing = [
        tile
        for tile in tiles
        if tile["bounds_enu_m"][0] <= target[0] <= tile["bounds_enu_m"][2]
        and tile["bounds_enu_m"][1] <= target[1] <= tile["bounds_enu_m"][3]
    ]
    candidates = containing or tiles
    ranked = sorted(
        ((_distance_xy(target, (tile["center_enu_m"][0], tile["center_enu_m"][1])), tile) for tile in candidates),
        key=lambda item: (item[0], item[1]["tile_id"]),
    )
    distance, tile = ranked[0]
    if not containing and distance > max_center_distance_m:
        return None, distance
    return tile, distance


def _negatives(
    target: tuple[float, float], positive_tile_id: str, tiles: list[dict[str, Any]], *, min_distance_m: float, count: int
) -> list[dict[str, Any]]:
    candidates = []
    for tile in tiles:
        if tile["tile_id"] == positive_tile_id:
            continue
        distance = _distance_xy(target, (tile["center_enu_m"][0], tile["center_enu_m"][1]))
        if distance >= min_distance_m:
            candidates.append((distance, tile))
    return [
        {"tile_id": tile["tile_id"], "image_path": tile["image_path"], "center_distance_m": round(distance, 4),
         "pixel_geometry": tile.get("pixel_geometry")}
        for distance, tile in sorted(candidates, key=lambda item: (item[0], item[1]["tile_id"]))[:count]
    ]


def build_training_map_pairs(
    experiment_dir: str | Path,
    map_release_path: str | Path | None,
    output_dir: str | Path,
    *,
    reference_map_path: str | Path | None = None,
    tile_layout_path: str | Path | None = None,
    negative_count: int = 4,
    min_negative_distance_m: float = 75.0,
    max_positive_center_distance_m: float = 100.0,
) -> dict[str, Any]:
    """Create positive/negative map-tile pairs from map-aligned teacher inputs."""

    if negative_count <= 0 or min_negative_distance_m <= 0.0 or max_positive_center_distance_m <= 0.0:
        raise ValueError("Negative count and training pair distances must be positive.")
    experiment_root = Path(experiment_dir).expanduser().resolve()
    experiment_manifest_path = experiment_root / "manifest.json"
    experiment = _read_json(experiment_manifest_path, "teacher experiment manifest")
    if experiment.get("schema_version") != EXPERIMENT_INPUT_SCHEMA:
        raise ValueError("Training map pairs require a vision_nav_teacher_experiment_input_v1 package.")
    if (map_release_path is None) == (reference_map_path is None):
        raise ValueError("Use exactly one of a MapRelease v2 or a ReferenceMapRelease for training map pairs.")
    map_source: dict[str, Any]
    retrieval_metadata_path: Path | None = None
    if map_release_path is not None:
        map_path = Path(map_release_path).expanduser().resolve()
        map_release = _read_json(map_path, "MapRelease")
        if map_release.get("schema_version") != "vision_nav_map_release_v2":
            raise ValueError("Training map pairs require a MapRelease v2.")
        map_id = map_release.get("release_id")
        model_id = map_release.get("model_release_id")
        if not isinstance(map_id, str) or not isinstance(model_id, str):
            raise ValueError("MapRelease is missing release_id or model_release_id.")
        retrieval_uri = map_release.get("retrieval_metadata_uri")
        retrieval_metadata_path = _resolve(map_path.parent, retrieval_uri, "MapRelease retrieval_metadata_uri")
        tiles = _tile_catalog(retrieval_metadata_path, map_root=map_path.parent, map_release_id=map_id, model_release_id=model_id)
        map_source = {
            "kind": "deployment_map_release",
            "release_id": map_id,
            "manifest_path": str(map_path),
            "manifest_sha256": sha256_file(map_path),
            "retrieval_metadata_path": str(retrieval_metadata_path),
            "retrieval_metadata_sha256": sha256_file(retrieval_metadata_path),
        }
    else:
        if tile_layout_path is None:
            raise ValueError("ReferenceMapRelease training pairs require --tile-layout.")
        reference_path = Path(reference_map_path).expanduser().resolve()
        layout_path = Path(tile_layout_path).expanduser().resolve()
        if not layout_path.is_file():
            raise ValueError(f"Reference map tile layout does not exist: {layout_path}")
        map_id, tiles, map_source = _reference_tile_catalog(reference_path, layout_path)
    labels_reference = experiment.get("labels") if isinstance(experiment.get("labels"), dict) else {}
    labels_manifest_path = Path(str(labels_reference.get("path") or "")).expanduser().resolve() / "manifest.json"
    if not labels_manifest_path.is_file():
        raise ValueError("Teacher experiment label manifest is missing.")
    expected_labels_manifest_digest = labels_reference.get("manifest_sha256")
    if not isinstance(expected_labels_manifest_digest, str) or sha256_file(labels_manifest_path) != expected_labels_manifest_digest:
        raise ValueError("Teacher experiment label manifest checksum mismatch.")
    coordinate_frame = _read_json(labels_manifest_path, "label manifest").get("coordinate_frame")
    if not isinstance(coordinate_frame, dict) or (coordinate_frame.get("map_release") or {}).get("release_id") != map_id:
        raise ValueError("Teacher labels are not anchored to the selected map source geodetic origin.")
    bound_hash = coordinate_frame["map_release"].get("manifest_sha256")
    if bound_hash is not None and bound_hash != map_source["manifest_sha256"]:
        raise ValueError("Teacher label map manifest differs from the selected map source")

    root = Path(output_dir).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError(f"Training map-pair output directory must be empty: {root}")
    results: dict[str, list[dict[str, Any]]] = {}
    unmatched: list[str] = []
    missing_negatives: list[str] = []
    for split_name in ("train", "validation"):
        split_path = experiment_root / str((experiment.get(split_name) or {}).get("path") or f"{split_name}.jsonl")
        expected_digest = (experiment.get(split_name) or {}).get("sha256")
        if sha256_file(split_path) != expected_digest:
            raise ValueError(f"Teacher experiment {split_name} split checksum mismatch.")
        rows: list[dict[str, Any]] = []
        for item in _read_jsonl(split_path, f"teacher {split_name} input"):
            sample = item.get("sample") if isinstance(item.get("sample"), dict) else {}
            supervision = item.get("supervision") if isinstance(item.get("supervision"), dict) else {}
            enu = supervision.get("position_enu_m") if isinstance(supervision.get("position_enu_m"), dict) else {}
            east, north = enu.get("east"), enu.get("north")
            if sample.get("map_release_id") != map_id or not isinstance(east, (int, float)) or not isinstance(north, (int, float)):
                unmatched.append(str(sample.get("sample_id") or "unknown"))
                continue
            target = (float(east), float(north))
            positive, distance = _positive_tile(target, tiles, max_center_distance_m=max_positive_center_distance_m)
            if positive is None:
                unmatched.append(str(sample.get("sample_id") or "unknown"))
                continue
            negatives = _negatives(target, positive["tile_id"], tiles, min_distance_m=min_negative_distance_m, count=negative_count)
            if not negatives:
                missing_negatives.append(str(sample.get("sample_id") or "unknown"))
                continue
            rows.append(
                {
                    "schema_version": MAP_PAIR_SCHEMA,
                    "sample": sample,
                    "supervision": supervision,
                    "map_release_id": map_id,
                    "positive": {
                        "tile_id": positive["tile_id"], "image_path": positive["image_path"],
                        "center_distance_m": round(float(distance or 0.0), 4), "bounds_enu_m": positive["bounds_enu_m"],
                        "pixel_geometry": positive.get("pixel_geometry"),
                    },
                    "spatial_hard_negatives": negatives,
                }
            )
        results[split_name] = rows
    if unmatched:
        raise ValueError(f"No valid positive map tile for {len(unmatched)} labeled sample(s): {', '.join(unmatched[:8])}")
    if missing_negatives:
        raise ValueError(f"No spatial hard negative for {len(missing_negatives)} sample(s): {', '.join(missing_negatives[:8])}")
    if not results["train"] or not results["validation"]:
        raise ValueError("Training map pairs require at least one train and one validation pair.")

    root.mkdir(parents=True, exist_ok=True)
    output_files: dict[str, dict[str, Any]] = {}
    for split_name, rows in results.items():
        path = root / f"{split_name}.jsonl"
        with path.open("w", encoding="utf-8") as stream:
            for row in rows:
                stream.write(json.dumps(row, sort_keys=True) + "\n")
        output_files[split_name] = {"path": path.name, "sha256": sha256_file(path), "pair_count": len(rows)}
    manifest = {
        "schema_version": MAP_PAIR_SCHEMA,
        "policy": "offline_training_only",
        "teacher_experiment": {"path": str(experiment_root), "manifest_sha256": sha256_file(experiment_manifest_path)},
        "map_source": map_source,
        # Retained for readers of older map-pair manifests. New consumers use
        # map_source.kind to distinguish pre-model reference maps from Pi maps.
        "map_release": {"release_id": map_id, "manifest_path": map_source["manifest_path"], "manifest_sha256": map_source["manifest_sha256"]},
        "retrieval_metadata": (
            {"path": str(retrieval_metadata_path), "sha256": sha256_file(retrieval_metadata_path)}
            if retrieval_metadata_path is not None else None
        ),
        "pair_policy": {"negative_count": negative_count, "min_negative_distance_m": min_negative_distance_m, "max_positive_center_distance_m": max_positive_center_distance_m},
        "splits": output_files,
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"output_dir": str(root), "manifest_path": str(manifest_path), "manifest": manifest}


def main() -> None:
    parser = argparse.ArgumentParser(description="Build verified UAV-frame/map-tile pairs for learned cross-view training.")
    parser.add_argument("--teacher-experiment", required=True, type=Path)
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--map-release", type=Path, help="Model-bound deployment MapRelease v2.")
    source.add_argument("--reference-map", type=Path, help="Model-independent ReferenceMapRelease for first teacher training.")
    parser.add_argument("--tile-layout", type=Path, help="COG tile layout required with --reference-map.")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--negative-count", type=int, default=4)
    parser.add_argument("--min-negative-distance-m", type=float, default=75.0)
    parser.add_argument("--max-positive-center-distance-m", type=float, default=100.0)
    args = parser.parse_args()
    try:
        result = build_training_map_pairs(
            args.teacher_experiment, args.map_release, args.output_dir,
            reference_map_path=args.reference_map, tile_layout_path=args.tile_layout,
            negative_count=args.negative_count,
            min_negative_distance_m=args.min_negative_distance_m,
            max_positive_center_distance_m=args.max_positive_center_distance_m,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result["manifest"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
