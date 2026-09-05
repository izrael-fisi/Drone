"""Compile learned retrieval and local-feature assets from tiled map imagery.

This is the bridge between a COG tile layout and a MapRelease v2. It uses the
logical ONNX interfaces declared by an audited ModelRelease; there is no
classical descriptor fallback and no guessed tensor naming convention.
"""

from __future__ import annotations

import argparse
from contextlib import nullcontext
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
from typing import Any, Protocol

import numpy as np

from vision_nav.training_capture import sha256_file

from .map_tile_layout import MAP_TILE_LAYOUT_SCHEMA
from .map_landmarks import MapLandmarkSampler, local_feature_image_size
from .onnx_runtime import LearnedModelRunner
from .releases import ModelRelease, load_model_release
from .shadow_inference import _frame_inputs, filter_local_feature_outputs, load_shadow_frame


MAP_ASSET_COMPILATION_SCHEMA = "vision_nav_learned_map_assets_v1"


class LogicalRunner(Protocol):
    def run(self, role: str, logical_inputs: dict[str, np.ndarray]) -> dict[str, np.ndarray]: ...


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _read_layout(path: str | Path) -> tuple[Path, dict[str, Any]]:
    source = Path(path).expanduser().resolve()
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read map tile layout: {exc}") from exc
    if raw.get("schema_version") != MAP_TILE_LAYOUT_SCHEMA:
        raise ValueError(f"Map tile layout must use {MAP_TILE_LAYOUT_SCHEMA}.")
    tiles = raw.get("tiles")
    if not isinstance(tiles, list) or not tiles:
        raise ValueError("Map tile layout requires one or more tiles.")
    return source, raw


def _tagged_output(contract: dict[str, Any], outputs: dict[str, np.ndarray], tag: str, *, role: str) -> np.ndarray:
    names = [name for name, spec in contract.get("outputs", {}).items() if spec.get("shadow_output") == tag]
    if len(names) != 1:
        raise ValueError(f"{role} requires exactly one output tagged {tag}.")
    name = names[0]
    if name not in outputs:
        raise ValueError(f"{role} runner did not return declared output {name}.")
    value = np.asarray(outputs[name])
    if not np.issubdtype(value.dtype, np.number) or not np.all(np.isfinite(value)):
        raise ValueError(f"{role} output {name} contains invalid values.")
    return value.astype(np.float32, copy=False)


def _embedding(value: np.ndarray) -> np.ndarray:
    if value.ndim != 2 or value.shape[0] != 1 or value.shape[1] <= 0:
        raise ValueError("Global retrieval embedding must have shape [1, descriptor_dimension].")
    vector = value.reshape(-1)
    norm = float(np.linalg.norm(vector))
    if norm <= 0.0:
        raise ValueError("Global retrieval embedding has zero norm.")
    return vector / norm


def _local_features(keypoints: np.ndarray, descriptors: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    if keypoints.ndim not in {2, 3} or keypoints.shape[-1] != 2 or keypoints.shape[-2] <= 0:
        raise ValueError("Local keypoints must be shaped N x 2 or B x N x 2.")
    if descriptors.ndim not in {2, 3} or descriptors.shape[-2] != keypoints.shape[-2] or descriptors.shape[-1] <= 0:
        raise ValueError("Local descriptors must align to keypoints as N x D or B x N x D.")
    if keypoints.ndim != descriptors.ndim or keypoints.shape[:-2] != descriptors.shape[:-2]:
        raise ValueError("Local feature batch dimensions must match.")
    return keypoints, descriptors


def _tile_scale(tile: dict[str, Any]) -> float:
    bounds = tile.get("bounds_enu_m")
    window = tile.get("source_window_px")
    if not isinstance(bounds, list) or len(bounds) != 4 or not isinstance(window, dict):
        raise ValueError("Map tile requires bounds_enu_m and source_window_px.")
    width, height = window.get("width"), window.get("height")
    if not isinstance(width, int) or not isinstance(height, int) or width <= 0 or height <= 0:
        raise ValueError("Map tile source_window_px requires positive width and height.")
    east_span = float(bounds[2]) - float(bounds[0])
    north_span = float(bounds[3]) - float(bounds[1])
    if east_span <= 0.0 or north_span <= 0.0:
        raise ValueError("Map tile ENU bounds must have positive area.")
    return (east_span / width + north_span / height) / 2.0


def _write_faiss(path: Path, embeddings: np.ndarray, faiss_ids: np.ndarray) -> None:
    try:
        import faiss
    except ImportError as exc:
        raise RuntimeError("FAISS index output requires drone-vision-nav[neural-runtime].") from exc
    index = faiss.IndexIDMap2(faiss.IndexFlatIP(int(embeddings.shape[1])))
    index.add_with_ids(np.asarray(embeddings, dtype=np.float32), np.asarray(faiss_ids, dtype=np.int64))
    faiss.write_index(index, str(path))


def compile_learned_map_assets(
    tile_layout_path: str | Path,
    *,
    map_release_id: str,
    model_release: ModelRelease,
    runner: LogicalRunner,
    output_root: str | Path | None = None,
    index_format: str = "numpy_flat",
    elevation_spec: str | Path | None = None,
) -> dict[str, Any]:
    _, layout = _read_layout(tile_layout_path)
    context = MapLandmarkSampler(layout, elevation_spec) if elevation_spec else nullcontext(None)
    with context as sampler:
        return _compile_learned_map_assets(
            tile_layout_path, map_release_id=map_release_id, model_release=model_release,
            runner=runner, output_root=output_root, index_format=index_format, landmark_sampler=sampler,
        )


def _compile_learned_map_assets(
    tile_layout_path: str | Path,
    *,
    map_release_id: str,
    model_release: ModelRelease,
    runner: LogicalRunner,
    output_root: str | Path | None,
    index_format: str,
    landmark_sampler: MapLandmarkSampler | None,
) -> dict[str, Any]:
    """Create release-bound learned retrieval and XFeat cache artifacts.

    ``tile_layout_path`` must be inside the eventual MapRelease staging root so
    its image URIs remain valid after promotion. The result deliberately stops
    before MapRelease creation because COG/STAC/tile-index provenance is owned
    by the surrounding map-production workflow.
    """

    if not map_release_id or "@" not in map_release_id:
        raise ValueError("map_release_id must be the immutable release ID, for example field-area@1.0.0.")
    if index_format not in {"numpy_flat", "faiss"}:
        raise ValueError("index_format must be numpy_flat or faiss.")
    layout_path, layout = _read_layout(tile_layout_path)
    root = Path(output_root).expanduser().resolve() if output_root else layout_path.parent
    if layout_path.parent != root:
        raise ValueError("tile_layout.json must live directly under output_root so tile image URIs remain release-relative.")
    global_contract = model_release.artifact_interfaces.get("global_retriever")
    local_contract = model_release.artifact_interfaces.get("local_features")
    if not isinstance(global_contract, dict) or not isinstance(local_contract, dict):
        raise ValueError("ModelRelease requires global_retriever and local_features interface contracts.")
    landmark_image_size = local_feature_image_size(local_contract) if landmark_sampler else None

    index_root = root / "index"
    feature_root = root / "features" / "xfeat"
    retrieval_metadata_path = index_root / "megaloc_tiles.json"
    feature_metadata_path = index_root / "xfeat_tiles.json"
    tile_index_path = index_root / "tiles.sqlite"
    feature_index_path = index_root / "xfeat_tiles.sqlite"
    compilation_path = index_root / "learned_map_assets.json"
    landmark_path = index_root / "landmark_provenance.json"
    index_path = index_root / ("megaloc.npz" if index_format == "numpy_flat" else "megaloc.faiss")
    outputs = (retrieval_metadata_path, feature_metadata_path, tile_index_path, feature_index_path, compilation_path, index_path)
    if any(path.exists() for path in outputs) or feature_root.exists():
        raise ValueError("Learned map asset destination already exists; create a new immutable map-release staging directory.")
    index_root.mkdir(parents=True)
    feature_root.mkdir(parents=True)

    embedding_rows: list[np.ndarray] = []
    faiss_ids: list[int] = []
    retrieval_tiles: list[dict[str, Any]] = []
    feature_tiles: list[dict[str, Any]] = []
    feature_count = 0
    landmark_tiles: list[dict[str, Any]] = []
    for index, tile in enumerate(layout["tiles"]):
        if not isinstance(tile, dict):
            raise ValueError("Map tile layout tile entries must be objects.")
        tile_id = tile.get("tile_id")
        image_uri = tile.get("image_uri")
        center = tile.get("center_enu_m")
        bounds = tile.get("bounds_enu_m")
        if not isinstance(tile_id, str) or not tile_id or Path(tile_id).name != tile_id or tile_id in {".", ".."}:
            raise ValueError("Map tile layout contains a tile without tile_id.")
        if not isinstance(image_uri, str) or not image_uri or Path(image_uri).is_absolute() or ".." in Path(image_uri).parts:
            raise ValueError(f"Map tile {tile_id} has an invalid release-relative image_uri.")
        if not isinstance(center, list) or len(center) != 3 or not isinstance(bounds, list) or len(bounds) != 4:
            raise ValueError(f"Map tile {tile_id} lacks valid ENU center/bounds.")
        image_path = (root / image_uri).resolve()
        if not image_path.is_file():
            raise ValueError(f"Map tile image does not exist: {image_path}")
        if landmark_sampler and sha256_file(image_path) != tile.get("image_sha256"):
            raise ValueError(f"Map tile {tile_id} image changed or lacks its layout checksum; rebuild the layout.")
        frame = load_shadow_frame(image_path)
        if landmark_sampler and (frame.width != tile["source_window_px"]["width"] or frame.height != tile["source_window_px"]["height"]):
            raise ValueError(f"Map tile {tile_id} dimensions do not match its source window.")
        global_inputs, global_reason = _frame_inputs(global_contract, frame)
        local_inputs, local_reason = _frame_inputs(local_contract, frame)
        if global_inputs is None:
            raise ValueError(f"Map tile {tile_id} cannot satisfy global retriever inputs: {global_reason}")
        if local_inputs is None:
            raise ValueError(f"Map tile {tile_id} cannot satisfy local feature inputs: {local_reason}")
        embedding = _embedding(_tagged_output(global_contract, runner.run("global_retriever", global_inputs), "retrieval_embedding", role="global_retriever"))
        local_outputs = runner.run("local_features", local_inputs)
        local_outputs, _ = filter_local_feature_outputs(local_contract, local_outputs)
        keypoints, descriptors = _local_features(
            _tagged_output(local_contract, local_outputs, "local_keypoints", role="local_features"),
            _tagged_output(local_contract, local_outputs, "local_descriptors", role="local_features"),
        )
        feature_path = feature_root / f"{tile_id}.npz"
        geometry_arrays = {}
        if landmark_sampler is not None:
            if keypoints.ndim == 3 and keypoints.shape[0] != 1:
                raise ValueError("Map feature compilation requires a single tile batch.")
            valid, points_enu, variance = landmark_sampler.sample(tile, keypoints, landmark_image_size)
            if np.count_nonzero(valid) < 6:
                raise ValueError(f"Map tile {tile_id} has fewer than six valid surface-backed features.")
            keypoints, descriptors = keypoints[..., valid, :], descriptors[..., valid, :]
            geometry_arrays = {"map_points_enu_m": points_enu, "map_point_variance_enu_m2": variance}
            landmark_tiles.append({"tile_id": tile_id, "valid_features": int(valid.sum()),
                "rejected_features": int((~valid).sum()), "surface_filter": landmark_sampler.last_sample_report})
        np.savez_compressed(feature_path, keypoints=keypoints, descriptors=descriptors, **geometry_arrays)
        # LocalFeatureCache resolves paths from the metadata file's directory
        # (`index/`), whereas retrieval image URIs are release-root-relative.
        feature_uri = os.path.relpath(feature_path, start=feature_metadata_path.parent).replace("\\", "/")
        embedding_rows.append(embedding)
        faiss_ids.append(index)
        feature_count += int(keypoints.shape[-2])
        retrieval_tiles.append(
            {
                "tile_id": tile_id,
                "faiss_id": index,
                "center_enu_m": [float(value) for value in center],
                "scale_m_per_px": round(_tile_scale(tile), 8),
                "bounds_enu_m": [float(value) for value in bounds],
                "image_uri": image_uri,
                "pixel_geometry": tile.get("pixel_geometry"),
            }
        )
        feature_tiles.append({"tile_id": tile_id, "feature_uri": feature_uri, "sha256": sha256_file(feature_path)})

    embeddings = np.stack(embedding_rows).astype(np.float32, copy=False)
    numeric_ids = np.asarray(faiss_ids, dtype=np.int64)
    if index_format == "numpy_flat":
        np.savez_compressed(index_path, embeddings=embeddings, faiss_ids=numeric_ids)
    else:
        _write_faiss(index_path, embeddings, numeric_ids)
    retrieval_metadata = {
        "schema_version": "vision_nav_retrieval_index_v1",
        "map_release_id": map_release_id,
        "model_release_id": model_release.release_id,
        "descriptor_dimension": int(embeddings.shape[1]),
        "metric": "inner_product_cosine",
        "normalize_query": True,
        "index_format": index_format,
        "tiles": retrieval_tiles,
    }
    local_metadata = {
        "schema_version": "vision_nav_local_feature_cache_v1",
        "map_release_id": map_release_id,
        "model_release_id": model_release.release_id,
        "tiles": feature_tiles,
    }
    if landmark_sampler is not None:
        provenance = {**landmark_sampler.provenance, "map_release_id": map_release_id,
                      "model_release_id": model_release.release_id, "tiles": landmark_tiles}
        landmark_path.write_text(json.dumps(provenance, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    retrieval_metadata_path.write_text(json.dumps(retrieval_metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    feature_metadata_path.write_text(json.dumps(local_metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    with sqlite3.connect(feature_index_path) as connection:
        connection.execute(
            "CREATE TABLE tile_features (tile_id TEXT PRIMARY KEY, feature_uri TEXT NOT NULL, sha256 TEXT NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO tile_features (tile_id, feature_uri, sha256) VALUES (?, ?, ?)",
            [(item["tile_id"], item["feature_uri"], item["sha256"]) for item in feature_tiles],
        )
        connection.commit()
    with sqlite3.connect(tile_index_path) as connection:
        connection.execute(
            "CREATE TABLE tiles (tile_id TEXT PRIMARY KEY, image_uri TEXT NOT NULL, center_east_m REAL NOT NULL, "
            "center_north_m REAL NOT NULL, center_up_m REAL NOT NULL, min_east_m REAL NOT NULL, min_north_m REAL NOT NULL, "
            "max_east_m REAL NOT NULL, max_north_m REAL NOT NULL, scale_m_per_px REAL NOT NULL)"
        )
        connection.executemany(
            "INSERT INTO tiles (tile_id, image_uri, center_east_m, center_north_m, center_up_m, min_east_m, min_north_m, max_east_m, max_north_m, scale_m_per_px) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            [
                (
                    item["tile_id"], item["image_uri"], *item["center_enu_m"], *item["bounds_enu_m"], item["scale_m_per_px"],
                )
                for item in retrieval_tiles
            ],
        )
        connection.commit()
    compilation = {
        "schema_version": MAP_ASSET_COMPILATION_SCHEMA,
        "created_utc": _utc_now(),
        "map_release_id": map_release_id,
        "model_release_id": model_release.release_id,
        "tile_layout": {"path": str(layout_path), "sha256": sha256_file(layout_path)},
        "index_format": index_format,
        "tile_count": len(retrieval_tiles),
        "feature_count": feature_count,
        "supported_geometry_modes": ["pnp_3d", "homography_restricted"] if landmark_sampler else ["homography_restricted"],
        "assets": {
            "retrieval_index_uri": str(index_path.relative_to(root)).replace("\\", "/"),
            "retrieval_metadata_uri": str(retrieval_metadata_path.relative_to(root)).replace("\\", "/"),
            "tile_index_uri": str(tile_index_path.relative_to(root)).replace("\\", "/"),
            "xfeat_index_uri": str(feature_index_path.relative_to(root)).replace("\\", "/"),
            "local_feature_metadata_uri": str(feature_metadata_path.relative_to(root)).replace("\\", "/"),
        },
        "checksums_sha256": {
            str(index_path.relative_to(root)).replace("\\", "/"): sha256_file(index_path),
            str(retrieval_metadata_path.relative_to(root)).replace("\\", "/"): sha256_file(retrieval_metadata_path),
            str(tile_index_path.relative_to(root)).replace("\\", "/"): sha256_file(tile_index_path),
            str(feature_index_path.relative_to(root)).replace("\\", "/"): sha256_file(feature_index_path),
            str(feature_metadata_path.relative_to(root)).replace("\\", "/"): sha256_file(feature_metadata_path),
        },
        "publication": {"px4_external_vision": "disabled", "shadow_only": True},
    }
    if landmark_sampler is not None:
        location = landmark_path.relative_to(root).as_posix()
        compilation["assets"]["landmark_provenance_uri"] = location
        compilation["checksums_sha256"][location] = sha256_file(landmark_path)
    compilation_path.write_text(json.dumps(compilation, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"output_root": str(root), "compilation_path": str(compilation_path), "compilation": compilation}


def main() -> None:
    parser = argparse.ArgumentParser(description="Compile MegaLoc/XFeat map assets from a tile layout and audited ONNX ModelRelease.")
    parser.add_argument("--tile-layout", required=True, type=Path)
    parser.add_argument("--map-release-id", required=True)
    parser.add_argument("--model-release", required=True, type=Path)
    parser.add_argument("--artifact-root", type=Path, help="Defaults to the ModelRelease directory.")
    parser.add_argument("--output-root", type=Path, help="Defaults to the tile_layout.json parent directory.")
    parser.add_argument("--provider", default="onnxruntime_cpu")
    parser.add_argument("--index-format", choices=("numpy_flat", "faiss"), default="numpy_flat")
    parser.add_argument("--elevation-spec", type=Path, help="Audited WGS84 ellipsoidal DSM or explicitly masked v2 DTM source JSON.")
    args = parser.parse_args()
    model_path = args.model_release.expanduser().resolve()
    model = load_model_release(model_path)
    try:
        result = compile_learned_map_assets(
            args.tile_layout,
            map_release_id=args.map_release_id,
            model_release=model,
            runner=LearnedModelRunner(model, artifact_root=args.artifact_root or model_path.parent, provider=args.provider),
            output_root=args.output_root,
            index_format=args.index_format,
            elevation_spec=args.elevation_spec,
        )
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result["compilation"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
