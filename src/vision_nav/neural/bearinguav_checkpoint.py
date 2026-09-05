"""Atomic, hash-bound epoch recovery for trusted offline research runs."""

from __future__ import annotations

import hashlib
import io
import json
import math
import os
from pathlib import Path

from .bearinguav_reference import checked_bytes


SCHEMA = "vision_nav_bearinguav_resume_v1"


def _cpu_tree(value):
    import torch
    if isinstance(value, torch.Tensor):
        if not torch.isfinite(value).all():
            raise ValueError("Nonfinite checkpoint tensor")
        return value.detach().cpu()
    if isinstance(value, dict):
        return {key: _cpu_tree(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return type(value)(_cpu_tree(item) for item in value)
    if isinstance(value, float) and not math.isfinite(value):
        raise ValueError("Nonfinite checkpoint scalar")
    if value is not None and not isinstance(value, (str, float, int, bool)):
        raise ValueError(f"Unsupported checkpoint value: {type(value).__name__}")
    return value


def _sync_directory(root: Path) -> None:
    if os.name == "posix":
        fd = os.open(root, os.O_RDONLY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def save(root: Path, state: dict, context: dict) -> dict:
    """Commit a complete snapshot before replacing the pointer; retain only latest."""
    import torch
    epoch = state["epoch"]
    if type(epoch) is not int or not 1 <= epoch <= 1000:
        raise ValueError("Invalid completed epoch")
    name = f"resume-epoch-{epoch:06d}.pt"
    path, temporary = root / name, root / f".{name}.tmp"
    if path.exists():
        raise ValueError("Epoch snapshot already exists")
    previous = None
    index = root / "resume.json"
    if index.exists():
        previous = json.loads(index.read_text())["epoch"]
        if type(previous) is not int or not 1 <= previous < epoch:
            raise ValueError("Epoch recovery pointer must advance monotonically")
    payload = _cpu_tree({"schema_version": SCHEMA, "context": context, "state": state})
    with temporary.open("xb") as stream:
        torch.save(payload, stream)
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temporary, path)
    with path.open("rb") as stream:
        digest = hashlib.file_digest(stream, "sha256").hexdigest()
    record = {"schema_version": SCHEMA, "epoch": epoch, "path": name,
              "sha256": digest, "context": context}
    index_temp = root / ".resume.json.tmp"
    with index_temp.open("w") as stream:
        json.dump(record, stream, indent=2, sort_keys=True, allow_nan=False)
        stream.write("\n")
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(index_temp, index)
    _sync_directory(root)
    # Only our prior epoch artifact in this fresh run directory is removed.
    if type(previous) is int and 1 <= previous < epoch:
        (root / f"resume-epoch-{previous:06d}.pt").unlink(missing_ok=True)
    return record


def load(root: Path, context: dict) -> tuple[dict, dict]:
    """The pointer and payload must agree; restricted loading never falls back."""
    import torch
    root = root.resolve()
    index = root / "resume.json"
    if index.stat().st_size > 64 * 1024:
        raise ValueError("Resume pointer exceeds 64 KiB")
    data = index.read_bytes()
    record = json.loads(data)
    epoch = record.get("epoch")
    if (record.get("schema_version") != SCHEMA or record.get("context") != context
            or type(epoch) is not int or not 1 <= epoch <= 1000
            or record.get("path") != f"resume-epoch-{epoch:06d}.pt"):
        raise ValueError("Resume context, schema or epoch mismatch")
    path = (root / record["path"]).resolve()
    if not path.is_relative_to(root):
        raise ValueError("Resume artifact escapes run directory")
    payload = torch.load(io.BytesIO(checked_bytes(path, record["sha256"], 512 * 1024 * 1024)),
                         map_location="cpu", weights_only=True)
    if (not isinstance(payload, dict) or payload.get("schema_version") != SCHEMA
            or payload.get("context") != context or payload.get("state", {}).get("epoch") != epoch):
        raise ValueError("Resume payload disagrees with pointer")
    state = _cpu_tree(payload["state"])
    required = {"epoch", "model", "best_model", "optimizer", "random", "history",
                "best", "best_epoch", "initial_validation", "best_validation"}
    if set(state) != required or [row["epoch"] for row in state["history"]] != list(range(1, epoch + 1)):
        raise ValueError("Incomplete resume state/history")
    if type(state["best_epoch"]) is not int or not 1 <= state["best_epoch"] <= epoch:
        raise ValueError("Invalid best checkpoint epoch")
    return state, {"resume_index_sha256": hashlib.sha256(data).hexdigest(),
                   "resume_checkpoint_sha256": record["sha256"], "completed_epoch": epoch}


def random_state(generator, device: str) -> dict:
    import random
    import numpy as np
    import torch
    array = np.random.get_state()
    return {"python": random.getstate(), "numpy": [array[0], array[1].tolist(), *array[2:]],
            "torch_cpu": torch.get_rng_state(), "shuffle": generator.get_state(),
            "torch_cuda": torch.cuda.get_rng_state_all() if device == "cuda" else []}


def restore_random(state: dict, generator, device: str) -> None:
    import random
    import numpy as np
    import torch
    random.setstate(state["python"])
    array = state["numpy"]
    np.random.set_state((array[0], np.asarray(array[1], dtype=np.uint32), *array[2:]))
    torch.set_rng_state(state["torch_cpu"])
    generator.set_state(state["shuffle"])
    if device == "cuda":
        if len(state["torch_cuda"]) != torch.cuda.device_count():
            raise ValueError("CUDA RNG device count mismatch")
        torch.cuda.set_rng_state_all(state["torch_cuda"])
    elif state["torch_cuda"]:
        raise ValueError("Cannot resume CUDA randomness on CPU")
