"""Bind live learned evidence to conditional PnP estimation, never flight output."""

from copy import deepcopy
import hashlib
import re

import numpy as np

from .correlated_window import payload_digest
from .delayed_alignment import _owned
from .pose_smoother import timestamp
from .reprojection_uncertainty import assess
from vision_nav.neural.candidate_groups import select_candidate_groups
from vision_nav.neural.bounded_pnp import validate_bound_attempt
from vision_nav.vio.live_worker import LiveFrameTicket


def prepare(row, *, frame_ticket, provenance, model_release_id, clock_id, uncertainty_model_id):
    """Select one original metric attempt after verifying its source envelope.

    Provenance and model-release ID come from the owning session, not an
    untrusted row. This binds evidence; it does not certify model permissions,
    calibration, global uniqueness, timing accuracy or native-worker liveness.
    """
    base = {"schema_version": "vision_nav_learned_pnp_binding_v1", "position_authority": False,
            "navigation_mode": "NO_GLOBAL_FIX", "px4_external_vision": "disabled",
            "covariance_calibrated": False, "request_without_uncertainty": None}
    try:
        row, provenance = _owned(row, 4*1024*1024), _owned(provenance, 256*1024)
        if not isinstance(row, dict) or not isinstance(provenance, dict):
            raise ValueError("Expected learned row and owner provenance")
        # Parent process ownership is appended after the child model handshake.
        # It is not part of the immutable model provenance stamped by that child.
        provenance.pop("execution", None)
        for value in (model_release_id, clock_id, uncertainty_model_id):
            if not isinstance(value, str) or not value.strip() or len(value) > 256:
                raise ValueError("Explicit bounded model, clock and uncertainty IDs required")
        if (row.get("schema_version") != "vision_nav_live_learned_sensor_session_v1"
                or row.get("state") != "MODEL_EVIDENCE" or row.get("position_authority") is not False
                or row.get("navigation_mode") != "NO_GLOBAL_FIX" or row.get("px4_external_vision") != "disabled"
                or row.get("global_fusion_connected") is not False):
            raise ValueError("Only successful shadow learned rows can enter conditional estimation")
        if row.get("provenance_sha256") != payload_digest(provenance):
            raise ValueError("Learned row does not match owner pipeline provenance")
        if not isinstance(frame_ticket, LiveFrameTicket):
            raise ValueError("An immutable native frame ticket is required")
        ticket = frame_ticket
        ns = timestamp(ticket.timestamp_ns)
        if (ticket.worker_run_id != row.get("worker_run_id") or ticket.worker_run_id != provenance.get("worker_run_id")
                or row.get("sequence") != ticket.sequence or row.get("anchor_vio_timestamp_ns") != ns
                or row.get("anchor_pixel_sha256") != ticket.pixel_sha256
                or row.get("anchor_png_sha256") != ticket.image_sha256):
            raise ValueError("Learned result belongs to another frame or native epoch")
        pixels = ticket.pixels
        if (not isinstance(pixels, np.ndarray) or pixels.dtype != np.uint8 or pixels.flags.writeable
                or pixels.ndim not in (2, 3) or pixels.nbytes > 64*1024*1024
                or hashlib.sha256(np.ascontiguousarray(pixels).tobytes()).hexdigest() != ticket.pixel_sha256):
            raise ValueError("Native frame pixels were changed or are not immutable")
        identities = row.get("window_frame_identities")
        if not isinstance(identities, list) or not identities or identities[-1] != {
                "sequence": ticket.sequence, "timestamp_ns": ns, "pixel_sha256": ticket.pixel_sha256}:
            raise ValueError("Temporal anchor identity differs from native ticket")
        local = row.get("local_verification")
        if (not isinstance(local, dict) or local.get("complete") is not True
                or local.get("geometry_mode") != "surface_backed_pnp_camera_candidate"
                or local.get("position_authority") is not False or local.get("px4_external_vision") != "disabled"):
            raise ValueError("Complete surface-backed learned geometry is required")
        attempts = local.get("attempts")
        if not isinstance(attempts, list) or not 1 <= len(attempts) <= 4 or any(a.get("status") != "verified" for a in attempts):
            raise ValueError("Every retrieved candidate must have a verification result")
        selection = select_candidate_groups(attempts, complete=True)
        if selection != local.get("candidate_selection") or selection["status"] != "unique":
            raise ValueError("Candidate selection is changed, incomplete or ambiguous")
        retrieved, candidates = row.get("retrieval"), row.get("candidates")
        ids = [a["tile_id"] for a in attempts]
        if (not isinstance(retrieved, list) or not isinstance(candidates, list)
                or [r["tile_id"] for r in retrieved] != ids or [c["tile_id"] for c in candidates] != ids):
            raise ValueError("Verification must cover the exact retrieved/model candidate set")
        for a, c in zip(attempts, candidates):
            if (c.get("map_release_id") != provenance.get("map_release_id")
                    or c.get("calibration_id") != provenance.get("calibration_id")
                    or c.get("model_sha256") != provenance.get("model_sha256")
                    or c.get("map_image_sha256") != a.get("image_sha256")):
                raise ValueError("Candidate map, model, calibration or image identity mismatch")
        attempt = next(a for a in attempts if a["tile_id"] == selection["selected_tile_id"])
        validate_bound_attempt(attempt)
        count = len(attempt["matched_query_pixels"])
        if not 6 <= count <= 128:
            raise ValueError("Conditional PnP accepts 6..128 correspondences; no silent subsampling")
        for name, size in (("matched_query_feature_indices", attempt["feature_counts"]["query"]),
                           ("matched_map_feature_indices", attempt["feature_counts"]["map"])):
            indices = attempt.get(name)
            if (type(size) is not int or size <= 0 or not isinstance(indices, list) or len(indices) != count
                    or len(set(indices)) != count or any(type(i) is not int or not 0 <= i < size for i in indices)):
                raise ValueError("Exact distinct source feature identities are required")
        for name in ("map_release_id", "calibration_id"):
            if not isinstance(provenance.get(name), str) or not provenance[name].strip():
                raise ValueError("Missing owner map/calibration identity")
        for digest in (provenance.get("model_sha256"), attempt.get("image_sha256")):
            if not isinstance(digest, str) or not re.fullmatch("[0-9a-f]{64}", digest):
                raise ValueError("Invalid model/map image checksum")
        context = {"timestamp_ns": ns, "frame_sha256": ticket.pixel_sha256,
            "map_release_id": provenance["map_release_id"], "calibration_id": provenance["calibration_id"],
            "model_release_id": model_release_id, "clock_id": clock_id, "uncertainty_model_id": uncertainty_model_id,
            "native_worker_run_id": ticket.worker_run_id, "frame_sequence": ticket.sequence,
            "pipeline_provenance_sha256": payload_digest(provenance), "learned_row_sha256": payload_digest(row),
            "candidate_selection_sha256": payload_digest(selection), "selected_tile_id": attempt["tile_id"],
            "uniqueness_scope": "retrieved_candidates_only_not_global_or_temporal"}
        request = {"schema_version": "vision_nav_pnp_uncertainty_request_v1", "attempt": deepcopy(attempt), "context": context}
        return {**base, "status": "awaiting_joint_noise", "reason": "bound_original_learned_geometry",
                "request_without_uncertainty": request, "draft_sha256": payload_digest(request)}
    except (ValueError, KeyError, TypeError, AttributeError, IndexError, OverflowError, RecursionError) as exc:
        return {**base, "status": "withheld", "reason": "invalid_learned_pnp_binding", "detail": str(exc)[:256]}


def bind_uncertainty(draft, uncertainty):
    """Attach explicitly measured/declared noise, and run conditional assessment."""
    base = {"schema_version": "vision_nav_learned_pnp_request_v1", "position_authority": False,
            "navigation_mode": "NO_GLOBAL_FIX", "px4_external_vision": "disabled", "request": None,
            "covariance_calibrated": False, "assessment": None}
    try:
        draft = _owned(draft, 4*1024*1024)
        request = draft["request_without_uncertainty"]
        if (draft.get("schema_version") != "vision_nav_learned_pnp_binding_v1"
                or draft.get("status") != "awaiting_joint_noise" or draft.get("position_authority") is not False
                or draft.get("draft_sha256") != payload_digest(request)):
            raise ValueError("Binding draft is absent or changed")
        request["uncertainty"] = _owned(uncertainty, 16*1024*1024)
        result = assess(request)
        if result["status"] != "conditional_research_covariance":
            return {**base, "status": "withheld", "reason": result["reason"], "assessment": result}
        return {**base, "status": "conditional_pnp_request", "reason": "explicit_noise_attached_not_navigation",
                "request": request, "assessment": result}
    except (ValueError, KeyError, TypeError, AttributeError, OverflowError, RecursionError, np.linalg.LinAlgError) as exc:
        return {**base, "status": "withheld", "reason": "invalid_learned_pnp_uncertainty", "detail": str(exc)[:256]}
