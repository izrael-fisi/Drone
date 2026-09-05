import hashlib
import json
import os
from pathlib import Path

import pytest

torch = pytest.importorskip("torch")
from vision_nav.neural import bearinguav_dataset as dataset
from vision_nav.neural import bearinguav_evaluation as evaluation
from vision_nav.neural import bearinguav_reference as reference
from vision_nav.neural import bearinguav_training as training
from test_bearinguav_dataset import inputs


@pytest.fixture
def converted(tmp_path):
    source, scenes = inputs(tmp_path / "source")
    output = tmp_path / "converted"
    dataset.build(source, scenes, output, release_id="synthetic-bearing-training-v1",
                  input_kind="synthetic", image_size=64, allow_legacy_timing=True)
    return output


def rewrite(root, name, doc):
    path = root / name
    path.write_text(json.dumps(doc))
    manifest = json.loads((root / "manifest.json").read_text())
    digest = hashlib.sha256(path.read_bytes()).hexdigest()
    manifest["checksums_sha256"][name] = digest
    (root / "manifest.json").write_text(json.dumps(manifest))
    return {"path": name, "sha256": digest}


def test_audit_binds_actual_train_population(converted):
    result = training.audit_dataset(converted)
    assert len(result["train"]) == 2
    assert result["manifest"]["input_kind"] == "synthetic"
    membership = json.loads((converted / "training-membership.json").read_text())
    membership["records"][0]["session_id"] = "concealed-session"
    item = rewrite(converted, "training-membership.json", membership)
    validation = json.loads((converted / "evaluation.json").read_text())
    validation["training_membership"] = item
    rewrite(converted, "evaluation.json", validation)
    with pytest.raises(ValueError, match="actual training cases"):
        training.audit_dataset(converted)


def test_audit_checks_training_split_not_only_validation(converted):
    cases = json.loads((converted / "train-cases.json").read_text())
    item = cases["records"][0]["sample"]
    sample = json.loads((converted / item["path"]).read_text())
    sample["split"] = "validation"
    cases["records"][0]["sample"] = rewrite(converted, item["path"], sample)
    rewrite(converted, "train-cases.json", cases)
    with pytest.raises(ValueError, match="split or convention"):
        training.audit_dataset(converted)


def test_audit_forbids_test_split_checkpoint_selection(converted):
    validation = json.loads((converted / "evaluation.json").read_text())
    validation["split"] = "test"
    rewrite(converted, "evaluation.json", validation)
    with pytest.raises(ValueError, match="never test"):
        training.audit_dataset(converted)


def test_audit_rejects_conflicting_validation_policy(converted):
    validation = json.loads((converted / "evaluation.json").read_text())
    validation["context_policy"] = "known_context"
    rewrite(converted, "evaluation.json", validation)
    with pytest.raises(ValueError, match="same dataset release and convention"):
        training.audit_dataset(converted)


def test_audit_rejects_corruption_before_loading_model(converted, monkeypatch, tmp_path):
    cases = json.loads((converted / "train-cases.json").read_text())
    sample = json.loads((converted / cases["records"][0]["sample"]["path"]).read_text())
    (converted / sample["patches"]["p1"]["path"]).write_bytes(b"bad")
    monkeypatch.setattr(reference, "load_model", lambda *a, **k: pytest.fail("Model loaded before audit"))
    with pytest.raises(ValueError, match="checksum mismatch"):
        training.train(converted, Path("missing"), Path("missing"), Path("missing"), tmp_path / "out")
    assert not (tmp_path / "out").exists()


@pytest.mark.parametrize("options", [{"epochs": 0}, {"batch_size": 0}, {"learning_rate": float("nan")},
    {"learning_rate": True}, {"seed": -1}, {"threads": 0}])
def test_invalid_hyperparameters_fail_before_model_load(tmp_path, options):
    with pytest.raises(ValueError, match="hyperparameters"):
        training.train(tmp_path, tmp_path, tmp_path, tmp_path, tmp_path / "out", **options)


def test_failed_training_retains_evidence_not_a_completed_run(converted, tmp_path, monkeypatch):
    class InvalidModel(torch.nn.Module):
        def __init__(self):
            super().__init__()
            self.bias = torch.nn.Parameter(torch.zeros(2))

        def forward(self, value):
            return self.bias.unsqueeze(0) * float("nan"), self.bias.unsqueeze(0)

    monkeypatch.setattr(reference, "load_model", lambda *a, **k: InvalidModel())
    with pytest.raises(ValueError, match="non-finite"):
        training.train(converted, Path("unused"), Path("unused"), Path("unused"), tmp_path / "out")
    assert not (tmp_path / "out").exists()
    failed = list(tmp_path.glob(".bearing-train-*/FAILED.json"))
    assert len(failed) == 1
    assert json.loads(failed[0].read_text())["status"] == "failed"
    assert not failed[0].with_name("training_run.json").exists()


def test_actual_checkpoint_can_train_save_reload_and_evaluate(converted, tmp_path, monkeypatch):
    source, weights = os.getenv("VISION_NAV_BEARING_SOURCE"), os.getenv("VISION_NAV_BEARING_WEIGHTS")
    if not source or not weights:
        pytest.skip("Official checkpoint not provisioned")
    source, weights = Path(source), Path(weights)
    output = tmp_path / "run"
    report = training.train(converted, source, weights / "best_model.pth",
                            weights / "training_configure.json", output, batch_size=1, epochs=2)
    assert report["train_positive_count"] == 1
    assert report["excluded_wrong_context_training_count"] == 1
    assert report["direction_supervision"].startswith("disabled")
    assert report["position_authority"] is False
    assert report["official_paper_reproduction"] is False
    assert report["best_epoch"] in (1, 2)
    for name, digest in report["artifacts_sha256"].items():
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == digest
    before = torch.load(weights / "best_model.pth", map_location="cpu", weights_only=True)["model_state_dict"]
    after = torch.load(output / "best_model.pth", map_location="cpu", weights_only=True)["model_state_dict"]
    assert before.keys() == after.keys()
    assert any(not torch.equal(before[key], after[key]) for key in before)
    evaluated = evaluation.run(converted / "evaluation.json", source, None,
                               weights / "training_configure.json", training_run=output)
    assert evaluated["checkpoint_origin"] == "local_research_finetune"
    assert evaluated["checkpoint_sha256"] == report["artifacts_sha256"]["best_model.pth"]
    saved_metrics = json.loads((output / "best-validation.json").read_text())["metrics"]
    assert evaluated["metrics"] == saved_metrics
    assert saved_metrics["direction_label_count"] == 0
    assert saved_metrics["wrong_context_finite_output_count"] == 1
    repeat_output = tmp_path / "repeat"
    training.train(converted, source, weights / "best_model.pth", weights / "training_configure.json",
                   repeat_output, batch_size=1, epochs=2)
    repeated = torch.load(repeat_output / "best_model.pth", map_location="cpu", weights_only=True)["model_state_dict"]
    assert all(torch.equal(after[key], repeated[key]) for key in after)
    original_evaluate = training.evaluate_model
    calls = 0

    def interrupt_after_epoch_one(*args):
        nonlocal calls
        calls += 1
        if calls == 3:
            raise RuntimeError("Injected second-epoch validation interruption")
        return original_evaluate(*args)

    with monkeypatch.context() as patch:
        patch.setattr(training, "evaluate_model", interrupt_after_epoch_one)
        with pytest.raises(RuntimeError, match="Injected"):
            training.train(converted, source, weights / "best_model.pth", weights / "training_configure.json",
                           tmp_path / "interrupted", batch_size=1, epochs=2)
    failed = next(tmp_path.glob(".bearing-train-*/FAILED.json")).parent
    assert json.loads((failed / "resume.json").read_text())["epoch"] == 1
    resumed_output = tmp_path / "resumed"
    resumed_report = training.train(converted, source, weights / "best_model.pth", weights / "training_configure.json",
                                    resumed_output, batch_size=1, epochs=2, resume_from=failed)
    resumed = torch.load(resumed_output / "best_model.pth", map_location="cpu", weights_only=True)["model_state_dict"]
    assert all(torch.equal(after[key], resumed[key]) for key in after)
    uninterrupted_last = torch.load(output / "last_model.pth", map_location="cpu", weights_only=True)["model_state_dict"]
    resumed_last = torch.load(resumed_output / "last_model.pth", map_location="cpu", weights_only=True)["model_state_dict"]
    assert all(torch.equal(uninterrupted_last[key], resumed_last[key]) for key in uninterrupted_last)
    assert json.loads((resumed_output / "history.json").read_text()) == json.loads((output / "history.json").read_text())
    assert resumed_report["epochs_executed_this_run"] == 1
    assert resumed_report["resume_provenance"]["completed_epoch"] == 1
    with pytest.raises(ValueError, match="context"):
        training.train(converted, source, weights / "best_model.pth", weights / "training_configure.json",
                       tmp_path / "different-settings", batch_size=2, epochs=3, resume_from=output)
    with pytest.raises(ValueError, match="total epochs"):
        training.train(converted, source, weights / "best_model.pth", weights / "training_configure.json",
                       tmp_path / "already-complete", batch_size=1, epochs=2, resume_from=output)
    with pytest.raises(ValueError, match="membership mismatch"):
        training.checkpoint_from_run(output, membership_sha256="0" * 64)
    with pytest.raises(ValueError, match="Output must be new"):
        training.train(converted, source, weights / "best_model.pth", weights / "training_configure.json", output)
    (output / "best_model.pth").write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="checksum mismatch"):
        evaluation.run(converted / "evaluation.json", source, None,
                       weights / "training_configure.json", training_run=output)
