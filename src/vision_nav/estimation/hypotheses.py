"""Multi-hypothesis global localization with explicit ambiguity management."""

from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Iterable
import numpy as np

from ..types import GlobalObservation


@dataclass
class Hypothesis:
    tile_id: str
    log_weight: float
    observation: GlobalObservation
    consecutive_support: int = 1
    last_timestamp_s: float = 0.0


class HypothesisBank:
    def __init__(self, max_hypotheses: int = 6, accept_confidence: float = 0.72,
                 min_grid_coverage: float = 0.25, max_age_s: float = 1.5) -> None:
        self.max_hypotheses = max_hypotheses
        self.accept_confidence = accept_confidence
        self.min_grid_coverage = min_grid_coverage
        self.max_age_s = max_age_s
        self._items: dict[str, Hypothesis] = {}

    def update(self, observations: Iterable[GlobalObservation], *, timestamp_s: float | None = None) -> None:
        if timestamp_s is not None:
            self.expire(timestamp_s)
        for obs in observations:
            estimate = obs.estimate
            if not estimate.valid or estimate.inlier_grid_coverage < self.min_grid_coverage:
                continue
            # Independent signals are summed in log space; geometry dominates appearance.
            quality = (0.20 * obs.retrieval_score + 0.25 * obs.dense_score
                       + 0.55 * self._geometry_score(estimate.inlier_count, estimate.reprojection_rmse_px))
            evidence = math.log(max(quality, 1e-6))
            old = self._items.get(estimate.tile_id)
            if old:
                old.log_weight = 0.70 * old.log_weight + evidence
                old.observation = obs
                old.consecutive_support += 1
                old.last_timestamp_s = obs.timestamp_s
            else:
                self._items[estimate.tile_id] = Hypothesis(estimate.tile_id, evidence, obs, 1, obs.timestamp_s)
        self._prune()

    def best(self) -> tuple[Hypothesis | None, float, float]:
        if not self._items:
            return None, 0.0, 0.0
        ordered = sorted(self._items.values(), key=lambda h: h.log_weight, reverse=True)
        weights = np.exp(np.array([h.log_weight for h in ordered]) - ordered[0].log_weight)
        probability = weights / weights.sum()
        margin = float(probability[0] - (probability[1] if len(probability) > 1 else 0.0))
        return ordered[0], float(probability[0]), margin

    def accepted(self) -> Hypothesis | None:
        best, probability, margin = self.best()
        if best and probability >= self.accept_confidence and margin >= 0.20 and best.consecutive_support >= 2:
            return best
        return None

    def expire(self, timestamp_s: float) -> None:
        self._items = {
            tile_id: item for tile_id, item in self._items.items()
            if timestamp_s - item.last_timestamp_s <= self.max_age_s
        }

    def _prune(self) -> None:
        self._items = dict(sorted(self._items.items(), key=lambda kv: kv[1].log_weight, reverse=True)[:self.max_hypotheses])

    @staticmethod
    def _geometry_score(inliers: int, rmse: float) -> float:
        return float(np.clip((inliers / 80.0) * math.exp(-rmse / 3.0), 0.01, 1.0))
