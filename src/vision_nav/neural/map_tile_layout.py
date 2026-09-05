"""Export georeferenced COG tiles for learned map production and training."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from vision_nav.training_capture import sha256_file

from .reference_map import load_reference_map
from .training_labels import geodetic_to_enu


MAP_TILE_LAYOUT_SCHEMA = "vision_nav_map_tile_layout_v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _dependencies() -> tuple[Any, Any, Any]:
    try:
        import rasterio
        from rasterio.windows import Window, bounds
        from pyproj import Transformer
    except ImportError as exc:
        raise RuntimeError(
            "COG tiling requires drone-vision-nav[map-production] (rasterio and pyproj)."
        ) from exc
    return rasterio, Window, (bounds, Transformer)


def _as_uint8(image: np.ndarray) -> np.ndarray:
    if image.dtype == np.uint8:
        return image
    finite = image[np.isfinite(image)]
    if finite.size == 0:
        return np.zeros(image.shape, dtype=np.uint8)
    low, high = float(np.percentile(finite, 1.0)), float(np.percentile(finite, 99.0))
    if high <= low:
        high = low + 1.0
    scaled = np.clip((image.astype(np.float32) - low) * (255.0 / (high - low)), 0.0, 255.0)
    return scaled.astype(np.uint8)


def _rgb_for_png(data: np.ndarray) -> np.ndarray:
    """Convert rasterio BxHxW data to OpenCV BGR/gray image for PNG encoding."""

    if data.ndim != 3:
        raise ValueError("COG tile data must have band, height, and width dimensions.")
    data = _as_uint8(data)
    if data.shape[0] == 1:
        return data[0]
    if data.shape[0] == 2:
        return cv2.merge((data[0], data[1], np.zeros_like(data[0])))
    # Raster datasets conventionally expose RGB bands. OpenCV writes BGR.
    return np.moveaxis(data[:3], 0, -1)[..., ::-1]


def _valid_fraction(data: np.ndarray, nodata: float | int | None) -> float:
    valid = np.all(np.isfinite(data), axis=0)
    if nodata is not None:
        valid &= np.any(data != nodata, axis=0)
    return float(np.count_nonzero(valid)) / float(valid.size) if valid.size else 0.0


def _enu_bounds(
    raster_bounds: tuple[float, float, float, float],
    *,
    transformer: Any,
    origin: dict[str, float],
) -> tuple[list[float], list[float]]:
    left, bottom, right, top = raster_bounds
    corners = [(left, bottom), (left, top), (right, bottom), (right, top)]
    enu_points = []
    for x, y in corners:
        lon, lat = transformer.transform(x, y)
        enu_points.append(
            geodetic_to_enu(
                float(lat), float(lon), origin["altitude_m"],
                origin_lat_deg=origin["lat_deg"], origin_lon_deg=origin["lon_deg"], origin_altitude_m=origin["altitude_m"],
            )
        )
    east = [point[0] for point in enu_points]
    north = [point[1] for point in enu_points]
    return [min(east), min(north), max(east), max(north)], [sum(east) / len(east), sum(north) / len(north), sum(point[2] for point in enu_points) / len(enu_points)]


def build_map_tile_layout(
    cog_path: str | Path,
    output_dir: str | Path,
    *,
    origin_lat_deg: float,
    origin_lon_deg: float,
    origin_altitude_m: float,
    tile_size_px: int = 512,
    stride_px: int | None = None,
    min_valid_fraction: float = 0.98,
    allow_partial_tiles: bool = False,
) -> dict[str, Any]:
    """Write PNG tile crops and an ENU layout from a north-up georeferenced COG."""

    if tile_size_px <= 0:
        raise ValueError("tile_size_px must be positive.")
    stride_px = stride_px or tile_size_px
    if stride_px <= 0 or not 0.0 <= min_valid_fraction <= 1.0:
        raise ValueError("stride_px must be positive and min_valid_fraction must be in [0, 1].")
    if not -90.0 <= origin_lat_deg <= 90.0 or not -180.0 <= origin_lon_deg <= 180.0:
        raise ValueError("Map tile origin latitude/longitude is outside WGS84 bounds.")
    cog = Path(cog_path).expanduser().resolve()
    if not cog.is_file():
        raise ValueError(f"COG does not exist: {cog}")
    root = Path(output_dir).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError(f"Map tile layout output directory must be empty: {root}")
    rasterio, Window, helpers = _dependencies()
    window_bounds, Transformer = helpers
    origin = {"lat_deg": origin_lat_deg, "lon_deg": origin_lon_deg, "altitude_m": origin_altitude_m}
    root.mkdir(parents=True, exist_ok=True)
    image_root = root / "imagery" / "tiles"
    image_root.mkdir(parents=True)
    tiles: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    with rasterio.open(cog) as source:
        if source.crs is None:
            raise ValueError("COG has no CRS; cannot derive map-tile ENU bounds.")
        transformer = Transformer.from_crs(source.crs, "EPSG:4326", always_xy=True)
        tile_number = 0
        for row in range(0, source.height, stride_px):
            for column in range(0, source.width, stride_px):
                width = min(tile_size_px, source.width - column)
                height = min(tile_size_px, source.height - row)
                if not allow_partial_tiles and (width != tile_size_px or height != tile_size_px):
                    continue
                window = Window(column, row, width, height)
                data = source.read(window=window)
                valid_fraction = _valid_fraction(data, source.nodata)
                tile_id = f"tile_{tile_number:06d}"
                tile_number += 1
                if valid_fraction < min_valid_fraction:
                    skipped.append({"tile_id": tile_id, "row": row, "column": column, "reason": "insufficient_valid_pixels", "valid_fraction": round(valid_fraction, 5)})
                    continue
                image_path = image_root / f"{tile_id}.png"
                if not cv2.imwrite(str(image_path), _rgb_for_png(data)):
                    raise RuntimeError(f"Could not write tile image: {image_path}")
                bounds_enu_m, center_enu_m = _enu_bounds(
                    window_bounds(window, source.transform), transformer=transformer, origin=origin
                )
                tiles.append(
                    {
                        "tile_id": tile_id,
                        "image_uri": str(image_path.relative_to(root)).replace("\\", "/"),
                        "image_sha256": sha256_file(image_path),
                        "pixel_geometry": {
                            "schema_version": "vision_nav_tile_pixel_geometry_v1",
                            "crs": source.crs.to_string(),
                            "affine": list(source.window_transform(window))[:6],
                            "image_size": [width, height],
                            "geodetic_origin_wgs84": origin,
                        },
                        "bounds_enu_m": [round(value, 5) for value in bounds_enu_m],
                        "center_enu_m": [round(value, 5) for value in center_enu_m],
                        "source_window_px": {"column": column, "row": row, "width": width, "height": height},
                        "valid_fraction": round(valid_fraction, 5),
                    }
                )
    if not tiles:
        raise ValueError("No valid COG tiles were exported. Review tile size, nodata, and validity threshold.")
    layout = {
        "schema_version": MAP_TILE_LAYOUT_SCHEMA,
        "created_utc": _utc_now(),
        "cog": {"path": str(cog), "sha256": sha256_file(cog)},
        "geodetic_origin_wgs84": origin,
        "tile_policy": {"tile_size_px": tile_size_px, "stride_px": stride_px, "min_valid_fraction": min_valid_fraction, "allow_partial_tiles": allow_partial_tiles},
        "tile_count": len(tiles),
        "skipped_tile_count": len(skipped),
        "tiles": tiles,
        "skipped": skipped,
    }
    layout_path = root / "tile_layout.json"
    layout_path.write_text(json.dumps(layout, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"output_dir": str(root), "layout_path": str(layout_path), "layout": layout}


def main() -> None:
    parser = argparse.ArgumentParser(description="Export COG tile crops and ENU layout for learned map production.")
    parser.add_argument("--cog", type=Path)
    parser.add_argument("--reference-map", type=Path, help="Derive COG and ENU origin from a ReferenceMapRelease.")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--origin-lat-deg", type=float)
    parser.add_argument("--origin-lon-deg", type=float)
    parser.add_argument("--origin-altitude-m", type=float)
    parser.add_argument("--tile-size-px", type=int, default=512)
    parser.add_argument("--stride-px", type=int)
    parser.add_argument("--min-valid-fraction", type=float, default=0.98)
    parser.add_argument("--allow-partial-tiles", action="store_true")
    args = parser.parse_args()
    try:
        if args.reference_map is not None:
            if args.cog is not None or any(value is not None for value in (args.origin_lat_deg, args.origin_lon_deg, args.origin_altitude_m)):
                raise ValueError("Use either --reference-map or --cog with all three explicit origin fields.")
            reference_path = args.reference_map.expanduser().resolve()
            reference = load_reference_map(reference_path)
            origin = reference["geodetic_origin_wgs84"]
            cog = reference_path.parent / str(reference["imagery_uri"])
            origin_lat_deg = float(origin["lat_deg"])
            origin_lon_deg = float(origin["lon_deg"])
            origin_altitude_m = float(origin["altitude_m"])
        else:
            if args.cog is None or not all(value is not None for value in (args.origin_lat_deg, args.origin_lon_deg, args.origin_altitude_m)):
                raise ValueError("Set --reference-map, or set --cog with --origin-lat-deg, --origin-lon-deg, and --origin-altitude-m.")
            cog = args.cog
            origin_lat_deg = args.origin_lat_deg
            origin_lon_deg = args.origin_lon_deg
            origin_altitude_m = args.origin_altitude_m
        result = build_map_tile_layout(
            cog, args.output_dir,
            origin_lat_deg=origin_lat_deg, origin_lon_deg=origin_lon_deg, origin_altitude_m=origin_altitude_m,
            tile_size_px=args.tile_size_px, stride_px=args.stride_px,
            min_valid_fraction=args.min_valid_fraction, allow_partial_tiles=args.allow_partial_tiles,
        )
    except (ValueError, RuntimeError) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result["layout"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
