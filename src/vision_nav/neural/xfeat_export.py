"""Export the pinned XFeat backbone to a research-only dense ONNX artifact.

The upstream XFeat project owns sparse keypoint extraction and matching
post-processing.  This module intentionally exports its learned dense heads
only.  It records the raw descriptor, keypoint-logit, and reliability maps so
the project can prove a numerical ONNX export before implementing a separately
validated, release-bound sparse post-processing path.

It never creates a ModelRelease, transfers an artifact to a Pi, or enables
PX4 external vision publication.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib
import json
from pathlib import Path
import subprocess
import sys
from typing import Any

import numpy as np

from vision_nav.training_capture import sha256_file

from .upstreams import canonical_digest, load_upstream_lock


XFEAT_DENSE_EXPORT_SCHEMA = "vision_nav_xfeat_dense_export_v1"
XFEAT_DENSE_PARITY_SCHEMA = "vision_nav_xfeat_dense_parity_v1"
XFEAT_SPARSE_EXPORT_SCHEMA = "vision_nav_xfeat_sparse_export_v1"
XFEAT_SPARSE_PARITY_SCHEMA = "vision_nav_xfeat_sparse_parity_v1"
XFEAT_PROJECT_NAME = "xfeat"
INPUT_NAME = "image"
OUTPUT_NAMES = ("dense_descriptors", "keypoint_logits", "reliability")
SPARSE_OUTPUT_NAMES = ("keypoints", "descriptors", "scores")


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


def _read_json(path: Path, label: str) -> dict[str, Any]:
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"Could not read {label}: {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"{label} must be a JSON object: {path}")
    return raw


def _project_from_lock(lock: dict[str, Any]) -> dict[str, Any]:
    for project in lock["projects"]:
        if project["name"] == XFEAT_PROJECT_NAME:
            return project
    raise ValueError("Research upstream lock does not include XFeat.")


def _git_revision(root: Path) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=False,
            capture_output=True,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        raise ValueError(f"Could not inspect XFeat checkout: {exc}") from exc
    revision = result.stdout.strip()
    if result.returncode != 0 or len(revision) != 40:
        raise ValueError(f"XFeat checkout is not a readable Git worktree: {root}")
    return revision


def inspect_pinned_xfeat_checkout(
    checkout: str | Path,
    *,
    upstream_lock: str | Path,
) -> dict[str, Any]:
    """Verify that a checkout is exactly the XFeat revision in the lock file."""

    root = Path(checkout).expanduser().resolve()
    lock_path = Path(upstream_lock).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"XFeat checkout does not exist: {root}")
    lock = load_upstream_lock(lock_path)
    project = _project_from_lock(lock)
    revision = _git_revision(root)
    if revision != project["commit"]:
        raise ValueError(
            "XFeat checkout revision does not match research/upstreams.lock.json: "
            f"expected {project['commit']}, got {revision}."
        )
    required = (root / "modules" / "model.py", root / "LICENSE")
    missing = [str(path.relative_to(root)) for path in required if not path.is_file()]
    if missing:
        raise ValueError(f"Pinned XFeat checkout is missing required files: {', '.join(missing)}")
    license_text = (root / "LICENSE").read_text(encoding="utf-8", errors="replace")
    if "Apache License" not in license_text or "Version 2.0" not in license_text:
        raise ValueError("Pinned XFeat checkout does not contain the expected Apache-2.0 license text.")
    if project.get("license_status") != "verified" or project.get("license") != "Apache-2.0":
        raise ValueError("Research upstream lock must record XFeat as a verified Apache-2.0 dependency.")
    return {
        "project": XFEAT_PROJECT_NAME,
        "repository": project["repository"],
        "commit": revision,
        "license": project["license"],
        "license_status": project["license_status"],
        "checkout": str(root),
        "upstream_lock": str(lock_path),
        "upstream_lock_sha256": canonical_digest(lock),
    }


def _load_xfeat_model(checkout: Path, weights: Path, *, device: str) -> tuple[Any, Any]:
    try:
        import torch
    except ImportError as exc:
        raise RuntimeError("XFeat ONNX export requires drone-vision-nav[research] (PyTorch).") from exc
    if not weights.is_file():
        raise ValueError(f"XFeat weights do not exist: {weights}")
    module_root = str(checkout)
    existing = sys.modules.get("modules")
    if existing is not None:
        existing_path = Path(getattr(existing, "__file__", "") or "").resolve()
        if module_root not in str(existing_path):
            raise RuntimeError(
                "Another Python package named 'modules' is already loaded. Run the XFeat export in a fresh Python process."
            )
    sys.path.insert(0, module_root)
    try:
        model_module = importlib.import_module("modules.model")
        model = model_module.XFeatModel().to(device)
        payload = torch.load(weights, map_location=device, weights_only=True)
    finally:
        try:
            sys.path.remove(module_root)
        except ValueError:
            pass
    if isinstance(payload, dict) and isinstance(payload.get("state_dict"), dict):
        payload = payload["state_dict"]
    if not isinstance(payload, dict) or not payload:
        raise ValueError("XFeat weights must contain a non-empty state dictionary or state_dict wrapper.")
    try:
        model.load_state_dict(payload, strict=True)
    except RuntimeError as exc:
        raise ValueError(f"XFeat weights do not strictly match the pinned upstream model: {exc}") from exc
    model.eval()
    return model, torch


def _validate_image_size(width: int, height: int) -> None:
    if width < 32 or height < 32 or width % 32 or height % 32:
        raise ValueError("XFeat ONNX export image width and height must be at least 32 and divisible by 32.")


def _resolve_export_member(root: Path, raw_path: Any, label: str) -> Path:
    if not isinstance(raw_path, str) or not raw_path:
        raise ValueError(f"XFeat export {label} path is missing.")
    candidate = (root / raw_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"XFeat export {label} path escapes its export directory.") from exc
    return candidate


def _load_npz(path: Path, label: str) -> dict[str, np.ndarray]:
    if not path.is_file():
        raise ValueError(f"Missing XFeat export {label}: {path}")
    try:
        with np.load(path, allow_pickle=False) as archive:
            return {name: np.asarray(archive[name]) for name in archive.files}
    except (OSError, ValueError) as exc:
        raise ValueError(f"Could not load XFeat export {label}: {path}: {exc}") from exc


def _interface_from_arrays(inputs: dict[str, np.ndarray], outputs: dict[str, np.ndarray]) -> dict[str, Any]:
    if set(inputs) != {INPUT_NAME} or set(outputs) != set(OUTPUT_NAMES):
        raise ValueError("XFeat dense export parity tensors do not match the expected raw model interface.")
    for name, array in {**inputs, **outputs}.items():
        if array.dtype.kind != "f" or not np.isfinite(array).all():
            raise ValueError(f"XFeat dense export tensor must be finite floating point: {name}")
    image = inputs[INPUT_NAME]
    descriptors = outputs["dense_descriptors"]
    logits = outputs["keypoint_logits"]
    reliability = outputs["reliability"]
    if image.ndim != 4 or image.shape[0] != 1 or image.shape[1] != 3:
        raise ValueError("XFeat dense export image parity tensor must be [1, 3, height, width].")
    if image.shape[2] % 32 or image.shape[3] % 32:
        raise ValueError("XFeat dense export image parity tensor must use dimensions divisible by 32.")
    expected_spatial = (image.shape[2] // 8, image.shape[3] // 8)
    if descriptors.shape != (1, 64, *expected_spatial):
        raise ValueError("XFeat dense descriptor output shape is incompatible with the pinned XFeat stride-8 backbone.")
    if logits.shape != (1, 65, *expected_spatial) or reliability.shape != (1, 1, *expected_spatial):
        raise ValueError("XFeat dense keypoint/reliability output shape is incompatible with the pinned XFeat backbone.")
    return {
        "inputs": {INPUT_NAME: {"name": INPUT_NAME, "rank": 4, "shape": list(image.shape)}},
        "outputs": {
            name: {"name": name, "rank": array.ndim, "shape": list(array.shape)}
            for name, array in outputs.items()
        },
        "descriptor_dimension": 64,
        "feature_stride_px": 8,
    }


def _sparse_interface_from_arrays(inputs: dict[str, np.ndarray], outputs: dict[str, np.ndarray]) -> dict[str, Any]:
    if set(inputs) != {INPUT_NAME} or set(outputs) != set(SPARSE_OUTPUT_NAMES):
        raise ValueError("XFeat sparse export parity tensors do not match the expected local-feature interface.")
    for name, array in {**inputs, **outputs}.items():
        if array.dtype.kind != "f" or not np.isfinite(array).all():
            raise ValueError(f"XFeat sparse export tensor must be finite floating point: {name}")
    image = inputs[INPUT_NAME]
    keypoints = outputs["keypoints"]
    descriptors = outputs["descriptors"]
    scores = outputs["scores"]
    if image.ndim != 4 or image.shape[:2] != (1, 3):
        raise ValueError("XFeat sparse export image parity tensor must be [1, 3, height, width].")
    if image.shape[2] % 32 or image.shape[3] % 32:
        raise ValueError("XFeat sparse export image parity tensor must use dimensions divisible by 32.")
    if keypoints.ndim != 3 or keypoints.shape[0] != 1 or keypoints.shape[2] != 2 or keypoints.shape[1] <= 0:
        raise ValueError("XFeat sparse export keypoints must have shape [1, feature_count, 2].")
    if descriptors.shape != (1, keypoints.shape[1], 64) or scores.shape != (1, keypoints.shape[1]):
        raise ValueError("XFeat sparse export descriptors/scores must align with fixed keypoints.")
    if np.any(keypoints[..., 0] < 0.0) or np.any(keypoints[..., 0] >= image.shape[3]):
        raise ValueError("XFeat sparse export keypoint x coordinates are outside the fixed image extent.")
    if np.any(keypoints[..., 1] < 0.0) or np.any(keypoints[..., 1] >= image.shape[2]):
        raise ValueError("XFeat sparse export keypoint y coordinates are outside the fixed image extent.")
    descriptor_norms = np.linalg.norm(descriptors, axis=-1)
    valid = scores > 0.0
    if np.any(valid & ~np.isclose(descriptor_norms, 1.0, atol=1e-3)):
        raise ValueError("XFeat sparse export valid descriptors must be L2 normalized.")
    return {
        "inputs": {INPUT_NAME: {"name": INPUT_NAME, "rank": 4, "shape": list(image.shape)}},
        "outputs": {
            name: {"name": name, "rank": array.ndim, "shape": list(array.shape)}
            for name, array in outputs.items()
        },
        "descriptor_dimension": 64,
        "maximum_feature_count": int(keypoints.shape[1]),
        "invalid_feature_score": -1.0,
    }


def _fixed_sparse_xfeat_wrapper(
    backbone: Any,
    torch: Any,
    *,
    image_width: int,
    image_height: int,
    maximum_feature_count: int,
    detection_threshold: float,
) -> Any:
    """Create a fixed-cardinality ONNX wrapper for pinned XFeat inference.

    The upstream Python API returns a variable number of features. A ModelRelease
    needs static ONNX tensors, so this wrapper reproduces its heatmap/NMS,
    reliability scoring, descriptor interpolation and L2 normalization over a
    fixed image extent. Invalid slots deliberately retain score ``-1`` and must
    be discarded by the release-bound consumer.
    """

    if maximum_feature_count <= 0 or maximum_feature_count > image_width * image_height:
        raise ValueError("XFeat maximum_feature_count must be positive and no greater than image_width * image_height.")
    if not 0.0 < detection_threshold < 1.0:
        raise ValueError("XFeat detection_threshold must be between zero and one.")
    x_coords = torch.arange(image_width, dtype=torch.float32)
    y_coords = torch.arange(image_height, dtype=torch.float32)
    yy, xx = torch.meshgrid(y_coords, x_coords, indexing="ij")
    all_points = torch.stack((xx.reshape(-1), yy.reshape(-1)), dim=-1).unsqueeze(0)

    class FixedSparseXFeat(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.backbone = backbone
            self.width = image_width
            self.height = image_height
            self.maximum_feature_count = maximum_feature_count
            self.detection_threshold = detection_threshold
            self.register_buffer("all_points", all_points)

        def _grid(self, points: Any) -> Any:
            scale = points.new_tensor([self.width - 1, self.height - 1])
            return (2.0 * (points / scale) - 1.0).unsqueeze(-2)

        def _sample(self, values: Any, points: Any, *, mode: str) -> Any:
            grid = self._grid(points).to(values.dtype)
            sampled = torch.nn.functional.grid_sample(values, grid, mode=mode, align_corners=False)
            return sampled.permute(0, 2, 3, 1).squeeze(-2)

        def forward(self, image: Any) -> tuple[Any, Any, Any]:
            descriptors, keypoint_logits, reliability = self.backbone(image)
            descriptors = torch.nn.functional.normalize(descriptors, dim=1)
            batch = image.shape[0]
            coarse_height = keypoint_logits.shape[-2]
            coarse_width = keypoint_logits.shape[-1]
            heatmap = torch.nn.functional.softmax(keypoint_logits, dim=1)[:, :64]
            heatmap = heatmap.permute(0, 2, 3, 1).reshape(batch, coarse_height, coarse_width, 8, 8)
            heatmap = heatmap.permute(0, 1, 3, 2, 4).reshape(batch, 1, self.height, self.width)
            points = self.all_points.expand(batch, -1, -1)
            heat_scores = self._sample(heatmap, points, mode="nearest").squeeze(-1)
            reliability_scores = self._sample(reliability, points, mode="bilinear").squeeze(-1)
            maxima = torch.nn.functional.max_pool2d(heatmap, kernel_size=5, stride=1, padding=2)
            is_candidate = (heatmap == maxima) & (heatmap > self.detection_threshold)
            candidate_mask = is_candidate.reshape(batch, -1)
            # Upstream NMS pads variable-length batches with [0, 0] and then
            # marks that sentinel invalid. Preserve that exact convention in
            # the fixed-cardinality graph rather than allowing a corner slot
            # to diverge from the reference implementation.
            origin_mask = torch.all(points == 0.0, dim=-1)
            candidate_mask = candidate_mask & ~origin_mask
            sparse_scores = torch.where(candidate_mask, heat_scores * reliability_scores, heat_scores.new_full(heat_scores.shape, -1.0))
            scores, indices = torch.topk(sparse_scores, self.maximum_feature_count, dim=1, largest=True, sorted=True)
            selected_points = torch.gather(points, 1, indices.unsqueeze(-1).expand(-1, -1, 2))
            selected_descriptors = self._sample(descriptors, selected_points, mode="bicubic")
            selected_descriptors = torch.nn.functional.normalize(selected_descriptors, dim=-1)
            return selected_points, selected_descriptors, scores

    return FixedSparseXFeat()


def build_xfeat_dense_export_manifest(
    *,
    export_root: Path,
    upstream: dict[str, Any],
    weights: Path,
    onnx_path: Path,
    inputs_path: Path,
    outputs_path: Path,
    interface: dict[str, Any],
    opset_version: int,
) -> dict[str, Any]:
    """Build the immutable evidence record shared by export and unit tests."""

    return {
        "schema_version": XFEAT_DENSE_EXPORT_SCHEMA,
        "created_utc": _utc_now(),
        "status": "exported",
        "publication_mode": "research_only",
        "px4_external_vision": "disabled",
        "deployment_state": "dense_backbone_only_not_model_release_ready",
        "profile_id": "pi5_crossview_nav_v1",
        "upstream": upstream,
        "weights": {"path": str(weights), "sha256": sha256_file(weights)},
        "onnx": {"path": onnx_path.name, "sha256": sha256_file(onnx_path), "opset_version": opset_version},
        "interface": interface,
        "parity_reference": {
            "inputs_npz": str(inputs_path.relative_to(export_root)), "inputs_sha256": sha256_file(inputs_path),
            "outputs_npz": str(outputs_path.relative_to(export_root)), "outputs_sha256": sha256_file(outputs_path),
        },
        "next_action": (
            "Run standalone ONNX parity, then implement and validate the upstream-equivalent sparse post-processing "
            "before binding XFeat to a four-role shadow ModelRelease."
        ),
    }


def export_xfeat_dense_onnx(
    checkout: str | Path,
    weights_path: str | Path,
    output_dir: str | Path,
    *,
    upstream_lock: str | Path = "research/upstreams.lock.json",
    image_width: int = 640,
    image_height: int = 480,
    device: str = "cpu",
    opset_version: int = 17,
    seed: int = 20260831,
) -> dict[str, Any]:
    """Export raw XFeat backbone heads and record a PyTorch parity fixture."""

    _validate_image_size(image_width, image_height)
    if opset_version < 13:
        raise ValueError("XFeat ONNX export requires opset_version >= 13.")
    if device not in {"cpu", "cuda"}:
        raise ValueError("XFeat ONNX export device must be cpu or cuda.")
    source = Path(checkout).expanduser().resolve()
    weights = Path(weights_path).expanduser().resolve()
    root = Path(output_dir).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError(f"XFeat export directory must be empty: {root}")
    upstream = inspect_pinned_xfeat_checkout(source, upstream_lock=upstream_lock)
    model, torch = _load_xfeat_model(source, weights, device=device)
    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("XFeat CUDA export requested, but PyTorch CUDA is unavailable.")

    class DenseXFeat(torch.nn.Module):
        def __init__(self, backbone: Any):
            super().__init__()
            self.backbone = backbone

        def forward(self, image: Any) -> tuple[Any, Any, Any]:
            return self.backbone(image)

    generator = torch.Generator(device=device)
    generator.manual_seed(seed)
    image = torch.rand((1, 3, image_height, image_width), generator=generator, device=device, dtype=torch.float32)
    wrapper = DenseXFeat(model).to(device).eval()
    with torch.no_grad():
        outputs = wrapper(image)
    input_array = image.detach().cpu().numpy().astype(np.float32)
    output_arrays = {name: value.detach().cpu().numpy().astype(np.float32) for name, value in zip(OUTPUT_NAMES, outputs)}
    interface = _interface_from_arrays({INPUT_NAME: input_array}, output_arrays)

    root.mkdir(parents=True, exist_ok=True)
    onnx_path = root / "xfeat_dense.onnx"
    try:
        torch.onnx.export(
            wrapper,
            image,
            onnx_path,
            input_names=[INPUT_NAME],
            output_names=list(OUTPUT_NAMES),
            opset_version=opset_version,
            do_constant_folding=True,
            dynamo=False,
        )
    except Exception as exc:
        raise RuntimeError(f"XFeat ONNX export failed: {type(exc).__name__}: {exc}") from exc
    parity_dir = root / "parity"
    parity_dir.mkdir()
    inputs_path = parity_dir / "inputs.npz"
    outputs_path = parity_dir / "pytorch_outputs.npz"
    np.savez_compressed(inputs_path, **{INPUT_NAME: input_array})
    np.savez_compressed(outputs_path, **output_arrays)
    manifest = build_xfeat_dense_export_manifest(
        export_root=root,
        upstream=upstream,
        weights=weights,
        onnx_path=onnx_path,
        inputs_path=inputs_path,
        outputs_path=outputs_path,
        interface=interface,
        opset_version=opset_version,
    )
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"output_dir": str(root), "manifest_path": str(manifest_path), "manifest": manifest}


def validate_xfeat_dense_export(export_dir: str | Path) -> dict[str, Any]:
    """Validate XFeat export provenance, checksums, and tensor contracts without ONNX execution."""

    root = Path(export_dir).expanduser().resolve()
    manifest_path = root / "manifest.json"
    manifest = _read_json(manifest_path, "XFeat dense export manifest")
    if manifest.get("schema_version") != XFEAT_DENSE_EXPORT_SCHEMA or manifest.get("status") != "exported":
        raise ValueError("XFeat dense export manifest is unsupported or incomplete.")
    if manifest.get("publication_mode") != "research_only" or manifest.get("px4_external_vision") != "disabled":
        raise ValueError("XFeat dense export must remain research-only with PX4 publication disabled.")
    if manifest.get("deployment_state") != "dense_backbone_only_not_model_release_ready":
        raise ValueError("XFeat dense export must not claim ModelRelease readiness.")
    upstream = manifest.get("upstream") if isinstance(manifest.get("upstream"), dict) else {}
    required_upstream = ("project", "repository", "commit", "license", "license_status", "upstream_lock_sha256")
    if any(not isinstance(upstream.get(key), str) or not upstream[key] for key in required_upstream):
        raise ValueError("XFeat dense export upstream provenance is incomplete.")
    if upstream.get("project") != XFEAT_PROJECT_NAME or upstream.get("license") != "Apache-2.0" or upstream.get("license_status") != "verified":
        raise ValueError("XFeat dense export must retain pinned Apache-2.0 provenance.")
    weights = manifest.get("weights") if isinstance(manifest.get("weights"), dict) else {}
    if not isinstance(weights.get("sha256"), str) or len(weights["sha256"]) != 64:
        raise ValueError("XFeat dense export is missing the immutable source weight digest.")
    onnx = manifest.get("onnx") if isinstance(manifest.get("onnx"), dict) else {}
    onnx_path = _resolve_export_member(root, onnx.get("path"), "ONNX")
    if not onnx_path.is_file() or not isinstance(onnx.get("sha256"), str) or sha256_file(onnx_path) != onnx["sha256"]:
        raise ValueError("XFeat dense ONNX artifact checksum mismatch.")
    parity = manifest.get("parity_reference") if isinstance(manifest.get("parity_reference"), dict) else {}
    inputs_path = _resolve_export_member(root, parity.get("inputs_npz"), "parity input")
    outputs_path = _resolve_export_member(root, parity.get("outputs_npz"), "parity output")
    for path, key, label in ((inputs_path, "inputs_sha256", "inputs"), (outputs_path, "outputs_sha256", "outputs")):
        if not path.is_file() or not isinstance(parity.get(key), str) or sha256_file(path) != parity[key]:
            raise ValueError(f"XFeat dense export parity {label} checksum mismatch.")
    inputs = _load_npz(inputs_path, "parity inputs")
    outputs = _load_npz(outputs_path, "PyTorch outputs")
    interface = _interface_from_arrays(inputs, outputs)
    if manifest.get("interface") != interface:
        raise ValueError("XFeat dense export interface does not match its immutable parity tensors.")
    return {
        "schema_version": XFEAT_DENSE_EXPORT_SCHEMA,
        "status": "valid",
        "publication_mode": "research_only",
        "px4_external_vision": "disabled",
        "export_dir": str(root),
        "manifest_sha256": sha256_file(manifest_path),
        "profile_id": manifest.get("profile_id"),
        "upstream": upstream,
        "onnx_path": str(onnx_path),
        "interface": interface,
    }


def evaluate_xfeat_dense_export_parity(
    export_dir: str | Path,
    *,
    atol: float = 1e-4,
    rtol: float = 1e-3,
) -> dict[str, Any]:
    """Execute an exported dense XFeat graph against its recorded PyTorch tensors."""

    if atol < 0 or rtol < 0:
        raise ValueError("XFeat parity tolerances must be non-negative.")
    audit = validate_xfeat_dense_export(export_dir)
    root = Path(export_dir).expanduser().resolve()
    manifest = _read_json(root / "manifest.json", "XFeat dense export manifest")
    parity = manifest["parity_reference"]
    inputs = _load_npz(_resolve_export_member(root, parity["inputs_npz"], "parity input"), "parity inputs")
    expected = _load_npz(_resolve_export_member(root, parity["outputs_npz"], "parity output"), "PyTorch outputs")
    try:
        import onnxruntime as ort
    except ImportError as exc:
        raise RuntimeError("XFeat ONNX parity requires onnxruntime.") from exc
    session = ort.InferenceSession(audit["onnx_path"], providers=["CPUExecutionProvider"])
    actual_values = session.run(list(OUTPUT_NAMES), {INPUT_NAME: inputs[INPUT_NAME]})
    outputs: dict[str, Any] = {}
    failed: list[str] = []
    for name, actual in zip(OUTPUT_NAMES, actual_values):
        reference = expected[name]
        actual_array = np.asarray(actual)
        same_shape = actual_array.shape == reference.shape
        absolute = np.abs(actual_array.astype(np.float64) - reference.astype(np.float64)) if same_shape else np.asarray([])
        passed = same_shape and bool(np.allclose(actual_array, reference, atol=atol, rtol=rtol))
        outputs[name] = {
            "status": "passed" if passed else "failed",
            "reference_shape": list(reference.shape),
            "actual_shape": list(actual_array.shape),
            "max_absolute_error": float(absolute.max()) if absolute.size else None,
        }
        if not passed:
            failed.append(name)
    return {
        "schema_version": XFEAT_DENSE_PARITY_SCHEMA,
        "created_utc": _utc_now(),
        "status": "passed" if not failed else "failed",
        "publication_mode": "research_only",
        "px4_external_vision": "disabled",
        "export_validation": audit,
        "thresholds": {"atol": atol, "rtol": rtol},
        "failed_outputs": failed,
        "outputs": outputs,
    }


def build_xfeat_sparse_export_manifest(
    *,
    export_root: Path,
    upstream: dict[str, Any],
    weights: Path,
    onnx_path: Path,
    inputs_path: Path,
    outputs_path: Path,
    interface: dict[str, Any],
    opset_version: int,
    detection_threshold: float,
) -> dict[str, Any]:
    return {
        "schema_version": XFEAT_SPARSE_EXPORT_SCHEMA,
        "created_utc": _utc_now(),
        "status": "exported",
        "publication_mode": "research_only",
        "px4_external_vision": "disabled",
        "deployment_state": "local_feature_role_candidate_not_model_release_ready",
        "profile_id": "pi5_crossview_nav_v1",
        "upstream": upstream,
        "weights": {"path": str(weights), "sha256": sha256_file(weights)},
        "onnx": {"path": onnx_path.name, "sha256": sha256_file(onnx_path), "opset_version": opset_version},
        "interface": interface,
        "postprocessing": {
            "source": "pinned_xfeat_detectAndCompute_fixed_cardinality_wrapper",
            "detection_threshold": detection_threshold,
            "nms_kernel_size": 5,
            "descriptor_interpolation": "bicubic_grid_sample_align_corners_false",
            "reliability_interpolation": "bilinear_grid_sample_align_corners_false",
            "invalid_feature_score": -1.0,
        },
        "parity_reference": {
            "inputs_npz": str(inputs_path.relative_to(export_root)), "inputs_sha256": sha256_file(inputs_path),
            "outputs_npz": str(outputs_path.relative_to(export_root)), "outputs_sha256": sha256_file(outputs_path),
        },
        "next_action": (
            "Run standalone ONNX parity, then compare the fixed-cardinality wrapper against the pinned upstream "
            "detectAndCompute output on real map/camera images before binding it to a four-role ModelRelease."
        ),
    }


def export_xfeat_sparse_onnx(
    checkout: str | Path,
    weights_path: str | Path,
    output_dir: str | Path,
    *,
    upstream_lock: str | Path = "research/upstreams.lock.json",
    image_width: int = 640,
    image_height: int = 480,
    maximum_feature_count: int = 2048,
    detection_threshold: float = 0.05,
    device: str = "cpu",
    opset_version: int = 17,
    seed: int = 20260831,
) -> dict[str, Any]:
    """Export fixed-cardinality XFeat local features for a future shadow role."""

    _validate_image_size(image_width, image_height)
    if opset_version < 16:
        raise ValueError("XFeat sparse ONNX export requires opset_version >= 16 for GridSample.")
    if device not in {"cpu", "cuda"}:
        raise ValueError("XFeat sparse ONNX export device must be cpu or cuda.")
    source = Path(checkout).expanduser().resolve()
    weights = Path(weights_path).expanduser().resolve()
    root = Path(output_dir).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError(f"XFeat sparse export directory must be empty: {root}")
    upstream = inspect_pinned_xfeat_checkout(source, upstream_lock=upstream_lock)
    model, torch = _load_xfeat_model(source, weights, device=device)
    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("XFeat CUDA export requested, but PyTorch CUDA is unavailable.")
    wrapper = _fixed_sparse_xfeat_wrapper(
        model, torch,
        image_width=image_width,
        image_height=image_height,
        maximum_feature_count=maximum_feature_count,
        detection_threshold=detection_threshold,
    ).to(device).eval()
    generator = torch.Generator(device=device)
    generator.manual_seed(seed)
    image = torch.rand((1, 3, image_height, image_width), generator=generator, device=device, dtype=torch.float32)
    with torch.no_grad():
        outputs = wrapper(image)
    input_array = image.detach().cpu().numpy().astype(np.float32)
    output_arrays = {name: value.detach().cpu().numpy().astype(np.float32) for name, value in zip(SPARSE_OUTPUT_NAMES, outputs)}
    interface = _sparse_interface_from_arrays({INPUT_NAME: input_array}, output_arrays)
    root.mkdir(parents=True, exist_ok=True)
    onnx_path = root / "xfeat_sparse.onnx"
    try:
        torch.onnx.export(
            wrapper,
            image,
            onnx_path,
            input_names=[INPUT_NAME],
            output_names=list(SPARSE_OUTPUT_NAMES),
            opset_version=opset_version,
            do_constant_folding=True,
            dynamo=False,
        )
    except Exception as exc:
        raise RuntimeError(f"XFeat sparse ONNX export failed: {type(exc).__name__}: {exc}") from exc
    parity_dir = root / "parity"
    parity_dir.mkdir()
    inputs_path = parity_dir / "inputs.npz"
    outputs_path = parity_dir / "pytorch_outputs.npz"
    np.savez_compressed(inputs_path, **{INPUT_NAME: input_array})
    np.savez_compressed(outputs_path, **output_arrays)
    manifest = build_xfeat_sparse_export_manifest(
        export_root=root, upstream=upstream, weights=weights, onnx_path=onnx_path,
        inputs_path=inputs_path, outputs_path=outputs_path, interface=interface,
        opset_version=opset_version, detection_threshold=detection_threshold,
    )
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"output_dir": str(root), "manifest_path": str(manifest_path), "manifest": manifest}


def validate_xfeat_sparse_export(export_dir: str | Path) -> dict[str, Any]:
    """Validate a sparse XFeat export without loading an ONNX provider."""

    root = Path(export_dir).expanduser().resolve()
    manifest_path = root / "manifest.json"
    manifest = _read_json(manifest_path, "XFeat sparse export manifest")
    if manifest.get("schema_version") != XFEAT_SPARSE_EXPORT_SCHEMA or manifest.get("status") != "exported":
        raise ValueError("XFeat sparse export manifest is unsupported or incomplete.")
    if manifest.get("publication_mode") != "research_only" or manifest.get("px4_external_vision") != "disabled":
        raise ValueError("XFeat sparse export must remain research-only with PX4 publication disabled.")
    if manifest.get("deployment_state") != "local_feature_role_candidate_not_model_release_ready":
        raise ValueError("XFeat sparse export must not claim ModelRelease readiness.")
    upstream = manifest.get("upstream") if isinstance(manifest.get("upstream"), dict) else {}
    if upstream.get("project") != XFEAT_PROJECT_NAME or upstream.get("license") != "Apache-2.0" or upstream.get("license_status") != "verified":
        raise ValueError("XFeat sparse export must retain pinned Apache-2.0 provenance.")
    for key in ("commit", "upstream_lock_sha256"):
        if not isinstance(upstream.get(key), str) or not upstream[key]:
            raise ValueError("XFeat sparse export upstream provenance is incomplete.")
    weights = manifest.get("weights") if isinstance(manifest.get("weights"), dict) else {}
    if not isinstance(weights.get("sha256"), str) or len(weights["sha256"]) != 64:
        raise ValueError("XFeat sparse export is missing the immutable source weight digest.")
    onnx = manifest.get("onnx") if isinstance(manifest.get("onnx"), dict) else {}
    onnx_path = _resolve_export_member(root, onnx.get("path"), "ONNX")
    if not onnx_path.is_file() or not isinstance(onnx.get("sha256"), str) or sha256_file(onnx_path) != onnx["sha256"]:
        raise ValueError("XFeat sparse ONNX artifact checksum mismatch.")
    parity = manifest.get("parity_reference") if isinstance(manifest.get("parity_reference"), dict) else {}
    inputs_path = _resolve_export_member(root, parity.get("inputs_npz"), "parity input")
    outputs_path = _resolve_export_member(root, parity.get("outputs_npz"), "parity output")
    for path, key, label in ((inputs_path, "inputs_sha256", "inputs"), (outputs_path, "outputs_sha256", "outputs")):
        if not path.is_file() or not isinstance(parity.get(key), str) or sha256_file(path) != parity[key]:
            raise ValueError(f"XFeat sparse export parity {label} checksum mismatch.")
    interface = _sparse_interface_from_arrays(_load_npz(inputs_path, "parity inputs"), _load_npz(outputs_path, "PyTorch outputs"))
    if manifest.get("interface") != interface:
        raise ValueError("XFeat sparse export interface does not match its immutable parity tensors.")
    postprocessing = manifest.get("postprocessing") if isinstance(manifest.get("postprocessing"), dict) else {}
    if postprocessing.get("source") != "pinned_xfeat_detectAndCompute_fixed_cardinality_wrapper":
        raise ValueError("XFeat sparse export is missing its fixed-cardinality post-processing provenance.")
    return {
        "schema_version": XFEAT_SPARSE_EXPORT_SCHEMA,
        "status": "valid",
        "publication_mode": "research_only",
        "px4_external_vision": "disabled",
        "export_dir": str(root),
        "manifest_sha256": sha256_file(manifest_path),
        "profile_id": manifest.get("profile_id"),
        "upstream": upstream,
        "onnx_path": str(onnx_path),
        "interface": interface,
        "postprocessing": postprocessing,
    }


def evaluate_xfeat_sparse_export_parity(
    export_dir: str | Path,
    *,
    atol: float = 1e-4,
    rtol: float = 1e-3,
) -> dict[str, Any]:
    if atol < 0 or rtol < 0:
        raise ValueError("XFeat sparse parity tolerances must be non-negative.")
    audit = validate_xfeat_sparse_export(export_dir)
    root = Path(export_dir).expanduser().resolve()
    manifest = _read_json(root / "manifest.json", "XFeat sparse export manifest")
    parity = manifest["parity_reference"]
    inputs = _load_npz(_resolve_export_member(root, parity["inputs_npz"], "parity input"), "parity inputs")
    expected = _load_npz(_resolve_export_member(root, parity["outputs_npz"], "parity output"), "PyTorch outputs")
    try:
        import onnxruntime as ort
    except ImportError as exc:
        raise RuntimeError("XFeat sparse ONNX parity requires onnxruntime.") from exc
    session = ort.InferenceSession(audit["onnx_path"], providers=["CPUExecutionProvider"])
    actual_values = session.run(list(SPARSE_OUTPUT_NAMES), {INPUT_NAME: inputs[INPUT_NAME]})
    outputs: dict[str, Any] = {}
    failed: list[str] = []
    for name, actual in zip(SPARSE_OUTPUT_NAMES, actual_values):
        reference = expected[name]
        actual_array = np.asarray(actual)
        same_shape = actual_array.shape == reference.shape
        absolute = np.abs(actual_array.astype(np.float64) - reference.astype(np.float64)) if same_shape else np.asarray([])
        passed = same_shape and bool(np.allclose(actual_array, reference, atol=atol, rtol=rtol))
        outputs[name] = {
            "status": "passed" if passed else "failed",
            "reference_shape": list(reference.shape),
            "actual_shape": list(actual_array.shape),
            "max_absolute_error": float(absolute.max()) if absolute.size else None,
        }
        if not passed:
            failed.append(name)
    return {
        "schema_version": XFEAT_SPARSE_PARITY_SCHEMA,
        "created_utc": _utc_now(),
        "status": "passed" if not failed else "failed",
        "publication_mode": "research_only",
        "px4_external_vision": "disabled",
        "export_validation": audit,
        "thresholds": {"atol": atol, "rtol": rtol},
        "failed_outputs": failed,
        "outputs": outputs,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Export the pinned XFeat dense backbone to a research-only ONNX artifact.")
    parser.add_argument("--checkout", required=True, type=Path, help="Detached checkout matching research/upstreams.lock.json.")
    parser.add_argument("--weights", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--upstream-lock", type=Path, default=Path("research/upstreams.lock.json"))
    parser.add_argument("--image-width", type=int, default=640)
    parser.add_argument("--image-height", type=int, default=480)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--opset-version", type=int, default=17)
    parser.add_argument("--seed", type=int, default=20260831)
    args = parser.parse_args()
    try:
        result = export_xfeat_dense_onnx(
            args.checkout, args.weights, args.output_dir, upstream_lock=args.upstream_lock,
            image_width=args.image_width, image_height=args.image_height, device=args.device,
            opset_version=args.opset_version, seed=args.seed,
        )
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result["manifest"], indent=2, sort_keys=True))


def validate_main() -> None:
    parser = argparse.ArgumentParser(description="Validate a research-only pinned XFeat ONNX export without executing ONNX.")
    parser.add_argument("--export-dir", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = validate_xfeat_dense_export(args.export_dir)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


def parity_main() -> None:
    parser = argparse.ArgumentParser(description="Validate PyTorch-to-ONNX parity for a pinned XFeat dense export.")
    parser.add_argument("--export-dir", required=True, type=Path)
    parser.add_argument("--atol", type=float, default=1e-4)
    parser.add_argument("--rtol", type=float, default=1e-3)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        report = evaluate_xfeat_dense_export_parity(args.export_dir, atol=args.atol, rtol=args.rtol)
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    if report["status"] != "passed":
        raise SystemExit(1)


def sparse_main() -> None:
    parser = argparse.ArgumentParser(description="Export fixed-cardinality pinned XFeat local features to a research-only ONNX artifact.")
    parser.add_argument("--checkout", required=True, type=Path, help="Detached checkout matching research/upstreams.lock.json.")
    parser.add_argument("--weights", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--upstream-lock", type=Path, default=Path("research/upstreams.lock.json"))
    parser.add_argument("--image-width", type=int, default=640)
    parser.add_argument("--image-height", type=int, default=480)
    parser.add_argument("--maximum-feature-count", type=int, default=2048)
    parser.add_argument("--detection-threshold", type=float, default=0.05)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--opset-version", type=int, default=17)
    parser.add_argument("--seed", type=int, default=20260831)
    args = parser.parse_args()
    try:
        result = export_xfeat_sparse_onnx(
            args.checkout, args.weights, args.output_dir, upstream_lock=args.upstream_lock,
            image_width=args.image_width, image_height=args.image_height,
            maximum_feature_count=args.maximum_feature_count, detection_threshold=args.detection_threshold,
            device=args.device, opset_version=args.opset_version, seed=args.seed,
        )
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result["manifest"], indent=2, sort_keys=True))


def sparse_validate_main() -> None:
    parser = argparse.ArgumentParser(description="Validate a research-only fixed-cardinality XFeat sparse ONNX export without executing ONNX.")
    parser.add_argument("--export-dir", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = validate_xfeat_sparse_export(args.export_dir)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


def sparse_parity_main() -> None:
    parser = argparse.ArgumentParser(description="Validate PyTorch-to-ONNX parity for a fixed-cardinality XFeat sparse export.")
    parser.add_argument("--export-dir", required=True, type=Path)
    parser.add_argument("--atol", type=float, default=1e-4)
    parser.add_argument("--rtol", type=float, default=1e-3)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        report = evaluate_xfeat_sparse_export_parity(args.export_dir, atol=args.atol, rtol=args.rtol)
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    if report["status"] != "passed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
