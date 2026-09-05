"""Run recorded camera windows against learned or explicit map candidates.

Optional learned local verification adds geometric evidence, not VIO or a live
navigation authority. No labels/GPS or MAVLink transport are used.
"""

from __future__ import annotations

import argparse
from collections import deque, OrderedDict
from contextlib import ExitStack, contextmanager
import hashlib
import json
from pathlib import Path
import time
from typing import Any

import cv2
import numpy as np

from vision_nav.camera import load_camera_calibration
from vision_nav.camera_ingress import frame_quality_metrics
from vision_nav.capture_timing import audit_capture_timing
from vision_nav.training_capture import sha256_file, validate_training_session
from .crossview_dataset import _camera_context
from .crossview_images import PREPROCESSING_ID, image_tensor
from .map_conditioned_export import MapConditionedOnnxProbe
from .map_pixel_geometry import tile_metric_axes
from .map_tile_layout import MAP_TILE_LAYOUT_SCHEMA
from .reference_map import load_reference_map
from .replay_retrieval import ReplayCandidateRetriever
from .teacher_dataset import pose_context


REPLAY_SCHEMA = "vision_nav_map_conditioned_capture_replay_v1"


def _member(root: Path, name: str) -> Path:
    path = (root / name).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise ValueError(f"Missing or escaped replay input: {name}")
    return path


class CameraWindow:
    """Bounded consecutive exposure window; any rejected frame breaks support."""

    def __init__(self, size: int, max_gap_ms: float):
        if type(size) is not int or size < 2 or not np.isfinite(max_gap_ms) or max_gap_ms <= 0:
            raise ValueError("Invalid replay window policy")
        self.size, self.max_gap_ns = size, int(max_gap_ms * 1e6)
        self.frames: deque[dict[str, Any]] = deque(maxlen=size)

    def clear(self) -> None:
        self.frames.clear()

    def push(self, frame: dict[str, Any]) -> str | None:
        reset = None
        if any(type(frame[key]) is not int or frame[key] < 0 for key in ("sequence", "sensor_ns", "host_ns")):
            self.clear()
            raise ValueError("Invalid camera sequence/timestamp")
        if self.frames:
            previous = self.frames[-1]
            if frame["sequence"] <= previous["sequence"]:
                self.clear()
                raise ValueError("Duplicate/reordered camera sequence")
            if frame["sequence"] != previous["sequence"] + 1:
                reset = "nonconsecutive_frame_sequence"
            elif any(not 0 < frame[key] - previous[key] <= self.max_gap_ns for key in ("sensor_ns", "host_ns")):
                reset = "frame_clock_gap_or_reordering"
            if reset:
                self.clear()
        self.frames.append(frame)
        return reset

    def inputs(self, candidate: np.ndarray, map_context: list[float], camera_context: list[float]) -> dict[str, np.ndarray]:
        if len(self.frames) != self.size:
            raise ValueError("Camera window is not ready")
        anchor_time = self.frames[-1]["sensor_ns"]
        return {
            "frame_window": np.stack([f["image"] for f in self.frames])[None],
            "candidate_map": candidate[None],
            "roll_pitch": np.asarray([f["roll_pitch"] for f in self.frames], np.float32)[None],
            "time_offsets_s": np.asarray([(f["sensor_ns"] - anchor_time) / 1e9 for f in self.frames], np.float32)[None],
            "camera_context": np.asarray(camera_context, np.float32)[None],
            "map_context": np.asarray(map_context, np.float32)[None],
        }


class MapWindowModels:
    """Shared release-bound learned stages for replay and owned live windows."""

    def __init__(self, export_dir, reference_map, tile_layout, calibration_path, *,
                 retrieval_export=None, retrieval_index=None, retrieval_top_k=4,
                 local_verification_config=None):
        self.calibration_path = Path(calibration_path).expanduser().resolve()
        self.calibration = load_camera_calibration(self.calibration_path)
        self.probe = MapConditionedOnnxProbe(export_dir)
        self.preprocessing = {"id": PREPROCESSING_ID,
            "source_sha256": sha256_file(Path(__file__).with_name("crossview_images.py"))}
        if self.probe.manifest.get("preprocessing") != self.preprocessing:
            raise ValueError("Export image preprocessing differs from this runtime; rebuild the export")
        if self.probe.manifest.get("calibration_sha256", {}).get(self.calibration.calibration_id) != sha256_file(self.calibration_path):
            raise ValueError("Export/calibration file checksum mismatch")
        self.reference_path = Path(reference_map).expanduser().resolve()
        self.reference = load_reference_map(self.reference_path)
        if self.calibration.calibration_id not in self.reference["calibration_ids"]:
            raise ValueError("Map/calibration identity mismatch")
        self.layout_path = Path(tile_layout).expanduser().resolve()
        self.layout = json.loads(self.layout_path.read_text(encoding="utf-8"))
        if (self.layout.get("schema_version") != MAP_TILE_LAYOUT_SCHEMA
                or self.layout.get("geodetic_origin_wgs84") != self.reference["geodetic_origin_wgs84"]
                or self.layout.get("cog", {}).get("sha256") != self.reference["checksums_sha256"][self.reference["imagery_uri"]]):
            raise ValueError("Tile layout does not match the selected reference map")
        self.tiles = {tile["tile_id"]: tile for tile in self.layout["tiles"]}
        if len(self.tiles) != len(self.layout["tiles"]) or self.layout.get("tile_count") != len(self.tiles):
            raise ValueError("Duplicate/mismatched tile index")
        if (retrieval_export is None) != (retrieval_index is None):
            raise ValueError("Retrieval requires both export and index")
        self.retriever = ReplayCandidateRetriever(retrieval_export, retrieval_index, self.reference_path,
            self.layout_path, top_k=retrieval_top_k) if retrieval_export is not None else None
        self.local_verifier = None
        if local_verification_config is not None:
            from .replay_local_geometry import ReplayLocalVerifier
            self.local_verifier = ReplayLocalVerifier(local_verification_config, self.layout_path, self.calibration)
        self.candidate_cache = OrderedDict()

    def candidate_for(self, tile_id):
        if tile_id in self.candidate_cache:
            self.candidate_cache.move_to_end(tile_id)
            return self.candidate_cache[tile_id]
        if tile_id not in self.tiles:
            raise ValueError(f"Candidate absent from selected map: {tile_id}")
        tile = self.tiles[tile_id]
        path = _member(self.layout_path.parent, tile["image_uri"])
        if sha256_file(path) != tile.get("image_sha256"):
            raise ValueError("Candidate map image checksum mismatch")
        geometry = tile["pixel_geometry"]
        image = cv2.imread(str(path))
        if (image is None or list(image.shape[1::-1]) != geometry.get("image_size")
                or geometry.get("geodetic_origin_wgs84") != self.reference["geodetic_origin_wgs84"]):
            raise ValueError("Candidate crop geometry/dimensions mismatch")
        if len(self.candidate_cache) >= 4:
            self.candidate_cache.popitem(last=False)
        candidate = {"tile_id": tile_id, "image": image_tensor(path, self.probe.config.image_size),
                     "map_context": tile_metric_axes(geometry), "image_sha256": tile["image_sha256"]}
        self.candidate_cache[tile_id] = candidate
        return candidate


def replay_map_conditioned_capture(
    session_dir: str | Path, export_dir: str | Path, reference_map: str | Path,
    tile_layout: str | Path, calibration_path: str | Path, output_dir: str | Path,
    *, candidate_tile_ids: list[str] | None = None, max_frame_gap_ms: float = 1000.0,
    retrieval_export: str | Path | None = None, retrieval_index: str | Path | None = None,
    retrieval_top_k: int = 4,
    local_verification_config: str | Path | None = None,
    min_sharpness: float = 20.0, max_saturated_fraction: float = 0.65,
    native_vio_binary: str | Path | None = None, native_vio_capture: str | Path | None = None,
    playback_speed: float = 1.0, metrics_interval_s: float = 1.0, metrics_max_samples: int = 36000,
) -> dict[str, Any]:
    if (native_vio_binary is None) != (native_vio_capture is None):
        raise ValueError("Concurrent replay requires both native binary and converted capture")
    automatic = retrieval_export is not None or retrieval_index is not None
    if automatic and (retrieval_export is None or retrieval_index is None or candidate_tile_ids is not None):
        raise ValueError("Automatic retrieval needs both export and index, without explicit candidates")
    if not automatic and (not candidate_tile_ids or len(candidate_tile_ids) > 4
                          or len(set(candidate_tile_ids)) != len(candidate_tile_ids)):
        raise ValueError("Select one to four distinct map candidate IDs")
    if not np.isfinite([min_sharpness, max_saturated_fraction]).all() or min_sharpness < 0 or not 0 <= max_saturated_fraction <= 1:
        raise ValueError("Invalid image-quality policy")
    root = Path(session_dir).expanduser().resolve()
    output = Path(output_dir).expanduser().resolve()
    if output == root or output.is_relative_to(root):
        raise ValueError("Replay output must be outside the immutable capture")
    if output.exists() and any(output.iterdir()):
        raise ValueError("Replay output must be empty")
    validation = validate_training_session(root, verify_checksums=True, require_sensor_timestamps=True)
    if validation["status"] != "valid":
        raise ValueError("Invalid capture: " + "; ".join(validation["errors"]))
    capture = validation["manifest"]
    telemetry_path = _member(root, capture["telemetry_path"])
    telemetry = [json.loads(line) for line in telemetry_path.read_text(encoding="utf-8").splitlines() if line.strip()]
    timing = audit_capture_timing(capture["frames"], telemetry, capture.get("mavlink", {}).get("timesync", {}))
    calibration_path = Path(calibration_path).expanduser().resolve()
    calibration = load_camera_calibration(calibration_path)
    calibration_id = calibration.calibration_id
    if capture["calibration"]["calibration_id"] != calibration_id:
        raise ValueError("Capture calibration differs from selected calibration")
    context = pose_context({"camera_to_body": capture.get("camera_to_body")})
    if not context["camera_extrinsics_valid"]:
        raise ValueError("Replay requires measured fixed camera-to-body extrinsics")
    camera_context = _camera_context(calibration, [{
        "camera_rotation_camera_optical_to_body_frd_xyzw": context["camera_rotation_camera_optical_to_body_frd_xyzw"].tolist(),
        "camera_translation_body_frd_m": context["camera_translation_body_frd_m"].tolist()}])
    models = MapWindowModels(export_dir, reference_map, tile_layout, calibration_path,
        retrieval_export=retrieval_export, retrieval_index=retrieval_index, retrieval_top_k=retrieval_top_k,
        local_verification_config=local_verification_config)
    probe, expected_preprocessing = models.probe, models.preprocessing
    window = CameraWindow(probe.config.window_size, max_frame_gap_ms)
    reference_path, reference = models.reference_path, models.reference
    if capture["map_release_id"] != reference["release_id"]:
        raise ValueError("Capture/map/calibration identity mismatch")
    layout_path = models.layout_path
    retriever, local_verifier = models.retriever, models.local_verifier
    candidate_cache, candidate_for = models.candidate_cache, models.candidate_for

    if not automatic:
        for tile_id in candidate_tile_ids:
            candidate_for(tile_id)

    paired = metrics = None
    if native_vio_binary is not None:
        from vision_nav.vio.paired_replay import PairedSensorReplay
        from .runtime_metrics import RuntimeMetricsRecorder
        paired = PairedSensorReplay(native_vio_binary, native_vio_capture, output / "native-vio",
            session=root, manifest=capture, calibration=calibration, camera_context=camera_context,
            window_size=window.size, playback_speed=playback_speed)
        metrics = RuntimeMetricsRecorder(output, run_id=paired.worker.worker_run_id,
            activity=lambda: {"phase": "paired_learned_replay", "vio": paired.worker.status()},
            native_vio_probe=paired.worker.resource_snapshot, interval_s=metrics_interval_s,
            max_samples=metrics_max_samples)

    output.mkdir(parents=True, exist_ok=True)
    (output / "timing-audit.json").write_text(json.dumps(timing, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    provenance = {"session_id": capture["session_id"], "capture_manifest_sha256": sha256_file(root / "manifest.json"),
        "telemetry_sha256": sha256_file(telemetry_path), "reference_map_sha256": sha256_file(reference_path),
        "tile_layout_sha256": sha256_file(layout_path), "calibration_sha256": sha256_file(calibration_path),
        "export_manifest_sha256": sha256_file(Path(export_dir).expanduser().resolve() / "manifest.json"),
        "model_sha256": probe.manifest["onnx"]["sha256"], "preprocessing": expected_preprocessing,
        "candidate_selection": "learned_image_retrieval" if automatic else "explicit_operator_candidates_not_global_retrieval",
        "retrieval": retriever.provenance if retriever else None,
        "local_verification": local_verifier.provenance if local_verifier else None,
        "candidate_tiles": [{"tile_id": c["tile_id"], "image_sha256": c["image_sha256"]} for c in candidate_cache.values()],
        "training_timing_policies": probe.manifest.get("training_timing_policies", []),
        "policy": {"window_size": window.size, "max_frame_gap_ms": max_frame_gap_ms,
                   "min_sharpness": min_sharpness, "max_saturated_fraction": max_saturated_fraction,
                   "candidate_image_cache_limit": 4},
        "gps_used_for_candidate_selection_or_model_inputs": False}
    counts = {"frames": 0, "warmup": 0, "rejected": 0, "model_windows": 0, "candidate_calls": 0,
              "window_resets": 0, "retrieval_calls": 0}
    decision_hash = hashlib.sha256()
    latencies = []
    stage_samples = {}
    retention_counts = {"all": {}, "native_not_terminal": {}}

    def observe(index):
        frame, alignment = capture["frames"][index], timing["frames"][index]
        if alignment["status"] != "paired":
            raise ValueError(alignment.get("reason", "exposure_alignment_rejected"))
        path = _member(root, frame["path"])
        if paired:
            paired.verify_frame(index, path)
        quality = frame_quality_metrics(path)
        if (quality.get("status") != "measured" or quality.get("laplacian_variance", 0) < min_sharpness
                or quality.get("saturated_fraction", 1) > max_saturated_fraction):
            raise ValueError("frame_quality_rejected")
        image = image_tensor(path, probe.config.image_size, calibration=calibration)
        if paired:
            paired.verify_frame(index, path)
        reset = window.push({"image": image, "sequence": frame["sequence"],
            "sensor_ns": alignment["camera"]["sensor_timestamp_ns"], "host_ns": alignment["camera"]["host_monotonic_ns"],
            "roll_pitch": alignment["attitude_rpy_rad"][:2]})
        if reset:
            counts["window_resets"] += 1
        return path, quality, reset

    with ExitStack() as resources, (output / "trace.jsonl").open("w", encoding="utf-8") as stream:
        if paired:
            resources.callback(metrics.stop)
            resources.callback(paired.stop)
            paired.start()
            metrics.start()
        work = paired.windows() if paired else ({"indices": [i]} for i in range(len(capture["frames"])))
        for item in work:
            index = item["indices"][-1]
            frame, alignment = capture["frames"][index], timing["frames"][index]
            counts["frames"] += 1
            row: dict[str, Any] = {"sequence": frame["sequence"], "state": "REJECTED", "position_authority": False,
                                  "navigation_mode": "NO_GLOBAL_FIX", "px4_external_vision": "disabled"}
            start = time.perf_counter()
            stages = {}

            @contextmanager
            def measured(stage):
                began = time.perf_counter()
                try:
                    yield
                finally:
                    stages[stage] = stages.get(stage, 0.) + (time.perf_counter() - began) * 1000

            if paired:
                window.clear()
                row["concurrent_replay"] = {**item, "started_host_monotonic_ns": time.monotonic_ns(),
                    "worker_run_id": paired.worker.worker_run_id, "native_before": paired.worker.status()}
            try:
                with measured("window_read_quality_rectification"):
                    for context_index in item["indices"][:-1]:
                        observe(context_index)
                    path, quality, reset = observe(index)
                row["quality"] = quality
                row.update(frame_sha256=sha256_file(path), window_reset_reason=reset, window_frame_count=len(window.frames),
                           exposure_host_ns=alignment["camera"]["host_monotonic_ns"],
                           timing_envelope_ms=alignment["total_timing_envelope_ms"])
                if len(window.frames) < window.size:
                    row.update(state="WINDOW_WARMUP", reason="insufficient_consecutive_exposures")
                    counts["warmup"] += 1
                else:
                    if retriever:
                        counts["retrieval_calls"] += 1
                        # Retrieve from this exposure, never from GPS or a previous
                        # window after a provider failure. Scores are not fixes.
                        row["retrieval"] = {"state": "running", "query_frame_sha256": row["frame_sha256"]}
                        with measured("retrieval"):
                            selected = retriever.search(cv2.imread(str(path)))
                        row["retrieval"].update(state="candidates", candidates=selected)
                        selected_ids = [c["tile_id"] for c in selected]
                    else:
                        selected_ids = candidate_tile_ids
                    evidence = []
                    for tile_id in selected_ids:
                        with measured("candidate_cache_and_inputs"):
                            candidate = candidate_for(tile_id)
                            inputs = window.inputs(candidate["image"], candidate["map_context"], camera_context)
                        counts["candidate_calls"] += 1
                        with measured("map_conditioned_student"):
                            evidence.append({**probe.run(inputs, tile_id=candidate["tile_id"],
                                                      map_release_id=reference["release_id"], calibration_id=calibration_id),
                                             "map_image_sha256": candidate["image_sha256"]})
                    row.update(state="MODEL_EVIDENCE", reason="uncalibrated_model_only_no_geometric_pose", candidates=evidence)
                    if local_verifier:
                        with measured("local_correspondence_and_geometry"):
                            row["local_verification"] = local_verifier.verify(path, selected_ids)
                        row["reason"] = "learned_and_geometric_shadow_evidence_not_navigation"
                        if not row["local_verification"]["complete"]:
                            window.clear()
                            counts["window_resets"] += 1
                    counts["model_windows"] += 1
                if paired:
                    for context_index in item["indices"]:
                        paired.verify_frame(context_index, _member(root, capture["frames"][context_index]["path"]))
            except (ValueError, RuntimeError, OSError) as exc:
                window.clear()
                counts["rejected"] += 1
                counts["window_resets"] += 1
                row.update(state="REJECTED", reason=str(exc))
                if row.get("retrieval", {}).get("state") == "running":
                    row["retrieval"].update(state="rejected", reason=str(exc))
            if paired:
                row["concurrent_replay"].update(completed_host_monotonic_ns=time.monotonic_ns(),
                    native_after=paired.worker.status())
                retention = paired.worker.clone_retention_snapshot(item["anchor_vio_timestamp_ns"])
                row["concurrent_replay"]["clone_retention"] = retention
                key = retention["status"] if retention["reason"] is None else retention["reason"]
                retention_counts["all"][key] = retention_counts["all"].get(key, 0) + 1
                if retention["native_worker_state"] in {"starting", "waiting_for_sensors", "processing"}:
                    active = retention_counts["native_not_terminal"]
                    active[key] = active.get(key, 0) + 1
            decision_hash.update((json.dumps(row, sort_keys=True, allow_nan=False) + "\n").encode())
            latency_ms = (time.perf_counter() - start) * 1000
            latencies.append(latency_ms)
            row["processing_latency_ms"] = latency_ms
            row["stage_latency_ms"] = stages
            for stage, value in stages.items():
                stage_samples.setdefault(stage, []).append(value)
            stream.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
            stream.flush()
    summary = {"schema_version": REPLAY_SCHEMA, "status": "replay_completed", "provenance": provenance, "counts": counts,
        "decision_sha256_excluding_latency": decision_hash.hexdigest(), "trace_sha256": sha256_file(output / "trace.jsonl"),
        "timing_audit_sha256": sha256_file(output / "timing-audit.json"),
        "processing_latency_ms": {str(p): float(np.percentile(latencies, p)) for p in (50, 95, 99)} if latencies else {},
        "stage_latency_ms": {stage: {"count": len(values), **{str(p): float(np.percentile(values, p))
                                for p in (50, 95, 99)}} for stage, values in stage_samples.items()},
        "position_authority": False, "navigation_mode": "NO_GLOBAL_FIX", "px4_external_vision": "disabled",
        "limitations": (["retrieval_recall_not_validated"] if automatic else ["operator_candidates_not_measured_retrieval"]) +
                       (["local_matcher_not_pi_validated", "no_continuous_vio_or_pose_covariance"] if local_verifier
                        else ["no_metric_geometry_or_vio"]) + ["uncalibrated_probabilities",
                        "recorded_capture_not_live_scheduler", "sensor_delay_not_calibrated", "not_a_pi_thermal_benchmark"]}
    if paired:
        summary["concurrent_replay"] = paired.status()
        summary["runtime_metrics"] = metrics.status()
        summary["decision_hash_scope"] = "includes_nondeterministic_replay_scheduling_and_native_progress"
        summary["limitations"] = [value for value in summary["limitations"]
            if value not in {"no_continuous_vio_or_pose_covariance", "no_metric_geometry_or_vio"}]
        summary["limitations"].extend(["local_vio_not_fused_with_global_evidence", "no_navigation_covariance"])
        if not local_verifier:
            summary["limitations"].append("no_metric_geometry")
        summary["source_frame_count"] = len(capture["frames"])
        summary["clone_retention_counts"] = retention_counts
        summary["counts_scope"] = "selected_window_anchors_not_all_source_frames"
        if (not summary["concurrent_replay"]["complete"] or summary["runtime_metrics"]["status"] != "stopped"
                or summary["runtime_metrics"]["native_vio_available_sample_count"] < 2):
            summary["status"] = "concurrent_evidence_incomplete"
    (output / "summary.json").write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def main() -> None:
    parser = argparse.ArgumentParser(description="Replay captured camera frames through the map-conditioned ONNX model without flight output")
    for name in ("session", "export-dir", "reference-map", "tile-layout", "calibration", "output-dir"):
        parser.add_argument("--" + name, required=True, type=Path)
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--candidate-tile", action="append")
    selection.add_argument("--retrieval-export", type=Path)
    parser.add_argument("--retrieval-index", type=Path)
    parser.add_argument("--retrieval-top-k", type=int, default=4)
    parser.add_argument("--local-verification-config", type=Path, help="Optional research-only XFeat/LighterGlue geometry config")
    parser.add_argument("--max-frame-gap-ms", type=float, default=1000)
    parser.add_argument("--native-vio-binary", type=Path)
    parser.add_argument("--native-vio-capture", type=Path, help="Sealed output from vision-nav-prepare-vio-capture for this session")
    parser.add_argument("--playback-speed", type=float, default=1.0)
    parser.add_argument("--metrics-interval-s", type=float, default=1.0)
    parser.add_argument("--metrics-max-samples", type=int, default=36000)
    args = parser.parse_args()
    try:
        report = replay_map_conditioned_capture(args.session, args.export_dir, args.reference_map, args.tile_layout,
            args.calibration, args.output_dir, candidate_tile_ids=args.candidate_tile, max_frame_gap_ms=args.max_frame_gap_ms,
            retrieval_export=args.retrieval_export, retrieval_index=args.retrieval_index, retrieval_top_k=args.retrieval_top_k,
            local_verification_config=args.local_verification_config, native_vio_binary=args.native_vio_binary,
            native_vio_capture=args.native_vio_capture, playback_speed=args.playback_speed,
            metrics_interval_s=args.metrics_interval_s, metrics_max_samples=args.metrics_max_samples)
    except (ValueError, RuntimeError, OSError) as exc:
        print(json.dumps({"status": "rejected", "reason": str(exc), "px4_external_vision": "disabled"}))
        raise SystemExit(2) from exc
    print(json.dumps(report, indent=2))
    if not report["counts"]["model_windows"] or report["status"] != "replay_completed":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
