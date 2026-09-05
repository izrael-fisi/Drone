from queue import Queue
from contextlib import contextmanager
from pathlib import Path
import threading
import weakref

import cv2
import numpy as np
import pytest

from vision_nav.camera_ingress import CapturedFrame
from vision_nav.neural.latest_capture import LatestCaptureWorker


class ControlledCamera:
    def __init__(self):
        self.requests, self.commands = Queue(), Queue()
        self.owner = threading.get_ident()
        self.closed = threading.Event()
        self.index = 0

    def capture(self, output):
        assert threading.get_ident() == self.owner
        self.requests.put(self.index)
        command = self.commands.get(timeout=5)
        if isinstance(command, Exception):
            raise command
        output.write_bytes(str(self.index).encode())
        frame = CapturedFrame(output, "controlled", sensor_timestamp_ns=self.index + 100,
                              quality={"index": self.index})
        self.index += 1
        return frame

    def close(self):
        assert threading.get_ident() == self.owner
        self.closed.set()


class MemoryCamera(ControlledCamera):
    def capture(self, output):
        raise AssertionError("Memory-capable camera must not use file capture")

    def capture_memory(self, output):
        assert threading.get_ident() == self.owner
        self.requests.put(self.index)
        command = self.commands.get(timeout=5)
        if isinstance(command, Exception):
            raise command
        pixels = np.full((8, 8, 3), self.index, dtype=np.uint8)
        pixels.setflags(write=False)
        self.last_buffer = weakref.ref(pixels)
        frame = CapturedFrame(output, "memory", sensor_timestamp_ns=100 + self.index,
                              quality={"index": self.index}, bgr=pixels)
        self.index += 1
        return frame


def start_controlled(tmp_path, camera_class=ControlledCamera):
    created = Queue()
    def factory():
        camera = camera_class()
        created.put(camera)
        return camera
    worker = LatestCaptureWorker(factory, tmp_path, interval_s=0.001)
    worker.start()
    camera = created.get(timeout=2)
    assert camera.requests.get(timeout=2) == 0
    return worker, camera


def finish(worker, camera):
    # The blocked call cannot be interrupted by this thread. It must not be
    # reported stopped, and only the camera owner may close it after release.
    assert worker.stop(timeout_s=0) is False
    assert worker.status()["state"] == "shutdown_timeout"
    camera.commands.put("release")
    assert worker.stop(timeout_s=2)
    assert camera.closed.is_set()
    assert not worker.status()["thread_alive"]


def test_capture_continues_with_no_consumer_and_only_latest_is_retained(tmp_path):
    untouched = tmp_path / "existing.jpg"
    untouched.write_bytes(b"user evidence")
    worker, camera = start_controlled(tmp_path)
    try:
        for next_index in range(1, 5):
            camera.commands.put("capture")
            assert camera.requests.get(timeout=2) == next_index
        status = worker.status()
        assert status["captured_count"] == 4 and status["superseded_count"] == 3
        assert status["pending_count"] == status["pending_capacity"] == 1
        assert len(list(tmp_path.glob(".pending-*/*.jpg"))) == 1
        frame = worker.take(timeout_s=0)
        assert frame.path.read_bytes() == b"3"
        assert frame.sensor_timestamp_ns == 103 and frame.quality == {"index": 3}
        assert frame.path.parent == tmp_path
        assert worker.status()["pending_count"] == 0
        with pytest.raises(TimeoutError):
            worker.take(timeout_s=0)
        camera.commands.put("capture")
        assert camera.requests.get(timeout=2) == 5
        assert worker.status()["captured_count"] == 5
        assert frame.path.read_bytes() == b"3"
    finally:
        finish(worker, camera)
    assert not list(tmp_path.glob(".pending-*"))
    assert frame.path.read_bytes() == b"3" and untouched.read_bytes() == b"user evidence"
    with pytest.raises(RuntimeError, match="restarted"):
        worker.start()


def test_camera_error_is_visible_and_does_not_deliver_old_pending_frame(tmp_path):
    worker, camera = start_controlled(tmp_path)
    camera.commands.put("capture")
    assert camera.requests.get(timeout=2) == 1
    camera.commands.put(ValueError("camera disconnected"))
    assert camera.closed.wait(2)
    assert worker.stop(timeout_s=2)
    with pytest.raises(RuntimeError, match="camera disconnected"):
        worker.take(timeout_s=0)
    assert worker.status()["state"] == "failed"
    assert not list(tmp_path.glob(".pending-*"))


def test_evidence_promotion_cannot_overwrite_existing_file(tmp_path):
    worker, camera = start_controlled(tmp_path)
    try:
        camera.commands.put("capture")
        assert camera.requests.get(timeout=2) == 1
        pending = next(tmp_path.glob(".pending-*/*.jpg"))
        destination = tmp_path / f"{pending.parent.name[1:]}-{pending.name}"
        destination.write_bytes(b"already exists")
        with pytest.raises(FileExistsError):
            worker.take(timeout_s=0)
        assert destination.read_bytes() == b"already exists"
        assert pending.exists()
    finally:
        finish(worker, camera)
    assert destination.read_bytes() == b"already exists"


def test_returned_external_path_never_deleted(tmp_path):
    outside = tmp_path / "outside.jpg"
    outside.write_bytes(b"keep")
    closed = threading.Event()
    class Camera:
        def capture(self, _):
            return CapturedFrame(outside, "bad")
        def close(self):
            closed.set()
    worker = LatestCaptureWorker(Camera, tmp_path / "frames")
    worker.start()
    assert closed.wait(2)
    assert worker.stop()
    assert worker.status()["state"] == "failed"
    assert outside.read_bytes() == b"keep"


def test_factory_failure_and_stop_before_start(tmp_path):
    def fail():
        raise RuntimeError("no camera")
    worker = LatestCaptureWorker(fail, tmp_path)
    worker.start()
    with pytest.raises(RuntimeError, match="no camera"):
        worker.take(timeout_s=2)
    assert worker.stop()
    assert worker.status()["state"] == "failed"
    assert not list(tmp_path.glob(".pending-*"))
    never_started = LatestCaptureWorker(fail, tmp_path)
    assert never_started.stop()
    with pytest.raises(RuntimeError, match="restarted"):
        never_started.start()


@pytest.mark.parametrize("interval", [0, -1, True, float("nan"), float("inf")])
def test_invalid_capture_cadence(tmp_path, interval):
    with pytest.raises(ValueError):
        LatestCaptureWorker(lambda: None, tmp_path, interval_s=interval)


def test_memory_capture_only_writes_selected_frames(tmp_path):
    worker, camera = start_controlled(tmp_path, MemoryCamera)
    try:
        camera.commands.put("capture")
        assert camera.requests.get(timeout=2) == 1
        first_buffer = camera.last_buffer
        for index in range(2, 5):
            camera.commands.put("capture")
            assert camera.requests.get(timeout=2) == index
        assert first_buffer() is None
        assert not list(tmp_path.rglob("*.jpg"))
        status = worker.status()
        assert status["memory_capture_count"] == 4 and status["file_capture_count"] == 0
        assert status["pending_memory_bytes"] == 8 * 8 * 3
        assert status["superseded_count"] == 3
        frame = worker.take(timeout_s=0)
        assert frame.bgr is None and frame.sensor_timestamp_ns == 103
        assert cv2.imread(str(frame.path))[0, 0].tolist() == [3, 3, 3]
        assert frame.quality == {"index": 3}
        assert len(list(tmp_path.rglob("*.jpg"))) == 1
        assert worker.status()["pending_memory_bytes"] == 0
    finally:
        finish(worker, camera)
    assert frame.path.exists() and not list(tmp_path.glob(".pending-*"))


def test_memory_encoding_does_not_block_capture(tmp_path, monkeypatch):
    worker, camera = start_controlled(tmp_path, MemoryCamera)
    entered, release = threading.Event(), threading.Event()
    results = Queue()
    original = cv2.imencode
    def slow_encode(*args):
        entered.set()
        assert release.wait(3)
        return original(*args)
    monkeypatch.setattr(cv2, "imencode", slow_encode)
    def consume():
        try:
            results.put(worker.take(timeout_s=1))
        except Exception as exc:
            results.put(exc)
    consumer = threading.Thread(target=consume)
    try:
        camera.commands.put("capture")
        assert camera.requests.get(timeout=2) == 1
        consumer.start()
        assert entered.wait(2)
        for index in range(2, 5):
            camera.commands.put("capture")
            assert camera.requests.get(timeout=2) == index
        assert worker.status()["memory_capture_count"] == 4
        assert worker.status()["superseded_count"] == 2
        release.set()
        consumer.join(2)
        frame = results.get(timeout=1)
        assert isinstance(frame, CapturedFrame)
        assert frame.sensor_timestamp_ns == 100
    finally:
        release.set()
        if consumer.ident is not None:
            consumer.join(3)
        finish(worker, camera)


@pytest.mark.parametrize("fault", ["encode", "collision"])
def test_memory_persistence_failure_is_not_success(tmp_path, monkeypatch, fault):
    worker, camera = start_controlled(tmp_path, MemoryCamera)
    try:
        camera.commands.put("capture")
        assert camera.requests.get(timeout=2) == 1
        if fault == "encode":
            monkeypatch.setattr(cv2, "imencode", lambda *_: (False, None))
            error = RuntimeError
        else:
            spool = next(tmp_path.glob(".pending-*"))
            target = tmp_path / f"{spool.name[1:]}-capture-000000000.jpg"
            target.write_bytes(b"existing evidence")
            error = FileExistsError
        with pytest.raises(error):
            worker.take(timeout_s=0)
        assert worker.status()["delivered_count"] == 0
        assert worker.status()["pending_count"] == 0
        if fault == "collision":
            assert target.read_bytes() == b"existing evidence"
        else:
            assert not list(tmp_path.rglob("*.jpg"))
    finally:
        finish(worker, camera)


@pytest.mark.parametrize("fault", ["mutable", "dtype", "channels", "empty", "view", "written"])
def test_memory_capture_validates_buffer_contract(tmp_path, fault):
    closed = threading.Event()
    class Camera:
        def capture_memory(self, output):
            pixels = np.zeros((8, 8, 3), dtype=np.uint8)
            if fault == "dtype":
                pixels = pixels.astype(np.float32)
            elif fault == "channels":
                pixels = np.zeros((8, 8), dtype=np.uint8)
            elif fault == "empty":
                pixels = np.zeros((0, 8, 3), dtype=np.uint8)
            elif fault == "view":
                pixels = pixels[:4]
            elif fault == "written":
                output.write_bytes(b"unexpected disk write")
            if fault != "mutable":
                pixels.setflags(write=False)
            return CapturedFrame(output, "bad", bgr=pixels)
        def close(self):
            closed.set()
    worker = LatestCaptureWorker(Camera, tmp_path)
    worker.start()
    assert closed.wait(2)
    assert worker.stop()
    with pytest.raises(RuntimeError, match="Memory capture requires"):
        worker.take(timeout_s=0)
    assert worker.status()["captured_count"] == 0
    assert not list(tmp_path.glob(".pending-*"))


def test_frame_metadata_never_copies_image(tmp_path):
    class NoCopyArray(np.ndarray):
        def __deepcopy__(self, memo):
            raise AssertionError("Image must not enter metadata serialization")
    pixels = np.zeros((8, 8, 3), dtype=np.uint8).view(NoCopyArray)
    frame = CapturedFrame(tmp_path / "reserved.jpg", "memory", bgr=pixels, quality={"test": [1]})
    metadata = frame.to_dict()
    assert "bgr" not in metadata
    metadata["quality"]["test"].append(2)
    assert frame.quality == {"test": [1]}


def test_failed_memory_write_removes_only_its_partial_evidence(tmp_path, monkeypatch):
    worker, camera = start_controlled(tmp_path, MemoryCamera)
    untouched = tmp_path / "existing.jpg"
    untouched.write_bytes(b"keep")
    original = Path.open
    @contextmanager
    def failing_open(path, mode="r", *args, **kwargs):
        with original(path, mode, *args, **kwargs) as handle:
            if mode != "xb":
                yield handle
            else:
                class BrokenWriter:
                    def write(self, data):
                        handle.write(data[:4])
                        raise OSError("disk full")
                yield BrokenWriter()
    try:
        camera.commands.put("capture")
        assert camera.requests.get(timeout=2) == 1
        monkeypatch.setattr(Path, "open", failing_open)
        with pytest.raises(OSError, match="disk full"):
            worker.take(timeout_s=0)
        assert list(tmp_path.rglob("*.jpg")) == [untouched]
        assert worker.status()["delivered_count"] == 0
    finally:
        finish(worker, camera)
    assert untouched.read_bytes() == b"keep"
