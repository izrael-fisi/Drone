"""Load reviewed upstream loss functions without running its top-level trainer."""

from __future__ import annotations

import ast
from pathlib import Path

from .video2bev_workspace import TRAINER, _sha, validate


FUNCTIONS = ("write_txt", "swap_first_element", "compose_neg_pair_from_similarity",
             "repeat_tensor", "get_matching_loss_two_view")


def load_matching_loss(workspace: Path, log_directory: Path, class_names: list[str]):
    """Preserve function bodies; inject only their explicit runtime globals.

    Caller must use fusion_cls_loss=False and matching_label_smooth=False. This
    adapter does not execute optional classification/label-smoothing branches.
    """
    import os
    import numpy as np
    import torch
    import torch.nn.functional as functional

    workspace = workspace.resolve()
    validate(workspace)
    if not 2 <= len(class_names) <= 16 or len(set(class_names)) != len(class_names):
        raise ValueError("Expected 2-16 distinct numeric upstream class IDs")
    if any(not isinstance(name, str) or not name.isascii() or not name.isdecimal()
           or len(name) > 8 for name in class_names):
        raise ValueError("Upstream diagnostic logging requires numeric class IDs")
    log_directory = log_directory.resolve()
    if log_directory.exists() or log_directory.is_relative_to(workspace):
        raise ValueError("Loss log directory must be new and outside the workspace")
    source = workspace / TRAINER
    tree = ast.parse(source.read_text())
    selected = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name in FUNCTIONS]
    if len(selected) != len(FUNCTIONS) or {node.name for node in selected} != set(FUNCTIONS):
        raise ValueError("Expected exactly the reviewed upstream loss functions")
    if any(node.decorator_list for node in selected):
        raise ValueError("Unexpected decorators on upstream loss functions")
    log_directory.mkdir(parents=True)
    namespace = {"torch": torch, "np": np, "os": os, "F": functional,
                 "txt_path": str(log_directory / "training.txt"), "class_names": class_names}
    # Compile only unchanged function definitions. Module-level argument parsing,
    # dataset setup, checkpoint loading, CUDA selection and training never run.
    exec(compile(ast.Module(body=selected, type_ignores=[]), str(source), "exec"), namespace)
    return namespace["get_matching_loss_two_view"], {
        "trainer_sha256": _sha(source), "adapter_sha256": _sha(Path(__file__)),
        "functions": list(FUNCTIONS), "function_bodies_modified": False,
    }
