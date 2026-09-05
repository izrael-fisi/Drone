"""Research adapter contract tests plus opt-in actual native sensor replay."""

import json
import os
from pathlib import Path

import numpy as np
import pytest

from vision_nav.vio.openvins_replay import REVISION, digest, make_synthetic, prepare_euroc, prepare_tumvi, read_yaml, run, write_yaml

ROOT = Path(__file__).resolve().parents[1]


@pytest.fixture
def capture(tmp_path):
    checkout = ROOT / "research/vendor/open_vins"
    if not (checkout / ".git").exists():
        pytest.skip("Pinned OpenVINS checkout required for native research fixtures")
    output = tmp_path / "capture"
    make_synthetic(checkout, output)
    return output


def reseal_test_file(capture, relative):
    path = capture / "input_manifest.json"
    manifest = json.loads(path.read_text())
    manifest["checksums"][relative] = digest(capture / relative)
    path.write_text(json.dumps(manifest))


def binary():
    location = os.environ.get("VISION_NAV_OPENVINS_BINARY")
    if not location:
        pytest.skip("Set VISION_NAV_OPENVINS_BINARY to exercise actual native frontend")
    result = Path(location).resolve()
    assert result.is_file(), "Configured native binary missing"
    return result


def test_fixture_carries_camera_imu_and_sealed_calibration(capture):
    data = [json.loads(line) for line in (capture / "events.jsonl").read_text().splitlines()]
    assert sum(e["type"] == "camera" for e in data) == 160
    for kind in ("camera", "imu"):
        times = [e["timestamp_ns"] for e in data if e["type"] == kind]
        assert all(b > a for a, b in zip(times, times[1:]))
    manifest = json.loads((capture / "input_manifest.json").read_text())
    assert manifest["source_revision"] == REVISION
    assert manifest["position_authority"] is False
    for name, checksum in manifest["checksums"].items():
        assert digest(capture / name) == checksum
    assert "OPENVINS-LICENSE" in manifest["checksums"]
    config = read_yaml(capture / "estimator_config.yaml")
    assert config["use_klt"] and not config["use_stereo"]
    assert config["max_cameras"] == 1
    assert not config["calib_cam_timeoffset"]
    with pytest.raises(FileExistsError):
        make_synthetic(ROOT / "research/vendor/open_vins", capture)


def test_config_yaml_round_trip(tmp_path):
    values = {"cam0": {"resolution": [320, 240], "T_imu_cam": np.eye(4).tolist()}, "enabled": True}
    path = tmp_path / "config.yaml"
    write_yaml(path, values)
    assert read_yaml(path) == values


def test_euroc_preparation_uses_dataset_extrinsics_and_noise(capture, tmp_path):
    import shutil

    mav0 = tmp_path / "mav0"
    (mav0 / "cam0/data").mkdir(parents=True)
    (mav0 / "imu0").mkdir()
    body_imu, body_camera = np.eye(4), np.eye(4)
    body_imu[1, 3], body_camera[1, 3] = .3, .7
    camera = {"T_BS": {"data": body_camera.ravel().tolist()}, "resolution": [320, 240],
              "intrinsics": [250., 250., 160., 120.], "camera_model": "pinhole", "distortion_model": "radtan",
              "distortion_coefficients": [0., 0., 0., 0.]}
    imu = {"T_BS": {"data": body_imu.ravel().tolist()}, "rate_hz": 200,
           "accelerometer_noise_density": .002, "accelerometer_random_walk": .003,
           "gyroscope_noise_density": .0002, "gyroscope_random_walk": .00002}
    write_yaml(mav0 / "cam0/sensor.yaml", camera)
    write_yaml(mav0 / "imu0/sensor.yaml", imu)
    (mav0 / "cam0/data.csv").write_text("#timestamp,filename\n1000000000,one.png\n")
    (mav0 / "imu0/data.csv").write_text("#timestamp,wx,wy,wz,ax,ay,az\n" + "".join(
        f"{1_000_000_000+i*5_000_000},0,0,0,0,0,9.81\n" for i in range(4)))
    shutil.copy2(capture / "images/00000.png", mav0 / "cam0/data/one.png")
    output = tmp_path / "prepared"
    manifest = prepare_euroc(ROOT / "research/vendor/open_vins", mav0, output, 1)
    assert manifest["source"]["ground_truth_used_by_frontend"] is False
    calibration = read_yaml(output / "kalibr_imucam_chain.yaml")
    assert np.isclose(calibration["cam0"]["T_imu_cam"][1][3], .4)
    assert read_yaml(output / "kalibr_imu_chain.yaml")["imu0"]["gyroscope_noise_density"] == .0002
    rows = [json.loads(x) for x in (output / "events.jsonl").read_text().splitlines()]
    assert [r["type"] for r in rows] == ["imu", "imu", "imu", "camera"]


def test_tumvi_preserves_calibrated_profile_and_explicit_pixel_conversion(capture, tmp_path):
    import cv2

    mav0 = tmp_path / "dataset-room1_512_16/mav0"
    (mav0 / "cam0/data").mkdir(parents=True)
    (mav0 / "imu0").mkdir()
    pixels = np.full((512, 512), 32768, dtype=np.uint16)
    cv2.imwrite(str(mav0 / "cam0/data/one.png"), pixels)
    (mav0 / "cam0/data.csv").write_text("1000000000,one.png\n")
    (mav0 / "imu0/data.csv").write_text("".join(
        f"{1_000_000_000+i*5_000_000},0,0,0,0,0,9.81\n" for i in range(4)))
    output = tmp_path / "tumvi"
    manifest = prepare_tumvi(ROOT / "research/vendor/open_vins", mav0, output, 1)
    assert manifest["source"]["ground_truth_used_by_frontend"] is False
    assert "mask_tumvi0.png" in manifest["checksums"]
    assert cv2.imread(str(output / "images/000000.png"), 0).min() == 128
    calibration = read_yaml(output / "kalibr_imucam_chain.yaml")["cam0"]
    assert "T_cam_imu" in calibration and "T_imu_cam" not in calibration
    assert calibration["distortion_model"] == "equidistant"
    assert read_yaml(output / "estimator_config.yaml")["gravity_mag"] == 9.80766
    # Native dataset timestamps are unchanged, not corrected a second time.
    events = [json.loads(x) for x in (output / "events.jsonl").read_text().splitlines()]
    assert events[-1]["timestamp_ns"] == 1_000_000_000


def test_native_respects_camera_mask(capture, tmp_path):
    import cv2

    exe = binary()
    cv2.imwrite(str(capture / "mask.png"), np.full((240, 320), 255, dtype=np.uint8))
    config = read_yaml(capture / "estimator_config.yaml")
    config.update(use_mask=True, mask0="mask.png")
    write_yaml(capture / "estimator_config.yaml", config)
    reseal_test_file(capture, "estimator_config.yaml")
    with pytest.raises(ValueError, match="mask"):
        run(Path("/does-not-exist"), capture, tmp_path / "unsealed")
    reseal_test_file(capture, "mask.png")
    output = tmp_path / "masked"
    report = run(exe, capture, output)
    assert report["returncode"] == 3, (output / "native.log").read_text()
    assert report["states"].get("LOCAL_ESTIMATE", 0) == 0


@pytest.mark.parametrize("mutation", ["changed", "missing", "escape", "image_unlisted", "external_config"])
def test_capture_validation_runs_before_native_execution(capture, tmp_path, mutation):
    path = capture / "input_manifest.json"
    manifest = json.loads(path.read_text())
    if mutation == "changed":
        (capture / "events.jsonl").write_text("changed")
    elif mutation == "missing":
        del manifest["checksums"]["events.jsonl"]
    elif mutation == "escape":
        manifest["checksums"]["../secret"] = "0" * 64
    elif mutation == "image_unlisted":
        del manifest["checksums"]["images/00000.png"]
    else:
        cfg = read_yaml(capture / "estimator_config.yaml")
        cfg["relative_config_imu"] = "elsewhere.yaml"
        write_yaml(capture / "estimator_config.yaml", cfg)
        manifest["checksums"]["estimator_config.yaml"] = digest(capture / "estimator_config.yaml")
    path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError):
        run(Path("/does-not-exist"), capture, tmp_path / "out")


def test_actual_frontend_initializes_from_pixels_and_imu_without_gt(capture, tmp_path):
    output = tmp_path / "actual"
    report = run(binary(), capture, output)
    assert report["complete"], (output / "native.log").read_text()
    assert report["states"]["LOCAL_ESTIMATE"] > 40
    rows = [json.loads(x) for x in (output / "local_states.jsonl").read_text().splitlines()]
    valid = [r for r in rows if r["local_state"] == "LOCAL_ESTIMATE"]
    assert all(r["navigation_mode"] == "NO_GLOBAL_FIX" and not r["position_authority"] for r in rows)
    assert valid[0]["timestamp_ns"] >= 3_000_000_000
    for r in valid:
        cov = np.array(r["state_covariance"])
        assert cov.shape == (15, 15) and np.isfinite(cov).all()
        assert np.linalg.eigvalsh((cov+cov.T)/2).min() > -1e-9
        pose = np.array(r["local_T_imu"])
        np.testing.assert_allclose(pose[:3, :3].T @ pose[:3, :3], np.eye(3), atol=1e-6)
    # A real image+IMU path must respond to the rendered translation, not just initialize.
    displacement = np.linalg.norm(np.array(valid[-1]["local_T_imu"])[:3, 3] - np.array(valid[0]["local_T_imu"])[:3, 3])
    assert displacement > 1
    assert report["smoother_factor_export"].startswith("disabled")
    with pytest.raises(FileExistsError):
        run(binary(), capture, output)


def test_native_joint_window_preserves_pose_and_correlation(capture, tmp_path):
    from vision_nav.vio.pose_window import JointPoseWindow, audit
    import subprocess

    exe = binary()
    contract = subprocess.run([str(exe.parent / "vision-nav-openvins-pose-contract-test")], capture_output=True, text=True)
    assert contract.returncode == 0, contract.stdout + contract.stderr
    plain = tmp_path / "plain"
    joint = tmp_path / "joint"
    assert run(exe, capture, plain)["complete"]
    assert run(exe, capture, joint, pose_window=True)["complete"]
    original = [json.loads(x) for x in (plain / "local_states.jsonl").read_text().splitlines()]
    enhanced = [json.loads(x) for x in (joint / "local_states.jsonl").read_text().splitlines()]
    assert [{k: v for k, v in r.items() if k != "pose_window"} for r in enhanced] == original
    source_times = {json.loads(x)["timestamp_ns"] for x in (capture / "events.jsonl").read_text().splitlines()
                    if json.loads(x)["type"] == "camera"}
    for row in enhanced:
        if row["pose_window"] is None:
            continue
        window = JointPoseWindow.from_record(row)
        assert set(window.timestamps_ns) <= source_times
        assert window.timestamps_ns[-1] == row["timestamp_ns"]
        np.testing.assert_allclose(window.local_T_imu[-1], row["local_T_imu"], atol=1e-10)
        np.testing.assert_allclose(np.array(row["pose_window"]["joint_covariance_native"])[-6:, -6:],
                                   np.array(row["state_covariance"])[:6, :6], atol=1e-10)
    evidence = audit(joint)
    assert evidence["windows_validated"] > 40
    assert evidence["max_clones"] == 11
    assert evidence["multi_pose_windows_without_cross_covariance"] == 0
    assert evidence["max_cross_block_frobenius_norm"] > .01


def test_native_joint_window_rejects_unbounded_clone_configuration(capture, tmp_path):
    cfg = read_yaml(capture / "estimator_config.yaml")
    cfg["max_clones"] = 100
    write_yaml(capture / "estimator_config.yaml", cfg)
    reseal_test_file(capture, "estimator_config.yaml")
    output = tmp_path / "bad_clones"
    report = run(binary(), capture, output, pose_window=True)
    assert report["returncode"] == 2
    assert "max_clones" in (output / "native.log").read_text()


@pytest.mark.parametrize("fault", ["imu_reorder", "no_imu", "wrong_size", "descriptor_mode", "no_excitation"])
def test_actual_frontend_rejects_bad_sensor_or_config_inputs(capture, tmp_path, fault):
    exe = binary()
    if fault == "descriptor_mode":
        cfg = read_yaml(capture / "estimator_config.yaml")
        cfg["use_klt"] = False
        write_yaml(capture / "estimator_config.yaml", cfg)
        reseal_test_file(capture, "estimator_config.yaml")
    elif fault == "wrong_size":
        import cv2
        cv2.imwrite(str(capture / "images/00000.png"), np.zeros((10, 10), dtype=np.uint8))
        reseal_test_file(capture, "images/00000.png")
    else:
        events = [json.loads(x) for x in (capture / "events.jsonl").read_text().splitlines()]
        if fault == "imu_reorder":
            events[0], events[1] = events[1], events[0]
        elif fault == "no_imu":
            events = [e for e in events if e["type"] == "camera"]
        else:
            events = [e for e in events if e["timestamp_ns"] < 3_500_000_000]
        (capture / "events.jsonl").write_text("".join(json.dumps(e)+"\n" for e in events))
        reseal_test_file(capture, "events.jsonl")
    report = run(exe, capture, tmp_path / "fault")
    assert not report["complete"]
    assert report["returncode"] in {2, 3}
    assert not report["position_authority"]
    reason = {"descriptor_mode": "requires mono KLT", "wrong_size": "resolution mismatch",
              "imu_reorder": "Reordered or gapped IMU", "no_imu": "bracketing IMU",
              "no_excitation": "initialized camera updates: 0"}[fault]
    assert reason in (tmp_path / "fault/native.log").read_text()
