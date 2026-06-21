from __future__ import annotations

import json
from pathlib import Path
from typing import Any


def load_manifest(bundle_path: str | Path) -> tuple[Path, dict[str, Any]]:
    bundle_dir = Path(bundle_path)
    manifest_path = bundle_dir / "manifest.json" if bundle_dir.is_dir() else bundle_dir
    if not manifest_path.exists():
        raise FileNotFoundError(f"Missing bundle manifest: {manifest_path}")
    manifest = json.loads(manifest_path.read_text())
    return manifest_path.parent, manifest


def resolve_bundle_path(bundle_dir: Path, relative_path: str) -> Path:
    path = Path(relative_path)
    if path.is_absolute():
        return path
    return bundle_dir / path


def manifest_orthophoto_path(bundle_dir: Path, manifest: dict[str, Any]) -> Path:
    try:
        return resolve_bundle_path(bundle_dir, manifest["orthophoto"]["path"])
    except KeyError as exc:
        raise KeyError("Manifest must contain orthophoto.path") from exc


def manifest_features_path(bundle_dir: Path, manifest: dict[str, Any]) -> Path:
    try:
        return resolve_bundle_path(bundle_dir, manifest["features"]["path"])
    except KeyError as exc:
        raise KeyError("Manifest must contain features.path") from exc


def manifest_georef(manifest: dict[str, Any]) -> dict[str, Any]:
    orthophoto = manifest.get("orthophoto", {})
    keys = [
        "origin_lat",
        "origin_lon",
        "gsd_m",
        "origin_pixel_x",
        "origin_pixel_y",
        "rotation_deg",
        "georef_source",
        "georef_confidence",
        "georef_crs",
    ]
    return {key: orthophoto[key] for key in keys if key in orthophoto}


def manifest_feature_options(manifest: dict[str, Any]) -> dict[str, Any]:
    features = manifest.get("features", {})
    return {
        "method": features.get("method", "orb"),
        "max_features": int(features.get("max_features", 3000)),
    }
