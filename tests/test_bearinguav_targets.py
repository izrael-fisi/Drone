import copy
import json
import os
from pathlib import Path

import numpy as np
import pytest

torch = pytest.importorskip("torch")
from vision_nav.neural import bearinguav_targets as targets
from vision_nav.neural import bearinguav_dataset as converter
from vision_nav.neural import map_conditioned_student as student
from vision_nav.neural import map_conditioned_export as exporter
from test_bearinguav_dataset import inputs


@pytest.fixture
def package(tmp_path):
    source, scenes = inputs(tmp_path / "input")
    converted = tmp_path / "converted"
    converter.build(source, scenes, converted, release_id="fixture", input_kind="synthetic",
                    image_size=64, allow_legacy_timing=True)
    return source, converted, tmp_path / "targets.json"


def fake_export(package, monkeypatch):
    source, converted, output = package
    monkeypatch.setattr(targets.reference, "load_model", lambda *a, **kw: object())
    monkeypatch.setattr(targets.reference, "predict", lambda *a, **kw: (np.array([.4, -.8, 0., 1.]), [1., 1.]))
    report = targets.build(source, converted, Path("unused"), Path("unused"), output, checkpoint=Path("unused"))
    return report


def test_coordinate_mapping_does_not_clamp_outside_or_invent_heading():
    assert targets.point_target([1., -1.], positive=True)["target_uv"] == [.75, .25]
    assert targets.point_target([2., 0.], positive=True)["status"] == "withheld"
    assert targets.point_target([-2., -2.], positive=True)["target_uv"] == [0., 0.]
    assert targets.point_target(None, positive=False)["status"] == "excluded"
    with pytest.raises(ValueError):
        targets.point_target([np.nan, 0.], positive=True)
    with pytest.raises(ValueError):
        targets.point_target(["0", "1"], positive=True)
    with pytest.raises(ValueError):
        targets.point_target([True, False], positive=True)


def test_targets_are_train_only_portable_and_bound(package, monkeypatch):
    report = fake_export(package, monkeypatch)
    source, _, path = package
    assert report["usable_count"] == 1
    assert len(report["records"]) == 2
    assert {r["window_id"] for r in report["records"]} == {"train-window"}
    provenance, points = targets.load_targets(path, source)
    np.testing.assert_allclose(points["train-window"][0], [.6, .3])
    assert 1 not in points["train-window"]
    assert provenance["targets_sha256"]
    moved = path.with_name("moved.json")
    path.rename(moved)
    assert targets.load_targets(moved, source)[1] == points


@pytest.mark.parametrize("fault", ["dataset", "validation", "negative", "order", "query", "geometry", "duplicate", "uv", "count", "teacher"])
def test_target_tampering_rejected(package, monkeypatch, fault):
    report = fake_export(package, monkeypatch)
    source, _, path = package
    if fault == "dataset":
        report["source_manifest_sha256"] = "0" * 64
    elif fault == "validation":
        report["records"][0]["window_id"] = "validation-window"
    elif fault == "negative":
        report["records"][1].update(status="usable", target_uv=[.5, .5])
    elif fault == "order":
        report["records"][0]["candidate_index"] = 1
    elif fault == "query":
        report["records"][0]["query_sha256"] = "0" * 64
    elif fault == "geometry":
        report["records"][0]["pixel_geometry"]["affine"][0] = 999
    elif fault == "duplicate":
        report["records"][1] = copy.deepcopy(report["records"][0])
    elif fault == "uv":
        report["records"][0]["target_uv"] = [.5, .5]
    elif fault == "count":
        report["usable_count"] = 0
    elif fault == "teacher":
        report["teacher"]["source_revision"] = "unknown"
    path.write_text(json.dumps(report))
    with pytest.raises(ValueError):
        targets.load_targets(path, source)


def test_auxiliary_gradients_do_not_train_no_match_or_negative_candidates():
    logits = torch.zeros((2, 17), requires_grad=True)
    loss = targets.point_loss(logits, {0: [.8, .2]}, 4)
    loss.backward()
    assert logits.grad[0, :-1].abs().sum() > 0
    assert logits.grad[0, -1] == 0
    assert logits.grad[1].abs().sum() == 0


@pytest.mark.parametrize("weight,path", [(0, "targets.json"), (.2, None), (-1, None), (float("nan"), "x"), (True, "x")])
def test_distillation_requires_explicit_valid_weight(tmp_path, weight, path):
    with pytest.raises(ValueError, match="explicit weight"):
        student.train_map_conditioned_student(tmp_path, tmp_path / "out", config=student.MapConditionedStudentConfig(),
                                             teacher_targets_path=path, teacher_weight=weight)


def test_real_teacher_to_student_training_and_onnx_parity(package, tmp_path):
    source_path, weights_path = os.getenv("VISION_NAV_BEARING_SOURCE"), os.getenv("VISION_NAV_BEARING_WEIGHTS")
    if not source_path or not weights_path:
        pytest.skip("Explicit pinned teacher source and weights required")
    pytest.importorskip("onnx")
    pytest.importorskip("onnxruntime")
    source, converted, output = package
    weights = Path(weights_path)
    report = targets.build(source, converted, Path(source_path), weights / "training_configure.json",
                           output, checkpoint=weights / "best_model.pth", threads=1)
    assert report["usable_count"] == 1
    config = student.MapConditionedStudentConfig(image_size=64, grid_size=4, feature_dim=16, window_size=2, heading_bins=8)
    distilled = student.train_map_conditioned_student(source, tmp_path / "distilled", config=config, epochs=2,
                                                      teacher_targets_path=output, teacher_weight=.25)
    baseline = student.train_map_conditioned_student(source, tmp_path / "baseline", config=config, epochs=2)
    assert distilled["teacher_distillation"] == "bearinguav_point_auxiliary"
    assert all(row["train"]["teacher_point_count"] == 1 for row in distilled["history"])
    assert all(row["validation"]["teacher_point_count"] == 0 for row in distilled["history"])
    assert all(row["validation"]["teacher_point_mean_loss"] is None for row in distilled["history"])
    assert baseline["teacher_distillation"] == "not_performed"
    a = torch.load(tmp_path / "distilled/map_conditioned_student.pt", weights_only=True)["state_dict"]
    b = torch.load(tmp_path / "baseline/map_conditioned_student.pt", weights_only=True)["state_dict"]
    assert any(not torch.equal(a[key], b[key]) for key in a)
    exported = exporter.export_map_conditioned_student(tmp_path / "distilled/map_conditioned_student.pt", source, tmp_path / "export")
    assert exported["status"] == "parity_passed"
    assert exported["teacher_provenance"] == distilled["teacher_provenance"]
    assert exported["distillation_loss_sha256"] == distilled["distillation_loss_sha256"]
    assert exported["field_ready"] is False
    with np.load(tmp_path / "export/sample_inputs.npz") as archive:
        sample = dict(archive)
    evidence = exporter.MapConditionedOnnxProbe(tmp_path / "export").run(
        sample, tile_id="unseen", map_release_id="unseen@1", calibration_id="cam@1")
    assert evidence["position_authority"] is False
    assert evidence["px4_external_vision"] == "disabled"
    before = output.read_bytes()
    with pytest.raises(ValueError, match="new"):
        targets.build(source, converted, Path(source_path), weights / "training_configure.json", output,
                      checkpoint=weights / "best_model.pth")
    assert output.read_bytes() == before
