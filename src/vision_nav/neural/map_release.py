"""Create and validate immutable MapRelease v2 manifests for learned navigation."""

from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

from .profiles import load_profile
from .releases import load_model_release


REQUIRED_ASSETS = ("cog_uri", "stac_item_uri", "tile_index_uri", "retrieval_index_uri", "xfeat_index_uri")
OPTIONAL_CHECKSUMMED_ASSETS = ("retrieval_metadata_uri", "local_feature_metadata_uri", "landmark_provenance_uri")


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_release(path: str | Path) -> dict:
    raw = json.loads(Path(path).read_text(encoding="utf-8"))
    validate_release(raw, root=Path(path).parent)
    return raw


def validate_release(raw: dict, *, root: Path | None = None, model_release: dict | None = None) -> None:
    required = (
        "schema_version", "release_id", "map_id", "version", "profile_id", "model_release_id", "crs",
        "local_origin_enu", "gsd_m_per_px", "calibration_ids", "geometry_modes", "checksums_sha256",
        *REQUIRED_ASSETS,
    )
    missing = [key for key in required if raw.get(key) in (None, "", [], {})]
    if missing:
        raise ValueError(f"MapRelease v2 is missing: {', '.join(missing)}")
    if raw["schema_version"] != "vision_nav_map_release_v2":
        raise ValueError("MapRelease must use schema_version vision_nav_map_release_v2.")
    profile = load_profile(str(raw["profile_id"]))
    unsupported = set(raw["geometry_modes"]) - set(profile.geometry_modes)
    if unsupported:
        raise ValueError(f"Map release has unsupported geometry modes: {', '.join(sorted(unsupported))}")
    if len(raw["local_origin_enu"]) != 3 or float(raw["gsd_m_per_px"]) <= 0.0:
        raise ValueError("Map release origin must be ENU [E,N,U] and GSD must be positive.")
    geodetic_origin = raw.get("geodetic_origin_wgs84")
    if geodetic_origin is not None:
        if not isinstance(geodetic_origin, dict):
            raise ValueError("MapRelease geodetic_origin_wgs84 must be an object when provided.")
        values = (geodetic_origin.get("lat_deg"), geodetic_origin.get("lon_deg"), geodetic_origin.get("altitude_m"))
        if not all(isinstance(value, (int, float)) for value in values):
            raise ValueError("MapRelease geodetic_origin_wgs84 requires numeric lat_deg, lon_deg, and altitude_m.")
        if not -90.0 <= float(values[0]) <= 90.0 or not -180.0 <= float(values[1]) <= 180.0:
            raise ValueError("MapRelease geodetic_origin_wgs84 latitude/longitude is outside WGS84 bounds.")
    if not isinstance(raw["checksums_sha256"], dict) or not raw["checksums_sha256"]:
        raise ValueError("Map release requires non-empty checksums_sha256.")
    for asset in (*REQUIRED_ASSETS, *OPTIONAL_CHECKSUMMED_ASSETS):
        if raw.get(asset) in (None, ""):
            continue
        location = str(raw[asset])
        if "://" in location:
            continue
        expected_digest = raw["checksums_sha256"].get(location)
        if not isinstance(expected_digest, str) or len(expected_digest) != 64:
            raise ValueError(f"Map release requires a SHA-256 checksum for {asset}: {location}")
        try:
            int(expected_digest, 16)
        except ValueError as exc:
            raise ValueError(f"Map release checksum is not hexadecimal for {asset}: {location}") from exc
    if model_release:
        if raw["model_release_id"] != model_release["release_id"]:
            raise ValueError("Map/model release ID mismatch.")
        if raw["profile_id"] != model_release["profile_id"]:
            raise ValueError("Map/model inference profile mismatch.")
        if not set(raw["calibration_ids"]).intersection(model_release["calibration_ids"]):
            raise ValueError("Map/model calibration compatibility mismatch.")
    if root:
        for asset in (*REQUIRED_ASSETS, *OPTIONAL_CHECKSUMMED_ASSETS):
            if raw.get(asset) in (None, ""):
                continue
            location = str(raw[asset])
            if "://" in location:
                continue
            path = root / location
            if not path.is_file():
                raise ValueError(f"Map release asset does not exist: {location}")
            actual_digest = _sha256_file(path)
            expected_digest = raw["checksums_sha256"][location]
            if actual_digest != expected_digest:
                raise ValueError(f"Map release checksum mismatch for {asset}: {location}")
        metadata_uri = raw.get("retrieval_metadata_uri")
        if metadata_uri:
            from .retrieval_index import load_retrieval_metadata

            load_retrieval_metadata(
                root / str(metadata_uri),
                expected_map_release_id=str(raw["release_id"]),
                expected_model_release_id=str(raw["model_release_id"]),
            )
        feature_metadata_uri = raw.get("local_feature_metadata_uri")
        if feature_metadata_uri:
            from .local_feature_cache import load_local_feature_cache

            load_local_feature_cache(
                root / str(feature_metadata_uri),
                expected_map_release_id=str(raw["release_id"]),
                expected_model_release_id=str(raw["model_release_id"]),
            )


def _parse_checksum(value: str) -> tuple[str, str]:
    if "=" not in value:
        raise argparse.ArgumentTypeError("Use relative/path=sha256.")
    path, digest = value.split("=", 1)
    if not path or len(digest) != 64:
        raise argparse.ArgumentTypeError("Checksum requires a path and a 64-character SHA-256 digest.")
    return path, digest


def main() -> None:
    parser = argparse.ArgumentParser(description="Create a MapRelease v2 for pi5_crossview_nav_v1.")
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--map-id", required=True)
    parser.add_argument("--version", required=True)
    parser.add_argument("--model-release", required=True, type=Path)
    parser.add_argument("--crs", required=True)
    parser.add_argument("--origin-enu", required=True, nargs=3, type=float)
    parser.add_argument("--geodetic-origin-wgs84", nargs=3, type=float, metavar=("LAT_DEG", "LON_DEG", "ALT_M"))
    parser.add_argument("--gsd-m-per-px", required=True, type=float)
    parser.add_argument("--cog-uri", required=True)
    parser.add_argument("--stac-item-uri", required=True)
    parser.add_argument("--tile-index-uri", required=True)
    parser.add_argument("--retrieval-index-uri", required=True)
    parser.add_argument("--retrieval-metadata-uri", help="Immutable FAISS-ID to map-tile metadata for Pi retrieval.")
    parser.add_argument("--local-feature-metadata-uri", help="Immutable XFeat cache metadata for Pi matching.")
    parser.add_argument("--landmark-provenance-uri", help="Checksummed metric landmark frame, datum and source record.")
    parser.add_argument("--xfeat-index-uri", required=True)
    parser.add_argument("--calibration-id", action="append", required=True)
    parser.add_argument("--geometry-mode", action="append", default=None)
    parser.add_argument("--checksum", action="append", required=True, type=_parse_checksum)
    args = parser.parse_args()
    model = load_model_release(args.model_release)
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
        "cog_uri": args.cog_uri,
        "stac_item_uri": args.stac_item_uri,
        "tile_index_uri": args.tile_index_uri,
        "retrieval_index_uri": args.retrieval_index_uri,
        "retrieval_metadata_uri": args.retrieval_metadata_uri,
        "local_feature_metadata_uri": args.local_feature_metadata_uri,
        "landmark_provenance_uri": args.landmark_provenance_uri,
        "xfeat_index_uri": args.xfeat_index_uri,
        "landmark_index_uri": None,
        "dem_uri": None,
        "calibration_ids": args.calibration_id,
        "geometry_modes": args.geometry_mode or ["homography_restricted"],
        "checksums_sha256": dict(args.checksum),
    }
    validate_release(release, model_release=model.to_dict())
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(release, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps({"release_id": release["release_id"], "output": str(args.output)}))


def validate_main() -> None:
    parser = argparse.ArgumentParser(description="Validate a learned MapRelease v2 and model compatibility.")
    parser.add_argument("--release", required=True, type=Path)
    parser.add_argument("--model-release", type=Path)
    args = parser.parse_args()
    raw = json.loads(args.release.read_text(encoding="utf-8"))
    model = load_model_release(args.model_release).to_dict() if args.model_release else None
    validate_release(raw, root=args.release.parent, model_release=model)
    print(json.dumps({"status": "valid", "release_id": raw["release_id"]}))
