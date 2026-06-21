from __future__ import annotations

from dataclasses import dataclass
import json
from pathlib import Path
import sqlite3
from typing import Iterable

import numpy as np

from vision_nav.georef import SimpleGeoReference


@dataclass(frozen=True)
class TerrainTile:
    tile_id: str
    row: int
    col: int
    x0_px: int
    y0_px: int
    x1_px: int
    y1_px: int
    min_east_m: float | None
    max_east_m: float | None
    min_north_m: float | None
    max_north_m: float | None
    image_path: Path
    descriptor_path: Path
    keypoint_count: int
    method: str

    @property
    def center_px(self) -> tuple[float, float]:
        return ((self.x0_px + self.x1_px) / 2.0, (self.y0_px + self.y1_px) / 2.0)

    @property
    def center_local_m(self) -> tuple[float, float] | None:
        if None in {self.min_east_m, self.max_east_m, self.min_north_m, self.max_north_m}:
            return None
        return (
            (float(self.min_east_m) + float(self.max_east_m)) / 2.0,
            (float(self.min_north_m) + float(self.max_north_m)) / 2.0,
        )


def create_tile_schema(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS tiles (
            tile_id TEXT PRIMARY KEY,
            row INTEGER NOT NULL,
            col INTEGER NOT NULL,
            x0_px INTEGER NOT NULL,
            y0_px INTEGER NOT NULL,
            x1_px INTEGER NOT NULL,
            y1_px INTEGER NOT NULL,
            min_east_m REAL,
            max_east_m REAL,
            min_north_m REAL,
            max_north_m REAL,
            image_path TEXT NOT NULL,
            descriptor_path TEXT NOT NULL,
            keypoint_count INTEGER NOT NULL,
            method TEXT NOT NULL
        )
        """
    )
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tiles_pixel_bounds ON tiles(x0_px, y0_px, x1_px, y1_px)")
    conn.execute("CREATE INDEX IF NOT EXISTS idx_tiles_local_bounds ON tiles(min_east_m, max_east_m, min_north_m, max_north_m)")


def tile_origins(length_px: int, tile_size_px: int, overlap_px: int) -> list[int]:
    if tile_size_px <= 0:
        raise ValueError("tile_size_px must be greater than zero")
    if overlap_px < 0 or overlap_px >= tile_size_px:
        raise ValueError("overlap_px must be non-negative and smaller than tile_size_px")
    if length_px <= tile_size_px:
        return [0]
    stride = tile_size_px - overlap_px
    starts = list(range(0, max(length_px - tile_size_px + 1, 1), stride))
    last = length_px - tile_size_px
    if starts[-1] != last:
        starts.append(last)
    return starts


def local_bounds_for_tile(georef: SimpleGeoReference | None, x0: int, y0: int, x1: int, y1: int) -> tuple[float | None, ...]:
    if georef is None:
        return (None, None, None, None)
    corners = [
        georef.pixel_to_local_m(x0, y0),
        georef.pixel_to_local_m(x1, y0),
        georef.pixel_to_local_m(x1, y1),
        georef.pixel_to_local_m(x0, y1),
    ]
    east = [value[0] for value in corners]
    north = [value[1] for value in corners]
    return (min(east), max(east), min(north), max(north))


def save_tile_descriptor(
    output_path: Path,
    *,
    tile_id: str,
    image_path: str,
    image_shape: tuple[int, int],
    method: str,
    keypoints_xy: np.ndarray,
    descriptors: np.ndarray,
    offset_xy_px: tuple[int, int],
) -> None:
    np.savez_compressed(
        output_path,
        tile_id=np.array(tile_id),
        image_path=np.array(image_path),
        image_shape=np.array(image_shape, dtype=np.int32),
        method=np.array(method),
        keypoints_xy=keypoints_xy.astype(np.float32),
        descriptors=descriptors,
        offset_xy_px=np.array(offset_xy_px, dtype=np.int32),
    )


def load_tile_descriptor(path: Path) -> dict:
    with np.load(path, allow_pickle=False) as data:
        return {
            "tile_id": str(data["tile_id"]),
            "image_path": str(data["image_path"]),
            "image_shape": tuple(int(value) for value in data["image_shape"]),
            "method": str(data["method"]),
            "keypoints_xy": data["keypoints_xy"].astype(np.float32),
            "descriptors": data["descriptors"],
            "offset_xy_px": tuple(int(value) for value in data["offset_xy_px"]),
        }


def build_tile_index(
    *,
    bundle_dir: Path,
    map_image: Path,
    index_path: Path,
    tiles_dir: Path,
    descriptors_dir: Path,
    georef: SimpleGeoReference | None,
    method: str,
    max_features: int,
    tile_size_px: int,
    overlap_px: int,
) -> dict[str, int | str]:
    import cv2
    from vision_nav.features import extract_features, load_gray_image

    image = load_gray_image(str(map_image))
    height, width = image.shape[:2]
    row_starts = tile_origins(height, tile_size_px, overlap_px)
    col_starts = tile_origins(width, tile_size_px, overlap_px)
    tiles_dir.mkdir(parents=True, exist_ok=True)
    descriptors_dir.mkdir(parents=True, exist_ok=True)
    index_path.parent.mkdir(parents=True, exist_ok=True)

    if index_path.exists():
        index_path.unlink()

    total_keypoints = 0
    tile_count = 0
    with sqlite3.connect(index_path) as conn:
        create_tile_schema(conn)
        for row, y0 in enumerate(row_starts):
            for col, x0 in enumerate(col_starts):
                tile_id = f"tile_{tile_count:06d}"
                x1 = min(x0 + tile_size_px, width)
                y1 = min(y0 + tile_size_px, height)
                tile = image[y0:y1, x0:x1]
                features = extract_features(tile, method=method, max_features=max_features)
                tile_image_path = tiles_dir / f"{tile_id}.png"
                descriptor_path = descriptors_dir / f"{tile_id}.npz"
                cv2.imwrite(str(tile_image_path), tile)
                save_tile_descriptor(
                    descriptor_path,
                    tile_id=tile_id,
                    image_path=str(tile_image_path.relative_to(bundle_dir)),
                    image_shape=tile.shape[:2],
                    method=method,
                    keypoints_xy=features.keypoints_xy,
                    descriptors=features.descriptors,
                    offset_xy_px=(x0, y0),
                )
                min_east, max_east, min_north, max_north = local_bounds_for_tile(georef, x0, y0, x1, y1)
                keypoint_count = int(features.keypoints_xy.shape[0])
                total_keypoints += keypoint_count
                conn.execute(
                    """
                    INSERT INTO tiles (
                        tile_id, row, col, x0_px, y0_px, x1_px, y1_px,
                        min_east_m, max_east_m, min_north_m, max_north_m,
                        image_path, descriptor_path, keypoint_count, method
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tile_id,
                        row,
                        col,
                        x0,
                        y0,
                        x1,
                        y1,
                        min_east,
                        max_east,
                        min_north,
                        max_north,
                        str(tile_image_path.relative_to(bundle_dir)),
                        str(descriptor_path.relative_to(bundle_dir)),
                        keypoint_count,
                        method,
                    ),
                )
                tile_count += 1
        conn.commit()

    sidecar = {
        "tile_count": tile_count,
        "feature_count": total_keypoints,
        "method": method,
        "tile_size_px": tile_size_px,
        "overlap_px": overlap_px,
        "image_shape": [height, width],
    }
    index_path.with_suffix(".json").write_text(json.dumps(sidecar, indent=2) + "\n")
    return {
        "tile_count": tile_count,
        "feature_count": total_keypoints,
        "method": method,
        "tile_index_path": str(index_path),
    }


def _row_to_tile(bundle_dir: Path, row: sqlite3.Row) -> TerrainTile:
    return TerrainTile(
        tile_id=str(row["tile_id"]),
        row=int(row["row"]),
        col=int(row["col"]),
        x0_px=int(row["x0_px"]),
        y0_px=int(row["y0_px"]),
        x1_px=int(row["x1_px"]),
        y1_px=int(row["y1_px"]),
        min_east_m=row["min_east_m"],
        max_east_m=row["max_east_m"],
        min_north_m=row["min_north_m"],
        max_north_m=row["max_north_m"],
        image_path=bundle_dir / str(row["image_path"]),
        descriptor_path=bundle_dir / str(row["descriptor_path"]),
        keypoint_count=int(row["keypoint_count"]),
        method=str(row["method"]),
    )


def load_all_tiles(index_path: Path, bundle_dir: Path) -> list[TerrainTile]:
    with sqlite3.connect(index_path) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute("SELECT * FROM tiles ORDER BY tile_id").fetchall()
    return [_row_to_tile(bundle_dir, row) for row in rows]


def query_tiles(
    index_path: Path,
    bundle_dir: Path,
    *,
    prior_east_m: float | None = None,
    prior_north_m: float | None = None,
    search_radius_m: float | None = None,
    max_candidates: int = 64,
) -> list[TerrainTile]:
    tiles = load_all_tiles(index_path, bundle_dir)
    if prior_east_m is None or prior_north_m is None or search_radius_m is None:
        return sorted(tiles, key=lambda tile: (-tile.keypoint_count, tile.tile_id))[:max_candidates]

    radius = max(float(search_radius_m), 0.0)
    candidates: list[tuple[float, TerrainTile]] = []
    for tile in tiles:
        if None in {tile.min_east_m, tile.max_east_m, tile.min_north_m, tile.max_north_m}:
            continue
        if (
            float(tile.max_east_m) < prior_east_m - radius
            or float(tile.min_east_m) > prior_east_m + radius
            or float(tile.max_north_m) < prior_north_m - radius
            or float(tile.min_north_m) > prior_north_m + radius
        ):
            continue
        center = tile.center_local_m
        distance = 0.0 if center is None else float(np.hypot(center[0] - prior_east_m, center[1] - prior_north_m))
        candidates.append((distance, tile))
    return [tile for _, tile in sorted(candidates, key=lambda item: (item[0], -item[1].keypoint_count))[:max_candidates]]


def iter_tiles(index_path: Path, bundle_dir: Path) -> Iterable[TerrainTile]:
    yield from load_all_tiles(index_path, bundle_dir)
