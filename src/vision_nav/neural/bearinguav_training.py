"""Offline Bearing-UAV domain adaptation; no model promotion or vehicle output."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
from pathlib import Path
import platform
import random
import shutil
import sys
import tempfile

import numpy as np

from . import bearinguav_evaluation as evaluation
from . import bearinguav_reference as reference
from . import bearinguav_checkpoint as recovery
from .bearinguav_reference import DATASET_CONVENTION as CONVENTION


SCHEMA = "vision_nav_bearinguav_training_run_v1"


def _json(path: Path, value: dict) -> None:
    with path.open("w") as stream:
        json.dump(value, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")


def _sha(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def audit_dataset(root: Path) -> dict:
    """Bind the declared training membership to actual training cases before eval."""
    root = root.resolve()
    manifest_path = root / "manifest.json"
    if manifest_path.stat().st_size > 8 * 1024 * 1024:
        raise ValueError("Dataset manifest exceeds 8 MiB")
    data = manifest_path.read_bytes()
    manifest = json.loads(data)
    if (manifest.get("schema_version") != "vision_nav_bearinguav_dataset_build_v1"
            or manifest.get("convention") != CONVENTION
            or manifest.get("context_policy") != "oracle_context"
            or manifest.get("input_kind") not in {"synthetic", "recorded"}):
        raise ValueError("Expected converted Bearing-UAV oracle-context dataset")
    checksums = manifest.get("checksums_sha256")
    if not isinstance(checksums, dict) or not checksums:
        raise ValueError("Dataset checksum index is required")
    for name, digest in checksums.items():
        evaluation._member(root, {"path": name, "sha256": digest}, 64 * 1024 * 1024)

    def member(item, limit=64 * 1024):
        if checksums.get(item.get("path")) != item.get("sha256"):
            raise ValueError("Referenced member not bound to dataset checksum index")
        return evaluation._member(root, item, limit)

    def document(name):
        _, contents = member({"path": name, "sha256": checksums.get(name)}, 8 * 1024 * 1024)
        return json.loads(contents)

    training = document("train-cases.json")
    membership = document("training-membership.json")
    if (training.get("schema_version") != "vision_nav_bearinguav_training_cases_v1"
            or training.get("policy") != "offline_training_only"):
        raise ValueError("Expected explicit offline training cases")
    rows = training.get("records")
    if not isinstance(rows, list) or not 1 <= len(rows) <= 10000:
        raise ValueError("Expected 1..10000 training cases")
    checked, actual, ids, pairs, positives = [], set(), set(), set(), set()
    for row in rows:
        sample_path, contents = member(row["sample"])
        sample = json.loads(contents)
        # Converter places all content-addressed assets next to its root manifests.
        if sample_path.parent != root:
            raise ValueError("Converted sample must be at the dataset root")
        if (sample.get("dataset_release_id") != manifest["release_id"]
                or sample.get("input_kind") != manifest["input_kind"] or sample.get("split") != "train"
                or sample.get("conversion", {}).get("convention") != CONVENTION):
            raise ValueError("Training sample release, split or convention mismatch")
        for item in sample["patches"].values():
            member(item, 16 * 1024 * 1024)
        member(sample["source_query"], 16 * 1024 * 1024)
        tensor, _ = reference.load_patches(sample, root)
        if list(tensor.shape[-2:]) != [manifest["image_size"]] * 2:
            raise ValueError("Patch dimensions differ from declared dataset size")
        evaluation.validate_labels(row)
        session, scene = evaluation._text(row, "session_id"), evaluation._text(row, "scene_id")
        window = evaluation._text(sample["conversion"], "source_window_id")
        digest = sample["source_query"]["sha256"]
        context = tuple(sample["patches"][name]["sha256"] for name in reference.PATCH_ORDER[:4])
        if sample["sample_id"] in ids or (digest, context) in pairs:
            raise ValueError("Duplicate training case or query/context pair")
        ids.add(sample["sample_id"])
        pairs.add((digest, context))
        actual.add((window, session, scene, digest))
        if row["context_match"]:
            if digest in positives:
                raise ValueError("Repeated positive training query")
            positives.add(digest)
        checked.append({"row": row, "sample": sample, "root": root})
    declared = [(evaluation._text(row, "sample_id"), evaluation._text(row, "session_id"),
                 evaluation._text(row, "scene_id"), evaluation._text(row, "uav_sha256"))
                for row in membership["records"]]
    if len(declared) != len(set(declared)) or set(declared) != actual:
        raise ValueError("Declared training membership differs from actual training cases")
    if not positives:
        raise ValueError("At least one positive training query is required")
    eval_manifest = document("evaluation.json")
    if eval_manifest.get("split") != "validation":
        raise ValueError("Checkpoint selection must use validation, never test")
    if eval_manifest.get("training_membership") != {
            "path": "training-membership.json", "sha256": checksums["training-membership.json"]}:
        raise ValueError("Validation must reference the actual training membership")
    audited = evaluation.audit(root / "evaluation.json")
    if (audited["manifest"]["dataset_release_id"] != manifest["release_id"]
            or audited["manifest"]["input_kind"] != manifest["input_kind"]
            or audited["manifest"]["context_policy"] != manifest["context_policy"]
            or audited["manifest"]["label_protocol"] != CONVENTION):
        raise ValueError("Validation must use the same dataset release and convention")
    for item in audited["checked"]:
        if item["root"] != root:
            raise ValueError("Converted validation sample must be at the dataset root")
        member(item["row"]["sample"])
        for asset in item["sample"]["patches"].values():
            member(asset, 16 * 1024 * 1024)
        member(item["sample"]["source_query"], 16 * 1024 * 1024)
    if (len(rows) != manifest["train_case_count"]
            or len(audited["checked"]) != manifest["validation_case_count"]):
        raise ValueError("Declared case counts differ from dataset")
    if not any(item["row"]["context_match"] for item in audited["checked"]):
        raise ValueError("Validation needs positive labels for checkpoint selection")
    return {"manifest": manifest, "manifest_sha256": hashlib.sha256(data).hexdigest(),
            "train": checked, "validation": audited,
            "membership_sha256": checksums["training-membership.json"]}


def evaluate_model(model, items: list[dict], device: str) -> dict:
    model.eval()
    rows = []
    for item in items:
        tensor, _ = reference.load_patches(item["sample"], item["root"])
        prediction, timings = reference.predict(model, tensor, device)
        rows.append({"sample_id": item["sample"]["sample_id"], "prediction": prediction.tolist(),
                     "inference_ms": timings, **evaluation.score(prediction, item["row"])})
    return {"metrics": evaluation.summarize(rows), "samples": rows}


def train(dataset: Path, source: Path, checkpoint: Path, configuration: Path, output: Path,
          *, epochs: int = 1, batch_size: int = 4, learning_rate: float = 1e-4,
          seed: int = 7, device: str = "cpu", threads: int = 2,
          resume_from: Path | None = None) -> dict:
    import torch
    import torchvision

    if (type(epochs) is not int or not 1 <= epochs <= 1000
            or type(batch_size) is not int or not 1 <= batch_size <= 64
            or type(threads) is not int or not 1 <= threads <= 16
            or type(seed) is not int or not 0 <= seed < 2**32
            or not isinstance(learning_rate, (float, int)) or isinstance(learning_rate, bool)
            or not math.isfinite(learning_rate) or not 0 < learning_rate <= 1):
        raise ValueError("Invalid finite training hyperparameters")
    dataset, output = dataset.resolve(), output.resolve()
    if output.exists() or output.is_relative_to(dataset):
        raise ValueError("Output must be new and outside the immutable dataset")
    os.environ.setdefault("CUBLAS_WORKSPACE_CONFIG", ":4096:8")
    random.seed(seed)
    np.random.seed(seed)
    torch.manual_seed(seed)
    torch.set_num_threads(threads)
    torch.use_deterministic_algorithms(True)
    if device == "cuda":
        torch.backends.cudnn.benchmark = False
        torch.backends.cuda.matmul.allow_tf32 = False
        torch.backends.cudnn.allow_tf32 = False
    audited = audit_dataset(dataset)
    # The default digest cannot silently accept a locally substituted initial model.
    model = reference.load_model(source, checkpoint, configuration, device)
    parameters = [p for p in model.parameters() if p.requires_grad]
    if not parameters:
        raise ValueError("The pinned model has no trainable parameters")
    optimizer = torch.optim.AdamW(parameters, lr=learning_rate, weight_decay=.01)
    positives = [item for item in audited["train"] if item["row"]["context_match"]]
    environment = {"torch": str(torch.__version__), "torchvision": str(torchvision.__version__),
                   "numpy": np.__version__, "python": sys.version.split()[0], "device": device,
                   "threads": threads, "system": platform.system(), "machine": platform.machine(),
                   "cuda_build": torch.version.cuda, "cudnn": torch.backends.cudnn.version(),
                   "cuda_devices": [torch.cuda.get_device_name(i) for i in range(torch.cuda.device_count())]
                   if device == "cuda" else [], "cublas_workspace": os.environ["CUBLAS_WORKSPACE_CONFIG"]}
    context = {"dataset_manifest_sha256": audited["manifest_sha256"],
               "source_revision": reference.SOURCE_COMMIT,
               "initial_checkpoint_sha256": reference.CHECKPOINT_SHA256,
               "configuration_sha256": reference.CONFIG_SHA256,
               "trainer_sha256": _sha(Path(__file__)), "recovery_sha256": _sha(Path(recovery.__file__)),
               "adapter_sha256": _sha(Path(reference.__file__)), "evaluator_sha256": _sha(Path(evaluation.__file__)),
               "batch_size": batch_size, "learning_rate": learning_rate, "seed": seed,
               "environment": environment}
    resumed, resume_provenance = None, None
    if resume_from is not None:
        if output.is_relative_to(resume_from.resolve()):
            raise ValueError("Resume output must be outside the original run")
        resumed, resume_provenance = recovery.load(resume_from, context)
        if resumed["epoch"] >= epochs:
            raise ValueError("Requested total epochs must exceed the completed resume epoch")
        expected = model.state_dict()
        if (resumed["best_model"].keys() != expected.keys()
                or any(resumed["best_model"][key].shape != value.shape for key, value in expected.items())):
            raise ValueError("Resumed best model is incompatible with the pinned architecture")
        model.load_state_dict(resumed["model"], strict=True)
        original_groups = [{k: v for k, v in group.items() if k != "params"}
                           for group in optimizer.state_dict()["param_groups"]]
        resumed_groups = [{k: v for k, v in group.items() if k != "params"}
                          for group in resumed["optimizer"]["param_groups"]]
        if resumed_groups != original_groups:
            raise ValueError("Resumed optimizer settings differ from the requested configuration")
        optimizer.load_state_dict(resumed["optimizer"])
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".bearing-train-", dir=output.parent))
    history, best, best_epoch = [], float("inf"), None
    try:
        generator = torch.Generator().manual_seed(seed)
        if resumed is None:
            initial = evaluate_model(model, audited["validation"]["checked"], device)
            start_epoch = 1
        else:
            initial = resumed["initial_validation"]
            history, best, best_epoch = resumed["history"], resumed["best"], resumed["best_epoch"]
            torch.save({"model_state_dict": resumed["best_model"]}, staging / "best_model.pth")
            _json(staging / "best-validation.json", resumed["best_validation"])
            start_epoch = resumed["epoch"] + 1
            recovery.restore_random(resumed["random"], generator, device)
        _json(staging / "initial-validation.json", initial)
        print(json.dumps({"staging_directory": str(staging), "next_epoch": start_epoch}), flush=True)
        for epoch in range(start_epoch, epochs + 1):
            model.train()
            order = torch.randperm(len(positives), generator=generator).tolist()
            total_loss, seen = 0., 0
            for start in range(0, len(order), batch_size):
                selected = [positives[i] for i in order[start:start + batch_size]]
                tensors = [reference.load_patches(item["sample"], dataset)[0] for item in selected]
                inputs = torch.cat(tensors).to(device)
                labels = torch.tensor([item["row"]["ground_truth"]["position_xy"] for item in selected],
                                      dtype=torch.float32, device=device)
                optimizer.zero_grad(set_to_none=True)
                position, _ = model(inputs)
                if position.shape != labels.shape or not torch.isfinite(position).all():
                    raise ValueError("Invalid training model output")
                # Position-only adaptation: no invented heading or wrong-map target.
                loss = torch.nn.functional.smooth_l1_loss(position, labels, beta=1., reduction="mean")
                if not torch.isfinite(loss):
                    raise ValueError("Nonfinite training loss")
                loss.backward()
                gradient_norm = torch.nn.utils.clip_grad_norm_(parameters, 1., error_if_nonfinite=True)
                optimizer.step()
                if not all(torch.isfinite(p).all() for p in parameters):
                    raise ValueError("Nonfinite parameters after optimizer step")
                total_loss += float(loss.detach()) * len(selected)
                seen += len(selected)
            validation = evaluate_model(model, audited["validation"]["checked"], device)
            metric = validation["metrics"]["position_error_normalized"]["mean"]
            history.append({"epoch": epoch, "train_position_smooth_l1": total_loss / seen,
                            "train_positive_count": seen, "validation": validation["metrics"],
                            "last_unclipped_gradient_norm": float(gradient_norm)})
            saved = {"model_state_dict": {name: value.detach().cpu() for name, value in model.state_dict().items()}}
            torch.save(saved, staging / "last_model.pth")
            if metric < best:
                best, best_epoch = metric, epoch
                shutil.copyfile(staging / "last_model.pth", staging / "best_model.pth")
                _json(staging / "best-validation.json", validation)
            _json(staging / "history.json", {"epochs": history})
            state = {"epoch": epoch, "model": model.state_dict(), "optimizer": optimizer.state_dict(),
                     "best_model": torch.load(staging / "best_model.pth", map_location="cpu", weights_only=True)["model_state_dict"],
                     "random": recovery.random_state(generator, device), "history": history,
                     "best": best, "best_epoch": best_epoch, "initial_validation": initial,
                     "best_validation": json.loads((staging / "best-validation.json").read_text())}
            recovery.save(staging, state, context)
            print(json.dumps({"epoch": epoch, "train_loss": total_loss / seen,
                              "validation_position_error_normalized": metric}), flush=True)
        # Detect on-disk dataset changes during the run, not just at startup.
        repeated = audit_dataset(dataset)
        if repeated["manifest_sha256"] != audited["manifest_sha256"]:
            raise ValueError("Dataset changed during training")
        report = {"schema_version": SCHEMA, "status": "research_training_only",
            "navigation_mode": "NO_GLOBAL_FIX", "position_authority": False,
            "px4_external_vision": "disabled", "field_ready": False,
            "dataset_release_id": audited["manifest"]["release_id"],
            "dataset_manifest_sha256": audited["manifest_sha256"],
            "training_membership_sha256": audited["membership_sha256"],
            "validation_manifest_sha256": audited["validation"]["manifest_sha256"],
            "input_kind": audited["manifest"]["input_kind"], "context_policy": "oracle_context",
            "source_revision": reference.SOURCE_COMMIT, "initial_checkpoint_sha256": reference.CHECKPOINT_SHA256,
            "configuration_sha256": reference.CONFIG_SHA256,
            "trainer_sha256": _sha(Path(__file__)), "reference_adapter_sha256": _sha(Path(reference.__file__)),
            "evaluator_sha256": _sha(Path(evaluation.__file__)),
            "resume_provenance": resume_provenance, "completed_epochs": epochs,
            "epochs_executed_this_run": epochs - start_epoch + 1,
            "best_epoch": best_epoch, "best_validation_position_error_normalized": best,
            "improved_over_initial_validation": best < initial["metrics"]["position_error_normalized"]["mean"],
            "selection": "lowest_validation_mean_normalized_position_error_among_trained_epochs",
            "hyperparameters": {"epochs": epochs, "batch_size": batch_size, "learning_rate": learning_rate,
                "seed": seed, "optimizer": "AdamW", "weight_decay": .01, "gradient_clip_norm": 1.,
                "loss": "position_smooth_l1_beta1_mean_xy", "trainable_scope": "upstream_configuration"},
            "trainable_parameters": sum(p.numel() for p in parameters),
            "train_positive_count": len(positives), "excluded_wrong_context_training_count": len(audited["train"]) - len(positives),
            "direction_supervision": "disabled_unqualified_camera_heading_labels",
            "overlapping_training_scenes": audited["validation"]["scene_overlap"],
            "timing_policies": audited["manifest"]["timing_policies"],
            "weights_and_dataset_rights_verified": False, "official_paper_reproduction": False,
            "environment": environment,
            "artifacts_sha256": {path.name: _sha(path) for path in staging.iterdir() if path.is_file()}}
        _json(staging / "training_run.json", report)
        staging.rename(output)
        return report
    except BaseException as exc:
        _json(staging / "FAILED.json", {"status": "failed", "reason": str(exc),
                                       "position_authority": False, "field_ready": False})
        raise


def checkpoint_from_run(root: Path, *, membership_sha256: str) -> tuple[Path, str, dict]:
    """Load only a completed research run bound to the evaluation membership."""
    path = root / "training_run.json"
    if path.stat().st_size > 1024 * 1024 or (root / "FAILED.json").exists():
        raise ValueError("Invalid or failed training run")
    report = json.loads(path.read_bytes())
    if (report.get("schema_version") != SCHEMA or report.get("status") != "research_training_only"
            or report.get("source_revision") != reference.SOURCE_COMMIT
            or report.get("configuration_sha256") != reference.CONFIG_SHA256
            or report.get("initial_checkpoint_sha256") != reference.CHECKPOINT_SHA256
            or report.get("training_membership_sha256") != membership_sha256):
        raise ValueError("Training provenance or evaluation membership mismatch")
    digest = report["artifacts_sha256"]["best_model.pth"]
    checkpoint, _ = evaluation._member(root, {"path": "best_model.pth", "sha256": digest}, 100 * 1024 * 1024)
    return checkpoint, digest, report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("dataset", "source", "checkpoint", "configuration", "output"):
        parser.add_argument(f"--{name}", required=True, type=Path)
    parser.add_argument("--epochs", type=int, default=1)
    parser.add_argument("--batch-size", type=int, default=4)
    parser.add_argument("--learning-rate", type=float, default=1e-4)
    parser.add_argument("--seed", type=int, default=7)
    parser.add_argument("--device", choices=("cpu", "cuda"), default="cpu")
    parser.add_argument("--threads", type=int, default=2)
    parser.add_argument("--resume-from", type=Path)
    args = parser.parse_args()
    report = train(args.dataset, args.source, args.checkpoint, args.configuration, args.output,
                   epochs=args.epochs, batch_size=args.batch_size, learning_rate=args.learning_rate,
                   seed=args.seed, device=args.device, threads=args.threads, resume_from=args.resume_from)
    print(json.dumps({"status": report["status"], "best_epoch": report["best_epoch"]}))


if __name__ == "__main__":
    main()
