import json
import random

import numpy as np
import pytest

torch = pytest.importorskip("torch")
from vision_nav.neural import bearinguav_checkpoint as checkpoint


def state(epoch=1):
    generator = torch.Generator().manual_seed(7)
    return {"epoch": epoch, "model": {"weight": torch.ones(2)},
            "best_model": {"weight": torch.ones(2)}, "optimizer": {"state": {}, "param_groups": []},
            "random": checkpoint.random_state(generator, "cpu"),
            "history": [{"epoch": i} for i in range(1, epoch + 1)],
            "best": .1, "best_epoch": 1, "initial_validation": {}, "best_validation": {}}


def test_save_load_is_hash_bound_and_retires_prior_epoch(tmp_path):
    context = {"dataset_sha256": "fixture", "learning_rate": .001}
    first = checkpoint.save(tmp_path, state(), context)
    second = checkpoint.save(tmp_path, state(2), context)
    assert not (tmp_path / first["path"]).exists()
    restored, provenance = checkpoint.load(tmp_path, context)
    assert restored["epoch"] == 2
    assert provenance["resume_checkpoint_sha256"] == second["sha256"]
    with pytest.raises(ValueError, match="context"):
        checkpoint.load(tmp_path, {**context, "learning_rate": .01})
    (tmp_path / second["path"]).write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum mismatch"):
        checkpoint.load(tmp_path, context)


def test_interrupted_pointer_commit_preserves_previous_complete_snapshot(tmp_path, monkeypatch):
    first = checkpoint.save(tmp_path, state(), {})
    original_replace = checkpoint.os.replace

    def interrupt(source, destination):
        if destination.name == "resume.json":
            raise OSError("Injected failure before pointer commit")
        return original_replace(source, destination)

    monkeypatch.setattr(checkpoint.os, "replace", interrupt)
    with pytest.raises(OSError, match="Injected failure"):
        checkpoint.save(tmp_path, state(2), {})
    restored, _ = checkpoint.load(tmp_path, {})
    assert restored["epoch"] == 1
    assert (tmp_path / first["path"]).exists()


def test_rng_recovery_includes_shuffle_python_numpy_and_dropout():
    random.seed(12)
    np.random.seed(12)
    torch.manual_seed(12)
    generator = torch.Generator().manual_seed(12)
    saved = checkpoint.random_state(generator, "cpu")

    def draw():
        return (random.random(), np.random.random(4), torch.randperm(7, generator=generator),
                torch.nn.functional.dropout(torch.ones(30), p=.5, training=True))

    first = draw()
    draw()
    checkpoint.restore_random(saved, generator, "cpu")
    repeated = draw()
    assert first[0] == repeated[0]
    np.testing.assert_array_equal(first[1], repeated[1])
    assert torch.equal(first[2], repeated[2]) and torch.equal(first[3], repeated[3])


def test_nonfinite_snapshot_never_replaces_pointer(tmp_path):
    checkpoint.save(tmp_path, state(), {})
    bad = state(2)
    bad["model"]["weight"][0] = float("nan")
    with pytest.raises(ValueError, match="Nonfinite"):
        checkpoint.save(tmp_path, bad, {})
    assert checkpoint.load(tmp_path, {})[0]["epoch"] == 1


def test_resume_pointer_cannot_escape_run_directory(tmp_path):
    record = checkpoint.save(tmp_path, state(), {})
    record["path"] = "../other.pt"
    (tmp_path / "resume.json").write_text(json.dumps(record))
    with pytest.raises(ValueError, match="mismatch"):
        checkpoint.load(tmp_path, {})


def test_snapshot_pointer_cannot_rewind(tmp_path):
    checkpoint.save(tmp_path, state(2), {})
    with pytest.raises(ValueError, match="monotonically"):
        checkpoint.save(tmp_path, state(1), {})
    assert checkpoint.load(tmp_path, {})[0]["epoch"] == 2
