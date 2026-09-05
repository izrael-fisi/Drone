"""Release-bound map indexing and automatic candidates for camera replay.

The index reuses the production retrieval metadata/reader, but is explicitly a
research artifact. No local-feature or vehicle ModelRelease is fabricated.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from vision_nav.training_capture import sha256_file
from .map_asset_compiler import _read_layout, _tile_scale
from .reference_map import load_reference_map
from .retrieval_export import RetrievalOnnxProbe, export_member
from .retrieval_index import RETRIEVAL_METADATA_SCHEMA, load_tile_retriever


REPLAY_RETRIEVAL_SCHEMA = "vision_nav_replay_retrieval_v1"
MAX_INDEX_BYTES = 256 * 1024 * 1024


def _map_layout(reference_path: Path, layout_path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    reference = load_reference_map(reference_path)
    _, layout = _read_layout(layout_path)
    if (layout.get("geodetic_origin_wgs84") != reference["geodetic_origin_wgs84"]
            or layout.get("cog", {}).get("sha256") != reference["checksums_sha256"][reference["imagery_uri"]]
            or layout.get("tile_count") != len(layout["tiles"])
            or len({t["tile_id"] for t in layout["tiles"]}) != len(layout["tiles"])):
        raise ValueError("Retrieval tile layout does not match reference map")
    return reference, layout


def build_replay_retrieval_index(
    export_dir: str | Path, reference_map: str | Path, tile_layout: str | Path, output_dir: str | Path,
) -> dict[str, Any]:
    reference_path, layout_path, output = [Path(p).expanduser().resolve() for p in (reference_map, tile_layout, output_dir)]
    if output.exists() and any(output.iterdir()):
        raise ValueError("Retrieval index output must be empty")
    probe = RetrievalOnnxProbe(export_dir)
    reference, layout = _map_layout(reference_path, layout_path)
    nbytes = len(layout["tiles"]) * probe.dimension * 4
    if nbytes > MAX_INDEX_BYTES:
        raise ValueError("Replay retrieval index exceeds 256 MiB embedding budget; select a smaller map area")
    embeddings = np.empty((len(layout["tiles"]), probe.dimension), dtype=np.float32)
    records = []
    for index, tile in enumerate(layout["tiles"]):
        path = export_member(layout_path.parent, tile["image_uri"])
        if sha256_file(path) != tile.get("image_sha256"):
            raise ValueError("Retrieval tile image checksum mismatch")
        image = cv2.imread(str(path))
        if image is None or list(image.shape[1::-1]) != tile["pixel_geometry"]["image_size"]:
            raise ValueError("Retrieval tile image dimensions mismatch")
        embeddings[index] = probe.embed(image)
        records.append({"tile_id": tile["tile_id"], "faiss_id": index,
                        "center_enu_m": tile["center_enu_m"], "scale_m_per_px": _tile_scale(tile),
                        "image_sha256": tile["image_sha256"]})
    metadata = {"schema_version": RETRIEVAL_METADATA_SCHEMA, "map_release_id": reference["release_id"],
                "model_release_id": probe.manifest["model_id"], "descriptor_dimension": probe.dimension,
                "metric": "inner_product_cosine", "normalize_query": True, "index_format": "numpy_flat",
                "tiles": records}
    output.mkdir(parents=True, exist_ok=True)
    index_path, metadata_path = output / "embeddings.npz", output / "tiles.json"
    np.savez_compressed(index_path, embeddings=embeddings, faiss_ids=np.arange(len(records), dtype=np.int64))
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    load_tile_retriever(index_path, metadata_path, expected_map_release_id=reference["release_id"],
                        expected_model_release_id=probe.manifest["model_id"])
    manifest = {"schema_version": REPLAY_RETRIEVAL_SCHEMA, "publication_mode": "research_only",
        "px4_external_vision": "disabled", "map_release_id": reference["release_id"],
        "model_id": probe.manifest["model_id"], "model_sha256": probe.manifest["onnx"]["sha256"],
        "retrieval_export_sha256": sha256_file(probe.root / "manifest.json"),
        "reference_map_sha256": sha256_file(reference_path), "tile_layout_sha256": sha256_file(layout_path),
        "tile_count": len(records), "descriptor_dimension": probe.dimension, "embedding_bytes": nbytes,
        "index": {"path": index_path.name, "sha256": sha256_file(index_path)},
        "metadata": {"path": metadata_path.name, "sha256": sha256_file(metadata_path)},
        "query_geometry": "raw_recorded_camera_bgr_no_gps_prior"}
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


class ReplayCandidateRetriever:
    """Search all selected-map embeddings, returning at most four candidates."""

    def __init__(self, export_dir: str | Path, index_dir: str | Path, reference_map: Path,
                 tile_layout: Path, *, top_k: int = 4):
        if type(top_k) is not int or not 1 <= top_k <= 4:
            raise ValueError("Replay retrieval top_k must be an integer in [1,4]")
        self.top_k = top_k
        self.probe = RetrievalOnnxProbe(export_dir)
        root = Path(index_dir).expanduser().resolve()
        manifest_path = root / "manifest.json"
        self.manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        reference, layout = _map_layout(reference_map, tile_layout)
        expected = {"schema_version": REPLAY_RETRIEVAL_SCHEMA, "publication_mode": "research_only",
            "px4_external_vision": "disabled", "map_release_id": reference["release_id"],
            "model_id": self.probe.manifest["model_id"], "model_sha256": self.probe.manifest["onnx"]["sha256"],
            "retrieval_export_sha256": sha256_file(self.probe.root / "manifest.json"),
            "reference_map_sha256": sha256_file(reference_map), "tile_layout_sha256": sha256_file(tile_layout),
            "tile_count": len(layout["tiles"]), "descriptor_dimension": self.probe.dimension,
            "embedding_bytes": len(layout["tiles"]) * self.probe.dimension * 4,
            "query_geometry": "raw_recorded_camera_bgr_no_gps_prior"}
        if any(self.manifest.get(key) != value for key, value in expected.items()):
            raise ValueError("Retrieval index map/model/layout compatibility mismatch")
        if expected["embedding_bytes"] > MAX_INDEX_BYTES:
            raise ValueError("Retrieval index exceeds embedding memory budget")
        members = {}
        for key in ("index", "metadata"):
            record = self.manifest.get(key, {})
            members[key] = export_member(root, record.get("path"))
            if sha256_file(members[key]) != record.get("sha256"):
                raise ValueError(f"Retrieval {key} checksum mismatch")
        metadata = json.loads(members["metadata"].read_text(encoding="utf-8"))
        if (metadata.get("index_format") != "numpy_flat" or metadata.get("normalize_query") is not True
                or metadata.get("descriptor_dimension") != self.probe.dimension):
            raise ValueError("Incompatible replay retrieval metadata")
        tile_identity = {t["tile_id"]: t["image_sha256"] for t in layout["tiles"]}
        if {t["tile_id"]: t.get("image_sha256") for t in metadata["tiles"]} != tile_identity:
            raise ValueError("Retrieval metadata tile imagery differs from layout")
        self.retriever = load_tile_retriever(members["index"], members["metadata"],
            expected_map_release_id=reference["release_id"], expected_model_release_id=self.probe.manifest["model_id"])
        self.provenance = {**expected, "index_manifest_sha256": sha256_file(manifest_path),
                           "index_sha256": self.manifest["index"]["sha256"], "top_k": top_k,
                           "cadence": "every_complete_accepted_window", "scores_are_probabilities": False}

    def search(self, bgr: np.ndarray) -> list[dict[str, Any]]:
        result = self.retriever.search(self.probe.embed(bgr), top_k=self.top_k)
        if not result or any(not np.isfinite(row["score"]) for row in result):
            raise ValueError("Retrieval produced no finite candidate evidence")
        return result


def main() -> None:
    parser = argparse.ArgumentParser(description="Build a shadow retrieval index from learned ONNX map embeddings")
    for name in ("retrieval-export", "reference-map", "tile-layout", "output-dir"):
        parser.add_argument("--" + name, required=True, type=Path)
    args = parser.parse_args()
    try:
        result = build_replay_retrieval_index(args.retrieval_export, args.reference_map, args.tile_layout, args.output_dir)
    except (ValueError, RuntimeError, OSError) as exc:
        parser.exit(2, f"Retrieval indexing rejected: {exc}\n")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
