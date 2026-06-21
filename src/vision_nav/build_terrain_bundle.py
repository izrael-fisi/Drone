from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path

import yaml

from vision_nav.build_map_bundle import build_map_bundle
from vision_nav.bundle import load_manifest, manifest_feature_options, manifest_orthophoto_path
from vision_nav.terrain_bundle import (
    DEFAULT_TERRAIN_OVERLAP_PX,
    DEFAULT_TERRAIN_TILE_INDEX,
    DEFAULT_TERRAIN_TILE_SIZE_PX,
    TERRAIN_BUNDLE_VERSION,
    georef_from_manifest,
    load_terrain_bundle,
    stac_manifest,
    terrain_manifest_fields,
)
from vision_nav.terrain_tiles import build_tile_index


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build a tiled terrain vision bundle from a mission bundle.")
    parser.add_argument("--bundle", required=True, help="Bundle directory or manifest.json path.")
    parser.add_argument("--tile-size-px", type=int, default=DEFAULT_TERRAIN_TILE_SIZE_PX)
    parser.add_argument("--overlap-px", type=int, default=DEFAULT_TERRAIN_OVERLAP_PX)
    parser.add_argument("--method", choices=["orb", "akaze", "sift"], help="Override feature method.")
    parser.add_argument("--max-features", type=int, help="Override per-tile max features.")
    parser.add_argument("--write-checksums", action="store_true", help="Write checksums.sha256 after build.")
    return parser.parse_args()


def _relative(path: Path, root: Path) -> str:
    return str(path.relative_to(root)).replace("\\", "/")


def build_terrain_bundle(
    bundle: str,
    *,
    tile_size_px: int = DEFAULT_TERRAIN_TILE_SIZE_PX,
    overlap_px: int = DEFAULT_TERRAIN_OVERLAP_PX,
    method: str | None = None,
    max_features: int | None = None,
    write_checksums: bool = False,
) -> dict:
    legacy = build_map_bundle(bundle)
    bundle_dir, manifest = load_manifest(bundle)
    map_path = manifest_orthophoto_path(bundle_dir, manifest)
    feature_options = manifest_feature_options(manifest)
    selected_method = method or feature_options["method"]
    selected_max_features = int(max_features or feature_options["max_features"])
    georef = georef_from_manifest(manifest)

    tile_index_path = bundle_dir / DEFAULT_TERRAIN_TILE_INDEX
    tiles_dir = bundle_dir / "imagery" / "tiles"
    descriptors_dir = bundle_dir / "index" / "descriptors"
    config_dir = bundle_dir / "config"
    config_dir.mkdir(parents=True, exist_ok=True)

    index_summary = build_tile_index(
        bundle_dir=bundle_dir,
        map_image=map_path,
        index_path=tile_index_path,
        tiles_dir=tiles_dir,
        descriptors_dir=descriptors_dir,
        georef=georef,
        method=selected_method,  # type: ignore[arg-type]
        max_features=selected_max_features,
        tile_size_px=tile_size_px,
        overlap_px=overlap_px,
    )

    manifest["terrain_bundle"] = {
        **terrain_manifest_fields(
            georef,
            tile_index_path=_relative(tile_index_path, bundle_dir),
            tile_size_px=tile_size_px,
            overlap_px=overlap_px,
            tile_count=int(index_summary["tile_count"]),
            feature_count=int(index_summary["feature_count"]),
        ),
        "built_at": datetime.now(timezone.utc).isoformat(),
        "builder": "vision_nav.build_terrain_bundle",
        "descriptor_dir": "index/descriptors",
        "tile_image_dir": "imagery/tiles",
        "runtime": "vision_imu_map",
    }
    manifest.setdefault("features", {})["method"] = selected_method
    manifest.setdefault("features", {})["max_features"] = selected_max_features
    manifest.setdefault("notes", [])
    if "Terrain runtime uses tiled map descriptors for coarse-to-local matching." not in manifest["notes"]:
        manifest["notes"].append("Terrain runtime uses tiled map descriptors for coarse-to-local matching.")

    manifest_path = bundle_dir / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")

    terrain_config = {
        "terrain_nav": {
            "version": TERRAIN_BUNDLE_VERSION,
            "tile_index_path": _relative(tile_index_path, bundle_dir),
            "max_candidates": 64,
            "startup_search": "bounded_region",
            "vertical_source": "barometer_optional",
            "covariance_policy": "inflate_when_visual_scale_is_weak_or_stale",
        },
        "sensors": {
            "barometer": {
                "enabled_optional": True,
                "source": "mavlink_or_replay",
                "required": False,
            }
        },
        "matching": {
            "method": selected_method,
            "max_features": selected_max_features,
            "tile_size_px": tile_size_px,
            "overlap_px": overlap_px,
        },
    }
    config_path = config_dir / "terrain_nav.yaml"
    config_path.write_text(yaml.safe_dump(terrain_config, sort_keys=False))

    terrain_bundle = load_terrain_bundle(bundle_dir)
    stac_path = bundle_dir / "manifest.stac.json"
    stac_path.write_text(json.dumps(stac_manifest(terrain_bundle), indent=2) + "\n")

    result = {
        "bundle_dir": str(bundle_dir),
        "manifest_path": str(manifest_path),
        "stac_manifest_path": str(stac_path),
        "legacy_feature_index": legacy.get("output"),
        "terrain_bundle": manifest["terrain_bundle"],
        "tile_index": {
            "path": str(tile_index_path),
            "tile_count": int(index_summary["tile_count"]),
            "feature_count": int(index_summary["feature_count"]),
            "method": selected_method,
        },
        "config_path": str(config_path),
    }
    if write_checksums:
        from vision_nav.bundle_checksums import write_checksum_file

        result["checksums"] = write_checksum_file(bundle_dir)
    return result


def main() -> None:
    args = parse_args()
    print(
        json.dumps(
            build_terrain_bundle(
                args.bundle,
                tile_size_px=args.tile_size_px,
                overlap_px=args.overlap_px,
                method=args.method,
                max_features=args.max_features,
                write_checksums=args.write_checksums,
            ),
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
