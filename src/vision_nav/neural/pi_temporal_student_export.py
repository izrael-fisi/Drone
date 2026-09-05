"""Export the compact Pi temporal student to ONNX with immutable parity inputs."""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Any

import numpy as np

from vision_nav.training_capture import sha256_file

from .pi_temporal_student import PI_TEMPORAL_STUDENT_SCHEMA, PiTemporalStudentConfig, build_pi_temporal_student, choose_torch_device
from .map_release import validate_release as validate_map_release
from .releases import ModelRelease, load_model_release
from .teacher_targets import TeacherTargetDataset, validate_teacher_targets


PI_TEMPORAL_STUDENT_EXPORT_SCHEMA = "vision_nav_pi_temporal_student_export_v1"
PI_TEMPORAL_STUDENT_PARITY_SCHEMA = "vision_nav_pi_temporal_student_parity_v1"
INPUT_NAMES = ("frame_window", "attitude_window")
OUTPUT_NAMES = ("location_distribution", "heading_distribution", "temporal_feature")
TEMPORAL_INPUT_SOURCES = {
    "frame_window": "frame_window_rgb",
    "attitude_window": "attitude_window_rpy",
}
TEMPORAL_OUTPUT_TAGS = {
    "location_distribution": "temporal_location_evidence",
    "heading_distribution": "temporal_heading_evidence",
    "temporal_feature": "temporal_feature_evidence",
}


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


def _load_student_checkpoint(
    checkpoint_path: Path, *, target_dir: str | Path, window_dir: str | Path, device: str,
) -> tuple[Any, PiTemporalStudentConfig, Any, dict[str, Any], dict[str, str]]:
    try:
        import torch
    except ImportError as exc:
        raise RuntimeError("Pi temporal student ONNX export requires drone-vision-nav[research] (PyTorch).") from exc
    target_audit = validate_teacher_targets(target_dir, window_dir=window_dir)
    try:
        payload = torch.load(checkpoint_path, map_location=device, weights_only=True)
    except TypeError:
        payload = torch.load(checkpoint_path, map_location=device)
    if not isinstance(payload, dict) or payload.get("schema_version") != PI_TEMPORAL_STUDENT_SCHEMA:
        raise ValueError("ONNX export requires a pi temporal student checkpoint.")
    if payload.get("teacher_targets_manifest_sha256") != target_audit["manifest_sha256"]:
        raise ValueError("Student checkpoint was trained against a different teacher-target manifest.")
    if payload.get("temporal_windows_manifest_sha256") != target_audit["temporal_windows_manifest_sha256"]:
        raise ValueError("Student checkpoint was trained against a different temporal-window manifest.")
    map_release_id = payload.get("map_release_id")
    calibration_id = payload.get("calibration_id")
    if not isinstance(map_release_id, str) or not map_release_id or not isinstance(calibration_id, str) or not calibration_id:
        raise ValueError("Student checkpoint is missing its map-release or camera-calibration identity.")
    raw_config = payload.get("config")
    if not isinstance(raw_config, dict):
        raise ValueError("Student checkpoint is missing its configuration.")
    try:
        config = PiTemporalStudentConfig(**raw_config)
        config.validate()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Student checkpoint has invalid configuration: {exc}") from exc
    model = build_pi_temporal_student(config).to(device)
    model.load_state_dict(payload.get("state_dict", {}), strict=True)
    model.eval()
    return model, config, torch, target_audit, {"map_release_id": map_release_id, "calibration_id": calibration_id}


def export_pi_temporal_student_onnx(
    checkpoint_path: str | Path,
    window_dir: str | Path,
    target_dir: str | Path,
    output_dir: str | Path,
    *,
    device: str = "auto",
    opset_version: int = 17,
) -> dict[str, Any]:
    """Export one exact-window ONNX model and PyTorch reference tensors."""

    if opset_version < 13:
        raise ValueError("Pi temporal student ONNX export requires opset_version >= 13.")
    checkpoint = Path(checkpoint_path).expanduser().resolve()
    if not checkpoint.is_file():
        raise ValueError(f"Pi temporal student checkpoint does not exist: {checkpoint}")
    resolved_device = choose_torch_device(device)
    model, config, torch, target_audit, compatibility = _load_student_checkpoint(
        checkpoint, target_dir=target_dir, window_dir=window_dir, device=resolved_device,
    )
    root = Path(output_dir).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError(f"Pi temporal student export directory must be empty: {root}")
    root.mkdir(parents=True, exist_ok=True)
    dataset = TeacherTargetDataset(
        window_dir, target_dir, split="validation", image_size=(config.image_width, config.image_height),
    )
    if not len(dataset):
        raise ValueError("Pi temporal student ONNX export requires a validation window.")
    item = dataset[0]
    frame_window = item["frame_rgb"].unsqueeze(0).to(resolved_device)
    attitude_window = item["attitude_rpy_rad"].unsqueeze(0).to(resolved_device)
    with torch.no_grad():
        reference_outputs = model(frame_window, attitude_window)
    onnx_path = root / "temporal_student.onnx"
    try:
        torch.onnx.export(
            model,
            (frame_window, attitude_window),
            onnx_path,
            input_names=list(INPUT_NAMES),
            output_names=list(OUTPUT_NAMES),
            opset_version=opset_version,
            do_constant_folding=True,
        )
    except Exception as exc:
        raise RuntimeError(f"Pi temporal student ONNX export failed: {type(exc).__name__}: {exc}") from exc
    parity_root = root / "parity"
    parity_root.mkdir()
    inputs_path = parity_root / "inputs.npz"
    outputs_path = parity_root / "pytorch_outputs.npz"
    np.savez_compressed(
        inputs_path,
        frame_window=frame_window.detach().cpu().numpy().astype(np.float32),
        attitude_window=attitude_window.detach().cpu().numpy().astype(np.float32),
    )
    np.savez_compressed(
        outputs_path,
        location_distribution=reference_outputs[0].detach().cpu().numpy().astype(np.float32),
        heading_distribution=reference_outputs[1].detach().cpu().numpy().astype(np.float32),
        temporal_feature=reference_outputs[2].detach().cpu().numpy().astype(np.float32),
    )
    manifest = {
        "schema_version": PI_TEMPORAL_STUDENT_EXPORT_SCHEMA,
        "created_utc": _utc_now(),
        "status": "exported",
        "publication_mode": "research_only",
        "px4_external_vision": "disabled",
        "profile_id": "pi5_crossview_nav_v1",
        "checkpoint": {"path": str(checkpoint), "sha256": sha256_file(checkpoint)},
        "teacher_targets_manifest_sha256": target_audit["manifest_sha256"],
        "temporal_windows_manifest_sha256": target_audit["temporal_windows_manifest_sha256"],
        "map_release_id": compatibility["map_release_id"],
        "calibration_id": compatibility["calibration_id"],
        "config": config.__dict__,
        "onnx": {"path": onnx_path.name, "sha256": sha256_file(onnx_path), "opset_version": opset_version},
        "interface": {
            "inputs": {
                "frame_window": {"rank": 5, "shape": list(frame_window.shape)},
                "attitude_window": {"rank": 3, "shape": list(attitude_window.shape)},
            },
            "outputs": {
                "location_distribution": {"rank": 2, "shape": list(reference_outputs[0].shape)},
                "heading_distribution": {"rank": 2, "shape": list(reference_outputs[1].shape)},
                "temporal_feature": {"rank": 2, "shape": list(reference_outputs[2].shape)},
            },
            "window_size": int(frame_window.shape[1]),
        },
        "parity_reference": {
            "window_id": item["window_id"],
            "inputs_npz": str(inputs_path.relative_to(root)), "inputs_sha256": sha256_file(inputs_path),
            "outputs_npz": str(outputs_path.relative_to(root)), "outputs_sha256": sha256_file(outputs_path),
        },
        "next_action": "Run standalone ONNX parity, then add this artifact to a complete four-role ModelRelease for Pi shadow validation.",
    }
    manifest_path = root / "manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"output_dir": str(root), "manifest_path": str(manifest_path), "manifest": manifest}


def _load_npz(path: Path, label: str) -> dict[str, np.ndarray]:
    if not path.is_file():
        raise ValueError(f"Missing {label}: {path}")
    try:
        with np.load(path, allow_pickle=False) as archive:
            return {name: np.asarray(archive[name]) for name in archive.files}
    except (OSError, ValueError) as exc:
        raise ValueError(f"Could not load {label}: {path}: {exc}") from exc


def _resolve_export_member(root: Path, raw_path: Any, label: str) -> Path:
    """Resolve a manifest-owned path without allowing it to escape the export."""

    if not isinstance(raw_path, str) or not raw_path:
        raise ValueError(f"Pi temporal student export {label} path is missing.")
    candidate = (root / raw_path).resolve()
    try:
        candidate.relative_to(root)
    except ValueError as exc:
        raise ValueError(f"Pi temporal student export {label} path escapes its export directory.") from exc
    return candidate


def _validate_tensor_interface(
    interface: dict[str, Any], inputs: dict[str, np.ndarray], outputs: dict[str, np.ndarray],
) -> dict[str, Any]:
    declared_inputs = interface.get("inputs") if isinstance(interface.get("inputs"), dict) else {}
    declared_outputs = interface.get("outputs") if isinstance(interface.get("outputs"), dict) else {}
    if set(declared_inputs) != set(INPUT_NAMES) or set(declared_outputs) != set(OUTPUT_NAMES):
        raise ValueError("Pi temporal student export interface does not name the expected inputs and outputs.")
    checked: dict[str, Any] = {"inputs": {}, "outputs": {}}
    for collection_name, arrays, declarations in (
        ("inputs", inputs, declared_inputs),
        ("outputs", outputs, declared_outputs),
    ):
        for name, array in arrays.items():
            declaration = declarations.get(name)
            if not isinstance(declaration, dict):
                raise ValueError(f"Pi temporal student export interface is missing {collection_name}.{name}.")
            declared_shape = declaration.get("shape")
            declared_rank = declaration.get("rank")
            if not isinstance(declared_shape, list) or declared_shape != list(array.shape) or declared_rank != array.ndim:
                raise ValueError(f"Pi temporal student export interface shape mismatch for {collection_name}.{name}.")
            if array.dtype.kind not in {"f"} or not np.isfinite(array).all():
                raise ValueError(f"Pi temporal student export tensor {collection_name}.{name} must be finite floating point.")
            checked[collection_name][name] = {"shape": list(array.shape), "dtype": str(array.dtype)}
    frame_shape = inputs["frame_window"].shape
    attitude_shape = inputs["attitude_window"].shape
    if len(frame_shape) != 5 or len(attitude_shape) != 3:
        raise ValueError("Pi temporal student export temporal inputs have invalid ranks.")
    if frame_shape[:2] != attitude_shape[:2] or attitude_shape[-1] != 3:
        raise ValueError("Pi temporal student export temporal inputs have incompatible batch/window or attitude axes.")
    if interface.get("window_size") != int(frame_shape[1]):
        raise ValueError("Pi temporal student export window_size does not match parity tensors.")
    return checked


def validate_pi_temporal_student_export(export_dir: str | Path) -> dict[str, Any]:
    """Validate the hash-bound standalone student export before graph execution.

    This is intentionally independent of ONNX Runtime. It verifies provenance,
    tensor names, shapes and checksums on the training host before a Pi or CUDA
    machine performs the stricter ONNX graph parity execution.
    """

    root = Path(export_dir).expanduser().resolve()
    manifest_path = root / "manifest.json"
    manifest = _read_json(manifest_path, "Pi temporal student export manifest")
    if manifest.get("schema_version") != PI_TEMPORAL_STUDENT_EXPORT_SCHEMA or manifest.get("status") != "exported":
        raise ValueError("Pi temporal student export manifest is unsupported or incomplete.")
    if manifest.get("publication_mode") != "research_only" or manifest.get("px4_external_vision") != "disabled":
        raise ValueError("Pi temporal student export must remain research-only with PX4 publication disabled.")
    for field in ("map_release_id", "calibration_id", "teacher_targets_manifest_sha256", "temporal_windows_manifest_sha256"):
        if not isinstance(manifest.get(field), str) or not manifest[field]:
            raise ValueError(f"Pi temporal student export is missing {field}.")
    onnx = manifest.get("onnx") if isinstance(manifest.get("onnx"), dict) else {}
    onnx_path = _resolve_export_member(root, onnx.get("path"), "ONNX")
    if not onnx_path.is_file() or not isinstance(onnx.get("sha256"), str) or sha256_file(onnx_path) != onnx["sha256"]:
        raise ValueError("Pi temporal student ONNX artifact checksum mismatch.")
    parity = manifest.get("parity_reference") if isinstance(manifest.get("parity_reference"), dict) else {}
    inputs_path = _resolve_export_member(root, parity.get("inputs_npz"), "parity input")
    outputs_path = _resolve_export_member(root, parity.get("outputs_npz"), "parity output")
    for path, key, label in (
        (inputs_path, "inputs_sha256", "inputs"),
        (outputs_path, "outputs_sha256", "outputs"),
    ):
        if not path.is_file() or not isinstance(parity.get(key), str) or sha256_file(path) != parity[key]:
            raise ValueError(f"Pi temporal student parity {label} checksum mismatch.")
    inputs = _load_npz(inputs_path, "Pi temporal student parity inputs")
    outputs = _load_npz(outputs_path, "Pi temporal student PyTorch outputs")
    if set(inputs) != set(INPUT_NAMES) or set(outputs) != set(OUTPUT_NAMES):
        raise ValueError("Pi temporal student parity tensors do not match the export interface.")
    interface = manifest.get("interface") if isinstance(manifest.get("interface"), dict) else {}
    tensor_summary = _validate_tensor_interface(interface, inputs, outputs)
    return {
        "schema_version": PI_TEMPORAL_STUDENT_EXPORT_SCHEMA,
        "status": "valid",
        "publication_mode": "research_only",
        "export_dir": str(root),
        "manifest_sha256": sha256_file(manifest_path),
        "profile_id": manifest.get("profile_id"),
        "map_release_id": manifest["map_release_id"],
        "calibration_id": manifest["calibration_id"],
        "onnx_path": str(onnx_path),
        "tensor_summary": tensor_summary,
    }


def validate_pi_temporal_student_release_binding(
    export_dir: str | Path,
    *,
    model_release: ModelRelease,
    map_release: dict[str, Any],
    artifact_root: str | Path,
) -> dict[str, Any]:
    """Verify that one standalone student export is attached safely to a release.

    A standalone student remains a research artifact. This gate binds its exact
    ONNX file, temporal tensor contract, map identity, and measured camera
    calibration to the future four-role release without treating it as flight
    authority. Full release validation and provider parity remain separate
    promotion gates.
    """

    export_audit = validate_pi_temporal_student_export(export_dir)
    root = Path(artifact_root).expanduser().resolve()
    errors: list[str] = []

    try:
        validate_map_release(map_release, model_release=model_release.to_dict())
    except (TypeError, ValueError) as exc:
        errors.append(f"map_release_invalid:{exc}")

    export_profile = export_audit["profile_id"]
    if model_release.profile_id != export_profile:
        errors.append("profile_id_mismatch")
    if map_release.get("profile_id") != export_profile:
        errors.append("map_profile_id_mismatch")
    if map_release.get("release_id") != export_audit["map_release_id"]:
        errors.append("map_release_id_mismatch")
    calibration_id = export_audit["calibration_id"]
    if calibration_id not in model_release.calibration_ids:
        errors.append("model_calibration_id_mismatch")
    raw_map_calibrations = map_release.get("calibration_ids")
    if not isinstance(raw_map_calibrations, list) or calibration_id not in raw_map_calibrations:
        errors.append("map_calibration_id_mismatch")

    artifact_location = model_release.artifact_paths.get("temporal_student")
    artifact_path: Path | None = None
    if not isinstance(artifact_location, str) or not artifact_location:
        errors.append("model_release_missing_temporal_student_artifact")
    else:
        artifact_path = (root / artifact_location).resolve()
        try:
            artifact_path.relative_to(root)
        except ValueError:
            errors.append("temporal_student_artifact_path_escapes_root")
        if artifact_path.is_file():
            export_manifest = _read_json(Path(export_dir).expanduser().resolve() / "manifest.json", "Pi temporal student export manifest")
            if sha256_file(artifact_path) != export_manifest["onnx"]["sha256"]:
                errors.append("temporal_student_artifact_digest_mismatch")
        else:
            errors.append("model_release_temporal_student_artifact_missing")

    contract = model_release.artifact_interfaces.get("temporal_student")
    if not isinstance(contract, dict):
        errors.append("model_release_missing_temporal_student_interface")
    else:
        inputs = contract.get("inputs") if isinstance(contract.get("inputs"), dict) else {}
        outputs = contract.get("outputs") if isinstance(contract.get("outputs"), dict) else {}
        exported_interface = _read_json(Path(export_dir).expanduser().resolve() / "manifest.json", "Pi temporal student export manifest").get("interface", {})
        exported_window_size = exported_interface.get("window_size") if isinstance(exported_interface, dict) else None
        if set(inputs) != set(INPUT_NAMES):
            errors.append("temporal_student_input_contract_mismatch")
        if set(outputs) != set(OUTPUT_NAMES):
            errors.append("temporal_student_output_contract_mismatch")
        for name in INPUT_NAMES:
            spec = inputs.get(name)
            exported_spec = exported_interface.get("inputs", {}).get(name) if isinstance(exported_interface, dict) else None
            if not isinstance(spec, dict) or not isinstance(exported_spec, dict):
                continue
            if spec.get("name") != name or spec.get("rank") != exported_spec.get("rank"):
                errors.append(f"temporal_student_input_interface_mismatch:{name}")
            if spec.get("shadow_source") != TEMPORAL_INPUT_SOURCES[name] or spec.get("window_size") != exported_window_size:
                errors.append(f"temporal_student_input_temporal_contract_mismatch:{name}")
        for name in OUTPUT_NAMES:
            spec = outputs.get(name)
            exported_spec = exported_interface.get("outputs", {}).get(name) if isinstance(exported_interface, dict) else None
            if not isinstance(spec, dict) or not isinstance(exported_spec, dict):
                continue
            if spec.get("name") != name or spec.get("rank") != exported_spec.get("rank"):
                errors.append(f"temporal_student_output_interface_mismatch:{name}")
            if spec.get("shadow_output") != TEMPORAL_OUTPUT_TAGS[name]:
                errors.append(f"temporal_student_output_shadow_tag_mismatch:{name}")

    return {
        "schema_version": "vision_nav_pi_temporal_student_release_binding_v1",
        "status": "valid" if not errors else "invalid",
        "publication_mode": "research_only",
        "px4_external_vision": "disabled",
        "model_release_id": model_release.release_id,
        "map_release_id": map_release.get("release_id"),
        "calibration_id": calibration_id,
        "temporal_student_artifact": str(artifact_path) if artifact_path else None,
        "export_validation": export_audit,
        "errors": errors,
    }


def evaluate_pi_temporal_student_onnx_parity(
    export_dir: str | Path, *, atol: float = 1e-4, rtol: float = 1e-3,
) -> dict[str, Any]:
    """Compare the standalone ONNX student with its PyTorch reference tensors."""

    if atol < 0.0 or rtol < 0.0:
        raise ValueError("atol and rtol must be non-negative.")
    audit = validate_pi_temporal_student_export(export_dir)
    root = Path(export_dir).expanduser().resolve()
    manifest_path = root / "manifest.json"
    manifest = _read_json(manifest_path, "Pi temporal student export manifest")
    onnx_path = Path(audit["onnx_path"])
    parity = manifest["parity_reference"]
    inputs_path = _resolve_export_member(root, parity["inputs_npz"], "parity input")
    outputs_path = _resolve_export_member(root, parity["outputs_npz"], "parity output")
    inputs = _load_npz(inputs_path, "Pi temporal student parity inputs")
    expected = _load_npz(outputs_path, "Pi temporal student PyTorch outputs")
    if set(inputs) != set(INPUT_NAMES) or set(expected) != set(OUTPUT_NAMES):
        raise ValueError("Pi temporal student parity tensors do not match the export interface.")
    try:
        import onnxruntime as ort
    except ImportError as exc:
        raise RuntimeError("Pi temporal student parity requires onnxruntime.") from exc
    session = ort.InferenceSession(str(onnx_path), providers=["CPUExecutionProvider"])
    actual_values = session.run(list(OUTPUT_NAMES), {name: inputs[name] for name in INPUT_NAMES})
    outputs: dict[str, Any] = {}
    failed: list[str] = []
    for name, actual in zip(OUTPUT_NAMES, actual_values):
        reference = expected[name]
        actual_array = np.asarray(actual)
        same_shape = actual_array.shape == reference.shape
        absolute = np.abs(actual_array.astype(np.float64) - reference.astype(np.float64)) if same_shape else np.asarray([])
        passed = same_shape and bool(np.allclose(actual_array, reference, atol=atol, rtol=rtol))
        outputs[name] = {
            "status": "passed" if passed else "failed", "reference_shape": list(reference.shape),
            "actual_shape": list(actual_array.shape),
            "max_absolute_error": float(absolute.max()) if absolute.size else None,
        }
        if not passed:
            failed.append(name)
    return {
        "schema_version": PI_TEMPORAL_STUDENT_PARITY_SCHEMA,
        "created_utc": _utc_now(),
        "status": "passed" if not failed else "failed",
        "publication_mode": "research_only",
        "export_validation": audit,
        "export_manifest_sha256": sha256_file(manifest_path),
        "thresholds": {"atol": atol, "rtol": rtol},
        "failed_outputs": failed,
        "outputs": outputs,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Export a compact Pi temporal student ONNX artifact and parity fixture.")
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--window-dir", required=True, type=Path)
    parser.add_argument("--target-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--opset-version", type=int, default=17)
    args = parser.parse_args()
    try:
        result = export_pi_temporal_student_onnx(
            args.checkpoint, args.window_dir, args.target_dir, args.output_dir,
            device=args.device, opset_version=args.opset_version,
        )
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result["manifest"], indent=2, sort_keys=True))


def parity_main() -> None:
    parser = argparse.ArgumentParser(description="Validate PyTorch-to-ONNX parity for the compact Pi temporal student.")
    parser.add_argument("--export-dir", required=True, type=Path)
    parser.add_argument("--atol", type=float, default=1e-4)
    parser.add_argument("--rtol", type=float, default=1e-3)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        report = evaluate_pi_temporal_student_onnx_parity(args.export_dir, atol=args.atol, rtol=args.rtol)
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    if report["status"] != "passed":
        raise SystemExit(1)


def validate_main() -> None:
    parser = argparse.ArgumentParser(description="Validate a compact Pi temporal student export without executing ONNX.")
    parser.add_argument("--export-dir", required=True, type=Path)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        report = validate_pi_temporal_student_export(args.export_dir)
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


def binding_main() -> None:
    parser = argparse.ArgumentParser(description="Bind a standalone Pi temporal student export to a shadow-only ModelRelease and MapRelease.")
    parser.add_argument("--export-dir", required=True, type=Path)
    parser.add_argument("--model-release", required=True, type=Path)
    parser.add_argument("--map-release", required=True, type=Path)
    parser.add_argument("--artifact-root", type=Path, help="Defaults to the ModelRelease directory.")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    model_path = args.model_release.expanduser().resolve()
    try:
        map_release = _read_json(args.map_release.expanduser().resolve(), "MapRelease")
        report = validate_pi_temporal_student_release_binding(
            args.export_dir,
            model_release=load_model_release(model_path),
            map_release=map_release,
            artifact_root=args.artifact_root or model_path.parent,
        )
    except ValueError as exc:
        raise SystemExit(str(exc)) from exc
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    if report["status"] != "valid":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
