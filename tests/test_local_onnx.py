import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
from types import SimpleNamespace

import cv2
import numpy as np
import pytest

from vision_nav.neural.learned_correspondence import LocalMatchingPolicy, LocalFeatures, load_local_matcher, match_image_pair
from vision_nav.neural.local_onnx import (OnnxLocalMatcher, sparse_features, canonical_features, matching_result,
                                         feature_parity)
from vision_nav.neural.local_onnx_export import export_local_onnx
from vision_nav.training_capture import sha256_file
from test_map_conditioned_replay import artifacts
from test_replay_retrieval import retrieval_artifacts, automatic


def features(n=16):
    return LocalFeatures(np.c_[np.arange(n), np.arange(n)].astype(np.float32),
                         np.eye(n, 64, dtype=np.float32), np.ones(n, np.float32))


def test_sparse_invalid_slots_are_removed_before_attention():
    p = LocalMatchingPolicy(max_features=16)
    f = features()
    scores = f.scores.copy()
    scores[6:] = -1
    result = sparse_features([f.keypoints[None], f.descriptors[None], scores[None]], p)
    assert len(result.keypoints) == 6


@pytest.mark.parametrize("failure", ["nan", "out_of_bounds", "bad_score", "duplicate", "unnormalized"])
def test_sparse_contract_rejects_bad_provider_output(failure):
    p = LocalMatchingPolicy(max_features=16)
    f = features()
    if failure == "nan":
        f.descriptors[0, 0] = np.nan
    elif failure == "out_of_bounds":
        f.keypoints[0, 0] = 1000
    elif failure == "bad_score":
        f.scores[0] = -0.5
    elif failure == "duplicate":
        f.keypoints[0] = f.keypoints[1]
    else:
        f.descriptors[0] = 0
    with pytest.raises(ValueError):
        sparse_features([f.keypoints[None], f.descriptors[None], f.scores[None]], p)


def test_feature_parity_allows_order_not_different_pixel_sets():
    p, f = LocalMatchingPolicy(), features()
    assert feature_parity(f, f.selected(np.arange(15, -1, -1)), p)["passed"]
    other = features()
    other.keypoints[0] += 0.01
    assert not feature_parity(f, other, p)["passed"]


@pytest.mark.parametrize("failure", ["duplicate", "out_of_range", "below_threshold", "nonfinite", "bad_shape"])
def test_matcher_contract_rejects_bad_provider_output(failure):
    f = features()
    matches, scores = np.arange(16, dtype=np.int64)[None], np.ones((1, 16), np.float32)
    if failure == "duplicate":
        matches[0, 1] = 0
    elif failure == "out_of_range":
        matches[0, 0] = 16
    elif failure == "below_threshold":
        scores[0, 0] = 0.1
    elif failure == "nonfinite":
        scores[0, 0] = np.nan
    else:
        matches = matches[0]
    with pytest.raises(ValueError):
        matching_result(f, f, [matches, scores], 0.2)


def test_onnx_configuration_cannot_override_export_policy(tmp_path):
    path = tmp_path / "config.json"
    path.write_text(json.dumps({"schema_version": "vision_nav_learned_local_reference_v1", "backend": "onnx_cpu",
                               "px4_external_vision": "disabled", "export_dir": "missing", "policy": {}}))
    with pytest.raises(ValueError, match="without overrides"):
        load_local_matcher(path)


@pytest.mark.parametrize("stage", ["extract", "match"])
def test_provider_exceptions_are_normalized_for_replay(stage):
    class ProviderError(Exception):
        pass
    def failure(*args):
        raise ProviderError("provider unavailable")
    matcher = OnnxLocalMatcher.__new__(OnnxLocalMatcher)
    matcher.policy = LocalMatchingPolicy()
    matcher.extractor = matcher.matcher = SimpleNamespace(run=failure)
    with pytest.raises(RuntimeError, match="Local ONNX inference failed: ProviderError"):
        if stage == "extract":
            matcher.extract(np.zeros((384, 640, 3), np.uint8))
        else:
            matcher.match(features(), features())


@pytest.fixture(scope="module")
def local_export(tmp_path_factory):
    config = os.environ.get("VISION_NAV_LOCAL_REFERENCE_CONFIG")
    if not config:
        pytest.skip("Set VISION_NAV_LOCAL_REFERENCE_CONFIG to test actual pretrained ONNX exports")
    config = Path(config)
    checkout = (config.parent / json.loads(config.read_text())["checkout"]).resolve()
    images = [checkout / "assets" / "ref.png", checkout / "assets" / "tgt.png"]
    root = tmp_path_factory.mktemp("local_onnx")
    # Exercise realistic perturbations in feature parity, not only the pair
    # used to trace the model. These are export checks, not pose ground truth.
    original = cv2.imread(str(images[0]))
    cv2.imwrite(str(root / "blurred.png"), cv2.GaussianBlur(original, (7, 7), 1.5))
    cv2.imwrite(str(root / "rotated.png"), cv2.rotate(original, cv2.ROTATE_90_CLOCKWISE))
    export = root / "export"
    report = export_local_onnx(config, images + [root / "blurred.png", root / "rotated.png"], export)
    path = root / "config.json"
    path.write_text(json.dumps({"schema_version": "vision_nav_learned_local_reference_v1", "backend": "onnx_cpu",
                               "px4_external_vision": "disabled", "export_dir": str(export)}))
    return export, path, images, report


def test_real_export_covers_stages_unequal_counts_and_cascade(local_export):
    export, config, images, report = local_export
    assert report["status"] == "parity_passed"
    assert all(m["passed"] for m in report["measurements"])
    assert {m["stage"] for m in report["measurements"]} == {"xfeat", "lighterglue", "cascade"}
    unequal = next(m for m in report["measurements"] if m.get("kind") == "unequal_pair")
    assert unequal["query_count"] != unequal["map_count"]
    result = match_image_pair(config, *images)
    assert result["match_count"] >= 20 and result["geometry"]["status"] == "accepted"
    assert result["provenance"]["startup_parity"] == "passed"
    assert result["position_authority"] is False


def test_blank_and_fewer_than_six_features_never_enter_attention(local_export, monkeypatch):
    matcher = OnnxLocalMatcher(local_export[0])
    blank = matcher.extract(np.zeros((384, 640, 3), np.uint8))
    assert len(blank.keypoints) == 0
    def forbidden(*args):
        pytest.fail("insufficient features entered attention")
    monkeypatch.setattr(matcher, "_match_raw", forbidden)
    for query in (blank, features(5)):
        assert len(matcher.match(query, features())[0]) == 0


def test_onnx_pair_runs_in_a_process_without_torch_or_kornia(local_export):
    _, config, images, _ = local_export
    code = """
import sys, importlib.abc
class BlockResearch(importlib.abc.MetaPathFinder):
    def find_spec(self, fullname, path=None, target=None):
        if fullname.split('.')[0] in {'torch', 'kornia'}:
            raise AssertionError('Research dependency imported: ' + fullname)
sys.meta_path.insert(0, BlockResearch())
from vision_nav.neural.learned_correspondence import match_image_pair
r = match_image_pair(*sys.argv[1:])
assert r['match_count'] >= 20
assert not r['position_authority']
assert not {'torch', 'kornia'} & set(sys.modules)
print(r['match_count'])
"""
    result = subprocess.run([sys.executable, "-c", code, str(config), *(str(p) for p in images)],
                            capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize("failure", ["graph", "parity", "source", "missing_case", "recorded_scores"])
def test_tampered_exports_are_rejected(local_export, tmp_path, failure):
    root = tmp_path / "export"
    shutil.copytree(local_export[0], root)
    manifest = json.loads((root / "manifest.json").read_text())
    if failure == "graph":
        (root / "xfeat.onnx").write_bytes(b"corrupt graph")
    elif failure == "parity":
        (root / manifest["parity_samples"][0]["path"]).write_bytes(b"corrupt reference")
    elif failure == "source":
        manifest["sources"]["runtime"] = "0" * 64
    elif failure == "missing_case":
        manifest["parity_samples"] = [s for s in manifest["parity_samples"] if s["kind"] != "minimum_pair"]
    else:
        entry = next(s for s in manifest["parity_samples"] if s["kind"] == "pair")
        path = root / entry["path"]
        with np.load(path) as arrays:
            values = {key: arrays[key] for key in arrays.files}
        values["matching_scores0"] = values["matching_scores0"] + np.float32(0.05)
        np.savez_compressed(path, **values)
        entry["sha256"] = sha256_file(path)
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        OnnxLocalMatcher(root)


def test_automatic_replay_uses_real_onnx_local_pipeline(local_export, artifacts, retrieval_artifacts, tmp_path):
    result = automatic(artifacts, retrieval_artifacts, tmp_path / "out", local_verification_config=local_export[1])
    assert result["provenance"]["local_verification"]["backend"] == "onnx_cpu"
    row = json.loads((tmp_path / "out" / "trace.jsonl").read_text().splitlines()[-1])
    assert row["local_verification"]["complete"] is True
    assert all(a["status"] == "verified" for a in row["local_verification"]["attempts"])
    assert row["position_authority"] is False and row["navigation_mode"] == "NO_GLOBAL_FIX"
