"""Killable research noise/PnP/GTSAM worker, with no sensor or aircraft handles."""

from copy import deepcopy
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
import types
import uuid

from .delayed_alignment import _owned
from .bounded_json import dumps as bounded_dumps
from ..runtime.process_resources import OwnedProcessResources


LIMIT = 40*1024*1024
PROVIDER_LIMIT = 1024*1024
DEFAULT_WIRE_FORMAT = "msgpack_v1"


def _encode(value):
    return bounded_dumps(value, LIMIT).encode("ascii")


def _serialize(value, wire_format):
    if wire_format == "json_v1":
        return _encode(value)
    if wire_format != "msgpack_v1":
        raise ValueError("Unknown estimator wire format")
    from .bounded_msgpack import pack
    return pack(value, LIMIT)


def _decode(raw, wire_format=DEFAULT_WIRE_FORMAT):
    if wire_format == "json_v1":
        return json.loads(raw)
    if wire_format != "msgpack_v1":
        raise ValueError("Unknown estimator wire format")
    from .bounded_msgpack import unpack
    return unpack(raw, LIMIT)


def _digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def _sources():
    root = Path(__file__).resolve().parent
    return {str(p): _digest(p) for p in sorted(root.glob("*.py"))} | {
        str(root.parent / name): _digest(root.parent / name) for name in (
            "vio/pose_window.py", "neural/bounded_pnp.py", "neural/shadow_geometry.py",
            "neural/candidate_groups.py")}


def _verify(config):
    for path, expected in config["sources"].items():
        if _digest(path) != expected:
            raise ValueError("Research estimator/provider source changed: "+path)


def _exact(connection, count):
    chunks = bytearray()
    while len(chunks) < count:
        chunk = connection.recv(min(count-len(chunks), 65536))
        if not chunk:
            raise EOFError("alignment_socket_closed")
        chunks.extend(chunk)
    return bytes(chunks)


def _send(connection, value, wire_format=DEFAULT_WIRE_FORMAT):
    data = _serialize(value, wire_format)
    connection.sendall(struct.pack("!Q", len(data))+data)


def _worker_main(connection, config):
    numerical_limit = None
    try:
        _verify(config)
        # Compile exactly the checked bytes, without a stale bytecode cache.
        path = config["provider_path"]
        code = Path(path).read_bytes()
        if len(code) > PROVIDER_LIMIT or hashlib.sha256(code).hexdigest() != config["sources"][path]:
            raise ValueError("Provider bytes changed")
        provider = types.ModuleType("vision_nav_explicit_research_noise")
        provider.__file__ = path
        exec(compile(code, path, "exec"), provider.__dict__)
        if not callable(getattr(provider, "attempt_noise", None)) or not callable(getattr(provider, "joint_noise", None)):
            raise ValueError("Provider requires attempt_noise(request, config) and joint_noise(request, config)")
        from .learned_pnp_binding import bind_uncertainty
        from .pnp_window_alignment import align, _align_owned, PnPAssessmentCache
        from .canonical_identity import encode as canonical_encode, object_digest
        import gtsam  # Include solver import in the startup deadline, not the first fresh fix.
        from scipy import stats  # The consistency gate also has a nontrivial cold import.
        from threadpoolctl import threadpool_info, threadpool_limits
        import importlib.metadata
        numerical_limit = threadpool_limits(limits=config["numerical_threads"])
        pools = threadpool_info()
        names = ("gtsam", "numpy", "scipy", "threadpoolctl")
        if config["wire_format"] == "msgpack_v1":
            names += ("msgpack",)
        versions = {name: importlib.metadata.version(name) for name in names}
        assessment_cache = PnPAssessmentCache()
        _verify(config)
        _send(connection, {"type": "ready", "worker_id": config["worker_id"],
                           "sources": config["sources"], "versions": versions,
                           "numerical_threadpools": pools, "wire_format": config["wire_format"]}, config["wire_format"])
        while True:
            size = struct.unpack("!Q", _exact(connection, 8))[0]
            if not 0 < size <= LIMIT:
                raise ValueError("Alignment request exceeds limit")
            raw = _exact(connection, size)
            decoded_started = time.monotonic_ns()
            message = _decode(raw, config["wire_format"])
            operation, request = message["operation"], message["request"]
            decoded_finished = time.monotonic_ns()
            _verify(config)
            work_started = time.monotonic_ns()
            if operation == "bind":
                noise = provider.attempt_noise(deepcopy(request["request_without_uncertainty"]), deepcopy(config["provider_config"]))
                result = bind_uncertainty(request, noise)
            elif operation == "noise":
                result = provider.joint_noise(deepcopy(request), deepcopy(config["provider_config"]))
            elif operation == "align":
                result = align(request, assessment_cache=assessment_cache)
            elif operation == "align_with_noise":
                if "joint_uncertainty" in request:
                    raise ValueError("Combined alignment requires an unbound noise request")
                began = time.monotonic_ns()
                noise = _owned(provider.joint_noise(deepcopy(request), deepcopy(config["provider_config"])), 32*1024*1024)
                if not isinstance(noise, dict):
                    raise ValueError("Provider must return an explicit joint uncertainty object")
                noise_finished = time.monotonic_ns()
                # The provider received a copy. Hash each original field once
                # for the unbound request, native identity and bound result.
                encoded_fields = {key: canonical_encode(value) for key, value in request.items()}
                aligned = _align_owned({**request, "joint_uncertainty": noise}, assessment_cache=assessment_cache,
                    encoded_fields={**encoded_fields, "joint_uncertainty": canonical_encode(noise)})
                result = {"input_request_sha256": object_digest(encoded_fields),
                    "joint_uncertainty": noise, "result": aligned,
                    "timing_ms": {"joint_noise": (noise_finished-began)/1e6,
                                  "alignment": (time.monotonic_ns()-noise_finished)/1e6}}
            else:
                raise ValueError("Unknown alignment operation")
            work_finished = time.monotonic_ns()
            _verify(config)
            verified_finished = time.monotonic_ns()
            _send(connection, {"type": "result", "worker_id": config["worker_id"],
                "request_id": message["request_id"], "operation": operation,
                "request_sha256": hashlib.sha256(raw).hexdigest(), "result": result,
                "pnp_assessment_cache": assessment_cache.status(),
                "child_timing_ms": {
                    "request_decode": (decoded_finished-decoded_started)/1e6,
                    "source_verification_before": (work_started-decoded_finished)/1e6,
                    "operation": (work_finished-work_started)/1e6,
                    "source_verification_after": (verified_finished-work_finished)/1e6,
                }}, config["wire_format"])
    except EOFError:
        pass
    except Exception as exc:
        try:
            _send(connection, {"type": "error", "worker_id": config["worker_id"],
                               "reason": f"{type(exc).__name__}:{exc}"[:2000]}, config["wire_format"])
        except OSError:
            pass
    finally:
        if numerical_limit is not None:
            numerical_limit.restore_original_limits()
        connection.close()


class ProcessAlignmentEngine:
    """One synchronous caller; cancel/stop may interrupt it from another thread.

    The provider is explicit trusted local Python code, not sandboxed. It is
    never imported in the parent. The child receives JSON-shaped data only, no session,
    live frame objects, raw images, sensor endpoints or publication handles.
    """

    worker_entry = staticmethod(_worker_main)

    def __init__(self, provider_path, *, provider_config=None, startup_timeout_s=20., operation_timeout_s=2., numerical_threads=1,
                 wire_format=DEFAULT_WIRE_FORMAT):
        for value, upper in ((startup_timeout_s, 120.), (operation_timeout_s, 30.)):
            if type(value) not in (int, float) or not math.isfinite(value) or not .05 <= value <= upper:
                raise ValueError("Invalid estimator startup/operation deadline")
        if type(numerical_threads) is not int or not 1 <= numerical_threads <= 4:
            raise ValueError("Estimator numerical thread budget must be an integer in [1,4]")
        if type(wire_format) is not str or wire_format not in ("json_v1", "msgpack_v1"):
            raise ValueError("Expected json_v1 or msgpack_v1 estimator wire format")
        path = Path(provider_path).resolve(strict=True)
        if not path.is_file() or path.suffix != ".py" or path.stat().st_size > PROVIDER_LIMIT:
            raise ValueError("Expected a local Python noise provider of at most 1 MiB")
        self.worker_id = str(uuid.uuid4())
        self._config = {"worker_id": self.worker_id, "provider_path": str(path),
            "numerical_threads": numerical_threads, "wire_format": wire_format,
            "provider_config": _owned(provider_config if provider_config is not None else {}, 32*1024),
            "sources": _sources() | {str(path): _digest(path)}}
        if not isinstance(self._config["provider_config"], dict):
            raise ValueError("Provider configuration must be an object")
        self.startup_timeout, self.timeout = startup_timeout_s, operation_timeout_s
        self._cancelled, self._call_lock, self._cleanup_lock = threading.Event(), threading.Lock(), threading.RLock()
        self._process = self._socket = self._resources = None
        self._state, self._error, self._versions = "not_started", None, {}
        self._pid = self._returncode = self._shutdown_action = None
        self._request = self._completed = 0
        self._last_operation = None
        self._numerical_threadpools = None
        self._last_receive_profile = None

    def _check(self, deadline):
        if self._cancelled.is_set():
            raise InterruptedError("alignment_cancelled")
        if time.monotonic() >= deadline:
            raise TimeoutError("alignment_startup_timeout" if self._state == "starting" else "alignment_operation_timeout")

    def _read(self, count, deadline):
        output = bytearray()
        connection = self._socket
        while len(output) < count:
            self._check(deadline)
            ready, _, _ = select.select([connection], [], [], .02)
            if ready:
                try:
                    part = connection.recv(min(count-len(output), 65536))
                except BlockingIOError:
                    continue
                if not part:
                    raise EOFError("alignment_socket_closed")
                output.extend(part)
        self._check(deadline)
        return bytes(output)

    def _response(self, deadline):
        started = time.monotonic_ns()
        size = struct.unpack("!Q", self._read(8, deadline))[0]
        if not 0 < size <= LIMIT:
            raise ValueError("Alignment response exceeds limit")
        raw = self._read(size, deadline)
        received = time.monotonic_ns()
        response = _decode(raw, self._config["wire_format"])
        decoded = time.monotonic_ns()
        self._last_receive_profile = {
            "response_bytes": size,
            "receive_wait_ms": (received-started)/1e6,
            "response_decode_ms": (decoded-received)/1e6,
        }
        if response.get("worker_id") != self.worker_id:
            raise ValueError("Alignment worker identity mismatch")
        if response.get("type") == "error":
            raise RuntimeError("alignment_child_error:"+str(response.get("reason")))
        return response

    def start(self):
        if self._state != "not_started" or self._cancelled.is_set():
            raise RuntimeError("Alignment worker cannot restart")
        _verify(self._config)
        _serialize({}, self._config["wire_format"])
        self._state = "starting"
        parent, child = socket.socketpair()
        self._socket = parent
        parent.setblocking(False)
        self._process = multiprocessing.get_context("spawn").Process(target=self.worker_entry,
            args=(child, self._config), name="vision-nav-research-alignment", daemon=True)
        try:
            self._process.start()
            child.close()
            self._pid = self._process.pid
            self._resources = OwnedProcessResources(self._pid, self.worker_id)
            response = self._response(time.monotonic()+self.startup_timeout)
            if (response.get("type") != "ready" or response.get("sources") != self._config["sources"]
                    or response.get("wire_format") != self._config["wire_format"]):
                raise ValueError("Alignment startup provenance mismatch")
            _verify(self._config)
            self._versions, self._state = response["versions"], "ready"
            self._numerical_threadpools = response.get("numerical_threadpools")
        except BaseException as exc:
            child.close()
            self._fail(exc)
            raise

    def _call(self, operation, request):
        if not self._call_lock.acquire(blocking=False):
            raise RuntimeError("Only one estimator request may be in flight")
        try:
            if self._state != "ready":
                raise RuntimeError("Alignment worker unavailable: "+str(self._error or self._state))
            began = time.monotonic()
            deadline = began+self.timeout
            _verify(self._config)
            self._request += 1
            raw = _serialize({"request_id": self._request, "operation": operation, "request": request}, self._config["wire_format"])
            encoded_at = time.monotonic()
            self._state = "processing"
            connection = self._socket
            pending = memoryview(struct.pack("!Q", len(raw))+raw)
            while pending:
                self._check(deadline)
                _, ready, _ = select.select([], [connection], [], .02)
                if ready:
                    try:
                        size = connection.send(pending[:65536])
                    except BlockingIOError:
                        continue
                    if not size:
                        raise EOFError("alignment_socket_closed")
                    pending = pending[size:]
            sent_at = time.monotonic()
            response = self._response(deadline)
            decoded_at = time.monotonic()
            if (response.get("type") != "result" or response.get("request_id") != self._request
                    or response.get("operation") != operation
                    or response.get("request_sha256") != hashlib.sha256(raw).hexdigest()
                    or not isinstance(response.get("result"), dict)):
                raise ValueError("Alignment response is not bound to the request")
            _verify(self._config)
            self._check(deadline)
            self._completed += 1
            finished_at = time.monotonic()
            self._last_operation = {"operation": operation, "elapsed_ms": (finished_at-began)*1000,
                "request_bytes": len(raw), "scope": "parent_encode_ipc_child_work_decode_and_verification",
                "pnp_assessment_cache": response.get("pnp_assessment_cache"),
                "transport_profile": {
                    **self._last_receive_profile,
                    "parent_verify_encode_ms": (encoded_at-began)*1000,
                    "parent_send_ms": (sent_at-encoded_at)*1000,
                    "parent_receive_response_ms": (decoded_at-sent_at)*1000,
                    "parent_final_verify_ms": (finished_at-decoded_at)*1000,
                    "child_timing_ms": response.get("child_timing_ms"),
                    "scope": "host_elapsed_stages_overlap_child_work_not_sensor_latency",
                }}
            self._state = "ready"
            return response["result"]
        except BaseException as exc:
            self._fail(exc)
            raise
        finally:
            self._call_lock.release()

    def bind(self, draft):
        return self._call("bind", draft)

    def joint_noise(self, request):
        return self._call("noise", request)

    def align(self, request):
        return self._call("align", request)

    def align_with_noise(self, request):
        """One deadline/round trip; return the exact noise for numerical replay."""
        return self._call("align_with_noise", request)

    def cancel(self):
        self._cancelled.set()

    def _fail(self, exc):
        self._error = self._error or f"{type(exc).__name__}:{exc}"[:2000]
        self._state = "failed"
        self.stop()

    def stop(self):
        self.cancel()
        with self._cleanup_lock:
            if self._socket is not None:
                self._socket.close()
                self._socket = None
            process = self._process
            if process is not None and process.pid is None:
                process.close()
                self._process = None
                process = None
            if process is not None:
                self._shutdown_action = self._shutdown_action or "graceful_socket_close"
                # Idle numerical workers may need time for interpreter/library
                # cleanup after EOF. A fault/in-flight cancellation stays short.
                process.join(1. if self._state == "ready" else .2)
                if process.is_alive():
                    self._shutdown_action = "terminate"
                    process.terminate()
                    process.join(1.)
                if process.is_alive():
                    self._shutdown_action = "kill"
                    process.kill()
                    process.join(1.)
                if process.is_alive():
                    self._state, self._error = "shutdown_incomplete", self._error or "alignment_process_did_not_stop"
                    return False
                self._returncode = process.exitcode
                if self._returncode != 0:
                    self._error = self._error or f"alignment_shutdown_exit_failed:{self._returncode}"
                process.close()
                self._process = None
            self._state = "failed" if self._error else "stopped"
            return True

    def resource_snapshot(self):
        if self._resources is None:
            return {"status": "unavailable", "reason": "alignment_process_not_started", "role": "research_alignment"}
        return {**self._resources.snapshot(), "role": "research_alignment"}

    def status(self):
        with self._cleanup_lock:
            alive = bool(self._process and self._process.is_alive())
            return {"schema_version": "vision_nav_research_alignment_process_v1", "state": self._state,
                "worker_id": self.worker_id, "pid": self._pid, "process_alive": alive,
                "error": self._error, "returncode": self._returncode, "shutdown_action": self._shutdown_action,
                "submitted_requests": self._request, "completed_requests": self._completed,
                "last_operation": deepcopy(self._last_operation),
                "numerical_threads": self._config["numerical_threads"],
                "numerical_threadpools": deepcopy(self._numerical_threadpools),
                "numerical_thread_scope": "detected_startup_blas_openmp_in_estimator_child_only",
                "startup_timeout_s": self.startup_timeout, "operation_timeout_s": self.timeout,
                "sources": deepcopy(self._config["sources"]), "provider_config": deepcopy(self._config["provider_config"]),
                "versions": deepcopy(self._versions), "max_message_bytes": LIMIT, "inflight_limit": 1,
                "wire_format": self._config["wire_format"], "evidence_and_identity_format": "canonical_json_unchanged",
                "automatic_restart": False, "provider_hard_timeout": True, "position_authority": False,
                "navigation_mode": "NO_GLOBAL_FIX", "px4_external_vision": "disabled"}
