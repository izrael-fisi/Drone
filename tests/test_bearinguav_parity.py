import copy
import json

import pytest

from vision_nav.neural import bearinguav_parity as parity


def report():
    result = {key: "same-identity" for key in parity.IDENTITY_FIELDS}
    result.update(schema_version="vision_nav_bearinguav_reference_v1",
                  status="research_inference_only", repeat_exact=True,
                  position_authority=False, navigation_mode="NO_GLOBAL_FIX",
                  px4_external_vision="disabled", field_ready=False,
                  metric_pose=None, covariance=None, heading_confidence=None,
                  relative_position_xy=[0.0, -0.3], direction_xy=[0.2, 0.8],
                  environment=dict(torch="2.1.2", torchvision="0.16.2", numpy="1.26.4",
                                   opencv="4.10.0", device="cpu", threads=2, python="3.11.16"))
    return result


def test_changed_environment_with_small_roundoff_passes_without_authority():
    old = report()
    new = copy.deepcopy(old)
    new["environment"]["torch"] = "2.13.0"
    new["relative_position_xy"][0] = 1e-7
    result = parity.compare(old, new)
    assert result["status"] == "passed"
    assert result["position_authority"] is result["field_ready"] is False
    assert result["scope"] == "single_sample_inference_compatibility_only"
    assert result["heads"]["relative_position_xy"]["maximum_absolute_delta"] == 1e-7


def test_output_drift_fails_instead_of_widening_tolerance():
    old = report()
    new = copy.deepcopy(old)
    new["direction_xy"][0] += 0.01
    assert parity.compare(old, new)["status"] == "failed"


@pytest.mark.parametrize("key", parity.IDENTITY_FIELDS)
def test_mismatched_or_missing_experiment_rejected(key):
    old = report()
    new = copy.deepcopy(old)
    new[key] = "changed"
    with pytest.raises(ValueError, match="same model/input"):
        parity.compare(old, new)
    del new[key]
    with pytest.raises(ValueError, match="Missing comparison"):
        parity.compare(old, new)


@pytest.mark.parametrize("key,value", [
    ("relative_position_xy", [float("nan"), 0]),
    ("relative_position_xy", [float("inf"), 0]),
    ("direction_xy", [True, 0]), ("direction_xy", [1]),
    ("direction_xy", "12"), ("repeat_exact", False),
    ("position_authority", True), ("covariance", [1]), ("environment", {}),
])
def test_invalid_or_authoritative_report_rejected(key, value):
    new = report()
    new[key] = value
    with pytest.raises(ValueError):
        parity.compare(report(), new)


def test_cli_hashes_inputs_and_preserves_existing_output(tmp_path, monkeypatch):
    paths = [tmp_path / name for name in ("old.json", "new.json", "out.json")]
    for path in paths[:2]:
        path.write_text(json.dumps(report()))
    monkeypatch.setattr("sys.argv", ["parity", "--reference", str(paths[0]),
                        "--candidate", str(paths[1]), "--output", str(paths[2])])
    parity.main()
    result = json.loads(paths[2].read_text())
    assert result["reference_report_sha256"] == result["candidate_report_sha256"]
    before = paths[2].read_bytes()
    with pytest.raises(SystemExit):
        parity.main()
    assert paths[2].read_bytes() == before


def test_oversized_report_rejected(tmp_path):
    path = tmp_path / "large.json"
    path.write_bytes(b" " * (1024 * 1024 + 1))
    with pytest.raises(ValueError, match="1 MiB"):
        parity.read_report(path)
