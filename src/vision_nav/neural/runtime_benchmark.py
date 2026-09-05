"""Repeatable learned shadow-runtime performance evidence for target hardware.

The benchmark deliberately reuses :func:`run_shadow_frame_probe`, which is the
same release-bound path used by the Pi shadow runtime.  It never creates a pose
publication path and never sends MAVLink.  Its output is evidence for a human
model-promotion review, not an automatic flight-authority decision.
"""

from __future__ import annotations

import argparse
from datetime import UTC, datetime
import json
from pathlib import Path
import time
from typing import Any, Callable, Iterable

import numpy as np

from .hardware_resources import _process_rss_bytes, _thermal_snapshot
from ..camera import CameraCalibration, load_camera_calibration, validate_image_size
from .local_feature_cache import load_local_feature_cache
from .map_asset_validation import validate_learned_map_assets
from .onnx_runtime import LearnedModelRunner
from .releases import ModelRelease, load_model_release
from .retrieval_index import load_tile_retriever
from .shadow_inference import LogicalRunner, ShadowFrame, load_shadow_frame, run_shadow_frame_probe
from .shadow_runtime import _release_asset_path, check_runtime_compatibility


RUNTIME_BENCHMARK_SCHEMA = "vision_nav_learned_runtime_benchmark_v1"
DEFAULT_REQUIRED_ROLES = ("global_retriever", "local_features", "local_matcher")


def _utc_now() -> str:
    return datetime.now(UTC).isoformat(timespec="microseconds").replace("+00:00", "Z")


def _percentile(values: list[float], percentile: float) -> float | None:
    if not values:
        return None
    return round(float(np.percentile(np.asarray(values, dtype=np.float64), percentile)), 3)


def _latency_summary(values: list[float]) -> dict[str, float | int | None]:
    return {
        "count": len(values),
        "min": round(min(values), 3) if values else None,
        "p50": _percentile(values, 50.0),
        "p95": _percentile(values, 95.0),
        "p99": _percentile(values, 99.0),
        "max": round(max(values), 3) if values else None,
    }


def _collect_stage_latencies(probe: dict[str, Any], stage_values: dict[str, list[float]]) -> None:
    for stage in probe.get("stages", []):
        if stage.get("status") != "executed":
            continue
        role = stage.get("role")
        latency = stage.get("latency_ms")
        if isinstance(role, str) and isinstance(latency, (int, float)):
            stage_values.setdefault(role, []).append(float(latency))


def benchmark_shadow_frames(
    runner: LogicalRunner,
    *,
    model_release: ModelRelease,
    frames: Iterable[ShadowFrame],
    map_release_id: str | None = None,
    candidate_lookup: Callable[[np.ndarray], list[dict[str, Any]]] | None = None,
    tile_feature_lookup: Callable[[str], dict[str, np.ndarray]] | None = None,
    camera_calibration: CameraCalibration | None = None,
    warmup_count: int = 1,
    required_roles: Iterable[str] = DEFAULT_REQUIRED_ROLES,
    max_p95_end_to_end_ms: float | None = 1000.0,
    max_rss_mib: float | None = 2048.0,
    max_temperature_c: float | None = 80.0,
    min_recorded_frames: int = 3,
    max_candidate_verifications: int = 4,
) -> dict[str, Any]:
    """Run a bounded, frame-file benchmark through learned shadow inference.

    ``frames`` are intentionally already decoded by the caller so capture and
    file-system variability do not masquerade as model inference performance.
    The per-frame result carries all skipped or failed stages, preserving enough
    detail to distinguish a slow model from an incomplete model release.
    """

    if warmup_count < 0:
        raise ValueError("warmup_count cannot be negative.")
    if min_recorded_frames <= 0:
        raise ValueError("min_recorded_frames must be positive.")
    if max_p95_end_to_end_ms is not None and max_p95_end_to_end_ms <= 0.0:
        raise ValueError("max_p95_end_to_end_ms must be positive when set.")
    if max_rss_mib is not None and max_rss_mib <= 0.0:
        raise ValueError("max_rss_mib must be positive when set.")
    if max_temperature_c is not None and max_temperature_c <= 0.0:
        raise ValueError("max_temperature_c must be positive when set.")

    prepared_frames = list(frames)
    declared_required_roles = tuple(dict.fromkeys(str(role) for role in required_roles if str(role)))
    if not declared_required_roles:
        raise ValueError("At least one required role is needed for a runtime benchmark.")

    errors: list[str] = []
    warnings: list[str] = []
    records: list[dict[str, Any]] = []
    stage_values: dict[str, list[float]] = {}
    end_to_end_values: list[float] = []
    rss_values: list[int] = []
    thermal_samples: list[dict[str, Any]] = []
    initial_rss = _process_rss_bytes()
    if initial_rss is not None:
        rss_values.append(initial_rss)
    cpu_started = time.process_time()
    wall_started = time.perf_counter()

    # Warmups use the exact same probe but are deliberately omitted from the
    # report statistics.  Reuse frames cyclically when the caller has fewer
    # than warmup + measured inputs; the measured-frame minimum remains strict.
    if prepared_frames:
        for index in range(warmup_count):
            frame = prepared_frames[index % len(prepared_frames)]
            run_shadow_frame_probe(
                runner,
                model_release=model_release,
                frame=frame,
                candidate_lookup=candidate_lookup,
                max_candidate_verifications=max_candidate_verifications,
                tile_feature_lookup=tile_feature_lookup,
                camera_matrix=camera_calibration.camera_matrix if camera_calibration else None,
                camera_image_size=camera_calibration.image_size if camera_calibration else None,
            )

    for sequence, frame in enumerate(prepared_frames):
        if camera_calibration is not None:
            try:
                validate_image_size(frame.bgr, camera_calibration)
            except ValueError as exc:
                records.append({"sequence": sequence, "frame": str(frame.path), "status": "failed", "reason": str(exc)})
                continue
        started = time.perf_counter()
        try:
            probe = run_shadow_frame_probe(
                runner,
                model_release=model_release,
                frame=frame,
                candidate_lookup=candidate_lookup,
                max_candidate_verifications=max_candidate_verifications,
                tile_feature_lookup=tile_feature_lookup,
                camera_matrix=camera_calibration.camera_matrix if camera_calibration else None,
                camera_image_size=camera_calibration.image_size if camera_calibration else None,
            )
            elapsed_ms = (time.perf_counter() - started) * 1000.0
            _collect_stage_latencies(probe, stage_values)
            end_to_end_values.append(elapsed_ms)
            records.append({
                "sequence": sequence,
                "frame": str(frame.path),
                "status": "completed" if probe["failed_stage_count"] == 0 else "failed_stage",
                "end_to_end_latency_ms": round(elapsed_ms, 3),
                "decision_reason": probe["decision_reason"],
                "executed_stage_count": probe["executed_stage_count"],
                "failed_stage_count": probe["failed_stage_count"],
                "stage_statuses": {str(stage["role"]): str(stage["status"]) for stage in probe["stages"]},
            })
        except Exception as exc:  # Keep a durable report even when one frame fails.
            records.append({
                "sequence": sequence,
                "frame": str(frame.path),
                "status": "failed",
                "reason": f"{type(exc).__name__}:{exc}",
            })
        rss = _process_rss_bytes()
        if rss is not None:
            rss_values.append(rss)
        thermal = _thermal_snapshot()
        thermal_samples.extend(thermal["samples"])

    wall_time_s = time.perf_counter() - wall_started
    cpu_time_s = time.process_time() - cpu_started
    completed_records = [record for record in records if record["status"] == "completed"]
    failed_records = [record for record in records if record["status"] != "completed"]
    executed_roles = {role for role, values in stage_values.items() if values}
    missing_roles = sorted(set(declared_required_roles) - executed_roles)
    if len(prepared_frames) < min_recorded_frames:
        errors.append("insufficient_input_frames")
    if len(completed_records) < min_recorded_frames:
        errors.append("insufficient_completed_frames")
    if failed_records:
        errors.append("shadow_inference_frame_failures")
    if missing_roles:
        errors.append("required_learned_stages_not_executed")
    end_to_end = _latency_summary(end_to_end_values)
    if max_p95_end_to_end_ms is not None and isinstance(end_to_end["p95"], float) and end_to_end["p95"] > max_p95_end_to_end_ms:
        errors.append("end_to_end_p95_exceeds_limit")
    maximum_rss = max(rss_values) if rss_values else None
    if max_rss_mib is not None and maximum_rss is not None and maximum_rss > max_rss_mib * 1024 * 1024:
        errors.append("rss_exceeds_limit")
    maximum_temperature = max((item["temperature_c"] for item in thermal_samples), default=None)
    if not thermal_samples:
        warnings.append("thermal_sensor_unavailable")
    elif max_temperature_c is not None and maximum_temperature is not None and maximum_temperature > max_temperature_c:
        errors.append("temperature_exceeds_limit")

    return {
        "schema_version": RUNTIME_BENCHMARK_SCHEMA,
        "generated_at_utc": _utc_now(),
        "status": "passed" if not errors else "failed",
        "publication": {
            "shadow_only": True,
            "px4_external_vision": "disabled",
            "promotion_decision": "human_review_required",
        },
        "active_release": {
            "map_release_id": map_release_id,
            "model_release_id": model_release.release_id,
            "profile_id": model_release.profile_id,
        },
        "requirements": {
            "warmup_count": warmup_count,
            "min_recorded_frames": min_recorded_frames,
            "required_roles": list(declared_required_roles),
            "max_p95_end_to_end_ms": max_p95_end_to_end_ms,
            "max_rss_mib": max_rss_mib,
            "max_temperature_c": max_temperature_c,
        },
        "metrics": {
            "frame_count": len(prepared_frames),
            "completed_frame_count": len(completed_records),
            "failed_frame_count": len(failed_records),
            "capture_drop_measurement": {
                "status": "not_measured_saved_frame_benchmark",
                "dropped_frame_count": None,
                "reason": "Saved frame inputs isolate model throughput; collect live-camera cadence evidence separately.",
            },
            "wall_time_s": round(wall_time_s, 6),
            "process_cpu_time_s": round(cpu_time_s, 6),
            "process_cpu_utilization_pct": round((cpu_time_s / wall_time_s) * 100.0, 3) if wall_time_s > 0.0 else None,
            "end_to_end_latency_ms": end_to_end,
            "stage_latency_ms": {role: _latency_summary(values) for role, values in sorted(stage_values.items())},
            "rss_bytes": {"initial": initial_rss, "maximum": maximum_rss},
            "thermal": {
                "status": "available" if thermal_samples else "unavailable",
                "sample_count": len(thermal_samples),
                "max_temperature_c": maximum_temperature,
            },
        },
        "coverage": {"executed_roles": sorted(executed_roles), "missing_required_roles": missing_roles},
        "records": records,
        "errors": errors,
        "warnings": warnings,
    }


def _frames_from_arguments(frame_paths: Iterable[Path], frames_dir: Path | None) -> list[ShadowFrame]:
    paths = list(frame_paths)
    if frames_dir is not None:
        directory = frames_dir.expanduser().resolve()
        if not directory.is_dir():
            raise ValueError(f"Frame directory does not exist: {directory}")
        paths.extend(sorted(path for path in directory.iterdir() if path.suffix.lower() in {".jpg", ".jpeg", ".png"}))
    unique_paths = list(dict.fromkeys(path.expanduser().resolve() for path in paths))
    if not unique_paths:
        raise ValueError("Provide at least one --frame or a directory containing JPG/PNG frames.")
    return [load_shadow_frame(path) for path in unique_paths]


def _load_measured_calibration(path: Path, calibration_id: str) -> CameraCalibration:
    calibration = load_camera_calibration(path)
    if calibration.calibration_status != "measured":
        raise ValueError("Runtime benchmark requires a measured camera calibration.")
    if calibration.calibration_id != calibration_id:
        raise ValueError("Camera calibration ID does not match --calibration-id.")
    return calibration


def main() -> None:
    parser = argparse.ArgumentParser(description="Benchmark learned Pi shadow inference without publishing PX4 odometry.")
    parser.add_argument("--map-release", required=True, type=Path)
    parser.add_argument("--model-release", required=True, type=Path)
    parser.add_argument("--model-artifact-root", type=Path)
    parser.add_argument("--calibration-id", required=True)
    parser.add_argument("--camera-calibration", required=True, type=Path)
    parser.add_argument("--provider", default="onnxruntime_cpu")
    parser.add_argument("--frame", action="append", default=[], type=Path)
    parser.add_argument("--frames-dir", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    parser.add_argument("--warmup-count", type=int, default=1)
    parser.add_argument("--retrieval-top-k", type=int, default=12)
    parser.add_argument("--max-candidate-verifications", type=int, default=4)
    parser.add_argument("--require-role", action="append", default=[])
    parser.add_argument("--min-recorded-frames", type=int, default=3)
    parser.add_argument("--max-p95-end-to-end-ms", type=float, default=1000.0)
    parser.add_argument("--max-rss-mib", type=float, default=2048.0)
    parser.add_argument("--max-temperature-c", type=float, default=80.0)
    args = parser.parse_args()

    map_path = args.map_release.expanduser().resolve()
    model_path = args.model_release.expanduser().resolve()
    map_release = json.loads(map_path.read_text(encoding="utf-8"))
    model_release = load_model_release(model_path)
    artifact_root = (args.model_artifact_root or model_path.parent).expanduser().resolve()
    compatibility = check_runtime_compatibility(
        map_release,
        model_release,
        calibration_id=args.calibration_id,
        provider=args.provider,
        root=map_path.parent,
        model_root=artifact_root,
    )
    if not compatibility.compatible:
        raise SystemExit("Runtime benchmark release compatibility failed: " + "; ".join(compatibility.reasons))
    asset_audit = validate_learned_map_assets(map_path)
    if asset_audit["status"] != "valid":
        raise SystemExit("Runtime benchmark learned-map audit failed: " + "; ".join(asset_audit["errors"]))
    calibration = _load_measured_calibration(args.camera_calibration, args.calibration_id)
    frames = _frames_from_arguments(args.frame, args.frames_dir)
    retriever = load_tile_retriever(
        _release_asset_path(map_path, str(map_release["retrieval_index_uri"])),
        _release_asset_path(map_path, str(map_release["retrieval_metadata_uri"])),
        expected_map_release_id=str(map_release["release_id"]),
        expected_model_release_id=model_release.release_id,
    )
    cache = load_local_feature_cache(
        _release_asset_path(map_path, str(map_release["local_feature_metadata_uri"])),
        expected_map_release_id=str(map_release["release_id"]),
        expected_model_release_id=model_release.release_id,
    )
    runner = LearnedModelRunner(model_release, artifact_root=artifact_root, provider=args.provider)
    report = benchmark_shadow_frames(
        runner,
        model_release=model_release,
        frames=frames,
        map_release_id=str(map_release["release_id"]),
        candidate_lookup=lambda embedding: retriever.search(embedding, top_k=args.retrieval_top_k),
        max_candidate_verifications=args.max_candidate_verifications,
        tile_feature_lookup=cache.load,
        camera_calibration=calibration,
        warmup_count=args.warmup_count,
        required_roles=args.require_role or DEFAULT_REQUIRED_ROLES,
        max_p95_end_to_end_ms=args.max_p95_end_to_end_ms,
        max_rss_mib=args.max_rss_mib,
        max_temperature_c=args.max_temperature_c,
        min_recorded_frames=args.min_recorded_frames,
    )
    report["map_asset_audit"] = {
        "status": asset_audit["status"],
        "tile_count": asset_audit["metrics"]["tile_count"],
        "feature_tile_count": asset_audit["metrics"]["feature_tile_count"],
    }
    args.output.expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)
    args.output.expanduser().resolve().write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(report, indent=2, sort_keys=True))
    if report["status"] != "passed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
