"""Export a completed recorded sensor session into sealed native VIO replay.

This preserves exactly dispatched events, not a refitted exposure clock or a
GNSS-derived motion prior. It does not certify supervised training readiness.
"""

import argparse
import hashlib
import json
from pathlib import Path
import re
import shutil

import cv2

from ..capture_timing import TimingPolicy, camera_exposure_time
from .openvins_replay import REVISION, digest, seal_input, validate_capture
from .prepare_capture import load_sensor_profile, normalize_imu_sample
from .stream_replay import sensor_batches
from .anchor_control import audit_control_stream


def _path(root, name):
    if (not isinstance(name, str) or not name or "\\" in name or Path(name).is_absolute()
            or ".." in Path(name).parts or str(Path(name)) != name):
        raise ValueError("Noncanonical recording evidence path")
    path = root / name
    if any(p.is_symlink() for p in (path, *path.parents) if p != root.parent):
        raise ValueError("Recording evidence must not contain symlinks")
    if not path.is_file() or not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("Recording evidence is missing or outside session: " + name)
    return path


def _read(root, name):
    path = _path(root, name)
    if path.stat().st_size > 8*1024*1024:
        raise ValueError("Oversized recording manifest")
    return json.loads(path.read_text())


def _verify(root, checksums):
    if not isinstance(checksums, dict) or not 1 <= len(checksums) <= 20200:
        raise ValueError("Invalid recording checksum inventory")
    for name, expected in checksums.items():
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError("Invalid recording checksum")
        if digest(_path(root, name)) != expected:
            raise ValueError("Recording checksum mismatch: " + name)


def _rows(path, *, limit=64*1024*1024):
    if path.stat().st_size > limit:
        raise ValueError("Recording journal exceeds size limit")
    with path.open() as stream:
        while line := stream.readline(65537):
            if len(line) > 65536:
                raise ValueError("Recording journal row exceeds size limit")
            yield json.loads(line)


def _finished(worker, count, *, joined):
    if (worker.get("status") not in {"complete", "no_local_estimate"} or worker.get("error")
            or worker.get("native_returncode") not in {0, 3} or worker.get("input_closed") is not True
            or worker.get("pending_batch_count") != 0 or worker.get("backpressure_count") != 0
            or worker.get("submitted_batch_count") != count or worker.get("completed_batch_count") != count
            or worker.get("position_authority") is not False
            or (joined and worker.get("thread_alive") is not False)):
        raise ValueError("Native recording did not complete cleanly")


def validate_recorded_session(session):
    """Verify on-disk evidence; hashes detect mutation, not malicious forgery."""
    root = Path(session).resolve()
    session_hash = digest(_path(root, "session_report.json"))
    report = _read(root, "session_report.json")
    count = report.get("camera_frames")
    if (report.get("schema_version") != "vision_nav_live_sensor_session_v1"
            or report.get("state") not in {"complete", "completed_without_local_estimate"}
            or report.get("error") or type(count) is not int or not 2 <= count <= 20000
            or report.get("delivered_frames") != count or report.get("position_authority") is not False
            or report.get("camera_state") != "stopped" or report.get("link_state") != "stopped"
            or report.get("camera_thread_alive") is not False or report.get("link_thread_alive") is not False):
        raise ValueError("Sensor recording is incomplete or its owners did not close")
    _finished(report["native"], count, joined=True)
    required = {"camera.jsonl", "telemetry.jsonl", "dispatch.jsonl", "timesync.json", "vio/live_report.json"}
    if set(report.get("evidence_sha256", {})) != required:
        raise ValueError("Session recording omits required evidence")
    files = {"session_report.json": session_hash, **report["evidence_sha256"]}
    native = _read(root, "vio/live_report.json")
    _finished(native["worker"], count, joined=False)
    identity = native["worker"]["worker_run_id"]
    if identity != report["native"]["worker_run_id"] or native.get("unacknowledged_images") != []:
        raise ValueError("Native recording identity or acknowledgement mismatch")
    archive = native.get("recording", {})
    if (archive.get("enabled") is not True or archive.get("complete") is not True
            or archive.get("status") != "complete" or archive.get("image_count") != count):
        raise ValueError("Complete image recording was not enabled or did not finish")
    inventory = archive.get("checksums", {})
    image_names = {f"images/{i:06d}.png" for i in range(count)}
    if set(inventory) != image_names | {"events.jsonl"}:
        raise ValueError("Recorded frame inventory is not complete")
    record_root = root / "vio/recording"
    found = list(record_root.rglob("*"))
    if any(p.is_symlink() for p in found) or {str(p.relative_to(record_root)) for p in found if p.is_file()} != set(inventory):
        raise ValueError("Recorded file inventory changed")
    image_bytes = sum(_path(record_root, name).stat().st_size for name in image_names)
    if (image_bytes != archive.get("image_bytes") or type(archive.get("max_image_bytes")) is not int
            or not image_bytes <= archive["max_image_bytes"] <= 32*1024**3):
        raise ValueError("Recorded image byte accounting mismatch")
    files.update({"vio/recording/"+name: sha for name, sha in inventory.items()})
    files.update({"vio/input/live_input.json": native["input_manifest_sha256"],
                  "vio/input/live_frames.jsonl": native["frames_journal_sha256"],
                  "vio/native/local_states.jsonl": native["native_states_sha256"]})
    manifest = _read(root, "vio/input/live_input.json")
    build = manifest.get("native_build", {})
    if (manifest.get("schema_version") != "vision_nav_native_live_input_v1"
            or manifest.get("worker_run_id") != identity
            or manifest.get("image_storage") != "retained_png_hardlinks_and_exact_native_events"
            or build.get("source_revision") != REVISION or build.get("ros_enabled") is not False
            or manifest.get("position_authority") is not False):
        raise ValueError("Native manifest does not bind this recording")
    config = manifest["configuration_checksums"]
    files.update({"vio/input/"+name: sha for name, sha in config.items()})
    anchor_control = None
    capacity = manifest.get("anchor_control_capacity", 0)
    if capacity:
        control_name = "vio/native/anchor_control_report.json"
        files[control_name] = native.get("anchor_control_report_sha256")
        control = _read(root, control_name)
        if (control.get("schema_version") != "vision_nav_anchor_control_report_v1"
                or control.get("worker_run_id") != identity or control.get("capacity") != capacity
                or control.get("binary_sha256") != manifest.get("binary_sha256")
                or control.get("status") != native["worker"]["status"] or control.get("error")
                or control.get("position_authority") is not False
                or set(control.get("checksums", {})) != {"owned_stream.jsonl", "anchor_acknowledgements.jsonl", "local_states.jsonl"}):
            raise ValueError("Invalid recorded native anchor control report")
        for name, sha in control["checksums"].items():
            key = "vio/native/"+name
            if key in files and files[key] != sha:
                raise ValueError("Native anchor and camera checksums disagree")
            files[key] = sha
        anchor_control = {"capacity": capacity, "worker_run_id": identity,
            "stream_path": "source_evidence/vio/native/owned_stream.jsonl"}
    _verify(root, files)
    if anchor_control:
        audit = audit_control_stream(root/"vio/native/owned_stream.jsonl", root/"vio/native/anchor_acknowledgements.jsonl",
            root/"vio/native/local_states.jsonl", worker_run_id=identity, capacity=capacity,
            sensor_events_path=record_root/"events.jsonl")
        if audit["camera_count"] != count:
            raise ValueError("Native anchor stream omitted captured frames")
    profile, _, _ = load_sensor_profile(root / "vio/input/sensor_profile")

    offered, acknowledged = [], []
    for row in _rows(root / "vio/input/live_frames.jsonl", limit=1024**3):
        collection = offered if row.get("record_type") == "offered" else acknowledged if row.get("record_type") == "acknowledged" else None
        if collection is None or len(collection) >= count or row.get("sequence") != len(collection) or row.get("worker_run_id") != identity:
            raise ValueError("Duplicate, reordered or mismatched frame acknowledgement")
        collection.append(row)
    if len(offered) != count or len(acknowledged) != count:
        raise ValueError("Incomplete offered/acknowledged frame journal")
    dispatch = iter(_rows(root / "dispatch.jsonl"))
    cameras = iter(_rows(root / "camera.jsonl"))
    raw = (normalize_imu_sample(r["sample"], profile) for r in _rows(root / "telemetry.jsonl") if r.get("record_type") == "telemetry")
    raw = (e for e in raw if e is not None)
    current = next(raw, None)
    seen = 0
    for index, batch in enumerate(sensor_batches(record_root / "events.jsonl")):
        if index >= count or not 2 <= len(batch) <= 1024:
            raise ValueError("Invalid recorded native batch count")
        offered_row, ack = offered[index], acknowledged[index]
        camera, delivered = next(cameras, None), next(dispatch, None)
        event = batch[-1]
        name = f"images/{index:06d}.png"
        if (not camera or not delivered or camera.get("record_type") != "camera" or delivered.get("record_type") != "dispatch"
                or camera.get("sequence") != index or delivered.get("sequence") != index
                or delivered.get("worker_run_id") != identity or event.get("image_path") != name
                or event.get("timestamp_ns") != offered_row.get("timestamp_ns")
                or event.get("timestamp_ns") != ack.get("timestamp_ns")
                or event.get("timestamp_ns") != delivered.get("timestamp_ns")):
            raise ValueError("Frame dispatch or timestamp identity mismatch")
        if (camera_exposure_time(camera["camera_metadata"], TimingPolicy()) != offered_row["camera_clock"]
                or hashlib.sha256(json.dumps(batch, sort_keys=True, allow_nan=False).encode()).hexdigest() != offered_row["batch_sha256"]):
            raise ValueError("Recorded batch differs from native offer")
        pixels = cv2.imread(str(record_root / name), cv2.IMREAD_UNCHANGED)
        if (pixels is None or list(pixels.shape) != offered_row["pixel_shape"]
                or hashlib.sha256(pixels.tobytes()).hexdigest() != offered_row["pixel_sha256"]
                or offered_row["pixel_sha256"] != ack["pixel_sha256"]
                or offered_row["pixel_sha256"] != delivered["pixel_sha256"]
                or inventory[name] != offered_row["image_sha256"] or inventory[name] != ack["image_sha256"]):
            raise ValueError("Recorded pixels differ from acknowledged native image")
        for raw_index, imu in enumerate(batch[:-1]):
            # Warmup samples may precede the first native batch; after that,
            # every selected raw IMU event must be preserved in order.
            if index == 0 and raw_index == 0:
                while current is not None and current["timestamp_ns"] < imu["timestamp_ns"]:
                    current = next(raw, None)
            if current != imu:
                raise ValueError("Recorded native IMU differs from source telemetry")
            current = next(raw, None)
        seen += 1
    if seen != count or next(cameras, None) is not None or next(dispatch, None) is not None:
        raise ValueError("Camera, dispatch and native event counts disagree")
    _verify(root, files)
    return {"root": root, "files": files, "configuration_checksums": config,
            "worker_run_id": identity, "image_count": count, "image_bytes": image_bytes,
            "anchor_control": anchor_control,
            "position_authority": False, "training_qualification": "not_established"}


def export_sensor_replay(session, output):
    root, output = Path(session).resolve(), Path(output).resolve()
    if output.is_relative_to(root) or root.is_relative_to(output) or output.exists():
        raise ValueError("Replay output must be new and outside the source session")
    checked = validate_recorded_session(root)
    output.mkdir(parents=True, exist_ok=False)
    copied = {}
    for name, expected in checked["files"].items():
        if name.startswith("vio/recording/"):
            target = name.removeprefix("vio/recording/")
        elif name.startswith("vio/input/") and name.removeprefix("vio/input/") in checked["configuration_checksums"]:
            target = name.removeprefix("vio/input/")
        else:
            target = "source_evidence/"+name
        if target in copied or target == "input_manifest.json":
            raise ValueError("Recording output path collision")
        destination = output / target
        destination.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(_path(root, name), destination)
        if digest(destination) != expected:
            raise ValueError("Recording changed while copying: " + name)
        copied[target] = expected
    # Never bless mutation during a long copy as a new valid input. A failed
    # export remains an unsealed partial directory, not a runnable capture.
    _verify(root, checked["files"])
    _verify(output, copied)
    try:
        source = {
            "worker_run_id": checked["worker_run_id"], "session_report_sha256": checked["files"]["session_report.json"],
            "camera_frames": checked["image_count"], "training_qualification": "not_established",
            "imu_policy": "exact_dispatched_subset_warmup_and_tail_preserved_in_source_evidence",
            "clock_policy": "original_per_frame_fit_no_refitting", "gps_policy": "audit_only_not_estimator_input",
            "exporter_sha256": digest(__file__)}
        if checked["anchor_control"]:
            source["anchor_control"] = checked["anchor_control"]
        manifest = seal_input(output, "recorded_live_sensor_session_exact_dispatched_events", source)
        _verify(root, checked["files"])
        _verify(output, copied)
        validate_capture(output)
    except Exception:
        (output / "input_manifest.json").unlink(missing_ok=True)
        raise
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--session", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args(argv)
    result = export_sensor_replay(args.session, args.output)
    print(json.dumps({"output": str(args.output.resolve()), "source": result["source"], "position_authority": False}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
