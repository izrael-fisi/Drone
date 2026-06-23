from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from vision_nav.terrain_bundle import summarize_terrain_bundle
from vision_nav.validate_map_bundle import validate_bundle


SCHEMA_VERSION = "vision_nav_terrain_bundle_validation_v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a durable terrain bundle validation report.")
    parser.add_argument("--bundle", required=True, help="Bundle directory or manifest.json path.")
    parser.add_argument("--output", help="Optional JSON report path.")
    parser.add_argument("--json", action="store_true", help="Print the full JSON report.")
    return parser.parse_args()


def create_terrain_bundle_validation_report(
    bundle: str | Path,
    *,
    output_path: str | Path | None = None,
) -> dict[str, Any]:
    bundle_path = Path(bundle).expanduser()
    issues: list[dict[str, Any]] = []
    map_summary: dict[str, Any] | None = None
    terrain_summary: dict[str, Any] | None = None

    try:
        map_summary = validate_bundle(str(bundle_path), require_features=True)
        issues.extend(source_issues("map_bundle", map_summary.get("issues") or []))
    except Exception as exc:
        issues.append({"source": "map_bundle", "severity": "error", "message": str(exc)})

    try:
        terrain_summary = summarize_terrain_bundle(bundle_path)
        issues.extend(source_issues("terrain_bundle", terrain_summary.get("issues") or []))
    except Exception as exc:
        issues.append({"source": "terrain_bundle", "severity": "error", "message": str(exc)})

    map_status = map_summary.get("status") if map_summary else "failed"
    terrain_status = terrain_summary.get("status") if terrain_summary else "failed"
    status = "failed" if map_status != "passed" or terrain_status != "passed" else "passed"
    report: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "bundle_path": str(bundle_path),
        "map_bundle_status": map_status,
        "terrain_bundle_status": terrain_status,
        "summary": validation_summary(map_summary, terrain_summary),
        "issues": issues,
        "map_bundle": map_summary,
        "terrain_bundle": terrain_summary,
    }
    if output_path is not None:
        destination = Path(output_path).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        report["report_path"] = str(destination)
        destination.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return report


def source_issues(source: str, raw_issues: list[Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for issue in raw_issues:
        if not isinstance(issue, dict):
            continue
        normalized = dict(issue)
        normalized.setdefault("source", source)
        issues.append(normalized)
    return issues


def validation_summary(
    map_summary: dict[str, Any] | None,
    terrain_summary: dict[str, Any] | None,
) -> dict[str, Any]:
    feature_index = (map_summary or {}).get("feature_index") or {}
    geospatial_health = (terrain_summary or {}).get("geospatial_health") or {}
    map_quality = geospatial_health.get("map_quality") or {}
    return {
        "bundle_id": (map_summary or {}).get("bundle_id") or (terrain_summary or {}).get("bundle_id"),
        "tile_count": (terrain_summary or {}).get("tile_count"),
        "feature_count": (terrain_summary or {}).get("feature_count") or feature_index.get("keypoints"),
        "feature_method": feature_index.get("method"),
        "gsd_m": (terrain_summary or {}).get("gsd_m"),
        "crs": (terrain_summary or {}).get("crs"),
        "has_tile_index": (terrain_summary or {}).get("has_tile_index"),
        "checksum_status": ((map_summary or {}).get("checksums") or {}).get("status"),
        "geospatial_health_status": geospatial_health.get("status"),
        "estimated_pi_runtime_cost": map_quality.get("estimated_pi_runtime_cost"),
    }


def print_human(report: dict[str, Any]) -> None:
    summary = report.get("summary") or {}
    print(f"Terrain bundle validation: {summary.get('bundle_id') or '(unnamed)'}")
    print(f"Bundle: {report['bundle_path']}")
    print(f"Status: {report['status']}")
    print(f"Map bundle: {report.get('map_bundle_status')}")
    print(f"Terrain bundle: {report.get('terrain_bundle_status')}")
    print(f"Tiles: {summary.get('tile_count') or 0}")
    print(f"Features: {summary.get('feature_count') or 0}")
    if summary.get("estimated_pi_runtime_cost"):
        print(f"Pi runtime cost: {summary['estimated_pi_runtime_cost']}")
    if report.get("report_path"):
        print(f"Report: {report['report_path']}")
    for issue in report.get("issues") or []:
        source = issue.get("source") or "bundle"
        severity = str(issue.get("severity") or "unknown").upper()
        print(f"[{severity}] {source}: {issue.get('message')}")


def main() -> None:
    args = parse_args()
    report = create_terrain_bundle_validation_report(args.bundle, output_path=args.output)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human(report)
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
