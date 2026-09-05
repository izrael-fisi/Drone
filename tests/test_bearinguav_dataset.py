import json
import hashlib
import os
from pathlib import Path
import shutil

import cv2
import numpy as np
import pytest

pytest.importorskip("torch")
from vision_nav.neural import bearinguav_dataset as dataset
from vision_nav.neural import bearinguav_evaluation as evaluation
from vision_nav.neural.crossview_dataset import build_crossview_dataset
from test_map_conditioned_student import _windows


def inputs(root):
    root.mkdir(parents=True, exist_ok=True)
    windows, registry, _ = _windows(root)
    crossview = root / "crossview"
    build_crossview_dataset([windows], registry, crossview, allow_legacy_timing=True)
    scenes = root / "scenes.json"
    scenes.write_text(json.dumps({"train": "field-a", "validation": "field-b"}))
    return crossview, scenes


def test_row_major_patches_and_quarter_block_scale():
    block = np.zeros((64, 64, 3), np.uint8)
    block[:32, :32] = 10
    block[:32, 32:] = 20
    block[32:, :32] = 30
    block[32:, 32:] = 40
    assert [int(p[0, 0, 0]) for p in dataset.split_block(block)] == [10, 20, 30, 40]
    assert dataset.normalized_position([.5, .5]) == [0, 0]
    assert dataset.normalized_position([.75, .25]) == [1, -1]
    assert dataset.normalized_position([0, 0]) == [-2, -2]
    with pytest.raises(ValueError):
        dataset.normalized_position([1, .5])
    with pytest.raises(ValueError):
        dataset.split_block(np.zeros((65, 64, 3), np.uint8))


def test_converter_is_portable_oracle_context_and_does_not_invent_heading(tmp_path):
    source, scenes = inputs(tmp_path / "input")
    output = tmp_path / "bearing"
    report = dataset.build(source, scenes, output, release_id="fixture-v1", input_kind="synthetic",
                           image_size=64, allow_legacy_timing=True)
    assert report["train_case_count"] == report["validation_case_count"] == 2
    assert report["position_authority"] is False
    assert report["heading_labels_qualified"] is False
    for name, digest in report["checksums_sha256"].items():
        assert hashlib.sha256((output / name).read_bytes()).hexdigest() == digest
    checked = evaluation.audit(output / "evaluation.json")
    assert checked["distinct_query_count"] == 1
    positive = next(i for i in checked["checked"] if i["row"]["context_match"])
    assert positive["row"]["ground_truth"]["direction_xy"] is None
    assert "yaw" in positive["row"]["ground_truth"]["direction_missing_reason"]
    np.testing.assert_allclose(positive["row"]["ground_truth"]["position_xy"], [0, 0], atol=1e-6)
    for item in checked["checked"]:
        assert item["sample"]["conversion"]["convention"] == dataset.CONVENTION
        assert cv2.imread(str(output / item["sample"]["patches"]["uav"]["path"])).shape == (64, 64, 3)
    moved = tmp_path / "moved"
    output.rename(moved)
    shutil.rmtree(source)
    assert evaluation.audit(moved / "evaluation.json")["distinct_query_count"] == 1
    with pytest.raises(ValueError):
        dataset.build(source, scenes, moved, release_id="fixture-v1", input_kind="synthetic")


def test_legacy_timing_gate_and_transactional_failure(tmp_path):
    source, scenes = inputs(tmp_path / "input")
    with pytest.raises(ValueError, match="Legacy timing"):
        dataset.build(source, scenes, tmp_path / "out", release_id="fixture-v1", input_kind="synthetic")
    assert not (tmp_path / "out").exists()
    with pytest.raises(ValueError):
        dataset.build(source, scenes, tmp_path / "out", release_id="fixture-v1", input_kind="synthetic",
                      image_size=1024, allow_legacy_timing=True)
    assert not list(tmp_path.glob(".bearing-build-*"))


def test_raw_query_corruption_and_leakage_survive_preprocessing(tmp_path):
    from test_bearinguav_evaluation import write_json
    source, scenes = inputs(tmp_path / "input")
    output = tmp_path / "bearing"
    dataset.build(source, scenes, output, release_id="fixture-v1", input_kind="synthetic",
                  image_size=32, allow_legacy_timing=True)
    manifest = json.loads((output / "evaluation.json").read_text())
    request = json.loads((output / manifest["records"][0]["sample"]["path"]).read_text())
    membership = json.loads((output / "training-membership.json").read_text())
    membership["records"][0]["uav_sha256"] = request["source_query"]["sha256"]
    manifest["training_membership"] = write_json(output / "training-membership.json", membership)
    write_json(output / "evaluation.json", manifest)
    with pytest.raises(ValueError, match="Train/evaluation overlap"):
        evaluation.audit(output / "evaluation.json")
    (output / request["source_query"]["path"]).write_bytes(b"corrupt raw")
    with pytest.raises(ValueError, match="checksum mismatch"):
        evaluation.audit(output / "evaluation.json")


def test_actual_checkpoint_evaluates_converted_existing_pipeline(tmp_path):
    source, weights = os.getenv("VISION_NAV_BEARING_SOURCE"), os.getenv("VISION_NAV_BEARING_WEIGHTS")
    if not source or not weights:
        pytest.skip("Official checkpoint not provisioned")
    crossview, scenes = inputs(tmp_path / "input")
    output = tmp_path / "bearing"
    dataset.build(crossview, scenes, output, release_id="fixture-v1", input_kind="synthetic",
                  image_size=64, allow_legacy_timing=True)
    report = evaluation.run(output / "evaluation.json", Path(source), Path(weights) / "best_model.pth",
                            Path(weights) / "training_configure.json")
    assert report["metrics"]["sample_count"] == 2
    assert report["metrics"]["direction_label_count"] == 0
    assert report["metrics"]["direction_error_deg"]["mean"] is None
    assert report["metrics"]["position_error_m"]["count"] == 0
    assert report["metrics"]["wrong_context_finite_output_count"] == 1
    assert report["context_policy"] == "oracle_context"
