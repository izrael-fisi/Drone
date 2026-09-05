"""Connect conditional PnP uncertainty to one correlated VIO research snapshot."""

from __future__ import annotations

import argparse
from collections import OrderedDict
import hashlib
import json
from pathlib import Path

import numpy as np

from .correlated_window import CorrelatedWindowAligner, WindowAlignmentConfig, payload_digest
from .bounded_json import dumps as bounded_dumps
from .canonical_identity import encode as canonical_encode, object_digest
from .reprojection_uncertainty import assess
from vision_nav.vio.pose_window import JointPoseWindow, joint_covariance


class PnPAssessmentCache:
    """Worker-scoped memoization of exact, immutable PnP requests only.

    Never caches VIO windows, joint cross-covariance, map batches or alignment.
    The owning worker verifies implementation sources before and after each
    operation. Serialized results prevent consumer mutation of retained values.
    Limits bound retained ASCII payload, not Python/process RSS.
    """

    def __init__(self, *, max_entries=16, max_bytes=2*1024*1024):
        if type(max_entries) is not int or not 1 <= max_entries <= 16:
            raise ValueError("PnP cache requires 1..16 entries")
        if type(max_bytes) is not int or not 1 <= max_bytes <= 2*1024*1024:
            raise ValueError("PnP cache payload limit must be in 1..2097152 bytes")
        self._max_entries, self._max_bytes = max_entries, max_bytes
        self._entries = OrderedDict()
        self._bytes = self._hits = self._misses = self._evictions = self._uncached = 0

    def assess(self, request):
        return self._assess_owned(request, payload_digest(request))

    def _assess_owned(self, request, key):
        """Reuse the digest computed in this same private alignment transaction."""
        if key in self._entries:
            self._hits += 1
            self._entries.move_to_end(key)
            return json.loads(self._entries[key])
        self._misses += 1
        result = assess(request)
        if result["status"] != "conditional_research_covariance":
            self._uncached += 1
            return result
        try:
            encoded = bounded_dumps(result, self._max_bytes)
        except ValueError:
            # Oversized valid results remain usable, without growing the cache.
            self._uncached += 1
            return result
        while self._entries and (len(self._entries) >= self._max_entries
                                 or self._bytes+len(encoded) > self._max_bytes):
            _, old = self._entries.popitem(last=False)
            self._bytes -= len(old)
            self._evictions += 1
        self._entries[key] = encoded
        self._bytes += len(encoded)
        return result

    def status(self):
        return {"scope": "exact_pnp_request_only_not_joint_vio_or_alignment",
                "entries": len(self._entries), "payload_bytes": self._bytes,
                "max_entries": self._max_entries, "max_payload_bytes": self._max_bytes,
                "hits": self._hits, "misses": self._misses,
                "evictions": self._evictions, "uncached": self._uncached}


def align(request, *, assessment_cache=None):
    """Propagate the complete declared input model, optionally reusing exact PnP.

    Inputs describe errors of estimated VIO poses and PnP measurements. At the
    true state, both pose residuals have the negative of their estimate error,
    so the covariance/cross-covariance signs are unchanged to first order.
    """
    return _align_owned(request, assessment_cache=assessment_cache)


def _align_owned(request, *, assessment_cache=None, encoded_fields=None):
    """Internal transaction path; encoded fields must belong to this owned tree.

    Public align never accepts cached identities. The isolated worker may reuse
    its own immutable encodings after passing copies to external providers.
    Native, map, calibration and full joint covariance validation still run.
    """
    if assessment_cache is not None and type(assessment_cache) is not PnPAssessmentCache:
        raise TypeError("Expected a worker-owned PnPAssessmentCache")
    if request.get("schema_version") != "vision_nav_joint_pnp_alignment_request_v1":
        raise ValueError("Unknown joint PnP alignment request")
    record = request["native_record"]
    window = JointPoseWindow.from_record(record)
    config = WindowAlignmentConfig(**request["config"])
    # PnP inputs already own the shared map error. Zero here prevents adding it twice.
    if np.any(config.map_common_left_covariance):
        raise ValueError("Shared map error is already in PnP inputs; additional map floor must be zero")
    items, noise = request["fixes"], request["joint_uncertainty"]
    if not isinstance(items, list) or not 1 <= len(items) <= min(16, len(window.timestamps_ns)):
        raise ValueError("Expected 1-16 PnP fixes within one retained VIO snapshot")
    if encoded_fields is None:
        encoded_fields = {key: canonical_encode(value) for key, value in request.items()}
    if set(encoded_fields) != set(request) or any(type(value) is not bytes for value in encoded_fields.values()):
        raise ValueError("Encoded fields must describe this exact owned request")
    snapshot_sha256 = hashlib.sha256(encoded_fields["native_record"]).hexdigest()
    pnp_digests = [payload_digest(item["request"]) for item in items]
    if (noise.get("snapshot_sha256") != snapshot_sha256
            or noise.get("pnp_request_sha256s") != pnp_digests):
        raise ValueError("Joint noise must bind the exact ordered VIO snapshot and PnP requests")
    expected = {"error_convention": "estimated_vio_right_pose_and_additive_pnp_inputs",
                "measurement_order": "concatenated_pnp_request_input_vectors",
                "map_error_ownership": "joint_measurement_covariance_only_no_extra_floor",
                "snapshot_policy": "replace_do_not_accumulate"}
    if (any(noise.get(k) != v for k, v in expected.items())
            or not isinstance(noise.get("cross_covariance_model"), str) or not noise["cross_covariance_model"].strip()):
        raise ValueError("Explicit joint uncertainty ownership, order and correlation model required")
    results, sizes, ids, times = [], [], [], []
    for item, pnp_digest in zip(items, pnp_digests):
        identity = item.get("fix_id")
        if not isinstance(identity, str) or not identity.strip() or identity in ids:
            raise ValueError("Distinct nonempty map fix identities required")
        result = assess(item["request"]) if assessment_cache is None else assessment_cache._assess_owned(item["request"], pnp_digest)
        for key in ("map_release_id", "model_release_id", "calibration_id", "clock_id", "uncertainty_model_id"):
            if result[key] != getattr(config, key):
                raise ValueError(f"PnP and VIO alignment provenance mismatch: {key}")
        ns = result["timestamp_ns"]
        if ns not in window.timestamps_ns or (times and ns <= times[-1]):
            raise ValueError("Fixes must use distinct ordered exact retained clone times")
        ids.append(identity)
        times.append(ns)
        results.append(result)
        sizes.append(5*len(item["request"]["attempt"]["matched_query_pixels"])+4)
    if len({r["scope"] for r in results}) != 1:
        raise ValueError("Cannot mix synthetic and unvalidated research uncertainty scopes")
    base = {"schema_version": "vision_nav_joint_pnp_alignment_v1", "request_sha256": object_digest(encoded_fields),
            "implementation_sha256": hashlib.sha256(Path(__file__).read_bytes()).hexdigest(),
            "position_authority": False, "navigation_mode": "NO_GLOBAL_FIX", "px4_external_vision": "disabled",
            "covariance_calibrated": False, "snapshot_policy": "replace_do_not_accumulate",
            "pnp_results": results, "map_batch": None, "alignment": None}
    if any(r["status"] != "conditional_research_covariance" for r in results):
        return {**base, "status": "withheld", "reason": "incomplete_conditional_pnp_uncertainty"}
    d = sum(sizes)
    if d > 1024:
        raise ValueError("Dense joint PnP input model exceeds 1024-dimension research bound")
    joint = joint_covariance(noise["joint_measurement_covariance"], d)
    offsets = np.cumsum([0, *sizes])
    s = np.zeros((6*len(items), d))
    for i, (item, result) in enumerate(zip(items, results)):
        cols = slice(offsets[i], offsets[i+1])
        declared = np.asarray(item["request"]["uncertainty"]["joint_measurement_covariance"])
        if not np.allclose(joint[cols, cols], declared, rtol=1e-8, atol=1e-12):
            raise ValueError("Joint covariance diagonal block differs from bound PnP noise")
        s[6*i:6*i+6, cols] = result["pose_input_sensitivity"]
    cross = np.asarray(noise["vio_input_cross_covariance"], dtype=float)
    v = len(window.covariance_right_local)
    if cross.shape != (v, d) or not np.isfinite(cross).all():
        raise ValueError("Finite explicit VIO/input cross-covariance required; no default independence")
    joint_covariance(np.block([[window.covariance_right_local, cross], [cross.T, joint]]), v+d)
    q = joint_covariance(s @ joint @ s.T, 6*len(items))
    c = cross @ s.T
    batch = {"schema_version": "vision_nav_correlated_map_batch_v1", "snapshot_sha256": snapshot_sha256,
             **{k: getattr(config, k) for k in ("map_release_id", "model_release_id", "calibration_id", "clock_id", "uncertainty_model_id")},
             "covariance_convention": "joint_right_local_pose_residuals",
             "common_map_error_independent_of_joint_residuals": True, "extrinsics_treated_as_fixed": True,
             "covariance_scope": results[0]["scope"], "cross_covariance_model": noise["cross_covariance_model"],
             "fixes": [{"fix_id": identity, "timestamp_ns": r["timestamp_ns"], "map_T_camera": r["map_T_camera"]}
                       for identity, r in zip(ids, results)],
             "map_joint_covariance": q.tolist(), "vio_map_cross_covariance": c.tolist()}
    aligned = CorrelatedWindowAligner(config)._solve_window(window, snapshot_sha256, batch)
    return {**base, "status": aligned["status"], "reason": aligned["reason"],
            "map_batch": batch, "alignment": aligned,
            "joint_input_dimensions": d, "input_order_fix_ids": ids,
            "map_error_ownership": expected["map_error_ownership"],
            "assumptions": "fixed_correct_correspondences_and_extrinsics_no_timing_error_not_field_calibrated"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", required=True, type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args(argv)
    if args.request.stat().st_size > 32*1024*1024:
        raise ValueError("Joint research request exceeds 32 MiB")
    result = align(json.loads(args.request.read_text()))
    with args.output.open("x") as stream:
        json.dump(result, stream, indent=2, allow_nan=False)
        stream.write("\n")
    print(json.dumps({k: result[k] for k in ("status", "reason", "position_authority")}))
    return 0 if result["status"] == "research_alignment" else 2


if __name__ == "__main__":
    raise SystemExit(main())
