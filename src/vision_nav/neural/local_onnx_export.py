"""Export real learned local models and compare against the pinned reference."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path

import cv2
import numpy as np

from vision_nav.training_capture import sha256_file
from .learned_correspondence import PinnedLocalMatcher, local_image
from .local_onnx import (LOCAL_ONNX_SCHEMA, PREPROCESSING, ATOL, RTOL, MATCH_INPUTS, MATCH_OUTPUTS,
                         OnnxLocalMatcher, canonical_features, sparse_features, feature_parity, matching_feed,
                         matching_result)
from .onnx_runtime import _onnx_session_factory
from .xfeat_export import _fixed_sparse_xfeat_wrapper


def export_local_onnx(config: str | Path, images: list[str | Path], output_dir: str | Path) -> dict:
    """Export batch-one local inference; no downloads or implicit weight choices."""
    import torch
    import onnx
    from kornia.feature.lightglue import normalize_keypoints, filter_matches

    root = Path(output_dir).expanduser().resolve()
    if root.exists():
        raise ValueError("Local ONNX output already exists; use a new release directory")
    if not 2 <= len(images) <= 12:
        raise ValueError("Supply two to twelve real parity images, query first and matching map second")
    reference = PinnedLocalMatcher(config)
    if reference.device != "cpu":
        raise ValueError("Local ONNX reference export currently requires device=cpu")
    policy = reference.policy
    paths = [Path(path).expanduser().resolve() for path in images]
    bgrs = [cv2.imread(str(path)) for path in paths]
    tensors = [local_image(image, policy) for image in bgrs]
    features = [canonical_features(reference.extract(image), policy) for image in bgrs]
    if min(len(features[0].keypoints), len(features[1].keypoints)) < 16:
        raise ValueError("First two parity images need at least sixteen learned keypoints each")
    reference_pairs, _ = reference.match(features[0], features[1])
    if len(reference_pairs) < 6:
        raise ValueError("First image pair must have at least six learned matches to exercise positive parity")
    root.mkdir(parents=True)
    parity_dir = root / "parity"
    parity_dir.mkdir()
    extractor = _fixed_sparse_xfeat_wrapper(reference.extractor.net, torch,
        image_width=policy.image_width, image_height=policy.image_height,
        maximum_feature_count=policy.max_features, detection_threshold=policy.detection_threshold).eval()

    class MatcherGraph(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.matcher = reference.matcher
            self.register_buffer("image_size", torch.tensor([[policy.image_width, policy.image_height]], dtype=torch.float32))

        def forward(self, keypoints0, descriptors0, keypoints1, descriptors1):
            # Use upstream learned layers unchanged, but leave Python input
            # assertions, early exit and ragged list creation outside the graph.
            encoding0 = self.matcher.posenc(normalize_keypoints(keypoints0, self.image_size))
            encoding1 = self.matcher.posenc(normalize_keypoints(keypoints1, self.image_size))
            desc0, desc1 = self.matcher.input_proj(descriptors0), self.matcher.input_proj(descriptors1)
            for layer in self.matcher.transformers:
                desc0, desc1 = layer(desc0, desc1, encoding0, encoding1)
            assignment, _ = self.matcher.log_assignment[-1](desc0, desc1)
            matches0, _, scores0, _ = filter_matches(assignment, policy.minimum_score)
            return matches0, scores0

    matcher = MatcherGraph().eval()
    extractor_path, matcher_path = root / "xfeat.onnx", root / "lighterglue.onnx"
    with torch.inference_mode():
        torch.onnx.export(extractor, torch.from_numpy(tensors[0]), str(extractor_path),
            input_names=["image"], output_names=["keypoints", "descriptors", "scores"],
            opset_version=17, dynamo=False)
        feed = matching_feed(features[0], features[1])
        q_count = torch.export.Dim("query_count", min=6, max=policy.max_features)
        t_count = torch.export.Dim("map_count", min=6, max=policy.max_features)
        torch.onnx.export(matcher, tuple(torch.from_numpy(feed[key]) for key in MATCH_INPUTS), str(matcher_path),
            input_names=list(MATCH_INPUTS), output_names=list(MATCH_OUTPUTS), opset_version=18, dynamo=True,
            external_data=False, dynamic_shapes=({1: q_count}, {1: q_count}, {1: t_count}, {1: t_count}))
    for path in (extractor_path, matcher_path):
        onnx.checker.check_model(str(path), full_check=True)
    extractor_session = _onnx_session_factory(extractor_path, ["CPUExecutionProvider"])
    matcher_session = _onnx_session_factory(matcher_path, ["CPUExecutionProvider"])
    samples, measurements = [], []

    def save_sample(kind, arrays):
        path = parity_dir / f"{len(samples):02d}_{kind}.npz"
        np.savez_compressed(path, **arrays)
        samples.append({"path": str(path.relative_to(root)), "sha256": sha256_file(path), "kind": kind})

    blank = np.zeros((policy.image_height, policy.image_width, 3), np.uint8)
    actual_features = []
    for index, (image, expected) in enumerate(zip(bgrs + [blank], features + [reference.extract(blank)])):
        tensor = local_image(image, policy)
        actual = sparse_features(extractor_session.run(None, {"image": tensor}), policy)
        check = feature_parity(expected, actual, policy)
        check.update(stage="xfeat", image_sha256=sha256_file(paths[index]) if index < len(paths) else None,
                     kind="image" if index < len(paths) else "blank")
        measurements.append(check)
        if not check["passed"] or (index == len(paths) and len(actual.keypoints)):
            raise ValueError(f"Pinned XFeat vs ONNX feature parity failed: {check}")
        save_sample(check["kind"], {"image": tensor, "keypoints": expected.keypoints,
                                   "descriptors": expected.descriptors, "scores": expected.scores})
        actual_features.append(actual)

    q, t = features[:2]
    cases = [("pair", q, t), ("unequal_pair", q.selected(np.arange(min(61, len(q.keypoints)))),
                             t.selected(np.arange(min(13, len(t.keypoints))))),
             ("minimum_pair", q.selected(np.arange(6)), t.selected(np.arange(6)))]
    for kind, query, tile in cases:
        feed = matching_feed(query, tile)
        with torch.inference_mode():
            data = {f"image{i}": {"keypoints": torch.from_numpy(feed[f"keypoints{i}"]),
                                  "descriptors": torch.from_numpy(feed[f"descriptors{i}"]),
                                  "image_size": matcher.image_size} for i in (0, 1)}
            expected_dict = reference.matcher(data)
            expected = [expected_dict[name].numpy() for name in MATCH_OUTPUTS]
        actual = matcher_session.run(list(MATCH_OUTPUTS), feed)
        matching_result(query, tile, actual, policy.minimum_score)
        passed = np.array_equal(expected[0], actual[0]) and np.allclose(expected[1], actual[1], atol=ATOL, rtol=RTOL)
        check = {"stage": "lighterglue", "kind": kind, "passed": bool(passed),
                 "query_count": len(query.keypoints), "map_count": len(tile.keypoints),
                 "matches": int(np.count_nonzero(actual[0] >= 0)),
                 "max_score_error": float(np.abs(expected[1] - actual[1]).max())}
        measurements.append(check)
        if not passed:
            raise ValueError(f"Pinned LighterGlue vs ONNX parity failed: {check}")
        save_sample(kind, {**feed, **dict(zip(MATCH_OUTPUTS, expected))})
    # Test the complete cascade too: tiny descriptor export errors must not
    # change discrete correspondences when propagated through attention.
    pairs, scores = matching_result(actual_features[0], actual_features[1],
        matcher_session.run(list(MATCH_OUTPUTS), matching_feed(*actual_features[:2])), policy.minimum_score)
    expected_pairs, expected_scores = reference.match(features[0], features[1])
    cascade_ok = np.array_equal(pairs, expected_pairs) and np.allclose(scores, expected_scores, atol=ATOL, rtol=RTOL)
    measurements.append({"stage": "cascade", "passed": bool(cascade_ok), "matches": len(pairs)})
    if not cascade_ok:
        raise ValueError("Full XFeat/LighterGlue cascade correspondence parity failed")
    manifest = {"schema_version": LOCAL_ONNX_SCHEMA, "status": "parity_passed", "publication_mode": "research_only",
        "px4_external_vision": "disabled", "preprocessing": PREPROCESSING, "policy": asdict(policy),
        "reference": reference.provenance, "graphs": {
            name: {"path": path.name, "sha256": sha256_file(path)}
            for name, path in (("xfeat", extractor_path), ("lighterglue", matcher_path))},
        "sources": {name: sha256_file(Path(__file__).with_name(filename)) for name, filename in
                    (("runtime", "local_onnx.py"), ("preprocessing", "learned_correspondence.py"),
                     ("export", "local_onnx_export.py"), ("sparse_wrapper", "xfeat_export.py"))},
        "parity_samples": samples, "measurements": measurements, "tolerances": {"atol": ATOL, "rtol": RTOL},
        "exporter": {"xfeat": "torchscript_opset17", "lighterglue": "torch_export_opset18"}, "onnx_version": onnx.__version__,
        "limitations": ["sample_parity_not_field_accuracy", "not_pi_thermal_or_latency_validated",
                        "not_full_vehicle_model_release", "six_to_max_features_dynamic_attention_no_padding"]}
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    try:
        OnnxLocalMatcher(root)
    except Exception:
        # Failed startup verification must not leave an apparently usable export.
        manifest["status"] = "startup_parity_failed"
        manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        raise
    return manifest


def main() -> None:
    parser = argparse.ArgumentParser(description="Export and verify pinned XFeat/LighterGlue for offline ONNX CPU replay")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--images", type=Path, nargs="+", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    try:
        manifest = export_local_onnx(args.config, args.images, args.output_dir)
    except (ValueError, RuntimeError, OSError) as exc:
        parser.exit(2, f"Local export rejected: {exc}\n")
    print(json.dumps(manifest, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
