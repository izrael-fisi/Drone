import ast
import os
from pathlib import Path

import pytest

from vision_nav.neural import video2bev_loss as adapter
from vision_nav.neural.video2bev_workspace import prepare


def test_extracts_only_reviewed_functions_without_executing_trainer(tmp_path, monkeypatch):
    pytest.importorskip("torch")
    monkeypatch.setattr(adapter, "validate", lambda _: {})
    root = tmp_path / "workspace"
    root.mkdir()
    (root / adapter.TRAINER).write_text(
        "raise RuntimeError('top-level trainer must not run')\n" +
        "\n".join(f"def {name}(*args, **kwargs):\n    return 7\n" for name in adapter.FUNCTIONS))
    function, provenance = adapter.load_matching_loss(root, tmp_path / "logs", ["0001", "0002"])
    assert function() == 7
    assert provenance["function_bodies_modified"] is False
    assert len(provenance["trainer_sha256"]) == 64
    with pytest.raises(ValueError, match="new"):
        adapter.load_matching_loss(root, tmp_path / "logs", ["0001", "0002"])


@pytest.mark.parametrize("names", [["one", "two"], ["0001", "0001"], ["0001"], ["0001", "\u0662"]])
def test_rejects_incompatible_diagnostic_class_ids(tmp_path, monkeypatch, names):
    pytest.importorskip("torch")
    monkeypatch.setattr(adapter, "validate", lambda _: {})
    with pytest.raises(ValueError):
        adapter.load_matching_loss(tmp_path, tmp_path / "logs", names)


def test_real_upstream_negative_selection_and_repeat_order(tmp_path):
    source = os.environ.get("VISION_NAV_VIDEO2BEV_SOURCE")
    if not source:
        pytest.skip("Explicit pinned Video2BEV source required")
    torch = pytest.importorskip("torch")
    root = tmp_path / "workspace"
    prepare(Path(source), root)
    names = ["0001", "0002", "0003", "0004"]
    loss, _ = adapter.load_matching_loss(root, tmp_path / "logs", names)
    # Compare compiled definitions with the untouched source AST, independently
    # checking the adapter didn't substitute its own sampling implementation.
    tree = ast.parse((root / adapter.TRAINER).read_text())
    original = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
                    and node.name == "compose_neg_pair_from_similarity")
    isolated = {}
    exec(compile(ast.Module(body=[original], type_ignores=[]), "reference", "exec"), isolated)
    sampler = loss.__globals__["compose_neg_pair_from_similarity"]
    assert sampler.__code__.co_code == isolated[sampler.__name__].__code__.co_code
    labels = torch.arange(4)
    candidates = torch.arange(4, dtype=torch.float32).reshape(4, 1, 1)
    similarity = torch.ones(4, 4) - torch.eye(4)
    common = dict(label=labels, id_names=names, epoch=0, iteration=0, anchor="bev", gallery_="satellite")
    top3 = sampler(similarity, candidates, sample_number=3, **common).reshape(4, 3)
    for query, chosen in enumerate(top3):
        assert set(chosen.tolist()) == set(range(4)) - {query}
    # Supplemental generated negatives may legitimately keep the class ID.
    self_negatives = sampler(torch.eye(4), candidates, sd_data=True, sample_number=1, **common)
    assert torch.equal(self_negatives, candidates)
    repeated = loss.__globals__["repeat_tensor"](candidates, 3).flatten()
    assert repeated.tolist() == [0, 0, 0, 1, 1, 1, 2, 2, 2, 3, 3, 3]
