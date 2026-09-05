"""Fixed-area absolute-coordinate baseline retained for research comparison.

New map-conditioned student development belongs in map_conditioned_student.py.
This frame/attitude-only model is not the generalizing cross-view localizer.

The student is trained only from an immutable teacher-target package. It is a
research artifact until exported, parity-tested, thermal-benchmarked, and added
to a complete ModelRelease alongside MegaLoc and XFeat/LighterGlue artifacts.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import random
from typing import Any

import numpy as np

from vision_nav.training_capture import sha256_file

from .teacher_targets import TEACHER_TARGETS_SCHEMA, TeacherTargetDataset, validate_teacher_targets
from .temporal_teacher_baseline import choose_torch_device


PI_TEMPORAL_STUDENT_SCHEMA = "vision_nav_pi_temporal_student_v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class PiTemporalStudentConfig:
    image_width: int = 320
    image_height: int = 320
    feature_dim: int = 64
    teacher_feature_dim: int = 128
    recurrent_layers: int = 1
    dropout: float = 0.05
    temporal_feature_weight: float = 1.0
    location_weight: float = 0.3
    heading_weight: float = 0.2

    def validate(self) -> None:
        if self.image_width <= 0 or self.image_height <= 0:
            raise ValueError("Student image dimensions must be positive.")
        if self.feature_dim <= 0 or self.teacher_feature_dim <= 0 or self.recurrent_layers <= 0:
            raise ValueError("Student feature dimensions and recurrent layers must be positive.")
        if not 0.0 <= self.dropout < 1.0:
            raise ValueError("Student dropout must be in [0, 1).")
        if min(self.temporal_feature_weight, self.location_weight, self.heading_weight) < 0.0:
            raise ValueError("Student loss weights cannot be negative.")


def build_student_run_record(
    *, target_dir: str | Path, window_dir: str | Path, config: PiTemporalStudentConfig,
    device: str, seed: int, epochs: int, learning_rate: float,
) -> dict[str, Any]:
    """Create hash-bound provenance before a student trainer opens tensors."""

    config.validate()
    target_audit = validate_teacher_targets(target_dir, window_dir=window_dir)
    actual_feature_dim = target_audit["splits"]["train"]["temporal_feature_dim"]
    if config.teacher_feature_dim != actual_feature_dim:
        raise ValueError(
            f"Student teacher_feature_dim={config.teacher_feature_dim} does not match teacher targets ({actual_feature_dim})."
        )
    map_release_ids = {
        map_release_id
        for split in target_audit["source_validation"]["splits"].values()
        for map_release_id in split["map_release_ids"]
    }
    calibration_ids = {
        calibration_id
        for split in target_audit["source_validation"]["splits"].values()
        for calibration_id in split["calibration_ids"]
    }
    if len(map_release_ids) != 1 or len(calibration_ids) != 1:
        raise ValueError("Pi temporal student requires one map reference and one camera calibration across both splits.")
    target_root = Path(target_dir).expanduser().resolve()
    return {
        "schema_version": PI_TEMPORAL_STUDENT_SCHEMA,
        "created_utc": _utc_now(),
        "status": "initialized",
        "publication_mode": "research_only",
        "px4_external_vision": "disabled",
        "profile_id": "pi5_crossview_nav_v1",
        "student_kind": "compact_gru_temporal_distillation_student",
        "map_release_id": next(iter(map_release_ids)),
        "calibration_id": next(iter(calibration_ids)),
        "input": {
            "teacher_targets_dir": str(target_root),
            "teacher_targets_schema": TEACHER_TARGETS_SCHEMA,
            "teacher_targets_manifest_sha256": target_audit["manifest_sha256"],
            "temporal_windows_manifest_sha256": target_audit["temporal_windows_manifest_sha256"],
            "validation": target_audit,
        },
        "training": {
            "config": asdict(config), "device": device, "seed": seed, "epochs": epochs, "learning_rate": learning_rate,
        },
    }


def _seed_everything(seed: int) -> None:
    import torch

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def build_pi_temporal_student(config: PiTemporalStudentConfig) -> Any:
    """Build a bounded GRU student with only Pi-available input modalities."""

    config.validate()
    try:
        import torch
    except ImportError as exc:
        raise RuntimeError("Pi temporal student training requires drone-vision-nav[research] (PyTorch).") from exc

    class PiTemporalStudent(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.image_encoder = torch.nn.Sequential(
                torch.nn.Conv2d(3, 16, kernel_size=5, stride=2, padding=2),
                torch.nn.GELU(),
                torch.nn.Conv2d(16, 32, kernel_size=3, stride=2, padding=1),
                torch.nn.GELU(),
                torch.nn.Conv2d(32, config.feature_dim, kernel_size=3, stride=2, padding=1),
                torch.nn.GELU(),
                torch.nn.AdaptiveAvgPool2d((1, 1)),
                torch.nn.Flatten(),
            )
            self.attitude_encoder = torch.nn.Sequential(
                torch.nn.Linear(3, config.feature_dim), torch.nn.GELU(),
            )
            self.temporal = torch.nn.GRU(
                input_size=config.feature_dim * 2, hidden_size=config.feature_dim,
                num_layers=config.recurrent_layers, batch_first=True,
                dropout=config.dropout if config.recurrent_layers > 1 else 0.0,
            )
            self.normalizer = torch.nn.LayerNorm(config.feature_dim)
            self.temporal_feature_head = torch.nn.Linear(config.feature_dim, config.teacher_feature_dim)
            self.location_head = torch.nn.Linear(config.feature_dim, 3)
            self.heading_head = torch.nn.Linear(config.feature_dim, 2)

        def forward(self, frame_window: Any, attitude_window: Any) -> tuple[Any, Any, Any]:
            # [B,T,C,H,W] and [B,T,3] directly match the Pi temporal ONNX contract.
            batch, frame_count = frame_window.shape[:2]
            encoded = self.image_encoder(frame_window.reshape(batch * frame_count, *frame_window.shape[2:]))
            encoded = encoded.reshape(batch, frame_count, -1)
            sequence, _ = self.temporal(torch.cat((encoded, self.attitude_encoder(attitude_window)), dim=-1))
            latent = self.normalizer(sequence[:, -1])
            return self.location_head(latent), self.heading_head(latent), self.temporal_feature_head(latent)

    return PiTemporalStudent()


def _student_batch(item: dict[str, Any], device: str) -> dict[str, Any]:
    return {
        "frame_window": item["frame_rgb"].unsqueeze(0).to(device),
        "attitude_window": item["attitude_rpy_rad"].unsqueeze(0).to(device),
        "teacher_temporal_feature": item["teacher_temporal_feature"].unsqueeze(0).to(device),
        "teacher_position_enu_m": item["teacher_position_enu_m"].unsqueeze(0).to(device),
        "teacher_heading_sin_cos": item["teacher_heading_sin_cos"].unsqueeze(0).to(device),
    }


def _student_losses(torch: Any, outputs: tuple[Any, Any, Any], batch: dict[str, Any], config: PiTemporalStudentConfig) -> dict[str, Any]:
    location, heading, temporal_feature = outputs
    temporal = torch.nn.functional.mse_loss(temporal_feature, batch["teacher_temporal_feature"])
    location_loss = torch.nn.functional.smooth_l1_loss(location, batch["teacher_position_enu_m"])
    heading_loss = torch.nn.functional.mse_loss(heading, batch["teacher_heading_sin_cos"])
    total = (
        config.temporal_feature_weight * temporal
        + config.location_weight * location_loss
        + config.heading_weight * heading_loss
    )
    return {"total": total, "temporal": temporal, "location": location_loss, "heading": heading_loss}


def _epoch(
    *, model: Any, dataset: Any, optimizer: Any | None, device: str,
    config: PiTemporalStudentConfig, max_batches: int | None,
) -> dict[str, float]:
    import torch

    training = optimizer is not None
    model.train(training)
    totals = {"total": 0.0, "temporal": 0.0, "location": 0.0, "heading": 0.0}
    position_error_m = 0.0
    heading_error_rad = 0.0
    count = 0
    context = torch.enable_grad() if training else torch.no_grad()
    with context:
        for index in range(len(dataset)):
            if max_batches is not None and count >= max_batches:
                break
            batch = _student_batch(dataset[index], device)
            if training:
                optimizer.zero_grad(set_to_none=True)
            outputs = model(batch["frame_window"], batch["attitude_window"])
            losses = _student_losses(torch, outputs, batch, config)
            if training:
                losses["total"].backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=5.0)
                optimizer.step()
            for name in totals:
                totals[name] += float(losses[name].detach().cpu())
            location, heading, _ = outputs
            position_error_m += float(
                torch.linalg.vector_norm(location - batch["teacher_position_enu_m"], dim=1).mean().detach().cpu()
            )
            predicted_heading = torch.atan2(heading[:, 0], heading[:, 1])
            teacher_heading = torch.atan2(batch["teacher_heading_sin_cos"][:, 0], batch["teacher_heading_sin_cos"][:, 1])
            delta = torch.atan2(torch.sin(predicted_heading - teacher_heading), torch.cos(predicted_heading - teacher_heading))
            heading_error_rad += float(torch.abs(delta).mean().detach().cpu())
            count += 1
    if count == 0:
        raise ValueError("Pi temporal student epoch has no usable windows.")
    return {
        **{f"{name}_loss": round(value / count, 8) for name, value in totals.items()},
        "teacher_position_error_m": round(position_error_m / count, 8),
        "teacher_heading_error_rad": round(heading_error_rad / count, 8),
        "batches": float(count),
    }


def train_pi_temporal_student(
    window_dir: str | Path, target_dir: str | Path, output_dir: str | Path, *,
    config: PiTemporalStudentConfig | None = None, device: str = "auto", epochs: int = 1,
    learning_rate: float = 1e-4, seed: int = 7, max_train_batches: int | None = None,
    max_validation_batches: int | None = None,
) -> dict[str, Any]:
    """Train a compact student checkpoint from immutable teacher-target data."""

    if epochs <= 0 or learning_rate <= 0.0:
        raise ValueError("epochs and learning_rate must be positive.")
    if max_train_batches is not None and max_train_batches <= 0 or max_validation_batches is not None and max_validation_batches <= 0:
        raise ValueError("Optional maximum batch counts must be positive.")
    active_config = config or PiTemporalStudentConfig()
    resolved_device = choose_torch_device(device)
    run_record = build_student_run_record(
        target_dir=target_dir, window_dir=window_dir, config=active_config, device=resolved_device,
        seed=seed, epochs=epochs, learning_rate=learning_rate,
    )
    root = Path(output_dir).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError(f"Pi temporal student output directory must be empty: {root}")
    try:
        import torch
    except ImportError as exc:
        raise RuntimeError("Pi temporal student training requires drone-vision-nav[research] (PyTorch).") from exc
    root.mkdir(parents=True, exist_ok=True)
    _seed_everything(seed)
    train_dataset = TeacherTargetDataset(window_dir, target_dir, split="train", image_size=(active_config.image_width, active_config.image_height))
    validation_dataset = TeacherTargetDataset(window_dir, target_dir, split="validation", image_size=(active_config.image_width, active_config.image_height))
    model = build_pi_temporal_student(active_config).to(resolved_device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate, weight_decay=1e-4)
    history: list[dict[str, Any]] = []
    for epoch in range(1, epochs + 1):
        history.append({
            "epoch": epoch,
            "train": _epoch(model=model, dataset=train_dataset, optimizer=optimizer, device=resolved_device, config=active_config, max_batches=max_train_batches),
            "validation": _epoch(model=model, dataset=validation_dataset, optimizer=None, device=resolved_device, config=active_config, max_batches=max_validation_batches),
        })
    checkpoint_path = root / "pi_temporal_student.pt"
    torch.save({
        "schema_version": PI_TEMPORAL_STUDENT_SCHEMA,
        "state_dict": model.state_dict(),
        "config": asdict(active_config),
        "teacher_targets_manifest_sha256": run_record["input"]["teacher_targets_manifest_sha256"],
        "temporal_windows_manifest_sha256": run_record["input"]["temporal_windows_manifest_sha256"],
        "map_release_id": run_record["map_release_id"],
        "calibration_id": run_record["calibration_id"],
        "history": history,
    }, checkpoint_path)
    run_record.update({
        "status": "completed", "completed_utc": _utc_now(), "torch_version": str(torch.__version__),
        "checkpoint": {"path": checkpoint_path.name, "sha256": sha256_file(checkpoint_path)},
        "history": history,
        "next_action": "Export this research-only checkpoint to ONNX, create parity fixtures, and pass Pi shadow benchmarks before any ModelRelease review.",
    })
    run_path = root / "training_run.json"
    run_path.write_text(json.dumps(run_record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"output_dir": str(root), "checkpoint_path": str(checkpoint_path), "run_record_path": str(run_path), "run_record": run_record}


def main() -> None:
    parser = argparse.ArgumentParser(description="Train a compact Pi temporal student from validated teacher targets.")
    parser.add_argument("--window-dir", required=True, type=Path)
    parser.add_argument("--target-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--learning-rate", type=float, default=1e-4)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--image-width", type=int, default=320)
    parser.add_argument("--image-height", type=int, default=320)
    parser.add_argument("--feature-dim", type=int, default=64)
    parser.add_argument("--teacher-feature-dim", type=int, default=128)
    parser.add_argument("--recurrent-layers", type=int, default=1)
    parser.add_argument("--max-train-batches", type=int)
    parser.add_argument("--max-validation-batches", type=int)
    args = parser.parse_args()
    config = PiTemporalStudentConfig(
        image_width=args.image_width, image_height=args.image_height, feature_dim=args.feature_dim,
        teacher_feature_dim=args.teacher_feature_dim, recurrent_layers=args.recurrent_layers,
    )
    try:
        result = train_pi_temporal_student(
            args.window_dir, args.target_dir, args.output_dir, config=config, device=args.device,
            epochs=args.epochs, learning_rate=args.learning_rate, seed=args.seed,
            max_train_batches=args.max_train_batches, max_validation_batches=args.max_validation_batches,
        )
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result["run_record"], indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
