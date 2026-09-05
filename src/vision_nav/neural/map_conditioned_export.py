"""Hash-bound ONNX export/parity and trace-only candidate-conditioned inference."""

from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
from typing import Any

import numpy as np

from vision_nav.training_capture import sha256_file
from .crossview_dataset import MapConditionedDataset
from .crossview_images import PREPROCESSING_ID
from .map_conditioned_student import (MAP_CONDITIONED_STUDENT_SCHEMA, MapConditionedStudentConfig,
    INPUT_NAMES, OUTPUT_NAMES, build_map_conditioned_student, validate_inputs, summarize_outputs)


EXPORT_SCHEMA = "vision_nav_map_conditioned_export_v1"


def export_map_conditioned_student(checkpoint: str | Path, dataset_dir: str | Path, output_dir: str | Path,
                                    *, atol: float = 1e-4, rtol: float = 1e-3) -> dict[str, Any]:
    import torch
    import onnx
    import onnxruntime as ort
    if not np.isfinite([atol, rtol]).all() or min(atol, rtol) < 0:
        raise ValueError("Export parity tolerances must be finite and nonnegative.")
    checkpoint = Path(checkpoint).expanduser().resolve()
    dataset_root = Path(dataset_dir).expanduser().resolve()
    payload = torch.load(checkpoint, map_location="cpu", weights_only=True)
    if payload.get("schema_version") != MAP_CONDITIONED_STUDENT_SCHEMA:
        raise ValueError("Wrong checkpoint family; absolute-ENU baseline cannot bind to this interface.")
    preprocessing_digest = sha256_file(Path(__file__).with_name("crossview_images.py"))
    if payload.get("source_sha256", {}).get("crossview_images.py") != preprocessing_digest or not payload.get("calibration_sha256"):
        raise ValueError("Checkpoint lacks compatible image preprocessing/calibration provenance; retrain with the current input contract")
    if sha256_file(dataset_root / "manifest.json") != payload["dataset_manifest_sha256"]:
        raise ValueError("Export dataset differs from the checkpoint's training manifest.")
    config = MapConditionedStudentConfig(**payload["config"])
    config.validate()
    model = build_map_conditioned_student(config)
    model.load_state_dict(payload["state_dict"], strict=True)
    model.eval()
    dataset = MapConditionedDataset(dataset_root, split="validation", image_size=config.image_size,
                                    grid_size=config.grid_size, heading_bins=config.heading_bins)
    root = Path(output_dir).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError("Map-conditioned export output must be empty.")
    root.mkdir(parents=True, exist_ok=True)
    first = {name: value[:1].contiguous() for name, value in dataset[0]["inputs"].items()}
    path = root / "map_conditioned_student.onnx"
    # Fixed shapes/batch=1 match the Pi's bounded per-candidate invocation.
    torch.onnx.export(model, tuple(first[n] for n in INPUT_NAMES), path, input_names=list(INPUT_NAMES),
                      output_names=list(OUTPUT_NAMES), opset_version=17, dynamo=False)
    onnx.checker.check_model(str(path))
    session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
    if {value.name for value in session.get_inputs()} != set(INPUT_NAMES):
        raise ValueError("Exporter omitted a required candidate/context input.")
    cases, failures = [], []
    for index in range(len(dataset)):
        item = dataset[index]
        for candidate in range(item["inputs"]["candidate_map"].shape[0]):
            tensors = {name: value[candidate:candidate+1].contiguous() for name, value in item["inputs"].items()}
            inputs = {name: value.numpy() for name, value in tensors.items()}
            validate_inputs(inputs, config)
            with torch.no_grad():
                reference = [value.numpy() for value in model(**tensors)]
            actual = session.run(list(OUTPUT_NAMES), inputs)
            errors = {name: float(np.max(np.abs(ref - out))) for name, ref, out in zip(OUTPUT_NAMES, reference, actual)}
            numeric_parity = all(np.allclose(ref, out, atol=atol, rtol=rtol) for ref, out in zip(reference, actual))
            decision_parity = all(np.array_equal(np.argmax(ref, axis=1), np.argmax(out, axis=1)) for ref, out in zip(reference, actual))
            passed = numeric_parity and decision_parity
            case = {"window_id": item["window_id"], "candidate_index": candidate, "max_absolute_errors": errors,
                    "numeric_parity": numeric_parity, "argmax_parity": decision_parity, "passed": passed}
            cases.append(case)
            if not passed:
                failures.append(case)
    np.savez_compressed(root / "sample_inputs.npz", **{name: value.numpy() for name, value in first.items()})
    manifest = {"schema_version": EXPORT_SCHEMA, "config": asdict(config), "status": "parity_passed" if not failures else "parity_failed",
                "publication_mode": "shadow_only", "px4_external_vision": "disabled", "checkpoint_sha256": sha256_file(checkpoint),
                "dataset_manifest_sha256": payload["dataset_manifest_sha256"],
                "training_map_release_ids": payload["training_map_release_ids"], "calibration_ids": payload["calibration_ids"],
                "teacher_distillation": payload.get("teacher_distillation", "not_performed"),
                "teacher_provenance": payload.get("teacher_provenance"),
                "teacher_loss_weight": payload.get("teacher_loss_weight", 0.0),
                "distillation_loss_sha256": payload.get("distillation_loss_sha256"),
                "calibration_sha256": payload["calibration_sha256"],
                "preprocessing": {"id": PREPROCESSING_ID, "source_sha256": preprocessing_digest},
                "training_timing_policies": payload.get("training_timing_policies", ["legacy_unspecified"]),
                "sensor_delay_calibrated": False, "gps_timing_calibrated": False,
                "onnx": {"path": path.name, "sha256": sha256_file(path)},
                "sample_inputs": {"path": "sample_inputs.npz", "sha256": sha256_file(root / "sample_inputs.npz")},
                "inputs": {name: list(value.shape) for name, value in first.items()}, "outputs": list(OUTPUT_NAMES),
                "input_semantics": {"frame_window": "rectified_rgb_zero_one", "candidate_map": "georeferenced_rgb_zero_one",
                    "roll_pitch": "px4_roll_pitch_rad_no_absolute_yaw", "time_offsets_s": "sensor_times_relative_to_last_frame",
                    "camera_context": "fx_w_fy_h_cx_center_w_cy_center_h_body_R_camera_xyzw_translation_m",
                    "map_context": "east_north_metres_per_normalized_u_then_v"},
                "parity": {"atol": atol, "rtol": rtol, "cases": cases}, "field_ready": False,
                "limitations": ["no_covariance_calibration", "no_pi_thermal_benchmark", "no_independent_geographic_validation",
                                "teacher_point_loss_not_qualified_distillation" if payload.get("teacher_provenance") else "no_teacher_distillation"]}
    (root / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    if failures:
        raise ValueError("ONNX numerical or decision parity failed; inspect export manifest.")
    return manifest


class MapConditionedOnnxProbe:
    """Reusable CPU session for recorded or live shadow candidate tensors."""

    def __init__(self, export_dir: str | Path):
        import onnxruntime as ort
        root = Path(export_dir).expanduser().resolve()
        self.manifest = json.loads((root / "manifest.json").read_text(encoding="utf-8"))
        if (self.manifest.get("schema_version") != EXPORT_SCHEMA or self.manifest.get("status") != "parity_passed"
                or self.manifest.get("publication_mode") != "shadow_only" or self.manifest.get("px4_external_vision") != "disabled"):
            raise ValueError("Only a parity-passed shadow export can be probed.")
        artifact = self.manifest["onnx"]
        path = (root / artifact["path"]).resolve()
        if not path.is_relative_to(root) or sha256_file(path) != artifact["sha256"]:
            raise ValueError("Map-conditioned ONNX path/checksum mismatch.")
        self.config = MapConditionedStudentConfig(**self.manifest["config"])
        self.config.validate()
        self.session = ort.InferenceSession(str(path), providers=["CPUExecutionProvider"])
        if {entry.name for entry in self.session.get_inputs()} != set(INPUT_NAMES):
            raise ValueError("ONNX graph inputs do not match the map-conditioned contract")

    def run(self, inputs: dict[str, np.ndarray], *, tile_id: str, map_release_id: str, calibration_id: str) -> dict[str, Any]:
        if not tile_id or not map_release_id or calibration_id not in self.manifest["calibration_ids"]:
            raise ValueError("Shadow probe requires map/tile identity and a compatible measured camera calibration.")
        validate_inputs(inputs, self.config)
        if inputs["frame_window"].shape[0] != 1:
            raise ValueError("Exported Pi probe uses one candidate per invocation.")
        try:
            output = dict(zip(OUTPUT_NAMES, self.session.run(list(OUTPUT_NAMES), inputs)))
        except Exception as exc:
            raise RuntimeError(f"map_conditioned_inference_failed:{type(exc).__name__}:{exc}") from exc
        return {**summarize_outputs(output, self.config), "tile_id": tile_id, "map_release_id": map_release_id,
                "model_sha256": self.manifest["onnx"]["sha256"], "calibration_id": calibration_id,
                "px4_external_vision": "disabled"}


def main() -> None:
    parser = argparse.ArgumentParser(description="Export and verify the candidate-map-conditioned student on CPU ONNX Runtime.")
    parser.add_argument("--checkpoint", type=Path, required=True)
    parser.add_argument("--dataset-dir", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    args = parser.parse_args()
    print(json.dumps(export_map_conditioned_student(args.checkpoint, args.dataset_dir, args.output_dir), indent=2))


def probe_main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate map-conditioned shadow tensors without publishing flight observations.")
    parser.add_argument("--export-dir", type=Path, required=True)
    parser.add_argument("--inputs", type=Path, required=True)
    parser.add_argument("--tile-id", required=True)
    parser.add_argument("--map-release-id", required=True)
    parser.add_argument("--calibration-id", required=True)
    args = parser.parse_args()
    with np.load(args.inputs, allow_pickle=False) as data:
        inputs = {name: data[name] for name in data.files}
    probe = MapConditionedOnnxProbe(args.export_dir)
    print(json.dumps(probe.run(inputs, tile_id=args.tile_id, map_release_id=args.map_release_id, calibration_id=args.calibration_id), indent=2))


if __name__ == "__main__":
    main()
