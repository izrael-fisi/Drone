"""Single-stage learned retrieval export for offline, shadow-only experiments.

This is not a partial vehicle ModelRelease. The existing four-stage deployment
validator remains unchanged. Map compilation and camera queries use the same
explicit preprocessing, and a CPU loader reruns the saved parity examples.
"""

from __future__ import annotations

import argparse
import gc
import importlib.util
import json
from pathlib import Path
import subprocess
from typing import Any

import cv2
import numpy as np

from vision_nav.training_capture import sha256_file
from .onnx_runtime import _onnx_session_factory
from .shadow_inference import _preprocess_image
from .upstreams import load_upstream_lock
from .retrieval_preprocessing import (ANTIALIAS_ID, antialias_contract, antialias_image,
                                      official_reference_image, preprocessing_parity)


RETRIEVAL_EXPORT_SCHEMA = "vision_nav_retrieval_export_v1"
PREPROCESSING_ID = "opencv_linear_rgb_imagenet_nchw_v1"
ATOL, RTOL = 1e-4, 1e-3


def export_member(root: Path, name: str) -> Path:
    if not isinstance(name, str) or not name or Path(name).is_absolute():
        raise ValueError("Retrieval artifact path must be relative")
    path = (root / name).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f"Missing or escaped retrieval artifact: {name}")
    return path


def preprocess_contract(image_size: int, preprocessing_id: str = PREPROCESSING_ID) -> dict[str, Any]:
    if type(image_size) is not int or not 8 <= image_size <= 1024:
        raise ValueError("Retrieval image size must be an integer in [8,1024]")
    if preprocessing_id == ANTIALIAS_ID:
        return antialias_contract(image_size)
    if preprocessing_id != PREPROCESSING_ID:
        raise ValueError("Unknown retrieval preprocessing ID")
    return {"id": PREPROCESSING_ID, "image_size": [image_size, image_size],
            "color_space": "rgb", "layout": "nchw", "value_range": "zero_one", "dtype": "float32",
            "mean": [0.485, 0.456, 0.406], "std": [0.229, 0.224, 0.225],
            "source_sha256": sha256_file(Path(__file__).with_name("shadow_inference.py"))}


def retrieval_image(bgr: np.ndarray, preprocessing: dict[str, Any]) -> np.ndarray:
    if not isinstance(bgr, np.ndarray) or bgr.dtype != np.uint8 or bgr.ndim != 3 or bgr.shape[2] != 3 or not bgr.size:
        raise ValueError("Retrieval requires a nonempty uint8 BGR image")
    if preprocessing["id"] == ANTIALIAS_ID:
        return antialias_image(bgr, preprocessing["image_size"][0])
    return _preprocess_image(bgr, {"preprocess": preprocessing})


def normalized_embedding(value: np.ndarray, dimension: int | None = None) -> np.ndarray:
    value = np.asarray(value)
    if (value.dtype != np.float32 or value.ndim != 2 or value.shape[0] != 1
            or not 1 <= value.shape[1] <= 65536 or not np.isfinite(value).all()
            or (dimension is not None and value.shape[1] != dimension)):
        raise ValueError("Retrieval output must be finite float32 [1, descriptor_dimension]")
    norm = float(np.linalg.norm(value.astype(np.float64)))
    if not np.isfinite(norm) or norm <= 1e-12:
        raise ValueError("Retrieval embedding has zero or invalid norm")
    return np.ascontiguousarray((value / norm).reshape(-1), dtype=np.float32)


def _provenance_check(provenance: dict[str, Any]) -> None:
    for key in ("model_family", "source_revision", "checkpoint_sha256", "code_license", "weights_license"):
        if not isinstance(provenance.get(key), str) or not provenance[key].strip():
            raise ValueError(f"Retrieval model provenance missing {key}")
    digest = provenance["checkpoint_sha256"]
    if len(digest) != 64 or set(digest) - set("0123456789abcdef") or digest == "0" * 64:
        raise ValueError("Invalid retrieval checkpoint digest")


def checkpoint_source_record(path: Path, weights: Path, weights_license: str) -> dict[str, Any]:
    """Verify downloaded checkpoint/card/license against an audited source record."""
    record = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(record, dict):
        raise ValueError("Checkpoint source record must be an object")
    revision = record.get("revision", "")
    if (record.get("schema_version") != "vision_nav_checkpoint_source_v1"
            or record.get("model_family") != "MegaLoc"
            or not isinstance(revision, str) or len(revision) != 40 or set(revision) - set("0123456789abcdef")
            or not isinstance(record.get("repository"), str) or not record["repository"].startswith("https://")
            or record.get("filename") != weights.name or record.get("sha256") != sha256_file(weights)
            or record.get("size_bytes") != weights.stat().st_size
            or record.get("weights_license") != weights_license or record.get("field_use_approved") is not False):
        raise ValueError("Checkpoint source record does not match local research weights")
    for key in ("license_file", "model_card"):
        member = record.get(key, {})
        local = export_member(weights.parent, member.get("path"))
        if sha256_file(local) != member.get("sha256"):
            raise ValueError(f"Checkpoint {key} checksum mismatch")
    return {**record, "record_sha256": sha256_file(path)}


def export_retrieval_onnx(
    model: Any, example_images: list[str | Path], output_dir: str | Path,
    *, model_id: str, provenance: dict[str, Any], image_size: int = 322,
    preprocessing_id: str = PREPROCESSING_ID,
) -> dict[str, Any]:
    """Export a loaded learned retriever and measure numerical/ranking parity.

    Callers own the model architecture and audited checkpoint loading. Examples
    must include real query/map imagery for a meaningful release evaluation.
    Synthetic fixtures verify plumbing only and retain that model provenance.
    """
    try:
        import torch
        import onnx
    except ImportError as exc:
        raise RuntimeError("Retrieval export requires PyTorch, ONNX and ONNX Runtime in the research environment") from exc

    if not isinstance(model_id, str) or "@" not in model_id:
        raise ValueError("Use a versioned retrieval model ID")
    _provenance_check(provenance)
    if not 2 <= len(example_images) <= 32:
        raise ValueError("Provide 2-32 retrieval parity images")
    preprocessing = preprocess_contract(image_size, preprocessing_id)
    root = Path(output_dir).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError("Retrieval export output must be empty")
    paths = [Path(p).expanduser().resolve() for p in example_images]
    if any(p.is_relative_to(root) for p in paths):
        raise ValueError("Parity images must be outside export output")
    raw_images = [cv2.imread(str(p)) for p in paths]
    inputs = np.concatenate([retrieval_image(image, preprocessing) for image in raw_images])
    reference_inputs = inputs
    preprocessing_check = None
    if preprocessing_id == ANTIALIAS_ID:
        reference_inputs = np.concatenate([official_reference_image(image, image_size) for image in raw_images])
        preprocessing_check = preprocessing_parity(reference_inputs, inputs)
        if not preprocessing_check["passed"]:
            raise ValueError("Retrieval preprocessing differs from official Torchvision inputs")
    model = model.cpu().eval()
    with torch.inference_mode():
        expected = [np.asarray(model(torch.from_numpy(x[None])).detach().cpu().numpy()) for x in reference_inputs]
    dimension = normalized_embedding(expected[0]).size
    for value in expected:
        normalized_embedding(value, dimension)
    root.mkdir(parents=True, exist_ok=True)
    path = root / "retriever.onnx"
    torch.onnx.export(model, (torch.from_numpy(inputs[:1]),), path,
                      input_names=["image"], output_names=["embedding"], opset_version=17,
                      dynamo=False, external_data=False)
    graph = onnx.load(str(path), load_external_data=False)
    if any(t.data_location == onnx.TensorProto.EXTERNAL for t in graph.graph.initializer):
        raise ValueError("Retrieval export must be a self-contained ONNX file")
    onnx.checker.check_model(graph)
    del graph
    gc.collect()
    session = _onnx_session_factory(path, ["CPUExecutionProvider"])
    expected = np.concatenate(expected)
    actual = np.concatenate([session.run(["embedding"], {"image": x[None]})[0] for x in inputs])
    parity = _parity(expected, actual)
    if not parity["passed"]:
        raise ValueError("Retrieval ONNX numerical or ranking parity failed; export is not usable")
    np.savez_compressed(root / "parity.npz", images=inputs, embeddings=expected)
    if preprocessing_check is not None:
        np.savez_compressed(root / "preprocessing_parity.npz", reference_inputs=reference_inputs,
                            **{f"raw_{i}": image for i, image in enumerate(raw_images)})
    manifest = {"schema_version": RETRIEVAL_EXPORT_SCHEMA, "model_id": model_id,
        "publication_mode": "research_only", "px4_external_vision": "disabled", "field_ready": False,
        "provenance": provenance, "preprocessing": preprocessing, "descriptor_dimension": dimension,
        "onnx": {"path": path.name, "sha256": sha256_file(path)},
        "parity": {**parity, "path": "parity.npz", "sha256": sha256_file(root / "parity.npz"),
                   "examples": [{"name": p.name, "sha256": sha256_file(p)} for p in paths]},
        "limitations": ["not_a_full_vehicle_model_release", "not_a_navigation_accuracy_benchmark"]}
    if preprocessing_check is None:
        manifest["limitations"].append("opencv_resize_differs_from_upstream_torchvision_antialiased_evaluation")
    else:
        import PIL
        import torchvision
        import onnxruntime
        manifest["preprocessing_parity"] = {**preprocessing_check, "path": "preprocessing_parity.npz",
            "sha256": sha256_file(root / "preprocessing_parity.npz"),
            "reference": "torchvision_tensor_normalize_then_resize_bilinear_antialias",
            "pillow_version": PIL.__version__, "torch_version": torch.__version__,
            "torchvision_version": torchvision.__version__, "onnxruntime_version": onnxruntime.__version__}
        manifest["parity"]["scope"] = "official_preprocessed_pytorch_to_runtime_preprocessed_onnx"
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return manifest


def _parity(expected: np.ndarray, actual: np.ndarray) -> dict[str, Any]:
    if expected.shape != actual.shape or expected.ndim != 2 or not 2 <= len(expected) <= 32:
        raise ValueError("Invalid retrieval parity tensor dimensions")
    ref = np.stack([normalized_embedding(x[None]) for x in expected])
    got = np.stack([normalized_embedding(x[None], ref.shape[1]) for x in actual])
    reference_scores, actual_scores = ref @ ref.T, got @ got.T
    # Self matches are excluded. Near-ties are explicitly unordered, not evidence
    # that an exporter preserved a meaningful ranking margin.
    order_preserved = True
    for query in range(len(ref)):
        for a in range(len(ref)):
            for b in range(a + 1, len(ref)):
                if query in (a, b):
                    continue
                margin = reference_scores[query, a] - reference_scores[query, b]
                if abs(margin) > 1e-3 and margin * (actual_scores[query, a] - actual_scores[query, b]) <= 0:
                    order_preserved = False
    close = bool(np.allclose(expected, actual, atol=ATOL, rtol=RTOL))
    return {"passed": close and order_preserved, "atol": ATOL, "rtol": RTOL,
            "ranking_margin": 1e-3, "ranking_preserved": order_preserved,
            "max_absolute_error": float(np.max(np.abs(expected.astype(np.float64) - actual))),
            "example_count": len(expected)}


class RetrievalOnnxProbe:
    """CPU image-to-embedding inference; no PyTorch dependency at replay time."""

    def __init__(self, export_dir: str | Path):
        self.root = Path(export_dir).expanduser().resolve()
        self.manifest = json.loads((self.root / "manifest.json").read_text(encoding="utf-8"))
        manifest = self.manifest
        if (not isinstance(manifest, dict) or manifest.get("schema_version") != RETRIEVAL_EXPORT_SCHEMA
                or manifest.get("publication_mode") != "research_only" or manifest.get("field_ready") is not False
                or manifest.get("px4_external_vision") != "disabled"):
            raise ValueError("Invalid shadow retrieval export")
        if not isinstance(manifest.get("model_id"), str) or "@" not in manifest["model_id"]:
            raise ValueError("Invalid retrieval model ID")
        _provenance_check(manifest.get("provenance", {}))
        self.dimension = manifest.get("descriptor_dimension")
        if type(self.dimension) is not int or not 1 <= self.dimension <= 65536:
            raise ValueError("Invalid retrieval descriptor dimension")
        self.preprocessing = manifest.get("preprocessing", {})
        size = self.preprocessing.get("image_size", [])
        if (not isinstance(size, list) or len(size) != 2
                or self.preprocessing != preprocess_contract(size[0], self.preprocessing.get("id"))):
            raise ValueError("Retrieval preprocessing differs from this runtime")
        for key in ("onnx", "parity"):
            record = manifest.get(key, {})
            path = export_member(self.root, record.get("path"))
            if sha256_file(path) != record.get("sha256"):
                raise ValueError(f"Retrieval {key} checksum mismatch")
        if manifest["parity"].get("passed") is not True:
            raise ValueError("Retrieval export did not pass parity")
        if self.preprocessing["id"] == ANTIALIAS_ID:
            record = manifest.get("preprocessing_parity", {})
            sample_path = export_member(self.root, record.get("path"))
            if sha256_file(sample_path) != record.get("sha256") or record.get("passed") is not True:
                raise ValueError("Retrieval preprocessing parity checksum/status mismatch")
            with np.load(sample_path, allow_pickle=False) as saved:
                reference = saved["reference_inputs"]
                if (reference.ndim != 4 or not 2 <= len(reference) <= 32
                        or reference.shape[1:] != (3, *size)
                        or set(saved.files) != {"reference_inputs", *(f"raw_{i}" for i in range(len(reference)))}):
                    raise ValueError("Retrieval preprocessing parity shapes mismatch")
                processed = np.concatenate([retrieval_image(saved[f"raw_{i}"], self.preprocessing)
                                            for i in range(len(reference))])
            if not preprocessing_parity(reference, processed)["passed"]:
                raise ValueError("Retrieval preprocessing parity failed on current host")
        self.session = _onnx_session_factory(export_member(self.root, manifest["onnx"]["path"]), ["CPUExecutionProvider"])
        input_meta, output_meta = self.session.get_inputs(), self.session.get_outputs()
        if (len(input_meta) != 1 or input_meta[0].name != "image" or input_meta[0].shape != [1, 3, *size]
                or input_meta[0].type != "tensor(float)" or len(output_meta) != 1
                or output_meta[0].name != "embedding" or output_meta[0].shape != [1, self.dimension]
                or output_meta[0].type != "tensor(float)"):
            raise ValueError("Retrieval ONNX interface mismatch")
        with np.load(export_member(self.root, manifest["parity"]["path"]), allow_pickle=False) as reference:
            images, embeddings = reference["images"], reference["embeddings"]
        if (images.ndim != 4 or not 2 <= len(images) <= 32 or images.shape[1:] != (3, *size)
                or images.dtype != np.float32 or not np.isfinite(images).all()
                or embeddings.shape != (len(images), self.dimension)):
            raise ValueError("Retrieval parity input shape/values mismatch")
        try:
            actual = np.concatenate([self.session.run(["embedding"], {"image": x[None]})[0] for x in images])
        except Exception as exc:
            raise RuntimeError(f"Retrieval parity inference failed: {exc}") from exc
        if not _parity(embeddings, actual)["passed"]:
            raise ValueError("Retrieval parity failed on current CPU provider")

    def embed(self, bgr: np.ndarray) -> np.ndarray:
        inputs = retrieval_image(bgr, self.preprocessing)
        try:
            value = self.session.run(["embedding"], {"image": inputs})[0]
        except Exception as exc:
            raise RuntimeError(f"Retrieval inference failed: {exc}") from exc
        return normalized_embedding(value, self.dimension)


def export_megaloc(checkout: Path, weights: Path, output_dir: Path, *, upstream_lock: Path,
                   example_images: list[Path], model_id: str, weights_license: str,
                   checkpoint_record: Path | None = None) -> dict[str, Any]:
    """Use only an explicitly supplied, clean pinned source and local safetensors."""
    try:
        from safetensors.torch import load_file
    except ImportError as exc:
        raise RuntimeError("MegaLoc export requires Safetensors, PyTorch and compatible Torchvision") from exc

    checkout, weights = checkout.expanduser().resolve(), weights.expanduser().resolve()
    project = next((p for p in load_upstream_lock(upstream_lock)["projects"] if p["name"] == "megaloc"), None)
    if not project:
        raise ValueError("MegaLoc is absent from upstream lock")
    revision = subprocess.run(["git", "-C", str(checkout), "rev-parse", "HEAD"], check=True,
                              capture_output=True, text=True).stdout.strip()
    dirty = subprocess.run(["git", "-C", str(checkout), "status", "--porcelain", "--untracked-files=normal"],
                           check=True, capture_output=True, text=True).stdout.strip()
    if revision != project["commit"] or dirty:
        raise ValueError("MegaLoc checkout must be clean and match the pinned revision")
    source = export_member(checkout, "megaloc_model.py")
    license_path = export_member(checkout, "LICENSE")
    if weights.suffix != ".safetensors" or not weights.is_file():
        raise ValueError("Provide local MegaLoc model.safetensors outside the source checkout")
    checkpoint_source = (checkpoint_source_record(checkpoint_record.expanduser().resolve(), weights, weights_license)
                         if checkpoint_record else None)
    spec = importlib.util.spec_from_file_location("vision_nav_pinned_megaloc", source)
    module = importlib.util.module_from_spec(spec)
    # Do not create __pycache__ in the source checkout: it must stay auditable/clean.
    exec(compile(source.read_bytes(), str(source), "exec"), module.__dict__)
    model = module.MegaLoc()
    model.load_state_dict(load_file(str(weights), device="cpu"), strict=True)
    return export_retrieval_onnx(model, example_images, output_dir, model_id=model_id,
        preprocessing_id=ANTIALIAS_ID,
        provenance={"model_family": "MegaLoc", "source_revision": revision, "repository": project["repository"],
                    "source_sha256": sha256_file(source), "checkpoint_sha256": sha256_file(weights),
                    "code_license": project["license"], "code_license_sha256": sha256_file(license_path),
                    "weights_license": weights_license, "upstream_lock_sha256": sha256_file(upstream_lock),
                    "checkpoint_source": checkpoint_source})


def main() -> None:
    parser = argparse.ArgumentParser(description="Export pinned local MegaLoc weights for CPU shadow retrieval")
    for name in ("checkout", "weights", "output-dir"):
        parser.add_argument("--" + name, type=Path, required=True)
    parser.add_argument("--upstream-lock", type=Path, default=Path("research/upstreams.lock.json"))
    parser.add_argument("--example-image", type=Path, action="append", required=True)
    parser.add_argument("--model-id", required=True)
    parser.add_argument("--weights-license", required=True, help="Recorded checkpoint terms; not a field-use approval")
    parser.add_argument("--checkpoint-record", type=Path, help="Audited checkpoint revision, digest and license/card files")
    args = parser.parse_args()
    try:
        report = export_megaloc(args.checkout, args.weights, args.output_dir, upstream_lock=args.upstream_lock,
                               example_images=args.example_image, model_id=args.model_id, weights_license=args.weights_license,
                               checkpoint_record=args.checkpoint_record)
    except (ValueError, RuntimeError, OSError, subprocess.SubprocessError) as exc:
        parser.exit(2, f"Retrieval export rejected: {exc}\n")
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
