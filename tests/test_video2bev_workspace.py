import ast
import json
import os
from pathlib import Path

from PIL import Image
import pytest

from vision_nav.neural import video2bev_workspace as workspace
from vision_nav.neural.video2bev_preflight import MODALITIES


def synthetic_data(root):
    data, negatives = root / "data", root / "negatives"
    for class_id in ("0001", "0002"):
        for modality in MODALITIES:
            folder = data / modality / class_id
            folder.mkdir(parents=True)
            if modality != "google":
                Image.new("RGB", (48, 48), "red" if class_id == "0001" else "blue").save(folder / "image.jpeg")
        for modality in ("satellite", "bev"):
            folder = negatives / modality / class_id
            folder.mkdir(parents=True)
            Image.new("RGB", (48, 48), "green").save(folder / "negative.jpeg")
    return data, negatives


def test_unused_import_patch_preserves_unrelated_statements():
    text = "import os\nfrom folder import ImageFolder\nfrom random_erasing import RandomErasing\nparser = make_parser()\nopt = parser.parse_args()\nloss = unchanged()\n"
    patched = workspace.patch_source(workspace.TRAINER, text)
    assert "loss = unchanged()" in patched
    assert "--sd-negative-root" in patched
    assert "from folder" not in patched
    with pytest.raises(ValueError, match="unused"):
        workspace.patch_source(workspace.TRAINER, text + "x = ImageFolder()\n")
    with pytest.raises(ValueError):
        workspace.patch_source(workspace.TRAINER, patched)


def test_ast_replacement_preserves_unicode_byte_offsets():
    text = 'label = "caf\u00e9"; self.sd_neg_sample_apth = "/author/path"\n'
    patched = workspace.patch_source(workspace.LOADER, text)
    assert 'label = "caf\u00e9";' in patched
    assert "kwargs['sd_negative_root']" in patched
    ast.parse(patched)


def test_both_factory_loaders_receive_explicit_root():
    text = "first = Dataloader_University(root, transforms=x)\nsecond = Dataloader_University(root, transforms=x)\n"
    patched = workspace.patch_source(workspace.FACTORY, text)
    assert patched.count("sd_negative_root=opt.sd_negative_root") == 2
    with pytest.raises(ValueError):
        workspace.patch_source(workspace.FACTORY, text.splitlines()[0])


def test_real_pinned_workspace_and_upstream_data_execution(tmp_path, monkeypatch):
    source = os.environ.get("VISION_NAV_VIDEO2BEV_SOURCE")
    if not source:
        pytest.skip("Explicit pinned Video2BEV second-stage source required")
    pytest.importorskip("torch")
    pytest.importorskip("torchvision")
    source = Path(source)
    old_model = (source / "model.py").read_bytes()
    old_loader = (source / workspace.LOADER).read_bytes()
    output = tmp_path / "prepared"
    result = workspace.prepare(source, output)
    assert set(result["changed_files"]) == {workspace.TRAINER, workspace.LOADER, workspace.FACTORY}
    assert (output / "model.py").read_bytes() == old_model
    assert (source / workspace.LOADER).read_bytes() == old_loader
    assert workspace._git_head(source) == workspace.COMMIT
    assert workspace.validate(output)["model_execution_verified"] is False
    data, negatives = synthetic_data(tmp_path)
    smoke = workspace.smoke_data(output, data, negatives, input_kind="synthetic")
    assert smoke["status"] == "passed"
    assert smoke["class_count"] == 2
    assert [2, 3, 256, 256] in smoke["batch_tensor_shapes"]
    assert len(smoke["batch_tensors"]) == len(smoke["batch_tensor_shapes"])
    assert all(len(row["sha256"]) == 64 for row in smoke["batch_tensors"])
    assert smoke["position_authority"] is False
    assert smoke["all_negative_files_audited"] is True
    assert smoke["negative_audit"]["complete"] is True
    assert smoke["negative_audit"]["image_counts"] == {"satellite": 2, "bev": 2}
    # Neither missing negatives nor stale prepared code can silently fall back.
    with pytest.raises(ValueError, match="negative"):
        workspace.smoke_data(output, data, tmp_path / "missing", input_kind="synthetic")
    corrupt = negatives / "satellite/0001/second.jpeg"
    corrupt.write_bytes(b"corrupt, even if the random sampler would miss it")
    with monkeypatch.context() as context:
        def forbidden_import(*args, **kwargs):
            pytest.fail("Bad negatives must be rejected before importing the loader")
        context.setattr(workspace.importlib, "import_module", forbidden_import)
        with pytest.raises(ValueError, match="negative_image"):
            workspace.smoke_data(output, data, negatives, input_kind="synthetic")
    corrupt.unlink()
    (output / "injected.py").write_text("# unexpected\n")
    with pytest.raises(ValueError, match="Unexpected"):
        workspace.validate(output)
    (output / "injected.py").unlink()
    (output / "model.py").write_text("# altered\n")
    with pytest.raises(ValueError, match="changed"):
        workspace.validate(output)
    with pytest.raises(ValueError, match="new"):
        workspace.prepare(source, output)
