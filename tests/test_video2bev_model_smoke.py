import os
from pathlib import Path
import sys
from types import ModuleType

import pytest

from vision_nav.neural import video2bev_model_smoke as smoke
from vision_nav.neural.video2bev_workspace import prepare, validate


@pytest.mark.parametrize("kwargs", [
    {"threads": 0}, {"threads": 17}, {"threads": True}, {"threads": 1.5},
    {"seed": -1}, {"seed": 2**32}, {"seed": False}, {"seed": "0"},
])
def test_options_rejected_before_workspace_loading(tmp_path, kwargs):
    with pytest.raises(ValueError):
        smoke.run(tmp_path / "missing", **kwargs)


def test_existing_import_namespace_is_not_overwritten(tmp_path, monkeypatch):
    monkeypatch.setattr(smoke, "validate", lambda _: {})
    existing = ModuleType("backbone")
    monkeypatch.setitem(sys.modules, "backbone", existing)
    with pytest.raises(ValueError, match="fresh process"):
        smoke.run(tmp_path)
    assert sys.modules["backbone"] is existing


def test_real_model_execution_is_repeatable_and_never_loads_weights(tmp_path, monkeypatch):
    source = os.environ.get("VISION_NAV_VIDEO2BEV_SOURCE")
    if not source:
        pytest.skip("Explicit pinned Video2BEV source required")
    torch = pytest.importorskip("torch")
    pytest.importorskip("torchvision")
    import urllib.request

    root = tmp_path / "workspace"
    manifest = prepare(Path(source), root)

    def forbidden(*args, **kwargs):
        pytest.fail("Random-initialization smoke must not load weights or download data")

    monkeypatch.setattr(torch, "load", forbidden)
    monkeypatch.setattr(torch.hub, "load", forbidden)
    monkeypatch.setattr(torch.hub, "load_state_dict_from_url", forbidden)
    monkeypatch.setattr(urllib.request, "urlopen", forbidden)
    rng = torch.get_rng_state().clone()
    threads = torch.get_num_threads()
    first = smoke.run(root)
    assert torch.equal(rng, torch.get_rng_state())
    assert torch.get_num_threads() == threads
    assert not any(name == "backbone" or name.startswith("backbone.")
                   or name == "_video2bev_smoke_model" for name in sys.modules)
    second = smoke.run(root)
    for key in ("tensors", "loss", "negative_sampling_log", "gradient_norm_before_clipping", "encoder_state_sha256",
                "matching_head_state_sha256", "fusion_state_sha256"):
        assert first[key] == second[key]
    assert first["status"] == "passed"
    assert first["parameters"] > first["trainable_parameters"] > 0
    assert first["tensors"]["logits"]["shape"] == [2, 10, 2]
    assert first["tensors"]["satellite_tokens"]["shape"] == [2, 257, 768]
    assert first["encoder_unchanged"] and first["fusion_updated"] and first["matching_head_updated"]
    assert first["encoder_training_mode"] is False
    assert first["upstream_matching_loss_executed"] is True
    assert first["loss_provenance"]["function_bodies_modified"] is False
    assert first["negative_sampling_log"].count("cur_batch_id:") == 8
    assert first["negative_sampling_log"].count("negative pair id:") == 16
    for key in ("checkpoint_loaded", "checkpoint_compatibility_verified", "official_training_reproduced",
                "geographic_accuracy_verified", "default_swin_base_verified", "position_authority"):
        assert first[key] is False
    assert first["px4_external_vision"] == "disabled"
    assert validate(root) == manifest
    # A source change must be rejected before importing or executing the model.
    (root / "model.py").write_text("raise RuntimeError('must not execute')\n")
    with pytest.raises(ValueError, match="changed"):
        smoke.run(root)


def test_failed_import_restores_namespace_and_threads(tmp_path, monkeypatch):
    torch = pytest.importorskip("torch")
    monkeypatch.setattr(smoke, "validate", lambda _: {})
    (tmp_path / "model.py").write_text("raise RuntimeError('import failed')\n")
    threads = torch.get_num_threads()
    with pytest.raises(RuntimeError, match="import failed"):
        smoke.run(tmp_path)
    assert torch.get_num_threads() == threads
    assert "backbone" not in sys.modules and "_video2bev_smoke_model" not in sys.modules
