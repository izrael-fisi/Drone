"""Pinned XFeat/LighterGlue reference inference for learned camera/map research.

PyTorch is deliberately isolated here. This reference is not a Pi ONNX release,
and never opens MAVLink or converts neural scores into position covariance.
"""

from __future__ import annotations

import argparse
from contextlib import redirect_stdout
from dataclasses import dataclass, asdict
import importlib
import io
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import cv2
import numpy as np

from vision_nav.training_capture import sha256_file
from .lighterglue_probe import inspect_pinned_lighterglue_checkout
from .shadow_geometry import verify_planar_correspondences


LOCAL_CONFIG_SCHEMA = "vision_nav_learned_local_reference_v1"


@dataclass(frozen=True)
class LocalMatchingPolicy:
    image_width: int = 640
    image_height: int = 384
    max_features: int = 512
    detection_threshold: float = 0.05
    minimum_score: float = 0.2
    min_inliers: int = 12
    min_inlier_ratio: float = 0.4
    max_reprojection_error_px: float = 3.0
    min_grid_coverage: float = 0.25

    def validate(self) -> None:
        for value in (self.image_width, self.image_height):
            if type(value) is not int or not 32 <= value <= 1024 or value % 32:
                raise ValueError("Local feature image dimensions must be multiples of 32 in [32,1024]")
        if type(self.max_features) is not int or not 16 <= self.max_features <= 2048:
            raise ValueError("Local matcher feature budget must be in [16,2048]")
        if type(self.min_inliers) is not int or not 6 <= self.min_inliers <= self.max_features:
            raise ValueError("Local geometry minimum inliers must be in [6,max_features]")
        for value in (self.detection_threshold, self.minimum_score, self.min_inlier_ratio, self.min_grid_coverage):
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value) or not 0 < value <= 1:
                raise ValueError("Local matching thresholds must be finite in (0,1]")
        if not np.isfinite(self.max_reprojection_error_px) or self.max_reprojection_error_px <= 0:
            raise ValueError("Local geometry reprojection threshold must be positive")

    def geometry_kwargs(self) -> dict[str, Any]:
        return {key: value for key, value in asdict(self).items()
                if key in {"minimum_score", "min_inliers", "min_inlier_ratio", "max_reprojection_error_px", "min_grid_coverage"}}


@dataclass(frozen=True)
class LocalFeatures:
    keypoints: np.ndarray
    descriptors: np.ndarray
    scores: np.ndarray

    def validate(self, policy: LocalMatchingPolicy) -> None:
        n = len(self.keypoints)
        if (self.keypoints.shape != (n, 2) or self.descriptors.shape != (n, 64)
                or self.scores.shape != (n,) or n > policy.max_features):
            raise ValueError("XFeat local arrays do not align or exceed the feature budget")
        if any(v.dtype != np.float32 or not np.isfinite(v).all() for v in (self.keypoints, self.descriptors, self.scores)):
            raise ValueError("XFeat local features must be finite float32 arrays")
        if (np.any(self.keypoints < 0) or np.any(self.keypoints >= [policy.image_width, policy.image_height])
                or np.any(self.scores <= 0) or np.any(self.scores > 1)
                or (n and not np.allclose(np.linalg.norm(self.descriptors, axis=1), 1, atol=1e-3))):
            raise ValueError("XFeat local feature coordinates, scores or descriptor norms are invalid")

    def selected(self, mask: np.ndarray) -> LocalFeatures:
        return LocalFeatures(self.keypoints[mask], self.descriptors[mask], self.scores[mask])


def local_image(bgr: np.ndarray, policy: LocalMatchingPolicy) -> np.ndarray:
    if not isinstance(bgr, np.ndarray) or bgr.dtype != np.uint8 or bgr.ndim != 3 or bgr.shape[2] != 3 or not bgr.size:
        raise ValueError("Local matching requires a nonempty uint8 BGR image")
    image = cv2.resize(bgr, (policy.image_width, policy.image_height), interpolation=cv2.INTER_LINEAR)
    image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    return np.ascontiguousarray(image.transpose(2, 0, 1)[None], dtype=np.float32) / np.float32(255)


def resized_camera_matrix(matrix: np.ndarray, source_size: tuple[int, int], target_size: tuple[int, int]) -> np.ndarray:
    """OpenCV resize pixel-centre convention, including principal-point offset."""
    matrix = np.asarray(matrix, dtype=np.float64)
    if matrix.shape != (3, 3) or not np.isfinite(matrix).all() or any(v <= 0 for v in (*source_size, *target_size)):
        raise ValueError("Invalid calibrated image geometry")
    sx, sy = np.asarray(target_size, dtype=float) / source_size
    transform = np.array([[sx, 0, (sx - 1) / 2], [0, sy, (sy - 1) / 2], [0, 0, 1]])
    return transform @ matrix


def matcher_checkpoint(state: dict[str, Any], model_state: dict[str, Any], layers: int) -> dict[str, Any]:
    """Retain only the declared matcher; reject missing learned parameters."""
    normalized = {}
    for key, value in state.items():
        if key.startswith("extractor.model.net."):
            continue
        name = key.removeprefix("matcher.")
        for index in range(layers):
            name = name.replace(f"self_attn.{index}.", f"transformers.{index}.self_attn.")
            name = name.replace(f"cross_attn.{index}.", f"transformers.{index}.cross_attn.")
        if name in normalized:
            raise ValueError("Duplicate normalized LighterGlue checkpoint parameter")
        normalized[name] = value
    # This is a deterministic threshold buffer, not a learned missing weight.
    if "confidence_thresholds" not in normalized and "confidence_thresholds" in model_state:
        normalized["confidence_thresholds"] = model_state["confidence_thresholds"]
    if set(normalized) != set(model_state):
        raise ValueError("LighterGlue checkpoint parameter set differs from the reference architecture")
    return normalized


class PinnedLocalMatcher:
    def __init__(self, config_path: str | Path):
        path = Path(config_path).expanduser().resolve()
        config = json.loads(path.read_text(encoding="utf-8"))
        if (config.get("schema_version") != LOCAL_CONFIG_SCHEMA or config.get("backend") != "pytorch_reference"
                or config.get("px4_external_vision") != "disabled"):
            raise ValueError("Local matcher config must select the shadow-only PyTorch reference")
        try:
            self.policy = LocalMatchingPolicy(**config.get("policy", {}))
        except TypeError as exc:
            raise ValueError(f"Invalid local matcher policy: {exc}") from exc
        self.policy.validate()
        device = config.get("device", "cpu")
        if device not in {"cpu", "cuda"}:
            raise ValueError("Local reference supports cpu or cuda")
        def member(key):
            if not isinstance(config.get(key), str) or not config[key]:
                raise ValueError(f"Local matcher config requires {key}")
            return (path.parent / Path(config[key]).expanduser()).resolve()
        checkout, lock = member("checkout"), member("upstream_lock")
        xfeat_weights, matcher_weights = member("xfeat_weights"), member("lighterglue_weights")
        upstream = inspect_pinned_lighterglue_checkout(checkout, upstream_lock=lock)
        dirty = subprocess.run(["git", "-C", str(checkout), "status", "--porcelain", "--untracked-files=normal"],
                               check=True, capture_output=True, text=True).stdout.strip()
        if dirty:
            raise ValueError("Learned matching requires a clean pinned checkout")
        for value in (xfeat_weights, matcher_weights):
            if not value.is_file():
                raise ValueError("Local learned checkpoint is missing")
        try:
            import torch
            import kornia
            from kornia.feature.lightglue import LightGlue
        except ImportError as exc:
            raise RuntimeError("Local reference matching requires PyTorch and Kornia in the research environment") from exc
        if device == "cuda" and not torch.cuda.is_available():
            raise RuntimeError("CUDA local matching requested, but unavailable")
        existing = sys.modules.get("modules")
        if existing is not None and not Path(getattr(existing, "__file__", "") or "/").resolve().is_relative_to(checkout):
            raise RuntimeError("A different 'modules' package is loaded; use a fresh research process")
        sys.path.insert(0, str(checkout))
        try:
            xfeat_module = importlib.import_module("modules.xfeat")
            light_module = importlib.import_module("modules.lighterglue")
        finally:
            sys.path.remove(str(checkout))
        with redirect_stdout(io.StringIO()):
            self.extractor = xfeat_module.XFeat(weights=None, top_k=self.policy.max_features,
                                                detection_threshold=self.policy.detection_threshold)
            self.extractor.dev = torch.device(device)
            self.extractor.net.to(device).eval()
            self.extractor.net.load_state_dict(torch.load(xfeat_weights, map_location=device, weights_only=True), strict=True)
            conf = {**light_module.LighterGlue.default_conf_xfeat,
                    "flash": False, "depth_confidence": -1, "width_confidence": -1,
                    "filter_threshold": self.policy.minimum_score, "weights": None}
            self.matcher = LightGlue(None, **conf).to(device).eval()
        weights = torch.load(matcher_weights, map_location=device, weights_only=True)
        self.matcher.load_state_dict(matcher_checkpoint(weights, self.matcher.state_dict(), conf["n_layers"]), strict=True)
        self.torch, self.device = torch, device
        self.provenance = {"schema_version": LOCAL_CONFIG_SCHEMA, "backend": "pytorch_reference", "device": device,
            "px4_external_vision": "disabled", "config_sha256": sha256_file(path), "upstream": upstream,
            "xfeat_weights_sha256": sha256_file(xfeat_weights), "lighterglue_weights_sha256": sha256_file(matcher_weights),
            "torch_version": str(torch.__version__), "kornia_version": kornia.__version__,
            "kornia_lightglue_source_sha256": sha256_file(Path(importlib.import_module(LightGlue.__module__).__file__)),
            "matcher_configuration": conf, "policy": asdict(self.policy),
            "preprocessing": "opencv_linear_rgb_zero_one_nchw_fixed_multiple_of_32",
            "reference_source_sha256": sha256_file(Path(__file__)),
            "limitations": ["not_onnx_or_pi_validated", "scores_not_calibrated_covariance", "full_attention_pruning_disabled"]}

    def extract(self, bgr: np.ndarray) -> LocalFeatures:
        image = self.torch.from_numpy(local_image(bgr, self.policy)).to(self.device)
        with self.torch.inference_mode():
            features = self.extractor.detectAndCompute(image)[0]
        result = LocalFeatures(*(features[name].detach().cpu().numpy().astype(np.float32)
                                 for name in ("keypoints", "descriptors", "scores")))
        result.validate(self.policy)
        return result

    def match(self, query: LocalFeatures, tile: LocalFeatures) -> tuple[np.ndarray, np.ndarray]:
        query.validate(self.policy)
        tile.validate(self.policy)
        if min(len(query.keypoints), len(tile.keypoints)) < 6:
            return np.empty((0, 2), np.int64), np.empty(0, np.float32)
        def side(features):
            return {"keypoints": self.torch.from_numpy(features.keypoints[None]).to(self.device),
                    "descriptors": self.torch.from_numpy(features.descriptors[None]).to(self.device),
                    "image_size": self.torch.tensor([[self.policy.image_width, self.policy.image_height]],
                                                    dtype=self.torch.float32, device=self.device)}
        with self.torch.inference_mode():
            raw = self.matcher({"image0": side(query), "image1": side(tile)})
        pairs = raw["matches"][0].detach().cpu().numpy()
        scores = raw["scores"][0].detach().cpu().numpy().astype(np.float32)
        validate_matches(query, tile, pairs, scores)
        return pairs.astype(np.int64), scores


def validate_matches(query: LocalFeatures, tile: LocalFeatures, pairs: np.ndarray, scores: np.ndarray) -> None:
    if (pairs.ndim != 2 or pairs.shape[1] != 2 or not np.issubdtype(pairs.dtype, np.integer)
            or scores.shape != (len(pairs),) or not np.isfinite(scores).all()
            or np.any(scores < 0) or np.any(scores > 1)):
        raise ValueError("Invalid learned correspondence output")
    if (np.any(pairs < 0) or np.any(pairs >= [len(query.keypoints), len(tile.keypoints)])
            or len(set(pairs[:, 0].tolist())) != len(pairs) or len(set(pairs[:, 1].tolist())) != len(pairs)):
        raise ValueError("Learned correspondence indices are out of range or not one-to-one")


def load_local_matcher(config_path: str | Path):
    path = Path(config_path).expanduser().resolve()
    config = json.loads(path.read_text(encoding="utf-8"))
    if config.get("backend") == "onnx_cpu":
        from .local_onnx import OnnxLocalMatcher
        if (config.get("schema_version") != LOCAL_CONFIG_SCHEMA or config.get("px4_external_vision") != "disabled"
                or not isinstance(config.get("export_dir"), str) or not config["export_dir"]
                or set(config) - {"schema_version", "backend", "px4_external_vision", "export_dir", "elevation_spec"}):
            raise ValueError("ONNX local config must use the export policy without overrides")
        matcher = OnnxLocalMatcher((path.parent / Path(config["export_dir"]).expanduser()).resolve())
        matcher.provenance["config_sha256"] = sha256_file(path)
        return matcher
    return PinnedLocalMatcher(path)


def match_image_pair(config: str | Path, query_path: str | Path, map_path: str | Path) -> dict[str, Any]:
    matcher = load_local_matcher(config)
    query, tile = [matcher.extract(cv2.imread(str(p))) for p in (query_path, map_path)]
    pairs, scores = matcher.match(query, tile)
    geometry = verify_planar_correspondences(query_keypoints=query.keypoints, map_keypoints=tile.keypoints,
        match_pairs=pairs, scores=scores, query_width=matcher.policy.image_width, query_height=matcher.policy.image_height,
        **matcher.policy.geometry_kwargs())
    return {"schema_version": "vision_nav_learned_pair_evidence_v1", "provenance": matcher.provenance,
            "query_sha256": sha256_file(Path(query_path)), "map_sha256": sha256_file(Path(map_path)),
            "feature_counts": {"query": len(query.keypoints), "map": len(tile.keypoints)},
            "match_count": len(pairs), "geometry": geometry, "position_authority": False,
            "navigation_mode": "NO_GLOBAL_FIX", "px4_external_vision": "disabled"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Run pinned learned correspondence on a research image pair, without flight output")
    for name in ("config", "query", "map", "output"):
        parser.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.output.exists():
        parser.error("Evidence output already exists")
    try:
        report = match_image_pair(args.config, args.query, args.map)
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(2, f"Learned pair rejected: {exc}\n")
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
