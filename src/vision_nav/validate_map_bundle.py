from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import numpy as np
import yaml

from vision_nav.bundle import (
    load_manifest,
    manifest_feature_options,
    manifest_features_path,
    manifest_georef,
    manifest_orthophoto_path,
    resolve_bundle_path,
)
from vision_nav.camera import load_camera_calibration
from vision_nav.bundle_checksums import CHECKSUM_FILENAME, verify_checksum_file
from vision_nav.geospatial_health import geospatial_health_report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Validate a prototype vision map bundle.")
    parser.add_argument("--bundle", required=True, help="Bundle directory or manifest.json path.")
    parser.add_argument("--require-features", action="store_true", help="Fail if the feature index is missing.")
    parser.add_argument("--require-calibration", action="store_true", help="Fail if calibration files are missing.")
    parser.add_argument("--require-checksums", action="store_true", help="Fail if checksums are missing or invalid.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    return parser.parse_args()


def add_issue(issues: list[dict[str, str]], severity: str, message: str) -> None:
    issues.append({"severity": severity, "message": message})


def validate_georef(manifest: dict[str, Any], issues: list[dict[str, str]]) -> dict[str, Any]:
    georef = manifest_georef(manifest)
    core = ["origin_lat", "origin_lon", "gsd_m"]
    present = [key for key in core if key in georef]
    if present and len(present) != len(core):
        add_issue(issues, "error", "Georef requires origin_lat, origin_lon, and gsd_m together.")
        return georef
    if not present:
        add_issue(issues, "warning", "No georef found; matcher will not emit lat/lon or local ENU measurements.")
        return georef

    lat = float(georef["origin_lat"])
    lon = float(georef["origin_lon"])
    gsd = float(georef["gsd_m"])
    if not -90.0 <= lat <= 90.0:
        add_issue(issues, "error", f"origin_lat out of range: {lat}")
    if not -180.0 <= lon <= 180.0:
        add_issue(issues, "error", f"origin_lon out of range: {lon}")
    if gsd <= 0:
        add_issue(issues, "error", f"gsd_m must be greater than zero: {gsd}")
    if "georef_confidence" in georef:
        confidence = float(georef["georef_confidence"])
        if not 0.0 <= confidence <= 1.0:
            add_issue(issues, "error", f"georef_confidence must be between 0 and 1: {confidence}")
    return georef


def validate_feature_index(
    features_path: Path,
    feature_options: dict[str, Any],
    require_features: bool,
    issues: list[dict[str, str]],
) -> dict[str, Any] | None:
    if not features_path.exists():
        severity = "error" if require_features else "warning"
        add_issue(issues, severity, f"Feature index is missing: {features_path}")
        return None

    with np.load(features_path, allow_pickle=False) as data:
        required_keys = ["method", "keypoints_xy", "descriptors", "image_shape"]
        missing = [key for key in required_keys if key not in data.files]
        if missing:
            add_issue(issues, "error", f"Feature index missing keys: {', '.join(missing)}")
            return None

        method = str(data["method"])
        keypoints_xy = data["keypoints_xy"]
        descriptors = data["descriptors"]
        if method != feature_options["method"]:
            add_issue(
                issues,
                "warning",
                f"Feature method mismatch: manifest={feature_options['method']} index={method}",
            )
        if keypoints_xy.ndim != 2 or keypoints_xy.shape[1] != 2:
            add_issue(issues, "error", f"keypoints_xy must have shape Nx2, got {keypoints_xy.shape}")
        if len(descriptors) != len(keypoints_xy):
            add_issue(
                issues,
                "error",
                f"Descriptor/keypoint count mismatch: descriptors={len(descriptors)} keypoints={len(keypoints_xy)}",
            )
        return {
            "method": method,
            "keypoints": int(len(keypoints_xy)),
            "descriptor_shape": list(descriptors.shape),
            "image_shape": [int(value) for value in data["image_shape"]],
        }


def validate_calibration(
    bundle_dir: Path,
    manifest: dict[str, Any],
    require_calibration: bool,
    issues: list[dict[str, str]],
) -> dict[str, Any]:
    calibration = manifest.get("calibration") or {}
    summary: dict[str, Any] = {}
    if not calibration:
        severity = "error" if require_calibration else "warning"
        add_issue(issues, severity, "Manifest has no calibration section.")
        return summary

    down_camera = calibration.get("down_camera")
    if down_camera:
        path = resolve_bundle_path(bundle_dir, down_camera)
        try:
            loaded = load_camera_calibration(path)
            summary["down_camera"] = loaded.to_log_dict()
        except Exception as exc:
            add_issue(issues, "error", f"Invalid down_camera calibration {path}: {exc}")
    elif require_calibration:
        add_issue(issues, "error", "Missing calibration.down_camera")

    camera_to_body = calibration.get("camera_to_body")
    if camera_to_body:
        path = resolve_bundle_path(bundle_dir, camera_to_body)
        if not path.exists():
            add_issue(issues, "error", f"Missing camera_to_body calibration: {path}")
        else:
            raw = yaml.safe_load(path.read_text())
            if not isinstance(raw, dict):
                add_issue(issues, "error", f"camera_to_body is not a YAML mapping: {path}")
            else:
                summary["camera_to_body"] = {
                    "path": str(path),
                    "frame_id": raw.get("frame_id"),
                    "child_frame_id": raw.get("child_frame_id"),
                }
    elif require_calibration:
        add_issue(issues, "error", "Missing calibration.camera_to_body")

    return summary


def validate_checksums(
    bundle: str,
    require_checksums: bool,
    issues: list[dict[str, str]],
) -> dict[str, Any]:
    try:
        summary = verify_checksum_file(bundle)
    except Exception as exc:
        add_issue(issues, "error", f"Invalid {CHECKSUM_FILENAME}: {exc}")
        return {"status": "failed", "error": str(exc)}

    if summary["status"] == "missing":
        severity = "error" if require_checksums else "warning"
        add_issue(issues, severity, f"Missing {CHECKSUM_FILENAME}; run vision-nav-bundle-checksums --write.")
    elif summary["status"] != "passed":
        add_issue(issues, "error", f"Checksum verification failed: {summary['checksum_file']}")
    elif summary.get("extra_files"):
        add_issue(issues, "warning", f"Bundle has files not covered by {CHECKSUM_FILENAME}.")
    return summary


def validate_bundle(
    bundle: str,
    require_features: bool = False,
    require_calibration: bool = False,
    require_checksums: bool = False,
) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    bundle_dir, manifest = load_manifest(bundle)
    summary: dict[str, Any] = {
        "bundle_dir": str(bundle_dir),
        "bundle_id": manifest.get("bundle_id"),
        "issues": issues,
    }

    try:
        orthophoto_path = manifest_orthophoto_path(bundle_dir, manifest)
        summary["orthophoto_path"] = str(orthophoto_path)
        if not orthophoto_path.exists():
            add_issue(issues, "error", f"Missing orthophoto image: {orthophoto_path}")
    except Exception as exc:
        add_issue(issues, "error", f"Invalid orthophoto path: {exc}")

    try:
        features_path = manifest_features_path(bundle_dir, manifest)
        feature_options = manifest_feature_options(manifest)
        summary["features_path"] = str(features_path)
        summary["feature_options"] = feature_options
        if feature_options["method"] not in {"orb", "akaze", "sift"}:
            add_issue(issues, "error", f"Unsupported feature method: {feature_options['method']}")
        if int(feature_options["max_features"]) <= 0:
            add_issue(issues, "error", f"max_features must be greater than zero: {feature_options['max_features']}")
        summary["feature_index"] = validate_feature_index(features_path, feature_options, require_features, issues)
    except Exception as exc:
        add_issue(issues, "error", f"Invalid feature config: {exc}")

    summary["georef"] = validate_georef(manifest, issues)
    if manifest.get("terrain_bundle"):
        try:
            summary["geospatial_health"] = geospatial_health_report(bundle)
            for issue in summary["geospatial_health"].get("issues", []):
                if issue not in issues:
                    issues.append(issue)
        except Exception as exc:
            add_issue(issues, "error", f"Invalid geospatial bundle health: {exc}")
    summary["calibration"] = validate_calibration(bundle_dir, manifest, require_calibration, issues)
    summary["checksums"] = validate_checksums(bundle, require_checksums, issues)
    summary["status"] = "failed" if any(issue["severity"] == "error" for issue in issues) else "passed"
    return summary


def print_human(summary: dict[str, Any]) -> None:
    print(f"Bundle: {summary.get('bundle_id') or '(unnamed)'}")
    print(f"Directory: {summary['bundle_dir']}")
    print(f"Status: {summary['status']}")
    print(f"Orthophoto: {summary.get('orthophoto_path')}")
    print(f"Features: {summary.get('features_path')}")
    feature_index = summary.get("feature_index")
    if feature_index:
        print(f"Feature index: {feature_index['keypoints']} keypoints, method={feature_index['method']}")
    for issue in summary["issues"]:
        print(f"[{issue['severity'].upper()}] {issue['message']}")


def main() -> None:
    args = parse_args()
    summary = validate_bundle(
        args.bundle,
        require_features=args.require_features,
        require_calibration=args.require_calibration,
        require_checksums=args.require_checksums,
    )
    if args.json:
        print(json.dumps(summary, indent=2, sort_keys=True))
    else:
        print_human(summary)
    if summary["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
