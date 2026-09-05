"""Immutable per-tile XFeat feature cache for learned local correspondence."""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np


LOCAL_FEATURE_CACHE_SCHEMA = "vision_nav_local_feature_cache_v1"


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


@dataclass(frozen=True)
class LocalFeatureTile:
    tile_id: str
    feature_uri: str
    sha256: str


@dataclass(frozen=True)
class LocalFeatureCache:
    root: Path
    map_release_id: str
    model_release_id: str
    tiles: dict[str, LocalFeatureTile]

    def load(self, tile_id: str) -> dict[str, np.ndarray]:
        tile = self.tiles.get(tile_id)
        if tile is None:
            raise ValueError(f"Candidate tile is absent from the local feature cache: {tile_id}")
        path = self.root / tile.feature_uri
        if not path.is_file() or _sha256_file(path) != tile.sha256:
            raise ValueError(f"Local feature asset failed checksum validation: {tile.feature_uri}")
        try:
            with np.load(path, allow_pickle=False) as archive:
                required = {"keypoints", "descriptors"}
                if not required.issubset(archive.files):
                    raise ValueError("Local feature asset requires keypoints and descriptors arrays.")
                values = {name: np.asarray(archive[name]) for name in required}
                if "map_points_enu_m" in archive.files:
                    values["map_points_enu_m"] = np.asarray(archive["map_points_enu_m"])
                if "map_point_variance_enu_m2" in archive.files:
                    values["map_point_variance_enu_m2"] = np.asarray(archive["map_point_variance_enu_m2"])
        except (OSError, ValueError) as exc:
            raise ValueError(f"Could not load local feature asset {tile.feature_uri}: {exc}") from exc
        for name, value in values.items():
            if not np.issubdtype(value.dtype, np.number) or not np.all(np.isfinite(value)):
                raise ValueError(f"Local feature asset {tile.feature_uri} has invalid {name} values.")
        keypoints, descriptors = values["keypoints"], values["descriptors"]
        if (keypoints.ndim not in {2, 3} or keypoints.shape[-1] != 2 or keypoints.shape[-2] == 0
                or descriptors.ndim != keypoints.ndim or descriptors.shape[:-1] != keypoints.shape[:-1]
                or descriptors.shape[-1] == 0 or (keypoints.ndim == 3 and keypoints.shape[0] != 1)):
            raise ValueError("Local feature keypoint/descriptor shapes must align as N x 2 / N x D with optional single batch.")
        if "map_points_enu_m" in values:
            keypoint_count = int(np.asarray(values["keypoints"]).reshape(-1, 2).shape[0])
            points = np.asarray(values["map_points_enu_m"])
            if points.ndim < 2 or points.shape[-1] != 3 or points.reshape(-1, 3).shape != (keypoint_count, 3):
                raise ValueError(f"Local feature asset {tile.feature_uri} map_points_enu_m must align to keypoints as N x 3.")
        if "map_point_variance_enu_m2" in values:
            variance = values["map_point_variance_enu_m2"]
            if "map_points_enu_m" not in values or variance.shape != (keypoint_count, 3) or np.any(variance <= 0):
                raise ValueError("Map point variance must be positive N x 3 ENU variances aligned with landmarks.")
        return values


def load_local_feature_cache(
    path: str | Path,
    *,
    expected_map_release_id: str | None = None,
    expected_model_release_id: str | None = None,
) -> LocalFeatureCache:
    source = Path(path).expanduser().resolve()
    raw = json.loads(source.read_text(encoding="utf-8"))
    if raw.get("schema_version") != LOCAL_FEATURE_CACHE_SCHEMA:
        raise ValueError(f"Local feature metadata must use {LOCAL_FEATURE_CACHE_SCHEMA}.")
    for key in ("map_release_id", "model_release_id", "tiles"):
        if raw.get(key) in (None, "", []):
            raise ValueError(f"Local feature metadata is missing {key}.")
    if expected_map_release_id and raw["map_release_id"] != expected_map_release_id:
        raise ValueError("Local feature metadata map_release_id does not match the active map release.")
    if expected_model_release_id and raw["model_release_id"] != expected_model_release_id:
        raise ValueError("Local feature metadata model_release_id does not match the active model release.")
    tiles: dict[str, LocalFeatureTile] = {}
    for entry in raw["tiles"]:
        if not isinstance(entry, dict):
            raise ValueError("Local feature metadata tiles must be objects.")
        tile_id = str(entry.get("tile_id", ""))
        feature_uri = str(entry.get("feature_uri", ""))
        digest = str(entry.get("sha256", ""))
        if not tile_id or not feature_uri or len(digest) != 64:
            raise ValueError("Local feature metadata tile requires tile_id, feature_uri, and SHA-256.")
        if tile_id in tiles:
            raise ValueError("Local feature metadata has duplicate tile_id.")
        tiles[tile_id] = LocalFeatureTile(tile_id, feature_uri, digest)
    return LocalFeatureCache(
        root=source.parent,
        map_release_id=str(raw["map_release_id"]),
        model_release_id=str(raw["model_release_id"]),
        tiles=tiles,
    )
