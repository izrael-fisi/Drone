"""Opt-in actual MegaLoc export tests; fixture geography is not an accuracy benchmark."""

import builtins
import json
import os
from pathlib import Path

import cv2
import numpy as np
import pytest

from vision_nav.neural.replay_retrieval import build_replay_retrieval_index, ReplayCandidateRetriever
from vision_nav.neural.retrieval_export import RetrievalOnnxProbe
from vision_nav.neural.retrieval_preprocessing import ANTIALIAS_ID
from test_map_conditioned_replay import artifacts, run


@pytest.fixture(scope="module")
def megaloc_export():
    value = os.environ.get("VISION_NAV_MEGALOC_EXPORT")
    if not value:
        pytest.skip("Set VISION_NAV_MEGALOC_EXPORT to test the explicitly provisioned official checkpoint export")
    root = Path(value).expanduser().resolve()
    manifest = json.loads((root / "manifest.json").read_text())
    assert manifest["provenance"]["model_family"] == "MegaLoc"
    assert manifest["provenance"]["checkpoint_sha256"] == "d4f9f2bcb60018f91eb6a8e061ed054fd55654e10c2569cf13841ea986ffb4f8"
    assert manifest["provenance"]["source_revision"] == "5fe0dd697c4a70ba3e23607f6716ab3c606b16db"
    assert manifest["preprocessing"]["id"] == ANTIALIAS_ID
    assert manifest["provenance"]["checkpoint_source"]["revision"] == "37bb43d65dd6388d1578052de5eb0bcdceb497e7"
    return root


def test_actual_megaloc_startup_raw_inputs_and_cpu_runtime(megaloc_export, monkeypatch):
    original = builtins.__import__
    def without_torch(name, *args, **kwargs):
        if name.split(".")[0] in ("torch", "torchvision"):
            raise AssertionError("CPU deployment must not import Torch")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", without_torch)
    probe = RetrievalOnnxProbe(megaloc_export)
    assert probe.dimension == 8448
    with np.load(megaloc_export / probe.manifest["preprocessing_parity"]["path"]) as saved:
        images = [saved[f"raw_{i}"] for i in range(len(saved["reference_inputs"]))]
    with np.load(megaloc_export / probe.manifest["parity"]["path"]) as saved:
        expected = saved["embeddings"]
    actual = np.stack([probe.embed(image) for image in images])
    expected /= np.linalg.norm(expected, axis=1, keepdims=True)
    np.testing.assert_allclose(actual, expected, atol=1e-4, rtol=1e-3)
    assert probe.manifest["field_ready"] is False


def test_actual_megaloc_indexes_and_replays_fixture_map(megaloc_export, artifacts, tmp_path):
    index = tmp_path / "index"
    report = build_replay_retrieval_index(megaloc_export, artifacts / "reference.json",
                                        artifacts / "tiles" / "tile_layout.json", index)
    assert report["descriptor_dimension"] == 8448
    assert report["embedding_bytes"] == report["tile_count"] * 8448 * 4
    retriever = ReplayCandidateRetriever(megaloc_export, index, artifacts / "reference.json",
                                        artifacts / "tiles" / "tile_layout.json", top_k=2)
    layout = json.loads((artifacts / "tiles" / "tile_layout.json").read_text())
    for tile in layout["tiles"]:
        matches = retriever.search(cv2.imread(str(artifacts / "tiles" / tile["image_uri"])))
        assert matches[0]["tile_id"] == tile["tile_id"]
        assert matches[0]["score"] == pytest.approx(1, abs=1e-5)
    del retriever
    args = {"candidate_tile_ids": None, "retrieval_export": megaloc_export, "retrieval_index": index,
            "retrieval_top_k": 2}
    a = run(artifacts, tmp_path / "a", **args)
    b = run(artifacts, tmp_path / "b", **args)
    assert a["counts"]["retrieval_calls"] == 1
    assert a["counts"]["candidate_calls"] == 2
    assert a["decision_sha256_excluding_latency"] == b["decision_sha256_excluding_latency"]
    assert a["navigation_mode"] == "NO_GLOBAL_FIX"
    assert a["position_authority"] is False
    assert a["px4_external_vision"] == "disabled"
