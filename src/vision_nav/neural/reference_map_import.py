"""Import a projected GeoTIFF into an immutable first-data reference map.

This deliberately accepts only already georeferenced projected imagery. It does
not infer location from a plain image or fetch imagery from the internet. The
result is a COG/STAC package plus ReferenceMapRelease suitable for calibration,
capture, and offline labels before a learned runtime model is available.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
import math
from pathlib import Path
from typing import Any

from .reference_map import create_reference_map, sha256_file


REFERENCE_MAP_IMPORT_SCHEMA = "vision_nav_reference_map_import_v1"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def _rasterio() -> Any:
    try:
        import rasterio
        from rasterio.shutil import copy as rio_copy
        from rasterio.warp import transform
    except ImportError as exc:
        raise RuntimeError("GeoTIFF reference-map import requires drone-vision-nav[map-production].") from exc
    return rasterio, rio_copy, transform


def _projected_meter_gsd(dataset: Any) -> float:
    crs = dataset.crs
    if crs is None:
        raise ValueError("Input GeoTIFF has no CRS.")
    if not crs.is_projected:
        raise ValueError("Input GeoTIFF must use a projected CRS with metre-scale pixels; georeference geographic imagery first.")
    axis_info = getattr(crs, "axis_info", None) or []
    unit_names = {str(getattr(axis, "unit_name", "")).lower() for axis in axis_info}
    if unit_names and not any("metre" in name or "meter" in name for name in unit_names):
        raise ValueError("Input GeoTIFF CRS units are not metres; supply an explicit --gsd-m-per-px after a documented conversion.")
    transform = dataset.transform
    x_scale = math.hypot(float(transform.a), float(transform.d))
    y_scale = math.hypot(float(transform.b), float(transform.e))
    if not math.isfinite(x_scale) or not math.isfinite(y_scale) or x_scale <= 0.0 or y_scale <= 0.0:
        raise ValueError("Input GeoTIFF has invalid affine pixel scale.")
    return (x_scale + y_scale) / 2.0


def _wgs84_geometry(dataset: Any, transform_coordinates: Any) -> tuple[list[float], dict[str, Any], tuple[float, float]]:
    bounds = dataset.bounds
    xs = [bounds.left, bounds.right, bounds.right, bounds.left, bounds.left]
    ys = [bounds.bottom, bounds.bottom, bounds.top, bounds.top, bounds.bottom]
    lons, lats = transform_coordinates(dataset.crs, "EPSG:4326", xs, ys)
    coordinates = [[float(lon), float(lat)] for lon, lat in zip(lons, lats)]
    bbox = [min(lons), min(lats), max(lons), max(lats)]
    center_x = (bounds.left + bounds.right) / 2.0
    center_y = (bounds.bottom + bounds.top) / 2.0
    center_lon, center_lat = transform_coordinates(dataset.crs, "EPSG:4326", [center_x], [center_y])
    return [float(value) for value in bbox], {"type": "Polygon", "coordinates": [coordinates]}, (float(center_lat[0]), float(center_lon[0]))


def import_reference_geotiff(
    source_path: str | Path,
    output_dir: str | Path,
    *,
    map_id: str,
    version: str,
    calibration_ids: list[str],
    origin_wgs84: tuple[float, float, float] | None = None,
    gsd_m_per_px: float | None = None,
) -> dict[str, Any]:
    """Create a COG/STAC/ReferenceMapRelease package from a projected GeoTIFF."""

    source = Path(source_path).expanduser().resolve()
    root = Path(output_dir).expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"Input GeoTIFF does not exist: {source}")
    if root.exists() and any(root.iterdir()):
        raise ValueError(f"Reference-map output directory must be empty: {root}")
    if not map_id or not version or not calibration_ids:
        raise ValueError("map_id, version, and at least one calibration ID are required.")
    rasterio, rio_copy, transform_coordinates = _rasterio()
    root.mkdir(parents=True, exist_ok=True)
    imagery_path = root / "imagery" / "map.tif"
    imagery_path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with rasterio.open(source) as dataset:
            if dataset.crs is None:
                raise ValueError("Input GeoTIFF has no CRS.")
            derived_gsd = _projected_meter_gsd(dataset)
            gsd = float(gsd_m_per_px) if gsd_m_per_px is not None else derived_gsd
            if not math.isfinite(gsd) or gsd <= 0.0:
                raise ValueError("gsd_m_per_px must be positive.")
            bbox, geometry, center_wgs84 = _wgs84_geometry(dataset, transform_coordinates)
            crs = dataset.crs.to_string()
            epsg = dataset.crs.to_epsg()
            shape = [dataset.height, dataset.width]
            affine = [
                float(dataset.transform.a), float(dataset.transform.b), float(dataset.transform.c),
                float(dataset.transform.d), float(dataset.transform.e), float(dataset.transform.f),
            ]
        # COG conversion is explicit so a copied ordinary GeoTIFF cannot be
        # mislabeled as cloud optimized in the STAC asset metadata.
        rio_copy(source, imagery_path, driver="COG", compress="DEFLATE", blocksize=512)
    except Exception as exc:
        if root.exists():
            # Keep an empty directory harmless, but do not leave a partial map
            # package that could be mistaken for valid field evidence.
            for path in sorted(root.rglob("*"), reverse=True):
                if path.is_file() or path.is_symlink():
                    path.unlink()
                elif path.is_dir():
                    path.rmdir()
            root.rmdir()
        if isinstance(exc, ValueError):
            raise
        raise RuntimeError(f"Could not convert input GeoTIFF to a COG: {exc}") from exc

    latitude, longitude = center_wgs84
    chosen_origin = origin_wgs84 or (latitude, longitude, 0.0)
    if len(chosen_origin) != 3 or not all(math.isfinite(float(value)) for value in chosen_origin):
        raise ValueError("origin_wgs84 must contain finite latitude, longitude, and altitude.")
    stac_path = root / "stac" / "item.json"
    stac_path.parent.mkdir(parents=True, exist_ok=True)
    stac = {
        "stac_version": "1.0.0",
        "type": "Feature",
        "id": f"{map_id}-{version}",
        "bbox": bbox,
        "geometry": geometry,
        "properties": {
            "datetime": None,
            "proj:shape": shape,
            "proj:transform": affine,
            **({"proj:epsg": int(epsg)} if epsg is not None else {"proj:wkt2": crs}),
            "vision_nav:gsd_m_per_px": gsd,
            "vision_nav:source_sha256": sha256_file(source),
        },
        "links": [],
        "assets": {
            "visual": {
                "href": "../imagery/map.tif",
                "type": "image/tiff; application=geotiff; profile=cloud-optimized",
                "roles": ["data", "visual"],
                "title": "Reference orthophoto COG",
            }
        },
    }
    stac_path.write_text(json.dumps(stac, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    reference_path = root / "reference_map.json"
    release = create_reference_map(
        reference_path,
        map_id=map_id,
        version=version,
        crs=crs,
        local_origin_enu=(0.0, 0.0, 0.0),
        geodetic_origin_wgs84=tuple(float(value) for value in chosen_origin),
        gsd_m_per_px=gsd,
        imagery_uri="imagery/map.tif",
        stac_item_uri="stac/item.json",
        calibration_ids=calibration_ids,
    )
    report = {
        "schema_version": REFERENCE_MAP_IMPORT_SCHEMA,
        "status": "valid",
        "created_utc": _utc_now(),
        "source_path": str(source),
        "source_sha256": sha256_file(source),
        "output_dir": str(root),
        "reference_map_path": str(reference_path),
        "reference_map_release_id": release["release_id"],
        "crs": crs,
        "gsd_m_per_px": gsd,
        "derived_gsd_m_per_px": derived_gsd,
        "wgs84_bbox": bbox,
        "origin_wgs84": release["geodetic_origin_wgs84"],
        "imagery": {"path": "imagery/map.tif", "sha256": sha256_file(imagery_path)},
        "stac": {"path": "stac/item.json", "sha256": sha256_file(stac_path)},
    }
    (root / "import_report.json").write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Convert a projected GeoTIFF into a COG/STAC ReferenceMapRelease package.")
    parser.add_argument("--input", required=True, type=Path, dest="source")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--map-id", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--calibration-id", action="append", required=True)
    parser.add_argument("--origin-wgs84", nargs=3, type=float, metavar=("LAT_DEG", "LON_DEG", "ALT_M"))
    parser.add_argument("--gsd-m-per-px", type=float, help="Override documented metre-per-pixel GSD after independent verification.")
    args = parser.parse_args()
    try:
        report = import_reference_geotiff(
            args.source, args.output_dir, map_id=args.map_id, version=args.version,
            calibration_ids=args.calibration_id,
            origin_wgs84=tuple(args.origin_wgs84) if args.origin_wgs84 else None,
            gsd_m_per_px=args.gsd_m_per_px,
        )
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
