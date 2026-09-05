"""Compact spatial cross-attention student, trained and exported for shadow use.

This project-owned trainable component is not a reproduction of Bearing-UAV or
Video2BEV and is not a distilled release until teacher targets are evaluated.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import json
from pathlib import Path
import random
from typing import Any

import numpy as np

from vision_nav.training_capture import sha256_file
from .crossview_dataset import MapConditionedDataset, load_crossview_dataset
from .temporal_teacher_baseline import choose_torch_device


MAP_CONDITIONED_STUDENT_SCHEMA = "vision_nav_map_conditioned_student_v1"
INPUT_NAMES = ("frame_window", "candidate_map", "roll_pitch", "time_offsets_s", "camera_context", "map_context")
OUTPUT_NAMES = ("location_logits", "heading_logits")


@dataclass(frozen=True)
class MapConditionedStudentConfig:
    image_size: int = 256
    grid_size: int = 8
    feature_dim: int = 64
    window_size: int = 4
    heading_bins: int = 36
    heading_loss_weight: float = 0.2

    def validate(self) -> None:
        dimensions = (self.image_size, self.grid_size, self.feature_dim, self.window_size, self.heading_bins)
        if any(isinstance(v, bool) or not isinstance(v, int) or v <= 0 for v in dimensions):
            raise ValueError("Student dimensions must be positive integers.")
        if self.image_size % (8 * self.grid_size) or self.feature_dim % 8 or self.window_size < 2 or self.heading_bins < 4:
            raise ValueError("Use image_size divisible by 8*grid_size, feature_dim divisible by 8, and at least two frames.")
        if not np.isfinite(self.heading_loss_weight) or self.heading_loss_weight < 0:
            raise ValueError("Heading loss weight must be finite and nonnegative.")


def build_map_conditioned_student(config: MapConditionedStudentConfig) -> Any:
    import torch
    config.validate()

    class MapConditionedStudent(torch.nn.Module):
        def __init__(self):
            super().__init__()
            dim = config.feature_dim
            def encoder():
                layers = []
                for incoming, outgoing in ((3, 16), (16, 32), (32, dim)):
                    layers.extend([torch.nn.Conv2d(incoming, outgoing, 3, stride=2, padding=1),
                                   torch.nn.GroupNorm(8, outgoing), torch.nn.GELU()])
                layers.append(torch.nn.AdaptiveAvgPool2d((config.grid_size, config.grid_size)))
                return torch.nn.Sequential(*layers)
            self.camera_encoder = encoder()
            self.map_encoder = encoder()
            self.context = torch.nn.Sequential(torch.nn.Linear(18, dim), torch.nn.GELU())
            self.temporal_score = torch.nn.Linear(dim, 1)
            self.query = torch.nn.Linear(dim, dim, bias=False)
            self.key = torch.nn.Linear(dim, dim, bias=False)
            self.value = torch.nn.Linear(dim, dim, bias=False)
            self.location = torch.nn.Sequential(torch.nn.Linear(4 * dim + 2, dim), torch.nn.GELU(), torch.nn.Linear(dim, 1))
            self.no_match = torch.nn.Sequential(torch.nn.Linear(dim * 2, dim), torch.nn.GELU(), torch.nn.Linear(dim, 1))
            self.heading = torch.nn.Sequential(torch.nn.Linear(dim * 2, dim), torch.nn.GELU(), torch.nn.Linear(dim, config.heading_bins))
            coords = (torch.arange(config.grid_size, dtype=torch.float32) + 0.5) / config.grid_size
            y, x = torch.meshgrid(coords, coords, indexing="ij")
            self.register_buffer("map_coordinates", torch.stack((x, y), dim=-1).reshape(1, -1, 2))

        def forward(self, frame_window, candidate_map, roll_pitch, time_offsets_s, camera_context, map_context):
            batch = frame_window.shape[0]
            tokens = self.camera_encoder(frame_window.reshape(-1, 3, config.image_size, config.image_size))
            tokens = tokens.flatten(2).transpose(1, 2).reshape(batch, config.window_size, config.grid_size ** 2, config.feature_dim)
            context = self.context(torch.cat((roll_pitch, time_offsets_s.unsqueeze(-1),
                                             camera_context.unsqueeze(1).expand(-1, config.window_size, -1),
                                             (map_context / 100).unsqueeze(1).expand(-1, config.window_size, -1)), dim=-1))
            temporal_features = tokens.mean(dim=2) + context
            temporal_weights = torch.softmax(self.temporal_score(temporal_features), dim=1)
            temporal = (temporal_weights * temporal_features).sum(dim=1)
            map_tokens = self.map_encoder(candidate_map).flatten(2).transpose(1, 2)
            query_tokens = tokens[:, -1] + temporal.unsqueeze(1)
            correspondence = torch.softmax(torch.matmul(self.query(map_tokens), self.key(query_tokens).transpose(1, 2)) / config.feature_dim ** 0.5, dim=-1)
            matched_query = torch.matmul(correspondence, self.value(query_tokens))
            evidence = torch.cat((map_tokens, matched_query, map_tokens * matched_query,
                                  temporal.unsqueeze(1).expand(-1, config.grid_size ** 2, -1),
                                  self.map_coordinates.expand(batch, -1, -1)), dim=-1)
            spatial_logits = self.location(evidence).squeeze(-1)
            pooled = torch.cat((map_tokens.mean(dim=1), temporal), dim=-1)
            # Last location class is explicitly 'no match', not a map cell.
            return torch.cat((spatial_logits, self.no_match(pooled)), dim=1), self.heading(pooled)

    return MapConditionedStudent()


def validate_inputs(inputs: dict[str, np.ndarray], config: MapConditionedStudentConfig) -> None:
    config.validate()
    if set(inputs) != set(INPUT_NAMES):
        raise ValueError("Map-conditioned input names differ from the trained interface.")
    batch = inputs["frame_window"].shape[0] if inputs["frame_window"].ndim == 5 else 0
    expected = {"frame_window": (batch, config.window_size, 3, config.image_size, config.image_size),
                "candidate_map": (batch, 3, config.image_size, config.image_size),
                "roll_pitch": (batch, config.window_size, 2), "time_offsets_s": (batch, config.window_size),
                "camera_context": (batch, 11), "map_context": (batch, 4)}
    if batch <= 0:
        raise ValueError("Empty or invalid frame batch.")
    for name, shape in expected.items():
        value = np.asarray(inputs[name])
        if value.shape != shape or value.dtype != np.float32 or not np.isfinite(value).all():
            raise ValueError(f"Invalid shape/dtype/values for {name}.")
    for name in ("frame_window", "candidate_map"):
        if inputs[name].min() < 0 or inputs[name].max() > 1:
            raise ValueError("Model imagery must be RGB float32 in [0,1].")
    times = inputs["time_offsets_s"]
    if not np.all(np.diff(times, axis=1) > 0) or not np.allclose(times[:, -1], 0, atol=1e-6):
        raise ValueError("Temporal offsets must strictly increase to the last frame at zero.")
    camera = inputs["camera_context"]
    if np.any(camera[:, :2] <= 0) or not np.allclose(np.linalg.norm(camera[:, 4:8], axis=1), 1, atol=1e-3):
        raise ValueError("Camera context requires valid intrinsics and a unit extrinsic quaternion.")
    if np.any(np.abs(np.linalg.det(inputs["map_context"].reshape(batch, 2, 2))) < 1e-6):
        raise ValueError("Map context contains degenerate metric axes.")


def summarize_outputs(outputs: dict[str, np.ndarray], config: MapConditionedStudentConfig) -> dict[str, Any]:
    """Expose uncalibrated learned distributions, never a NavEstimate."""
    location = np.asarray(outputs["location_logits"], dtype=np.float64)
    heading = np.asarray(outputs["heading_logits"], dtype=np.float64)
    if location.shape != (1, config.grid_size ** 2 + 1) or heading.shape != (1, config.heading_bins):
        raise ValueError("Shadow cross-view output shapes do not match the exported interface.")
    if not np.isfinite(location).all() or not np.isfinite(heading).all():
        raise ValueError("Nonfinite map-conditioned outputs.")
    def softmax(value):
        result = np.exp(value - value.max())
        return result / result.sum()
    probabilities = softmax(location[0])
    heading_probabilities = softmax(heading[0])
    cell = int(np.argmax(probabilities))
    no_match = cell == config.grid_size ** 2
    return {"status": "no_match_evidence" if no_match else "location_evidence",
            "position_authority": False, "covariance": None, "calibrated": False,
            "no_match_probability": float(probabilities[-1]),
            "location_probability_grid": probabilities[:-1].reshape(config.grid_size, config.grid_size).tolist(),
            "most_likely_uv": None if no_match else [(cell % config.grid_size + 0.5) / config.grid_size, (cell // config.grid_size + 0.5) / config.grid_size],
            "heading_probability_bins": heading_probabilities.tolist(),
            "heading_bin_convention": "radians_clockwise_from_north_bins_0_to_2pi",
            "reason": "uncalibrated_candidate_conditioned_shadow_evidence"}


def _epoch(model: Any, dataset: MapConditionedDataset, *, device: str, optimizer: Any | None, config: MapConditionedStudentConfig,
           teacher_targets: dict | None = None, teacher_weight: float = 0.0) -> dict[str, Any]:
    import torch
    training = optimizer is not None
    model.train(training)
    losses, positives, correct_cells, negatives, false_matches = [], 0, 0, 0, 0
    teacher_losses, distilled_points = [], 0
    order = list(range(len(dataset)))
    if training:
        random.shuffle(order)
    with torch.set_grad_enabled(training):
        for index in order:
            batch = dataset[index]
            inputs = {k: v.to(device) for k, v in batch["inputs"].items()}
            validate_inputs({k: v.detach().cpu().numpy() for k, v in inputs.items()}, config)
            location, heading = model(**inputs)
            target, heading_target = batch["location_target"].to(device), batch["heading_target"].to(device)
            loss = torch.nn.functional.cross_entropy(location, target)
            headed = heading_target >= 0
            if bool(headed.any()):
                loss = loss + config.heading_loss_weight * torch.nn.functional.cross_entropy(heading[headed], heading_target[headed])
            if training and teacher_targets is not None:
                from .bearinguav_targets import point_loss
                points = teacher_targets.get(batch["window_id"], {})
                if any(int(target[i]) == config.grid_size ** 2 for i in points):
                    raise ValueError("Negative candidates must not receive teacher point loss")
                if points:
                    auxiliary = point_loss(location, points, config.grid_size)
                    loss = loss + teacher_weight * auxiliary
                    teacher_losses.append(float(auxiliary.detach()))
                    distilled_points += len(points)
            if not bool(torch.isfinite(loss)):
                raise ValueError("Training produced nonfinite loss.")
            if training:
                optimizer.zero_grad(set_to_none=True)
                loss.backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), 5, error_if_nonfinite=True)
                optimizer.step()
            predicted = location.argmax(dim=1)
            is_positive = target < config.grid_size ** 2
            positives += int(is_positive.sum())
            negatives += int((~is_positive).sum())
            correct_cells += int(((predicted == target) & is_positive).sum())
            false_matches += int(((predicted != config.grid_size ** 2) & ~is_positive).sum())
            losses.append(float(loss.detach()))
    return {"mean_loss": float(np.mean(losses)), "positive_count": positives, "negative_count": negatives,
            "positive_cell_accuracy": correct_cells / positives if positives else None,
            "negative_false_match_fraction": false_matches / negatives if negatives else None,
            "teacher_point_count": distilled_points,
            "teacher_point_mean_loss": float(np.mean(teacher_losses)) if teacher_losses else None,
            "metric_scope": "uncalibrated_tile_classification_not_metric_pose_accuracy"}


def train_map_conditioned_student(dataset_dir: str | Path, output_dir: str | Path, *, config: MapConditionedStudentConfig,
                                   epochs: int = 1, learning_rate: float = 1e-4, device: str = "cpu", seed: int = 7,
                                   teacher_targets_path: str | Path | None = None, teacher_weight: float = 0.0) -> dict[str, Any]:
    import torch
    config.validate()
    if epochs <= 0 or not np.isfinite(learning_rate) or learning_rate <= 0:
        raise ValueError("Positive epochs and finite learning rate required.")
    if (isinstance(teacher_weight, bool) or not isinstance(teacher_weight, (int, float))
            or not np.isfinite(teacher_weight) or not 0 <= teacher_weight <= 1
            or (teacher_targets_path is None) != (teacher_weight == 0)):
        raise ValueError("Teacher targets require an explicit weight in (0,1]; otherwise use zero")
    device = choose_torch_device(device)
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    root = Path(output_dir).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError("Student training output must be empty.")
    dataset_root = Path(dataset_dir).expanduser().resolve()
    dataset_manifest, records = load_crossview_dataset(dataset_root)
    teacher_provenance, teacher_targets = None, None
    if teacher_targets_path is not None:
        from .bearinguav_targets import load_targets
        teacher_provenance, teacher_targets = load_targets(Path(teacher_targets_path), dataset_root)
    if any(len(r["frames"]) != config.window_size for rows in records.values() for r in rows):
        raise ValueError("All temporal windows must match the configured window size.")
    datasets = {split: MapConditionedDataset(dataset_root, split=split, image_size=config.image_size,
                                           grid_size=config.grid_size, heading_bins=config.heading_bins) for split in records}
    calibration_sha256 = {}
    for rows in records.values():
        for row in rows:
            digest = sha256_file(dataset_root / row["calibration"])
            previous = calibration_sha256.setdefault(row["calibration_id"], digest)
            if previous != digest:
                raise ValueError("One calibration ID names different calibration files")
    model = build_map_conditioned_student(config).to(device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate, weight_decay=1e-4)
    history = []
    root.mkdir(parents=True, exist_ok=True)
    for epoch in range(epochs):
        epoch_report = {"epoch": epoch + 1}
        for split, data in datasets.items():
            epoch_report[split] = _epoch(model, data, device=device, config=config,
                optimizer=optimizer if split == "train" else None,
                teacher_targets=teacher_targets if split == "train" else None,
                teacher_weight=teacher_weight if split == "train" else 0.0)
        history.append(epoch_report)
    checkpoint = root / "map_conditioned_student.pt"
    provenance = {"schema_version": MAP_CONDITIONED_STUDENT_SCHEMA, "config": asdict(config),
                  "source_sha256": {path.name: sha256_file(path) for path in
                      (Path(__file__), Path(__file__).with_name("crossview_dataset.py"), Path(__file__).with_name("map_pixel_geometry.py"),
                       Path(__file__).with_name("crossview_images.py"))},
                  "dataset_manifest_sha256": sha256_file(dataset_root / "manifest.json"),
                  "calibration_ids": sorted({r["calibration_id"] for rows in records.values() for r in rows}),
                  "calibration_sha256": calibration_sha256,
                  "training_map_release_ids": sorted({r["map_release_id"] for r in records["train"]}),
                  "validation_map_release_ids": sorted({r["map_release_id"] for r in records["validation"]}),
                  "training_kind": "supervised_with_teacher_point_loss" if teacher_provenance else "supervised_map_conditioned_research_student",
                  "teacher_distillation": "bearinguav_point_auxiliary" if teacher_provenance else "not_performed",
                  "teacher_provenance": teacher_provenance, "teacher_loss_weight": teacher_weight,
                  "distillation_loss_sha256": sha256_file(Path(__file__).with_name("bearinguav_targets.py")) if teacher_provenance else None,
                  "geographic_holdout_verified": dataset_manifest["geographic_holdout_verified"],
                  "training_timing_policies": dataset_manifest["timing_policies"],
                  "sensor_delay_calibrated": False, "gps_timing_calibrated": False,
                  "px4_external_vision": "disabled", "history": history}
    torch.save({**provenance, "state_dict": model.state_dict()}, checkpoint)
    report = {**provenance, "checkpoint_sha256": sha256_file(checkpoint), "device": device,
              "seed": seed, "learning_rate": learning_rate, "torch_version": str(torch.__version__),
              "parameter_count": sum(p.numel() for p in model.parameters()), "status": "research_training_completed"}
    (root / "training_run.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Train the map-conditioned spatial/temporal research student.")
    parser.add_argument("--dataset-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--image-size", type=int, default=256)
    parser.add_argument("--grid-size", type=int, default=8)
    parser.add_argument("--feature-dim", type=int, default=64)
    parser.add_argument("--window-size", type=int, default=4)
    parser.add_argument("--heading-bins", type=int, default=36)
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--learning-rate", type=float, default=1e-4)
    parser.add_argument("--device", choices=("cpu", "cuda", "auto"), default="auto")
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--teacher-targets", type=Path)
    parser.add_argument("--teacher-weight", type=float, default=0.0)
    args = parser.parse_args()
    config = MapConditionedStudentConfig(args.image_size, args.grid_size, args.feature_dim, args.window_size, args.heading_bins)
    print(json.dumps(train_map_conditioned_student(args.dataset_dir, args.output_dir, config=config,
        epochs=args.epochs, learning_rate=args.learning_rate, device=args.device, seed=args.seed,
        teacher_targets_path=args.teacher_targets, teacher_weight=args.teacher_weight), indent=2))


if __name__ == "__main__":
    main()
