"""Validated MegaLoc/FAISS tile retrieval for a MapRelease v2.

FAISS stores numeric vector IDs, while navigation needs stable map tile IDs and
local priors. The adjacent metadata file is therefore part of the immutable map
release rather than an exporter-private convention.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np


RETRIEVAL_METADATA_SCHEMA = "vision_nav_retrieval_index_v1"


@dataclass(frozen=True)
class RetrievalTile:
    tile_id: str
    faiss_id: int
    center_enu_m: tuple[float, float, float]
    scale_m_per_px: float


@dataclass(frozen=True)
class RetrievalMetadata:
    map_release_id: str
    model_release_id: str
    descriptor_dimension: int
    metric: str
    normalize_query: bool
    index_format: str
    tiles: tuple[RetrievalTile, ...]


def load_retrieval_metadata(
    path: str | Path,
    *,
    expected_map_release_id: str | None = None,
    expected_model_release_id: str | None = None,
) -> RetrievalMetadata:
    source = Path(path).expanduser().resolve()
    raw = json.loads(source.read_text(encoding="utf-8"))
    if raw.get("schema_version") != RETRIEVAL_METADATA_SCHEMA:
        raise ValueError(f"Retrieval metadata must use {RETRIEVAL_METADATA_SCHEMA}.")
    for key in ("map_release_id", "model_release_id", "descriptor_dimension", "metric", "tiles"):
        if raw.get(key) in (None, "", []):
            raise ValueError(f"Retrieval metadata is missing {key}.")
    if expected_map_release_id and raw["map_release_id"] != expected_map_release_id:
        raise ValueError("Retrieval metadata map_release_id does not match the active map release.")
    if expected_model_release_id and raw["model_release_id"] != expected_model_release_id:
        raise ValueError("Retrieval metadata model_release_id does not match the active model release.")
    dimension = int(raw["descriptor_dimension"])
    if dimension <= 0:
        raise ValueError("Retrieval metadata descriptor_dimension must be positive.")
    metric = str(raw["metric"])
    if metric != "inner_product_cosine":
        raise ValueError("Retrieval metadata metric must be inner_product_cosine.")
    index_format = str(raw.get("index_format", "faiss"))
    if index_format not in {"faiss", "numpy_flat"}:
        raise ValueError("Retrieval metadata index_format must be faiss or numpy_flat.")
    tiles: list[RetrievalTile] = []
    tile_ids: set[str] = set()
    faiss_ids: set[int] = set()
    for entry in raw["tiles"]:
        if not isinstance(entry, dict):
            raise ValueError("Retrieval metadata tiles must be objects.")
        tile_id = str(entry.get("tile_id", ""))
        faiss_id = entry.get("faiss_id")
        center = entry.get("center_enu_m")
        scale = entry.get("scale_m_per_px")
        if not tile_id or not isinstance(faiss_id, int) or faiss_id < 0:
            raise ValueError("Retrieval metadata tile requires non-empty tile_id and non-negative faiss_id.")
        if tile_id in tile_ids or faiss_id in faiss_ids:
            raise ValueError("Retrieval metadata has duplicate tile_id or faiss_id.")
        if not isinstance(center, list) or len(center) != 3 or not np.all(np.isfinite(np.asarray(center, dtype=float))):
            raise ValueError("Retrieval metadata tile center_enu_m must contain three finite values.")
        if not isinstance(scale, (int, float)) or not np.isfinite(scale) or scale <= 0:
            raise ValueError("Retrieval metadata tile scale_m_per_px must be positive.")
        tile_ids.add(tile_id)
        faiss_ids.add(faiss_id)
        tiles.append(RetrievalTile(tile_id, faiss_id, tuple(float(value) for value in center), float(scale)))
    return RetrievalMetadata(
        map_release_id=str(raw["map_release_id"]),
        model_release_id=str(raw["model_release_id"]),
        descriptor_dimension=dimension,
        metric=metric,
        normalize_query=bool(raw.get("normalize_query", True)),
        index_format=index_format,
        tiles=tuple(tiles),
    )


def _normalized_query(embedding: np.ndarray, metadata: RetrievalMetadata) -> np.ndarray:
    query = np.asarray(embedding, dtype=np.float32).reshape(-1)
    if query.size != metadata.descriptor_dimension or not np.all(np.isfinite(query)):
        raise ValueError("Global retrieval embedding does not match the active retrieval index dimension.")
    if metadata.normalize_query:
        norm = float(np.linalg.norm(query))
        if norm <= 0.0:
            raise ValueError("Global retrieval embedding has zero norm.")
        query = query / norm
    return query


def _candidate_records(
    metadata: RetrievalMetadata,
    *,
    ordered_ids: np.ndarray,
    ordered_scores: np.ndarray,
) -> list[dict[str, Any]]:
    by_faiss_id = {tile.faiss_id: tile for tile in metadata.tiles}
    candidates: list[dict[str, Any]] = []
    for rank, (score, faiss_id) in enumerate(zip(ordered_scores, ordered_ids), start=1):
        tile = by_faiss_id.get(int(faiss_id))
        if tile is None:
            raise ValueError("Retrieval index returned an ID absent from retrieval metadata.")
        candidates.append({
            "rank": rank,
            "tile_id": tile.tile_id,
            "score": float(score),
            "center_enu_m": list(tile.center_enu_m),
            "scale_m_per_px": tile.scale_m_per_px,
        })
    return candidates


class FaissTileRetriever:
    """Thin, validated FAISS reader. It only returns candidates, never poses."""

    def __init__(self, index: Any, metadata: RetrievalMetadata) -> None:
        if int(index.d) != metadata.descriptor_dimension:
            raise ValueError("FAISS index dimension does not match retrieval metadata.")
        if int(index.ntotal) != len(metadata.tiles):
            raise ValueError("FAISS index vector count does not match retrieval metadata tiles.")
        self._index = index
        self._metadata = metadata
        self._by_faiss_id = {tile.faiss_id: tile for tile in metadata.tiles}

    def search(self, embedding: np.ndarray, *, top_k: int) -> list[dict[str, Any]]:
        if top_k <= 0:
            raise ValueError("top_k must be positive.")
        query = _normalized_query(embedding, self._metadata)
        scores, ids = self._index.search(query[None, :], min(top_k, len(self._metadata.tiles)))
        return _candidate_records(self._metadata, ordered_ids=ids[0], ordered_scores=scores[0])


class NumpyTileRetriever:
    """Portable exact cosine retriever for small, bounded Pi map releases.

    It avoids making a FAISS wheel a prerequisite for the first Pi shadow run.
    Map production may still emit a FAISS index for larger areas; both formats
    are bound to the same immutable metadata and FAISS-style numeric IDs.
    """

    def __init__(self, embeddings: np.ndarray, faiss_ids: np.ndarray, metadata: RetrievalMetadata) -> None:
        vectors = np.asarray(embeddings, dtype=np.float32)
        ids = np.asarray(faiss_ids, dtype=np.int64).reshape(-1)
        if vectors.ndim != 2 or vectors.shape != (len(metadata.tiles), metadata.descriptor_dimension):
            raise ValueError("NumPy retrieval index vectors do not match metadata dimensions.")
        if ids.shape != (len(metadata.tiles),) or set(ids.tolist()) != {tile.faiss_id for tile in metadata.tiles}:
            raise ValueError("NumPy retrieval index IDs do not match retrieval metadata.")
        if not np.all(np.isfinite(vectors)):
            raise ValueError("NumPy retrieval index contains non-finite embeddings.")
        norms = np.linalg.norm(vectors, axis=1)
        if np.any(norms <= 0.0):
            raise ValueError("NumPy retrieval index contains zero-norm embeddings.")
        self._vectors = vectors / norms[:, None]
        self._ids = ids
        self._metadata = metadata

    def search(self, embedding: np.ndarray, *, top_k: int) -> list[dict[str, Any]]:
        if top_k <= 0:
            raise ValueError("top_k must be positive.")
        query = _normalized_query(embedding, self._metadata)
        scores = self._vectors @ query
        count = min(top_k, len(self._ids))
        order = np.lexsort((self._ids, -scores))[:count]
        return _candidate_records(self._metadata, ordered_ids=self._ids[order], ordered_scores=scores[order])


def load_faiss_tile_retriever(
    index_path: str | Path,
    metadata_path: str | Path,
    *,
    expected_map_release_id: str,
    expected_model_release_id: str,
) -> FaissTileRetriever:
    try:
        import faiss
    except ImportError as exc:
        raise RuntimeError("faiss-cpu is required for Pi global retrieval. Install drone-vision-nav[neural-runtime].") from exc
    metadata = load_retrieval_metadata(
        metadata_path,
        expected_map_release_id=expected_map_release_id,
        expected_model_release_id=expected_model_release_id,
    )
    try:
        index = faiss.read_index(str(Path(index_path).expanduser().resolve()))
    except Exception as exc:
        raise RuntimeError(f"Could not load FAISS retrieval index: {exc}") from exc
    return FaissTileRetriever(index, metadata)


def load_numpy_tile_retriever(
    index_path: str | Path,
    metadata_path: str | Path,
    *,
    expected_map_release_id: str,
    expected_model_release_id: str,
) -> NumpyTileRetriever:
    metadata = load_retrieval_metadata(
        metadata_path,
        expected_map_release_id=expected_map_release_id,
        expected_model_release_id=expected_model_release_id,
    )
    if metadata.index_format != "numpy_flat":
        raise ValueError("NumPy retrieval loader requires metadata index_format=numpy_flat.")
    try:
        with np.load(Path(index_path).expanduser().resolve(), allow_pickle=False) as archive:
            embeddings = np.asarray(archive["embeddings"])
            faiss_ids = np.asarray(archive["faiss_ids"])
    except (OSError, KeyError, ValueError) as exc:
        raise ValueError(f"Could not load NumPy retrieval index: {exc}") from exc
    return NumpyTileRetriever(embeddings, faiss_ids, metadata)


def load_tile_retriever(
    index_path: str | Path,
    metadata_path: str | Path,
    *,
    expected_map_release_id: str,
    expected_model_release_id: str,
) -> FaissTileRetriever | NumpyTileRetriever:
    """Load the declared immutable retrieval index without guessing its format."""

    metadata = load_retrieval_metadata(
        metadata_path,
        expected_map_release_id=expected_map_release_id,
        expected_model_release_id=expected_model_release_id,
    )
    if metadata.index_format == "numpy_flat":
        return load_numpy_tile_retriever(
            index_path,
            metadata_path,
            expected_map_release_id=expected_map_release_id,
            expected_model_release_id=expected_model_release_id,
        )
    return load_faiss_tile_retriever(
        index_path,
        metadata_path,
        expected_map_release_id=expected_map_release_id,
        expected_model_release_id=expected_model_release_id,
    )
