"""Synthetic process-test noise only. Never use this fixture on an aircraft."""

import os
import time
import numpy as np
from vision_nav.estimation.correlated_window import payload_digest


def _fault(config, stage):
    if config.get("fault_stage") == stage:
        if config.get("exit"):
            os._exit(17)
        if config.get("raise"):
            raise RuntimeError("synthetic provider failure")
        time.sleep(config.get("delay_s", 0))


def attempt_noise(request, config):
    _fault(config, "bind")
    n = len(request["attempt"]["matched_query_pixels"])
    matrix = np.zeros((5*n+4, 5*n+4))
    matrix[:2*n, :2*n] = np.eye(2*n)*.04
    return {"attempt_sha256": payload_digest(request["attempt"]),
        "measurement_order": "all_query_uv_then_all_map_xyz_then_fx_fy_cx_cy",
        "correspondences": "fixed_correct_inliers_conditional", "map_systematics": "included_in_joint_measurement_covariance",
        "rectification_error": "included_in_joint_measurement_covariance", "timing_error": "not_modeled",
        "camera_extrinsic_error": "not_modeled", "scope": "synthetic_contract_test",
        "noise_model_description": "Synthetic independent pixel errors, fixed K/map, not measured aircraft noise",
        "joint_measurement_covariance": matrix.tolist()}


def joint_noise(request, config):
    _fault(config, "noise")
    blocks = [np.asarray(f["request"]["uncertainty"]["joint_measurement_covariance"]) for f in request["fixes"]]
    d = sum(len(c) for c in blocks)
    joint, offset = np.zeros((d, d)), 0
    for block in blocks:
        joint[offset:offset+len(block), offset:offset+len(block)] = block
        offset += len(block)
    return {"snapshot_sha256": payload_digest(request["native_record"]),
        "pnp_request_sha256s": [payload_digest(f["request"]) for f in request["fixes"]],
        "error_convention": "estimated_vio_right_pose_and_additive_pnp_inputs",
        "measurement_order": "concatenated_pnp_request_input_vectors",
        "map_error_ownership": "joint_measurement_covariance_only_no_extra_floor",
        "snapshot_policy": "replace_do_not_accumulate",
        "cross_covariance_model": "Explicit synthetic independent measurements, not physical VIO",
        "joint_measurement_covariance": joint.tolist(),
        "vio_input_cross_covariance": np.zeros((6*len(request["native_record"]["pose_window"]["pose_timestamps_ns"]), d)).tolist()}
