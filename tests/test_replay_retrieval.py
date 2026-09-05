import json
import shutil

import cv2
import numpy as np
import pytest

from vision_nav.training_capture import sha256_file, write_session_checksums
from vision_nav.neural.retrieval_export import export_retrieval_onnx, RetrievalOnnxProbe, normalized_embedding
from vision_nav.neural.replay_retrieval import build_replay_retrieval_index, ReplayCandidateRetriever
from test_map_conditioned_replay import artifacts, run


@pytest.fixture(scope="module")
def retrieval_artifacts(artifacts, tmp_path_factory):
    torch = pytest.importorskip("torch")
    root = tmp_path_factory.mktemp("retrieval-export")
    torch.manual_seed(19)
    class FixtureRetriever(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.features = torch.nn.Conv2d(3, 8, 3, padding=1)
        def forward(self, image):
            return self.features(image).square().mean(dim=(2, 3))
    model = FixtureRetriever().eval()
    torch.save(model.state_dict(), root / "fixture.pt")
    layout = json.loads((artifacts / "tiles" / "tile_layout.json").read_text())
    capture = json.loads((artifacts / "train" / "manifest.json").read_text())
    images = [artifacts / "tiles" / t["image_uri"] for t in layout["tiles"]]
    images.append(artifacts / "train" / capture["frames"][0]["path"])
    export_retrieval_onnx(model, images, root / "export", model_id="synthetic-test-retriever@1",
        provenance={"model_family": "synthetic_test_fixture_not_megaloc", "source_revision": "fixture-1",
                    "checkpoint_sha256": sha256_file(root / "fixture.pt"), "code_license": "test fixture",
                    "weights_license": "test fixture"}, image_size=32)
    build_replay_retrieval_index(root / "export", artifacts / "reference.json",
                                artifacts / "tiles" / "tile_layout.json", root / "index")
    return root


def automatic(root, retrieval, output, **kwargs):
    return run(root, output, candidate_tile_ids=None, retrieval_export=retrieval / "export",
               retrieval_index=retrieval / "index", **kwargs)


def test_real_onnx_retrieval_selects_map_tile_and_replays_student(artifacts, retrieval_artifacts, tmp_path):
    r = ReplayCandidateRetriever(retrieval_artifacts / "export", retrieval_artifacts / "index",
                                artifacts / "reference.json", artifacts / "tiles" / "tile_layout.json", top_k=1)
    layout = json.loads((artifacts / "tiles" / "tile_layout.json").read_text())
    for tile in layout["tiles"]:
        assert r.search(cv2.imread(str(artifacts / "tiles" / tile["image_uri"])))[0]["tile_id"] == tile["tile_id"]
    a = automatic(artifacts, retrieval_artifacts, tmp_path / "a", retrieval_top_k=1)
    b = automatic(artifacts, retrieval_artifacts, tmp_path / "b", retrieval_top_k=1)
    assert a["counts"]["retrieval_calls"] == 1
    assert a["counts"]["candidate_calls"] == 1
    assert a["counts"]["model_windows"] == 1
    assert a["decision_sha256_excluding_latency"] == b["decision_sha256_excluding_latency"]
    assert a["provenance"]["candidate_selection"] == "learned_image_retrieval"
    trace = [json.loads(line) for line in (tmp_path / "a" / "trace.jsonl").read_text().splitlines()]
    assert all(row["position_authority"] is False and row["navigation_mode"] == "NO_GLOBAL_FIX" for row in trace)
    assert "retrieval" not in trace[0]
    selected = trace[-1]["retrieval"]["candidates"]
    assert len(selected) == 1
    assert trace[-1]["candidates"][0]["tile_id"] == selected[0]["tile_id"]
    assert trace[-1]["retrieval"]["query_frame_sha256"] == trace[-1]["frame_sha256"]


def test_automatic_replay_and_index_do_not_need_torch(artifacts, retrieval_artifacts, tmp_path, monkeypatch):
    import builtins
    original = builtins.__import__
    def without_torch(name, *args, **kwargs):
        if name == "torch" or name.startswith("torch."):
            raise ImportError("CPU retrieval must not import torch")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", without_torch)
    build_replay_retrieval_index(retrieval_artifacts / "export", artifacts / "reference.json",
                                artifacts / "tiles" / "tile_layout.json", tmp_path / "index")
    assert automatic(artifacts, retrieval_artifacts, tmp_path / "out")["counts"]["model_windows"] == 1


def test_automatic_retrieval_ignores_poisoned_gps(artifacts, retrieval_artifacts, tmp_path):
    capture = tmp_path / "capture"
    shutil.copytree(artifacts / "train", capture)
    manifest = json.loads((capture / "manifest.json").read_text())
    with (capture / manifest["telemetry_path"]).open("a") as stream:
        stream.write(json.dumps({"index": 999, "host_monotonic_ns": 1, "sample": {
            "message_type": "GPS_RAW_INT", "gps_lat": -80, "gps_lon": 170, "gps_fix_type": 6}}) + "\n")
    manifest["telemetry_count"] += 1
    (capture / "manifest.json").write_text(json.dumps(manifest))
    write_session_checksums(capture)
    a = automatic(artifacts, retrieval_artifacts, tmp_path / "a")
    b = automatic(artifacts, retrieval_artifacts, tmp_path / "b", session_dir=capture)
    assert a["decision_sha256_excluding_latency"] == b["decision_sha256_excluding_latency"]


def test_only_selected_tile_images_enter_student_cache(artifacts, retrieval_artifacts, tmp_path, monkeypatch):
    from vision_nav.neural import map_conditioned_replay as replay
    original, seen = replay.image_tensor, []
    def spy(path, *args, **kwargs):
        if str(path).startswith(str(artifacts / "tiles")):
            seen.append(str(path))
        return original(path, *args, **kwargs)
    monkeypatch.setattr(replay, "image_tensor", spy)
    automatic(artifacts, retrieval_artifacts, tmp_path / "out", retrieval_top_k=1)
    assert len(seen) == 1


def test_retrieval_provider_failure_is_recorded_without_manual_fallback(artifacts, retrieval_artifacts, tmp_path, monkeypatch):
    def failed(self, bgr):
        raise RuntimeError("retriever_provider_failed")
    monkeypatch.setattr(ReplayCandidateRetriever, "search", failed)
    report = automatic(artifacts, retrieval_artifacts, tmp_path / "out")
    assert report["counts"]["model_windows"] == 0
    assert report["counts"]["candidate_calls"] == 0
    row = json.loads((tmp_path / "out" / "trace.jsonl").read_text().splitlines()[-1])
    assert row["state"] == "REJECTED"
    assert row["retrieval"]["state"] == "rejected"
    assert row["reason"] == "retriever_provider_failed"


def test_gap_never_retrieves_on_incomplete_window(artifacts, retrieval_artifacts, tmp_path):
    result = automatic(artifacts, retrieval_artifacts, tmp_path / "out", max_frame_gap_ms=50)
    assert result["counts"]["retrieval_calls"] == result["counts"]["model_windows"] == 0


@pytest.mark.parametrize("field,value", [("tile_layout_sha256", "a" * 64), ("model_id", "different@1"),
                                        ("retrieval_export_sha256", "b" * 64), ("descriptor_dimension", 99)])
def test_index_binding_rejects_mismatches(artifacts, retrieval_artifacts, tmp_path, field, value):
    root = tmp_path / "retrieval"
    shutil.copytree(retrieval_artifacts, root)
    manifest_path = root / "index" / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest[field] = value
    manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="compatibility mismatch"):
        automatic(artifacts, root, tmp_path / "out")


@pytest.mark.parametrize("filename", ["retriever.onnx", "parity.npz"])
def test_export_artifact_tampering_is_rejected(retrieval_artifacts, tmp_path, filename):
    shutil.copytree(retrieval_artifacts / "export", tmp_path / "export")
    with (tmp_path / "export" / filename).open("ab") as stream:
        stream.write(b"tampered")
    with pytest.raises(ValueError, match="checksum mismatch"):
        RetrievalOnnxProbe(tmp_path / "export")


def test_changed_preprocessing_rejected(retrieval_artifacts, tmp_path):
    shutil.copytree(retrieval_artifacts / "export", tmp_path / "export")
    path = tmp_path / "export" / "manifest.json"
    manifest = json.loads(path.read_text())
    manifest["preprocessing"]["color_space"] = "bgr"
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="preprocessing differs"):
        RetrievalOnnxProbe(tmp_path / "export")


def test_current_provider_must_reproduce_saved_parity(retrieval_artifacts, tmp_path):
    shutil.copytree(retrieval_artifacts / "export", tmp_path / "export")
    path = tmp_path / "export" / "manifest.json"
    manifest = json.loads(path.read_text())
    with np.load(tmp_path / "export" / "parity.npz") as data:
        images, expected = data["images"], data["embeddings"].copy()
    expected[:, 0] += 1
    np.savez_compressed(tmp_path / "export" / "parity.npz", images=images, embeddings=expected)
    manifest["parity"]["sha256"] = sha256_file(tmp_path / "export" / "parity.npz")
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError, match="parity failed"):
        RetrievalOnnxProbe(tmp_path / "export")


@pytest.mark.parametrize("value", [np.zeros((1, 3), np.float32), np.full((1, 3), np.nan, np.float32),
                                 np.ones((2, 3), np.float32), np.ones((1, 3), np.float64)])
def test_invalid_embeddings_rejected(value):
    with pytest.raises(ValueError):
        normalized_embedding(value)


@pytest.mark.parametrize("kwargs", [{"candidate_tile_ids": ["tile_000000"]}, {"retrieval_index": None},
                                   {"retrieval_top_k": 0}, {"retrieval_top_k": 5}, {"retrieval_top_k": True}])
def test_automatic_selection_contract_rejects_invalid_options(artifacts, retrieval_artifacts, tmp_path, kwargs):
    args = {"candidate_tile_ids": None, "retrieval_export": retrieval_artifacts / "export",
            "retrieval_index": retrieval_artifacts / "index", **kwargs}
    with pytest.raises(ValueError):
        run(artifacts, tmp_path / "out", **args)
