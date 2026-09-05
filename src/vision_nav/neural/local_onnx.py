"""Offline XFeat/LighterGlue CPU inference with recorded export parity.

No PyTorch, checkpoint checkout, network, or flight transport is needed here.
This two-stage research artifact is not a promoted vehicle ModelRelease.
"""

from __future__ import annotations

from dataclasses import asdict
import json
from pathlib import Path
from typing import Any

import numpy as np

from vision_nav.training_capture import sha256_file
from .learned_correspondence import LocalFeatures, LocalMatchingPolicy, local_image, validate_matches
from .onnx_runtime import _onnx_session_factory
from .retrieval_export import export_member


LOCAL_ONNX_SCHEMA = "vision_nav_local_onnx_v1"
PREPROCESSING = "opencv_linear_rgb_zero_one_nchw_fixed_multiple_of_32"
ATOL, RTOL = 1e-4, 1e-3
MATCH_INPUTS = ("keypoints0", "descriptors0", "keypoints1", "descriptors1")
MATCH_OUTPUTS = ("matches0", "matching_scores0")


def canonical_features(features: LocalFeatures, policy: LocalMatchingPolicy) -> LocalFeatures:
    """Compare by exact pixel identity, not provider-dependent score ordering."""
    features.validate(policy)
    if len(np.unique(features.keypoints, axis=0)) != len(features.keypoints):
        raise ValueError("Duplicate local keypoints")
    order = np.lexsort((features.keypoints[:, 0], features.keypoints[:, 1]))
    return features.selected(order)


def sparse_features(values: list[np.ndarray], policy: LocalMatchingPolicy) -> LocalFeatures:
    points, descriptors, scores = (np.asarray(value) for value in values)
    n = policy.max_features
    if (points.shape != (1, n, 2) or descriptors.shape != (1, n, 64) or scores.shape != (1, n)
            or any(value.dtype != np.float32 or not np.isfinite(value).all() for value in (points, descriptors, scores))
            or np.any((scores < 0) & (scores != -1)) or np.any(scores > 1)):
        raise ValueError("Invalid fixed-cardinality XFeat output")
    valid = scores[0] > 0
    return canonical_features(LocalFeatures(points[0, valid], descriptors[0, valid], scores[0, valid]), policy)


def matching_feed(query: LocalFeatures, tile: LocalFeatures) -> dict[str, np.ndarray]:
    return {"keypoints0": query.keypoints[None], "descriptors0": query.descriptors[None],
            "keypoints1": tile.keypoints[None], "descriptors1": tile.descriptors[None]}


def matching_result(query: LocalFeatures, tile: LocalFeatures, values: list[np.ndarray],
                    minimum_score: float) -> tuple[np.ndarray, np.ndarray]:
    matches, scores = (np.asarray(value) for value in values)
    if (matches.dtype != np.int64 or matches.shape != (1, len(query.keypoints))
            or scores.dtype != np.float32 or scores.shape != matches.shape
            or not np.isfinite(scores).all() or np.any(scores < 0) or np.any(scores > 1)
            or np.any(matches < -1) or np.any(matches >= len(tile.keypoints))):
        raise ValueError("Invalid LighterGlue ONNX output")
    valid = matches[0] >= 0
    if np.any(scores[0, valid] <= minimum_score):
        raise ValueError("LighterGlue ONNX accepted a below-threshold correspondence")
    pairs = np.column_stack((np.flatnonzero(valid), matches[0, valid])).astype(np.int64)
    selected_scores = scores[0, valid]
    validate_matches(query, tile, pairs, selected_scores)
    return pairs, selected_scores


def feature_parity(expected: LocalFeatures, actual: LocalFeatures, policy: LocalMatchingPolicy) -> dict[str, Any]:
    expected, actual = canonical_features(expected, policy), canonical_features(actual, policy)
    same_points = np.array_equal(expected.keypoints, actual.keypoints)
    equal = same_points and np.allclose(expected.descriptors, actual.descriptors, atol=ATOL, rtol=RTOL)
    equal = equal and np.allclose(expected.scores, actual.scores, atol=ATOL, rtol=RTOL)
    return {"passed": bool(equal), "reference_count": len(expected.keypoints), "actual_count": len(actual.keypoints),
            "same_keypoints": same_points,
            "max_descriptor_error": float(np.abs(expected.descriptors - actual.descriptors).max())
                if same_points and len(expected.keypoints) else None,
            "max_score_error": float(np.abs(expected.scores - actual.scores).max())
                if same_points and len(expected.keypoints) else None}


def _checked_member(root: Path, entry: dict[str, Any]) -> Path:
    path = export_member(root, entry["path"])
    if sha256_file(path) != entry.get("sha256"):
        raise ValueError(f"Local ONNX artifact checksum mismatch: {path.name}")
    return path


def _interface(session, inputs: dict[str, list[Any]], outputs: dict[str, list[Any]]) -> None:
    for nodes, expected in ((session.get_inputs(), inputs), (session.get_outputs(), outputs)):
        actual = {node.name: node for node in nodes}
        if set(actual) != set(expected):
            raise ValueError("Local ONNX tensor names mismatch")
        for name, shape in expected.items():
            dtype = "tensor(int64)" if name == "matches0" else "tensor(float)"
            if actual[name].type != dtype or actual[name].shape != shape:
                raise ValueError(f"Local ONNX tensor interface mismatch: {name}")


def _run(session, names: list[str], feed: dict[str, np.ndarray]) -> list[np.ndarray]:
    try:
        return session.run(names, feed)
    except Exception as exc:
        # ORT's pybind exceptions are not RuntimeError subclasses. Normalize
        # them so replay can retain a failed attempt and clear temporal state.
        raise RuntimeError(f"Local ONNX inference failed: {type(exc).__name__}: {exc}") from exc


class OnnxLocalMatcher:
    def __init__(self, export_dir: str | Path):
        root = Path(export_dir).expanduser().resolve()
        manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        if (manifest.get("schema_version") != LOCAL_ONNX_SCHEMA or manifest.get("status") != "parity_passed"
                or manifest.get("publication_mode") != "research_only" or manifest.get("px4_external_vision") != "disabled"
                or manifest.get("preprocessing") != PREPROCESSING):
            raise ValueError("Unsupported or unverified local ONNX export")
        self.policy = LocalMatchingPolicy(**manifest["policy"])
        self.policy.validate()
        provenance = manifest["reference"]
        for key in ("xfeat_weights_sha256", "lighterglue_weights_sha256", "kornia_lightglue_source_sha256"):
            digest = provenance.get(key, "")
            if not isinstance(digest, str) or len(digest) != 64 or set(digest) - set("0123456789abcdef"):
                raise ValueError("Missing learned local checkpoint/source identity")
        graphs = {key: _checked_member(root, manifest["graphs"][key]) for key in ("xfeat", "lighterglue")}
        # Bind input/output handling, not just learned weights. Re-export after
        # changing the runtime contract instead of silently reusing stale parity.
        for key, filename in (("runtime", "local_onnx.py"), ("preprocessing", "learned_correspondence.py")):
            if manifest["sources"][key] != sha256_file(Path(__file__).with_name(filename)):
                raise ValueError("Local ONNX runtime/preprocessing source changed; re-export")
        samples = manifest["parity_samples"]
        if not isinstance(samples, list) or not 5 <= len(samples) <= 32:
            raise ValueError("Missing bounded local ONNX parity sample set")
        paths = [_checked_member(root, sample) for sample in samples]
        self.extractor = _onnx_session_factory(graphs["xfeat"], ["CPUExecutionProvider"])
        self.matcher = _onnx_session_factory(graphs["lighterglue"], ["CPUExecutionProvider"])
        import onnxruntime as ort
        if any(session.get_providers() != ["CPUExecutionProvider"] for session in (self.extractor, self.matcher)):
            raise ValueError("Local ONNX research artifact requires the CPU provider")
        p = self.policy
        _interface(self.extractor, {"image": [1, 3, p.image_height, p.image_width]},
                   {"keypoints": [1, p.max_features, 2], "descriptors": [1, p.max_features, 64], "scores": [1, p.max_features]})
        _interface(self.matcher,
                   {"keypoints0": [1, "query_count", 2], "descriptors0": [1, "query_count", 64],
                    "keypoints1": [1, "map_count", 2], "descriptors1": [1, "map_count", 64]},
                   {"matches0": [1, "query_count"], "matching_scores0": [1, "query_count"]})
        kinds = set()
        image_features, cascade_reference = [], None
        for sample, path in zip(samples, paths):
            with np.load(path, allow_pickle=False) as arrays:
                kind = sample["kind"]
                kinds.add(kind)
                if kind in {"image", "blank"}:
                    image = arrays["image"]
                    if (image.shape != (1, 3, p.image_height, p.image_width) or image.dtype != np.float32
                            or not np.isfinite(image).all() or np.any(image < 0) or np.any(image > 1)):
                        raise ValueError("Invalid local parity image")
                    expected = LocalFeatures(*(arrays[name] for name in ("keypoints", "descriptors", "scores")))
                    actual = self._extract_tensor(image)
                    if not feature_parity(expected, actual, p)["passed"] or (kind == "blank" and len(actual.keypoints)):
                        raise ValueError("XFeat local startup parity failed")
                    if kind == "image":
                        image_features.append(actual)
                elif kind in {"pair", "unequal_pair", "minimum_pair"}:
                    q, t = [LocalFeatures(arrays[f"keypoints{i}"][0], arrays[f"descriptors{i}"][0],
                                          np.ones(arrays[f"keypoints{i}"].shape[1], np.float32)) for i in (0, 1)]
                    if ((kind == "minimum_pair" and (len(q.keypoints), len(t.keypoints)) != (6, 6))
                            or (kind == "unequal_pair" and len(q.keypoints) == len(t.keypoints))):
                        raise ValueError("Local parity case does not cover its declared feature counts")
                    actual = self._match_raw(q, t)
                    if (not np.array_equal(actual[0], arrays["matches0"])
                            or not np.allclose(actual[1], arrays["matching_scores0"], atol=ATOL, rtol=RTOL)):
                        raise ValueError("LighterGlue local startup parity failed")
                    if kind == "pair":
                        cascade_reference = (q, t, arrays["matches0"], arrays["matching_scores0"])
                else:
                    raise ValueError("Unknown local parity sample kind")
        if not {"image", "blank", "pair", "unequal_pair", "minimum_pair"} <= kinds:
            raise ValueError("Incomplete learned local parity coverage")
        if len(image_features) < 2 or cascade_reference is None:
            raise ValueError("Missing full local cascade parity inputs")
        q, t, pairs, scores = cascade_reference
        if not (np.array_equal(q.keypoints, image_features[0].keypoints)
                and np.array_equal(t.keypoints, image_features[1].keypoints)):
            raise ValueError("Local cascade parity images do not match its feature inputs")
        actual = self._match_raw(*image_features[:2])
        if not np.array_equal(actual[0], pairs) or not np.allclose(actual[1], scores, atol=ATOL, rtol=RTOL):
            raise ValueError("Full learned local startup cascade parity failed")
        self.provenance = {"schema_version": LOCAL_ONNX_SCHEMA, "backend": "onnx_cpu", "device": "cpu",
            "onnxruntime_version": ort.__version__, "providers": self.matcher.get_providers(),
            "export_manifest_sha256": sha256_file(root / "manifest.json"), "graphs": manifest["graphs"],
            "reference": provenance, "policy": asdict(p), "preprocessing": PREPROCESSING,
            "startup_parity": "passed", "px4_external_vision": "disabled",
            "limitations": ["not_pi_thermal_or_field_validated", "scores_not_calibrated_covariance",
                            "full_attention_pruning_disabled"]}

    def _extract_tensor(self, image: np.ndarray) -> LocalFeatures:
        return sparse_features(_run(self.extractor, ["keypoints", "descriptors", "scores"], {"image": image}), self.policy)

    def extract(self, bgr: np.ndarray) -> LocalFeatures:
        return self._extract_tensor(local_image(bgr, self.policy))

    def _match_raw(self, query: LocalFeatures, tile: LocalFeatures) -> list[np.ndarray]:
        query.validate(self.policy)
        tile.validate(self.policy)
        if min(len(query.keypoints), len(tile.keypoints)) < 6:
            raise ValueError("ONNX attention requires at least six real features per image")
        values = _run(self.matcher, list(MATCH_OUTPUTS), matching_feed(query, tile))
        matching_result(query, tile, values, self.policy.minimum_score)
        return values

    def match(self, query: LocalFeatures, tile: LocalFeatures) -> tuple[np.ndarray, np.ndarray]:
        query.validate(self.policy)
        tile.validate(self.policy)
        if min(len(query.keypoints), len(tile.keypoints)) < 6:
            return np.empty((0, 2), np.int64), np.empty(0, np.float32)
        return matching_result(query, tile, self._match_raw(query, tile), self.policy.minimum_score)
