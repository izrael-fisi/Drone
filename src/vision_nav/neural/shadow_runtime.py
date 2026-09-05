"""Learned Pi shadow-runtime entrypoint.

This process deliberately never publishes MAVLink vision/odometry. It validates the
learned release chain, emits a complete decision trace, and broadcasts status for
ground-station review before any flight-control integration is considered.
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
from datetime import UTC, datetime
import json
import math
import os
from pathlib import Path
import socket
import time
from typing import Any

from .map_release import load_release, validate_release
from .artifacts import validate_model_artifacts
from .onnx_runtime import LearnedModelRunner
from .profiles import InferenceProfile, load_profile
from .local_feature_cache import load_local_feature_cache
from .latest_capture import LatestCaptureWorker
from .attitude_collector import MavlinkAttitudeCollector
from .status_heartbeat import ShadowStatusHeartbeat, write_status_snapshot
from .runtime_metrics import RuntimeMetricsRecorder
from .retrieval_index import load_tile_retriever
from .releases import ModelRelease, load_model_release
from .shadow_inference import load_shadow_frame, run_shadow_frame_probe
from .shadow_hypotheses import ShadowCameraCandidateBank
from .temporal_window import ATTITUDE_WINDOW_RPY_SOURCE, FRAME_WINDOW_RGB_SOURCE, TemporalWindowBuilder
from ..camera import CameraCalibration, load_camera_calibration, validate_image_size
from ..camera_ingress import CameraIngress, CapturedFrame, create_camera_ingress
from ..capture_timing import TimingPolicy, camera_exposure_time
from ..camera_health import analyze_frame_file
from ..mavlink_bridge import MavlinkVisionBridge


@dataclass(frozen=True)
class RuntimeCompatibility:
    compatible: bool
    reasons: tuple[str, ...]
    provider: str
    profile: InferenceProfile


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def check_runtime_compatibility(
    map_release: dict[str, Any], model_release: ModelRelease, *, calibration_id: str,
    provider: str, root: Path, model_root: Path | None = None,
) -> RuntimeCompatibility:
    profile = load_profile(model_release.profile_id)
    reasons: list[str] = []
    try:
        validate_release(map_release, root=root, model_release=model_release.to_dict())
    except ValueError as exc:
        reasons.append(str(exc))
    if calibration_id not in model_release.calibration_ids:
        reasons.append("camera_calibration_not_compatible_with_model")
    if calibration_id not in map_release.get("calibration_ids", []):
        reasons.append("camera_calibration_not_compatible_with_map")
    if provider not in profile.supported_providers:
        reasons.append("inference_provider_not_supported_by_profile")
    if provider == "hailo_ai_hat_optional" and not os.environ.get("HAILO_ARCH"):
        reasons.append("hailo_provider_requested_but_hailo_runtime_not_detected")
    if model_root is not None:
        artifact_validation = validate_model_artifacts(
            model_release,
            artifact_root=model_root,
            require_interfaces=True,
        )
        reasons.extend(artifact_validation.errors)
    return RuntimeCompatibility(not reasons, tuple(reasons), provider, profile)


def runtime_status_v3(
    *, compatibility: RuntimeCompatibility, map_release: dict[str, Any], model_release: ModelRelease,
    camera_health: str, runtime_state: str, sequence: int, reason: str | None = None,
    latency_ms: dict[str, float] | None = None, inference_probe: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": "vision_nav_runtime_status_v3",
        "updated_at_utc": utc_now(),
        "sequence": sequence,
        "runtime_state": runtime_state,
        "shadow_mode": True,
        "px4_publication": {"enabled": False, "reason": "shadow_release_no_odometry_output"},
        "active_map": {
            "release_id": map_release.get("release_id"), "map_id": map_release.get("map_id"),
            "gsd_m_per_px": map_release.get("gsd_m_per_px"), "crs": map_release.get("crs"),
            "geometry_modes": map_release.get("geometry_modes", []),
        },
        "active_model": {
            "release_id": model_release.release_id, "profile_id": model_release.profile_id,
            "semantic_version": model_release.semantic_version,
            "onnx_digest_sha256": model_release.onnx_digest_sha256,
            "teacher_lineage": list(model_release.teacher_lineage),
        },
        "inference": {
            "provider": compatibility.provider,
            "provider_ready": compatibility.compatible,
            "global_retriever": compatibility.profile.global_retriever,
            "local_correspondence": compatibility.profile.local_correspondence,
            "temporal_student": compatibility.profile.temporal_student,
            "latency_ms": latency_ms or {},
            "frame_probe": inference_probe or {},
        },
        "camera_health": {"status": camera_health},
        "position": {
            "source_state": "no_position", "navigation_mode": "NO_GLOBAL_FIX",
            "heading_confidence": 0.0, "location_confidence": 0.0,
            "top_k_candidates": (inference_probe or {}).get("top_k_candidates", []), "active_hypotheses": [],
            "shadow_tracking_candidate": (inference_probe or {}).get("shadow_tracking_candidate"),
            "temporal_student_evidence": (inference_probe or {}).get("temporal_student_evidence"),
        },
        "compatibility": {"compatible": compatibility.compatible, "reasons": list(compatibility.reasons)},
        "publication_decision": {"published": False, "reason": reason or "shadow_mode"},
    }


def _write_trace(output_dir: Path, status: dict[str, Any], *, snapshot: bool = True) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    if snapshot:
        write_status_snapshot(output_dir / "runtime_status.json", status)
    with (output_dir / "decision_trace.jsonl").open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(status, sort_keys=True) + "\n")


def _send_udp(status: dict[str, Any], target: str | None) -> None:
    if not target:
        return
    host, separator, port = target.rpartition(":")
    if not separator or not host or not port.isdigit():
        raise ValueError("Status UDP target must use host:port.")
    if not 1 <= int(port) <= 65535:
        raise ValueError("Status UDP port must be from 1 to 65535.")
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
        sock.settimeout(0.5)
        sock.sendto(json.dumps(status).encode("utf-8"), (host, int(port)))


def _emit_heartbeat(output_dir: Path, target: str | None, status: dict[str, Any]) -> str | None:
    write_status_snapshot(output_dir / "runtime_status.json", status)
    try:
        _send_udp(status, target)
    except (OSError, ValueError) as exc:
        return f"{type(exc).__name__}:{exc}"
    return None


def _load_inputs(args: argparse.Namespace) -> tuple[dict[str, Any], ModelRelease, RuntimeCompatibility]:
    map_path = args.map_release.expanduser().resolve()
    model_path = args.model_release.expanduser().resolve()
    raw_map = json.loads(map_path.read_text(encoding="utf-8"))
    model = load_model_release(model_path)
    compatibility = check_runtime_compatibility(
        raw_map, model, calibration_id=args.calibration_id, provider=args.provider, root=map_path.parent,
        model_root=args.model_artifact_root or model_path.parent,
    )
    return raw_map, model, compatibility


def _sample_frame(
    args: argparse.Namespace,
    *,
    sequence: int,
    ingress: CameraIngress | None,
) -> tuple[CapturedFrame | None, CameraIngress | None]:
    if args.frame:
        return CapturedFrame(path=args.frame.expanduser().resolve(), backend="file"), ingress
    if args.camera_backend == "none":
        return None, ingress
    if ingress is None:
        ingress = create_camera_ingress(
            args.camera_backend,
            width=args.camera_width,
            height=args.camera_height,
            timeout_ms=args.camera_timeout_ms,
        )
    capture_path = args.output_dir / "frames" / f"shadow_{sequence:06d}.jpg"
    return ingress.capture(capture_path), ingress


def shadow_capture_timing(captured: CapturedFrame, *, now_ns: int, max_age_ms: float) -> dict[str, Any]:
    """Retain camera time; processing/reception time is never a substitute."""
    if type(now_ns) is not int or now_ns <= 0 or type(max_age_ms) not in (int, float) or not math.isfinite(max_age_ms) or max_age_ms <= 0:
        raise ValueError("Valid monotonic time and finite positive maximum frame age required")
    camera = camera_exposure_time(captured.to_dict(), TimingPolicy())
    result = {"camera": camera, "observed_host_monotonic_ns": now_ns,
              "host_monotonic_ns": None, "age_ms": None,
              "attitude_time_basis": "host_receive_unqualified",
              "position_authority": False, "sensor_timing_calibrated": False}
    if camera["status"] != "available":
        return {**result, "status": "unavailable", "reason": camera["reason"]}
    age = (now_ns - camera["host_monotonic_ns"]) / 1e6
    result["age_ms"] = age
    if age < 0 or age > max_age_ms:
        return {**result, "status": "rejected", "reason": "camera_timestamp_future_or_stale"}
    return {**result, "status": "available", "host_monotonic_ns": camera["host_monotonic_ns"]}


def _release_asset_path(release_path: Path, location: str) -> Path:
    path = Path(location).expanduser()
    return path if path.is_absolute() else release_path.parent / path


def _frame_calibration(args: argparse.Namespace, compatibility: RuntimeCompatibility) -> tuple[CameraCalibration | None, RuntimeCompatibility]:
    """Require measured intrinsics before a live frame enters a learned model."""

    if not (args.frame or args.camera_backend != "none"):
        return None, compatibility
    if not args.camera_calibration:
        reason = "camera_calibration_file_required_for_frame_inference"
        return None, RuntimeCompatibility(False, (*compatibility.reasons, reason), compatibility.provider, compatibility.profile)
    try:
        calibration = load_camera_calibration(args.camera_calibration)
    except (OSError, ValueError) as exc:
        reason = f"camera_calibration_load_failed:{type(exc).__name__}:{exc}"
        return None, RuntimeCompatibility(False, (*compatibility.reasons, reason), compatibility.provider, compatibility.profile)
    reasons: list[str] = []
    if calibration.calibration_status != "measured":
        reasons.append("camera_calibration_not_measured")
    if calibration.calibration_id != args.calibration_id:
        reasons.append("camera_calibration_id_does_not_match_runtime")
    if reasons:
        return None, RuntimeCompatibility(False, (*compatibility.reasons, *reasons), compatibility.provider, compatibility.profile)
    return calibration, compatibility


def _temporal_window_size(model_release: ModelRelease) -> int | None:
    contract = model_release.artifact_interfaces.get("temporal_student")
    if not isinstance(contract, dict):
        return None
    sizes: set[int] = set()
    sources = set()
    for spec in contract.get("inputs", {}).values():
        source = spec.get("shadow_source")
        if source in {FRAME_WINDOW_RGB_SOURCE, ATTITUDE_WINDOW_RPY_SOURCE}:
            sources.add(source)
            size = spec.get("window_size")
            if not isinstance(size, int) or size <= 0:
                return None
            sizes.add(size)
    if sources != {FRAME_WINDOW_RGB_SOURCE, ATTITUDE_WINDOW_RPY_SOURCE} or len(sizes) != 1:
        return None
    return sizes.pop()


def main() -> None:
    parser = argparse.ArgumentParser(description="Run learned Pi cross-view navigation in telemetry-only shadow mode.")
    parser.add_argument("--map-release", required=True, type=Path)
    parser.add_argument("--model-release", required=True, type=Path)
    parser.add_argument("--calibration-id", required=True)
    parser.add_argument("--provider", default="onnxruntime_cpu")
    parser.add_argument("--output-dir", required=True, type=Path)
    parser.add_argument("--model-artifact-root", type=Path, help="Defaults to the ModelRelease directory.")
    parser.add_argument("--frame", type=Path, help="Run release-bound shadow inference on this frame.")
    parser.add_argument("--camera-calibration", type=Path, help="Measured camera calibration YAML required for frame inference.")
    parser.add_argument("--camera-backend", choices=("none", "auto", "picamera2", "rpicam_still"), default="none")
    parser.add_argument("--camera-width", type=int, default=1456)
    parser.add_argument("--camera-height", type=int, default=1088)
    parser.add_argument("--camera-timeout-ms", type=int, default=1000)
    parser.add_argument("--capture-interval-s", type=float, default=0.5,
                        help="Independent live still-capture interval; only the newest waiting frame is kept.")
    parser.add_argument("--retrieval-top-k", type=int, default=12)
    parser.add_argument("--max-candidate-verifications", type=int, default=4,
                        help="Bound matcher/geometry attempts per frame (1-24); incomplete searches cannot yield a unique candidate.")
    parser.add_argument("--mavlink-endpoint", help="Read-only PX4 MAVLink link for temporal attitude context.")
    parser.add_argument("--mavlink-source-system", type=int, default=42)
    parser.add_argument("--mavlink-source-component", type=int, default=197)
    parser.add_argument("--mavlink-target-system", type=int, default=1)
    parser.add_argument("--mavlink-target-component", type=int, default=1)
    parser.add_argument("--attitude-stale-after-ms", type=float, default=500.0)
    parser.add_argument("--max-attitude-frame-delta-ms", type=float, default=50.0)
    parser.add_argument("--max-temporal-frame-gap-ms", type=float, default=1500.0)
    parser.add_argument("--max-frame-age-ms", type=float, default=1000.0,
                        help="Maximum capture-to-result age for temporal candidate evidence; not a flight threshold.")
    parser.add_argument("--status-udp-target")
    parser.add_argument("--heartbeat-interval-s", type=float, default=0.5)
    parser.add_argument("--inference-warning-ms", type=float, default=2000.0)
    parser.add_argument("--record-runtime-metrics", action="store_true")
    parser.add_argument("--metrics-interval-s", type=float, default=1.0)
    parser.add_argument("--metrics-max-samples", type=int, default=3600)
    parser.add_argument("--once", action="store_true", help="Write one validation/status snapshot and exit.")
    parser.add_argument("--interval-s", type=float, default=1.0)
    args = parser.parse_args()
    for value in (args.max_frame_age_ms, args.max_temporal_frame_gap_ms, args.max_attitude_frame_delta_ms,
                  args.capture_interval_s, args.camera_timeout_ms, args.attitude_stale_after_ms,
                  args.heartbeat_interval_s, args.inference_warning_ms, args.metrics_interval_s):
        if not math.isfinite(value) or value <= 0:
            parser.error("Timing limits must be finite and positive")
    if any(not 1 <= value <= 255 for value in (args.mavlink_target_system, args.mavlink_target_component)):
        parser.error("Expected MAVLink sender IDs must be from 1 to 255")
    if not 1 <= args.metrics_max_samples <= 36000:
        parser.error("Metrics sample count must be from 1 to 36000")
    if args.record_runtime_metrics and args.once:
        parser.error("Runtime metrics require a continuous run, not --once")
    map_release, model_release, compatibility = _load_inputs(args)
    calibration, compatibility = _frame_calibration(args, compatibility)
    sequence = 0
    runner = None
    candidate_lookup = None
    tile_feature_lookup = None
    retrieval_error = None
    local_feature_error = None
    temporal_window = None
    temporal_error = None
    attitude_collector = None
    attitude_generation = None
    candidate_bank = ShadowCameraCandidateBank()
    if compatibility.compatible and (args.frame or args.camera_backend != "none"):
        try:
            runner = LearnedModelRunner(
                model_release,
                artifact_root=args.model_artifact_root or args.model_release.expanduser().resolve().parent,
                provider=args.provider,
            )
        except (RuntimeError, ValueError) as exc:
            compatibility = RuntimeCompatibility(
                False,
                (*compatibility.reasons, f"learned_runner_unavailable:{type(exc).__name__}:{exc}"),
                compatibility.provider,
                compatibility.profile,
            )
    if compatibility.compatible and runner is not None and map_release.get("retrieval_metadata_uri"):
        try:
            active_map_path = args.map_release.expanduser().resolve()
            retriever = load_tile_retriever(
                _release_asset_path(active_map_path, str(map_release["retrieval_index_uri"])),
                _release_asset_path(active_map_path, str(map_release["retrieval_metadata_uri"])),
                expected_map_release_id=str(map_release["release_id"]),
                expected_model_release_id=model_release.release_id,
            )
            candidate_lookup = lambda embedding: retriever.search(embedding, top_k=args.retrieval_top_k)
        except (RuntimeError, ValueError) as exc:
            retrieval_error = f"retrieval_index_unavailable:{type(exc).__name__}:{exc}"
    if compatibility.compatible and runner is not None and map_release.get("local_feature_metadata_uri"):
        try:
            active_map_path = args.map_release.expanduser().resolve()
            feature_cache = load_local_feature_cache(
                _release_asset_path(active_map_path, str(map_release["local_feature_metadata_uri"])),
                expected_map_release_id=str(map_release["release_id"]),
                expected_model_release_id=model_release.release_id,
            )
            tile_feature_lookup = feature_cache.load
        except (RuntimeError, ValueError) as exc:
            local_feature_error = f"local_feature_cache_unavailable:{type(exc).__name__}:{exc}"
    temporal_window_size = _temporal_window_size(model_release)
    if compatibility.compatible and runner is not None and temporal_window_size:
        if args.frame:
            temporal_error = "temporal_window_requires_live_camera_frame_timestamps"
        elif not args.mavlink_endpoint:
            temporal_error = "temporal_window_requires_read_only_mavlink_endpoint"
        else:
            temporal_window = TemporalWindowBuilder(
                window_size=temporal_window_size,
                max_attitude_frame_delta_ms=args.max_attitude_frame_delta_ms,
                max_frame_gap_ms=args.max_temporal_frame_gap_ms,
            )
    ingress: CameraIngress | None = None
    capture_worker: LatestCaptureWorker | None = None
    heartbeat: ShadowStatusHeartbeat | None = None
    metrics: RuntimeMetricsRecorder | None = None
    try:
        if args.mavlink_endpoint and not args.frame:
            attitude_collector = MavlinkAttitudeCollector(
                lambda: MavlinkVisionBridge(args.mavlink_endpoint,
                    source_system=args.mavlink_source_system, source_component=args.mavlink_source_component),
                target_system=args.mavlink_target_system, target_component=args.mavlink_target_component,
                stale_after_ms=args.attitude_stale_after_ms)
            attitude_collector.start()
        if compatibility.compatible and runner is not None and not args.once and not args.frame and args.camera_backend != "none":
            capture_worker = LatestCaptureWorker(
                lambda: create_camera_ingress(args.camera_backend, width=args.camera_width,
                                              height=args.camera_height, timeout_ms=args.camera_timeout_ms),
                args.output_dir / "frames", interval_s=args.capture_interval_s)
            capture_worker.start()
        if not args.once:
            def camera_snapshot():
                if capture_worker is None:
                    return {"status": "not_sampled"}
                camera = capture_worker.status()
                return {"status": camera["state"], "capture_worker": camera}
            heartbeat = ShadowStatusHeartbeat(
                runtime_status_v3(compatibility=compatibility, map_release=map_release, model_release=model_release,
                                 camera_health="not_sampled", runtime_state="NO_GLOBAL_FIX", sequence=0),
                lambda value: _emit_heartbeat(args.output_dir, args.status_udp_target, value),
                camera_status=camera_snapshot,
                attitude_status=lambda: attitude_collector.status() if attitude_collector is not None else {"status": "not_configured"},
                metrics_status=lambda: metrics.status() if metrics is not None else {"status": "disabled"},
                interval_s=args.heartbeat_interval_s, inference_warning_ms=args.inference_warning_ms,
                max_frame_age_ms=args.max_frame_age_ms)
            heartbeat.start()
            if args.record_runtime_metrics:
                def metrics_activity():
                    return {**heartbeat.activity(), "camera": camera_snapshot(),
                            "attitude": attitude_collector.status() if attitude_collector is not None else {"status": "not_configured"}}
                metrics = RuntimeMetricsRecorder(args.output_dir, run_id=heartbeat.run_id, activity=metrics_activity,
                    interval_s=args.metrics_interval_s, max_samples=args.metrics_max_samples)
                metrics.start()
        while True:
            if heartbeat is not None:
                heartbeat.check()
                heartbeat.set_phase("waiting_for_frame", iteration=sequence)
            # A validated model release alone, or a frame-only inference probe, is never a global fix.
            state = "NO_GLOBAL_FIX"
            reason = "awaiting_learned_inference_decision" if compatibility.compatible else "release_or_provider_incompatible"
            camera_health = "not_sampled"
            probe: dict[str, Any] | None = None
            if compatibility.compatible and runner is not None:
                try:
                    if capture_worker is not None:
                        captured = capture_worker.take(timeout_s=args.camera_timeout_ms / 1000.0)
                    else:
                        captured, ingress = _sample_frame(args, sequence=sequence, ingress=ingress)
                    if captured is not None:
                        if heartbeat is not None:
                            heartbeat.set_phase("frame_preprocessing", iteration=sequence)
                        frame_path = captured.path
                        shadow_frame = load_shadow_frame(frame_path)
                        if calibration is not None:
                            validate_image_size(shadow_frame.bgr, calibration)
                        health_report = analyze_frame_file(frame_path)
                        camera_health = str(health_report["status"])
                        attitude_before = None
                        if temporal_window is not None and attitude_collector is not None:
                            batch = attitude_collector.drain()
                            attitude_before = batch["health"]
                            if (attitude_before["generation"] != attitude_generation
                                    or attitude_before["status"] != "receiving"):
                                temporal_window.reset_attitude_context("attitude_context_discontinuity")
                                candidate_bank.invalidate()
                            attitude_generation = attitude_before["generation"]
                            for attitude in batch["samples"]:
                                temporal_window.push_attitude(**attitude)
                        capture_timing = shadow_capture_timing(
                            captured, now_ns=time.monotonic_ns(), max_age_ms=args.max_frame_age_ms)
                        capture_ns = capture_timing["host_monotonic_ns"]
                        if capture_ns is None:
                            candidate_bank.invalidate()
                        if heartbeat is not None:
                            heartbeat.set_phase("inference", iteration=sequence, capture_ns=capture_ns)
                        probe = run_shadow_frame_probe(
                            runner,
                            model_release=model_release,
                            frame=shadow_frame,
                            candidate_lookup=candidate_lookup,
                            tile_feature_lookup=tile_feature_lookup,
                            temporal_window=temporal_window,
                            frame_monotonic_ns=capture_ns,
                            camera_matrix=calibration.camera_matrix if calibration is not None else None,
                            camera_image_size=calibration.image_size if calibration is not None else None,
                            candidate_bank=candidate_bank,
                            max_candidate_verifications=args.max_candidate_verifications,
                        )
                        result_timing = shadow_capture_timing(
                            captured, now_ns=time.monotonic_ns(), max_age_ms=args.max_frame_age_ms)
                        probe["capture_timing"] = capture_timing
                        probe["result_timing"] = result_timing
                        probe["capture_metadata"] = captured.to_dict()
                        if attitude_before is not None:
                            attitude_after = attitude_collector.status()
                            probe["attitude_context"] = {"before": attitude_before, "after": attitude_after}
                            if (attitude_before["status"] != "receiving" or attitude_after["status"] != "receiving"
                                    or attitude_after["generation"] != attitude_before["generation"]):
                                candidate_bank.invalidate()
                                temporal_window.reset_attitude_context("attitude_context_unavailable_or_changed")
                                for key in ("shadow_tracking_candidate", "temporal_student_evidence"):
                                    probe[key] = {"status": "withheld", "reason": "attitude_context_unavailable_or_changed",
                                                  "position_authority": False}
                                probe["decision_reason"] = "attitude_context_unavailable_or_changed"
                        if result_timing["status"] != "available":
                            candidate_bank.invalidate()
                            if temporal_window is not None:
                                temporal_window.invalidate("capture_timing_unavailable_or_result_stale")
                            probe["shadow_tracking_candidate"] = {
                                "status": "withheld", "reason": "capture_timing_unavailable_or_result_stale",
                                "position_authority": False}
                            probe["temporal_student_evidence"] = {
                                "status": "withheld", "reason": "capture_timing_unavailable_or_result_stale",
                                "position_authority": False}
                            probe["decision_reason"] = "capture_timing_unavailable_or_result_stale"
                        reason = str(probe["decision_reason"])
                        if retrieval_error:
                            probe["retrieval_index_error"] = retrieval_error
                        if local_feature_error:
                            probe["local_feature_cache_error"] = local_feature_error
                        if temporal_error:
                            probe["temporal_context_error"] = temporal_error
                except Exception as exc:
                    candidate_bank.invalidate()
                    if temporal_window is not None:
                        temporal_window.invalidate("shadow_frame_probe_failed")
                    reason = f"shadow_frame_probe_error:{type(exc).__name__}:{exc}"
                    camera_health = "waiting" if isinstance(exc, TimeoutError) else "failed"
            status = runtime_status_v3(
                compatibility=compatibility, map_release=map_release, model_release=model_release,
                camera_health=camera_health, runtime_state=state, sequence=sequence, reason=reason,
                latency_ms=(probe or {}).get("latency_ms"), inference_probe=probe,
            )
            if capture_worker is not None:
                status["camera_health"]["capture_worker"] = capture_worker.status()
            status["attitude_health"] = (attitude_collector.status() if attitude_collector is not None else
                                         {"status": "not_configured", "position_authority": False})
            if heartbeat is not None:
                heartbeat.check()
                heartbeat.set_phase("recording_evidence", iteration=sequence)
                status["runtime_run_id"] = heartbeat.run_id
                _write_trace(args.output_dir, status, snapshot=False)
                heartbeat.completed(status)
            else:
                _write_trace(args.output_dir, status)
                _send_udp(status, args.status_udp_target)
            print(json.dumps(status, sort_keys=True))
            if args.once:
                return
            sequence += 1
            time.sleep(max(0.1, args.interval_s))
    finally:
        if heartbeat is not None:
            heartbeat.set_phase("stopping", iteration=sequence)
        capture_stopped = capture_worker.stop() if capture_worker is not None else True
        attitude_stopped = attitude_collector.stop() if attitude_collector is not None else True
        metrics_stopped = metrics.stop() if metrics is not None else True
        try:
            if ingress is not None:
                ingress.close()
        finally:
            heartbeat_stopped = (heartbeat.stop(final_phase="stopped" if capture_stopped and attitude_stopped and metrics_stopped else "shutdown_incomplete")
                                 if heartbeat is not None else True)
        if not capture_stopped or not attitude_stopped or not heartbeat_stopped or not metrics_stopped:
            raise RuntimeError(f"Runtime worker shutdown incomplete: capture_stopped={capture_stopped}, attitude_stopped={attitude_stopped}, heartbeat_stopped={heartbeat_stopped}, metrics_stopped={metrics_stopped}")
        if heartbeat is not None:
            heartbeat.check()


def validate_main() -> None:
    parser = argparse.ArgumentParser(description="Validate learned Pi runtime releases without starting the service.")
    parser.add_argument("--map-release", required=True, type=Path)
    parser.add_argument("--model-release", required=True, type=Path)
    parser.add_argument("--calibration-id", required=True)
    parser.add_argument("--provider", default="onnxruntime_cpu")
    parser.add_argument("--model-artifact-root", type=Path, help="Defaults to the ModelRelease directory.")
    args = parser.parse_args()
    map_release, model_release, compatibility = _load_inputs(args)
    status = runtime_status_v3(
        compatibility=compatibility, map_release=map_release, model_release=model_release,
        camera_health="not_sampled", runtime_state="VALIDATED" if compatibility.compatible else "NO_GLOBAL_FIX",
        sequence=0, reason="validation_only",
    )
    print(json.dumps(status, indent=2, sort_keys=True))
    if not compatibility.compatible:
        raise SystemExit(2)


def replay_main() -> None:
    parser = argparse.ArgumentParser(description="Record deterministic learned-runtime release validation for a replay package.")
    parser.add_argument("--map-release", required=True, type=Path)
    parser.add_argument("--model-release", required=True, type=Path)
    parser.add_argument("--calibration-id", required=True)
    parser.add_argument("--provider", default="onnxruntime_cpu")
    parser.add_argument("--model-artifact-root", type=Path, help="Defaults to the ModelRelease directory.")
    parser.add_argument("--output-dir", required=True, type=Path)
    args = parser.parse_args()
    map_release, model_release, compatibility = _load_inputs(args)
    status = runtime_status_v3(
        compatibility=compatibility, map_release=map_release, model_release=model_release,
        camera_health="replay_not_sampled", runtime_state="NO_GLOBAL_FIX", sequence=0,
        reason="replay_requires_exported_learned_inference_artifacts",
    )
    _write_trace(args.output_dir, status)
    print(json.dumps(status, sort_keys=True))
