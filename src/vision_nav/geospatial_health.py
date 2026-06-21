from __future__ import annotations

import argparse
import json
from pathlib import Path
import sqlite3
import struct
from statistics import median
from typing import Any

from vision_nav.terrain_bundle import TerrainBundle, load_terrain_bundle


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
TIFF_TAGS = {
    256: "image_width",
    257: "image_length",
    259: "compression",
    273: "strip_offsets",
    322: "tile_width",
    323: "tile_length",
    324: "tile_offsets",
    33550: "model_pixel_scale",
    33922: "model_tiepoint",
    34264: "model_transformation",
    34735: "geokey_directory",
    34736: "geodouble_params",
    34737: "geoascii_params",
}


def _add_issue(issues: list[dict[str, str]], severity: str, message: str) -> None:
    issues.append({"severity": severity, "message": message})


def _status_from_issues(issues: list[dict[str, str]]) -> str:
    if any(issue["severity"] == "error" for issue in issues):
        return "failed"
    if any(issue["severity"] == "warning" for issue in issues):
        return "degraded"
    return "passed"


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path)


def _png_size(path: Path) -> tuple[int, int] | None:
    with path.open("rb") as handle:
        header = handle.read(24)
    if len(header) < 24 or not header.startswith(PNG_SIGNATURE) or header[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", header[16:24])


def _read_tiff_ifds(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if len(raw) < 8:
        raise ValueError("TIFF header is too short")

    byte_order = raw[:2]
    if byte_order == b"II":
        endian = "<"
    elif byte_order == b"MM":
        endian = ">"
    else:
        raise ValueError("TIFF byte order marker is missing")

    version = struct.unpack(endian + "H", raw[2:4])[0]
    if version != 42:
        raise ValueError("Only classic TIFF headers are supported by the lightweight validator")

    first_ifd_offset = struct.unpack(endian + "I", raw[4:8])[0]
    ifds: list[dict[str, Any]] = []
    offset = first_ifd_offset
    visited: set[int] = set()
    while offset and offset not in visited and offset + 2 <= len(raw):
        visited.add(offset)
        entry_count = struct.unpack(endian + "H", raw[offset : offset + 2])[0]
        cursor = offset + 2
        tags: dict[str, Any] = {}
        present_tag_ids: list[int] = []
        for _ in range(entry_count):
            if cursor + 12 > len(raw):
                break
            tag, tag_type, count, value_or_offset = struct.unpack(endian + "HHII", raw[cursor : cursor + 12])
            present_tag_ids.append(tag)
            name = TIFF_TAGS.get(tag)
            if name:
                tags[name] = _decode_tiff_inline_value(raw, endian, tag_type, count, value_or_offset)
            cursor += 12
        if cursor + 4 > len(raw):
            break
        offset = struct.unpack(endian + "I", raw[cursor : cursor + 4])[0]
        ifds.append({"tags": tags, "tag_ids": sorted(present_tag_ids)})

    if not ifds:
        raise ValueError("No TIFF image file directories found")
    return {
        "format": "TIFF",
        "byte_order": "little" if endian == "<" else "big",
        "ifd_count": len(ifds),
        "first_ifd": ifds[0],
    }


def _decode_tiff_inline_value(raw: bytes, endian: str, tag_type: int, count: int, value_or_offset: int) -> Any:
    type_sizes = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 12: 8}
    size = type_sizes.get(tag_type)
    if size is None:
        return {"type": tag_type, "count": count}

    byte_count = size * count
    if byte_count <= 4:
        data = struct.pack(endian + "I", value_or_offset)[:byte_count]
    else:
        if value_or_offset + byte_count > len(raw):
            return {"type": tag_type, "count": count, "offset": value_or_offset}
        data = raw[value_or_offset : value_or_offset + byte_count]

    if tag_type == 2:
        return data.rstrip(b"\x00").decode("ascii", errors="replace")
    if tag_type == 3:
        values = list(struct.unpack(endian + f"{count}H", data))
    elif tag_type == 4:
        values = list(struct.unpack(endian + f"{count}I", data))
    elif tag_type == 1:
        values = list(data)
    else:
        return {"type": tag_type, "count": count}
    return values[0] if len(values) == 1 else values


def raster_metadata(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    if suffix == ".png":
        size = _png_size(path)
        return {
            "format": "PNG",
            "width_px": size[0] if size else None,
            "height_px": size[1] if size else None,
            "metadata_readable": size is not None,
            "cog": {"status": "not_applicable", "reason": "PNG is not a Cloud Optimized GeoTIFF."},
        }
    if suffix in {".tif", ".tiff"}:
        tiff = _read_tiff_ifds(path)
        tags = tiff["first_ifd"]["tags"]
        has_tiling = "tile_width" in tags and "tile_length" in tags and "tile_offsets" in tags
        has_overviews = int(tiff["ifd_count"]) > 1
        has_geotiff = any(
            key in tags
            for key in (
                "model_pixel_scale",
                "model_tiepoint",
                "model_transformation",
                "geokey_directory",
            )
        )
        reasons: list[str] = []
        if not has_tiling:
            reasons.append("not tiled")
        if not has_overviews:
            reasons.append("no internal overviews")
        if not has_geotiff:
            reasons.append("no GeoTIFF georeference tags")
        return {
            "format": "TIFF",
            "width_px": tags.get("image_width"),
            "height_px": tags.get("image_length"),
            "metadata_readable": True,
            "ifd_count": tiff["ifd_count"],
            "tiff_tags": tags,
            "cog": {
                "status": "passed" if not reasons else "degraded",
                "candidate": not reasons,
                "tiled": has_tiling,
                "has_overviews": has_overviews,
                "has_geotiff_tags": has_geotiff,
                "reasons": reasons,
            },
        }
    return {
        "format": suffix.lstrip(".").upper() or "unknown",
        "width_px": None,
        "height_px": None,
        "metadata_readable": False,
        "cog": {"status": "not_applicable", "reason": "Unsupported raster header for lightweight inspection."},
    }


def georef_bounds(bundle: TerrainBundle, raster: dict[str, Any]) -> dict[str, Any] | None:
    if bundle.georef is None:
        return None
    width = raster.get("width_px")
    height = raster.get("height_px")
    source_region = bundle.manifest.get("source_region") or {}
    width = int(width or source_region.get("width_px") or 0)
    height = int(height or source_region.get("height_px") or 0)
    if width <= 0 or height <= 0:
        return None
    corners = [
        bundle.georef.pixel_to_latlon(0, 0),
        bundle.georef.pixel_to_latlon(width, 0),
        bundle.georef.pixel_to_latlon(width, height),
        bundle.georef.pixel_to_latlon(0, height),
    ]
    lats = [lat for lat, _ in corners]
    lons = [lon for _, lon in corners]
    local_corners = [
        bundle.georef.pixel_to_local_m(0, 0),
        bundle.georef.pixel_to_local_m(width, 0),
        bundle.georef.pixel_to_local_m(width, height),
        bundle.georef.pixel_to_local_m(0, height),
    ]
    east = [value[0] for value in local_corners]
    north = [value[1] for value in local_corners]
    return {
        "lat_min": min(lats),
        "lat_max": max(lats),
        "lon_min": min(lons),
        "lon_max": max(lons),
        "east_min_m": min(east),
        "east_max_m": max(east),
        "north_min_m": min(north),
        "north_max_m": max(north),
        "width_m": max(east) - min(east),
        "height_m": max(north) - min(north),
    }


def stac_health(bundle: TerrainBundle) -> dict[str, Any]:
    path = bundle.bundle_dir / "manifest.stac.json"
    issues: list[dict[str, str]] = []
    if not path.exists():
        _add_issue(issues, "warning", "Missing manifest.stac.json.")
        return {"path": str(path), "exists": False, "issues": issues, "status": _status_from_issues(issues)}

    try:
        raw = json.loads(path.read_text())
    except Exception as exc:
        _add_issue(issues, "error", f"STAC manifest is not valid JSON: {exc}")
        return {"path": str(path), "exists": True, "issues": issues, "status": _status_from_issues(issues)}

    if raw.get("stac_version") != "1.0.0":
        _add_issue(issues, "warning", "STAC manifest should use stac_version 1.0.0.")
    if raw.get("type") != "Feature":
        _add_issue(issues, "error", "STAC manifest type must be Feature.")
    assets = raw.get("assets")
    if not isinstance(assets, dict) or not assets:
        _add_issue(issues, "error", "STAC manifest has no assets.")
        assets = {}
    for name, asset in assets.items():
        href = asset.get("href") if isinstance(asset, dict) else None
        if not href:
            _add_issue(issues, "error", f"STAC asset {name} has no href.")
            continue
        asset_path = bundle.bundle_dir / href
        if not asset_path.exists():
            _add_issue(issues, "error", f"STAC asset {name} is missing: {href}")
    return {
        "path": str(path),
        "exists": True,
        "asset_count": len(assets),
        "issues": issues,
        "status": _status_from_issues(issues),
    }


def tile_index_health(bundle: TerrainBundle) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    if bundle.tile_index_path is None:
        _add_issue(issues, "warning", "Manifest has no terrain_bundle.tile_index_path.")
        return {"path": None, "exists": False, "issues": issues, "status": _status_from_issues(issues)}
    if not bundle.tile_index_path.exists():
        _add_issue(issues, "error", f"Missing terrain tile index: {bundle.tile_index_path}")
        return {"path": str(bundle.tile_index_path), "exists": False, "issues": issues, "status": _status_from_issues(issues)}

    with sqlite3.connect(bundle.tile_index_path) as conn:
        conn.row_factory = sqlite3.Row
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='tiles'"
        ).fetchone()
        if table is None:
            _add_issue(issues, "error", "Tile index has no tiles table.")
            return {"path": str(bundle.tile_index_path), "exists": True, "issues": issues, "status": _status_from_issues(issues)}
        row = conn.execute(
            """
            SELECT
              COUNT(*) AS tile_count,
              COALESCE(SUM(keypoint_count), 0) AS feature_count,
              MIN(min_east_m) AS min_east_m,
              MAX(max_east_m) AS max_east_m,
              MIN(min_north_m) AS min_north_m,
              MAX(max_north_m) AS max_north_m
            FROM tiles
            """
        ).fetchone()
        methods = [str(value[0]) for value in conn.execute("SELECT DISTINCT method FROM tiles ORDER BY method").fetchall()]
        tile_rows = conn.execute(
            """
            SELECT tile_id, x0_px, y0_px, x1_px, y1_px, image_path, descriptor_path, keypoint_count
            FROM tiles
            ORDER BY tile_id
            """
        ).fetchall()

    missing_assets: list[str] = []
    for asset_row in tile_rows:
        for key in ("image_path", "descriptor_path"):
            rel = str(asset_row[key])
            if not (bundle.bundle_dir / rel).exists():
                missing_assets.append(rel)
                if len(missing_assets) >= 5:
                    break
        if len(missing_assets) >= 5:
            break
    if missing_assets:
        _add_issue(issues, "error", f"Tile index references missing assets: {', '.join(missing_assets)}")

    tile_count = int(row["tile_count"] or 0)
    if tile_count <= 0:
        _add_issue(issues, "error", "Tile index has no tiles.")
    feature_count = int(row["feature_count"] or 0)
    if feature_count <= 0:
        _add_issue(issues, "warning", "Tile index has no extracted features.")
    quality = map_quality_from_tiles(tile_rows, tile_count=tile_count, feature_count=feature_count)
    if quality["low_texture_ratio"] > 0.25:
        _add_issue(
            issues,
            "warning",
            f"{quality['low_texture_tile_count']} of {tile_count} tiles have low feature density.",
        )

    return {
        "path": str(bundle.tile_index_path),
        "exists": True,
        "tile_count": tile_count,
        "feature_count": feature_count,
        "methods": methods,
        "local_bounds_m": {
            "east_min": row["min_east_m"],
            "east_max": row["max_east_m"],
            "north_min": row["min_north_m"],
            "north_max": row["max_north_m"],
        },
        "quality": quality,
        "missing_assets": missing_assets,
        "issues": issues,
        "status": _status_from_issues(issues),
    }


def map_quality_from_tiles(tile_rows: list[sqlite3.Row], *, tile_count: int, feature_count: int) -> dict[str, Any]:
    density_threshold_per_mpx = 150.0
    densities: list[float] = []
    low_texture_tiles: list[str] = []
    for row in tile_rows:
        width_px = max(int(row["x1_px"]) - int(row["x0_px"]), 1)
        height_px = max(int(row["y1_px"]) - int(row["y0_px"]), 1)
        area_mpx = (width_px * height_px) / 1_000_000.0
        density = float(row["keypoint_count"]) / max(area_mpx, 1e-9)
        densities.append(density)
        if density < density_threshold_per_mpx:
            low_texture_tiles.append(str(row["tile_id"]))

    if not densities:
        density_summary = {"min": 0.0, "median": 0.0, "mean": 0.0, "max": 0.0}
    else:
        density_summary = {
            "min": min(densities),
            "median": median(densities),
            "mean": sum(densities) / len(densities),
            "max": max(densities),
        }

    if tile_count <= 64 and feature_count <= 100_000:
        runtime_cost = "low"
    elif tile_count <= 256 and feature_count <= 400_000:
        runtime_cost = "moderate"
    else:
        runtime_cost = "high"

    return {
        "feature_density_per_mpx": density_summary,
        "low_texture_threshold_per_mpx": density_threshold_per_mpx,
        "low_texture_tile_count": len(low_texture_tiles),
        "low_texture_ratio": (len(low_texture_tiles) / tile_count) if tile_count else 1.0,
        "low_texture_tile_ids_sample": low_texture_tiles[:10],
        "estimated_pi_runtime_cost": runtime_cost,
    }


def geospatial_health_report(bundle_path: str | Path) -> dict[str, Any]:
    bundle = load_terrain_bundle(bundle_path)
    issues: list[dict[str, str]] = []

    if not bundle.orthophoto_path.exists():
        _add_issue(issues, "error", f"Missing orthophoto: {bundle.orthophoto_path}")
        raster = {"format": "missing", "metadata_readable": False}
    else:
        try:
            raster = raster_metadata(bundle.orthophoto_path)
        except Exception as exc:
            raster = {"format": bundle.orthophoto_path.suffix.lstrip(".").upper(), "metadata_readable": False, "error": str(exc)}
            _add_issue(issues, "warning", f"Could not inspect raster metadata: {exc}")

    if bundle.georef is None:
        _add_issue(issues, "error", "Missing georeference; terrain map upload should be blocked.")
    else:
        if bundle.georef.gsd_m <= 0:
            _add_issue(issues, "error", f"Invalid GSD: {bundle.georef.gsd_m}")
        if not bundle.crs:
            _add_issue(issues, "warning", "CRS is missing; prefer EPSG-tagged GeoTIFF/COG or STAC metadata.")
        if bundle.georef.confidence < 0.7:
            _add_issue(issues, "warning", f"Low georeference confidence: {bundle.georef.confidence:.2f}")

    cog = raster.get("cog") or {}
    if cog.get("status") == "degraded":
        _add_issue(issues, "warning", f"GeoTIFF is not COG-ready: {', '.join(cog.get('reasons') or [])}")

    stac = stac_health(bundle)
    tile_index = tile_index_health(bundle)
    for child in (stac, tile_index):
        for issue in child.get("issues", []):
            issues.append(issue)

    report = {
        "bundle_dir": str(bundle.bundle_dir),
        "bundle_id": bundle.manifest.get("bundle_id"),
        "orthophoto_path": str(bundle.orthophoto_path),
        "raster": raster,
        "georef": {
            "source": bundle.georef.source if bundle.georef else None,
            "confidence": bundle.georef.confidence if bundle.georef else None,
            "crs": bundle.crs,
            "gsd_m": bundle.gsd_m,
            "local_origin": (bundle.manifest.get("terrain_bundle") or {}).get("local_origin"),
            "bounds": georef_bounds(bundle, raster),
        },
        "stac": stac,
        "tile_index": tile_index,
        "map_quality": tile_index.get("quality"),
        "issues": issues,
        "status": _status_from_issues(issues),
    }
    return report


def write_geospatial_health_report(bundle_path: str | Path, output_path: str | Path | None = None) -> dict[str, Any]:
    report = geospatial_health_report(bundle_path)
    bundle = load_terrain_bundle(bundle_path)
    path = Path(output_path) if output_path else bundle.bundle_dir / "bundle_health.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    report["path"] = str(path)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect terrain bundle geospatial health.")
    parser.add_argument("--bundle", required=True, help="Bundle directory or manifest.json path.")
    parser.add_argument("--write", action="store_true", help="Write bundle_health.json.")
    parser.add_argument("--output", help="Custom output path for --write.")
    parser.add_argument("--json", action="store_true", help="Emit JSON only.")
    return parser.parse_args()


def print_human(report: dict[str, Any]) -> None:
    print(f"Geospatial health: {report.get('bundle_id') or '(unnamed)'}")
    print(f"Status: {report['status']}")
    print(f"Raster: {report['raster'].get('format')} {report['raster'].get('width_px')}x{report['raster'].get('height_px')}")
    print(f"CRS: {report['georef'].get('crs') or '(missing)'}")
    print(f"GSD: {report['georef'].get('gsd_m')}")
    print(f"Tiles: {report['tile_index'].get('tile_count') or 0}")
    print(f"Features: {report['tile_index'].get('feature_count') or 0}")
    for issue in report["issues"]:
        print(f"[{issue['severity'].upper()}] {issue['message']}")


def main() -> None:
    args = parse_args()
    report = write_geospatial_health_report(args.bundle, args.output) if args.write else geospatial_health_report(args.bundle)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human(report)
    if report["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
