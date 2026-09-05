"""Promote learned map artifacts into a validated MapRelease v2.

The research environment produces the MegaLoc FAISS index and XFeat tile cache.
This command never synthesizes descriptors from a classical fallback: all required
learned artifacts must be supplied explicitly.
"""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
import tempfile

from .map_release import validate_release
from .releases import load_model_release


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def relative_or_absolute(path: Path, root: Path) -> str:
    try:
        return str(path.resolve().relative_to(root.resolve())).replace("\\", "/")
    except ValueError:
        return str(path.resolve())


def main() -> None:
    parser = argparse.ArgumentParser(description="Promote learned map artifacts into a field-valid MapRelease v2.")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--map-id", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--model-release", required=True, type=Path)
    parser.add_argument("--cog", required=True, type=Path)
    parser.add_argument("--stac-item", required=True, type=Path)
    parser.add_argument("--tile-index", required=True, type=Path)
    parser.add_argument("--megaloc-index", required=True, type=Path)
    parser.add_argument("--megaloc-metadata", required=True, type=Path)
    parser.add_argument("--xfeat-index", required=True, type=Path)
    parser.add_argument("--xfeat-metadata", required=True, type=Path)
    parser.add_argument("--landmark-provenance", type=Path)
    parser.add_argument("--crs", required=True)
    parser.add_argument("--origin-enu", required=True, nargs=3, type=float)
    parser.add_argument("--geodetic-origin-wgs84", nargs=3, type=float, metavar=("LAT_DEG", "LON_DEG", "ALT_M"))
    parser.add_argument("--gsd-m-per-px", required=True, type=float)
    parser.add_argument("--calibration-id", action="append", required=True)
    parser.add_argument("--geometry-mode", action="append", choices=("pnp_3d", "homography_restricted"))
    args = parser.parse_args()

    model = load_model_release(args.model_release)
    assets = {
        "cog_uri": args.cog,
        "stac_item_uri": args.stac_item,
        "tile_index_uri": args.tile_index,
        "retrieval_index_uri": args.megaloc_index,
        "retrieval_metadata_uri": args.megaloc_metadata,
        "xfeat_index_uri": args.xfeat_index,
        "local_feature_metadata_uri": args.xfeat_metadata,
    }
    if args.landmark_provenance:
        assets["landmark_provenance_uri"] = args.landmark_provenance
    missing = [name for name, path in assets.items() if not path.is_file()]
    if missing:
        raise SystemExit(f"Cannot promote map release; missing learned artifacts: {', '.join(missing)}")
    root = args.output.parent.resolve()
    release = {
        "schema_version": "vision_nav_map_release_v2",
        "release_id": f"{args.map_id}@{args.version}",
        "map_id": args.map_id,
        "version": args.version,
        "profile_id": model.profile_id,
        "model_release_id": model.release_id,
        "crs": args.crs,
        "local_origin_enu": args.origin_enu,
        "geodetic_origin_wgs84": (
            {"lat_deg": args.geodetic_origin_wgs84[0], "lon_deg": args.geodetic_origin_wgs84[1], "altitude_m": args.geodetic_origin_wgs84[2]}
            if args.geodetic_origin_wgs84
            else None
        ),
        "gsd_m_per_px": args.gsd_m_per_px,
        **{key: relative_or_absolute(value, root) for key, value in assets.items()},
        "landmark_index_uri": None,
        "dem_uri": None,
        "calibration_ids": args.calibration_id,
        "geometry_modes": args.geometry_mode or (["pnp_3d"] if args.landmark_provenance else ["homography_restricted"]),
        "checksums_sha256": {relative_or_absolute(value, root): sha256(value) for value in assets.values()},
    }
    validate_release(release, root=root, model_release=model.to_dict())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    from .map_asset_validation import validate_learned_map_assets

    # Validate before exposing a release manifest to a consumer.
    if args.output.exists():
        raise SystemExit("Map release already exists; use a new immutable release destination.")
    # Close before reopening: Windows does not share an open temporary file.
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", dir=root, encoding="utf-8", delete=False) as handle:
        staging = Path(handle.name)
    try:
        staging.write_text(json.dumps(release, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        report = validate_learned_map_assets(staging)
        if report["status"] != "valid":
            raise SystemExit("Cannot promote map release: " + "; ".join(report["errors"]))
        staging.replace(args.output)
    finally:
        staging.unlink(missing_ok=True)
    print(json.dumps({"status": "promoted", "release_id": release["release_id"], "output": str(args.output)}))


if __name__ == "__main__":
    main()
