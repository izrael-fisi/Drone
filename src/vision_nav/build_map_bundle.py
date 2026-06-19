from __future__ import annotations

import argparse
import json
from pathlib import Path

from vision_nav.build_feature_map import build_feature_map
from vision_nav.bundle import (
    load_manifest,
    manifest_feature_options,
    manifest_features_path,
    manifest_georef,
    manifest_orthophoto_path,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Build feature index from a map bundle manifest.")
    parser.add_argument("--bundle", required=True, help="Bundle directory or manifest.json path.")
    parser.add_argument(
        "--metadata-json",
        help="Optional metadata JSON path. Defaults next to the feature index.",
    )
    parser.add_argument("--write-checksums", action="store_true", help="Write checksums.sha256 after build.")
    return parser.parse_args()


def build_map_bundle(bundle: str, metadata_json: str | None = None) -> dict:
    bundle_dir, manifest = load_manifest(bundle)
    map_path = manifest_orthophoto_path(bundle_dir, manifest)
    features_path = manifest_features_path(bundle_dir, manifest)
    feature_options = manifest_feature_options(manifest)
    georef = manifest_georef(manifest)

    features_path.parent.mkdir(parents=True, exist_ok=True)

    result = build_feature_map(
        map_image=str(map_path),
        output=str(features_path),
        method=feature_options["method"],
        max_features=feature_options["max_features"],
        origin_lat=georef.get("origin_lat"),
        origin_lon=georef.get("origin_lon"),
        gsd_m=georef.get("gsd_m"),
        origin_pixel_x=float(georef.get("origin_pixel_x", 0.0)),
        origin_pixel_y=float(georef.get("origin_pixel_y", 0.0)),
        rotation_deg=float(georef.get("rotation_deg", 0.0)),
    )

    result["bundle_dir"] = str(bundle_dir)
    result["bundle_id"] = manifest.get("bundle_id")

    metadata_path = Path(metadata_json) if metadata_json else features_path.with_suffix(".json")
    metadata_path.parent.mkdir(parents=True, exist_ok=True)
    metadata_path.write_text(json.dumps(result, indent=2) + "\n")
    result["metadata_json"] = str(metadata_path)
    return result


def main() -> None:
    args = parse_args()
    result = build_map_bundle(args.bundle, args.metadata_json)
    if args.write_checksums:
        from vision_nav.bundle_checksums import write_checksum_file

        result["checksums"] = write_checksum_file(args.bundle)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
