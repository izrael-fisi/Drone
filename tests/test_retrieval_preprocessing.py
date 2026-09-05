import builtins
import json
import shutil

import cv2
import numpy as np
import pytest

from vision_nav.training_capture import sha256_file
from vision_nav.neural.retrieval_export import (RetrievalOnnxProbe, export_retrieval_onnx,
                                               preprocess_contract, retrieval_image, checkpoint_source_record)
from vision_nav.neural.retrieval_preprocessing import ANTIALIAS_ID, official_reference_image, preprocessing_parity


@pytest.mark.parametrize("shape", [(384, 640), (1080, 1920), (47, 83), (322, 322), (1, 41), (321, 641)])
def test_antialiased_float_resize_matches_upstream_tensor_preprocessing(shape):
    pytest.importorskip("torchvision")
    image = np.random.default_rng(0).integers(0, 256, (*shape, 3), dtype=np.uint8)
    actual = retrieval_image(image, preprocess_contract(322, ANTIALIAS_ID))
    expected = official_reference_image(image, 322)
    assert actual.shape == (1, 3, 322, 322)
    assert actual.dtype == np.float32
    assert preprocessing_parity(expected, actual)["passed"]


def test_high_frequency_downsample_needs_antialias():
    pytest.importorskip("torchvision")
    pixels = ((np.indices((981, 1293)).sum(axis=0) % 2) * 255).astype(np.uint8)
    image = np.repeat(pixels[:, :, None], 3, axis=2)
    expected = official_reference_image(image, 322)
    assert preprocessing_parity(expected, retrieval_image(image, preprocess_contract(322, ANTIALIAS_ID)))["passed"]
    assert not preprocessing_parity(expected, retrieval_image(image, preprocess_contract(322)))["passed"]


@pytest.fixture(scope="module")
def antialiased_export(tmp_path_factory):
    torch = pytest.importorskip("torch")
    pytest.importorskip("torchvision")
    root = tmp_path_factory.mktemp("antialias")
    class Model(torch.nn.Module):
        def forward(self, image):
            return (image + 3).mean(dim=(2, 3))
    images = []
    for i, size in enumerate(((91, 155), (67, 173), (100, 80))):
        image = np.random.default_rng(i).integers(0, 256, (*size, 3), dtype=np.uint8)
        path = root / f"image-{i}.png"
        assert cv2.imwrite(str(path), image)
        images.append(path)
    export_retrieval_onnx(Model(), images, root / "export", model_id="antialias-fixture@1", image_size=32,
        preprocessing_id=ANTIALIAS_ID,
        provenance={"model_family": "test_not_megaloc", "source_revision": "test",
                    "checkpoint_sha256": "a" * 64, "code_license": "test", "weights_license": "test"})
    return root


def test_export_verifies_full_official_input_to_onnx_path(antialiased_export):
    probe = RetrievalOnnxProbe(antialiased_export / "export")
    assert probe.manifest["preprocessing_parity"]["passed"] is True
    assert probe.manifest["parity"]["scope"] == "official_preprocessed_pytorch_to_runtime_preprocessed_onnx"
    assert "opencv_resize_differs_from_upstream_torchvision_antialiased_evaluation" not in probe.manifest["limitations"]
    assert probe.embed(cv2.imread(str(antialiased_export / "image-0.png"))).shape == (3,)
    assert probe.manifest["field_ready"] is False


def test_antialiased_startup_and_inference_do_not_import_torch(antialiased_export, monkeypatch):
    original = builtins.__import__
    def without_torch(name, *args, **kwargs):
        if name.split(".")[0] in ("torch", "torchvision"):
            raise AssertionError("Runtime must not use Torch")
        return original(name, *args, **kwargs)
    monkeypatch.setattr(builtins, "__import__", without_torch)
    probe = RetrievalOnnxProbe(antialiased_export / "export")
    assert np.isfinite(probe.embed(cv2.imread(str(antialiased_export / "image-1.png")))).all()


@pytest.mark.parametrize("change", ["bytes", "reference", "raw", "manifest", "shape"])
def test_preprocessing_evidence_is_reverified(antialiased_export, tmp_path, change):
    root = tmp_path / "export"
    shutil.copytree(antialiased_export / "export", root)
    manifest = json.loads((root / "manifest.json").read_text())
    path = root / "preprocessing_parity.npz"
    if change == "manifest":
        del manifest["preprocessing_parity"]
    elif change == "bytes":
        with path.open("ab") as stream:
            stream.write(b"corrupt")
    else:
        with np.load(path, allow_pickle=False) as saved:
            arrays = {k: saved[k] for k in saved.files}
        if change == "reference":
            arrays["reference_inputs"] += 0.1
        elif change == "raw":
            arrays["raw_0"][:] = 0
        else:
            arrays["reference_inputs"] = arrays["reference_inputs"][:, :, :-1]
        np.savez_compressed(path, **arrays)
        manifest["preprocessing_parity"]["sha256"] = sha256_file(path)
    (root / "manifest.json").write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        RetrievalOnnxProbe(root)


def test_unknown_preprocessing_is_not_silently_changed():
    with pytest.raises(ValueError, match="Unknown"):
        preprocess_contract(322, "auto_fallback")


@pytest.mark.parametrize("mutation", [None, "checkpoint", "license", "card", "revision", "approval", "terms"])
def test_checkpoint_download_provenance_is_bound(tmp_path, mutation):
    weights = tmp_path / "model.safetensors"
    weights.write_bytes(b"test-only checkpoint identity fixture")
    license_path, card = tmp_path / "LICENSE", tmp_path / "README.md"
    license_path.write_text("test license")
    card.write_text("test model card")
    record = {"schema_version": "vision_nav_checkpoint_source_v1", "model_family": "MegaLoc",
              "repository": "https://example.invalid/test", "revision": "a" * 40,
              "filename": weights.name, "sha256": sha256_file(weights), "size_bytes": weights.stat().st_size,
              "weights_license": "test", "field_use_approved": False,
              "license_file": {"path": license_path.name, "sha256": sha256_file(license_path)},
              "model_card": {"path": card.name, "sha256": sha256_file(card)}}
    if mutation == "checkpoint":
        weights.write_bytes(b"changed weights")
    elif mutation == "license":
        license_path.write_text("changed terms")
    elif mutation == "card":
        card.write_text("changed model card")
    elif mutation == "revision":
        record["revision"] = "main"
    elif mutation == "approval":
        record["field_use_approved"] = True
    elif mutation == "terms":
        record["weights_license"] = "different license"
    path = tmp_path / "source.json"
    path.write_text(json.dumps(record))
    if mutation:
        with pytest.raises(ValueError):
            checkpoint_source_record(path, weights, "test")
    else:
        assert checkpoint_source_record(path, weights, "test")["record_sha256"] == sha256_file(path)
