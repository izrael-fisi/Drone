"""Materialize offline-only GNSS/attitude supervision for learned navigation.

The vehicle runtime never consumes these labels. They turn a verified training
index into auditable ENU position and heading targets for cloud training and
evaluation, while preserving the raw reference used to derive every label.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
from typing import Any

from vision_nav.training_capture import sha256_file

from .reference_map import load_reference_map


TRAINING_LABEL_SCHEMA = "vision_nav_training_labels_v1"
WGS84_A_M = 6_378_137.0
WGS84_E2 = 6.69437999014e-3
DEFAULT_MAX_GPS_HORIZONTAL_ACCURACY_M = 10.0


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _is_finite_number(value: Any) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(float(value))


def _geodetic_to_ecef(lat_deg: float, lon_deg: float, altitude_m: float) -> tuple[float, float, float]:
    lat = math.radians(lat_deg)
    lon = math.radians(lon_deg)
    sin_lat = math.sin(lat)
    radius = WGS84_A_M / math.sqrt(1.0 - WGS84_E2 * sin_lat * sin_lat)
    return (
        (radius + altitude_m) * math.cos(lat) * math.cos(lon),
        (radius + altitude_m) * math.cos(lat) * math.sin(lon),
        (radius * (1.0 - WGS84_E2) + altitude_m) * sin_lat,
    )


def geodetic_to_enu(
    lat_deg: float,
    lon_deg: float,
    altitude_m: float,
    *,
    origin_lat_deg: float,
    origin_lon_deg: float,
    origin_altitude_m: float,
) -> tuple[float, float, float]:
    """Convert WGS84 geodetic coordinates to a local East/North/Up frame."""

    x, y, z = _geodetic_to_ecef(lat_deg, lon_deg, altitude_m)
    ox, oy, oz = _geodetic_to_ecef(origin_lat_deg, origin_lon_deg, origin_altitude_m)
    dx, dy, dz = x - ox, y - oy, z - oz
    lat0 = math.radians(origin_lat_deg)
    lon0 = math.radians(origin_lon_deg)
    east = -math.sin(lon0) * dx + math.cos(lon0) * dy
    north = -math.sin(lat0) * math.cos(lon0) * dx - math.sin(lat0) * math.sin(lon0) * dy + math.cos(lat0) * dz
    up = math.cos(lat0) * math.cos(lon0) * dx + math.cos(lat0) * math.sin(lon0) * dy + math.sin(lat0) * dz
    return east, north, up


def _origin_from_samples(
    samples: list[dict[str, Any]], *, min_gps_fix_type: int, require_fix_type: bool,
    max_gps_horizontal_accuracy_m: float, require_gps_horizontal_accuracy: bool,
) -> dict[str, float]:
    for sample in samples:
        reference = sample.get("gps_reference")
        if not isinstance(reference, dict):
            continue
        lat, lon = reference.get("lat"), reference.get("lon")
        if not _is_finite_number(lat) or not _is_finite_number(lon):
            continue
        fix_type = reference.get("fix_type")
        if require_fix_type and (not isinstance(fix_type, int) or fix_type < min_gps_fix_type):
            continue
        horizontal_accuracy_m = reference.get("horizontal_accuracy_m")
        if require_gps_horizontal_accuracy and not _is_finite_number(horizontal_accuracy_m):
            continue
        if _is_finite_number(horizontal_accuracy_m) and (
            float(horizontal_accuracy_m) <= 0.0 or float(horizontal_accuracy_m) > max_gps_horizontal_accuracy_m
        ):
            continue
        altitude = reference.get("alt_m")
        return {
            "lat_deg": float(lat),
            "lon_deg": float(lon),
            "altitude_m": float(altitude) if _is_finite_number(altitude) else 0.0,
        }
    raise ValueError("No GNSS reference satisfies the requested training-label quality policy.")


def _normalize_heading(yaw_rad: Any) -> float | None:
    if not _is_finite_number(yaw_rad):
        return None
    return math.atan2(math.sin(float(yaw_rad)), math.cos(float(yaw_rad)))


def _map_release_origin(path: str | Path, samples: list[dict[str, Any]]) -> tuple[dict[str, float], dict[str, Any]]:
    source = Path(path).expanduser().resolve()
    _check_recorded_map_binding(source, samples)
    try:
        release = json.loads(source.read_text(encoding="utf-8"))
    except Exception as exc:
        raise ValueError(f"Could not read MapRelease for training labels: {source}: {exc}") from exc
    if release.get("schema_version") != "vision_nav_map_release_v2":
        raise ValueError("Training-label MapRelease must use schema_version vision_nav_map_release_v2.")
    release_id = release.get("release_id")
    if not isinstance(release_id, str) or not release_id:
        raise ValueError("Training-label MapRelease is missing release_id.")
    sample_release_ids = {sample.get("map_release_id") for sample in samples if isinstance(sample.get("map_release_id"), str)}
    if sample_release_ids and sample_release_ids != {release_id}:
        raise ValueError("Training-index samples do not all reference the MapRelease selected for label generation.")
    origin = release.get("geodetic_origin_wgs84")
    if not isinstance(origin, dict):
        raise ValueError("Training-label MapRelease requires geodetic_origin_wgs84 for map-aligned ENU labels.")
    lat, lon, altitude = origin.get("lat_deg"), origin.get("lon_deg"), origin.get("altitude_m")
    if not all(_is_finite_number(value) for value in (lat, lon, altitude)):
        raise ValueError("MapRelease geodetic_origin_wgs84 must contain finite lat_deg, lon_deg, and altitude_m values.")
    return (
        {"lat_deg": float(lat), "lon_deg": float(lon), "altitude_m": float(altitude)},
        {"release_id": release_id, "manifest_path": str(source), "manifest_sha256": sha256_file(source)},
    )


def _reference_map_origin(path: str | Path, samples: list[dict[str, Any]]) -> tuple[dict[str, float], dict[str, Any]]:
    source = Path(path).expanduser().resolve()
    _check_recorded_map_binding(source, samples)
    release = load_reference_map(source)
    release_id = str(release["release_id"])
    sample_release_ids = {sample.get("map_release_id") for sample in samples if isinstance(sample.get("map_release_id"), str)}
    if sample_release_ids and sample_release_ids != {release_id}:
        raise ValueError("Training-index samples do not all reference the selected ReferenceMapRelease.")
    origin = release["geodetic_origin_wgs84"]
    return (
        {"lat_deg": float(origin["lat_deg"]), "lon_deg": float(origin["lon_deg"]), "altitude_m": float(origin["altitude_m"])},
        {"release_id": release_id, "manifest_path": str(source), "manifest_sha256": sha256_file(source)},
    )


def _check_recorded_map_binding(source: Path, samples: list[dict[str, Any]]) -> None:
    expected = set()
    for sample in samples:
        value = (sample.get("map_reference") or {}).get("manifest_sha256")
        if value is not None:
            if not isinstance(value, str) or len(value) != 64:
                raise ValueError("Invalid recorded map manifest digest")
            expected.add(value)
    if expected and expected != {sha256_file(source)}:
        raise ValueError("Selected map manifest differs from the map bound to recorded training samples")


def _label_sample(
    sample: dict[str, Any],
    *,
    origin: dict[str, float],
    min_gps_fix_type: int,
    require_fix_type: bool,
    max_gps_age_ms: float,
    max_gps_horizontal_accuracy_m: float,
    require_gps_horizontal_accuracy: bool,
) -> dict[str, Any]:
    reference = sample.get("gps_reference")
    label: dict[str, Any] = {
        "schema_version": TRAINING_LABEL_SCHEMA,
        "sample_id": sample.get("sample_id"),
        "capture_session_id": sample.get("capture_session_id"),
        "map_release_id": sample.get("map_release_id"),
        "calibration_id": sample.get("calibration_id"),
        "policy": "offline_training_only",
        "source": "px4_gnss_reference_and_attitude",
        "status": "rejected",
        "reason": None,
        "gps_reference": reference if isinstance(reference, dict) else None,
        "target": None,
    }
    if not isinstance(reference, dict):
        label["reason"] = "missing_gps_reference"
        return label
    lat, lon = reference.get("lat"), reference.get("lon")
    if not _is_finite_number(lat) or not _is_finite_number(lon):
        label["reason"] = "invalid_gps_coordinates"
        return label
    fix_type = reference.get("fix_type")
    if require_fix_type and (not isinstance(fix_type, int) or fix_type < min_gps_fix_type):
        label["reason"] = "gps_fix_type_below_training_policy"
        return label
    horizontal_accuracy_m = reference.get("horizontal_accuracy_m")
    if require_gps_horizontal_accuracy and not _is_finite_number(horizontal_accuracy_m):
        label["reason"] = "missing_gps_horizontal_accuracy"
        return label
    if _is_finite_number(horizontal_accuracy_m):
        if float(horizontal_accuracy_m) <= 0.0:
            label["reason"] = "invalid_gps_horizontal_accuracy"
            return label
        if float(horizontal_accuracy_m) > max_gps_horizontal_accuracy_m:
            label["reason"] = "gps_horizontal_accuracy_below_training_policy"
            label["gps_horizontal_accuracy_m"] = float(horizontal_accuracy_m)
            return label
    sample_time = sample.get("timestamp_ns")
    gps_time = reference.get("timestamp_ns")
    if not isinstance(sample_time, int) or not isinstance(gps_time, int):
        label["reason"] = "missing_gps_timestamp"
        return label
    age_ms = abs(sample_time - gps_time) / 1_000_000.0
    if age_ms > max_gps_age_ms:
        label["reason"] = "gps_reference_too_old"
        label["gps_age_ms"] = round(age_ms, 3)
        return label
    altitude = reference.get("alt_m")
    altitude_m = float(altitude) if _is_finite_number(altitude) else origin["altitude_m"]
    east, north, up = geodetic_to_enu(
        float(lat),
        float(lon),
        altitude_m,
        origin_lat_deg=origin["lat_deg"],
        origin_lon_deg=origin["lon_deg"],
        origin_altitude_m=origin["altitude_m"],
    )
    attitude = sample.get("attitude") if isinstance(sample.get("attitude"), dict) else {}
    heading = _normalize_heading(attitude.get("yaw_rad"))
    label["status"] = "accepted"
    label["gps_age_ms"] = round(age_ms, 3)
    label["gps_horizontal_accuracy_m"] = float(horizontal_accuracy_m) if _is_finite_number(horizontal_accuracy_m) else None
    label["target"] = {
        "position_enu_m": {"east": round(east, 4), "north": round(north, 4), "up": round(up, 4)},
        "heading_rad": heading,
        "heading_source": "px4_attitude" if heading is not None else None,
    }
    return label


def materialize_training_labels(
    index_dir: str | Path,
    output_dir: str | Path,
    *,
    origin_lat_deg: float | None = None,
    origin_lon_deg: float | None = None,
    origin_altitude_m: float | None = None,
    min_gps_fix_type: int = 3,
    require_fix_type: bool = True,
    max_gps_age_ms: float = 500.0,
    max_gps_horizontal_accuracy_m: float = DEFAULT_MAX_GPS_HORIZONTAL_ACCURACY_M,
    require_gps_horizontal_accuracy: bool = True,
    map_release: str | Path | None = None,
    reference_map: str | Path | None = None,
) -> dict[str, Any]:
    """Write immutable ENU/heading labels from a verified training index."""

    if min_gps_fix_type < 0:
        raise ValueError("min_gps_fix_type cannot be negative.")
    if max_gps_age_ms <= 0.0:
        raise ValueError("max_gps_age_ms must be positive.")
    if max_gps_horizontal_accuracy_m <= 0.0:
        raise ValueError("max_gps_horizontal_accuracy_m must be positive.")
    source_root = Path(index_dir).expanduser().resolve()
    index_path = source_root / "samples.jsonl"
    index_manifest_path = source_root / "manifest.json"
    if not index_path.is_file() or not index_manifest_path.is_file():
        raise ValueError("Training index must contain manifest.json and samples.jsonl.")
    index_manifest = json.loads(index_manifest_path.read_text(encoding="utf-8"))
    if index_manifest.get("schema_version") != "vision_nav_training_index_v1":
        raise ValueError("Unsupported training index schema.")
    if index_manifest.get("index_sha256") != sha256_file(index_path):
        raise ValueError("Training index checksum mismatch. Rebuild the index before materializing labels.")
    samples = [json.loads(line) for line in index_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    if not samples:
        raise ValueError("Training index contains no samples.")
    explicit_origin = (origin_lat_deg, origin_lon_deg, origin_altitude_m)
    if any(value is not None for value in explicit_origin) and not all(value is not None for value in explicit_origin):
        raise ValueError("Set origin latitude, longitude, and altitude together, or omit all three.")
    configured_origins = int(map_release is not None) + int(reference_map is not None) + int(any(value is not None for value in explicit_origin))
    if configured_origins > 1:
        raise ValueError("Use only one of --map-release, --reference-map, or an explicit WGS84 origin.")
    if any((s.get("map_reference") or {}).get("manifest_sha256") is not None for s in samples) and map_release is None and reference_map is None:
        raise ValueError("Map-bound recorded samples require their selected map release, not a replacement origin")
    map_release_metadata: dict[str, Any] | None = None
    if map_release is not None:
        origin, map_release_metadata = _map_release_origin(map_release, samples)
        origin_source = "map_release_geodetic_origin"
    elif reference_map is not None:
        origin, map_release_metadata = _reference_map_origin(reference_map, samples)
        origin_source = "reference_map_geodetic_origin"
    elif all(value is not None for value in explicit_origin):
        origin = {"lat_deg": float(origin_lat_deg), "lon_deg": float(origin_lon_deg), "altitude_m": float(origin_altitude_m)}
        origin_source = "explicit_wgs84_origin"
    else:
        origin = _origin_from_samples(
            samples,
            min_gps_fix_type=min_gps_fix_type,
            require_fix_type=require_fix_type,
            max_gps_horizontal_accuracy_m=max_gps_horizontal_accuracy_m,
            require_gps_horizontal_accuracy=require_gps_horizontal_accuracy,
        )
        origin_source = "first_accepted_gps_reference"
    if not -90.0 <= origin["lat_deg"] <= 90.0 or not -180.0 <= origin["lon_deg"] <= 180.0:
        raise ValueError("Training-label origin latitude/longitude is outside WGS84 bounds.")

    root = Path(output_dir).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError(f"Training-label output directory must be empty: {root}")
    root.mkdir(parents=True, exist_ok=True)
    labels = [
        _label_sample(
            sample,
            origin=origin,
            min_gps_fix_type=min_gps_fix_type,
            require_fix_type=require_fix_type,
            max_gps_age_ms=max_gps_age_ms,
            max_gps_horizontal_accuracy_m=max_gps_horizontal_accuracy_m,
            require_gps_horizontal_accuracy=require_gps_horizontal_accuracy,
        )
        for sample in samples
    ]
    labels_path = root / "labels.jsonl"
    with labels_path.open("w", encoding="utf-8") as stream:
        for label in labels:
            stream.write(json.dumps(label, sort_keys=True) + "\n")
    accepted = sum(1 for label in labels if label["status"] == "accepted")
    reasons: dict[str, int] = {}
    for label in labels:
        if label["status"] == "accepted":
            continue
        reason = str(label.get("reason") or "unknown")
        reasons[reason] = reasons.get(reason, 0) + 1
    manifest = {
        "schema_version": TRAINING_LABEL_SCHEMA,
        "created_utc": _utc_now(),
        "policy": "offline_training_only",
        "source_training_index": {
            "path": str(source_root),
            "dataset_release_id": index_manifest.get("dataset_release_id"),
            "samples_sha256": sha256_file(index_path),
        },
        "origin_wgs84": origin,
        "coordinate_frame": {"type": "enu", "origin_source": origin_source, "map_release": map_release_metadata},
        "requirements": {
            "min_gps_fix_type": min_gps_fix_type,
            "require_fix_type": require_fix_type,
            "max_gps_age_ms": max_gps_age_ms,
            "max_gps_horizontal_accuracy_m": max_gps_horizontal_accuracy_m,
            "require_gps_horizontal_accuracy": require_gps_horizontal_accuracy,
        },
        "label_path": labels_path.name,
        "label_sha256": sha256_file(labels_path),
        "sample_count": len(labels),
        "accepted_count": accepted,
        "rejected_count": len(labels) - accepted,
        "rejection_counts": reasons,
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"output_dir": str(root), "manifest_path": str(manifest_path), "manifest": manifest}


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Materialize offline-only GNSS/attitude ENU labels from a verified learned-navigation training index."
    )
    parser.add_argument("--index-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--origin-lat-deg", type=float)
    parser.add_argument("--origin-lon-deg", type=float)
    parser.add_argument("--origin-altitude-m", type=float)
    parser.add_argument("--min-gps-fix-type", type=int, default=3)
    parser.add_argument("--allow-missing-fix-type", action="store_true")
    parser.add_argument("--max-gps-age-ms", type=float, default=500.0)
    parser.add_argument("--max-gps-horizontal-accuracy-m", type=float, default=DEFAULT_MAX_GPS_HORIZONTAL_ACCURACY_M)
    parser.add_argument("--allow-missing-gps-horizontal-accuracy", action="store_true")
    parser.add_argument("--map-release", type=Path, help="MapRelease v2 with geodetic_origin_wgs84 for map-aligned labels.")
    parser.add_argument("--reference-map", type=Path, help="Model-independent ReferenceMapRelease for first training labels.")
    args = parser.parse_args()
    try:
        result = materialize_training_labels(
            args.index_dir,
            args.output_dir,
            origin_lat_deg=args.origin_lat_deg,
            origin_lon_deg=args.origin_lon_deg,
            origin_altitude_m=args.origin_altitude_m,
            min_gps_fix_type=args.min_gps_fix_type,
            require_fix_type=not args.allow_missing_fix_type,
            max_gps_age_ms=args.max_gps_age_ms,
            max_gps_horizontal_accuracy_m=args.max_gps_horizontal_accuracy_m,
            require_gps_horizontal_accuracy=not args.allow_missing_gps_horizontal_accuracy,
            map_release=args.map_release,
            reference_map=args.reference_map,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result["manifest"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
