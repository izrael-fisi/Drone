"""Immutable model-independent geospatial map references for data collection.

A ReferenceMapRelease is deliberately smaller than a deployment MapRelease v2.
It pins the imagery and WGS84/ENU origin used to collect and label the first
training data, before a learned model exists to build MegaLoc/XFeat assets.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import hashlib
import json
import math
from pathlib import Path
from typing import Any


REFERENCE_MAP_SCHEMA = "vision_nav_reference_map_release_v1"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _asset_path(root: Path, location: Any, field: str) -> Path:
    if not isinstance(location, str) or not location:
        raise ValueError(f"Reference map is missing {field}.")
    path = Path(location)
    if path.is_absolute():
        raise ValueError(f"Reference map {field} must be a relative path.")
    resolved = (root / path).resolve()
    try:
        resolved.relative_to(root.resolve())
    except ValueError as exc:
        raise ValueError(f"Reference map {field} resolves outside its release root.") from exc
    return resolved


def validate_reference_map(raw: dict[str, Any], *, root: Path | None = None) -> None:
    required = (
        "schema_version", "release_id", "map_id", "version", "crs", "local_origin_enu",
        "geodetic_origin_wgs84", "gsd_m_per_px", "imagery_uri", "stac_item_uri",
        "calibration_ids", "checksums_sha256",
    )
    missing = [key for key in required if raw.get(key) in (None, "", [], {})]
    if missing:
        raise ValueError(f"Reference map is missing: {', '.join(missing)}")
    if raw["schema_version"] != REFERENCE_MAP_SCHEMA:
        raise ValueError(f"Reference map must use {REFERENCE_MAP_SCHEMA}.")
    expected_id = f"{raw['map_id']}@{raw['version']}"
    if raw["release_id"] != expected_id:
        raise ValueError("Reference map release_id must equal map_id@version.")
    origin = raw["local_origin_enu"]
    if not isinstance(origin, list) or len(origin) != 3 or not all(isinstance(value, (int, float)) and math.isfinite(float(value)) for value in origin):
        raise ValueError("Reference map local_origin_enu must contain three finite ENU values.")
    wgs84 = raw["geodetic_origin_wgs84"]
    if not isinstance(wgs84, dict):
        raise ValueError("Reference map geodetic_origin_wgs84 must be an object.")
    latitude, longitude, altitude = wgs84.get("lat_deg"), wgs84.get("lon_deg"), wgs84.get("altitude_m")
    if not all(isinstance(value, (int, float)) and math.isfinite(float(value)) for value in (latitude, longitude, altitude)):
        raise ValueError("Reference map WGS84 origin must contain finite latitude, longitude, and altitude.")
    if not -90.0 <= float(latitude) <= 90.0 or not -180.0 <= float(longitude) <= 180.0:
        raise ValueError("Reference map WGS84 origin latitude/longitude is out of range.")
    if not isinstance(raw["gsd_m_per_px"], (int, float)) or float(raw["gsd_m_per_px"]) <= 0.0:
        raise ValueError("Reference map gsd_m_per_px must be positive.")
    calibration_ids = raw["calibration_ids"]
    if not isinstance(calibration_ids, list) or not all(isinstance(value, str) and value for value in calibration_ids):
        raise ValueError("Reference map calibration_ids must contain non-empty IDs.")
    checksums = raw["checksums_sha256"]
    if not isinstance(checksums, dict):
        raise ValueError("Reference map checksums_sha256 must be an object.")
    for field in ("imagery_uri", "stac_item_uri"):
        location = str(raw[field])
        digest = checksums.get(location)
        if not isinstance(digest, str) or len(digest) != 64:
            raise ValueError(f"Reference map requires a SHA-256 checksum for {field}.")
        if root is not None:
            asset = _asset_path(root, location, field)
            if not asset.is_file():
                raise ValueError(f"Reference map asset does not exist: {location}")
            if sha256_file(asset) != digest:
                raise ValueError(f"Reference map checksum mismatch for {field}: {location}")


def load_reference_map(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser().resolve()
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read reference map: {source}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError("Reference map must be a JSON object.")
    validate_reference_map(raw, root=source.parent)
    return raw


def create_reference_map(
    output_path: str | Path,
    *,
    map_id: str,
    version: str,
    crs: str,
    local_origin_enu: tuple[float, float, float],
    geodetic_origin_wgs84: tuple[float, float, float],
    gsd_m_per_px: float,
    imagery_uri: str,
    stac_item_uri: str,
    calibration_ids: list[str],
) -> dict[str, Any]:
    output = Path(output_path).expanduser().resolve()
    root = output.parent
    release = {
        "schema_version": REFERENCE_MAP_SCHEMA,
        "created_utc": _utc_now(),
        "release_id": f"{map_id}@{version}",
        "map_id": map_id,
        "version": version,
        "crs": crs,
        "local_origin_enu": list(local_origin_enu),
        "geodetic_origin_wgs84": {
            "lat_deg": geodetic_origin_wgs84[0], "lon_deg": geodetic_origin_wgs84[1], "altitude_m": geodetic_origin_wgs84[2],
        },
        "gsd_m_per_px": gsd_m_per_px,
        "imagery_uri": imagery_uri,
        "stac_item_uri": stac_item_uri,
        "calibration_ids": calibration_ids,
        "checksums_sha256": {},
    }
    for field in ("imagery_uri", "stac_item_uri"):
        location = str(release[field])
        asset = _asset_path(root, location, field)
        if not asset.is_file():
            raise ValueError(f"Reference map asset does not exist: {asset}")
        release["checksums_sha256"][location] = sha256_file(asset)
    validate_reference_map(release, root=root)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(release, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return release


def main() -> None:
    parser = argparse.ArgumentParser(description="Create or validate a model-independent reference map release for training capture.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    create = subparsers.add_parser("create")
    create.add_argument("--output", required=True, type=Path)
    create.add_argument("--map-id", required=True)
    create.add_argument("--version", required=True)
    create.add_argument("--crs", required=True)
    create.add_argument("--origin-enu", required=True, nargs=3, type=float)
    create.add_argument("--origin-wgs84", required=True, nargs=3, type=float, metavar=("LAT_DEG", "LON_DEG", "ALT_M"))
    create.add_argument("--gsd-m-per-px", required=True, type=float)
    create.add_argument("--imagery-uri", required=True)
    create.add_argument("--stac-item-uri", required=True)
    create.add_argument("--calibration-id", action="append", required=True)
    validate = subparsers.add_parser("validate")
    validate.add_argument("--reference-map", required=True, type=Path)
    args = parser.parse_args()
    try:
        if args.command == "create":
            release = create_reference_map(
                args.output, map_id=args.map_id, version=args.version, crs=args.crs,
                local_origin_enu=tuple(args.origin_enu), geodetic_origin_wgs84=tuple(args.origin_wgs84),
                gsd_m_per_px=args.gsd_m_per_px, imagery_uri=args.imagery_uri, stac_item_uri=args.stac_item_uri,
                calibration_ids=args.calibration_id,
            )
        else:
            release = load_reference_map(args.reference_map)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps({"status": "valid", "release_id": release["release_id"]}, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
