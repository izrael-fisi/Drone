"""Evaluation contracts and actual reference inference with explicitly synthetic labels."""

import copy
import hashlib
import json
import os
from pathlib import Path

import cv2
import numpy as np
import pytest

pytest.importorskip("torch")
from vision_nav.neural import bearinguav_evaluation as evaluation
from test_bearinguav_reference import sample


def write_json(path, value):
    path.write_text(json.dumps(value, indent=2) + "\n")
    return {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}


def fixture(root):
    root.mkdir(parents=True, exist_ok=True)
    membership = {"schema_version": evaluation.MEMBERSHIP_SCHEMA,
                  "training_release_id": "synthetic-training-population",
                  "provenance": "test fixture, not original pretrained model training data",
                  "records": [{"sample_id": "train-1", "session_id": "train-session",
                               "scene_id": "train-scene", "uav_sha256": "1" * 64}]}
    manifest = {"schema_version": evaluation.SCHEMA, "dataset_release_id": "synthetic-evaluation-v1",
                "label_protocol": "synthetic block coordinates, not flight ground truth",
                "input_kind": "synthetic", "split": "test", "require_scene_disjoint": True,
                "context_policy": "known_context", "records": [],
                "training_membership": write_json(root / "training.json", membership)}
    for index in range(3):
        folder = root / f"sample-{index}"
        request = sample(folder)
        request.update(sample_id=f"eval-{index}", dataset_release_id=manifest["dataset_release_id"], split="test")
        image = cv2.imread(str(folder / "uav.png"))
        image[0, 0, 0] = index
        cv2.imwrite(str(folder / "uav.png"), image)
        request["patches"]["uav"]["sha256"] = hashlib.sha256((folder / "uav.png").read_bytes()).hexdigest()
        member = write_json(folder / "sample.json", request)
        member["path"] = f"sample-{index}/sample.json"
        manifest["records"].append({"sample": member, "session_id": f"eval-session-{index}",
            "scene_id": "eval-scene", "context_match": index < 2,
            "ground_truth": {"position_xy": [0, 0], "direction_xy": [1, 0],
                "coordinate_frame": "official_normalized_satellite_block_not_ENU",
                "provenance": "synthetic label for metric plumbing only",
                "metric_error_basis": {"scope": "local_linear_error_conversion_not_global_pose",
                    "provenance": "synthetic anisotropic axes", "en_m_per_normalized_xy": [[2, 0], [0, 3]]}}
                if index < 2 else None})
    write_json(root / "manifest.json", manifest)
    return manifest


def test_error_math_wraparound_and_missing_direction():
    row = {"context_match": True, "ground_truth": {"position_xy": [0, 0], "direction_xy": [1, 0],
        "coordinate_frame": "official_normalized_satellite_block_not_ENU", "provenance": "fixture",
        "metric_error_basis": {"scope": "local_linear_error_conversion_not_global_pose", "provenance": "fixture",
                               "en_m_per_normalized_xy": [[2, 0], [0, 3]]}}}
    a = evaluation.score(np.array([3, 4, 0, -2]), row)
    assert a["position_error_normalized"] == 5
    assert a["position_error_m"] == pytest.approx(np.sqrt(180))
    assert a["direction_error_deg"] == pytest.approx(90)
    angle = np.deg2rad(359)
    b = evaluation.score(np.array([0, 0, np.cos(angle), np.sin(angle)]), row)
    assert b["direction_error_deg"] == pytest.approx(1)
    c = evaluation.score(np.zeros(4), row)
    assert c["invalid_direction"] and c["direction_error_deg"] is None
    wrong = evaluation.score(np.ones(4), {"context_match": False, "ground_truth": None})
    report = evaluation.summarize([a, b, c, wrong])
    assert report["sample_count"] == 4 and report["matching_context_count"] == 3
    assert report["direction_error_deg"]["count"] == 2
    assert report["wrong_context_finite_output_count"] == 1
    assert report["false_global_fix_rate"] is None


@pytest.mark.parametrize("fault", ["session", "sample_id", "image", "scene", "mixed_kind",
    "wrong_split", "duplicate", "sample_checksum", "image_checksum", "label_frame", "label_nan",
    "direction_not_unit", "metric_singular", "wrong_context_target"])
def test_audit_rejects_leakage_corruption_and_invalid_labels(tmp_path, fault):
    manifest = fixture(tmp_path)
    row = manifest["records"][0]
    if fault in {"session", "scene"}:
        row[f"{fault}_id"] = f"train-{fault}"
    elif fault == "duplicate":
        manifest["records"].append(copy.deepcopy(row))
    elif fault == "sample_checksum":
        row["sample"]["sha256"] = "0" * 64
    elif fault in {"sample_id", "image", "mixed_kind", "wrong_split", "image_checksum"}:
        path = tmp_path / row["sample"]["path"]
        request = json.loads(path.read_text())
        if fault == "sample_id":
            request["sample_id"] = "train-1"
        elif fault == "mixed_kind":
            request["input_kind"] = "recorded"
        elif fault == "wrong_split":
            request["split"] = "validation"
        elif fault == "image_checksum":
            request["patches"]["uav"]["sha256"] = "0" * 64
        else:
            training = json.loads((tmp_path / "training.json").read_text())
            training["records"][0]["uav_sha256"] = request["patches"]["uav"]["sha256"]
            manifest["training_membership"] = write_json(tmp_path / "training.json", training)
        updated = write_json(path, request)
        row["sample"]["sha256"] = updated["sha256"]
    elif fault == "label_frame":
        row["ground_truth"]["coordinate_frame"] = "ENU"
    elif fault == "label_nan":
        row["ground_truth"]["position_xy"] = [float("nan"), 0]
    elif fault == "direction_not_unit":
        row["ground_truth"]["direction_xy"] = [0, 0]
    elif fault == "metric_singular":
        row["ground_truth"]["metric_error_basis"]["en_m_per_normalized_xy"] = [[1, 0], [1, 0]]
    elif fault == "wrong_context_target":
        row["context_match"] = False
    write_json(tmp_path / "manifest.json", manifest)
    with pytest.raises(ValueError):
        evaluation.audit(tmp_path / "manifest.json")


def test_scene_overlap_is_reported_when_session_only_policy_requested(tmp_path):
    manifest = fixture(tmp_path)
    manifest["require_scene_disjoint"] = False
    manifest["records"][0]["scene_id"] = "train-scene"
    write_json(tmp_path / "manifest.json", manifest)
    checked = evaluation.audit(tmp_path / "manifest.json")
    assert checked["scene_overlap"] == ["train-scene"]


def test_paired_wrong_context_allowed_without_duplicate_positive_weighting(tmp_path):
    manifest = fixture(tmp_path)
    wrong = manifest["records"][2]
    wrong["session_id"] = manifest["records"][0]["session_id"]
    query = tmp_path / "sample-2/uav.png"
    query.write_bytes((tmp_path / "sample-0/uav.png").read_bytes())
    path = tmp_path / "sample-2/sample.json"
    request = json.loads(path.read_text())
    request["patches"]["uav"]["sha256"] = hashlib.sha256(query.read_bytes()).hexdigest()
    wrong["sample"]["sha256"] = write_json(path, request)["sha256"]
    write_json(tmp_path / "manifest.json", manifest)
    with pytest.raises(ValueError, match="query/context"):
        evaluation.audit(tmp_path / "manifest.json")
    different_map = tmp_path / "sample-2/p1.png"
    cv2.imwrite(str(different_map), np.zeros((64, 64, 3), np.uint8))
    request["patches"]["p1"]["sha256"] = hashlib.sha256(different_map.read_bytes()).hexdigest()
    wrong["sample"]["sha256"] = write_json(path, request)["sha256"]
    write_json(tmp_path / "manifest.json", manifest)
    assert evaluation.audit(tmp_path / "manifest.json")["distinct_query_count"] == 2
    wrong["context_match"] = True
    wrong["ground_truth"] = copy.deepcopy(manifest["records"][0]["ground_truth"])
    write_json(tmp_path / "manifest.json", manifest)
    with pytest.raises(ValueError, match="Repeated positive"):
        evaluation.audit(tmp_path / "manifest.json")


def test_labels_do_not_reach_inference_and_model_loaded_once(tmp_path, monkeypatch):
    import torch
    manifest = fixture(tmp_path)
    loads, tensors = [], []
    def load(*args):
        loads.append(args)
        return object()
    def predict(model, tensor, device):
        assert isinstance(tensor, torch.Tensor)
        tensors.append(tensor.clone())
        return np.array([.1, .2, 1., 0.]), [1., 1.]
    monkeypatch.setattr(evaluation.reference, "load_model", load)
    monkeypatch.setattr(evaluation.reference, "predict", predict)
    args = (tmp_path / "manifest.json", tmp_path, tmp_path, tmp_path)
    first = evaluation.run(*args)
    assert len(loads) == 1 and len(tensors) == 3
    manifest["records"][0]["ground_truth"]["position_xy"] = [4, 5]
    write_json(tmp_path / "manifest.json", manifest)
    second = evaluation.run(*args)
    assert all(torch.equal(tensors[i], tensors[i + 3]) for i in range(3))
    assert [r["prediction"] for r in first["samples"]] == [r["prediction"] for r in second["samples"]]
    assert first["metrics"]["position_error_normalized"] != second["metrics"]["position_error_normalized"]


def test_actual_checkpoint_batch(tmp_path):
    # Provisioning is explicit; this test never downloads model artifacts.
    source, weights = os.getenv("VISION_NAV_BEARING_SOURCE"), os.getenv("VISION_NAV_BEARING_WEIGHTS")
    if not source or not weights:
        pytest.skip("Official model paths not provisioned")
    fixture(tmp_path)
    report = evaluation.run(tmp_path / "manifest.json", Path(source), Path(weights) / "best_model.pth",
                            Path(weights) / "training_configure.json")
    assert report["metrics"]["sample_count"] == 3
    assert report["metrics"]["position_error_m"]["count"] == 2
    assert report["metrics"]["wrong_context_finite_output_count"] == 1
    assert report["input_kind"] == "synthetic" and report["position_authority"] is False
    assert report["navigation_mode"] == "NO_GLOBAL_FIX" and report["px4_external_vision"] == "disabled"


def test_training_pair_membership_exports_verified_queries_only(tmp_path):
    from vision_nav.neural.training_map_pairs import MAP_PAIR_SCHEMA
    request = sample(tmp_path)
    frame = tmp_path / "uav.png"
    pair = {"schema_version": MAP_PAIR_SCHEMA, "sample": {"sample_id": "actual-train-1",
        "capture_session_id": "flight-1", "frame_path": str(frame),
        "frame_sha256": request["patches"]["uav"]["sha256"]}}
    split = tmp_path / "train.jsonl"
    split.write_text(json.dumps(pair) + "\n")
    manifest = {"schema_version": MAP_PAIR_SCHEMA, "policy": "offline_training_only",
                "splits": {"train": {"path": split.name, "sha256": hashlib.sha256(split.read_bytes()).hexdigest()},
                           "validation": {"path": "not-used.jsonl"}}}
    write_json(tmp_path / "manifest.json", manifest)
    write_json(tmp_path / "scenes.json", {"flight-1": "field-west"})
    result = evaluation.membership_from_training_pairs(tmp_path, tmp_path / "scenes.json", "train-release-1")
    assert result["records"] == [{"sample_id": "actual-train-1", "session_id": "flight-1",
                                   "scene_id": "field-west", "uav_sha256": pair["sample"]["frame_sha256"]}]
    write_json(tmp_path / "scenes.json", {})
    with pytest.raises(ValueError, match="flight-1"):
        evaluation.membership_from_training_pairs(tmp_path, tmp_path / "scenes.json", "train-release-1")
    write_json(tmp_path / "scenes.json", {"flight-1": "field-west"})
    frame.write_bytes(b"changed capture")
    with pytest.raises(ValueError, match="checksum mismatch"):
        evaluation.membership_from_training_pairs(tmp_path, tmp_path / "scenes.json", "train-release-1")
    split.write_text("{}\n")
    with pytest.raises(ValueError, match="checksum mismatch"):
        evaluation.membership_from_training_pairs(tmp_path, tmp_path / "scenes.json", "train-release-1")
