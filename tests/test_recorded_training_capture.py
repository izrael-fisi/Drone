"""Recorded sensor sessions enter training tooling without invented evidence."""

from dataclasses import replace
import json
from pathlib import Path
import shutil

import pytest
import yaml

from vision_nav.camera_extrinsics import create_extrinsics_document
from vision_nav.neural.dataset_release import create_dataset_release
from vision_nav.neural.reference_map import create_reference_map
from vision_nav.neural.training_dataset import build_training_index, LEGACY_TIMING
from vision_nav.neural.training_labels import materialize_training_labels
from vision_nav.neural.teacher_dataset import pose_context
from vision_nav.training_capture import validate_training_session
from vision_nav.vio.export_training_capture import export_training_capture
from vision_nav.vio.openvins_replay import digest, validate_capture
from test_live_sensor_session import setup, SyntheticDevices
from test_prepare_vio_capture import sources


class AttitudeRig(SyntheticDevices):
    def link_factory(self):
        link = super().link_factory()
        read = link.try_read_telemetry
        pending = None
        def with_attitude(timeout_s):
            nonlocal pending
            if pending is not None:
                value, pending = pending, None
                return value
            sample = read(timeout_s)
            pending = replace(sample, message_type="ATTITUDE", roll_rad=.01, pitch_rad=-.02, yaw_rad=.3)
            return sample
        link.try_read_telemetry = with_attitude
        return link


@pytest.fixture
def inputs(setup, sources, tmp_path):
    rig = AttitudeRig(sources)
    session, _ = setup(rig=rig, frame_count=8, record_images=True)
    session.start()
    try:
        assert session.join(10), session.status()
        assert session.status()["state"] == "completed_without_local_estimate"
        cal = tmp_path / "calibration.yaml"
        cal.write_text(yaml.safe_dump({"camera_name": "synthetic-test-only", "calibration_id": "fixture-camera",
            "calibration_status": "measured", "image_width": 320, "image_height": 240, "distortion_model": "plumb_bob",
            "camera_matrix": {"data": [250., 0., 160., 0., 250., 120., 0., 0., 1.]},
            "distortion_coefficients": {"rows": 1, "cols": 5, "data": [0., 0., 0., 0., 0.]}}))
        ext = tmp_path / "extrinsics.yaml"
        ext.write_text(yaml.safe_dump(create_extrinsics_document(extrinsic_id="synthetic-ext", camera_calibration_id="fixture-camera",
            translation_m=(0., 0., 0.), rotation_quat_xyzw=(0., 0., 0., 1.), measurement_method="synthetic fixture not aircraft measurement",
            translation_sigma_m=.01, rotation_sigma_deg=.1)))
        maps = tmp_path / "maps"
        maps.mkdir()
        shutil.copyfile(session.native.root / "recording/images/000000.png", maps / "image.png")
        (maps / "stac.json").write_text(json.dumps({"type": "Feature", "id": "synthetic-test-only"}))
        reference = maps / "reference_map_release.json"
        create_reference_map(reference, map_id="fixture-map", version="1", crs="EPSG:32610",
            local_origin_enu=(0., 0., 0.), geodetic_origin_wgs84=(37., -122., 10.), gsd_m_per_px=.2,
            imagery_uri="image.png", stac_item_uri="stac.json", calibration_ids=["fixture-camera"])
        yield session, cal, ext, reference
    finally:
        assert session.stop()


def export(inputs, output):
    session, cal, ext, reference = inputs
    return export_training_capture(session.root, output, calibration_file=cal, camera_to_body_file=ext, reference_map=reference,
                                   conditions=["synthetic_test_only"])


def research_release(captures):
    return create_dataset_release(captures, release_id="recorded-synthetic@1", version="1", source_revision="test",
        license_summary="synthetic fixtures only", require_offline_gnss_reference=False, require_mavlink_timesync=False)


def test_recorded_capture_enters_exposure_index_without_gps_or_clock_snapshot_fabrication(inputs, tmp_path):
    session, cal, ext, reference = inputs
    output = tmp_path / "training"
    result = export(inputs, output)
    manifest = result["manifest"]
    assert validate_training_session(output, require_sensor_timestamps=True)["status"] == "valid"
    assert validate_capture(output / "native_replay")["position_authority"] is False
    assert manifest["session_id"] == "live-vio-" + session.native.worker_run_id
    assert manifest["source_recording"]["worker_run_id"] == session.native.worker_run_id
    assert manifest["map_reference"]["manifest_sha256"] == digest(reference)
    assert manifest["calibration"]["file_sha256"] == digest(cal)
    assert manifest["camera_to_body"]["file_sha256"] == digest(ext)
    assert pose_context({"camera_to_body": manifest["camera_to_body"]})["camera_extrinsics_valid"]
    assert manifest["mavlink"]["exposure_alignment"]["status"] == "available"
    assert manifest["mavlink"]["offline_gnss_reference"]["status"] == "not_ready"
    assert manifest["mavlink"]["timesync"]["pre_capture"]["status"] == "not_recorded"
    assert manifest["mavlink"]["timesync"]["post_capture"]["status"] == "not_recorded"
    assert manifest["started_utc"] is None and manifest["completed_utc"] is None
    assert manifest["timebase"]["frame_timestamp_reference"] == "first_row_exposure_start"
    original = [json.loads(line)["sample"] for line in (session.root / "telemetry.jsonl").read_text().splitlines()
                if json.loads(line)["record_type"] == "telemetry"]
    normalized = [json.loads(line) for line in (output / manifest["telemetry_path"]).read_text().splitlines()]
    assert [row["sample"] for row in normalized] == original
    for i, frame in enumerate(manifest["frames"]):
        assert digest(output / frame["path"]) == frame["native_input"]["image_sha256"]
        assert frame["native_input"]["sequence"] == i
        assert frame["host_monotonic_ns"] == frame["native_input"]["timestamp_ns"]+20_000_000_000
    assert result["training_eligibility"]["status"] == "invalid"
    with pytest.raises(ValueError, match="GNSS|TIMESYNC"):
        create_dataset_release([output], release_id="strict@1", version="1", source_revision="test", license_summary="synthetic")
    release_path = tmp_path / "research-release.json"
    release_path.write_text(json.dumps(research_release([output])))
    index_dir = tmp_path / "index"
    index = build_training_index(release_path, index_dir)
    assert index["manifest"]["sample_count"] == 8
    samples = [json.loads(row) for row in (index_dir / "samples.jsonl").read_text().splitlines()]
    assert all(row["timestamp_reference"] == "first_row_exposure_start" for row in samples)
    assert all(row["gps_reference"] is None for row in samples)
    assert samples[0]["attitude"]["yaw_rad"] == .3
    assert samples[0]["camera_metadata"]["quality"]["status"] == "measured"
    assert samples[0]["camera_metadata"]["quality_provenance"]["frame_sha256"] == samples[0]["frame_sha256"]
    labels = materialize_training_labels(index_dir, tmp_path / "labels", reference_map=reference)
    assert labels["manifest"]["accepted_count"] == 0
    assert all(json.loads(line)["target"] is None for line in (tmp_path / "labels/labels.jsonl").read_text().splitlines())
    with pytest.raises(ValueError, match="legacy midpoint"):
        build_training_index(release_path, tmp_path / "legacy", timing_policy=LEGACY_TIMING, require_attitude=False)


@pytest.mark.parametrize("fault", ["calibration_id", "unmeasured", "intrinsics", "distortion", "dimensions",
                                  "extrinsic_id", "extrinsic_status", "rotation", "translation", "map_id", "map_asset"])
def test_export_rejects_mismatched_calibration_or_map_before_creating_capture(inputs, tmp_path, fault):
    _, cal, ext, reference = inputs
    if fault in {"calibration_id", "unmeasured", "intrinsics", "distortion", "dimensions"}:
        data = yaml.safe_load(cal.read_text())
        if fault == "calibration_id":
            data["calibration_id"] = "other"
        elif fault == "unmeasured":
            data["calibration_status"] = "provisional"
        elif fault == "intrinsics":
            data["camera_matrix"]["data"][0] += .1
        elif fault == "distortion":
            data["distortion_coefficients"]["data"][4] = .001
        else:
            data["image_width"] = 640
        cal.write_text(yaml.safe_dump(data))
    elif fault.startswith("extrinsic") or fault in {"rotation", "translation"}:
        data = yaml.safe_load(ext.read_text())
        if fault == "extrinsic_id":
            data["camera_calibration_id"] = "other"
        elif fault == "extrinsic_status":
            data["extrinsic_status"] = "provisional"
        elif fault == "rotation":
            data["rotation_quat_xyzw"] = {"x": 0., "y": 0., "z": 1., "w": 0.}
        else:
            data["translation_m"]["x"] = .01
        ext.write_text(yaml.safe_dump(data))
    elif fault == "map_id":
        data = json.loads(reference.read_text())
        data["calibration_ids"] = ["other"]
        reference.write_text(json.dumps(data))
    else:
        (reference.parent / "image.png").write_bytes(b"changed map")
    with pytest.raises(ValueError):
        export(inputs, tmp_path / "invalid")
    assert not (tmp_path / "invalid").exists()


def test_source_identity_and_portability_survive_reexport(inputs, tmp_path):
    first, second = tmp_path / "first", tmp_path / "second"
    a, b = export(inputs, first), export(inputs, second)
    assert a["manifest"]["session_id"] == b["manifest"]["session_id"]
    with pytest.raises(ValueError, match="same capture session"):
        research_release([first, second])
    moved = tmp_path / "relocated"
    first.rename(moved)
    assert validate_training_session(moved, require_sensor_timestamps=True)["status"] == "valid"
    for field in ("calibration", "camera_to_body"):
        assert (moved / a["manifest"][field]["path"]).is_file()
    with pytest.raises(ValueError, match="new and outside"):
        export(inputs, second)


@pytest.mark.parametrize("fault", ["source_calibration", "copied_frame", "copied_map", "manifest"])
def test_late_mutation_never_leaves_a_valid_training_capture(inputs, tmp_path, monkeypatch, fault):
    import vision_nav.vio.export_training_capture as module
    if fault == "source_calibration":
        original = module.shutil.copyfile
        def changed(source, destination):
            result = original(source, destination)
            if Path(source) == inputs[1]:
                inputs[1].write_text("changed after copy")
            return result
        monkeypatch.setattr(module.shutil, "copyfile", changed)
    else:
        original = module.write_session_checksums
        def changed(root):
            relative = {"copied_frame": "native_replay/images/000000.png", "copied_map": "map_reference.json",
                        "manifest": "manifest.json"}[fault]
            (root / relative).write_text("changed before checksumming")
            return original(root)
        monkeypatch.setattr(module, "write_session_checksums", changed)
    output = tmp_path / "partial"
    with pytest.raises(ValueError, match="changed|checksum|Input checksum"):
        export(inputs, output)
    assert not (output / "manifest.json").exists()
    assert not (output / "checksums.sha256").exists()


def test_bound_map_cannot_be_replaced_under_same_release_id(inputs, tmp_path):
    output = tmp_path / "capture"
    export(inputs, output)
    release_path = tmp_path / "research.json"
    release_path.write_text(json.dumps(research_release([output])))
    build_training_index(release_path, tmp_path / "index")
    reference = inputs[3]
    data = json.loads(reference.read_text())
    data["geodetic_origin_wgs84"]["altitude_m"] += 10.
    reference.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="map manifest differs"):
        materialize_training_labels(tmp_path / "index", tmp_path / "labels", reference_map=reference)
    with pytest.raises(ValueError, match="replacement origin"):
        materialize_training_labels(tmp_path / "index", tmp_path / "manual-labels", origin_lat_deg=37., origin_lon_deg=-122., origin_altitude_m=10.)


def test_deployment_map_can_be_bound_without_changing_legacy_capture_paths(inputs, tmp_path):
    session, cal, ext, reference = inputs
    base = json.loads(reference.read_text())
    base.update(schema_version="vision_nav_map_release_v2", profile_id="pi5_crossview_nav_v1", model_release_id="synthetic-model@1",
                geometry_modes=["pnp_3d"], cog_uri="image.png", tile_index_uri="stac.json", retrieval_index_uri="stac.json",
                xfeat_index_uri="stac.json")
    deployment = reference.parent / "map_release.json"
    deployment.write_text(json.dumps(base))
    result = export_training_capture(session.root, tmp_path / "deployment-capture", calibration_file=cal,
                                     camera_to_body_file=ext, map_release=deployment)
    assert result["manifest"]["map_reference"]["kind"] == "deployment_map_release"
    assert result["manifest"]["map_reference"]["manifest_sha256"] == digest(deployment)
    with pytest.raises(ValueError, match="exactly one"):
        export_training_capture(session.root, tmp_path / "ambiguous", calibration_file=cal, camera_to_body_file=ext,
                                map_release=deployment, reference_map=reference)
