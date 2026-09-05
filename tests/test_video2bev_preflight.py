from pathlib import Path
import subprocess

from PIL import Image
import pytest

from vision_nav.neural.video2bev_preflight import MODALITIES, SOURCE_FILES, audit_dataset, inspect_workflow
from vision_nav.neural.upstream_training import _git_head


def dataset(root):
    for name in MODALITIES:
        directory = root / name / "0001"
        directory.mkdir(parents=True)
        if name != "google":
            Image.new("RGB", (32, 32), "red").save(directory / "frame.jpg")


def test_raw_frames_alone_cannot_pass_as_rendered_bev(tmp_path):
    root = tmp_path / "data"
    (root / "drone").mkdir(parents=True)
    report = audit_dataset(root)
    assert report["complete"] is False
    assert "missing_rendered_bev_classes" in report["errors"]


def test_complete_class_layout_allows_google_fallback_and_hashes_pixels(tmp_path):
    dataset(tmp_path)
    before = audit_dataset(tmp_path)
    assert before["complete"] is True
    assert before["image_counts"] == {"satellite": 1, "street": 1, "drone": 1, "google": 0, "bev": 1}
    Image.new("RGB", (32, 32), "blue").save(tmp_path / "bev/0001/frame.jpg")
    after = audit_dataset(tmp_path)
    assert after["inventory_sha256"] != before["inventory_sha256"]


@pytest.mark.parametrize("fault", ["missing_class", "empty_street", "corrupt", "nested", "symlink", "small"])
def test_loader_failures_are_caught_before_training(tmp_path, fault):
    root = tmp_path / "data"
    dataset(root)
    path = root / "street/0001/frame.jpg"
    if fault == "missing_class":
        (root / "street/0001").rename(root / "street/0002")
    elif fault == "empty_street":
        path.unlink()
    elif fault == "corrupt":
        path.write_bytes(b"not an image")
    elif fault == "nested":
        (root / "bev/0001/nested").mkdir()
    elif fault == "symlink":
        path.rename(tmp_path / "outside.jpg")
        path.symlink_to(tmp_path / "outside.jpg")
    else:
        Image.new("RGB", (1, 1)).save(path)
    assert audit_dataset(root)["complete"] is False


def test_second_stage_exposes_missing_imports_and_hardcoded_negatives(tmp_path):
    checkout, data = tmp_path / "source", tmp_path / "data"
    dataset(data)
    for name in SOURCE_FILES:
        path = checkout / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# fixture\n")
    (checkout / "folder.py").unlink()
    (checkout / "tool/Dataloader_University.py").write_text(
        "class Loader:\n    def __init__(self):\n        self.sd_neg_sample_apth = '/author/negative'\n")
    result = inspect_workflow(checkout, data, second_stage=True)
    assert "missing_upstream_module:folder.py" in result["errors"]
    assert "upstream_hardcoded_synthetic_negative_root_requires_adapter" in result["errors"]
    assert result["requires_precomputed_3dgs_bev"] is True
    assert result["model_execution_verified"] is False


def test_first_stage_requires_separate_backbone_asset(tmp_path):
    data = tmp_path / "data"
    dataset(data)
    result = inspect_workflow(tmp_path / "missing", data, second_stage=False)
    assert "missing_first_stage_backbone_weights" in result["errors"]


def test_pinned_head_does_not_allow_modified_or_shadow_python(tmp_path):
    def git(*args):
        return subprocess.check_output(["git", "-C", str(tmp_path), *args], text=True).strip()
    git("init", "-q")
    script = tmp_path / "model.py"
    script.write_text("# pinned\n")
    git("add", "model.py")
    git("-c", "user.name=Test", "-c", "user.email=test@example.invalid", "commit", "-qm", "fixture")
    assert _git_head(tmp_path) == git("rev-parse", "HEAD")
    script.write_text("# modified\n")
    with pytest.raises(ValueError, match="contents changed"):
        _git_head(tmp_path)
    script.write_text("# pinned\n")
    (tmp_path / "injected.py").write_text("# shadow module\n")
    with pytest.raises(ValueError, match="Untracked"):
        _git_head(tmp_path)
