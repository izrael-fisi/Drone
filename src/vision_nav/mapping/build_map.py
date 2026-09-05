"""Offline map compiler entry point.

Input manifest: one JSON line per orthorectified satellite tile with id, center_enu_m,
resolution_m_per_px and image_uri.  Production deployments materialize embeddings and
feature tensors into versioned object storage, then atomically publish index.jsonl.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from .schema import TileRecord


def compile_map(manifest: Path, output: Path, model_version: str = "salad-v1") -> int:
    output.parent.mkdir(parents=True, exist_ok=True)
    rows: list[str] = []
    for raw in manifest.read_text().splitlines():
        item = json.loads(raw)
        tile_id = item["tile_id"]
        root = f"features/{model_version}/{tile_id}"
        record = TileRecord(tile_id=tile_id, center_enu_m=item["center_enu_m"],
            resolution_m_per_px=item["resolution_m_per_px"], image_uri=item["image_uri"],
            embedding_uri=f"{root}/global.npy", sparse_features_uri=f"{root}/sparse.npz",
            dense_features_uri=f"{root}/dense.npz", elevation_uri=item.get("elevation_uri"),
            semantic_histogram=item.get("semantic_histogram"), acquisition_date=item.get("acquisition_date"))
        rows.append(record.to_json())
    output.write_text("\n".join(rows) + ("\n" if rows else ""))
    return len(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Create an atomic visual map index from a tile manifest.")
    parser.add_argument("manifest", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--model-version", default="salad-v1")
    args = parser.parse_args()
    print(f"Compiled {compile_map(args.manifest, args.output, args.model_version)} map tiles")


if __name__ == "__main__":
    main()
