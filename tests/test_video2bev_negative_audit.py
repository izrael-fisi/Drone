from pathlib import Path

from PIL import Image
import pytest

from vision_nav.neural import video2bev_preflight as preflight


def populate(root):
    for view in ("satellite", "bev"):
        for name in ("0001", "0002"):
            folder = root / view / name
            folder.mkdir(parents=True)
            Image.new("RGB", (32, 32), "red").save(folder / "negative.jpeg")
    return ["0001", "0002"]


def test_all_eligible_images_hashed_and_order_independent(tmp_path):
    classes = populate(tmp_path)
    first = preflight.audit_negatives(tmp_path, classes)
    assert first["complete"]
    assert first["image_counts"] == {"satellite": 2, "bev": 2}
    assert preflight.audit_negatives(tmp_path, classes[::-1]) == first
    # Not sampled by upstream glob, so these don't change the inventory.
    for filename in ("ignored.jpg", ".hidden.jpeg", "ignored.JPEG"):
        (tmp_path / "bev/0001" / filename).write_bytes(b"not an image")
    assert preflight.audit_negatives(tmp_path, classes) == first
    Image.new("RGB", (32, 32), "blue").save(tmp_path / "bev/0001/negative.jpeg")
    assert preflight.audit_negatives(tmp_path, classes)["inventory_sha256"] != first["inventory_sha256"]


@pytest.mark.parametrize("failure", ["corrupt", "directory", "external", "tiny", "empty", "missing"])
def test_rejects_bad_negative_even_when_another_sample_is_valid(tmp_path, failure):
    root = tmp_path / "negatives"
    classes = populate(root)
    folder = root / "satellite/0001"
    path = folder / "second.jpeg"
    if failure == "corrupt":
        path.write_bytes(b"corrupt")
    elif failure == "directory":
        path.mkdir()
    elif failure == "external":
        outside = tmp_path / "external.jpeg"
        Image.new("RGB", (32, 32)).save(outside)
        path.symlink_to(outside)
    elif failure == "tiny":
        Image.new("RGB", (2, 2)).save(path)
    elif failure == "empty":
        (folder / "negative.jpeg").unlink()
    elif failure == "missing":
        (folder / "negative.jpeg").unlink()
        folder.rmdir()
    report = preflight.audit_negatives(root, classes)
    assert not report["complete"] and report["errors"]
    assert report["inventory_sha256"] is None


def test_audit_limit_never_claims_complete(tmp_path, monkeypatch):
    classes = populate(tmp_path)
    monkeypatch.setattr(preflight, "MAX_AUDIT_IMAGES", 2)
    result = preflight.audit_negatives(tmp_path, classes)
    assert result["complete"] is False
    assert "negative_image_audit_limit_exceeded" in result["errors"]
    assert result["inventory_sha256"] is None


@pytest.mark.parametrize("classes", [[], [".."], ["x/y"], ["x\\y"], ["a", "a"]])
def test_invalid_class_inputs(tmp_path, classes):
    with pytest.raises(ValueError):
        preflight.audit_negatives(tmp_path, classes)
