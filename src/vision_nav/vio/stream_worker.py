"""Bounded native VIO process worker for sealed sensor-stream experiments."""

from collections import Counter, deque
from contextlib import ExitStack
from copy import deepcopy
import json
import math
import os
from pathlib import Path
import select
import subprocess
import threading
import time
import uuid

from .openvins_replay import REVISION, digest, validate_capture, verify_binary
from .anchor_control import AnchorCommand, AnchorInputClosed, MAX_REQUESTS, QUEUE_CAPACITY, SCHEMA, validate_ack
from . import map_frame_control as map_control
from vision_nav.runtime.process_resources import OwnedProcessResources


class NativeVioStreamWorker:
    """One process/pipe owner, bounded input queue, original sensor timestamps.

    Input images must belong to a sealed replay capture. This is the streaming
    execution boundary, not yet a live camera/IMU adapter or flight publisher.
    """

    def __init__(self, binary: Path, capture: Path, output: Path, *, capacity=2, batch_timeout_s=5., anchor_capacity=0, map_frame_control=False):
        self._initialize(binary, capture, output, capacity=capacity, batch_timeout_s=batch_timeout_s,
                         anchor_capacity=anchor_capacity, map_frame_control=map_frame_control)
        self.manifest = validate_capture(self.capture)
        source = self.manifest.get("source")
        if isinstance(source, dict) and source.get("anchor_control") is not None:
            raise ValueError("Recorded anchor controls require openvins_replay run --pose-window; sensor-only streaming would discard them")
        if isinstance(source, dict) and source.get("map_frame_control") is not None:
            raise ValueError("Recorded map-frame controls require their full ordered control stream, not sensor-only replay")
        self.manifest_sha256 = digest(self.capture / "input_manifest.json")

    def _initialize(self, binary, capture, output, *, capacity, batch_timeout_s, anchor_capacity=0, map_frame_control=False):
        """Shared process ownership; each input adapter establishes its own binding."""
        if os.name != "posix":
            raise ValueError("Native stream worker currently requires POSIX pipes")
        if type(capacity) is not int or not 1 <= capacity <= 8:
            raise ValueError("Batch capacity must be in [1,8]")
        if type(anchor_capacity) is not int or not 0 <= anchor_capacity <= 4:
            raise ValueError("Anchor capacity must be in [0,4]; zero disables control")
        if type(map_frame_control) is not bool:
            raise ValueError("Map-frame controls require an explicit boolean")
        if type(batch_timeout_s) not in (int, float) or not math.isfinite(batch_timeout_s) or batch_timeout_s <= 0:
            raise ValueError("Batch timeout must be finite and positive")
        self.binary, self.capture, self.output = binary.resolve(), capture.resolve(), output.resolve()
        self.worker_run_id = str(uuid.uuid4())
        if self.output.is_relative_to(self.capture):
            raise ValueError("Stream output must not modify input capture")
        self.native_build = verify_binary(self.binary, pose_window=True, stream_input=True,
                                         anchor_control=bool(anchor_capacity), map_frame_control=map_frame_control)
        self.binary_sha256 = digest(self.binary)
        self.capacity, self.timeout = capacity, batch_timeout_s
        self._condition = threading.Condition()
        self._queue, self._stop = deque(), threading.Event()
        self._thread = self._process = None
        self._state, self._error = "not_started", None
        self._closed = False
        self._submitted = self._completed = self._rejected_full = 0
        self._latest = self._latest_received_ns = None
        self._last_submitted_ns = 0
        self._returncode = None
        self._result_taken = 0
        self._snapshot_generation = 0
        self._batch_timings = deque(maxlen=256)
        self._active_batch_timing = None
        self._local_states = Counter()
        self._resource_probe = None
        self.anchor_capacity = anchor_capacity
        self.map_frame_control_enabled = map_frame_control
        self._map_submitted = self._map_completed = 0
        self._map_backpressure = 0
        self._map_last_ack = self._map_active = self._map_pending_snapshot = None
        self._pending_batches = self._pending_controls = 0
        self._control_submitted = self._control_completed = self._control_backpressure = 0
        self._active_anchors, self._last_anchor_ack = [], None
        self._last_posterior_ns = None
        self._control_inflight = None
        self._unapplied_controls = []
        self._control_source_hashes = {name: digest(Path(__file__).parent/name) for name in
            ("stream_worker.py", "anchor_control.py", "openvins_replay.py")} if anchor_capacity or map_frame_control else {}
        if map_frame_control:
            self._control_source_hashes.update({name: digest(Path(__file__).parent/name) for name in
                ("map_frame_control.py", "../estimation/bounded_json.py")})

    def start(self):
        with self._condition:
            if self._thread is not None or self._closed or self._stop.is_set():
                raise RuntimeError("VIO worker cannot be restarted")
            self.output.mkdir(parents=True, exist_ok=False)
            self._state = "starting"
            self._thread = threading.Thread(target=self._run, name="vision-nav-native-vio", daemon=True)
            self._thread.start()

    def submit(self, events: list[dict]) -> bool:
        """Offer IMU look-ahead followed by one camera; False means retry unchanged.

        Full queues do not consume the batch or advance sensor watermarks. The
        caller must retry or abort; it must not drop inertial samples and continue.
        """
        if not isinstance(events, list) or not 1 <= len(events) <= 1024:
            raise ValueError("Batch must contain 1..1024 sensor events")
        lines, size = [], 0
        for index, event in enumerate(events):
            if not isinstance(event, dict) or event.get("type") != ("camera" if index == len(events) - 1 else "imu"):
                raise ValueError("Batch must end with exactly one camera after IMU look-ahead")
            stamp = event.get("timestamp_ns")
            if type(stamp) is not int or not 0 < stamp < 2**63:
                raise ValueError("Sensor timestamp must be positive integer nanoseconds")
            if event["type"] == "camera":
                self._validate_image_reference(event)
            line = json.dumps(event, sort_keys=True, allow_nan=False).encode() + b"\n"
            if len(line) > 65536:
                raise ValueError("Sensor event exceeds 64 KiB")
            size += len(line)
            if size > 262144:
                raise ValueError("Sensor batch exceeds 256 KiB")
            lines.append(line)
        payload = b"".join(lines)
        stamp = json.loads(lines[-1])["timestamp_ns"]
        with self._condition:
            if self._thread is None or self._closed or self._stop.is_set() or self._state in {"failed", "stopped", "complete", "no_local_estimate"}:
                raise RuntimeError("VIO input is not accepting batches")
            if stamp <= self._last_submitted_ns:
                raise ValueError("Camera batches must have increasing sensor timestamps")
            if self._pending_batches >= self.capacity:
                self._rejected_full += 1
                return False
            self._queue.append((payload, stamp, None, time.monotonic_ns()))
            self._pending_batches += 1
            self._last_submitted_ns = stamp
            self._submitted += 1
            self._condition.notify_all()
            return True

    def request_anchor(self, operation, anchor_timestamp_ns, *, worker_run_id):
        """Queue retain/release between sensor batches. None means not submitted.

        A returned receipt is not proof of retention; require its native ACK.
        Controls reserve their own bounded slots and never drop sensor batches.
        """
        if operation not in ("retain", "release") or type(anchor_timestamp_ns) is not int or not 0 < anchor_timestamp_ns < 2**63:
            raise ValueError("Expected retain/release and an exact camera timestamp")
        if worker_run_id != self.worker_run_id:
            raise ValueError("Anchor belongs to a different native worker epoch")
        with self._condition:
            if (not self.anchor_capacity or self._thread is None
                    or self._state in {"failed", "stopped", "shutdown_incomplete"}
                    or self._stop.is_set() and self._state not in {"complete", "no_local_estimate"}):
                raise RuntimeError("Native anchor input is not accepting commands")
            if self._closed or self._state in {"complete", "no_local_estimate"}:
                raise AnchorInputClosed("Native anchor input closed before command submission")
            if self._control_submitted >= MAX_REQUESTS:
                raise RuntimeError("Native anchor command budget exhausted; finish this epoch")
            if self._pending_controls >= QUEUE_CAPACITY:
                self._control_backpressure += 1
                return None
            self._control_submitted += 1
            request = {"type": "anchor_control", "schema_version": SCHEMA, "worker_run_id": self.worker_run_id,
                "request_id": self._control_submitted, "operation": operation, "anchor_timestamp_ns": anchor_timestamp_ns}
            command = AnchorCommand(request, time.monotonic()+self.timeout)
            payload = (json.dumps(request, sort_keys=True, allow_nan=False)+"\n").encode()
            self._queue.append((payload, None, command, time.monotonic_ns()))
            self._pending_controls += 1
            self._condition.notify_all()
            return command

    def _validate_image_reference(self, event):
        if event.get("image_path") not in self.manifest["checksums"]:
            raise ValueError("Camera must be bound to the sealed input capture")

    def request_map_frame(self, operation, payload, *, worker_run_id):
        """Queue a snapshot/init/clear transaction, never a new sensor receipt.

        Requires explicit opt-in. None means backpressure without submission;
        a returned command must be awaited, not resubmitted after a wait timeout.
        """
        value = map_control.prepare_payload(operation, payload)
        if worker_run_id != self.worker_run_id:
            raise ValueError("Map frame belongs to a different native worker epoch")
        with self._condition:
            if (not self.map_frame_control_enabled or self._thread is None
                    or self._state in {"failed", "stopped", "shutdown_incomplete"}
                    or self._stop.is_set() and self._state not in {"complete", "no_local_estimate"}):
                raise RuntimeError("Native map-frame input is not accepting commands")
            if self._closed or self._state in {"complete", "no_local_estimate"}:
                raise map_control.MapFrameInputClosed("Native map-frame input closed before submission")
            if self._map_submitted >= map_control.MAX_REQUESTS:
                raise RuntimeError("Map-frame command budget exhausted; finish this epoch")
            if self._pending_controls >= QUEUE_CAPACITY:
                self._control_backpressure += 1
                self._map_backpressure += 1
                return None
            request = {"type": "map_frame_control", "schema_version": map_control.SCHEMA,
                "worker_run_id": self.worker_run_id, "request_id": self._map_submitted + 1,
                "operation": operation, **value}
            encoded = (json.dumps(request, sort_keys=True, allow_nan=False) + "\n").encode()
            if len(encoded) > map_control.REQUEST_LIMIT + 1:
                raise ValueError("Map-frame request exceeds 64 KiB")
            command = map_control.MapFrameCommand(request, time.monotonic() + self.timeout)
            self._queue.append((encoded, None, command, time.monotonic_ns()))
            self._map_submitted += 1
            self._pending_controls += 1
            self._condition.notify_all()
            return command

    def _check_input(self):
        pass

    def _before_batch(self, payload):
        pass

    def _after_batch(self, payload, record):
        pass

    def _finalize_input(self):
        pass

    def finish(self):
        """Close input after queued batches, allowing native EOF and normal exit."""
        with self._condition:
            self._closed = True
            self._condition.notify_all()

    def join(self, timeout_s=0.):
        if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or timeout_s < 0:
            raise ValueError("Join timeout must be finite and nonnegative")
        if self._thread is not None:
            self._thread.join(timeout_s)
        return not bool(self._thread and self._thread.is_alive())

    def stop(self, timeout_s=3.):
        self._stop.set()
        with self._condition:
            self._closed = True
            self._condition.notify_all()
        return self.join(timeout_s)

    def status(self):
        with self._condition:
            latest = self._latest
            map_pending = sum(isinstance(item[2], map_control.MapFrameCommand) for item in self._queue)
            return {"schema_version": "vision_nav_native_vio_worker_v1", "status": self._state, "error": self._error,
                "worker_run_id": self.worker_run_id,
                "submitted_batch_count": self._submitted, "completed_batch_count": self._completed,
                "pending_batch_count": self._pending_batches, "pending_capacity": self.capacity,
                "backpressure_count": self._rejected_full, "input_closed": self._closed,
                "local_state_counts": dict(self._local_states),
                "native_pid": self._process.pid if self._process else None, "native_returncode": self._returncode,
                "thread_alive": bool(self._thread and self._thread.is_alive()),
                "last_sensor_timestamp_ns": latest["timestamp_ns"] if latest else None,
                "last_local_state": latest["local_state"] if latest else None,
                "last_result_received_age_ms": (time.monotonic_ns() - self._latest_received_ns) / 1e6 if latest else None,
                "sensor_clock_is_host_clock": False, "position_authority": False, "px4_external_vision": "disabled",
                "control_queue": {"pending": self._pending_controls, "capacity": QUEUE_CAPACITY,
                    "backpressure_count": self._control_backpressure},
                "map_frame_control": {"enabled": self.map_frame_control_enabled,
                    "submitted": self._map_submitted, "acknowledged": self._map_completed,
                    "pending": map_pending, "backpressure_count": self._map_backpressure,
                    "active_initialization_id": self._map_active["request_id"] if self._map_active else None,
                    "last_acknowledgement": deepcopy(self._map_last_ack),
                    "scope": "research_conditional_projection_not_navigation_authority"},
                "anchor_control": {"enabled": bool(self.anchor_capacity), "capacity": self.anchor_capacity,
                    "submitted": self._control_submitted, "acknowledged": self._control_completed,
                    "pending": self._pending_controls - map_pending, "pending_capacity": QUEUE_CAPACITY,
                    "backpressure_count": self._control_backpressure - self._map_backpressure,
                    "active_anchor_timestamps_ns": list(self._active_anchors),
                    "last_acknowledgement": deepcopy(self._last_anchor_ack),
                    "membership_scope": "last_native_ack_not_valid_after_worker_exit"}}

    def take_latest(self):
        """Copy a captured posterior without blocking native input/publication.

        A newer posterior may arrive during the copy; it remains available to
        the next call. Control changes/failure invalidate the captured snapshot.
        """
        with self._condition:
            if self._latest is None or self._result_taken == self._completed:
                return None
            self._result_taken = self._completed
            latest, received, completed = self._latest, self._latest_received_ns, self._completed
            generation = self._snapshot_generation
        # Native records are private and only replaced, never modified in place.
        record = deepcopy(latest)
        with self._condition:
            if generation != self._snapshot_generation or self._state in {"failed", "stopped", "shutdown_incomplete"}:
                return None
        return {"record": record, "received_host_monotonic_ns": received,
                "worker_run_id": self.worker_run_id,
                "completed_batch_count": completed, "position_authority": False}

    def timing_snapshot(self):
        """Bounded host service timings, not exposure age or real-time qualification."""
        with self._condition:
            rows, active, completed = list(self._batch_timings), self._active_batch_timing, self._completed
        return {"schema_version": "vision_nav_native_batch_timings_v1",
            "scope": "host_queue_service_includes_scheduling_io_and_json_decode_not_pure_native_compute",
            "completed_batch_count": completed, "retained_sample_limit": 256,
            "omitted_older_sample_count": max(0, completed-len(rows)),
            "samples": deepcopy(rows), "active_stage": dict(zip(
                ("sensor_timestamp_ns", "queued_host_ns", "started_host_ns", "phase", "phase_started_host_ns"), active)) if active else None,
            "observed_host_monotonic_ns": time.monotonic_ns(), "position_authority": False}

    def resource_snapshot(self):
        """Optional child-only resource probe, separate from lifecycle/status locks."""
        with self._condition:
            probe = self._resource_probe
        if probe is None:
            return {"status": "unavailable", "reason": "native_process_not_bound", "position_authority": False}
        return probe.snapshot()

    def clone_retention_snapshot(self, anchor_timestamp_ns):
        """Atomic, non-consuming membership evidence tied to a native posterior.

        Unlike take_latest(), this copies no poses/covariance and consumes no
        snapshot. EOF/stall does not refresh the original receipt timestamp.
        """
        from .pose_window import clone_retention_metadata

        with self._condition:
            evidence = clone_retention_metadata(self._latest, anchor_timestamp_ns)
            evidence.update(worker_run_id=self.worker_run_id, completed_batch_count=self._completed,
                native_worker_state=self._state, native_error=self._error,
                native_result_received_host_monotonic_ns=self._latest_received_ns,
                observed_host_monotonic_ns=time.monotonic_ns())
            return evidence

    def _check_wait(self, deadline):
        if self._stop.is_set():
            raise InterruptedError("Native VIO stop requested")
        if time.monotonic() >= deadline:
            raise TimeoutError("Native VIO batch deadline exceeded")
        if self._process.poll() is not None:
            raise RuntimeError(f"Native VIO exited before batch acknowledgement: {self._process.returncode}")

    def _write_batch(self, payload, deadline):
        remaining = memoryview(payload)
        fd = self._process.stdin.fileno()
        while remaining:
            self._check_wait(deadline)
            if select.select([], [fd], [], .02)[1]:
                try:
                    remaining = remaining[os.write(fd, remaining):]
                except BlockingIOError:
                    continue

    def _read_line(self, reader, deadline, limit):
        pending = bytearray()
        while True:
            self._check_wait(deadline)
            chunk = reader.readline(limit + 1 - len(pending))
            pending.extend(chunk)
            if len(pending) > limit:
                raise ValueError("Native response exceeds byte bound")
            if pending.endswith(b"\n"):
                return json.loads(pending)
            if not chunk:
                self._stop.wait(.005)

    def _read_result(self, reader, deadline, stamp):
        record = self._read_line(reader, deadline, 2*1024*1024)
        if (record.get("schema_version") != "vision_nav_openvins_local_v1"
                or record.get("source_revision") != REVISION or record.get("position_authority") is not False
                or record.get("navigation_mode") != "NO_GLOBAL_FIX" or record.get("px4_external_vision") != "disabled"
                or type(record.get("timestamp_ns")) is not int or record["timestamp_ns"] != stamp
                or record.get("local_state") not in {"LOCAL_ESTIMATE", "INITIALIZING_OR_NO_UPDATE"}):
            raise ValueError("Native state identity, timestamp or authority mismatch")
        if self.anchor_capacity:
            window = record.get("pose_window")
            if (record.get("passive_anchor_actions") != []
                    or record["local_state"] == "LOCAL_ESTIMATE" and (
                        not isinstance(window, dict) or window.get("schema_version") != "vision_nav_openvins_pose_window_v2"
                        or window.get("posterior_timestamp_ns") != stamp
                        or window.get("passive_anchor_capacity") != self.anchor_capacity
                        or window.get("passive_anchor_timestamps_ns") != self._active_anchors)
                    or record["local_state"] == "INITIALIZING_OR_NO_UPDATE" and window is not None):
                raise ValueError("Native pose window differs from acknowledged anchor controls")
        if self.map_frame_control_enabled:
            active = map_control.validate_projection(record.get("map_frame_projection"), self.worker_run_id, stamp, self._map_active)
            with self._condition:
                self._map_active = active
        elif "map_frame_projection" in record:
            raise ValueError("Unexpected map-frame output without opt-in")
        return record

    def _read_anchor_ack(self, reader, command):
        ack = self._read_line(reader, command._deadline, 4096)
        return validate_ack(ack, command.request, self.anchor_capacity, self._active_anchors, self._last_posterior_ns)

    def _verify_control_sources(self):
        if not self.anchor_capacity and not self.map_frame_control_enabled:
            return
        if digest(self.binary) != self.binary_sha256 or any(
                digest(Path(__file__).parent/name) != sha for name, sha in self._control_source_hashes.items()):
            raise ValueError("Native anchor binary or adapter source changed during epoch")
        if hasattr(self, "manifest_sha256"):
            if digest(self.capture/"input_manifest.json") != self.manifest_sha256:
                raise ValueError("Controlled stream capture manifest changed")
            validate_capture(self.capture)

    def _run(self):
        process = None
        try:
            # Pre-create only our own output; native refuses existing files, so
            # wait for it to create the state stream before opening the reader.
            with ExitStack() as stack:
                self._verify_control_sources()
                log = stack.enter_context((self.output / "native.log").open("x"))
                owned_stream = stack.enter_context((self.output / "owned_stream.jsonl").open("xb")) if self.anchor_capacity or self.map_frame_control_enabled else None
                args = [str(self.binary), str(self.capture / "estimator_config.yaml"),
                    str(self.capture / "events.jsonl"), str(self.output / "local_states.jsonl"),
                    "--pose-window", "--stream-input"]
                ack_path = self.output / "anchor_acknowledgements.jsonl"
                if self.anchor_capacity:
                    args.extend(["--anchor-control", str(self.anchor_capacity), self.worker_run_id, str(ack_path)])
                map_path = self.output / "map_frame_acknowledgements.jsonl"
                if self.map_frame_control_enabled:
                    args.extend(["--map-frame-control", self.worker_run_id, str(map_path)])
                process = subprocess.Popen(args, stdin=subprocess.PIPE,
                    stdout=log, stderr=subprocess.STDOUT, bufsize=0)
                resource_probe = OwnedProcessResources(process.pid, self.worker_run_id)
                with self._condition:
                    self._process = process
                    self._resource_probe = resource_probe
                os.set_blocking(process.stdin.fileno(), False)
                deadline = time.monotonic() + self.timeout
                while (not (self.output / "local_states.jsonl").exists() or self.anchor_capacity and not ack_path.exists()
                       or self.map_frame_control_enabled and not map_path.exists()):
                    self._check_wait(deadline)
                    self._stop.wait(.005)
                ack_reader = stack.enter_context(ack_path.open("rb")) if self.anchor_capacity else None
                map_reader = stack.enter_context(map_path.open("rb")) if self.map_frame_control_enabled else None
                with (self.output / "local_states.jsonl").open("rb") as reader:
                    while True:
                        self._check_input()
                        with self._condition:
                            self._state = "waiting_for_sensors"
                            self._condition.wait_for(lambda: self._queue or self._closed or self._stop.is_set(), timeout=.1)
                            if self._stop.is_set():
                                raise InterruptedError("Native VIO stop requested")
                            if not self._queue:
                                if self._closed:
                                    break
                                if process.poll() is not None:
                                    raise RuntimeError("Native VIO exited while waiting for sensors")
                                continue
                            payload, stamp, command, queued_ns = self._queue.popleft()
                            if command:
                                self._pending_controls -= 1
                                self._control_inflight = command
                            else:
                                self._pending_batches -= 1
                            self._state = "processing"
                        started_ns = time.monotonic_ns()
                        if not command:
                            self._active_batch_timing = (stamp, queued_ns, started_ns, "prepare", started_ns)
                        if command and time.monotonic() >= command._deadline:
                            response = {"request": command.request, "status": "not_applied", "reason": "queue_deadline_exceeded", "position_authority": False}
                            self._unapplied_controls.append(response)
                            command._settle(response)
                            self._control_inflight = None
                            continue
                        deadline = time.monotonic() + self.timeout
                        if not command:
                            self._before_batch(payload)
                        if owned_stream:
                            owned_stream.write(payload)
                            owned_stream.flush()
                        if command:
                            self._write_batch(payload, command._deadline)
                            if isinstance(command, map_control.MapFrameCommand):
                                ack = map_control.validate_ack(self._read_line(map_reader, command._deadline, map_control.RESPONSE_LIMIT),
                                    command.request, self._last_posterior_ns, self._map_active)
                                request = command.request
                                with self._condition:
                                    self._map_completed += 1
                                    # Status must not repeatedly copy a multi-MiB snapshot.
                                    self._map_last_ack = {k: v for k, v in ack.items() if k != "snapshot"}
                                    if ack["status"] == "applied" and request["operation"] == "capture":
                                        self._map_pending_snapshot = {k: ack["snapshot"][k] for k in
                                            ("snapshot_id", "sha256", "query_timestamp_ns", "posterior_timestamp_ns")}
                                    elif ack["status"] == "applied" and request["operation"] == "initialize":
                                        if (self._map_pending_snapshot is None or self._map_pending_snapshot["snapshot_id"] != request["snapshot_id"]
                                                or self._map_pending_snapshot["sha256"] != request["snapshot_sha256"]):
                                            raise ValueError("Map initialization lacks an owned snapshot")
                                        self._map_active = {**request, "observation_timestamp_ns": self._map_pending_snapshot["query_timestamp_ns"]}
                                    if request["operation"] == "initialize" and self._map_pending_snapshot is not None:
                                        if request["snapshot_id"] == self._map_pending_snapshot["snapshot_id"]:
                                            self._map_pending_snapshot = None
                                    if ack["status"] == "applied" and request["operation"] == "clear":
                                        self._map_active = self._map_pending_snapshot = None
                                    if ack["status"] == "applied" and request["operation"] != "capture":
                                        self._latest = self._latest_received_ns = None
                                        self._snapshot_generation += 1
                                command._settle(ack)
                                self._control_inflight = None
                                continue
                            ack = self._read_anchor_ack(ack_reader, command)
                            with self._condition:
                                self._active_anchors = ack["active_anchor_timestamps_ns"]
                                self._last_anchor_ack = ack
                                self._control_completed += 1
                                if ack["status"] == "applied":
                                    # A covariance-variable change is not a new sensor
                                    # posterior. Wait for the next camera snapshot.
                                    self._latest = self._latest_received_ns = None
                                    self._snapshot_generation += 1
                            command._settle(ack)
                            self._control_inflight = None
                            continue
                        prepared_ns = time.monotonic_ns()
                        self._active_batch_timing = (stamp, queued_ns, started_ns, "write", prepared_ns)
                        self._write_batch(payload, deadline)
                        written_ns = time.monotonic_ns()
                        self._active_batch_timing = (stamp, queued_ns, started_ns, "native_wait_and_decode", written_ns)
                        record = self._read_result(reader, deadline, stamp)
                        received_ns = time.monotonic_ns()
                        self._active_batch_timing = (stamp, queued_ns, started_ns, "adapter_ack", received_ns)
                        self._after_batch(payload, record)
                        acknowledged_ns = time.monotonic_ns()
                        self._active_batch_timing = (stamp, queued_ns, started_ns, "publish", acknowledged_ns)
                        with self._condition:
                            published_ns = time.monotonic_ns()
                            # Receipt precedes adapter/disk/lock delays; those
                            # delays must not make a snapshot appear younger.
                            self._latest, self._latest_received_ns = record, received_ns
                            self._last_posterior_ns = record["timestamp_ns"]
                            self._completed += 1
                            self._local_states[record["local_state"]] += 1
                            self._batch_timings.append({"sensor_timestamp_ns": stamp,
                                "local_state": record["local_state"], "queued_host_ns": queued_ns,
                                "received_host_ns": received_ns, "published_host_ns": published_ns,
                                "queue_wait_ms": (started_ns-queued_ns)/1e6,
                                "prepare_ms": (prepared_ns-started_ns)/1e6,
                                "write_ms": (written_ns-prepared_ns)/1e6,
                                "native_wait_and_decode_ms": (received_ns-written_ns)/1e6,
                                "adapter_ack_ms": (acknowledged_ns-received_ns)/1e6,
                                "publish_lock_wait_ms": (published_ns-acknowledged_ns)/1e6,
                                "service_ms": (published_ns-started_ns)/1e6,
                                "queue_to_snapshot_ms": (published_ns-queued_ns)/1e6})
                            self._active_batch_timing = None
                process.stdin.close()
                self._returncode = process.wait(timeout=self.timeout)
                with self._condition:
                    self._state = {0: "complete", 3: "no_local_estimate"}.get(self._returncode, "failed")
                    if self._state == "failed":
                        self._error = f"Native VIO exit code {self._returncode}"
        except InterruptedError:
            with self._condition:
                self._state = "stopped"
        except Exception as exc:
            with self._condition:
                self._state, self._error = "failed", f"{type(exc).__name__}:{exc}"[:1000]
        finally:
            try:
                if process is not None:
                    if process.poll() is None:
                        process.terminate()
                        try:
                            process.wait(timeout=1.)
                        except subprocess.TimeoutExpired:
                            process.kill()
                            process.wait(timeout=1.)
                    if process.stdin and not process.stdin.closed:
                        process.stdin.close()
                    self._returncode = process.returncode
            except Exception as exc:
                with self._condition:
                    self._state, self._error = "shutdown_incomplete", f"{type(exc).__name__}:{exc}"[:1000]
            finally:
                with self._condition:
                    if (self.anchor_capacity or self.map_frame_control_enabled) and self._state not in {"complete", "no_local_estimate"}:
                        self._latest = self._latest_received_ns = None
                        self._snapshot_generation += 1
                    self._map_active = self._map_pending_snapshot = None
                    unsettled = [item[2] for item in self._queue if item[2] is not None]
                    if self._control_inflight:
                        unsettled.append(self._control_inflight)
                    for command in unsettled:
                        response = {"request": command.request, "status": "indeterminate" if command is self._control_inflight else "not_applied",
                            "reason": "native_worker_terminated", "detail": self._error, "position_authority": False}
                        self._unapplied_controls.append(response)
                        command._settle(response)
                    self._queue.clear()
                    self._pending_batches = self._pending_controls = 0
                    self._control_inflight = None
                try:
                    if self.anchor_capacity or self.map_frame_control_enabled:
                        try:
                            self._verify_control_sources()
                        except Exception as exc:
                            self._state, self._error = "failed", f"control_source_verification_failed:{type(exc).__name__}:{exc}"[:1000]
                            self._latest = self._latest_received_ns = None
                        report = {"schema_version": "vision_nav_anchor_control_report_v1", "worker_run_id": self.worker_run_id,
                            "capacity": self.anchor_capacity, "status": self._state, "error": self._error,
                            "binary_sha256": self.binary_sha256, "position_authority": False,
                            "python_adapter_sha256": self._control_source_hashes,
                            "unapplied_or_indeterminate": self._unapplied_controls,
                            "scope": "native_stream_control_before_input_adapter_final_verification",
                            "checksums": {name: digest(self.output/name) for name in
                                ("owned_stream.jsonl", "anchor_acknowledgements.jsonl", "map_frame_acknowledgements.jsonl", "local_states.jsonl") if (self.output/name).is_file()}}
                        if self.anchor_capacity:
                            (self.output/"anchor_control_report.json").write_text(json.dumps(report, indent=2)+"\n")
                        if self.map_frame_control_enabled:
                            report.update(schema_version="vision_nav_map_frame_control_report_v1",
                                command_count=self._map_submitted, acknowledged_count=self._map_completed,
                                source_snapshot_and_uncertainty="declared_research_not_physically_qualified")
                            (self.output/"map_frame_control_report.json").write_text(json.dumps(report, indent=2)+"\n")
                    self._finalize_input()
                except Exception as exc:
                    with self._condition:
                        self._state, self._error = "failed", f"input_finalize_failed:{type(exc).__name__}:{exc}"[:1000]
                self._stop.set()
