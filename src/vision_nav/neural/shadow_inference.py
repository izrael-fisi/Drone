"""Release-bound frame inference used by the Pi shadow runtime.

This stops before geometric pose estimation. It can exercise a release-defined
retrieval/local-matching chain, but those outputs remain diagnostic evidence
until temporal support and robust geometry independently accept them.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from time import perf_counter
from typing import Any, Callable, Protocol

import cv2
import numpy as np

from .artifacts import REQUIRED_ONNX_ARTIFACTS
from .candidate_groups import landmark_bounds, select_candidate_groups
from .learned_correspondence import resized_camera_matrix
from .releases import ModelRelease
from .shadow_geometry import verify_planar_correspondences
from .bounded_pnp import verify_bounded_pnp
from .shadow_hypotheses import ShadowCameraCandidateBank
from .temporal_window import TemporalWindowBuilder


FRAME_RGB_SOURCE = "frame_rgb"
QUERY_LOCAL_KEYPOINTS_SOURCE = "query_local_keypoints"
QUERY_LOCAL_DESCRIPTORS_SOURCE = "query_local_descriptors"
MAP_TILE_KEYPOINTS_SOURCE = "map_tile_keypoints"
MAP_TILE_DESCRIPTORS_SOURCE = "map_tile_descriptors"
TEMPORAL_LOCATION_EVIDENCE = "temporal_location_evidence"
TEMPORAL_HEADING_EVIDENCE = "temporal_heading_evidence"
TEMPORAL_FEATURE_EVIDENCE = "temporal_feature_evidence"
LOCAL_FEATURE_KEYPOINTS = "local_keypoints"
LOCAL_FEATURE_DESCRIPTORS = "local_descriptors"
LOCAL_FEATURE_SCORES = "local_feature_scores"


class LogicalRunner(Protocol):
    def run(self, role: str, logical_inputs: dict[str, np.ndarray]) -> dict[str, np.ndarray]: ...


@dataclass(frozen=True)
class ShadowFrame:
    path: Path
    bgr: np.ndarray

    @property
    def width(self) -> int:
        return int(self.bgr.shape[1])

    @property
    def height(self) -> int:
        return int(self.bgr.shape[0])


def load_shadow_frame(path: str | Path) -> ShadowFrame:
    """Decode an image using OpenCV's BGR convention and reject bad input early."""

    resolved = Path(path).expanduser().resolve()
    if not resolved.is_file():
        raise ValueError(f"Shadow frame does not exist: {resolved}")
    image = cv2.imread(str(resolved), cv2.IMREAD_COLOR)
    if image is None or image.ndim != 3 or image.shape[2] != 3:
        raise ValueError(f"Shadow frame could not be decoded as a three-channel image: {resolved}")
    return ShadowFrame(path=resolved, bgr=image)


def _preprocess_image(bgr: np.ndarray, spec: dict[str, Any]) -> np.ndarray:
    """Prepare one explicit frame input. No exporter convention is inferred."""

    preprocess = spec.get("preprocess")
    if not isinstance(preprocess, dict):
        raise ValueError("missing_preprocess_contract")
    size = preprocess.get("image_size")
    if not isinstance(size, list) or len(size) != 2 or not all(isinstance(value, int) and value > 0 for value in size):
        raise ValueError("invalid_preprocess_image_size")
    width, height = size
    color_space = preprocess.get("color_space")
    if color_space not in {"rgb", "bgr"}:
        raise ValueError("invalid_preprocess_color_space")
    layout = preprocess.get("layout")
    if layout not in {"nchw", "nhwc"}:
        raise ValueError("invalid_preprocess_layout")
    value_range = preprocess.get("value_range", "zero_one")
    if value_range not in {"zero_one", "zero_255"}:
        raise ValueError("invalid_preprocess_value_range")
    if preprocess.get("dtype", "float32") != "float32":
        raise ValueError("unsupported_preprocess_dtype")

    image = cv2.resize(bgr, (width, height), interpolation=cv2.INTER_LINEAR)
    if color_space == "rgb":
        image = cv2.cvtColor(image, cv2.COLOR_BGR2RGB)
    image = image.astype(np.float32)
    if value_range == "zero_one":
        image /= 255.0

    mean = preprocess.get("mean")
    std = preprocess.get("std")
    if mean is not None or std is not None:
        if not isinstance(mean, list) or not isinstance(std, list) or len(mean) not in {1, 3} or len(std) != len(mean):
            raise ValueError("invalid_preprocess_normalization")
        mean_array = np.asarray(mean, dtype=np.float32).reshape(1, 1, -1)
        std_array = np.asarray(std, dtype=np.float32).reshape(1, 1, -1)
        if not np.all(np.isfinite(mean_array)) or not np.all(np.isfinite(std_array)) or np.any(std_array == 0.0):
            raise ValueError("invalid_preprocess_normalization")
        image = (image - mean_array) / std_array

    if layout == "nchw":
        return np.ascontiguousarray(image.transpose(2, 0, 1)[None, ...])
    return np.ascontiguousarray(image[None, ...])


def _frame_inputs(contract: dict[str, Any], frame: ShadowFrame) -> tuple[dict[str, np.ndarray] | None, str | None]:
    logical_inputs: dict[str, np.ndarray] = {}
    for logical_name, spec in contract.get("inputs", {}).items():
        if spec.get("shadow_source") != FRAME_RGB_SOURCE:
            return None, f"input_not_available_in_frame_probe:{logical_name}"
        try:
            logical_inputs[logical_name] = _preprocess_image(frame.bgr, spec)
        except ValueError as exc:
            return None, f"{logical_name}:{exc}"
    return logical_inputs, None


def _matcher_inputs(
    contract: dict[str, Any],
    *,
    live_outputs: dict[str, np.ndarray],
    candidates: list[dict[str, Any]],
    tile_feature_lookup: Callable[[str], dict[str, np.ndarray]] | None,
) -> tuple[dict[str, np.ndarray] | None, str | None, str | None, dict[str, np.ndarray] | None]:
    if not candidates:
        return None, "no_retrieval_candidate_for_local_matcher", None, None
    if tile_feature_lookup is None:
        return None, "local_feature_cache_unavailable", None, None
    tile_id = str(candidates[0].get("tile_id", ""))
    if not tile_id:
        return None, "retrieval_candidate_has_no_tile_id", None, None
    try:
        map_features = tile_feature_lookup(tile_id)
    except Exception as exc:
        return None, f"map_tile_feature_load_failed:{type(exc).__name__}:{exc}", tile_id, None
    values: dict[str, np.ndarray] = {}
    for logical_name, spec in contract.get("inputs", {}).items():
        source = spec.get("shadow_source")
        if source == QUERY_LOCAL_KEYPOINTS_SOURCE:
            value = live_outputs.get("local_keypoints")
        elif source == QUERY_LOCAL_DESCRIPTORS_SOURCE:
            value = live_outputs.get("local_descriptors")
        elif source == MAP_TILE_KEYPOINTS_SOURCE:
            value = map_features.get("keypoints")
        elif source == MAP_TILE_DESCRIPTORS_SOURCE:
            value = map_features.get("descriptors")
        else:
            return None, f"unsupported_matcher_input_source:{logical_name}", tile_id, None
        if value is None:
            return None, f"missing_matcher_input:{logical_name}", tile_id, None
        values[logical_name] = np.asarray(value)
    return values, None, tile_id, map_features


def _tensor_summary(value: np.ndarray) -> dict[str, Any]:
    array = np.asarray(value)
    finite = bool(np.isfinite(array).all()) if np.issubdtype(array.dtype, np.number) else False
    summary: dict[str, Any] = {"shape": list(array.shape), "dtype": str(array.dtype), "finite": finite}
    if finite and array.size:
        summary.update(min=float(array.min()), max=float(array.max()), mean=float(array.mean()))
    return summary


def filter_local_feature_outputs(
    contract: dict[str, Any],
    outputs: dict[str, np.ndarray],
) -> tuple[dict[str, np.ndarray], dict[str, Any] | None]:
    """Discard explicit invalid fixed-cardinality local-feature slots.

    Learned local-feature exports may use static feature tensors for ONNX
    compatibility. When their release contract declares a score output, only
    slots above its explicit threshold can become map-cache or matcher input.
    Models without a score declaration retain their native output unchanged.
    """

    named_outputs = contract.get("outputs") if isinstance(contract.get("outputs"), dict) else {}
    score_names = [
        name for name, spec in named_outputs.items()
        if isinstance(spec, dict) and spec.get("shadow_output") == LOCAL_FEATURE_SCORES
    ]
    if not score_names:
        return outputs, None
    if len(score_names) != 1:
        raise ValueError("local_features_requires_exactly_one_local_feature_scores_output")
    keypoint_names = [
        name for name, spec in named_outputs.items()
        if isinstance(spec, dict) and spec.get("shadow_output") == LOCAL_FEATURE_KEYPOINTS
    ]
    descriptor_names = [
        name for name, spec in named_outputs.items()
        if isinstance(spec, dict) and spec.get("shadow_output") == LOCAL_FEATURE_DESCRIPTORS
    ]
    if len(keypoint_names) != 1 or len(descriptor_names) != 1:
        raise ValueError("local_features_score_filter_requires_keypoints_and_descriptors")
    keypoint_name, descriptor_name, score_name = keypoint_names[0], descriptor_names[0], score_names[0]
    if any(name not in outputs for name in (keypoint_name, descriptor_name, score_name)):
        raise ValueError("local_features_score_filter_declared_output_missing")
    keypoints = np.asarray(outputs[keypoint_name])
    descriptors = np.asarray(outputs[descriptor_name])
    scores = np.asarray(outputs[score_name], dtype=np.float32)
    if keypoints.ndim != 3 or descriptors.ndim != 3 or keypoints.shape[0] != 1 or descriptors.shape[0] != 1:
        raise ValueError("local_features_score_filter_requires_single_frame_batched_features")
    if keypoints.shape[1] != descriptors.shape[1] or keypoints.shape[2] != 2 or descriptors.shape[2] <= 0:
        raise ValueError("local_features_score_filter_feature_shapes_invalid")
    if scores.ndim == 3 and scores.shape[-1] == 1:
        scores = scores[..., 0]
    if scores.shape != keypoints.shape[:2] or not np.isfinite(scores).all():
        raise ValueError("local_features_score_filter_scores_invalid")
    score_spec = named_outputs[score_name]
    minimum_score = score_spec.get("minimum_score", 0.0)
    if not isinstance(minimum_score, (int, float)) or not np.isfinite(float(minimum_score)):
        raise ValueError("local_features_score_filter_minimum_score_invalid")
    accepted = scores[0] > float(minimum_score)
    filtered = dict(outputs)
    filtered[keypoint_name] = keypoints[:, accepted, :]
    filtered[descriptor_name] = descriptors[:, accepted, :]
    filtered[score_name] = scores[:, accepted]
    return filtered, {
        "status": "applied",
        "source_output": score_name,
        "minimum_score": float(minimum_score),
        "input_feature_count": int(keypoints.shape[1]),
        "accepted_feature_count": int(np.count_nonzero(accepted)),
        "rejected_feature_count": int(keypoints.shape[1] - np.count_nonzero(accepted)),
    }


def _local_matcher_evidence(contract: dict[str, Any], outputs: dict[str, np.ndarray]) -> dict[str, Any]:
    """Summarize only explicitly declared pair-index correspondence outputs."""

    index_name = next(
        (
            name for name, spec in contract.get("outputs", {}).items()
            if spec.get("shadow_output") == "match_indices"
            and spec.get("shadow_encoding") == "pairs_last_dim_2"
        ),
        None,
    )
    score_name = next(
        (
            name for name, spec in contract.get("outputs", {}).items()
            if spec.get("shadow_output") == "match_scores"
            and spec.get("shadow_encoding") == "per_correspondence"
        ),
        None,
    )
    if index_name is None or score_name is None:
        return {"status": "unavailable", "reason": "missing_declared_pair_correspondence_encoding"}
    if index_name not in outputs or score_name not in outputs:
        return {"status": "unavailable", "reason": "declared_pair_correspondence_output_missing"}
    pairs = np.asarray(outputs[index_name])
    scores = np.asarray(outputs[score_name], dtype=np.float32).reshape(-1)
    if pairs.ndim < 2 or pairs.shape[-1] != 2 or not np.issubdtype(pairs.dtype, np.number):
        return {"status": "unavailable", "reason": "invalid_pair_correspondence_shape"}
    flattened_pairs = pairs.reshape(-1, 2)
    valid_pair_mask = np.all(flattened_pairs >= 0, axis=1)
    if scores.size != flattened_pairs.shape[0] or not np.all(np.isfinite(scores)):
        return {"status": "unavailable", "reason": "pair_score_count_or_values_invalid"}
    score_spec = contract["outputs"][score_name]
    threshold = score_spec.get("minimum_confidence")
    if not isinstance(threshold, (int, float)) or not 0.0 <= float(threshold) <= 1.0:
        return {"status": "unavailable", "reason": "missing_or_invalid_match_score_threshold"}
    valid_scores = scores[valid_pair_mask]
    return {
        "status": "available",
        "declared_threshold": float(threshold),
        "pair_count": int(flattened_pairs.shape[0]),
        "valid_pair_count": int(np.count_nonzero(valid_pair_mask)),
        "accepted_score_count": int(np.count_nonzero(valid_scores >= float(threshold))),
        "mean_valid_score": float(valid_scores.mean()) if valid_scores.size else None,
        "max_valid_score": float(valid_scores.max()) if valid_scores.size else None,
    }


def _local_matcher_geometry_evidence(
    contract: dict[str, Any],
    inputs: dict[str, np.ndarray],
    outputs: dict[str, np.ndarray],
    map_features: dict[str, np.ndarray] | None,
    camera_matrix: np.ndarray | None,
    camera_image_size: tuple[int, int] | None,
) -> dict[str, Any]:
    """Verify declared query-to-map pair indices without creating a pose."""

    geometry = contract.get("geometry")
    if not isinstance(geometry, dict) or geometry.get("mode") not in {"homography_restricted", "pnp_3d"}:
        return {"status": "unavailable", "reason": "missing_supported_geometry_contract"}
    index_name = next(
        (
            name for name, spec in contract.get("outputs", {}).items()
            if spec.get("shadow_output") == "match_indices"
            and spec.get("shadow_encoding") == "pairs_last_dim_2"
            and spec.get("pair_order") == "query_then_map"
        ),
        None,
    )
    score_name = next(
        (
            name for name, spec in contract.get("outputs", {}).items()
            if spec.get("shadow_output") == "match_scores" and spec.get("shadow_encoding") == "per_correspondence"
        ),
        None,
    )
    query_name = next((name for name, spec in contract.get("inputs", {}).items() if spec.get("shadow_source") == QUERY_LOCAL_KEYPOINTS_SOURCE), None)
    map_name = next((name for name, spec in contract.get("inputs", {}).items() if spec.get("shadow_source") == MAP_TILE_KEYPOINTS_SOURCE), None)
    if not index_name or not score_name or not query_name or not map_name:
        return {"status": "unavailable", "reason": "missing_declared_homography_bindings"}
    if index_name not in outputs or score_name not in outputs:
        return {"status": "unavailable", "reason": "declared_homography_outputs_missing"}
    image_size = geometry.get("query_image_size")
    if not isinstance(image_size, list) or len(image_size) != 2 or not all(isinstance(value, int) and value > 0 for value in image_size):
        return {"status": "unavailable", "reason": "invalid_homography_query_image_size"}
    threshold = contract["outputs"][score_name].get("minimum_confidence")
    policy_names = ("min_inliers", "min_inlier_ratio", "max_reprojection_error_px", "min_grid_coverage")
    if not isinstance(threshold, (int, float)) or any(not isinstance(geometry.get(name), (int, float)) for name in policy_names):
        return {"status": "unavailable", "reason": "invalid_homography_thresholds"}
    width, height = image_size
    common = {
        "query_keypoints": inputs[query_name],
        "match_pairs": outputs[index_name],
        "scores": outputs[score_name],
        "query_width": width,
        "query_height": height,
        "minimum_score": float(threshold),
        "min_inliers": int(geometry["min_inliers"]),
        "min_inlier_ratio": float(geometry["min_inlier_ratio"]),
        "max_reprojection_error_px": float(geometry["max_reprojection_error_px"]),
        "min_grid_coverage": float(geometry["min_grid_coverage"]),
    }
    if geometry["mode"] == "homography_restricted":
        return verify_planar_correspondences(
            map_keypoints=inputs[map_name],
            **common,
        )
    if map_features is None or "map_points_enu_m" not in map_features:
        return {"status": "unavailable", "reason": "map_tile_has_no_3d_landmarks"}
    if camera_matrix is None or camera_image_size is None:
        return {"status": "unavailable", "reason": "measured_camera_matrix_required_for_pnp"}
    native_width, native_height = camera_image_size
    if native_width <= 0 or native_height <= 0:
        return {"status": "unavailable", "reason": "invalid_measured_camera_image_size"}
    scaled_camera = resized_camera_matrix(camera_matrix, (native_width, native_height), (width, height))
    result, _ = verify_bounded_pnp(
        map_points_enu_m=map_features["map_points_enu_m"],
        camera_matrix=scaled_camera,
        **common,
    )
    return result


def _temporal_student_evidence(contract: dict[str, Any], outputs: dict[str, np.ndarray]) -> dict[str, Any]:
    """Expose compact-student output as uncalibrated shadow evidence only."""

    named_outputs = contract.get("outputs") if isinstance(contract.get("outputs"), dict) else {}
    def output_name(tag: str, fallback: str) -> str | None:
        return next(
            (name for name, spec in named_outputs.items() if isinstance(spec, dict) and spec.get("shadow_output") == tag),
            fallback if fallback in named_outputs else None,
        )

    location_name = output_name(TEMPORAL_LOCATION_EVIDENCE, "location_distribution")
    heading_name = output_name(TEMPORAL_HEADING_EVIDENCE, "heading_distribution")
    feature_name = output_name(TEMPORAL_FEATURE_EVIDENCE, "temporal_feature")
    if not location_name or not heading_name or not feature_name:
        return {"status": "unavailable", "reason": "temporal_student_output_contract_incomplete"}
    if any(name not in outputs for name in (location_name, heading_name, feature_name)):
        return {"status": "unavailable", "reason": "temporal_student_declared_output_missing"}
    location = np.asarray(outputs[location_name], dtype=np.float32)
    heading = np.asarray(outputs[heading_name], dtype=np.float32)
    feature = np.asarray(outputs[feature_name], dtype=np.float32)
    if location.ndim != 2 or location.shape != (1, 3) or heading.ndim != 2 or heading.shape != (1, 2):
        return {"status": "unavailable", "reason": "temporal_student_output_shape_invalid"}
    if feature.ndim != 2 or feature.shape[0] != 1 or feature.shape[1] <= 0:
        return {"status": "unavailable", "reason": "temporal_student_feature_shape_invalid"}
    if not np.isfinite(location).all() or not np.isfinite(heading).all() or not np.isfinite(feature).all():
        return {"status": "unavailable", "reason": "temporal_student_output_not_finite"}
    heading_sin, heading_cos = float(heading[0, 0]), float(heading[0, 1])
    heading_norm = float(np.hypot(heading_sin, heading_cos))
    if heading_norm <= 1e-6:
        return {"status": "unavailable", "reason": "temporal_student_heading_vector_degenerate"}
    return {
        "status": "available",
        "position_authority": False,
        "location_evidence_enu_m": [float(value) for value in location[0]],
        "heading_evidence_rad": float(np.arctan2(heading_sin, heading_cos)),
        "heading_vector_norm": heading_norm,
        "temporal_feature_dimension": int(feature.shape[1]),
        "reason": "uncalibrated_student_evidence_shadow_only",
    }


def _verify_map_candidates(
    runner: LogicalRunner, contract: dict[str, Any], *, live_outputs: dict[str, np.ndarray],
    candidates: list[dict[str, Any]], tile_feature_lookup: Callable | None,
    camera_matrix: np.ndarray | None, camera_image_size: tuple[int, int] | None,
    limit: int,
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Verify a bounded set independently; never hide alternative valid matches."""
    unique = []
    seen = set()
    for candidate in candidates:
        tile_id = candidate.get("tile_id")
        if tile_id not in seen:
            unique.append(candidate)
            seen.add(tile_id)
    attempts = []
    started = perf_counter()
    for candidate in unique[:limit]:
        begin = perf_counter()
        inputs, reason, tile_id, features = _matcher_inputs(
            contract, live_outputs=live_outputs, candidates=[candidate], tile_feature_lookup=tile_feature_lookup,
        )
        tile_id = tile_id or candidate.get("tile_id")
        attempt = {"candidate_tile_id": tile_id, "status": "skipped", "reason": reason}
        if inputs is not None:
            try:
                outputs = runner.run("local_matcher", inputs)
                attempt = {
                    "candidate_tile_id": tile_id, "status": "executed",
                    "inputs": {name: list(value.shape) for name, value in inputs.items()},
                    "outputs": {name: _tensor_summary(value) for name, value in outputs.items()},
                    "correspondence_evidence": _local_matcher_evidence(contract, outputs),
                    "geometry_evidence": _local_matcher_geometry_evidence(
                        contract, inputs, outputs, features, camera_matrix, camera_image_size,
                    ),
                    "landmark_support_bounds_enu_m": landmark_bounds(features.get("map_points_enu_m")) if features else None,
                }
            except Exception as exc:
                attempt.update(status="failed", reason=f"{type(exc).__name__}:{exc}")
        attempt["latency_ms"] = round((perf_counter() - begin) * 1000, 3)
        attempts.append(attempt)
    accepted = [item for item in attempts if item.get("geometry_evidence", {}).get("status") == "accepted"]
    truncated = len(unique) > limit
    incomplete = any(item["status"] != "executed" or item.get("geometry_evidence", {}).get("status") == "unavailable" for item in attempts)
    selection = select_candidate_groups([
        {"tile_id": item["candidate_tile_id"], "geometry": item.get("geometry_evidence", {}),
         "landmark_support_bounds_enu_m": item.get("landmark_support_bounds_enu_m")} for item in attempts
    ], complete=not truncated and not incomplete)
    selection.update(candidate_count=len(unique), verified_count=len(attempts), limit=limit, truncated=truncated)
    selected = (next(item for item in accepted if item["candidate_tile_id"] == selection["selected_tile_id"])
                if selection["status"] == "unique" else (attempts[0] if attempts else {}))
    stage = {**selected, "role": "local_matcher", "candidate_attempts": attempts, "candidate_selection": selection,
             "latency_ms": round((perf_counter() - started) * 1000, 3)}
    stage["status"] = "executed" if any(a["status"] == "executed" for a in attempts) else ("failed" if any(a["status"] == "failed" for a in attempts) else "skipped")
    if not attempts:
        stage["reason"] = "no_retrieval_candidate_for_local_matcher"
    return stage, selection


def run_shadow_frame_probe(
    runner: LogicalRunner,
    *,
    model_release: ModelRelease,
    frame: ShadowFrame,
    candidate_lookup: Callable[[np.ndarray], list[dict[str, Any]]] | None = None,
    tile_feature_lookup: Callable[[str], dict[str, np.ndarray]] | None = None,
    temporal_window: TemporalWindowBuilder | None = None,
    frame_monotonic_ns: int | None = None,
    camera_matrix: np.ndarray | None = None,
    camera_image_size: tuple[int, int] | None = None,
    candidate_bank: ShadowCameraCandidateBank | None = None,
    max_candidate_verifications: int = 4,
) -> dict[str, Any]:
    """Execute stages that can be fed by one frame and visibly withhold the rest."""

    if isinstance(max_candidate_verifications, bool) or not isinstance(max_candidate_verifications, int) or not 1 <= max_candidate_verifications <= 24:
        raise ValueError("max_candidate_verifications must be between 1 and 24.")
    stages: list[dict[str, Any]] = []
    candidate_selection: dict[str, Any] = {"status": "withheld", "reason": "local_matcher_not_run"}
    latency_ms: dict[str, float] = {}
    top_k_candidates: list[dict[str, Any]] = []
    live_outputs: dict[str, np.ndarray] = {}
    correspondence_evidence: dict[str, Any] = {"status": "unavailable", "reason": "local_matcher_not_run"}
    geometry_evidence: dict[str, Any] = {"status": "unavailable", "reason": "local_matcher_not_run"}
    shadow_candidate: dict[str, Any] = {"status": "unavailable", "reason": "pnp_geometry_not_accepted"}
    temporal_evidence: dict[str, Any] = {"status": "not_configured"}
    temporal_student_evidence: dict[str, Any] = {"status": "not_configured"}
    pending_geometry_candidate: dict[str, Any] | None = None
    if temporal_window is not None:
        if frame_monotonic_ns is None:
            temporal_evidence = temporal_window.invalidate("frame_monotonic_timestamp_missing")
        else:
            temporal_evidence = temporal_window.observe_frame(frame.bgr, host_monotonic_ns=frame_monotonic_ns)
    executed = 0
    failed = 0
    for role in REQUIRED_ONNX_ARTIFACTS:
        contract = model_release.artifact_interfaces.get(role)
        if not isinstance(contract, dict):
            stages.append({"role": role, "status": "skipped", "reason": "missing_interface_contract"})
            continue
        if role == "local_matcher":
            stage, candidate_selection = _verify_map_candidates(
                runner, contract, live_outputs=live_outputs, candidates=top_k_candidates,
                tile_feature_lookup=tile_feature_lookup, camera_matrix=camera_matrix,
                camera_image_size=camera_image_size, limit=max_candidate_verifications,
            )
            stages.append(stage)
            latency_ms[role] = stage["latency_ms"]
            executed += int(stage["status"] == "executed")
            failed += int(any(item["status"] == "failed" for item in stage["candidate_attempts"]))
            correspondence_evidence = stage.get("correspondence_evidence", correspondence_evidence)
            geometry_evidence = stage.get("geometry_evidence", geometry_evidence)
            if candidate_selection["status"] == "unique":
                if "camera_position_enu_m" in geometry_evidence:
                    pending_geometry_candidate = {"tile_id": stage["candidate_tile_id"],
                                                  "camera_position_enu_m": geometry_evidence["camera_position_enu_m"]}
            elif candidate_selection["accepted_tile_ids"]:
                # Per-tile evidence remains available, but no arbitrary winner
                # is allowed to enter the temporal tracking candidate bank.
                geometry_evidence = {"status": "withheld", "reason": candidate_selection["reason"]}
                shadow_candidate = {"status": "withheld", "reason": candidate_selection["reason"]}
            continue
        elif role == "temporal_student":
            if temporal_window is None:
                inputs, skip_reason = None, "temporal_window_not_configured"
            else:
                inputs, skip_reason = temporal_window.build_inputs(contract, image_preprocessor=_preprocess_image)
        else:
            inputs, skip_reason = _frame_inputs(contract, frame)
        if inputs is None:
            stages.append({"role": role, "status": "skipped", "reason": skip_reason})
            continue
        started = perf_counter()
        try:
            outputs = runner.run(role, inputs)
            local_feature_filter = None
            if role == "local_features":
                outputs, local_feature_filter = filter_local_feature_outputs(contract, outputs)
            elapsed_ms = round((perf_counter() - started) * 1000.0, 3)
            latency_ms[role] = elapsed_ms
            stages.append({
                "role": role,
                "status": "executed",
                "latency_ms": elapsed_ms,
                "inputs": {name: list(value.shape) for name, value in inputs.items()},
                "outputs": {name: _tensor_summary(value) for name, value in outputs.items()},
            })
            if local_feature_filter is not None:
                stages[-1]["local_feature_filter"] = local_feature_filter
            for logical_name, output_spec in contract.get("outputs", {}).items():
                output_tag = output_spec.get("shadow_output")
                if output_tag and logical_name in outputs:
                    live_outputs[str(output_tag)] = np.asarray(outputs[logical_name])
            if role == "global_retriever" and candidate_lookup is not None:
                embedding_name = next(
                    (
                        name
                        for name, output_spec in contract.get("outputs", {}).items()
                        if output_spec.get("shadow_output") == "retrieval_embedding"
                    ),
                    None,
                )
                if embedding_name is None:
                    stages[-1]["retrieval"] = {"status": "skipped", "reason": "missing_retrieval_embedding_output_contract"}
                elif embedding_name not in outputs:
                    stages[-1]["retrieval"] = {"status": "failed", "reason": "declared_retrieval_embedding_missing_from_runner_output"}
                    failed += 1
                else:
                    try:
                        top_k_candidates = candidate_lookup(outputs[embedding_name])
                        stages[-1]["retrieval"] = {"status": "executed", "candidate_count": len(top_k_candidates)}
                    except Exception as exc:
                        stages[-1]["retrieval"] = {"status": "failed", "reason": f"{type(exc).__name__}:{exc}"}
                        failed += 1
            if role == "temporal_student":
                temporal_student_evidence = _temporal_student_evidence(contract, outputs)
                stages[-1]["temporal_student_evidence"] = temporal_student_evidence
            executed += 1
        except Exception as exc:  # Provider and exporter errors vary by environment.
            elapsed_ms = round((perf_counter() - started) * 1000.0, 3)
            latency_ms[role] = elapsed_ms
            stages.append({
                "role": role,
                "status": "failed",
                "latency_ms": elapsed_ms,
                "reason": f"{type(exc).__name__}:{exc}",
            })
            failed += 1

    if pending_geometry_candidate is not None:
        if temporal_evidence.get("status") != "ready":
            shadow_candidate = {"status": "waiting", "reason": "temporal_candidate_window_not_ready"}
        elif temporal_student_evidence.get("status") != "available":
            shadow_candidate = {"status": "waiting", "reason": "temporal_student_evidence_not_available"}
        elif candidate_bank is None or frame_monotonic_ns is None or not pending_geometry_candidate.get("tile_id"):
            shadow_candidate = {"status": "waiting", "reason": "candidate_bank_or_timestamp_unavailable"}
        else:
            shadow_candidate = candidate_bank.update(
                tile_id=str(pending_geometry_candidate["tile_id"]),
                camera_position_enu_m=pending_geometry_candidate["camera_position_enu_m"],
                timestamp_monotonic_ns=frame_monotonic_ns,
            )
    if candidate_bank is not None and shadow_candidate.get("status") not in {"warming_up", "tracking_candidate"}:
        candidate_bank.invalidate()

    if failed:
        decision_reason = "learned_inference_stage_failed"
    elif executed:
        decision_reason = "learned_frame_probe_complete_shadow_only"
    else:
        decision_reason = "no_frame_runnable_model_stage"
    return {
        "frame": {"path": str(frame.path), "width": frame.width, "height": frame.height},
        "stages": stages,
        "latency_ms": latency_ms,
        "top_k_candidates": top_k_candidates,
        "candidate_selection": candidate_selection,
        "correspondence_evidence": correspondence_evidence,
        "geometry_evidence": geometry_evidence,
        "shadow_tracking_candidate": shadow_candidate,
        "temporal_context": temporal_evidence,
        "temporal_student_evidence": temporal_student_evidence,
        "executed_stage_count": executed,
        "failed_stage_count": failed,
        "decision_reason": decision_reason,
    }
