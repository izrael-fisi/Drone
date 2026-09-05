"""Audit the released rendered-BEV training inputs without executing upstream code."""

from __future__ import annotations

import ast
from glob import iglob
import hashlib
import io
import json
from pathlib import Path, PurePosixPath, PureWindowsPath


MODALITIES = ("satellite", "street", "drone", "google", "bev")
SOURCE_FILES = ("train_bev_paired_fsra.py", "model.py", "utils.py", "folder.py",
                "random_erasing.py", "autoaugment.py", "dataset.py",
                "backbone/vit_pytorch.py", "tool/make_dataloader.py",
                "tool/Dataloader_University.py", "tool/random_erasing.py", "tool/autoaugment.py")
MAX_AUDIT_IMAGES = 100000


def _image_record(path: Path, root: Path) -> dict:
    from PIL import Image

    if not path.is_file() or not path.resolve().is_relative_to(root):
        raise ValueError("Expected contained image file")
    with path.open("rb") as stream:
        contents = stream.read(32 * 1024 * 1024 + 1)
    if len(contents) > 32 * 1024 * 1024:
        raise ValueError("Image exceeds 32 MiB")
    with Image.open(io.BytesIO(contents)) as image:
        if min(image.size) < 16 or max(image.size) > 8192:
            raise ValueError("Image dimensions outside audit bounds")
        image.load()
        image.convert("RGB")
        dimensions = list(image.size)
    return {"path": path.relative_to(root).as_posix(),
            "sha256": hashlib.sha256(contents).hexdigest(), "size": dimensions}


def audit_negatives(root: Path, classes: list[str]) -> dict:
    """Decode every supplemental file the pinned glob sampler can select."""
    from PIL import Image

    root = root.resolve()
    if not isinstance(classes, list) or not classes or any(
            not isinstance(name, str) or not name or name in {".", ".."}
            or "/" in name or "\\" in name for name in classes):
        raise ValueError("Expected nonempty contained class names")
    if len(set(classes)) != len(classes):
        raise ValueError("Duplicate class names")
    errors, inventory, counts = [], [], {"satellite": 0, "bev": 0}
    visited, limit_hit = 0, False
    for modality in counts:
        for class_id in sorted(classes):
            directory = root / modality / class_id
            if not directory.is_dir() or not directory.resolve().is_relative_to(root):
                errors.append(f"missing_or_external_negative_class:{modality}/{class_id}")
                continue
            found = False
            # Match upstream glob semantics, including suffix case and hidden-file
            # exclusion. Path.glob includes hidden files and is not equivalent.
            for filename in iglob(str(directory / "*.jpeg")):
                found = True
                visited += 1
                if visited > MAX_AUDIT_IMAGES:
                    errors.append("negative_image_audit_limit_exceeded")
                    limit_hit = True
                    break
                path = Path(filename)
                try:
                    inventory.append(_image_record(path, root))
                    counts[modality] += 1
                except (OSError, ValueError, Image.DecompressionBombError) as exc:
                    errors.append(f"invalid_negative_image:{path.relative_to(root).as_posix()}:{type(exc).__name__}")
            if not found:
                errors.append(f"empty_negative_class:{modality}/{class_id}")
            if limit_hit:
                break
        if limit_hit:
            break
    inventory.sort(key=lambda row: row["path"])
    complete = not errors
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"schema_version": "vision_nav_video2bev_negative_audit_v1", "complete": complete,
            "errors": errors, "classes": sorted(classes), "image_counts": counts,
            "inventory_sha256": digest if complete else None,
            "scope": "all_glob_eligible_negative_images_for_selected_bev_classes",
            "image_limit": MAX_AUDIT_IMAGES, "rights_verified": False,
            "semantic_negative_quality_verified": False}


def audit_dataset(root: Path) -> dict:
    """Validate files the pinned loaders randomly sample, including unused views."""
    from PIL import Image

    root = root.resolve()
    errors, inventory, counts = [], [], {}
    visited = 0
    directories = {}
    for name in MODALITIES:
        directory = root / name
        if not directory.is_dir() or not directory.resolve().is_relative_to(root):
            errors.append(f"missing_or_external_modality:{name}")
            continue
        children = sorted(directory.iterdir())
        if any(not p.is_dir() or not p.resolve().is_relative_to(root) for p in children):
            errors.append(f"invalid_class_directory:{name}")
            continue
        directories[name] = {p.name: p for p in children}
    classes = sorted(directories.get("bev", {}))
    if not classes:
        errors.append("missing_rendered_bev_classes")
    for name, folders in directories.items():
        if set(classes) - set(folders):
            errors.append(f"missing_bev_class_in_modality:{name}")
        counts[name] = 0
        # Extra classes are ignored by this upstream sampler, whose index is BEV.
        for class_id in classes:
            directory = folders.get(class_id)
            if directory is None:
                continue
            files = sorted(directory.iterdir())
            if not files and name != "google":
                errors.append(f"empty_class:{name}/{class_id}")
            for path in files:
                relative = path.relative_to(root).as_posix()
                visited += 1
                if visited > MAX_AUDIT_IMAGES:
                    errors.append("dataset_exceeds_100000_audited_images")
                    return {"errors": errors, "class_count": len(classes), "image_counts": counts,
                            "inventory_sha256": None, "complete": False}
                try:
                    inventory.append(_image_record(path, root))
                    counts[name] += 1
                except (OSError, ValueError, Image.DecompressionBombError) as exc:
                    errors.append(f"invalid_image:{relative}:{type(exc).__name__}")
    digest = hashlib.sha256(json.dumps(inventory, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    return {"errors": errors, "class_count": len(classes), "image_counts": counts,
            "inventory_sha256": digest, "complete": not errors,
            "scope": "decoded_training_assets_not_geographic_split_or_reconstruction_quality"}


def inspect_workflow(checkout: Path, dataset: Path, *, second_stage: bool) -> dict:
    errors, files = [], {}
    for name in SOURCE_FILES:
        path = checkout / name
        if not path.is_file() or not path.resolve().is_relative_to(checkout.resolve()):
            errors.append(f"missing_upstream_module:{name}")
        else:
            files[name] = hashlib.sha256(path.read_bytes()).hexdigest()
    negative_root = None
    if second_stage:
        loader = checkout / "tool/Dataloader_University.py"
        if loader.is_file():
            try:
                tree = ast.parse(loader.read_text())
                for node in ast.walk(tree):
                    if isinstance(node, ast.Assign) and isinstance(node.value, ast.Constant):
                        if any(isinstance(target, ast.Attribute) and target.attr == "sd_neg_sample_apth"
                               for target in node.targets):
                            negative_root = node.value.value
                if isinstance(negative_root, str) and (
                        PurePosixPath(negative_root).is_absolute() or PureWindowsPath(negative_root).is_absolute()):
                    errors.append("upstream_hardcoded_synthetic_negative_root_requires_adapter")
            except (SyntaxError, UnicodeError):
                errors.append("invalid_upstream_loader_source")
    else:
        backbone = checkout / "pre_trained/vit_small_p16_224-15ec54c9.pth"
        if not backbone.is_file() or backbone.stat().st_size == 0:
            errors.append("missing_first_stage_backbone_weights")
    data = audit_dataset(dataset)
    errors.extend(data["errors"])
    return {"schema_version": "vision_nav_video2bev_input_preflight_v1",
            "errors": errors, "source_sha256": files, "dataset": data,
            "upstream_synthetic_negative_root": negative_root,
            "model_execution_verified": False, "checkpoint_compatibility_verified": False,
            "requires_precomputed_3dgs_bev": True,
            "warnings": ["Shell entrypoint contains author-specific paths; do not launch unchanged",
                         "CUDA/dependencies/checkpoint loading are separate execution gates",
                         "Dataset, generated BEV and model rights require separate review"]}
