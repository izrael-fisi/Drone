from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import shutil
from typing import Any

from vision_nav.build_terrain_bundle import build_terrain_bundle


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Build a terrain mission bundle from a saved map source folder."
    )
    parser.add_argument("--map-source", required=True, help="Folder containing satellite.png and metadata.json.")
    parser.add_argument("--bundle", required=True, help="Output mission_bundle directory.")
    parser.add_argument(
        "--repo",
        default=".",
        help="Drone repo path used to copy default calibration files. Defaults to current directory.",
    )
    parser.add_argument("--pipeline", default="classical", choices=["classical", "neural"])
    parser.add_argument("--feature-method", default="orb", choices=["orb", "akaze", "sift"])
    parser.add_argument("--max-features", type=int, default=3000)
    parser.add_argument("--mission-plan-json", help="Optional mission plan JSON file to include.")
    parser.add_argument("--qgc-plan-json", help="Optional QGroundControl .plan JSON file to include.")
    parser.add_argument("--write-checksums", action="store_true", help="Write checksums.sha256 after build.")
    return parser.parse_args()


def build_bundle_from_map_source(
    map_source: str | Path,
    bundle: str | Path,
    *,
    repo: str | Path = ".",
    pipeline: str = "classical",
    feature_method: str = "orb",
    max_features: int = 3000,
    mission_plan_json: str | Path | None = None,
    qgc_plan_json: str | Path | None = None,
    write_checksums: bool = False,
) -> dict[str, Any]:
    region_dir = Path(map_source).expanduser()
    bundle_dir = Path(bundle).expanduser()
    repo_path = Path(repo).expanduser()
    metadata = load_region_metadata(region_dir)
    validate_builder_options(pipeline, feature_method, max_features)

    satellite_path = region_dir / "satellite.png"
    metadata_path = region_dir / "metadata.json"
    if not satellite_path.is_file():
        raise FileNotFoundError(f"Missing map source satellite.png: {satellite_path}")
    if not metadata_path.is_file():
        raise FileNotFoundError(f"Missing map source metadata.json: {metadata_path}")

    ortho_dir = bundle_dir / "ortho"
    features_dir = bundle_dir / "features"
    calibration_dir = bundle_dir / "calibration"
    mission_dir = bundle_dir / "mission"
    high_compute_dir = bundle_dir / "high_compute_region"
    for directory in (ortho_dir, features_dir, calibration_dir, mission_dir, high_compute_dir):
        directory.mkdir(parents=True, exist_ok=True)

    orthophoto_path = ortho_dir / "map.png"
    shutil.copy2(satellite_path, orthophoto_path)
    shutil.copy2(satellite_path, high_compute_dir / "satellite.png")
    shutil.copy2(metadata_path, high_compute_dir / "metadata.json")
    copy_elevation_assets(region_dir, bundle_dir)

    calibration = copy_default_calibration(repo_path, calibration_dir)
    mission_plan_rel = copy_optional_json(mission_plan_json, mission_dir / "mission_plan.json", bundle_dir)
    qgc_plan_rel = copy_optional_json(qgc_plan_json, mission_dir / "qgc.plan", bundle_dir)

    manifest = {
        "bundle_id": "desktop-region",
        "description": "Mission bundle generated from a saved Drone desktop map source.",
        "version": "0.1.0",
        "coordinate_frame": "simple_local_tangent",
        "mission": {
            "desktop_plan_path": mission_plan_rel,
            "qgc_plan_path": qgc_plan_rel,
            "mavlink_upload_ready": qgc_plan_rel is not None,
        },
        "pipeline": {
            "selected": pipeline,
            "low_compute": {
                "name": "Classical ORB/AKAZE",
                "features_path": "features/map_features.npz",
            },
            "high_compute": {
                "name": "SuperPoint + LightGlue",
                "region_path": "high_compute_region",
            },
        },
        "terrain_bundle": {
            "version": "0.1.0",
            "tile_index_path": "index/tiles.sqlite",
            "tile_size_px": 512,
            "overlap_px": 64,
            "local_origin": {
                "latitude": metadata["origin_lat"],
                "longitude": metadata["origin_lon"],
                "east_m": 0.0,
                "north_m": 0.0,
            },
            "crs": metadata.get("georef_crs"),
            "gsd_m": metadata["gsd_m_per_px"],
            "coordinate_frame": "local_enu",
            "vertical_source": "barometer_optional",
            "sensors": {
                "barometer": {
                    "enabled_optional": True,
                    "source": "mavlink_or_replay",
                    "required": False,
                }
            },
            "runtime": "vision_imu_map",
        },
        "orthophoto": {
            "path": "ortho/map.png",
            "origin_lat": metadata["origin_lat"],
            "origin_lon": metadata["origin_lon"],
            "origin_pixel_x": metadata.get("origin_pixel_x", 0.0),
            "origin_pixel_y": metadata.get("origin_pixel_y", 0.0),
            "gsd_m": metadata["gsd_m_per_px"],
            "rotation_deg": metadata.get("rotation_deg", 0.0),
            "georef_source": metadata.get("georef_source") or "manual",
            "georef_confidence": metadata.get("georef_confidence", 1.0),
            "georef_crs": metadata.get("georef_crs"),
        },
        "features": {
            "path": "features/map_features.npz",
            "method": feature_method,
            "max_features": max_features,
        },
        "calibration": calibration,
        "source_region": {
            "path": str(region_dir),
            "metadata_path": "metadata.json",
            "origin_lat": metadata["origin_lat"],
            "origin_lon": metadata["origin_lon"],
            "gsd_m_per_px": metadata["gsd_m_per_px"],
            "width_px": metadata["width_px"],
            "height_px": metadata["height_px"],
            "zoom": metadata.get("zoom"),
            "source": metadata.get("source"),
            "original_file": metadata.get("original_file"),
            "georef_source": metadata.get("georef_source"),
            "georef_confidence": metadata.get("georef_confidence"),
            "georef_crs": metadata.get("georef_crs"),
        },
        "notes": [
            "Low-compute Pi runtime uses the classical feature index.",
            "Terrain runtime uses tiled map descriptors for coarse-to-local vision matching.",
            "High-compute runtimes can use high_compute_region/satellite.png and metadata.json with SuperPoint + LightGlue.",
        ],
        "generated_at": datetime.now(timezone.utc).isoformat(),
    }
    (bundle_dir / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    result = build_terrain_bundle(
        str(bundle_dir),
        method=feature_method,
        max_features=max_features,
        write_checksums=write_checksums,
    )
    result["map_source_dir"] = str(region_dir)
    result["source_metadata_path"] = str(metadata_path)
    result["bundle_manifest_path"] = str(bundle_dir / "manifest.json")
    result["mission_plan_path"] = str(bundle_dir / mission_plan_rel) if mission_plan_rel else None
    result["qgc_plan_path"] = str(bundle_dir / qgc_plan_rel) if qgc_plan_rel else None
    return result


def load_region_metadata(region_dir: Path) -> dict[str, Any]:
    metadata_path = region_dir / "metadata.json"
    if not metadata_path.is_file():
        raise FileNotFoundError(f"Missing map source metadata.json: {metadata_path}")
    metadata = json.loads(metadata_path.read_text(encoding="utf-8"))
    if not isinstance(metadata, dict):
        raise ValueError(f"metadata.json must contain an object: {metadata_path}")
    required = ("origin_lat", "origin_lon", "gsd_m_per_px", "width_px", "height_px")
    missing = [key for key in required if key not in metadata]
    if missing:
        raise ValueError(f"metadata.json missing required fields: {', '.join(missing)}")
    validate_lat_lon_gsd(metadata["origin_lat"], metadata["origin_lon"], metadata["gsd_m_per_px"])
    for key in ("width_px", "height_px"):
        value = int(metadata[key])
        if value <= 0:
            raise ValueError(f"{key} must be greater than zero: {value}")
        metadata[key] = value
    for key in ("origin_lat", "origin_lon", "gsd_m_per_px", "origin_pixel_x", "origin_pixel_y", "rotation_deg", "georef_confidence"):
        if key in metadata and metadata[key] is not None:
            metadata[key] = float(metadata[key])
    if "georef_confidence" in metadata and metadata["georef_confidence"] is not None:
        confidence = float(metadata["georef_confidence"])
        if not 0.0 <= confidence <= 1.0:
            raise ValueError(f"georef_confidence must be between 0 and 1: {confidence}")
    return metadata


def validate_lat_lon_gsd(origin_lat: Any, origin_lon: Any, gsd_m_per_px: Any) -> None:
    lat = float(origin_lat)
    lon = float(origin_lon)
    gsd = float(gsd_m_per_px)
    if not math.isfinite(lat) or not -90.0 <= lat <= 90.0:
        raise ValueError(f"origin_lat must be between -90 and 90 degrees: {origin_lat}")
    if not math.isfinite(lon) or not -180.0 <= lon <= 180.0:
        raise ValueError(f"origin_lon must be between -180 and 180 degrees: {origin_lon}")
    if not math.isfinite(gsd) or gsd <= 0.0:
        raise ValueError(f"gsd_m_per_px must be greater than zero: {gsd_m_per_px}")


def validate_builder_options(pipeline: str, feature_method: str, max_features: int) -> None:
    if pipeline not in {"classical", "neural"}:
        raise ValueError(f"Unsupported pipeline: {pipeline}")
    if feature_method not in {"orb", "akaze", "sift"}:
        raise ValueError(f"Unsupported feature method: {feature_method}")
    if int(max_features) <= 0:
        raise ValueError(f"max_features must be greater than zero: {max_features}")


def copy_default_calibration(repo_path: Path, calibration_dir: Path) -> dict[str, str]:
    calibration: dict[str, str] = {}
    candidates = {
        "down_camera": repo_path / "config" / "camera" / "down_camera.yaml",
        "camera_to_body": repo_path / "config" / "camera" / "camera_to_body.yaml",
    }
    for key, source in candidates.items():
        if not source.is_file():
            continue
        target = calibration_dir / source.name
        shutil.copy2(source, target)
        calibration[key] = f"calibration/{source.name}"
    return calibration


def copy_elevation_assets(region_dir: Path, bundle_dir: Path) -> None:
    target = bundle_dir / "elevation"
    if target.exists():
        shutil.rmtree(target)
    source = region_dir / "elevation"
    if source.is_dir():
        shutil.copytree(source, target)


def copy_optional_json(source: str | Path | None, target: Path, bundle_dir: Path) -> str | None:
    if source is None:
        return None
    source_path = Path(source).expanduser()
    raw = json.loads(source_path.read_text(encoding="utf-8"))
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(raw, indent=2) + "\n")
    return str(target.relative_to(bundle_dir)).replace("\\", "/")


def main() -> None:
    args = parse_args()
    result = build_bundle_from_map_source(
        args.map_source,
        args.bundle,
        repo=args.repo,
        pipeline=args.pipeline,
        feature_method=args.feature_method,
        max_features=args.max_features,
        mission_plan_json=args.mission_plan_json,
        qgc_plan_json=args.qgc_plan_json,
        write_checksums=args.write_checksums,
    )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
