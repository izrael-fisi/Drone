"""Bounded passive-pose control receipts, not sensor updates or map factors."""

from copy import deepcopy
from contextlib import ExitStack
import json
import math
from pathlib import Path
import threading
import uuid

from .openvins_replay import REVISION, digest


SCHEMA = "vision_nav_openvins_anchor_control_v1"
MAX_REQUESTS = 1024
QUEUE_CAPACITY = 4


class AnchorInputClosed(RuntimeError):
    """Orderly input closure rejected an anchor command before submission."""


class AnchorCommand:
    """Owner-set receipt. A caller wait timeout does not cancel a native command."""

    def __init__(self, request, deadline):
        self._request = deepcopy(request)
        self._deadline = deadline
        self._event = threading.Event()
        self._response = None

    @property
    def request(self):
        return deepcopy(self._request)

    def result(self, timeout_s=0.):
        if type(timeout_s) not in (int, float) or not math.isfinite(timeout_s) or not 0 <= timeout_s <= 60:
            raise ValueError("Anchor result wait must be finite and in [0,60] seconds")
        if not self._event.wait(timeout_s):
            raise TimeoutError("Anchor command still pending; wait again on the same receipt")
        return deepcopy(self._response)

    def _settle(self, response):
        if self._event.is_set():
            raise RuntimeError("Anchor receipt already settled")
        self._response = deepcopy(response)
        self._event.set()


def validate_ack(ack, request, capacity, previous_active, posterior_ns):
    """Require an exact reply and a coherent native membership transition."""
    if (not isinstance(ack, dict) or ack.get("schema_version") != "vision_nav_openvins_anchor_ack_v1"
            or ack.get("source_revision") != REVISION or ack.get("position_authority") is not False
            or ack.get("navigation_mode") != "NO_GLOBAL_FIX" or ack.get("px4_external_vision") != "disabled"
            or type(ack.get("capacity")) is not int or ack["capacity"] != capacity
            or type(ack.get("request_id")) is not int or type(ack.get("anchor_timestamp_ns")) is not int
            or type(ack.get("input_line")) is not int or ack["input_line"] < 1
            or any(ack.get(k) != request[k] for k in ("request_id", "worker_run_id", "operation", "anchor_timestamp_ns"))
            or ack.get("posterior_timestamp_ns") != posterior_ns
            or (posterior_ns is not None and type(ack.get("posterior_timestamp_ns")) is not int)):
        raise ValueError("Native anchor acknowledgement identity or posterior mismatch")
    active = ack.get("active_anchor_timestamps_ns")
    if (not isinstance(active, list) or len(active) > capacity
            or any(type(t) is not int or not 0 < t < 2**63 or posterior_ns is None or t > posterior_ns for t in active)
            or active != sorted(set(active))):
        raise ValueError("Native anchor acknowledgement has invalid membership")
    expected = set(previous_active)
    target = request["anchor_timestamp_ns"]
    if ack.get("status") == "applied" and ack.get("reason") == "ok":
        if request["operation"] == "retain":
            if target in expected or len(expected) >= capacity:
                raise ValueError("Native retained a duplicate or exceeded capacity")
            expected.add(target)
        else:
            if target not in expected:
                raise ValueError("Native released an unknown anchor")
            expected.remove(target)
    elif ack.get("status") != "rejected" or ack.get("reason") not in {
            "no_initialized_posterior", "already_retained", "anchor_capacity_reached",
            "camera_clone_not_retained", "anchor_not_retained"}:
        raise ValueError("Invalid native anchor acknowledgement outcome")
    else:
        reason, operation = ack["reason"], request["operation"]
        if (reason == "already_retained" and (operation != "retain" or target not in expected)
                or reason == "anchor_capacity_reached" and (operation != "retain" or len(expected) < capacity)
                or reason == "camera_clone_not_retained" and (operation != "retain" or target in expected)
                or reason == "anchor_not_retained" and (operation != "release" or target in expected)):
            raise ValueError("Native anchor rejection reason contradicts ownership")
    if active != sorted(expected):
        raise ValueError("Native anchor acknowledgement changed unrelated membership")
    return ack


def audit_control_stream(events_path, acknowledgements_path, states_path, *, worker_run_id, capacity, sensor_events_path=None):
    """Verify exact ordered controls, sensor receipts and passive membership.

    This audits ownership, not filter accuracy or the numeric covariance. A
    supplied sensor-only recording must match every non-control event exactly.
    """
    if type(capacity) is not int or not 1 <= capacity <= 4 or str(uuid.UUID(worker_run_id)) != worker_run_id:
        raise ValueError("Invalid anchor control session identity or capacity")
    paths = [Path(events_path), Path(acknowledgements_path), Path(states_path)]
    if sensor_events_path is not None:
        paths.append(Path(sensor_events_path))
    if any(p.stat().st_size > 1024**3 for p in paths):
        raise ValueError("Anchor control evidence exceeds 1 GiB per file")
    hashes = [digest(p) for p in paths]

    def read(stream, limit=65536):
        line = stream.readline(limit+1)
        if len(line) > limit:
            raise ValueError("Oversized anchor control evidence row")
        if not line:
            return None
        row = json.loads(line)
        if not isinstance(row, dict):
            raise ValueError("Expected an object in anchor control evidence")
        return row

    active, last_request, posterior, cameras, controls = [], 0, None, 0, 0
    with ExitStack() as stack:
        streams = [stack.enter_context(p.open()) for p in paths]
        events, acks, states = streams[:3]
        index = 0
        while (event := read(events)) is not None:
            index += 1
            if not isinstance(event, dict):
                raise ValueError("Invalid controlled input event")
            if event.get("type") == "anchor_control":
                if (set(event) != {"type", "schema_version", "worker_run_id", "request_id", "operation", "anchor_timestamp_ns"}
                        or event.get("schema_version") != SCHEMA or event.get("worker_run_id") != worker_run_id
                        or type(event.get("request_id")) is not int or not last_request < event["request_id"] <= MAX_REQUESTS
                        or type(event.get("anchor_timestamp_ns")) is not int or not 0 < event["anchor_timestamp_ns"] < 2**63
                        or event.get("operation") not in ("retain", "release")):
                    raise ValueError("Invalid ordered anchor control request")
                ack = validate_ack(read(acks, 4096), event, capacity, active, posterior)
                if ack["input_line"] != index:
                    raise ValueError("Anchor acknowledgement input order differs")
                active, last_request = ack["active_anchor_timestamps_ns"], event["request_id"]
                controls += 1
                continue
            if event.get("type") not in {"imu", "camera"}:
                raise ValueError("Unknown controlled stream sensor type")
            if sensor_events_path is not None and read(streams[3]) != event:
                raise ValueError("Controlled stream differs from recorded sensor events")
            if event["type"] == "camera":
                record = read(states, 2*1024*1024)
                stamp = event.get("timestamp_ns")
                if (type(stamp) is not int or not 0 < stamp < 2**63 or posterior is not None and stamp <= posterior
                        or not isinstance(record, dict) or record.get("timestamp_ns") != stamp or record.get("line") != index
                        or record.get("schema_version") != "vision_nav_openvins_local_v1"
                        or record.get("source_revision") != REVISION or record.get("position_authority") is not False
                        or record.get("navigation_mode") != "NO_GLOBAL_FIX" or record.get("px4_external_vision") != "disabled"
                        or record.get("passive_anchor_actions") != []):
                    raise ValueError("Controlled stream camera acknowledgement mismatch")
                window = record.get("pose_window")
                if record.get("local_state") == "LOCAL_ESTIMATE":
                    if (not isinstance(window, dict) or window.get("schema_version") != "vision_nav_openvins_pose_window_v2"
                            or window.get("passive_anchor_capacity") != capacity
                            or window.get("passive_anchor_timestamps_ns") != active):
                        raise ValueError("Pose window differs from acknowledged anchor membership")
                elif record.get("local_state") != "INITIALIZING_OR_NO_UPDATE" or window is not None:
                    raise ValueError("Invalid controlled native local state")
                posterior, cameras = stamp, cameras+1
        if read(acks, 4096) is not None or read(states, 2*1024*1024) is not None or sensor_events_path is not None and read(streams[3]) is not None:
            raise ValueError("Unconsumed control, state or sensor evidence")
    if hashes != [digest(p) for p in paths]:
        raise ValueError("Control evidence changed during audit")
    return {"verified": True, "camera_count": cameras, "control_count": controls,
        "final_active_anchor_timestamps_ns": active, "position_authority": False}
