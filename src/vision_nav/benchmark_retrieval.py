from __future__ import annotations

import argparse
import json
from pathlib import Path
from statistics import mean
from typing import Any

import numpy as np

from vision_nav.terrain_bundle import TerrainBundle, load_terrain_bundle
from vision_nav.terrain_tiles import (
    NEURAL_RETRIEVAL_DESCRIPTOR_KEYS,
    TerrainTile,
    image_global_descriptor,
    load_all_tiles,
    load_retrieval_descriptor_file,
    load_tile_retrieval_descriptor,
    rank_tiles_by_global_descriptor,
    rank_tiles_by_retrieval_descriptor,
)


DEFAULT_TOP_K = (1, 5, 10)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Benchmark terrain tile retrieval descriptors on replay logs.")
    parser.add_argument("--bundle", required=True, help="Terrain bundle directory or manifest.json path.")
    parser.add_argument("--log", required=True, help="JSONL replay log with frame_path and ground-truth tile/local fields.")
    parser.add_argument("--top-k", default="1,5,10", help="Comma-separated top-k cutoffs. Default: 1,5,10.")
    parser.add_argument(
        "--backend",
        choices=["global", "neural", "all"],
        default="all",
        help="Retrieval backend to benchmark. Neural is reported as unavailable until neural descriptors are generated.",
    )
    parser.add_argument("--output", help="Optional JSON output path.")
    parser.add_argument("--json", action="store_true", help="Print JSON only.")
    return parser.parse_args()


def parse_top_k(value: str) -> list[int]:
    parsed = sorted({int(item.strip()) for item in value.split(",") if item.strip()})
    if not parsed or any(item <= 0 for item in parsed):
        raise ValueError("--top-k must contain positive integers")
    return parsed


def frame_path_from_record(record: dict[str, Any]) -> str | None:
    for key in ("frame_path", "frame", "image_path", "path"):
        value = record.get(key)
        if value:
            return str(value)
    nested = record.get("capture") if isinstance(record.get("capture"), dict) else {}
    return str(nested["frame_path"]) if nested.get("frame_path") else None


def resolve_frame_path(raw_path: str, *, log_path: Path) -> Path:
    path = Path(raw_path).expanduser()
    if path.is_absolute():
        return path
    candidate = log_path.parent / path
    return candidate if candidate.exists() else Path.cwd() / path


def load_frame_array(path: Path) -> np.ndarray:
    if path.suffix.lower() == ".npy":
        return np.load(path)
    try:
        import cv2  # type: ignore

        image = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
        if image is not None:
            return image
    except Exception:
        pass
    try:
        from PIL import Image  # type: ignore

        return np.asarray(Image.open(path).convert("L"))
    except Exception as exc:
        raise ValueError(f"Could not load frame image {path}. Install opencv-python-headless/Pillow or use .npy frames.") from exc


def expected_tile_id_from_record(record: dict[str, Any], tiles: list[TerrainTile]) -> str | None:
    for container in (record, record.get("ground_truth"), record.get("truth"), record.get("expected"), record.get("reference")):
        if not isinstance(container, dict):
            continue
        for key in ("expected_tile_id", "tile_id", "map_tile_id"):
            value = container.get(key)
            if value:
                return str(value)

    local = _nested_mapping(record, ("ground_truth", "truth", "expected", "reference"), "local_enu_m")
    if local is not None:
        east = local.get("x", local.get("east_m"))
        north = local.get("y", local.get("north_m"))
        if east is not None and north is not None:
            return tile_id_for_local_position(tiles, float(east), float(north))

    pixel = _nested_mapping(record, ("ground_truth", "truth", "expected", "reference"), "map_pixel")
    if pixel is None:
        pixel = _nested_mapping(record, ("ground_truth", "truth", "expected", "reference"), "estimated_map_pixel")
    if pixel is not None:
        x_px = pixel.get("x", pixel.get("x_px"))
        y_px = pixel.get("y", pixel.get("y_px"))
        if x_px is not None and y_px is not None:
            return tile_id_for_map_pixel(tiles, float(x_px), float(y_px))
    return None


def _nested_mapping(record: dict[str, Any], containers: tuple[str, ...], key: str) -> dict[str, Any] | None:
    direct = record.get(key)
    if isinstance(direct, dict):
        return direct
    for name in containers:
        container = record.get(name)
        if isinstance(container, dict) and isinstance(container.get(key), dict):
            return container[key]
    return None


def tile_id_for_local_position(tiles: list[TerrainTile], east_m: float, north_m: float) -> str | None:
    candidates: list[tuple[float, str]] = []
    for tile in tiles:
        if None in {tile.min_east_m, tile.max_east_m, tile.min_north_m, tile.max_north_m}:
            continue
        if float(tile.min_east_m) <= east_m <= float(tile.max_east_m) and float(tile.min_north_m) <= north_m <= float(tile.max_north_m):
            center = tile.center_local_m
            distance = 0.0 if center is None else float(np.hypot(center[0] - east_m, center[1] - north_m))
            candidates.append((distance, tile.tile_id))
    return sorted(candidates)[0][1] if candidates else None


def tile_id_for_map_pixel(tiles: list[TerrainTile], x_px: float, y_px: float) -> str | None:
    candidates: list[tuple[float, str]] = []
    for tile in tiles:
        if tile.x0_px <= x_px <= tile.x1_px and tile.y0_px <= y_px <= tile.y1_px:
            cx, cy = tile.center_px
            candidates.append((float(np.hypot(cx - x_px, cy - y_px)), tile.tile_id))
    return sorted(candidates)[0][1] if candidates else None


def benchmark_global_descriptor(
    bundle: TerrainBundle,
    records: list[dict[str, Any]],
    *,
    log_path: Path,
    top_k: list[int],
) -> dict[str, Any]:
    if bundle.tile_index_path is None or not bundle.tile_index_path.exists():
        return {"status": "failed", "reason": "bundle has no terrain tile index"}

    tiles = load_all_tiles(bundle.tile_index_path, bundle.bundle_dir)
    evaluations: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for sequence, record in enumerate(records, start=1):
        raw_frame_path = frame_path_from_record(record)
        expected_tile = expected_tile_id_from_record(record, tiles)
        if not raw_frame_path or not expected_tile:
            skipped.append(
                {
                    "sequence": sequence,
                    "reason": "missing_frame_path_or_expected_tile",
                    "has_frame_path": bool(raw_frame_path),
                    "has_expected_tile": bool(expected_tile),
                }
            )
            continue
        frame_path = resolve_frame_path(raw_frame_path, log_path=log_path)
        try:
            descriptor = image_global_descriptor(load_frame_array(frame_path))
        except Exception as exc:
            skipped.append({"sequence": sequence, "reason": f"frame_load_failed: {exc}", "frame_path": str(frame_path)})
            continue
        ranking = rank_tiles_by_global_descriptor(tiles, descriptor)
        rank = next((int(item["rank"]) for item in ranking if item["tile_id"] == expected_tile), None)
        evaluations.append(
            {
                "sequence": sequence,
                "frame_path": str(frame_path),
                "expected_tile_id": expected_tile,
                "rank": rank,
                "top_tile_id": ranking[0]["tile_id"] if ranking else None,
                "top_distance": ranking[0]["distance"] if ranking else None,
                "top_candidates": ranking[: min(max(top_k), len(ranking))],
            }
        )

    evaluated = len(evaluations)
    ranks = [int(item["rank"]) for item in evaluations if item.get("rank") is not None]
    top_k_hits = {
        str(k): sum(1 for rank in ranks if rank <= k)
        for k in top_k
    }
    recall_at_k = {
        key: (hits / evaluated if evaluated else 0.0)
        for key, hits in top_k_hits.items()
    }
    return {
        "status": "passed" if evaluated else "degraded",
        "descriptor": "grayscale_histogram_v1",
        "tile_count": len(tiles),
        "record_count": len(records),
        "evaluated_records": evaluated,
        "skipped_records": len(skipped),
        "top_k_hits": top_k_hits,
        "recall_at_k": recall_at_k,
        "mean_rank": mean(ranks) if ranks else None,
        "miss_count": evaluated - len(ranks),
        "records": evaluations,
        "skipped": skipped,
    }


def query_retrieval_descriptor_from_record(
    record: dict[str, Any],
    *,
    log_path: Path,
    descriptor_keys: tuple[str, ...] = NEURAL_RETRIEVAL_DESCRIPTOR_KEYS,
) -> tuple[np.ndarray | None, str | None]:
    containers = [record]
    for name in ("retrieval", "retrieval_descriptors", "descriptors", "query_descriptors"):
        value = record.get(name)
        if isinstance(value, dict):
            containers.append(value)

    for container in containers:
        for key in descriptor_keys:
            value = container.get(key)
            if isinstance(value, list):
                return _normalize_query_descriptor(value), key
        nested = container.get("neural")
        if isinstance(nested, list):
            return _normalize_query_descriptor(nested), "neural"
        if isinstance(nested, dict):
            for key in descriptor_keys:
                value = nested.get(key)
                if isinstance(value, list):
                    return _normalize_query_descriptor(value), f"neural.{key}"

    for key in (
        "neural_descriptor_path",
        "neural_global_descriptor_path",
        "query_neural_descriptor_path",
        "retrieval_descriptor_path",
    ):
        value = record.get(key)
        if not value:
            continue
        path = resolve_frame_path(str(value), log_path=log_path)
        try:
            descriptor = load_retrieval_descriptor_file(path, descriptor_keys=descriptor_keys)
        except Exception:
            descriptor = None
        if descriptor is not None:
            return descriptor, key
    return None, None


def _normalize_query_descriptor(value: list[Any]) -> np.ndarray:
    arr = np.asarray(value, dtype=np.float32).reshape(-1)
    norm = float(np.linalg.norm(arr))
    return arr / norm if norm > 0 else arr


def benchmark_precomputed_neural_descriptor(
    bundle: TerrainBundle,
    records: list[dict[str, Any]],
    *,
    log_path: Path,
    top_k: list[int],
) -> dict[str, Any]:
    if bundle.tile_index_path is None or not bundle.tile_index_path.exists():
        return {"status": "failed", "reason": "bundle has no terrain tile index"}

    tiles = load_all_tiles(bundle.tile_index_path, bundle.bundle_dir)
    tile_descriptor_count = sum(
        1
        for tile in tiles
        if load_tile_retrieval_descriptor(tile, descriptor_keys=NEURAL_RETRIEVAL_DESCRIPTOR_KEYS)[0] is not None
    )
    if tile_descriptor_count == 0:
        return {
            "status": "not_available",
            "descriptor": "precomputed_neural_retrieval_v1",
            "tile_count": len(tiles),
            "tile_descriptor_count": 0,
            "reason": "No precomputed neural retrieval descriptors were found in tile descriptor npz files or sibling sidecars.",
        }

    evaluations: list[dict[str, Any]] = []
    skipped: list[dict[str, Any]] = []
    for sequence, record in enumerate(records, start=1):
        expected_tile = expected_tile_id_from_record(record, tiles)
        query_descriptor, query_source = query_retrieval_descriptor_from_record(record, log_path=log_path)
        if expected_tile is None or query_descriptor is None:
            skipped.append(
                {
                    "sequence": sequence,
                    "reason": "missing_expected_tile_or_query_descriptor",
                    "has_expected_tile": bool(expected_tile),
                    "has_query_descriptor": query_descriptor is not None,
                }
            )
            continue
        ranking = rank_tiles_by_retrieval_descriptor(
            tiles,
            query_descriptor,
            descriptor_keys=NEURAL_RETRIEVAL_DESCRIPTOR_KEYS,
        )
        if not ranking:
            skipped.append(
                {
                    "sequence": sequence,
                    "reason": "no_compatible_tile_descriptors",
                    "has_expected_tile": True,
                    "has_query_descriptor": True,
                }
            )
            continue
        rank = next((int(item["rank"]) for item in ranking if item["tile_id"] == expected_tile), None)
        evaluations.append(
            {
                "sequence": sequence,
                "expected_tile_id": expected_tile,
                "query_descriptor_source": query_source,
                "rank": rank,
                "top_tile_id": ranking[0]["tile_id"] if ranking else None,
                "top_distance": ranking[0]["distance"] if ranking else None,
                "top_candidates": ranking[: min(max(top_k), len(ranking))],
            }
        )

    evaluated = len(evaluations)
    ranks = [int(item["rank"]) for item in evaluations if item.get("rank") is not None]
    top_k_hits = {str(k): sum(1 for rank in ranks if rank <= k) for k in top_k}
    recall_at_k = {key: (hits / evaluated if evaluated else 0.0) for key, hits in top_k_hits.items()}
    return {
        "status": "passed" if evaluated else "degraded",
        "descriptor": "precomputed_neural_retrieval_v1",
        "tile_count": len(tiles),
        "tile_descriptor_count": tile_descriptor_count,
        "record_count": len(records),
        "evaluated_records": evaluated,
        "skipped_records": len(skipped),
        "top_k_hits": top_k_hits,
        "recall_at_k": recall_at_k,
        "mean_rank": mean(ranks) if ranks else None,
        "miss_count": evaluated - len(ranks),
        "records": evaluations,
        "skipped": skipped,
    }


def benchmark_retrieval(bundle_path: str | Path, log_path: str | Path, *, top_k: list[int] | None = None, backend: str = "all") -> dict[str, Any]:
    bundle = load_terrain_bundle(bundle_path)
    log = Path(log_path)
    top_k = top_k or list(DEFAULT_TOP_K)
    records = [json.loads(line) for line in log.read_text().splitlines() if line.strip()]
    backends: dict[str, Any] = {}
    if backend in {"global", "all"}:
        backends["global_histogram_v1"] = benchmark_global_descriptor(bundle, records, log_path=log, top_k=top_k)
    if backend in {"neural", "all"}:
        backends["neural"] = benchmark_precomputed_neural_descriptor(bundle, records, log_path=log, top_k=top_k)
    return {
        "bundle_dir": str(bundle.bundle_dir),
        "bundle_id": bundle.manifest.get("bundle_id"),
        "log": str(log),
        "top_k": top_k,
        "backend": backend,
        "record_count": len(records),
        "backends": backends,
    }


def print_human(report: dict[str, Any]) -> None:
    print(f"Retrieval benchmark: {report.get('bundle_id') or '(unnamed)'}")
    for name, backend in report["backends"].items():
        print(f"{name}: {backend.get('status')}")
        if backend.get("status") == "not_available":
            print(f"  {backend.get('reason')}")
            continue
        recall = backend.get("recall_at_k") or {}
        print(f"  evaluated: {backend.get('evaluated_records')}/{backend.get('record_count')}")
        print(f"  mean rank: {backend.get('mean_rank')}")
        for key, value in recall.items():
            print(f"  recall@{key}: {value:.3f}")


def main() -> None:
    args = parse_args()
    report = benchmark_retrieval(args.bundle, args.log, top_k=parse_top_k(args.top_k), backend=args.backend)
    if args.output:
        Path(args.output).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human(report)
    if any(backend.get("status") == "failed" for backend in report["backends"].values()):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
