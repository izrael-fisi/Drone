"""Owned, killable ONNX process with one bounded image-window request in flight.

The spawned child receives immutable pixel copies and calibration/model paths,
never a camera, serial endpoint, sensor-session object or navigation transport.
Socket messages contain length-bounded JSON and raw uint8 pixels, not pickles.
"""

import hashlib
import json
import math
import multiprocessing
from pathlib import Path
import select
import socket
import struct
import threading
import time
import uuid

import numpy as np

from ..runtime.process_resources import OwnedProcessResources
from ..vio.live_worker import LiveFrameTicket
from ..vio.openvins_replay import digest
from .live_sensor_inference import (SCHEMA, SUCCESS, TERMINAL, WindowModelInference,
                                    attach_native_progress, inference_files)


MAX_METADATA = 256*1024
MAX_RESPONSE = 4*1024*1024


def _json(value):
    return json.dumps(value, sort_keys=True, allow_nan=False).encode()


def pack_window(item, request_id, max_bytes):
    tickets = item["tickets"]
    if not 2 <= len(tickets) <= 16 or len(item["contexts"]) != len(tickets):
        raise ValueError("IPC window length is invalid")
    descriptors, pixels, size = [], [], 0
    for ticket in tickets:
        if not isinstance(ticket, LiveFrameTicket):
            raise ValueError("IPC requires native image tickets")
        value = ticket.pixels
        if (not isinstance(value, np.ndarray) or value.dtype != np.uint8 or value.ndim not in (2, 3)
                or value.flags.writeable or any(v <= 0 for v in value.shape)
                or value.ndim == 3 and value.shape[2] != 3):
            raise ValueError("IPC requires immutable uint8 gray/BGR pixels")
        size += value.nbytes
        if size > max_bytes:
            raise ValueError("IPC image window exceeds memory budget")
        raw = value.tobytes()
        if hashlib.sha256(raw).hexdigest() != ticket.pixel_sha256:
            raise ValueError("IPC pixels differ from native image ticket")
        pixels.append(raw)
        descriptors.append({"worker_run_id": ticket.worker_run_id, "sequence": ticket.sequence,
            "timestamp_ns": ticket.timestamp_ns, "pixel_sha256": ticket.pixel_sha256,
            "image_sha256": ticket.image_sha256, "shape": list(value.shape), "bytes": len(raw)})
    metadata = _json({"request_id": request_id, "worker_run_id": item["worker_run_id"],
                      "tickets": descriptors, "contexts": item["contexts"]})
    if len(metadata) > MAX_METADATA:
        raise ValueError("IPC window metadata exceeds limit")
    return struct.pack("!I", len(metadata))+metadata+b"".join(pixels)


def unpack_window(payload, max_bytes):
    if len(payload) < 4 or len(payload) > 4+MAX_METADATA+max_bytes:
        raise ValueError("IPC window size is invalid")
    length = struct.unpack("!I", payload[:4])[0]
    if not 0 < length <= MAX_METADATA or 4+length > len(payload):
        raise ValueError("IPC metadata size is invalid")
    metadata = json.loads(payload[4:4+length])
    entries, offset, tickets = metadata["tickets"], 4+length, []
    if not 2 <= len(entries) <= 16 or len(metadata["contexts"]) != len(entries):
        raise ValueError("IPC window shape is invalid")
    for entry in entries:
        shape = entry["shape"]
        if (not isinstance(shape, list) or len(shape) not in (2, 3)
                or any(type(v) is not int or v <= 0 for v in shape)
                or len(shape) == 3 and shape[2] != 3):
            raise ValueError("IPC pixel shape is invalid")
        nbytes = math.prod(shape)
        if (type(entry["bytes"]) is not int or entry["bytes"] != nbytes
                or offset+nbytes-(4+length) > max_bytes or offset+nbytes > len(payload)):
            raise ValueError("IPC pixel size is invalid")
        raw = payload[offset:offset+nbytes]
        offset += nbytes
        if hashlib.sha256(raw).hexdigest() != entry["pixel_sha256"]:
            raise ValueError("IPC pixel checksum mismatch")
        pixels = np.frombuffer(raw, dtype=np.uint8).reshape(shape)
        tickets.append(LiveFrameTicket(entry["worker_run_id"], entry["sequence"], entry["timestamp_ns"],
            entry["pixel_sha256"], entry["image_sha256"], pixels))
    if offset != len(payload):
        raise ValueError("IPC window has trailing bytes")
    return metadata["request_id"], {"worker_run_id": metadata["worker_run_id"],
                                    "tickets": tuple(tickets), "contexts": metadata["contexts"]}


def _recv_exact(connection, count):
    data = bytearray()
    while len(data) < count:
        chunk = connection.recv(min(count-len(data), 65536))
        if not chunk:
            raise EOFError("inference_socket_closed")
        data.extend(chunk)
    return bytes(data)


def _worker_main(connection, config):
    """Child-only blocking I/O; the parent owns all wall-clock deadlines."""
    try:
        engine = WindowModelInference(**config["binding"])
        if engine.fixed_hashes != config["expected_files"]:
            raise ValueError("Model initialization changed bound source files")
        ready = _json({"type": "ready", "worker_id": config["worker_id"], "provenance": engine.provenance})
        if len(ready) > MAX_RESPONSE:
            raise ValueError("Inference provenance exceeds response limit")
        connection.sendall(struct.pack("!Q", len(ready))+ready)
        while True:
            length = struct.unpack("!Q", _recv_exact(connection, 8))[0]
            if not 0 < length <= config["max_window_bytes"]+MAX_METADATA+4:
                raise ValueError("Inference request exceeds limit")
            payload = _recv_exact(connection, length)
            request_id, item = unpack_window(payload, config["max_window_bytes"])
            row = engine.process(item)
            response = _json({"type": "result", "worker_id": config["worker_id"], "request_id": request_id,
                "request_sha256": hashlib.sha256(payload).hexdigest(), "row": row})
            if len(response) > MAX_RESPONSE:
                raise ValueError("Inference result exceeds response limit")
            connection.sendall(struct.pack("!Q", len(response))+response)
    except EOFError:
        pass
    except Exception as exc:
        try:
            failure = _json({"type": "error", "worker_id": config["worker_id"], "reason": f"{type(exc).__name__}:{exc}"[:2000]})
            connection.sendall(struct.pack("!Q", len(failure))+failure)
        except OSError:
            pass
    finally:
        connection.close()


class ProcessWindowInference:
    """Single caller owns start/process/stop; cancel may be called from any thread.

    A timeout or protocol error permanently disables this worker. It never
    retries an old window or restarts a process under the same sensor epoch.
    """

    worker_entry = staticmethod(_worker_main)

    def __init__(self, session, export_dir, reference_map, tile_layout, calibration, camera_to_body, *,
                 retrieval_export, retrieval_index, retrieval_top_k=4, local_verification_config=None,
                 min_sharpness=20., max_saturated_fraction=.65, startup_timeout_s=120., inference_timeout_s=5.,
                 max_window_bytes=64*1024*1024):
        if session.status()["state"] != "not_started":
            raise ValueError("Bind learned worker before sensor acquisition")
        for name, value, low, high in (("startup_timeout_s", startup_timeout_s, .05, 300.),
                                      ("inference_timeout_s", inference_timeout_s, .05, 60.)):
            if type(value) not in (int, float) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f"{name} must be in [{low},{high}]")
        if type(max_window_bytes) is not int or not 1024 <= max_window_bytes <= 256*1024*1024:
            raise ValueError("max_window_bytes must be in [1 KiB,256 MiB]")
        self.session, self.worker_id = session, str(uuid.uuid4())
        self.startup_timeout, self.timeout, self.max_bytes = startup_timeout_s, inference_timeout_s, max_window_bytes
        self.fixed_hashes = inference_files(export_dir, reference_map, tile_layout, calibration,
            camera_to_body, retrieval_export, retrieval_index, local_verification_config)
        profile = session.native.capture / "sensor_profile"
        self.fixed_hashes[str(profile / "sensor_profile.json")] = digest(profile / "sensor_profile.json")
        self.fixed_hashes.update({str(profile / p): value for p, value in session.native._profile["checksums"].items()})
        self._worker_source_hash = digest(__file__)
        self._binding = dict(sensor_profile=str(profile), worker_run_id=session.native.worker_run_id,
            window_size=session._window_size, export_dir=str(Path(export_dir).resolve()),
            reference_map=str(Path(reference_map).resolve()), tile_layout=str(Path(tile_layout).resolve()),
            calibration=str(Path(calibration).resolve()), camera_to_body=str(Path(camera_to_body).resolve()),
            retrieval_export=str(Path(retrieval_export).resolve()), retrieval_index=str(Path(retrieval_index).resolve()),
            retrieval_top_k=retrieval_top_k, local_verification_config=str(Path(local_verification_config).resolve()) if local_verification_config else None,
            min_sharpness=min_sharpness, max_saturated_fraction=max_saturated_fraction)
        self._process = self._socket = self._resources = None
        self._pid = self._returncode = None
        self._shutdown_action = None
        self._state, self._error, self._request = "not_started", None, 0
        self._completed, self._cancelled, self._lock = 0, threading.Event(), threading.Lock()
        self._last_sequence = -1
        self.provenance = {"worker_run_id": session.native.worker_run_id, "files_sha256": self.fixed_hashes,
                           "execution": self._execution()}

    def _execution(self):
        return {"mode": "spawned_model_process", "worker_id": self.worker_id,
            "worker_source_sha256": self._worker_source_hash,
            "startup_timeout_s": self.startup_timeout, "inference_timeout_s": self.timeout,
            "max_window_bytes": self.max_bytes, "max_response_bytes": MAX_RESPONSE,
            "inflight_limit": 1, "automatic_restart": False,
            "ipc": "private_socket_length_bounded_json_and_uint8_pixels"}

    def verify_sources(self):
        if digest(__file__) != self._worker_source_hash:
            raise ValueError("Inference worker source changed")
        for path, expected in self.fixed_hashes.items():
            if digest(path) != expected:
                raise ValueError("Live learned source/release changed: "+path)

    def _check_wait(self, deadline):
        if self._cancelled.is_set():
            raise InterruptedError("inference_cancelled")
        if time.monotonic() >= deadline:
            raise TimeoutError("model_startup_timeout" if self._state == "starting" else "model_inference_timeout")
        if self.session.status()["state"] in TERMINAL-SUCCESS:
            raise InterruptedError("sensor_session_invalidated")

    def _transfer(self, data, deadline):
        pending = memoryview(struct.pack("!Q", len(data))+data)
        while pending:
            self._check_wait(deadline)
            _, ready, _ = select.select([], [self._socket], [], .02)
            if ready:
                try:
                    written = self._socket.send(pending[:65536])
                except BlockingIOError:
                    continue
                if written == 0:
                    raise EOFError("inference_socket_closed")
                pending = pending[written:]

    def _read(self, count, deadline):
        data = bytearray()
        while len(data) < count:
            self._check_wait(deadline)
            ready, _, _ = select.select([self._socket], [], [], .02)
            if ready:
                try:
                    chunk = self._socket.recv(min(count-len(data), 65536))
                except BlockingIOError:
                    continue
                if not chunk:
                    raise EOFError("inference_socket_closed")
                data.extend(chunk)
        return bytes(data)

    def _response(self, deadline):
        size = struct.unpack("!Q", self._read(8, deadline))[0]
        if not 0 < size <= MAX_RESPONSE:
            raise ValueError("Inference response length exceeds limit")
        response = json.loads(self._read(size, deadline))
        if response.get("worker_id") != self.worker_id:
            raise ValueError("Inference response worker identity mismatch")
        if response.get("type") == "error":
            raise RuntimeError("inference_child_error:"+str(response.get("reason")))
        return response

    def start(self):
        if self._state != "not_started" or self._cancelled.is_set():
            raise RuntimeError("Inference process cannot restart")
        self.verify_sources()
        self._state = "starting"
        parent, child = socket.socketpair()
        self._socket = parent
        self._socket.setblocking(False)
        config = {"binding": self._binding, "worker_id": self.worker_id,
                  "expected_files": self.fixed_hashes, "max_window_bytes": self.max_bytes}
        self._process = multiprocessing.get_context("spawn").Process(target=self.worker_entry,
            args=(child, config), name="vision-nav-learned-inference", daemon=True)
        try:
            self._process.start()
            child.close()
            self._pid = self._process.pid
            self._resources = OwnedProcessResources(self._pid, self.worker_id)
            ready = self._response(time.monotonic()+self.startup_timeout)
            if (ready.get("type") != "ready" or ready.get("provenance", {}).get("files_sha256") != self.fixed_hashes
                    or ready.get("provenance", {}).get("worker_run_id") != self.session.native.worker_run_id):
                raise ValueError("Inference startup provenance mismatch")
            self.verify_sources()
            self.provenance = {**ready["provenance"], "execution": self._execution()}
            self._state = "ready"
        except BaseException as exc:
            child.close()
            self._fail(exc)
            raise

    def process(self, item):
        if not self._lock.acquire(blocking=False):
            raise RuntimeError("Only one inference request may be in flight")
        began, before = time.monotonic_ns(), self.session.native.status()
        row = {"schema_version": SCHEMA, "worker_run_id": self.session.native.worker_run_id,
            "state": "REJECTED", "position_authority": False, "navigation_mode": "NO_GLOBAL_FIX",
            "px4_external_vision": "disabled", "global_fusion_connected": False, "stage_latency_ms": {}}
        try:
            anchor = item["tickets"][-1]
            row.update(sequence=anchor.sequence, anchor_vio_timestamp_ns=anchor.timestamp_ns,
                anchor_pixel_sha256=anchor.pixel_sha256, anchor_png_sha256=anchor.image_sha256)
            if self._state != "ready":
                raise RuntimeError("inference_worker_unavailable:"+str(self._error or self._state))
            if anchor.sequence <= self._last_sequence:
                raise ValueError("Duplicate or reordered inference window")
            self._last_sequence = anchor.sequence
            self.verify_sources()
            self._request += 1
            payload = pack_window(item, self._request, self.max_bytes)
            request_hash = hashlib.sha256(payload).hexdigest()
            self._state = "processing"
            deadline = began/1e9+self.timeout
            self._transfer(payload, deadline)
            response = self._response(deadline)
            result = response.get("row", {})
            if (response.get("type") != "result" or response.get("request_id") != self._request
                    or response.get("request_sha256") != request_hash
                    or result.get("worker_run_id") != self.session.native.worker_run_id
                    or result.get("sequence") != anchor.sequence
                    or result.get("anchor_pixel_sha256") != anchor.pixel_sha256
                    or result.get("anchor_vio_timestamp_ns") != anchor.timestamp_ns
                    or result.get("schema_version") != SCHEMA
                    or result.get("state") not in {"MODEL_EVIDENCE", "REJECTED"}
                    or result.get("position_authority") is not False or result.get("navigation_mode") != "NO_GLOBAL_FIX"
                    or result.get("px4_external_vision") != "disabled" or result.get("global_fusion_connected") is not False):
                raise ValueError("Inference response does not belong to the submitted shadow window")
            self.verify_sources()
            row = result
            self._completed += 1
            self._state = "ready"
        except (Exception, KeyboardInterrupt) as exc:
            self._fail(exc)
            row.update(state="REJECTED", reason=str(exc))
            if isinstance(exc, KeyboardInterrupt):
                raise
        finally:
            self._lock.release()
        row["provider_processing_latency_ms"] = row.get("processing_latency_ms")
        row.update(started_host_monotonic_ns=began, completed_host_monotonic_ns=time.monotonic_ns(),
            processing_latency_ms=(time.monotonic_ns()-began)/1e6, inference_worker=self.status())
        return attach_native_progress(self.session, row, before)

    def cancel(self):
        self._cancelled.set()

    def _fail(self, exc):
        self._error = self._error or f"{type(exc).__name__}:{exc}"[:2000]
        self._state = "failed"
        self.stop()

    def stop(self):
        self.cancel()
        if self._socket is not None:
            self._socket.close()
            self._socket = None
        process = self._process
        if process is not None and process.pid is None:
            process.close()
            self._process = None
            process = None
        if process is not None and process.pid is not None:
            self._shutdown_action = self._shutdown_action or "graceful_socket_close"
            process.join(.2)
            if process.is_alive():
                self._shutdown_action = "terminate"
                process.terminate()
                process.join(1.)
            if process.is_alive():
                self._shutdown_action = "kill"
                process.kill()
                process.join(1.)
            if process.is_alive():
                self._state, self._error = "shutdown_incomplete", self._error or "inference_process_did_not_stop"
                return False
            self._returncode = process.exitcode
            if self._returncode != 0:
                self._error = self._error or f"inference_shutdown_exit_failed:{self._returncode}"
                self._state = "failed"
            process.close()
            self._process = None
        if not self._error:
            self._state = "stopped"
        return True

    def resource_snapshot(self):
        if self._resources is None:
            return {"status": "unavailable", "reason": "inference_process_not_started", "role": "learned_inference"}
        return {**self._resources.snapshot(), "role": "learned_inference"}

    def status(self):
        process = self._process
        try:
            alive = bool(process and process.is_alive())
        except ValueError:
            alive = False  # The owner has just reaped and closed this handle.
        return {"schema_version": "vision_nav_learned_process_v1", "state": self._state, "error": self._error,
            "worker_id": self.worker_id, "source_worker_run_id": self.session.native.worker_run_id,
            "pid": self._pid, "returncode": self._returncode,
            "process_alive": alive, "shutdown_action": self._shutdown_action,
            "submitted_windows": self._request, "completed_windows": self._completed,
            "position_authority": False, "automatic_restart": False}
