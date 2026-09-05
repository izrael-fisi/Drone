import copy
import subprocess
import sys

import pytest
import yaml

from vision_nav.camera_extrinsics import create_extrinsics_document, load_camera_to_body


def arguments():
    return dict(extrinsic_id="mount-v1", camera_calibration_id="camera-v1", translation_m=(0., 0., .1),
                rotation_quat_xyzw=(0., 0., 0., 1.), measurement_method="documented_jig_measurement",
                translation_sigma_m=.002, rotation_sigma_deg=.5)


@pytest.mark.parametrize("key", ["translation_sigma_m", "rotation_sigma_deg"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1., True, "1"])
def test_creation_rejects_invalid_uncertainty(key, value):
    args = arguments()
    args[key] = value
    with pytest.raises(ValueError):
        create_extrinsics_document(**args)


@pytest.mark.parametrize("key", ["translation_sigma_m", "rotation_sigma_deg"])
@pytest.mark.parametrize("value", [float("nan"), float("inf"), -1., True, "1"])
def test_loading_rejects_invalid_uncertainty(tmp_path, key, value):
    document = create_extrinsics_document(**arguments())
    document["uncertainty"][key] = value
    path = tmp_path / "extrinsics.yaml"
    path.write_text(yaml.safe_dump(document))
    with pytest.raises(ValueError):
        load_camera_to_body(path)


@pytest.mark.parametrize("key", ["extrinsic_id", "camera_calibration_id", "measurement_method"])
@pytest.mark.parametrize("value", [None, "", "  ", 1])
def test_measured_release_requires_real_identity(tmp_path, key, value):
    document = create_extrinsics_document(**arguments())
    document[key] = value
    path = tmp_path / "extrinsics.yaml"
    path.write_text(yaml.safe_dump(document))
    with pytest.raises(ValueError):
        load_camera_to_body(path)


@pytest.mark.parametrize("key,value", [("translation_m", (0, float("nan"), 0)),
    ("translation_m", (0, float("inf"), 0)), ("translation_m", (0, 0)),
    ("rotation_quat_xyzw", (0, 0, 1)), ("rotation_quat_xyzw", (0, 0, 0, 2)),
    ("rotation_quat_xyzw", (0, 0, 0, True))])
def test_creation_rejects_invalid_transform(key, value):
    args = arguments()
    args[key] = value
    with pytest.raises(ValueError):
        create_extrinsics_document(**args)


def test_round_trip_and_placeholder_stays_unmeasured(tmp_path):
    document = create_extrinsics_document(**arguments())
    path = tmp_path / "extrinsics.yaml"
    path.write_text(yaml.safe_dump(document))
    before = copy.deepcopy(document)
    loaded = load_camera_to_body(path)
    assert loaded.status == "measured" and loaded.extrinsic_id == "mount-v1"
    assert loaded.uncertainty["rotation_sigma_deg"] == .5
    assert yaml.safe_load(path.read_text()) == before
    document.pop("extrinsic_status")
    path.write_text(yaml.safe_dump(document))
    assert load_camera_to_body(path).status != "measured"


def test_cli_will_not_overwrite_calibration_release(tmp_path):
    path = tmp_path / "calibration.yaml"
    path.write_text("existing measured evidence")
    result = subprocess.run([sys.executable, "-m", "vision_nav.camera_extrinsics", "--output", str(path),
        "--extrinsic-id", "new", "--camera-calibration-id", "camera", "--translation-m", "0", "0", "0",
        "--rotation-quat-xyzw", "0", "0", "0", "1", "--measurement-method", "test",
        "--translation-sigma-m", "0.01", "--rotation-sigma-deg", "1"], capture_output=True, text=True)
    assert result.returncode != 0
    assert "instead of overwriting" in result.stderr
    assert path.read_text() == "existing measured evidence"
