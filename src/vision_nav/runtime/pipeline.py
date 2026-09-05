"""Rate-separated online orchestrator; no model-specific code belongs here."""

from __future__ import annotations

from dataclasses import dataclass, replace
import numpy as np

from ..contracts import (ContinuousMapCorrelator, DenseVerifier, GlobalRetriever, LocalBEVBuilder,
                         SparseMatcher, VisualOdometryFrontend, validate_frame_packet)
from ..estimation.factor_graph import VisionFactorGraph
from ..estimation.geometry import RobustGeometry
from ..estimation.hypotheses import HypothesisBank
from ..types import FramePacket, GlobalObservation, NavEstimate, NavigationMode


@dataclass(frozen=True)
class RuntimePolicy:
    retrieval_top_k: int = 12
    max_hypotheses: int = 6
    min_accept_confidence: float = .72
    global_recovery_period_s: float = 2.0
    local_correlation_period_s: float = .2
    max_covariance_trace_m2: float = 100.0
    require_versioned_frame: bool = True
    max_frame_gap_s: float = 0.5


class VisionNavigationRuntime:
    """Global VPR is recovery/initialization; local correlation maintains tracking.

    Asynchronous hardware processes may call ingest_frame serially after timestamp ordering.
    The output is an estimate only; PX4 owns all control and failsafe decisions.
    """
    def __init__(self, vo: VisualOdometryFrontend, retriever: GlobalRetriever, sparse: SparseMatcher,
                 dense: DenseVerifier, geometry: RobustGeometry, backend: VisionFactorGraph,
                 policy: RuntimePolicy = RuntimePolicy(), bev: LocalBEVBuilder | None = None,
                 correlator: ContinuousMapCorrelator | None = None) -> None:
        self.vo, self.retriever, self.sparse, self.dense = vo, retriever, sparse, dense
        self.geometry, self.backend, self.policy, self.bev, self.correlator = geometry, backend, policy, bev, correlator
        self.hypotheses = HypothesisBank(policy.max_hypotheses, policy.min_accept_confidence)
        self._last_recovery_s = -float("inf")
        self._last_local_s = -float("inf")
        self._last_frame_timestamp_s: float | None = None
        self._last_rejection_reason: str | None = None
        self._active_calibration_id: str | None = None

    def ingest_frame(self, frame: FramePacket) -> NavEstimate:
        valid, reason = validate_frame_packet(
            frame, require_versioned_calibration=self.policy.require_versioned_frame
        )
        if valid and self._last_frame_timestamp_s is not None and frame.timestamp_s <= self._last_frame_timestamp_s:
            valid, reason = False, "nonmonotonic_frame_timestamp"
        if not valid:
            return self._withheld_estimate(frame.timestamp_s, reason or "invalid_frame")
        frame_gap_s = (
            frame.timestamp_s - self._last_frame_timestamp_s
            if self._last_frame_timestamp_s is not None
            else 0.0
        )
        self._last_frame_timestamp_s = frame.timestamp_s
        self._active_calibration_id = frame.calibration_id
        if frame_gap_s > self.policy.max_frame_gap_s:
            self.hypotheses.expire(frame.timestamp_s)
            self._last_rejection_reason = "frame_gap_exceeds_policy"

        try:
            odometry = self.vo.track(frame)
        except Exception as exc:
            odometry = None
            self._last_rejection_reason = f"vo_error:{type(exc).__name__}"
        if odometry is not None:
            self.backend.add_odometry(odometry)
        provisional = self.backend.optimize(frame.timestamp_s)
        should_recover = (frame_gap_s > self.policy.max_frame_gap_s
                          or frame.timestamp_s - self._last_recovery_s >= self.policy.global_recovery_period_s
                          or provisional.mode != NavigationMode.TRACKING
                          or provisional.diagnostics["covariance_trace_m2"] > self.policy.max_covariance_trace_m2)
        if should_recover:
            self._global_recovery(frame)
            self._last_recovery_s = frame.timestamp_s
        # The BEV port is retained here for continuous feature-map correlation. Implement it
        # with Video2BEV / VGGT / MASt3R local windows on accelerator hardware.
        if self.bev and frame.timestamp_s - self._last_local_s >= self.policy.local_correlation_period_s:
            local_bev = self.bev.update(frame, odometry)
            if local_bev is not None and self.correlator is not None:
                # Correlate only around the active tile/hypotheses; this is continuous local
                # tracking, not a second expensive global search.
                local_observations = [
                    replace(observation, timestamp_s=frame.timestamp_s)
                    for observation in self.correlator.correlate(local_bev, provisional.active_tile_id)
                ]
                self.hypotheses.update(local_observations, timestamp_s=frame.timestamp_s)
            self._last_local_s = frame.timestamp_s
        accepted = self.hypotheses.accepted()
        if accepted:
            self.backend.add_global_observation(accepted.observation)
        output = self.backend.optimize(frame.timestamp_s)
        return self._apply_mode(output)

    def _global_recovery(self, frame: FramePacket) -> None:
        observations: list[GlobalObservation] = []
        try:
            candidates = self.retriever.retrieve(frame, self.policy.retrieval_top_k)
            for candidate in candidates:
                sparse = self.sparse.match(frame, candidate.tile_id)
                coarse = self.geometry.estimate(frame, candidate.tile_id, sparse)
                # Dense RoMa is expensive: run only after sparse geometry passes.
                if coarse.valid:
                    dense = self.dense.verify(frame, candidate.tile_id, sparse)
                    fine = self.geometry.estimate(frame, candidate.tile_id, dense)
                    if fine.valid:
                        observations.append(GlobalObservation(frame.timestamp_s, fine, candidate.score,
                                                              float(np.mean(dense.confidence))))
        except Exception as exc:
            self._last_rejection_reason = f"global_recovery_error:{type(exc).__name__}"
        self.hypotheses.update(observations, timestamp_s=frame.timestamp_s)

    def _apply_mode(self, estimate: NavEstimate) -> NavEstimate:
        accepted = self.hypotheses.accepted()
        trace = float(estimate.diagnostics.get("covariance_trace_m2", float("inf")))
        if estimate.diagnostics.get("publication_withheld"):
            mode = NavigationMode.NO_GLOBAL_FIX
        elif accepted and estimate.confidence >= self.policy.min_accept_confidence:
            mode = NavigationMode.TRACKING
        elif trace > self.policy.max_covariance_trace_m2:
            mode = NavigationMode.NO_GLOBAL_FIX
        elif accepted:
            mode = NavigationMode.DEGRADED
        else:
            mode = NavigationMode.RECOVERING
        diagnostics = dict(estimate.diagnostics)
        diagnostics["hypothesis_count"] = float(len(self.hypotheses._items))
        if self._last_rejection_reason:
            diagnostics["last_rejection_reason"] = self._last_rejection_reason
        return NavEstimate(
            estimate.timestamp_s, estimate.pose_map_body, estimate.velocity_map_mps,
            estimate.covariance, estimate.confidence, mode, estimate.active_tile_id,
            diagnostics,
            map_version=estimate.map_version,
            calibration_id=self._active_calibration_id,
            model_version=estimate.model_version,
            measurement_age_s=0.0,
        )

    def _withheld_estimate(self, timestamp_s: float, reason: str) -> NavEstimate:
        """Return an observable no-fix state; invalid frames never create a factor."""

        self._last_rejection_reason = reason
        estimate = self.backend.optimize(timestamp_s)
        diagnostics = dict(estimate.diagnostics)
        diagnostics["last_rejection_reason"] = reason
        diagnostics["publication_withheld"] = True
        return NavEstimate(
            timestamp_s, estimate.pose_map_body, estimate.velocity_map_mps, estimate.covariance,
            0.0, NavigationMode.NO_GLOBAL_FIX, estimate.active_tile_id, diagnostics,
            map_version=estimate.map_version,
            calibration_id=self._active_calibration_id,
            model_version=estimate.model_version,
        )
