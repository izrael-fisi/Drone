"""Fail-closed compatibility port for the original architecture demonstration."""

from __future__ import annotations

import numpy as np

from ..types import GlobalObservation, NavEstimate, NavigationMode, Pose3, VisualOdometry


class VisionFactorGraph:
    """Legacy messages lack the contracts required by MetricPoseSmoother.

    Keep the demo callable, but never integrate scale-unqualified VO, reuse map
    evidence, or turn feature scores into position confidence. The identity pose
    is only a placeholder required by the legacy NavEstimate schema.
    """

    def add_odometry(self, odometry: VisualOdometry) -> None:
        pass

    def add_global_observation(self, observation: GlobalObservation) -> None:
        pass

    def optimize(self, timestamp_s: float) -> NavEstimate:
        return NavEstimate(
            timestamp_s, Pose3(np.zeros(3), np.array([0., 0., 0., 1.])),
            np.zeros(3), np.eye(6) * 1e6, 0.0, NavigationMode.NO_GLOBAL_FIX, None,
            {"covariance_trace_m2": 3e6, "global_updates": 0.0,
             "backend_unbound": True, "publication_withheld": True, "position_authority": False,
             "last_rejection_reason": "legacy_factors_lack_metric_provenance"},
        )
