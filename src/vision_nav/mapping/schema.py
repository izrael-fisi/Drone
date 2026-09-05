"""Offline reference-map record format. Store binary features out of band by URI."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any
import json
import numpy as np


@dataclass(frozen=True)
class TileRecord:
    tile_id: str
    center_enu_m: list[float]
    resolution_m_per_px: float
    image_uri: str
    embedding_uri: str
    sparse_features_uri: str
    dense_features_uri: str
    elevation_uri: str | None = None
    semantic_histogram: dict[str, float] | None = None
    acquisition_date: str | None = None

    def to_json(self) -> str:
        return json.dumps(asdict(self), separators=(",", ":"))

    @classmethod
    def from_json(cls, line: str) -> "TileRecord":
        return cls(**json.loads(line))
