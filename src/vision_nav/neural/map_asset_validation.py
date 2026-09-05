"""Deep integrity checks for learned MapRelease retrieval and feature assets."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import sqlite3
import numpy as np
from typing import Any

from .local_feature_cache import load_local_feature_cache
from .map_release import load_release
from .retrieval_index import load_retrieval_metadata, load_tile_retriever
from .map_landmarks import LANDMARK_SCHEMA


MAP_ASSET_VALIDATION_SCHEMA = "vision_nav_learned_map_asset_validation_v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _asset_path(root: Path, location: Any, label: str) -> Path:
    if not isinstance(location, str) or not location:
        raise ValueError(f"MapRelease is missing {label}.")
    path = Path(location).expanduser()
    path = path if path.is_absolute() else root / path
    return path.resolve()


def _under_root(path: Path, root: Path, label: str) -> None:
    try:
        path.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"{label} resolves outside the MapRelease root: {path}") from exc


def _sqlite_rows(path: Path, table: str, columns: tuple[str, ...]) -> list[tuple[Any, ...]]:
    try:
        with sqlite3.connect(path) as connection:
            observed = {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}
            if not set(columns).issubset(observed):
                raise ValueError(f"SQLite table {table} has unexpected columns.")
            return list(connection.execute(f"SELECT {', '.join(columns)} FROM {table} ORDER BY {columns[0]}"))
    except sqlite3.Error as exc:
        raise ValueError(f"Could not read SQLite table {table}: {exc}") from exc


def validate_learned_map_assets(map_release_path: str | Path) -> dict[str, Any]:
    """Validate all learned map assets referenced by an immutable MapRelease.

    This is a preflight/audit operation. It intentionally loads every local
    feature archive and can be more expensive than normal Pi startup.
    """

    release_path = Path(map_release_path).expanduser().resolve()
    root = release_path.parent
    errors: list[str] = []
    metrics: dict[str, Any] = {"tile_count": 0, "feature_tile_count": 0, "feature_count": 0}
    release_id: str | None = None
    model_release_id: str | None = None
    index_format: str | None = None
    try:
        release = load_release(release_path)
        release_id = str(release["release_id"])
        model_release_id = str(release["model_release_id"])
        retrieval_metadata_path = _asset_path(root, release.get("retrieval_metadata_uri"), "retrieval_metadata_uri")
        local_metadata_path = _asset_path(root, release.get("local_feature_metadata_uri"), "local_feature_metadata_uri")
        retrieval_index_path = _asset_path(root, release.get("retrieval_index_uri"), "retrieval_index_uri")
        tile_index_path = _asset_path(root, release.get("tile_index_uri"), "tile_index_uri")
        feature_index_path = _asset_path(root, release.get("xfeat_index_uri"), "xfeat_index_uri")
        for label, path in (
            ("retrieval metadata", retrieval_metadata_path),
            ("local feature metadata", local_metadata_path),
            ("retrieval index", retrieval_index_path),
            ("tile index", tile_index_path),
            ("XFeat index", feature_index_path),
        ):
            _under_root(path, root, label)
            if not path.is_file():
                raise ValueError(f"{label} is missing: {path}")
        retrieval_metadata = load_retrieval_metadata(
            retrieval_metadata_path,
            expected_map_release_id=release_id,
            expected_model_release_id=model_release_id,
        )
        raw_retrieval_metadata = json.loads(retrieval_metadata_path.read_text(encoding="utf-8"))
        raw_tiles_by_id = {
            str(tile.get("tile_id")): tile
            for tile in raw_retrieval_metadata.get("tiles", [])
            if isinstance(tile, dict) and isinstance(tile.get("tile_id"), str)
        }
        index_format = retrieval_metadata.index_format
        # Constructor validation verifies vector dimensions, all numeric IDs,
        # and all index entries against the immutable metadata.
        load_tile_retriever(
            retrieval_index_path,
            retrieval_metadata_path,
            expected_map_release_id=release_id,
            expected_model_release_id=model_release_id,
        )
        cache = load_local_feature_cache(
            local_metadata_path,
            expected_map_release_id=release_id,
            expected_model_release_id=model_release_id,
        )
        expected_tiles = {tile.tile_id: tile for tile in retrieval_metadata.tiles}
        metric_geometry = "pnp_3d" in release["geometry_modes"]
        if metric_geometry:
            provenance_path = _asset_path(root, release.get("landmark_provenance_uri"), "landmark_provenance_uri")
            _under_root(provenance_path, root, "landmark provenance")
            provenance = json.loads(provenance_path.read_text(encoding="utf-8"))
            if (provenance.get("schema_version") != LANDMARK_SCHEMA or provenance.get("frame") != "ENU"
                    or provenance.get("map_release_id") != release_id or provenance.get("model_release_id") != model_release_id
                    or provenance.get("geodetic_origin_wgs84") != release.get("geodetic_origin_wgs84")
                    or provenance.get("origin_vertical_datum") != "wgs84_ellipsoid"):
                raise ValueError("Metric landmark provenance does not match the map/model/origin contract.")
            from .map_landmarks import validate_surface_contract
            mask = validate_surface_contract(provenance.get("elevation", {}))
            if mask and mask["imagery_sha256"] != provenance.get("imagery_sha256"):
                raise ValueError("Ground-support mask and landmark imagery differ")
        if set(cache.tiles) != set(expected_tiles):
            raise ValueError("Retrieval and local-feature metadata contain different tile IDs.")
        for tile_id, tile in cache.tiles.items():
            feature_path = (cache.root / tile.feature_uri).resolve()
            _under_root(feature_path, root, f"XFeat feature {tile_id}")
            features = cache.load(tile_id)
            if metric_geometry:
                if "map_points_enu_m" not in features or "map_point_variance_enu_m2" not in features:
                    raise ValueError(f"pnp_3d advertised without metric landmarks and uncertainty: {tile_id}")
                points = features["map_points_enu_m"].reshape(-1, 3)
                if len(points) < 6 or np.linalg.matrix_rank(points - points.mean(axis=0), tol=1e-6) < 2:
                    raise ValueError(f"pnp_3d requires at least six spatially distributed landmarks: {tile_id}")
            metrics["feature_count"] += int(features["keypoints"].shape[-2])
        tile_rows = _sqlite_rows(
            tile_index_path,
            "tiles",
            (
                "tile_id", "image_uri", "center_east_m", "center_north_m", "center_up_m",
                "min_east_m", "min_north_m", "max_east_m", "max_north_m", "scale_m_per_px",
            ),
        )
        if len(tile_rows) != len(expected_tiles):
            raise ValueError("Tile SQLite index count does not match retrieval metadata.")
        for row in tile_rows:
            tile_id = str(row[0])
            tile = expected_tiles.get(tile_id)
            if tile is None:
                raise ValueError(f"Tile SQLite index contains unknown tile ID: {tile_id}")
            raw_tile = raw_tiles_by_id.get(tile_id)
            if not isinstance(raw_tile, dict):
                raise ValueError(f"Retrieval metadata is missing tile details for: {tile_id}")
            image_path = (root / str(row[1])).resolve()
            _under_root(image_path, root, f"tile image {tile_id}")
            if not image_path.is_file():
                raise ValueError(f"Tile SQLite index image is missing: {image_path}")
            if str(row[1]) != str(raw_tile.get("image_uri")):
                raise ValueError(f"Tile SQLite index image URI does not match retrieval metadata: {tile_id}")
            expected = (*raw_tile.get("center_enu_m", []), *raw_tile.get("bounds_enu_m", []), raw_tile.get("scale_m_per_px"))
            observed = tuple(float(value) for value in row[2:])
            if len(expected) != len(observed) or not all(
                isinstance(value, (int, float)) and math.isclose(float(actual), float(value), abs_tol=1e-8)
                for actual, value in zip(observed, expected)
            ):
                raise ValueError(f"Tile SQLite index geometry does not match retrieval metadata: {tile_id}")
        feature_rows = _sqlite_rows(feature_index_path, "tile_features", ("tile_id", "feature_uri", "sha256"))
        if len(feature_rows) != len(cache.tiles):
            raise ValueError("XFeat SQLite index count does not match local feature metadata.")
        expected_features = {(tile_id, tile.feature_uri, tile.sha256) for tile_id, tile in cache.tiles.items()}
        if {(str(row[0]), str(row[1]), str(row[2])) for row in feature_rows} != expected_features:
            raise ValueError("XFeat SQLite index does not match local feature metadata.")
        metrics["tile_count"] = len(expected_tiles)
        metrics["feature_tile_count"] = len(cache.tiles)
    except (OSError, ValueError, RuntimeError, json.JSONDecodeError) as exc:
        errors.append(f"{type(exc).__name__}:{exc}")
    return {
        "schema_version": MAP_ASSET_VALIDATION_SCHEMA,
        "generated_at_utc": _utc_now(),
        "status": "valid" if not errors else "invalid",
        "map_release_path": str(release_path),
        "map_release_id": release_id,
        "model_release_id": model_release_id,
        "index_format": index_format,
        "metrics": metrics,
        "errors": errors,
        "publication": {"px4_external_vision": "disabled", "shadow_only": True},
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Deep-validate learned retrieval, tile, and XFeat cache assets in a MapRelease v2.")
    parser.add_argument("--map-release", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    report = validate_learned_map_assets(args.map_release)
    rendered = json.dumps(report, indent=2, sort_keys=True)
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(rendered + "\n", encoding="utf-8")
    print(rendered)
    if report["status"] != "valid":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
