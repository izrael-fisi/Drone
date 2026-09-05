"""Exact native input recording, fault handling and offline replay export."""

import json
from pathlib import Path

import pytest

from vision_nav.vio.export_sensor_replay import export_sensor_replay, validate_recorded_session
from vision_nav.vio.openvins_replay import digest, run, validate_capture
from vision_nav.vio.live_worker import NativeVioLiveWorker
from test_native_live_worker import arrival_inputs, make_worker, offer
from test_prepare_vio_capture import sources
from test_live_sensor_session import setup, SyntheticDevices
from test_vio_stream_worker import wait_for


def test_retention_reuses_png_inode_and_preserves_acknowledged_bytes(arrival_inputs, tmp_path, monkeypatch):
    worker = make_worker(arrival_inputs, tmp_path, record_images=True)
    observed = []
    original = worker._before_batch
    def inspect(payload):
        name = json.loads(payload.splitlines()[-1])["image_path"]
        temporary, retained = worker.capture / name, worker.root / "recording" / name
        assert temporary.stat().st_ino == retained.stat().st_ino
        observed.append(name)
        original(payload)
    monkeypatch.setattr(worker, "_before_batch", inspect)
    worker.start()
    try:
        for index in range(4):
            offer(worker, arrival_inputs, index)
            wait_for(lambda: worker.status()["completed_batch_count"] == index+1)
        worker.finish()
        assert worker.join(5)
        report = json.loads((worker.root / "live_report.json").read_text())
        assert report["recording"]["complete"] and report["recording"]["image_count"] == 4
        assert len(observed) == 4 and not list((worker.capture / "images").iterdir())
        for name, sha in report["recording"]["checksums"].items():
            assert digest(worker.root / "recording" / name) == sha
        assert report["recording"]["image_bytes"] == sum(p.stat().st_size for p in (worker.root / "recording/images").iterdir())
    finally:
        assert worker.stop()


@pytest.mark.parametrize("fault", ["image", "missing", "extra", "symlink", "events"])
def test_archive_mutation_before_finish_fails_epoch(arrival_inputs, tmp_path, fault):
    worker = make_worker(arrival_inputs, tmp_path, record_images=True)
    worker.start()
    try:
        for index in range(2):
            offer(worker, arrival_inputs, index)
            wait_for(lambda: worker.status()["completed_batch_count"] == index+1)
        image = worker.root / "recording/images/000000.png"
        if fault == "image":
            image.write_bytes(b"corrupt")
        elif fault == "missing":
            image.unlink()
        elif fault == "extra":
            image.with_name("extra.png").write_bytes(b"extra")
        elif fault == "symlink":
            image.unlink()
            image.symlink_to(worker.root / "recording/images/000001.png")
        else:
            (worker.root / "recording/events.jsonl").write_text("{}\n")
        worker.finish()
        assert worker.join(5)
        assert worker.status()["status"] == "failed"
        report = json.loads((worker.root / "live_report.json").read_text())
        assert not report["recording"]["complete"]
        assert not report["position_authority"]
    finally:
        assert worker.stop()


@pytest.mark.parametrize("fault", ["byte_limit", "link_failure"])
def test_recording_storage_failure_stops_without_silent_image_loss(arrival_inputs, tmp_path, monkeypatch, fault):
    worker = make_worker(arrival_inputs, tmp_path, record_images=True,
                         max_image_bytes=1024 if fault == "byte_limit" else 1024**3)
    if fault == "link_failure":
        def fail(*args):
            raise OSError("simulated full recording filesystem")
        monkeypatch.setattr(Path, "hardlink_to", fail)
    worker.start()
    try:
        with pytest.raises((ValueError, OSError), match="limit|filesystem"):
            offer(worker, arrival_inputs)
        assert worker.join(5)
        assert worker.status()["status"] == "failed"
        assert worker.status()["completed_batch_count"] == 0
        assert not list((worker.capture / "images").iterdir())
        assert not json.loads((worker.root / "live_report.json").read_text())["recording"]["complete"]
    finally:
        assert worker.stop()


def test_complete_live_session_exports_byte_identical_native_replay(setup, tmp_path):
    session, rig = setup(frame_count=160, duration_s=30., record_images=True)
    session.start()
    try:
        assert session.join(35), session.status()
        assert session.status()["state"] == "complete", session.status()
        checked = validate_recorded_session(session.root)
        assert checked["image_count"] == 160
        output = tmp_path / "sealed-replay"
        manifest = export_sensor_replay(session.root, output)
        assert validate_capture(output) == manifest
        assert manifest["source"]["worker_run_id"] == session.native.worker_run_id
        assert manifest["source"]["training_qualification"] == "not_established"
        replay = run(session.native.binary, output, tmp_path / "replay-result", pose_window=True)
        assert replay["complete"]
        assert (tmp_path / "replay-result/local_states.jsonl").read_bytes() == (session.native.output / "local_states.jsonl").read_bytes()
        assert rig.camera_count == rig.link_count == 1
        assert not manifest["position_authority"]
        assert not (output / "manifest.json").exists()  # not a fabricated training release
    finally:
        assert session.stop()


@pytest.fixture
def recorded(setup):
    session, _ = setup(frame_count=4, record_images=True)
    session.start()
    try:
        assert session.join(10), session.status()
        assert session.status()["state"] == "completed_without_local_estimate", session.status()
        validate_recorded_session(session.root)
        yield session
    finally:
        assert session.stop()


def refresh_evidence(root, name):
    report_path = root / "session_report.json"
    report = json.loads(report_path.read_text())
    report["evidence_sha256"][name] = digest(root / name)
    report_path.write_text(json.dumps(report))


@pytest.mark.parametrize("fault", ["image", "native_report", "telemetry", "camera", "dispatch", "missing_clock",
                                  "shutdown", "ack", "path", "source_configuration"])
def test_export_rejects_incomplete_mutated_or_mismatched_session(recorded, tmp_path, fault):
    root = recorded.root
    if fault == "image":
        (root / "vio/recording/images/000000.png").write_bytes(b"corrupt")
    elif fault == "native_report":
        (root / "vio/live_report.json").write_text("{}")
    elif fault in {"telemetry", "camera", "dispatch"}:
        path = root / (fault+".jsonl")
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        if fault == "telemetry":
            for row in rows:
                if row.get("record_type") == "telemetry":
                    row["sample"]["acceleration_m_s2"] = [0., 0., 0.]
        elif fault == "camera":
            rows[0]["camera_metadata"]["sensor_timestamp_ns"] += 1
        else:
            rows[0]["worker_run_id"] = "other-worker"
        path.write_text("".join(json.dumps(row)+"\n" for row in rows))
        refresh_evidence(root, path.name)  # exercise semantic checks, not just hashes
    elif fault == "missing_clock":
        (root / "timesync.json").unlink()
    elif fault == "shutdown":
        path = root / "session_report.json"
        report = json.loads(path.read_text())
        report["native"]["thread_alive"] = True
        path.write_text(json.dumps(report))
    elif fault == "ack":
        path = root / "vio/input/live_frames.jsonl"
        rows = [json.loads(line) for line in path.read_text().splitlines()]
        ack = next(row for row in rows if row["record_type"] == "acknowledged")
        ack["sequence"] = 3
        path.write_text("".join(json.dumps(row)+"\n" for row in rows))
        native_path = root / "vio/live_report.json"
        report = json.loads(native_path.read_text())
        report["frames_journal_sha256"] = digest(path)
        native_path.write_text(json.dumps(report))
        refresh_evidence(root, "vio/live_report.json")
    elif fault == "path":
        path = root / "session_report.json"
        report = json.loads(path.read_text())
        report["evidence_sha256"]["../outside"] = "0"*64
        path.write_text(json.dumps(report))
    else:
        (root / "vio/input/kalibr_imucam_chain.yaml").write_text("changed")
    output = tmp_path / "must-not-export"
    with pytest.raises((ValueError, KeyError, FileNotFoundError)):
        export_sensor_replay(root, output)
    assert not (output / "input_manifest.json").exists()


def test_export_rejects_copy_time_mutation_without_sealing(recorded, tmp_path, monkeypatch):
    import vision_nav.vio.export_sensor_replay as exporter
    original = exporter.shutil.copyfile
    def change(source, destination):
        result = original(source, destination)
        if str(source).endswith("images/000000.png"):
            Path(source).write_bytes(b"changed during copy")
        return result
    monkeypatch.setattr(exporter.shutil, "copyfile", change)
    with pytest.raises(ValueError, match="checksum mismatch"):
        export_sensor_replay(recorded.root, tmp_path / "partial")
    assert not (tmp_path / "partial/input_manifest.json").exists()


def test_no_recording_and_failed_sessions_cannot_export(setup, tmp_path):
    session, _ = setup(frame_count=4)
    session.start()
    try:
        assert session.join(10)
        with pytest.raises(ValueError, match="recording"):
            export_sensor_replay(session.root, tmp_path / "disabled")
    finally:
        assert session.stop()


def test_export_does_not_seal_a_late_changed_input(recorded, tmp_path, monkeypatch):
    import vision_nav.vio.export_sensor_replay as exporter
    original = exporter.seal_input
    def change(output, scope, source):
        (output / "images/000000.png").write_bytes(b"changed just before sealing")
        return original(output, scope, source)
    monkeypatch.setattr(exporter, "seal_input", change)
    with pytest.raises(ValueError, match="checksum mismatch"):
        export_sensor_replay(recorded.root, tmp_path / "late-partial")
    assert not (tmp_path / "late-partial/input_manifest.json").exists()


def test_uninitialized_complete_recording_is_replayable_not_navigation_success(recorded, tmp_path):
    output = tmp_path / "static-replay"
    manifest = export_sensor_replay(recorded.root, output)
    result = run(recorded.native.binary, output, tmp_path / "static-result", pose_window=True)
    assert result["returncode"] == 3 and result["complete"] is False
    assert not manifest["position_authority"]
    assert manifest["source"]["training_qualification"] == "not_established"
    with pytest.raises(ValueError, match="new and outside"):
        export_sensor_replay(recorded.root, output)
    with pytest.raises(ValueError, match="new and outside"):
        export_sensor_replay(recorded.root, recorded.root / "nested")


@pytest.mark.parametrize("options", [{"record_images": "yes"}, {"max_image_bytes": True},
    {"max_image_bytes": 1023}, {"max_image_bytes": 32*1024**3+1}, {"max_image_bytes": 1024.0}])
def test_recording_bounds_validated_before_creating_or_starting_native(tmp_path, options):
    with pytest.raises(ValueError, match="record_images|max_image_bytes"):
        NativeVioLiveWorker(tmp_path / "binary", tmp_path / "checkout", tmp_path / "profile", tmp_path / "output", **options)
    assert not (tmp_path / "output").exists()


def test_partial_images_remain_evidence_after_camera_failure(setup, sources, tmp_path):
    rig = SyntheticDevices(sources, camera_failure=8)
    session, _ = setup(rig=rig, frame_count=20, record_images=True)
    session.start()
    try:
        assert session.join(10)
        assert session.status()["state"] == "failed"
        assert list((session.native.root / "recording/images").iterdir())
        with pytest.raises(ValueError, match="incomplete"):
            export_sensor_replay(session.root, tmp_path / "failed")
    finally:
        assert session.stop()
