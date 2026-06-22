from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
from typing import Any

from vision_nav.bundle import load_manifest, manifest_features_path, manifest_orthophoto_path, resolve_bundle_path


REQUIRED_TERRAIN_BUNDLE_FILES = (
    "manifest.json",
    "ortho/map.png",
    "features/map_features.npz",
    "index/tiles.sqlite",
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Diagnose missing or invalid terrain mission bundle inputs.")
    parser.add_argument("--bundle", required=True, help="Expected mission_bundle directory.")
    parser.add_argument(
        "--search-root",
        action="append",
        default=[],
        help="Additional folder to scan for mission bundles or raw Mission Planner map sources.",
    )
    parser.add_argument("--json", action="store_true", help="Emit JSON only.")
    return parser.parse_args()


def diagnose_bundle_inputs(
    bundle_path: str | Path,
    *,
    search_roots: list[str | Path] | None = None,
    max_candidates: int = 8,
) -> dict[str, Any]:
    bundle = Path(bundle_path).expanduser()
    roots = default_search_roots(bundle, search_roots)
    required_files = [
        {
            "path": str(bundle / relative),
            "relative_path": relative,
            "exists": (bundle / relative).exists(),
        }
        for relative in REQUIRED_TERRAIN_BUNDLE_FILES
    ]
    bundle_candidates = find_bundle_candidates(roots, expected_bundle=bundle, max_candidates=max_candidates)
    map_source_candidates = find_map_source_candidates(roots, max_candidates=max_candidates)

    actions = recommended_actions(bundle, bundle_candidates, map_source_candidates)
    return {
        "schema_version": "vision_nav_bundle_input_diagnostic_v1",
        "bundle_path": str(bundle),
        "bundle_exists": bundle.exists(),
        "required_files": required_files,
        "missing_required_files": [item["relative_path"] for item in required_files if not item["exists"]],
        "search_roots": [str(root) for root in roots],
        "bundle_candidates": bundle_candidates,
        "map_source_candidates": map_source_candidates,
        "recommended_actions": actions,
    }


def compact_bundle_diagnostic(report: Any, *, max_items: int = 3) -> dict[str, Any] | None:
    if not isinstance(report, dict):
        return None
    bundle_candidates = [
        {
            "path": item.get("path"),
            "bundle_id": item.get("bundle_id"),
            "tile_index_exists": item.get("tile_index_exists"),
            "field_proof_warning": item.get("field_proof_warning"),
        }
        for item in report.get("bundle_candidates") or []
        if isinstance(item, dict)
    ]
    map_sources = [
        {
            "path": item.get("path"),
            "name": item.get("name"),
            "source": item.get("source"),
            "georef_source": item.get("georef_source"),
        }
        for item in report.get("map_source_candidates") or []
        if isinstance(item, dict)
    ]
    actions = [
        {
            "id": item.get("id"),
            "status": item.get("status"),
            "title": item.get("title"),
            "desktop_action": item.get("desktop_action"),
            "command": item.get("command"),
            "bundle_path": item.get("bundle_path"),
            "map_source_path": item.get("map_source_path"),
        }
        for item in report.get("recommended_actions") or []
        if isinstance(item, dict)
    ]
    return {
        "bundle_exists": report.get("bundle_exists"),
        "missing_required_files": report.get("missing_required_files") or [],
        "bundle_candidate_count": len(bundle_candidates),
        "map_source_candidate_count": len(map_sources),
        "bundle_candidates": bundle_candidates[:max_items],
        "map_source_candidates": map_sources[:max_items],
        "recommended_actions": actions[:max_items],
    }


def default_search_roots(bundle: Path, extra_roots: list[str | Path] | None = None) -> list[Path]:
    roots: list[Path] = []
    env_roots = os.environ.get("VISION_NAV_BUNDLE_SEARCH_ROOTS")
    if env_roots:
        roots.extend(Path(item).expanduser() for item in env_roots.split(os.pathsep) if item.strip())
    if extra_roots:
        roots.extend(Path(item).expanduser() for item in extra_roots)
    home = Path.home()
    roots.extend(
        [
            bundle.parent,
            home / "drone-data" / "map_bundles",
            home / "DroneTransfer" / "to-pi",
            home / "DroneTransfer" / "outgoing",
            home / "DroneTransfer" / "from-pi",
            Path.cwd() / "map_bundles",
            Path.cwd() / "transfer",
        ]
    )

    deduped: list[Path] = []
    seen: set[str] = set()
    for root in roots:
        normalized = str(root)
        if normalized not in seen:
            seen.add(normalized)
            deduped.append(root)
    return deduped


def find_bundle_candidates(roots: list[Path], *, expected_bundle: Path, max_candidates: int) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for manifest_path in scan_for_files(roots, "manifest.json", max_depth=5):
        bundle_dir = manifest_path.parent
        normalized = str(bundle_dir)
        if normalized in seen:
            continue
        seen.add(normalized)
        summary = summarize_bundle_candidate(bundle_dir, expected_bundle=expected_bundle)
        if summary is not None:
            candidates.append(summary)
    candidates.sort(key=lambda item: (not item.get("is_expected_path"), item.get("field_proof_warning") is not None, item["path"]))
    return candidates[:max_candidates]


def find_map_source_candidates(roots: list[Path], *, max_candidates: int) -> list[dict[str, Any]]:
    candidates: list[dict[str, Any]] = []
    seen: set[str] = set()
    for metadata_path in scan_for_files(roots, "metadata.json", max_depth=5):
        source_dir = metadata_path.parent
        normalized = str(source_dir)
        if normalized in seen or not (source_dir / "satellite.png").exists():
            continue
        seen.add(normalized)
        candidates.append(summarize_map_source_candidate(source_dir))
    candidates.sort(key=lambda item: item["path"])
    return candidates[:max_candidates]


def scan_for_files(roots: list[Path], filename: str, *, max_depth: int) -> list[Path]:
    found: list[Path] = []
    for root in roots:
        if not root.exists() or not root.is_dir():
            continue
        try:
            root_resolved = root.resolve()
        except OSError:
            root_resolved = root
        for current, dirs, files in os.walk(root):
            current_path = Path(current)
            try:
                relative_depth = len(current_path.resolve().relative_to(root_resolved).parts)
            except Exception:
                relative_depth = len(current_path.relative_to(root).parts)
            if relative_depth >= max_depth:
                dirs[:] = []
            dirs[:] = [item for item in dirs if item not in {".git", "node_modules", "target", "__pycache__"}]
            if filename in files:
                found.append(current_path / filename)
    return found


def summarize_bundle_candidate(bundle_dir: Path, *, expected_bundle: Path) -> dict[str, Any] | None:
    try:
        loaded_dir, manifest = load_manifest(bundle_dir)
    except Exception:
        return None
    terrain = manifest.get("terrain_bundle") if isinstance(manifest.get("terrain_bundle"), dict) else {}
    source_region = manifest.get("source_region") if isinstance(manifest.get("source_region"), dict) else {}
    orthophoto_path = safe_manifest_path(loaded_dir, manifest, "orthophoto")
    features_path = safe_manifest_path(loaded_dir, manifest, "features")
    tile_index_rel = terrain.get("tile_index_path") if isinstance(terrain, dict) else None
    tile_index_path = resolve_bundle_path(loaded_dir, str(tile_index_rel)) if tile_index_rel else loaded_dir / "index" / "tiles.sqlite"
    bundle_id = str(manifest.get("bundle_id") or loaded_dir.name)
    warning = field_proof_warning(loaded_dir, bundle_id)
    return {
        "path": str(loaded_dir),
        "bundle_id": bundle_id,
        "is_expected_path": loaded_dir == expected_bundle,
        "orthophoto_exists": bool(orthophoto_path and orthophoto_path.exists()),
        "feature_index_exists": bool(features_path and features_path.exists()),
        "terrain_metadata_present": bool(terrain),
        "tile_index_exists": tile_index_path.exists(),
        "tile_count": terrain.get("tile_count") if isinstance(terrain, dict) else None,
        "feature_count": terrain.get("feature_count") if isinstance(terrain, dict) else None,
        "map_source": source_region.get("source") if isinstance(source_region, dict) else None,
        "map_name": source_region.get("name") if isinstance(source_region, dict) else None,
        "field_proof_warning": warning,
    }


def safe_manifest_path(bundle_dir: Path, manifest: dict[str, Any], kind: str) -> Path | None:
    try:
        if kind == "orthophoto":
            return manifest_orthophoto_path(bundle_dir, manifest)
        if kind == "features":
            return manifest_features_path(bundle_dir, manifest)
    except Exception:
        return None
    return None


def summarize_map_source_candidate(source_dir: Path) -> dict[str, Any]:
    metadata_path = source_dir / "metadata.json"
    metadata: dict[str, Any] = {}
    try:
        parsed = json.loads(metadata_path.read_text(encoding="utf-8"))
        if isinstance(parsed, dict):
            metadata = parsed
    except Exception:
        metadata = {}
    return {
        "path": str(source_dir),
        "name": metadata.get("name") or metadata.get("id") or source_dir.name,
        "source": metadata.get("source"),
        "origin_lat": metadata.get("origin_lat"),
        "origin_lon": metadata.get("origin_lon"),
        "gsd_m_per_px": metadata.get("gsd_m_per_px"),
        "georef_source": metadata.get("georef_source"),
        "georef_confidence": metadata.get("georef_confidence"),
        "georef_crs": metadata.get("georef_crs"),
        "width_px": metadata.get("width_px"),
        "height_px": metadata.get("height_px"),
        "has_satellite_png": (source_dir / "satellite.png").exists(),
        "has_metadata_json": metadata_path.exists(),
    }


def field_proof_warning(bundle_dir: Path, bundle_id: str) -> str | None:
    text = f"{bundle_dir} {bundle_id}".lower()
    if any(marker in text for marker in ("example", "synthetic", "smoke")):
        return "Example or synthetic bundles are useful for tooling smoke tests but do not satisfy real field evidence."
    return None


def recommended_actions(
    bundle: Path,
    bundle_candidates: list[dict[str, Any]],
    map_source_candidates: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = [
        {
            "id": "build_or_upload_selected_bundle",
            "status": "action_required",
            "title": "Build and upload the selected Mission Planner terrain bundle.",
            "desktop_action": "Mission Planner > Build Bundle, Upload Bundle",
            "command": f"VISION_NAV_BUNDLE={shell_quote(str(bundle))} ./scripts/pi/validate_terrain_bundle.sh",
        }
    ]
    usable_bundle = next((item for item in bundle_candidates if not item.get("field_proof_warning")), None)
    if usable_bundle:
        actions.append(
            {
                "id": "validate_candidate_bundle",
                "status": "optional",
                "title": "Validate a detected terrain bundle candidate.",
                "command": f"VISION_NAV_BUNDLE={shell_quote(str(usable_bundle['path']))} ./scripts/pi/validate_terrain_bundle.sh",
                "bundle_path": usable_bundle["path"],
            }
        )
    if map_source_candidates:
        actions.append(
            {
                "id": "build_from_detected_map_source",
                "status": "optional",
                "title": "Use a detected saved map source to build the runtime bundle.",
                "desktop_action": "Mission Planner > Select Map Source, Build Bundle, Upload Bundle",
                "map_source_path": map_source_candidates[0]["path"],
            }
        )
    else:
        actions.append(
            {
                "id": "import_map_source",
                "status": "action_required",
                "title": "Import or create a georeferenced map source before building the mission bundle.",
                "desktop_action": "Maps > Import Map, then Mission Planner > Build Bundle",
            }
        )
    return actions


def shell_quote(value: str) -> str:
    import shlex

    return shlex.quote(value)


def print_human(report: dict[str, Any]) -> None:
    print(f"Bundle input diagnostic: {report['bundle_path']}")
    print(f"Bundle exists: {'yes' if report.get('bundle_exists') else 'no'}")
    missing = report.get("missing_required_files") or []
    if missing:
        print(f"Missing required files: {', '.join(str(item) for item in missing)}")
    else:
        print("Required files: present")
    if report.get("bundle_candidates"):
        print("Detected bundle candidates:")
        for item in report["bundle_candidates"]:
            suffix = " (field-proof warning)" if item.get("field_proof_warning") else ""
            print(f"- {item.get('path')} [{item.get('bundle_id')}]{suffix}")
    if report.get("map_source_candidates"):
        print("Detected map sources:")
        for item in report["map_source_candidates"]:
            print(f"- {item.get('path')} [{item.get('name') or 'unnamed'}]")
    print("Recommended actions:")
    for action in report.get("recommended_actions") or []:
        print(f"- {action.get('id')}: {action.get('title')}")
        if action.get("desktop_action"):
            print(f"  app: {action['desktop_action']}")
        if action.get("command"):
            print(f"  command: {action['command']}")


def main() -> None:
    args = parse_args()
    report = diagnose_bundle_inputs(args.bundle, search_roots=args.search_root)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human(report)


if __name__ == "__main__":
    main()
