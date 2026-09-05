"""Opt-in native map-frame transactions. No learned acceptance or PX4 authority."""

import hashlib
import json
import math
import re

import numpy as np

from .anchor_control import AnchorCommand
from .openvins_replay import REVISION
from vision_nav.estimation.bounded_json import owned

SCHEMA = "vision_nav_openvins_map_frame_control_v1"
ACK_SCHEMA = "vision_nav_openvins_map_frame_ack_v1"
PROJECTION_SCHEMA = "vision_nav_native_map_projection_v1"
MAX_REQUESTS = 1024
REQUEST_LIMIT = 65535
RESPONSE_LIMIT = 2 * 1024 * 1024


class MapFrameInputClosed(RuntimeError):
    """Orderly input closure before a map-frame command was submitted."""


class MapFrameCommand(AnchorCommand):
    """Shares the owned native receipt lifecycle; a wait timeout is not cancellation."""


def _integer(value):
    return type(value) is int and 0 < value < 2**63


def _matrix(value, rows, cols):
    if (type(value) is not list or len(value) != rows
            or any(type(row) is not list or len(row) != cols for row in value)
            or any(type(v) not in (int, float) for row in value for v in row)):
        raise ValueError("Invalid native map-frame matrix shape/type")
    try:
        array = np.asarray(value, dtype=np.float64)
    except (ValueError, OverflowError) as exc:
        raise ValueError("Invalid native map-frame matrix values") from exc
    if not np.isfinite(array).all():
        raise ValueError("Nonfinite native map-frame matrix")
    return array


def _bindings(value):
    names = {"fix_id", "map_release_id", "model_release_id", "calibration_id"}
    if (type(value) is not dict or set(value) != names | {"uncertainty_sha256", "conditional_model", "uncertainty_status"}
            or any(type(value[k]) is not str or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,128}", value[k]) for k in names)
            or type(value["uncertainty_sha256"]) is not str or not re.fullmatch(r"[a-f0-9]{64}", value["uncertainty_sha256"])
            or value["conditional_model"] != "complete_native_state_and_future_innovations_v1"
            or value["uncertainty_status"] != "research_unqualified"):
        raise ValueError("Incomplete or unsupported map-frame uncertainty declaration")


def prepare_payload(operation, payload):
    """Own and bound the request before acquiring the worker queue lock."""
    value = owned(payload, REQUEST_LIMIT - 512)
    fields = {"capture": {"query_timestamp_ns"}, "clear": set(), "initialize": {
        "snapshot_id", "snapshot_sha256", "expires_at_ns", "local_T_map", "regression_native",
        "conditional_covariance_native", "bindings"}}
    if operation not in fields or type(value) is not dict or set(value) != fields[operation]:
        raise ValueError("Invalid map-frame operation or fields")
    for key in {"query_timestamp_ns", "snapshot_id", "expires_at_ns"} & set(value):
        if not _integer(value[key]):
            raise ValueError("Map-frame identities/times must be positive signed-range integers")
    if operation == "initialize":
        if type(value["snapshot_sha256"]) is not str or not re.fullmatch(r"[a-f0-9]{64}", value["snapshot_sha256"]):
            raise ValueError("Missing native snapshot digest")
        _matrix(value["local_T_map"], 4, 4)
        b = value["regression_native"]
        n = len(b[0]) if type(b) is list and b and type(b[0]) is list else 0
        if not 15 <= n <= 256:
            raise ValueError("Full native regression dimension must be in [15,256]")
        _matrix(b, 6, n)
        _matrix(value["conditional_covariance_native"], 6, 6)
        _bindings(value["bindings"])
    return value


def validate_ack(ack, request, posterior_ns, active):
    if (type(ack) is not dict or ack.get("schema_version") != ACK_SCHEMA
            or ack.get("source_revision") != REVISION or ack.get("position_authority") is not False
            or ack.get("navigation_mode") != "NO_GLOBAL_FIX" or ack.get("px4_external_vision") != "disabled"
            or any(ack.get(k) != request[k] for k in ("worker_run_id", "request_id", "operation"))
            or type(ack.get("request_id")) is not int or not _integer(ack.get("input_line"))
            or ack.get("posterior_timestamp_ns") != posterior_ns
            or posterior_ns is not None and type(ack.get("posterior_timestamp_ns")) is not int
            or ack.get("status") not in {"applied", "rejected"}
            or type(ack.get("reason")) is not str or not 0 < len(ack["reason"]) <= 240
            or (ack["status"] == "applied") != (ack["reason"] == "ok")):
        raise ValueError("Native map-frame acknowledgement identity/outcome mismatch")
    expected_active = active["request_id"] if active else None
    if ack["status"] == "applied":
        if request["operation"] == "initialize":
            if active is not None:
                raise ValueError("Native replaced an existing map frame without clear")
            expected_active = request["request_id"]
        elif request["operation"] == "clear":
            expected_active = None
    if (ack.get("active_initialization_id") != expected_active
            or expected_active is not None and type(ack.get("active_initialization_id")) is not int):
        raise ValueError("Native map-frame active identity changed unexpectedly")
    snapshot = ack.get("snapshot")
    if ack["status"] == "applied" and request["operation"] == "capture":
        if (type(snapshot) is not dict or set(snapshot) != {"canonical_json", "sha256"}
                or type(snapshot["canonical_json"]) is not str or not snapshot["canonical_json"].isascii()
                or len(snapshot["canonical_json"]) > RESPONSE_LIMIT
                or hashlib.sha256(snapshot["canonical_json"].encode("ascii")).hexdigest() != snapshot["sha256"]):
            raise ValueError("Native snapshot byte identity mismatch")
        digest = snapshot["sha256"]
        snapshot = json.loads(snapshot["canonical_json"])
        if (active is not None or type(snapshot) is not dict or snapshot.get("snapshot_id") != request["request_id"]
                or type(snapshot.get("snapshot_id")) is not int or snapshot.get("posterior_timestamp_ns") != posterior_ns
                or not _integer(snapshot.get("posterior_timestamp_ns"))
                or snapshot.get("query_timestamp_ns") != request["query_timestamp_ns"]
                or type(snapshot.get("query_timestamp_ns")) is not int
                or snapshot["query_timestamp_ns"] > posterior_ns
                or snapshot.get("covariance_convention") != "ordered_complete_native_error_state"
                or snapshot.get("covariance_calibrated") is not False
                or type(snapshot.get("dimension")) is not int or not 15 <= snapshot["dimension"] <= 256):
            raise ValueError("Invalid complete-native snapshot identity")
        n = snapshot["dimension"]
        _matrix(snapshot.get("covariance_native"), n, n)
        layout = snapshot.get("layout")
        if type(layout) is not list or not 1 <= len(layout) <= n:
            raise ValueError("Missing native variable layout")
        offset, names, query_found = 0, set(), False
        for variable in layout:
            if (type(variable) is not dict or type(variable.get("name")) is not str
                    or not 0 < len(variable["name"]) <= 128 or variable["name"] in names
                    or type(variable.get("offset")) is not int or variable["offset"] != offset
                    or not _integer(variable.get("size")) or offset + variable["size"] > n
                    or type(variable.get("value")) is not list
                    or len(variable["value"]) not in {variable["size"], variable["size"] + 1}):
                raise ValueError("Invalid or incomplete native variable layout")
            _matrix(variable["value"], len(variable["value"]), 1)
            if variable["offset"] == snapshot.get("query_variable_offset"):
                query_found = (variable["size"] == 6 and variable["name"] in {
                    f"clone:{request['query_timestamp_ns']}", f"passive:{request['query_timestamp_ns']}"})
            offset += variable["size"]
            names.add(variable["name"])
        if offset != n or not query_found or type(snapshot.get("query_variable_offset")) is not int:
            raise ValueError("Native query/full-state layout mismatch")
        snapshot["sha256"] = digest
        ack["snapshot"] = snapshot
    elif snapshot is not None:
        raise ValueError("Unexpected map-frame snapshot")
    return ack


def validate_projection(value, epoch, stamp, active):
    if (type(value) is not dict or value.get("schema_version") != PROJECTION_SCHEMA
            or value.get("worker_run_id") != epoch or type(value.get("timestamp_ns")) is not int
            or value["timestamp_ns"] != stamp or value.get("position_authority") is not False
            or value.get("covariance_calibrated") is not False or value.get("px4_external_vision") != "disabled"
            or type(value.get("reason")) is not str or not 0 < len(value["reason"]) <= 240):
        raise ValueError("Native map projection identity/authority mismatch")
    if value.get("status") == "unavailable":
        if value.get("initialization_id") is not None or any(k in value for k in ("map_T_imu", "covariance")):
            raise ValueError("Unavailable map projection contains a pose")
        return None
    if (value.get("status") != "conditional_projection" or active is None
            or type(value.get("initialization_id")) is not int or value["initialization_id"] != active["request_id"]
            or not _integer(value.get("observation_timestamp_ns")) or value["observation_timestamp_ns"] > stamp
            or value["observation_timestamp_ns"] != active["observation_timestamp_ns"]
            or value.get("expires_at_ns") != active["expires_at_ns"] or type(value.get("expires_at_ns")) is not int
            or stamp >= value["expires_at_ns"] or value.get("bindings") != active["bindings"]
            or value.get("covariance_convention") != "right_local_se3_rotation_then_translation"
            or type(value.get("map_age_s")) not in (int, float) or not math.isfinite(value["map_age_s"])
            or abs(value["map_age_s"] - (stamp - value["observation_timestamp_ns"]) / 1e9) > 1e-9):
        raise ValueError("Native map projection source/time mismatch")
    t, p = _matrix(value.get("map_T_imu"), 4, 4), _matrix(value.get("covariance"), 6, 6)
    scale = max(1e-12, float(np.max(np.abs(p))))
    if (not np.allclose(t[3], [0, 0, 0, 1], atol=1e-12, rtol=0)
            or not np.allclose(t[:3, :3].T @ t[:3, :3], np.eye(3), atol=1e-9, rtol=0)
            or abs(np.linalg.det(t[:3, :3]) - 1) > 1e-9
            or np.max(np.abs(p - p.T)) > 1e-10 * scale or np.linalg.eigvalsh(p).min() < -1e-10 * scale):
        raise ValueError("Invalid native map projection geometry/covariance")
    return active
