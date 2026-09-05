"""Prepare an auditable portable copy of pinned Video2BEV second-stage source."""

from __future__ import annotations

import argparse
import ast
import hashlib
import importlib
import json
from pathlib import Path
import random
import shutil
import subprocess
import sys
import tempfile
from types import ModuleType, SimpleNamespace

from .upstream_training import _git_head
from .video2bev_preflight import audit_dataset, audit_negatives


COMMIT = "d6737eb6ca3ead1bdfdc6f38b20347597ed5abce"
SCHEMA = "vision_nav_video2bev_workspace_v1"
TRAINER = "train_bev_paired_fsra.py"
LOADER = "tool/Dataloader_University.py"
FACTORY = "tool/make_dataloader.py"


def _sha(path):
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _replace_nodes(text, changes):
    # AST columns are UTF-8 byte offsets, not character offsets.
    lines = text.encode().splitlines(keepends=True)
    offsets = [0]
    for line in lines:
        offsets.append(offsets[-1] + len(line))
    payload = text.encode()
    spans = [(offsets[node.lineno - 1] + node.col_offset,
              offsets[node.end_lineno - 1] + node.end_col_offset, replacement.encode())
             for node, replacement in changes]
    for start, end, replacement in sorted(spans, reverse=True):
        payload = payload[:start] + replacement + payload[end:]
    result = payload.decode()
    ast.parse(result)
    return result


def patch_source(name: str, text: str) -> str:
    tree, changes = ast.parse(text), []
    if name == TRAINER:
        for module, symbol in (("folder", "ImageFolder"), ("random_erasing", "RandomErasing")):
            matches = [node for node in tree.body if isinstance(node, ast.ImportFrom)
                       and node.module == module and len(node.names) == 1
                       and node.names[0].name == symbol and node.names[0].asname is None]
            if len(matches) != 1 or any(isinstance(node, ast.Name) and node.id == symbol for node in ast.walk(tree)):
                raise ValueError("Expected exactly one unused upstream import")
            changes.append((matches[0], "# Removed unused import from absent upstream module."))
        parse = [node for node in tree.body if isinstance(node, ast.Assign)
                 and isinstance(node.value, ast.Call) and isinstance(node.value.func, ast.Attribute)
                 and isinstance(node.value.func.value, ast.Name) and node.value.func.value.id == "parser"
                 and node.value.func.attr == "parse_args"]
        if len(parse) != 1:
            raise ValueError("Expected one upstream argument parse")
        changes.append((parse[0], "parser.add_argument('--sd-negative-root', required=True, type=os.path.abspath)\n"
                        + ast.unparse(parse[0])))
    elif name == LOADER:
        assignments = [node for node in ast.walk(tree) if isinstance(node, ast.Assign)
                       and any(isinstance(target, ast.Attribute) and target.attr == "sd_neg_sample_apth"
                               for target in node.targets)]
        if len(assignments) != 1 or not isinstance(assignments[0].value, ast.Constant):
            raise ValueError("Expected one hardcoded negative root")
        changes.append((assignments[0].value, "kwargs['sd_negative_root']"))
    elif name == FACTORY:
        calls = [node for node in ast.walk(tree) if isinstance(node, ast.Call)
                 and isinstance(node.func, ast.Name) and node.func.id == "Dataloader_University"]
        if len(calls) != 2 or any(k.arg == "sd_negative_root" for node in calls for k in node.keywords):
            raise ValueError("Expected two unadapted upstream data-loader constructors")
        for call in calls:
            copied = ast.parse(ast.unparse(call), mode="eval").body
            copied.keywords.append(ast.keyword(arg="sd_negative_root", value=ast.parse("opt.sd_negative_root", mode="eval").body))
            changes.append((call, ast.unparse(copied)))
    else:
        raise ValueError("No reviewed adaptation for this source file")
    return _replace_nodes(text, changes)


def prepare(source: Path, output: Path) -> dict:
    source, output = source.resolve(), output.resolve()
    if output.exists() or output.is_relative_to(source):
        raise ValueError("Workspace must be new and outside the pinned checkout")
    if _git_head(source) != COMMIT:
        raise ValueError("Wrong Video2BEV second-stage revision")
    names = subprocess.check_output(["git", "-C", str(source), "ls-files", "-z"]).decode().split("\0")
    output.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=".video2bev-adapt-", dir=output.parent))
    before, after = {}, {}
    try:
        for name in filter(None, names):
            path = source / name
            if not path.is_file() or path.is_symlink() or not path.resolve().is_relative_to(source):
                raise ValueError("Only contained regular tracked files can enter the workspace")
            destination = staging / name
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(path, destination)
            before[name] = _sha(destination)
        for name in (TRAINER, LOADER, FACTORY):
            path = staging / name
            path.write_text(patch_source(name, path.read_text()), encoding="utf-8")
        after = {name: _sha(staging / name) for name in before}
        changed = [name for name in before if before[name] != after[name]]
        if set(changed) != {TRAINER, LOADER, FACTORY}:
            raise ValueError("Unexpected adaptation file set")
        result = {"schema_version": SCHEMA, "status": "prepared_not_training_qualified",
                  "source_revision": COMMIT, "adapter_sha256": _sha(Path(__file__)),
                  "source_sha256": before, "workspace_sha256": after, "changed_files": sorted(changed),
                  "changes": ["Remove two unused imports of absent root modules",
                              "Require explicit --sd-negative-root",
                              "Pass that root to both upstream data loaders"],
                  "model_and_losses_changed": False, "publication_mode": "research_only",
                  "px4_external_vision": "disabled", "model_execution_verified": False}
        (staging / "workspace.json").write_text(json.dumps(result, sort_keys=True, indent=2) + "\n")
        staging.rename(output)
        return result
    except BaseException:
        shutil.rmtree(staging)
        raise


def validate(root: Path) -> dict:
    root = root.resolve()
    path = root / "workspace.json"
    if path.stat().st_size > 1024 * 1024:
        raise ValueError("Workspace manifest too large")
    report = json.loads(path.read_bytes())
    if report.get("schema_version") != SCHEMA or report.get("source_revision") != COMMIT:
        raise ValueError("Unsupported prepared workspace")
    members = report.get("workspace_sha256")
    if not isinstance(members, dict) or not {TRAINER, LOADER, FACTORY, "model.py", "LICENSE"}.issubset(members):
        raise ValueError("Incomplete workspace inventory")
    for name, digest in members.items():
        member = root / name
        if Path(name).is_absolute() or not member.resolve().is_relative_to(root) or _sha(member) != digest:
            raise ValueError("Workspace member changed or escaped")
    if any(path.relative_to(root).as_posix() not in members for path in root.rglob("*.py")):
        raise ValueError("Unexpected Python source in prepared workspace")
    return report


def smoke_data(root: Path, dataset: Path, negatives: Path, *, input_kind: str) -> dict:
    """Execute the real upstream transforms/samplers/collation, not a model."""
    import numpy as np
    import torch

    root, dataset, negatives = root.resolve(), dataset.resolve(), negatives.resolve()
    validate(root)
    if input_kind not in {"synthetic", "recorded"}:
        raise ValueError("Explicit synthetic/recorded input classification required")
    audit = audit_dataset(dataset)
    if audit["errors"]:
        raise ValueError(f"Prepared dataset failed image audit: {audit['errors']}")
    negative_audit = audit_negatives(negatives, [path.name for path in (dataset / "bev").iterdir()])
    if not negative_audit["complete"]:
        raise ValueError(f"Synthetic-negative image audit failed: {negative_audit['errors']}")
    if any(name == "tool" or name.startswith("tool.") for name in sys.modules):
        raise ValueError("A tool package is already imported; use a fresh process")
    np.random.seed(0)
    random.seed(0)
    torch.manual_seed(0)
    package = ModuleType("tool")
    package.__path__ = [str(root / "tool")]
    sys.modules["tool"] = package
    try:
        factory = importlib.import_module("tool.make_dataloader")
        opt = SimpleNamespace(h=256, w=256, pad=10, erasing_p=0., color_jitter=False, DA=False,
                              rendered_BEV=True, train_all=False, data_dir=str(dataset),
                              sd_negative_sample=True, sd_negative_root=str(negatives),
                              batchsize=2, sample_num=1, num_worker=0)
        first, classes, sizes, second = factory.make_dataset(opt)
        shapes, tensor_evidence = [], []
        def inspect(value):
            if isinstance(value, torch.Tensor):
                if not torch.isfinite(value).all():
                    raise ValueError("Nonfinite upstream batch")
                shapes.append(list(value.shape))
                tensor_evidence.append({"shape": list(value.shape), "dtype": str(value.dtype),
                    "sha256": hashlib.sha256(value.detach().cpu().contiguous().numpy().tobytes()).hexdigest()})
            elif isinstance(value, (tuple, list)):
                for item in value:
                    inspect(item)
            else:
                raise ValueError("Unexpected upstream batch structure")
        inspect(next(iter(first)))
        inspect(next(iter(second)))
    finally:
        for name in list(sys.modules):
            if name == "tool" or name.startswith("tool."):
                del sys.modules[name]
    return {"schema_version": "vision_nav_video2bev_data_smoke_v1", "status": "passed",
            "scope": "first_batch_from_each_real_upstream_loader_not_model_training",
            "workspace_manifest_sha256": _sha(root / "workspace.json"), "dataset_audit": audit,
            "input_kind": input_kind, "class_count": len(classes), "declared_sizes": sizes,
            "batch_tensor_shapes": shapes, "batch_tensors": tensor_evidence, "torch": str(torch.__version__),
            "negative_root": str(negatives), "all_negative_files_audited": True,
            "negative_audit": negative_audit,
            "position_authority": False, "px4_external_vision": "disabled"}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    build = sub.add_parser("prepare")
    build.add_argument("--source", type=Path, required=True)
    build.add_argument("--output", type=Path, required=True)
    smoke = sub.add_parser("smoke-data")
    for name in ("workspace", "dataset", "negatives", "output"):
        smoke.add_argument(f"--{name}", type=Path, required=True)
    smoke.add_argument("--input-kind", choices=("synthetic", "recorded"), required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(args.source, args.output)
    else:
        if args.output.exists():
            parser.error("Output already exists")
        result = smoke_data(args.workspace, args.dataset, args.negatives, input_kind=args.input_kind)
        with args.output.open("x") as stream:
            json.dump(result, stream, indent=2, allow_nan=False)
            stream.write("\n")
    print(json.dumps({"status": result["status"]}))


if __name__ == "__main__":
    main()
