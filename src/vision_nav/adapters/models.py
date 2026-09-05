"""Model adapters. Heavy research dependencies stay outside control/estimation code."""

from __future__ import annotations

import hashlib
import numpy as np

from ..types import CorrespondenceSet, FramePacket, RetrievalCandidate, VisualOdometry, Pose3


class ModelUnavailable(RuntimeError):
    pass


class XFeatLighterGlueAdapter:
    """Deployment adapter target: XFeat features plus LighterGlue correspondence."""
    name = "xfeat_lighterglue"
    def match(self, frame: FramePacket, tile_id: str) -> CorrespondenceSet:
        try:
            import onnxruntime  # noqa: F401
        except ImportError as exc:
            raise ModelUnavailable("Install drone-vision-nav[neural-runtime] and bind the validated ONNX release.") from exc
        raise NotImplementedError("Bind XFeat/LighterGlue tensors through vision_nav.neural.onnx_runtime.")


class RoMaDenseAdapter:
    """Adapter target: RoMa certainty-weighted dense matches, seeded by sparse geometry."""
    name = "roma"
    def verify(self, frame: FramePacket, tile_id: str, seed: CorrespondenceSet) -> CorrespondenceSet:
        try:
            import romatch  # noqa: F401
        except ImportError as exc:
            raise ModelUnavailable("Install vision-first-nav[models] for RoMa verification.") from exc
        raise NotImplementedError("Run RoMa only on sparse/BEV shortlisted tiles and return certainty weights.")


class MegaLocRetrieverAdapter:
    """Deployment adapter target: MegaLoc embedding plus FAISS hierarchical index."""
    name = "megaloc"
    def retrieve(self, frame: FramePacket, top_k: int) -> list[RetrievalCandidate]:
        raise NotImplementedError("Load the map embedding index and a SALAD/MegaLoc checkpoint.")


class Video2BEVAdapter:
    """Bounded video-to-BEV port; may use learned Gaussian-splat reconstruction."""
    name = "video2bev"
    def update(self, frame: FramePacket, odometry: VisualOdometry | None) -> np.ndarray | None:
        raise NotImplementedError("Maintain a bounded keyframe window and emit an aligned C,H,W BEV feature map.")


class MASt3RSLAMAdapter:
    """Local visual-SLAM/VO port using MASt3R-SLAM point-map tracking."""
    name = "mast3r_slam"
    def track(self, frame: FramePacket) -> VisualOdometry | None:
        raise NotImplementedError("Convert MASt3R-SLAM's local pose/quality to VisualOdometry factors.")


class VGGTWindowAdapter:
    """Short-window reconstruction/refinement port; unsuitable as an unchecked direct pose."""
    name = "vggt_window"
    def reconstruct(self, keyframes: list[FramePacket]) -> dict[str, np.ndarray]:
        raise NotImplementedError("Return camera poses, depth/point maps and tracks for local map refinement.")


class TopicGeoScorer:
    """Optional semantic compatibility evidence for ranked tile candidates."""
    name = "topicgeo"
    def score(self, frame: FramePacket, tile_id: str) -> float:
        raise NotImplementedError("Return calibrated [0,1] topic compatibility; never use as pose evidence alone.")


class DeterministicStub:
    """Repeatable interface probe for CI/simulation—not a navigation algorithm."""
    def retrieve(self, frame: FramePacket, top_k: int) -> list[RetrievalCandidate]:
        return [RetrievalCandidate(f"tile-{i}", 1.0 / (i + 1), np.array([i * 10., 0., 0.]), 0.5)
                for i in range(top_k)]

    def match(self, frame: FramePacket, tile_id: str) -> CorrespondenceSet:
        rng = np.random.default_rng(self._seed(frame, tile_id))
        source = rng.uniform([0, 0], [frame.camera.width, frame.camera.height], size=(64, 2))
        target = source + rng.normal(0, 0.35, size=(64, 2))
        return CorrespondenceSet(source, target, rng.uniform(.72, .99, 64), "sparse_xfeat_lighterglue")

    def verify(self, frame: FramePacket, tile_id: str, seed: CorrespondenceSet) -> CorrespondenceSet:
        return CorrespondenceSet(seed.source_uv, seed.target_uv, np.minimum(1., seed.confidence + .02), "dense_roma")

    def track(self, frame: FramePacket) -> VisualOdometry:
        return VisualOdometry(frame.timestamp_s, Pose3(np.array([0.05, 0., 0.]), np.array([0., 0., 0., 1.])),
                              np.eye(6) * .01, 120, .9)

    @staticmethod
    def _seed(frame: FramePacket, tile_id: str) -> int:
        return int(hashlib.sha256(f"{frame.sequence}:{tile_id}".encode()).hexdigest()[:8], 16)
