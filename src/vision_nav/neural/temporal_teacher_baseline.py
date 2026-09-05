"""Runnable learned temporal cross-view baseline for verified field windows.

This is deliberately a project-owned integration baseline, not a claim to
reproduce Video2BEV, Bearing-UAV, or any other external paper. It proves that
the capture contract can train a temporal image-plus-attitude model end to end,
and produces a fully auditable teacher checkpoint for later distillation work.
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

from .temporal_training_windows import (
    TEMPORAL_TRAINING_WINDOWS_SCHEMA,
    TemporalCrossViewTeacherDataset,
    validate_temporal_training_windows,
)


TEMPORAL_TEACHER_BASELINE_SCHEMA = "vision_nav_temporal_teacher_baseline_v1"
TEMPORAL_TEACHER_EVALUATION_SCHEMA = "vision_nav_temporal_teacher_evaluation_v1"


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")


@dataclass(frozen=True)
class TemporalTeacherBaselineConfig:
    image_width: int = 320
    image_height: int = 320
    feature_dim: int = 128
    temporal_layers: int = 2
    temporal_heads: int = 4
    dropout: float = 0.1
    contrastive_weight: float = 1.0
    position_weight: float = 0.2
    heading_weight: float = 0.1

    def validate(self) -> None:
        if self.image_width <= 0 or self.image_height <= 0:
            raise ValueError("Teacher image dimensions must be positive.")
        if self.feature_dim <= 0 or self.temporal_layers <= 0 or self.temporal_heads <= 0:
            raise ValueError("Teacher feature dimension, layers, and heads must be positive.")
        if self.feature_dim % self.temporal_heads != 0:
            raise ValueError("Teacher feature_dim must be divisible by temporal_heads.")
        if not 0.0 <= self.dropout < 1.0:
            raise ValueError("Teacher dropout must be in [0, 1).")
        if min(self.contrastive_weight, self.position_weight, self.heading_weight) < 0.0:
            raise ValueError("Teacher loss weights cannot be negative.")


def choose_torch_device(requested: str) -> str:
    """Resolve a training device without silently choosing a nonexistent GPU."""

    try:
        import torch
    except ImportError as exc:
        raise RuntimeError("Temporal teacher training requires drone-vision-nav[research] (PyTorch).") from exc
    if requested == "auto":
        return "cuda" if torch.cuda.is_available() else "cpu"
    if requested == "cuda" and not torch.cuda.is_available():
        raise ValueError("CUDA was requested but PyTorch cannot see a CUDA device.")
    if requested not in {"cpu", "cuda"}:
        raise ValueError("Teacher device must be auto, cpu, or cuda.")
    return requested


def build_run_record(
    *,
    window_dir: str | Path,
    config: TemporalTeacherBaselineConfig,
    device: str,
    seed: int,
    epochs: int,
    batch_size: int,
    learning_rate: float,
) -> dict[str, Any]:
    """Create immutable pre-training provenance before PyTorch opens a batch."""

    config.validate()
    audit = validate_temporal_training_windows(window_dir, require_pose_context=True)
    root = Path(window_dir).expanduser().resolve()
    manifest_path = root / "manifest.json"
    return {
        "schema_version": TEMPORAL_TEACHER_BASELINE_SCHEMA,
        "created_utc": _utc_now(),
        "status": "initialized",
        "publication_mode": "research_only",
        "px4_external_vision": "disabled",
        "teacher_kind": "project_owned_temporal_crossview_baseline",
        "not_a_paper_reproduction": True,
        "input": {
            "temporal_windows_dir": str(root),
            "temporal_windows_schema": TEMPORAL_TRAINING_WINDOWS_SCHEMA,
            "temporal_windows_manifest_sha256": sha256_file(manifest_path),
            "validation": audit,
        },
        "training": {
            "config": asdict(config),
            "device": device,
            "seed": seed,
            "epochs": epochs,
            "batch_size": batch_size,
            "learning_rate": learning_rate,
        },
    }


def build_evaluation_record(
    *, checkpoint_path: str | Path, window_dir: str | Path, device: str,
) -> dict[str, Any]:
    """Create evaluation provenance before loading a PyTorch checkpoint."""

    checkpoint = Path(checkpoint_path).expanduser().resolve()
    if not checkpoint.is_file():
        raise ValueError(f"Temporal teacher checkpoint does not exist: {checkpoint}")
    audit = validate_temporal_training_windows(window_dir, require_pose_context=True)
    root = Path(window_dir).expanduser().resolve()
    manifest_path = root / "manifest.json"
    return {
        "schema_version": TEMPORAL_TEACHER_EVALUATION_SCHEMA,
        "created_utc": _utc_now(),
        "status": "initialized",
        "publication_mode": "research_only",
        "px4_external_vision": "disabled",
        "checkpoint": {"path": str(checkpoint), "sha256": sha256_file(checkpoint)},
        "input": {
            "temporal_windows_dir": str(root),
            "temporal_windows_schema": TEMPORAL_TRAINING_WINDOWS_SCHEMA,
            "temporal_windows_manifest_sha256": sha256_file(manifest_path),
            "validation": audit,
        },
        "device": device,
    }


def _seed_everything(seed: int) -> None:
    import torch

    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    if torch.cuda.is_available():
        torch.cuda.manual_seed_all(seed)


def _image_encoder(torch: Any, feature_dim: int) -> Any:
    return torch.nn.Sequential(
        torch.nn.Conv2d(3, 32, kernel_size=5, stride=2, padding=2),
        torch.nn.GELU(),
        torch.nn.Conv2d(32, 64, kernel_size=3, stride=2, padding=1),
        torch.nn.GELU(),
        torch.nn.Conv2d(64, feature_dim, kernel_size=3, stride=2, padding=1),
        torch.nn.GELU(),
        torch.nn.AdaptiveAvgPool2d((1, 1)),
        torch.nn.Flatten(),
        torch.nn.LayerNorm(feature_dim),
    )


def build_temporal_teacher_model(config: TemporalTeacherBaselineConfig) -> Any:
    """Build the compact temporal image/attitude baseline lazily with PyTorch."""

    config.validate()
    try:
        import torch
    except ImportError as exc:
        raise RuntimeError("Temporal teacher training requires drone-vision-nav[research] (PyTorch).") from exc

    class TemporalCrossViewBaseline(torch.nn.Module):
        def __init__(self) -> None:
            super().__init__()
            self.image_encoder = _image_encoder(torch, config.feature_dim)
            self.attitude_encoder = torch.nn.Sequential(
                torch.nn.Linear(3, config.feature_dim), torch.nn.GELU(), torch.nn.LayerNorm(config.feature_dim),
            )
            encoder_layer = torch.nn.TransformerEncoderLayer(
                d_model=config.feature_dim, nhead=config.temporal_heads,
                dim_feedforward=config.feature_dim * 4, dropout=config.dropout,
                batch_first=True, activation="gelu", norm_first=True,
            )
            self.temporal_encoder = torch.nn.TransformerEncoder(encoder_layer, num_layers=config.temporal_layers)
            self.map_encoder = _image_encoder(torch, config.feature_dim)
            self.position_head = torch.nn.Sequential(
                torch.nn.Linear(config.feature_dim, config.feature_dim), torch.nn.GELU(), torch.nn.Linear(config.feature_dim, 3),
            )
            self.heading_head = torch.nn.Sequential(
                torch.nn.Linear(config.feature_dim, config.feature_dim), torch.nn.GELU(), torch.nn.Linear(config.feature_dim, 2),
            )

        def forward(self, frame_rgb: Any, attitude_rpy_rad: Any, map_crops_rgb: Any) -> dict[str, Any]:
            # frame_rgb: [B,T,C,H,W]; map_crops_rgb: [B,K,C,H,W], positive first.
            batch, frame_count = frame_rgb.shape[:2]
            frame_features = self.image_encoder(frame_rgb.reshape(batch * frame_count, *frame_rgb.shape[2:]))
            frame_features = frame_features.reshape(batch, frame_count, -1)
            temporal = self.temporal_encoder(frame_features + self.attitude_encoder(attitude_rpy_rad))
            query = torch.nn.functional.normalize(temporal[:, -1], dim=-1)
            crop_features = self.map_encoder(map_crops_rgb.reshape(batch * map_crops_rgb.shape[1], *map_crops_rgb.shape[2:]))
            crop_features = torch.nn.functional.normalize(crop_features.reshape(batch, map_crops_rgb.shape[1], -1), dim=-1)
            return {
                "retrieval_logits": torch.einsum("bd,bkd->bk", query, crop_features),
                "position_enu_m": self.position_head(query),
                "heading_sin_cos": self.heading_head(query),
                "temporal_feature": query,
            }

    return TemporalCrossViewBaseline()


def _single_batch(item: dict[str, Any], device: str) -> dict[str, Any]:
    import torch

    negatives = item["negative_map_rgb"]
    map_crops = torch.cat((item["positive_map_rgb"].unsqueeze(0), negatives), dim=0).unsqueeze(0).to(device)
    return {
        "frame_rgb": item["frame_rgb"].unsqueeze(0).to(device),
        "attitude_rpy_rad": item["attitude_rpy_rad"].unsqueeze(0).to(device),
        "map_crops_rgb": map_crops,
        "position_enu_m": item["position_enu_m"].unsqueeze(0).to(device),
        "heading_rad": item["heading_rad"],
        "heading_valid": bool(item["heading_valid"]),
    }


def _losses(torch: Any, output: dict[str, Any], batch: dict[str, Any], config: TemporalTeacherBaselineConfig) -> dict[str, Any]:
    targets = torch.zeros(output["retrieval_logits"].shape[0], dtype=torch.long, device=output["retrieval_logits"].device)
    contrastive = torch.nn.functional.cross_entropy(output["retrieval_logits"], targets)
    position = torch.nn.functional.smooth_l1_loss(output["position_enu_m"], batch["position_enu_m"])
    heading = torch.zeros((), device=output["retrieval_logits"].device)
    if batch["heading_valid"]:
        target = torch.tensor(
            [[float(np.sin(batch["heading_rad"])), float(np.cos(batch["heading_rad"]))]],
            dtype=torch.float32, device=output["retrieval_logits"].device,
        )
        heading = torch.nn.functional.mse_loss(output["heading_sin_cos"], target)
    total = config.contrastive_weight * contrastive + config.position_weight * position + config.heading_weight * heading
    return {"total": total, "contrastive": contrastive, "position": position, "heading": heading}


def _epoch(
    *, model: Any, dataset: Any, optimizer: Any | None, device: str, config: TemporalTeacherBaselineConfig, max_batches: int | None,
) -> dict[str, float]:
    import torch

    training = optimizer is not None
    model.train(training)
    totals = {"total": 0.0, "contrastive": 0.0, "position": 0.0, "heading": 0.0}
    retrieval_correct = 0
    position_error_m = 0.0
    heading_error_rad = 0.0
    heading_count = 0
    count = 0
    context = torch.enable_grad() if training else torch.no_grad()
    with context:
        for index in range(len(dataset)):
            if max_batches is not None and count >= max_batches:
                break
            batch = _single_batch(dataset[index], device)
            if training:
                optimizer.zero_grad(set_to_none=True)
            output = model(batch["frame_rgb"], batch["attitude_rpy_rad"], batch["map_crops_rgb"])
            losses = _losses(torch, output, batch, config)
            if training:
                losses["total"].backward()
                torch.nn.utils.clip_grad_norm_(model.parameters(), max_norm=5.0)
                optimizer.step()
            for name in totals:
                totals[name] += float(losses[name].detach().cpu())
            retrieval_correct += int(int(output["retrieval_logits"].argmax(dim=1)[0].item()) == 0)
            position_error_m += float(
                torch.linalg.vector_norm(output["position_enu_m"] - batch["position_enu_m"], dim=1).mean().detach().cpu()
            )
            if batch["heading_valid"]:
                predicted_heading = torch.atan2(output["heading_sin_cos"][:, 0], output["heading_sin_cos"][:, 1])
                heading_delta = torch.atan2(
                    torch.sin(predicted_heading - float(batch["heading_rad"])),
                    torch.cos(predicted_heading - float(batch["heading_rad"])),
                )
                heading_error_rad += float(torch.abs(heading_delta).mean().detach().cpu())
                heading_count += 1
            count += 1
    if count == 0:
        raise ValueError("Temporal teacher epoch has no usable windows.")
    return {
        **{f"{name}_loss": round(value / count, 8) for name, value in totals.items()},
        "retrieval_top1": round(retrieval_correct / count, 8),
        "position_error_m": round(position_error_m / count, 8),
        "heading_error_rad": round(heading_error_rad / heading_count, 8) if heading_count else None,
        "heading_evaluated_batches": float(heading_count),
        "batches": float(count),
    }


def _log_mlflow_run(
    *,
    tracking_uri: str,
    experiment_name: str,
    run_name: str | None,
    run_record: dict[str, Any],
    output_dir: Path,
    run_record_path: Path,
) -> dict[str, Any]:
    """Log reproducibility metadata only when an operator explicitly opts in."""

    try:
        import mlflow
    except ImportError as exc:
        raise RuntimeError("MLflow tracking was requested but mlflow is not installed in this research environment.") from exc
    mlflow.set_tracking_uri(tracking_uri)
    mlflow.set_experiment(experiment_name)
    with mlflow.start_run(run_name=run_name) as active_run:
        training = run_record["training"]
        mlflow.log_params({
            "teacher_kind": run_record["teacher_kind"],
            "temporal_windows_manifest_sha256": run_record["input"]["temporal_windows_manifest_sha256"],
            "device": training["device"],
            "seed": training["seed"],
            "epochs": training["epochs"],
            "batch_size": training["batch_size"],
            "learning_rate": training["learning_rate"],
            **{f"model_{name}": value for name, value in training["config"].items()},
        })
        for epoch in run_record["history"]:
            for split_name in ("train", "validation"):
                for metric_name, value in epoch[split_name].items():
                    if isinstance(value, (int, float)):
                        mlflow.log_metric(f"{split_name}_{metric_name}", float(value), step=int(epoch["epoch"]))
        metadata = {"status": "logged", "tracking_uri": tracking_uri, "experiment": experiment_name, "run_id": active_run.info.run_id}
        run_record["mlflow"] = metadata
        run_record_path.write_text(json.dumps(run_record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        mlflow.log_artifact(str(run_record_path))
        mlflow.log_artifact(str(output_dir / "temporal_teacher_baseline.pt"))
        return metadata


def train_temporal_teacher_baseline(
    window_dir: str | Path,
    output_dir: str | Path,
    *,
    config: TemporalTeacherBaselineConfig | None = None,
    device: str = "auto",
    epochs: int = 1,
    batch_size: int = 1,
    learning_rate: float = 1e-4,
    seed: int = 7,
    max_train_batches: int | None = None,
    max_validation_batches: int | None = None,
    mlflow_tracking_uri: str | None = None,
    mlflow_experiment: str = "drone-vision-nav-teachers",
    mlflow_run_name: str | None = None,
) -> dict[str, Any]:
    """Train a compact baseline and save checkpoint plus immutable run report."""

    if epochs <= 0 or batch_size <= 0 or learning_rate <= 0.0:
        raise ValueError("epochs, batch_size, and learning_rate must be positive.")
    if batch_size != 1:
        raise ValueError("This auditable baseline currently uses batch_size=1 because hard-negative counts vary by window.")
    if max_train_batches is not None and max_train_batches <= 0 or max_validation_batches is not None and max_validation_batches <= 0:
        raise ValueError("Optional maximum batch counts must be positive.")
    active_config = config or TemporalTeacherBaselineConfig()
    resolved_device = choose_torch_device(device)
    run_record = build_run_record(
        window_dir=window_dir, config=active_config, device=resolved_device, seed=seed, epochs=epochs,
        batch_size=batch_size, learning_rate=learning_rate,
    )
    root = Path(output_dir).expanduser().resolve()
    if root.exists() and any(root.iterdir()):
        raise ValueError(f"Teacher baseline output directory must be empty: {root}")
    root.mkdir(parents=True, exist_ok=True)
    try:
        import torch
    except ImportError as exc:
        raise RuntimeError("Temporal teacher training requires drone-vision-nav[research] (PyTorch).") from exc
    _seed_everything(seed)
    train_dataset = TemporalCrossViewTeacherDataset(window_dir, split="train", image_size=(active_config.image_width, active_config.image_height))
    validation_dataset = TemporalCrossViewTeacherDataset(window_dir, split="validation", image_size=(active_config.image_width, active_config.image_height))
    model = build_temporal_teacher_model(active_config).to(resolved_device)
    optimizer = torch.optim.AdamW(model.parameters(), lr=learning_rate, weight_decay=1e-4)
    history: list[dict[str, Any]] = []
    for epoch in range(1, epochs + 1):
        train_metrics = _epoch(model=model, dataset=train_dataset, optimizer=optimizer, device=resolved_device, config=active_config, max_batches=max_train_batches)
        validation_metrics = _epoch(model=model, dataset=validation_dataset, optimizer=None, device=resolved_device, config=active_config, max_batches=max_validation_batches)
        history.append({"epoch": epoch, "train": train_metrics, "validation": validation_metrics})
    checkpoint_path = root / "temporal_teacher_baseline.pt"
    torch.save(
        {
            "schema_version": TEMPORAL_TEACHER_BASELINE_SCHEMA,
            "state_dict": model.state_dict(),
            "config": asdict(active_config),
            "input_temporal_windows_manifest_sha256": run_record["input"]["temporal_windows_manifest_sha256"],
            "history": history,
        },
        checkpoint_path,
    )
    run_record.update(
        {
            "status": "completed",
            "completed_utc": _utc_now(),
            "torch_version": str(torch.__version__),
            "checkpoint": {"path": checkpoint_path.name, "sha256": sha256_file(checkpoint_path)},
            "history": history,
            "mlflow": {"status": "disabled"},
            "next_action": "Evaluate held-out routes and export only after a separate promotion review; PX4 external vision remains disabled.",
        }
    )
    run_record_path = root / "training_run.json"
    run_record_path.write_text(json.dumps(run_record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    if mlflow_tracking_uri:
        _log_mlflow_run(
            tracking_uri=mlflow_tracking_uri, experiment_name=mlflow_experiment, run_name=mlflow_run_name,
            run_record=run_record, output_dir=root, run_record_path=run_record_path,
        )
    return {"output_dir": str(root), "checkpoint_path": str(checkpoint_path), "run_record_path": str(run_record_path), "run_record": run_record}


def evaluate_temporal_teacher_baseline(
    checkpoint_path: str | Path,
    window_dir: str | Path,
    *,
    device: str = "auto",
    max_validation_batches: int | None = None,
) -> dict[str, Any]:
    """Re-evaluate a checkpoint on the immutable validation temporal windows."""

    if max_validation_batches is not None and max_validation_batches <= 0:
        raise ValueError("max_validation_batches must be positive when specified.")
    resolved_device = choose_torch_device(device)
    report = build_evaluation_record(checkpoint_path=checkpoint_path, window_dir=window_dir, device=resolved_device)
    try:
        import torch
    except ImportError as exc:
        raise RuntimeError("Temporal teacher evaluation requires drone-vision-nav[research] (PyTorch).") from exc
    checkpoint = Path(checkpoint_path).expanduser().resolve()
    try:
        payload = torch.load(checkpoint, map_location=resolved_device, weights_only=True)
    except TypeError:  # PyTorch before the safe weights_only parameter.
        payload = torch.load(checkpoint, map_location=resolved_device)
    if not isinstance(payload, dict) or payload.get("schema_version") != TEMPORAL_TEACHER_BASELINE_SCHEMA:
        raise ValueError("Temporal teacher checkpoint has an unsupported schema.")
    if payload.get("input_temporal_windows_manifest_sha256") != report["input"]["temporal_windows_manifest_sha256"]:
        raise ValueError("Temporal teacher checkpoint was trained against a different temporal-window manifest.")
    raw_config = payload.get("config")
    if not isinstance(raw_config, dict):
        raise ValueError("Temporal teacher checkpoint is missing its model configuration.")
    try:
        config = TemporalTeacherBaselineConfig(**raw_config)
        config.validate()
    except (TypeError, ValueError) as exc:
        raise ValueError(f"Temporal teacher checkpoint has invalid model configuration: {exc}") from exc
    model = build_temporal_teacher_model(config).to(resolved_device)
    model.load_state_dict(payload.get("state_dict", {}), strict=True)
    dataset = TemporalCrossViewTeacherDataset(
        window_dir, split="validation", image_size=(config.image_width, config.image_height),
    )
    metrics = _epoch(
        model=model, dataset=dataset, optimizer=None, device=resolved_device, config=config,
        max_batches=max_validation_batches,
    )
    report.update(
        {
            "status": "completed",
            "completed_utc": _utc_now(),
            "torch_version": str(torch.__version__),
            "model_config": asdict(config),
            "split": "validation",
            "metrics": metrics,
            "next_action": "Compare this held-out result with tracked teacher runs; it remains research-only and cannot publish PX4 external vision.",
        }
    )
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description="Train a project-owned temporal learned cross-view baseline from validated windows.")
    parser.add_argument("--window-dir", required=True, type=Path)
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=1)
    parser.add_argument("--learning-rate", type=float, default=1e-4)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--image-width", type=int, default=320)
    parser.add_argument("--image-height", type=int, default=320)
    parser.add_argument("--feature-dim", type=int, default=128)
    parser.add_argument("--temporal-layers", type=int, default=2)
    parser.add_argument("--temporal-heads", type=int, default=4)
    parser.add_argument("--max-train-batches", type=int)
    parser.add_argument("--max-validation-batches", type=int)
    parser.add_argument("--mlflow-tracking-uri")
    parser.add_argument("--mlflow-experiment", default="drone-vision-nav-teachers")
    parser.add_argument("--mlflow-run-name")
    args = parser.parse_args()
    config = TemporalTeacherBaselineConfig(
        image_width=args.image_width, image_height=args.image_height, feature_dim=args.feature_dim,
        temporal_layers=args.temporal_layers, temporal_heads=args.temporal_heads,
    )
    try:
        result = train_temporal_teacher_baseline(
            args.window_dir, args.output_dir, config=config, device=args.device, epochs=args.epochs,
            batch_size=args.batch_size, learning_rate=args.learning_rate, seed=args.seed,
            max_train_batches=args.max_train_batches, max_validation_batches=args.max_validation_batches,
            mlflow_tracking_uri=args.mlflow_tracking_uri, mlflow_experiment=args.mlflow_experiment,
            mlflow_run_name=args.mlflow_run_name,
        )
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    print(json.dumps(result["run_record"], indent=2, sort_keys=True))


def evaluate_main() -> None:
    parser = argparse.ArgumentParser(description="Evaluate a temporal learned teacher checkpoint on validated held-out windows.")
    parser.add_argument("--checkpoint", required=True, type=Path)
    parser.add_argument("--window-dir", required=True, type=Path)
    parser.add_argument("--device", choices=("auto", "cpu", "cuda"), default="auto")
    parser.add_argument("--max-validation-batches", type=int)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    try:
        report = evaluate_temporal_teacher_baseline(
            args.checkpoint, args.window_dir, device=args.device, max_validation_batches=args.max_validation_batches,
        )
    except (RuntimeError, ValueError) as exc:
        raise SystemExit(str(exc)) from exc
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
