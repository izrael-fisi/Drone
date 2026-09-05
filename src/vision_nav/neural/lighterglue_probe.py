"""Record a research-only compatibility probe for XFeat and LighterGlue.

The official XFeat repository ships its smaller LighterGlue configuration.  Its
normal Python API uses dynamic match lists, which is valuable as a reference but
not directly suitable for a fixed-shape vehicle ONNX contract.  This module
records immutable input/output evidence from that exact upstream implementation
before a separate fixed-shape ONNX exporter is considered.

It does not export ONNX, create a ModelRelease, transfer artifacts to a Pi, or
publish any PX4 message.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import importlib
import json
from pathlib import Path
import sys
from typing import Any

import numpy as np

from vision_nav.training_capture import sha256_file

from .upstreams import canonical_digest, load_upstream_lock
from .xfeat_export import _git_revision


LIGHTERGLUE_PROBE_SCHEMA = "vision_nav_lighterglue_probe_v1"
LIGHTERGLUE_PROJECT_NAME = "xfeat"
_REQUIRED_SOURCE_FILES = ("modules/lighterglue.py", "modules/xfeat.py", "LICENSE")


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


def inspect_pinned_lighterglue_checkout(
    checkout: str | Path,
    *,
    upstream_lock: str | Path,
) -> dict[str, Any]:
    """Verify the official XFeat checkout that owns the LighterGlue matcher."""

    root = Path(checkout).expanduser().resolve()
    lock_path = Path(upstream_lock).expanduser().resolve()
    if not root.is_dir():
        raise ValueError(f"LighterGlue checkout does not exist: {root}")
    lock = load_upstream_lock(lock_path)
    try:
        project = next(project for project in lock["projects"] if project["name"] == LIGHTERGLUE_PROJECT_NAME)
    except StopIteration as exc:
        raise ValueError("Research upstream lock does not include the official XFeat/LighterGlue source.") from exc
    if project.get("repository") != "https://github.com/verlab/accelerated_features.git":
        raise ValueError("LighterGlue must use the official verlab/accelerated_features source, not generic LightGlue.")
    if project.get("license_status") != "verified" or project.get("license") != "Apache-2.0":
        raise ValueError("Research upstream lock must record XFeat/LighterGlue as verified Apache-2.0.")
    revision = _git_revision(root)
    if revision != project["commit"]:
        raise ValueError(
            "LighterGlue checkout revision does not match research/upstreams.lock.json: "
            f"expected {project['commit']}, got {revision}."
        )
    missing = [relative for relative in _REQUIRED_SOURCE_FILES if not (root / relative).is_file()]
    if missing:
        raise ValueError(f"Pinned XFeat/LighterGlue checkout is missing required files: {', '.join(missing)}")
    license_text = (root / "LICENSE").read_text(encoding="utf-8", errors="replace")
    if "Apache License" not in license_text or "Version 2.0" not in license_text:
        raise ValueError("Pinned XFeat/LighterGlue checkout does not contain Apache-2.0 license text.")
    return {
        "project": "xfeat_lighterglue",
        "repository": project["repository"],
        "commit": revision,
        "license": project["license"],
        "license_status": project["license_status"],
        "checkout": str(root),
        "upstream_lock": str(lock_path),
        "upstream_lock_sha256": canonical_digest(lock),
    }


def _load_lighterglue(checkout: Path, weights: Path, *, device: str) -> tuple[Any, Any]:
    try:
        import torch
    except ImportError as exc:
        raise RuntimeError("LighterGlue probing requires drone-vision-nav[research] and PyTorch.") from exc
    try:
        import kornia  # noqa: F401  # The upstream module imports kornia.feature.lightglue.
    except ImportError as exc:
        raise RuntimeError("LighterGlue probing requires kornia>=0.7 from research/requirements-research.txt.") from exc
    if device not in {"cpu", "cuda"}:
        raise ValueError("LighterGlue probe device must be cpu or cuda.")
    if device == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("LighterGlue CUDA probe requested, but PyTorch CUDA is unavailable.")
    if not weights.is_file():
        raise ValueError(f"LighterGlue weights do not exist: {weights}")
    module_root = str(checkout)
    existing = sys.modules.get("modules")
    if existing is not None:
        existing_path = Path(getattr(existing, "__file__", "") or "").resolve()
        if module_root not in str(existing_path):
            raise RuntimeError("Another Python package named 'modules' is already loaded. Run the probe in a fresh process.")
    sys.path.insert(0, module_root)
    try:
        module = importlib.import_module("modules.lighterglue")
        matcher = module.LighterGlue(weights=str(weights)).to(device).eval()
    finally:
        try:
            sys.path.remove(module_root)
        except ValueError:
            pass
    return matcher, torch


def _validate_probe_arrays(inputs: dict[str, np.ndarray], outputs: dict[str, np.ndarray]) -> dict[str, Any]:
    required_inputs = {"keypoints0", "keypoints1", "descriptors0", "descriptors1", "image_size0", "image_size1"}
    required_outputs = {"matches0", "matching_scores0", "matches1", "matching_scores1", "matches", "scores"}
    if set(inputs) != required_inputs:
        raise ValueError("LighterGlue probe inputs do not match the fixed XFeat matcher interface.")
    if set(outputs) != required_outputs:
        raise ValueError("LighterGlue probe outputs do not match the official matcher evidence interface.")
    keypoints0, keypoints1 = inputs["keypoints0"], inputs["keypoints1"]
    descriptors0, descriptors1 = inputs["descriptors0"], inputs["descriptors1"]
    if keypoints0.ndim != 3 or keypoints1.ndim != 3 or keypoints0.shape[:1] != (1,) or keypoints1.shape[:1] != (1,):
        raise ValueError("LighterGlue probe keypoints must have one fixed batch with [1, N, 2] shape.")
    if keypoints0.shape[-1] != 2 or keypoints1.shape[-1] != 2:
        raise ValueError("LighterGlue probe keypoints must be XY pairs.")
    if descriptors0.shape != (1, keypoints0.shape[1], 64) or descriptors1.shape != (1, keypoints1.shape[1], 64):
        raise ValueError("LighterGlue requires fixed XFeat 64-dimensional descriptors aligned to keypoints.")
    if inputs["image_size0"].shape != (1, 2) or inputs["image_size1"].shape != (1, 2):
        raise ValueError("LighterGlue probe image sizes must have [1, 2] width/height shape.")
    if not all(np.issubdtype(value.dtype, np.number) and np.isfinite(value).all() for value in inputs.values()):
        raise ValueError("LighterGlue probe inputs must be finite numeric arrays.")
    if outputs["matches0"].shape != (1, keypoints0.shape[1]) or outputs["matches1"].shape != (1, keypoints1.shape[1]):
        raise ValueError("LighterGlue probe match-index arrays must align with input feature counts.")
    if outputs["matching_scores0"].shape != outputs["matches0"].shape or outputs["matching_scores1"].shape != outputs["matches1"].shape:
        raise ValueError("LighterGlue probe matching scores must align with match-index arrays.")
    matches = outputs["matches"]
    scores = outputs["scores"]
    if matches.ndim != 2 or matches.shape[1:] != (2,) or scores.ndim != 1 or scores.shape[0] != matches.shape[0]:
        raise ValueError("LighterGlue probe correspondence evidence must be N x 2 pairs with one score each.")
    if np.any(matches[:, 0] < 0) or np.any(matches[:, 0] >= keypoints0.shape[1]) or np.any(matches[:, 1] < 0) or np.any(matches[:, 1] >= keypoints1.shape[1]):
        raise ValueError("LighterGlue probe correspondence indices are outside the XFeat feature arrays.")
    if not np.isfinite(np.concatenate([np.asarray(value).reshape(-1) for value in outputs.values()])).all():
        raise ValueError("LighterGlue probe outputs must be finite numeric arrays.")
    return {
        "input_feature_count": {"query": int(keypoints0.shape[1]), "map": int(keypoints1.shape[1])},
        "descriptor_dimension": 64,
        "accepted_match_count": int(matches.shape[0]),
        "match_ratio_query": float(matches.shape[0] / max(1, keypoints0.shape[1])),
        "match_ratio_map": float(matches.shape[0] / max(1, keypoints1.shape[1])),
        "score_range": {"min": float(np.min(scores)) if scores.size else None, "max": float(np.max(scores)) if scores.size else None},
    }


def build_lighterglue_probe_manifest(
    *,
    probe_root: Path,
    upstream: dict[str, Any],
    weights: Path,
    inputs_path: Path,
    outputs_path: Path,
    interface: dict[str, Any],
    minimum_confidence: float,
) -> dict[str, Any]:
    return {
        "schema_version": LIGHTERGLUE_PROBE_SCHEMA,
        "created_utc": _utc_now(),
        "status": "probed",
        "publication_mode": "research_only",
        "px4_external_vision": "disabled",
        "deployment_state": "upstream_matcher_contract_probe_not_onnx_ready",
        "profile_id": "pi5_crossview_nav_v1",
        "upstream": upstream,
        "weights": {"path": str(weights), "sha256": sha256_file(weights)},
        "matcher": {
            "name": "XFeat LighterGlue",
            "minimum_confidence": minimum_confidence,
            "input_descriptor_dimension": 64,
            "fixed_cardinality_required_for_onnx": True,
        },
        "interface": interface,
        "reference": {
            "inputs_npz": str(inputs_path.relative_to(probe_root)),
            "inputs_sha256": sha256_file(inputs_path),
            "outputs_npz": str(outputs_path.relative_to(probe_root)),
            "outputs_sha256": sha256_file(outputs_path),
        },
        "next_action": (
            "Compare the upstream reference outputs against a fixed-cardinality ONNX candidate on real camera/map pairs. "
            "Do not bind this probe to a ModelRelease or copy it to a Pi."
        ),
    }


def probe_lighterglue(
    checkout: str | Path,
    weights_path: str | Path,
    output_dir: str | Path,
    *,
    upstream_lock: str | Path = "research/upstreams.lock.json",
    feature_count: int = 1024,
    image_width: int = 640,
    image_height: int = 384,
    minimum_confidence: float = 0.2,
    device: str = "cpu",
    seed: int = 20260831,
) -> dict[str, Any]:
    """Run the pinned upstream matcher once and record its fixed-shape evidence."""

    if feature_count <= 0:
        raise ValueError("LighterGlue feature_count must be positive.")
    if image_width <= 0 or image_height <= 0:
        raise ValueError("LighterGlue image dimensions must be positive.")
    if not 0.0 < minimum_confidence < 1.0:
        raise ValueError("LighterGlue minimum_confidence must be between zero and one.")
    root = Path(output_dir).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError(f"LighterGlue probe directory must be empty: {root}")
    source = Path(checkout).expanduser().resolve()
    weights = Path(weights_path).expanduser().resolve()
    upstream = inspect_pinned_lighterglue_checkout(source, upstream_lock=upstream_lock)
    matcher, torch = _load_lighterglue(source, weights, device=device)
    generator = torch.Generator(device=device)
    generator.manual_seed(seed)
    keypoints0 = torch.stack((torch.rand((1, feature_count), generator=generator, device=device) * (image_width - 1), torch.rand((1, feature_count), generator=generator, device=device) * (image_height - 1)), dim=-1)
    keypoints1 = torch.stack((torch.rand((1, feature_count), generator=generator, device=device) * (image_width - 1), torch.rand((1, feature_count), generator=generator, device=device) * (image_height - 1)), dim=-1)
    descriptors0 = torch.nn.functional.normalize(torch.rand((1, feature_count, 64), generator=generator, device=device), dim=-1)
    descriptors1 = torch.nn.functional.normalize(torch.rand((1, feature_count, 64), generator=generator, device=device), dim=-1)
    image_size = torch.tensor([[image_width, image_height]], dtype=torch.float32, device=device)
    inputs_torch = {
        "keypoints0": keypoints0, "keypoints1": keypoints1,
        "descriptors0": descriptors0, "descriptors1": descriptors1,
        "image_size0": image_size, "image_size1": image_size,
    }
    with torch.inference_mode():
        raw = matcher(inputs_torch, min_conf=minimum_confidence)
    matches = raw["matches"][0].detach().cpu().numpy().astype(np.int64)
    scores = raw["scores"][0].detach().cpu().numpy().astype(np.float32)
    inputs = {name: value.detach().cpu().numpy().astype(np.float32) for name, value in inputs_torch.items()}
    outputs = {
        "matches0": raw["matches0"].detach().cpu().numpy().astype(np.int64),
        "matching_scores0": raw["matching_scores0"].detach().cpu().numpy().astype(np.float32),
        "matches1": raw["matches1"].detach().cpu().numpy().astype(np.int64),
        "matching_scores1": raw["matching_scores1"].detach().cpu().numpy().astype(np.float32),
        "matches": matches,
        "scores": scores,
    }
    interface = _validate_probe_arrays(inputs, outputs)
    root.mkdir(parents=True, exist_ok=True)
    reference = root / "reference"
    reference.mkdir()
    inputs_path = reference / "inputs.npz"
    outputs_path = reference / "upstream_outputs.npz"
    np.savez_compressed(inputs_path, **inputs)
    np.savez_compressed(outputs_path, **outputs)
    manifest = build_lighterglue_probe_manifest(
        probe_root=root,
        upstream=upstream,
        weights=weights,
        inputs_path=inputs_path,
        outputs_path=outputs_path,
        interface=interface,
        minimum_confidence=minimum_confidence,
    )
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"output_dir": str(root), "manifest_path": str(manifest_path), "manifest": manifest}


def validate_lighterglue_probe(probe_dir: str | Path) -> dict[str, Any]:
    """Validate a probe's provenance and recorded upstream matcher contract."""

    root = Path(probe_dir).expanduser().resolve()
    manifest = _read_json(root / "manifest.json", "LighterGlue probe manifest")
    errors: list[str] = []
    if manifest.get("schema_version") != LIGHTERGLUE_PROBE_SCHEMA or manifest.get("status") != "probed":
        errors.append("unsupported_or_incomplete_probe_manifest")
    if manifest.get("publication_mode") != "research_only" or manifest.get("px4_external_vision") != "disabled":
        errors.append("probe_must_remain_research_only_with_px4_disabled")
    if manifest.get("deployment_state") != "upstream_matcher_contract_probe_not_onnx_ready":
        errors.append("probe_must_not_claim_onnx_or_model_release_readiness")
    upstream = manifest.get("upstream") if isinstance(manifest.get("upstream"), dict) else {}
    if upstream.get("project") != "xfeat_lighterglue" or upstream.get("repository") != "https://github.com/verlab/accelerated_features.git":
        errors.append("probe_requires_official_xfeat_lighterglue_provenance")
    if upstream.get("license") != "Apache-2.0" or upstream.get("license_status") != "verified":
        errors.append("probe_requires_verified_apache_provenance")
    reference = manifest.get("reference") if isinstance(manifest.get("reference"), dict) else {}
    try:
        inputs_path = root / str(reference["inputs_npz"])
        outputs_path = root / str(reference["outputs_npz"])
        if sha256_file(inputs_path) != reference.get("inputs_sha256") or sha256_file(outputs_path) != reference.get("outputs_sha256"):
            errors.append("probe_reference_checksum_mismatch")
        with np.load(inputs_path, allow_pickle=False) as archive:
            inputs = {name: np.asarray(archive[name]) for name in archive.files}
        with np.load(outputs_path, allow_pickle=False) as archive:
            outputs = {name: np.asarray(archive[name]) for name in archive.files}
        interface = _validate_probe_arrays(inputs, outputs)
        if manifest.get("interface") != interface:
            errors.append("probe_interface_mismatch")
    except (KeyError, OSError, ValueError) as exc:
        errors.append(f"probe_reference_invalid:{type(exc).__name__}:{exc}")
        interface = None
    return {
        "status": "valid" if not errors else "invalid",
        "probe_dir": str(root),
        "interface": interface,
        "errors": errors,
        "next_action": (
            "Build and compare a fixed-cardinality ONNX matcher candidate on real XFeat camera/map features."
            if not errors else "Repair the immutable upstream probe before attempting a matcher ONNX export."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Record or validate a research-only official XFeat LighterGlue compatibility probe.")
    parser.add_argument("--probe-dir", type=Path, help="Validate an existing probe directory instead of creating one.")
    parser.add_argument("--checkout", type=Path, help="Detached official XFeat checkout matching the upstream lock.")
    parser.add_argument("--weights", type=Path, help="Explicit local xfeat-lighterglue.pt checkpoint.")
    parser.add_argument("--output-dir", type=Path, help="Empty output directory for a new probe.")
    parser.add_argument("--upstream-lock", type=Path, default=Path("research/upstreams.lock.json"))
    parser.add_argument("--feature-count", type=int, default=1024)
    parser.add_argument("--image-width", type=int, default=640)
    parser.add_argument("--image-height", type=int, default=384)
    parser.add_argument("--minimum-confidence", type=float, default=0.2)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    args = parser.parse_args()
    if args.probe_dir:
        if any(value is not None for value in (args.checkout, args.weights, args.output_dir)):
            parser.error("--probe-dir cannot be combined with new-probe inputs.")
        result = validate_lighterglue_probe(args.probe_dir)
    else:
        if not all((args.checkout, args.weights, args.output_dir)):
            parser.error("--checkout, --weights, and --output-dir are required to create a probe.")
        result = probe_lighterglue(
            args.checkout, args.weights, args.output_dir,
            upstream_lock=args.upstream_lock, feature_count=args.feature_count,
            image_width=args.image_width, image_height=args.image_height,
            minimum_confidence=args.minimum_confidence, device=args.device,
        )
    print(json.dumps(result, indent=2, sort_keys=True))
    if result.get("status") == "invalid":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
