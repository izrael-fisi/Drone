from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
from typing import Any

from vision_nav.bundle import load_manifest, manifest_georef, manifest_orthophoto_path, resolve_bundle_path
from vision_nav.georef import SimpleGeoReference

TERRAIN_BUNDLE_VERSION = "0.1.0"
DEFAULT_TERRAIN_TILE_INDEX = "index/tiles.sqlite"
DEFAULT_TERRAIN_TILE_SIZE_PX = 512
DEFAULT_TERRAIN_OVERLAP_PX = 64
ELEVATION_ASSET_CANDIDATES = {
    "dem": ("elevation/dem.tif", "elevation/dem.tiff", "terrain/dem.tif", "terrain/dem.tiff"),
    "dsm": ("elevation/dsm.tif", "elevation/dsm.tiff", "terrain/dsm.tif", "terrain/dsm.tiff"),
}


@dataclass(frozen=True)
class TerrainBundle:
    bundle_dir: Path
    manifest_path: Path
    manifest: dict[str, Any]
    orthophoto_path: Path
    georef: SimpleGeoReference | None
    tile_index_path: Path | None
    tile_size_px: int
    overlap_px: int
    gsd_m: float | None
    crs: str | None
    elevation_assets: dict[str, str]

    @property
    def has_tile_index(self) -> bool:
        return self.tile_index_path is not None and self.tile_index_path.exists()


def georef_from_manifest(manifest: dict[str, Any]) -> SimpleGeoReference | None:
    georef = manifest_georef(manifest)
    if not {"origin_lat", "origin_lon", "gsd_m"}.issubset(georef):
        return None
    return SimpleGeoReference.from_dict(georef)


def load_terrain_bundle(bundle_path: str | Path) -> TerrainBundle:
    bundle_dir, manifest = load_manifest(bundle_path)
    manifest_path = bundle_dir / "manifest.json"
    orthophoto_path = manifest_orthophoto_path(bundle_dir, manifest)
    georef = georef_from_manifest(manifest)
    terrain = manifest.get("terrain_bundle") or {}
    tile_index_rel = terrain.get("tile_index_path")
    tile_index_path = resolve_bundle_path(bundle_dir, tile_index_rel) if tile_index_rel else None
    tile_size_px = int(terrain.get("tile_size_px", DEFAULT_TERRAIN_TILE_SIZE_PX))
    overlap_px = int(terrain.get("overlap_px", DEFAULT_TERRAIN_OVERLAP_PX))
    gsd_m = terrain.get("gsd_m") or (georef.gsd_m if georef else None)
    crs = terrain.get("crs") or (georef.crs if georef else None)
    elevation_assets = normalize_elevation_assets(bundle_dir, terrain)
    return TerrainBundle(
        bundle_dir=bundle_dir,
        manifest_path=manifest_path,
        manifest=manifest,
        orthophoto_path=orthophoto_path,
        georef=georef,
        tile_index_path=tile_index_path,
        tile_size_px=tile_size_px,
        overlap_px=overlap_px,
        gsd_m=float(gsd_m) if gsd_m is not None else None,
        crs=str(crs) if crs else None,
        elevation_assets=elevation_assets,
    )


def normalize_elevation_assets(bundle_dir: Path, terrain: dict[str, Any]) -> dict[str, str]:
    raw = terrain.get("elevation_assets")
    assets: dict[str, str] = {}
    if isinstance(raw, dict):
        for kind in ("dem", "dsm"):
            value = raw.get(kind)
            if isinstance(value, dict):
                value = value.get("path") or value.get("href")
            if isinstance(value, str) and value:
                assets[kind] = str(Path(value)).replace("\\", "/")

    for kind, candidates in ELEVATION_ASSET_CANDIDATES.items():
        if kind in assets:
            continue
        for candidate in candidates:
            if (bundle_dir / candidate).exists():
                assets[kind] = candidate
                break
    return assets


def discover_elevation_assets(bundle_dir: Path) -> dict[str, str]:
    return normalize_elevation_assets(bundle_dir, {})


def terrain_manifest_fields(
    georef: SimpleGeoReference | None,
    *,
    tile_index_path: str = DEFAULT_TERRAIN_TILE_INDEX,
    tile_size_px: int = DEFAULT_TERRAIN_TILE_SIZE_PX,
    overlap_px: int = DEFAULT_TERRAIN_OVERLAP_PX,
    tile_count: int | None = None,
    feature_count: int | None = None,
    elevation_assets: dict[str, str] | None = None,
) -> dict[str, Any]:
    local_origin = None
    if georef is not None:
        local_origin = {
            "latitude": georef.origin_lat,
            "longitude": georef.origin_lon,
            "east_m": 0.0,
            "north_m": 0.0,
        }
    fields: dict[str, Any] = {
        "version": TERRAIN_BUNDLE_VERSION,
        "tile_index_path": tile_index_path,
        "tile_size_px": int(tile_size_px),
        "overlap_px": int(overlap_px),
        "local_origin": local_origin,
        "crs": georef.crs if georef else None,
        "gsd_m": georef.gsd_m if georef else None,
        "coordinate_frame": "local_enu",
        "vertical_source": "barometer_optional",
        "sensors": {
            "barometer": {
                "enabled_optional": True,
                "source": "mavlink_or_replay",
                "required": False,
            }
        },
    }
    if tile_count is not None:
        fields["tile_count"] = int(tile_count)
    if feature_count is not None:
        fields["feature_count"] = int(feature_count)
    if elevation_assets:
        fields["elevation_assets"] = elevation_assets
    return fields


def stac_manifest(bundle: TerrainBundle) -> dict[str, Any]:
    georef = bundle.georef
    center = [georef.origin_lon, georef.origin_lat] if georef else None
    assets = {
        "orthophoto": {"href": str(bundle.orthophoto_path.relative_to(bundle.bundle_dir))},
        "tile_index": {
            "href": str(bundle.tile_index_path.relative_to(bundle.bundle_dir))
            if bundle.tile_index_path and bundle.tile_index_path.is_relative_to(bundle.bundle_dir)
            else DEFAULT_TERRAIN_TILE_INDEX
        },
    }
    for kind, href in bundle.elevation_assets.items():
        assets[kind] = {
            "href": href,
            "roles": ["data", "elevation", kind],
            "type": "image/tiff; application=geotiff",
        }
    return {
        "stac_version": "1.0.0",
        "type": "Feature",
        "id": bundle.manifest.get("bundle_id") or bundle.bundle_dir.name,
        "properties": {
            "platform": "drone-vision-nav",
            "gnss_denied": True,
            "vision_navigation": True,
        },
        "geometry": {"type": "Point", "coordinates": center} if center else None,
        "links": [],
        "assets": assets,
    }


def summarize_terrain_bundle(bundle_path: str | Path) -> dict[str, Any]:
    bundle = load_terrain_bundle(bundle_path)
    from vision_nav.geospatial_health import geospatial_health_report

    health = geospatial_health_report(bundle_path)
    issues: list[dict[str, str]] = []
    if not bundle.orthophoto_path.exists():
        issues.append({"severity": "error", "message": f"Missing orthophoto: {bundle.orthophoto_path}"})
    if bundle.georef is None:
        issues.append({"severity": "warning", "message": "No georeference found; terrain runtime cannot emit lat/lon."})
    if bundle.tile_index_path is None:
        issues.append({"severity": "warning", "message": "Manifest has no terrain_bundle.tile_index_path."})
    elif not bundle.tile_index_path.exists():
        issues.append({"severity": "error", "message": f"Missing terrain tile index: {bundle.tile_index_path}"})
    for issue in health.get("issues", []):
        if issue not in issues:
            issues.append(issue)

    terrain = bundle.manifest.get("terrain_bundle") or {}
    return {
        "bundle_dir": str(bundle.bundle_dir),
        "bundle_id": bundle.manifest.get("bundle_id"),
        "orthophoto_path": str(bundle.orthophoto_path),
        "tile_index_path": str(bundle.tile_index_path) if bundle.tile_index_path else None,
        "tile_count": terrain.get("tile_count"),
        "feature_count": terrain.get("feature_count"),
        "tile_size_px": bundle.tile_size_px,
        "overlap_px": bundle.overlap_px,
        "gsd_m": bundle.gsd_m,
        "crs": bundle.crs,
        "has_tile_index": bundle.has_tile_index,
        "elevation_assets": bundle.elevation_assets,
        "geospatial_health": health,
        "issues": issues,
        "status": "failed"
        if any(issue["severity"] == "error" for issue in issues) or health["status"] == "failed"
        else "passed",
    }


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate and summarize a terrain vision bundle.")
    parser.add_argument("--bundle", required=True, help="Bundle directory or manifest.json path.")
    parser.add_argument("--json", action="store_true", help="Emit JSON only.")
    return parser.parse_args()


def print_human(summary: dict[str, Any]) -> None:
    health = summary.get("geospatial_health") or {}
    map_quality = health.get("map_quality") or {}
    print(f"Terrain bundle: {summary.get('bundle_id') or '(unnamed)'}")
    print(f"Directory: {summary['bundle_dir']}")
    print(f"Status: {summary['status']}")
    print(f"Map health: {health.get('status') or 'unknown'}")
    print(f"Tile index: {summary.get('tile_index_path')}")
    print(f"Tiles: {summary.get('tile_count') or 0}")
    print(f"Features: {summary.get('feature_count') or 0}")
    if map_quality:
        print(f"Pi runtime cost: {map_quality.get('estimated_pi_runtime_cost') or 'unknown'}")
    for issue in summary["issues"]:
        print(f"[{issue['severity'].upper()}] {issue['message']}")


def main() -> None:
    args = parse_args()
    summary = summarize_terrain_bundle(args.bundle)
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print_human(summary)
    if summary["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
