"""Bind temporal model inputs to immutable exposure/native-attitude audits."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np

from vision_nav.capture_timing import TimingPolicy
from vision_nav.training_capture import sha256_file
from .training_dataset import EXPOSURE_TIMING, LEGACY_TIMING


def checked_exposure_frame(frame: dict[str, Any], cache: dict[tuple[str, str], dict[str, Any]]) -> dict[str, Any]:
    if frame.get("timing_policy", LEGACY_TIMING) != EXPOSURE_TIMING:
        raise ValueError("Map-conditioned training requires exposure-native timing; use --allow-legacy-timing only for research baselines")
    reference = frame.get("timing_audit")
    if not isinstance(reference, dict) or not isinstance(reference.get("path"), str) or not isinstance(reference.get("sha256"), str):
        raise ValueError("Missing exposure timing audit reference")
    path = Path(reference["path"]).expanduser().resolve()
    key = (str(path), reference["sha256"])
    if key not in cache:
        if not path.is_file() or sha256_file(path) != reference["sha256"]:
            raise ValueError("Exposure timing audit checksum mismatch")
        audit = json.loads(path.read_text(encoding="utf-8"))
        if (audit.get("schema_version") != "vision_nav_exposure_alignment_v1"
                or audit.get("clock_fit", {}).get("status") != "available"
                or audit["clock_fit"].get("policy") != vars(TimingPolicy())):
            raise ValueError("Unsupported or rejected exposure clock audit")
        by_sequence = {}
        for row in audit["frames"]:
            if type(row.get("sequence")) is not int or row["sequence"] in by_sequence:
                raise ValueError("Invalid/duplicate timing-audit frame sequence")
            by_sequence[row["sequence"]] = row
        cache[key] = {"audit": audit, "by_sequence": by_sequence}
    stored = cache[key]
    audit = stored["audit"]
    if audit.get("session_id") != frame.get("capture_session_id"):
        raise ValueError("Exposure timing audit belongs to another capture session")
    row = stored["by_sequence"].get(reference.get("sequence"))
    if not isinstance(row, dict) or row.get("status") != "paired":
        raise ValueError("Frame has no accepted exposure/native-attitude pairing")
    if not isinstance(row.get("frame_sha256"), str) or len(row["frame_sha256"]) != 64 or frame.get("frame_sha256") != row["frame_sha256"]:
        raise ValueError("Camera image identity differs from its timing audit")
    camera = row.get("camera", {})
    conversion = row.get("conversion", {})
    if (camera.get("status") != "available" or conversion.get("status") != "available"
            or camera.get("timestamp_reference") != "first_row_exposure_start"
            or camera.get("host_monotonic_ns") != frame.get("timestamp_ns")
            or camera.get("sensor_timestamp_ns") != frame.get("sensor_timestamp_ns")):
        raise ValueError("Frame timestamps disagree with the exposure audit")
    angles = np.asarray(frame.get("attitude_rpy_rad"), dtype=float)
    audited = np.asarray(row.get("attitude_rpy_rad"), dtype=float)
    if angles.shape != (3,) or audited.shape != (3,) or not np.isfinite(angles).all() or not np.allclose(angles, audited, rtol=0, atol=1e-6):
        raise ValueError("Frame attitude differs from its audited native sample")
    envelope = row.get("total_timing_envelope_ms")
    if type(envelope) not in (int, float) or not np.isfinite(envelope) or not 0 <= envelope <= TimingPolicy().max_attitude_delta_ms:
        raise ValueError("Exposure/attitude timing envelope exceeds training policy")
    return row
