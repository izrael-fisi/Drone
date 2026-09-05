"""Reference checkpoint compatibility, not an aircraft accuracy benchmark."""

import copy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys

import cv2
import numpy as np
import pytest

torch = pytest.importorskip("torch")
from vision_nav.neural import bearinguav_reference as reference


def sample(root, size=64):
    root.mkdir(parents=True, exist_ok=True)
    request = {"schema_version": reference.REQUEST_SCHEMA, "sample_id": "synthetic-five-patch",
               "dataset_release_id": "synthetic-no-geography-v1", "split": "compatibility_only",
               "input_kind": "synthetic", "patches": {}}
    rng = np.random.default_rng(17)
    for name in reference.PATCH_ORDER:
        path = root / f"{name}.png"
        assert cv2.imwrite(str(path), rng.integers(0, 256, (size, size, 3), dtype=np.uint8))
        request["patches"][name] = {"path": path.name, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    return request


def test_preprocessing_and_patch_order_match_official_test_transform(tmp_path):
    from PIL import Image
    from torchvision import transforms
    request = sample(tmp_path)
    before = copy.deepcopy(request)
    actual, provenance = reference.load_patches(request, tmp_path)
    transform = transforms.Compose([transforms.ToTensor(),
        transforms.Normalize([0.485, 0.456, 0.406], [0.229, 0.224, 0.225])])
    expected = torch.stack([transform(Image.fromarray(cv2.cvtColor(
        cv2.imread(str(tmp_path / request["patches"][name]["path"])), cv2.COLOR_BGR2RGB)))
        for name in reference.PATCH_ORDER]).unsqueeze(0)
    assert torch.equal(actual, expected)
    assert [row["role"] for row in provenance] == list(reference.PATCH_ORDER)
    assert request == before


@pytest.mark.parametrize("fault", ["missing_patch", "extra_patch", "wrong_hash", "escape",
    "missing_provenance", "unspecified_kind", "shape", "corrupt", "small"])
def test_invalid_sample_rejected_before_model_loading(tmp_path, fault):
    request = sample(tmp_path)
    if fault == "missing_patch":
        del request["patches"]["uav"]
    elif fault == "extra_patch":
        request["patches"]["p5"] = request["patches"]["p1"]
    elif fault == "wrong_hash":
        request["patches"]["p1"]["sha256"] = "0" * 64
    elif fault == "escape":
        request["patches"]["p1"]["path"] = "../escape.png"
    elif fault == "missing_provenance":
        del request["dataset_release_id"]
    elif fault == "unspecified_kind":
        request["input_kind"] = "unknown"
    else:
        path = tmp_path / "p1.png"
        if fault == "corrupt":
            path.write_bytes(b"not an image")
        else:
            cv2.imwrite(str(path), np.zeros((32 if fault == "shape" else 16, 64, 3), np.uint8))
        request["patches"]["p1"]["sha256"] = hashlib.sha256(path.read_bytes()).hexdigest()
    with pytest.raises(ValueError):
        reference.load_patches(request, tmp_path)


def test_lock_matches_adapter():
    root = Path(__file__).resolve().parents[1]
    lock = json.loads((root / "research/upstreams.lock.json").read_text())
    project = next(row for row in lock["projects"] if row["name"] == "bearinguav")
    assert project["commit"] == reference.SOURCE_COMMIT
    assert project["license_status"] != "verified"
    assert project["runtime_scope"] == "cloud_teacher"


def test_checkpoint_size_guard(tmp_path):
    path = tmp_path / "weights"
    path.write_bytes(b"too large")
    with pytest.raises(ValueError, match="size limit"):
        reference.checked_bytes(path, "0" * 64, 1)


def test_source_revision_dirty_and_shadow_source_rejected(tmp_path, monkeypatch):
    answers = {("rev-parse", "--show-toplevel"): str(tmp_path),
               ("rev-parse", "HEAD"): reference.SOURCE_COMMIT,
               ("diff", "HEAD", "--"): "", ("ls-files",): "tracked.py"}
    monkeypatch.setattr(subprocess, "check_output", lambda command, **kw: answers[tuple(command[3:])])
    reference.verify_source(tmp_path)
    answers[("rev-parse", "HEAD")] = "0" * 40
    with pytest.raises(ValueError, match="revision"):
        reference.verify_source(tmp_path)
    answers[("rev-parse", "HEAD")] = reference.SOURCE_COMMIT
    answers[("diff", "HEAD", "--")] = "dirty"
    with pytest.raises(ValueError, match="tracked contents"):
        reference.verify_source(tmp_path)
    answers[("diff", "HEAD", "--")] = ""
    (tmp_path / "injected.py").write_text("raise AssertionError('must not import')")
    with pytest.raises(ValueError, match="Unexpected Python"):
        reference.verify_source(tmp_path)


@pytest.fixture(scope="module")
def provisioned():
    source, weights = os.getenv("VISION_NAV_BEARING_SOURCE"), os.getenv("VISION_NAV_BEARING_WEIGHTS")
    if not source or not weights:
        pytest.skip("Explicit official source and checkpoint paths required for real-model test")
    return Path(source), Path(weights)


def test_real_checkpoint_cpu_no_download_deterministic_and_no_pose_authority(provisioned, tmp_path, monkeypatch):
    source, weights = provisioned
    monkeypatch.setattr(torch.hub, "download_url_to_file", lambda *a, **k: pytest.fail("Unexpected download"))
    request = sample(tmp_path)
    result = reference.evaluate(request, tmp_path, source,
        weights / "best_model.pth", weights / "training_configure.json")
    from PIL import Image
    from cvphr.utils.utils_transform import transform_pipeline3
    actual, _ = reference.load_patches(request, tmp_path)
    official = torch.stack([transform_pipeline3()(Image.fromarray(cv2.cvtColor(
        cv2.imread(str(tmp_path / request["patches"][name]["path"])), cv2.COLOR_BGR2RGB)))
        for name in reference.PATCH_ORDER]).unsqueeze(0)
    assert torch.equal(actual, official)
    assert result["repeat_exact"] is True
    assert result["parameter_count"] == 17134600
    assert result["tensor_shape"] == [1, 5, 3, 64, 64]
    assert result["input_kind"] == "synthetic"
    assert result["navigation_mode"] == "NO_GLOBAL_FIX"
    assert result["position_authority"] is False
    assert result["px4_external_vision"] == "disabled"
    assert result["metric_pose"] is result["covariance"] is result["heading_confidence"] is None
    assert result["weights_license_status"] == "unverified"
    assert np.isfinite(result["relative_position_xy"]).all()


def test_tampered_checkpoint_and_config_rejected(provisioned, tmp_path):
    source, weights = provisioned
    corrupt = tmp_path / "corrupt"
    corrupt.write_bytes(b"untrusted checkpoint bytes")
    with pytest.raises(ValueError, match="checksum mismatch"):
        reference.load_model(source, corrupt, weights / "training_configure.json", "cpu")
    with pytest.raises(ValueError, match="checksum mismatch"):
        reference.load_model(source, weights / "best_model.pth", corrupt, "cpu")


def test_cli_preserves_existing_evidence(tmp_path):
    output = tmp_path / "result.json"
    output.write_text("keep me")
    command = [sys.executable, "-m", "vision_nav.neural.bearinguav_reference",
        "--sample", "missing.json", "--source", "missing", "--checkpoint", "missing",
        "--configuration", "missing", "--output", str(output)]
    result = subprocess.run(command, capture_output=True, text=True)
    assert result.returncode == 2
    assert "immutable" in result.stderr
    assert output.read_text() == "keep me"
