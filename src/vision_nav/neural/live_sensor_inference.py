"""Finite prop-off learned inference sharing the native VIO sensor owner.

This runs real ONNX stages, not a learned-pose publication adapter. Native local
tracking and selected learned windows progress independently; global fusion and
aircraft transport remain disconnected.
"""

import argparse
from contextlib import contextmanager
import hashlib
import json
from pathlib import Path
import time

import cv2
import numpy as np

from ..camera_ingress import image_quality_metrics
from ..vio.export_training_capture import _calibration_binding
from ..vio.live_worker import LiveFrameTicket
from ..vio.openvins_replay import digest
from ..vio.prepare_capture import load_sensor_profile
from ..vio.sensor_session import LiveSensorSession
from .crossview_dataset import _camera_context
from .crossview_images import pixels_tensor
from .map_conditioned_replay import CameraWindow, MapWindowModels
from .teacher_dataset import pose_context
from .releases import _canonical_digest


SCHEMA = "vision_nav_live_learned_sensor_session_v1"
TERMINAL = {"complete", "completed_without_local_estimate", "failed", "stopped", "shutdown_incomplete"}
SUCCESS = {"complete", "completed_without_local_estimate"}


def inference_files(export_dir, reference_map, tile_layout, calibration, camera_to_body,
                    retrieval_export, retrieval_index, local_verification_config=None):
    files = [Path(calibration), Path(camera_to_body), Path(reference_map), Path(tile_layout),
             Path(export_dir) / "manifest.json", Path(retrieval_export) / "manifest.json",
             Path(retrieval_index) / "manifest.json", Path(__file__),
             Path(__file__).with_name("__init__.py"),
             Path(__file__).with_name("crossview_images.py"), Path(__file__).with_name("map_conditioned_replay.py")]
    if local_verification_config:
        files.append(Path(local_verification_config))
        files.extend([Path(__file__).with_name("replay_local_geometry.py"),
                      Path(__file__).with_name("candidate_groups.py"),
                      Path(__file__).with_name("map_landmarks.py"),
                      Path(__file__).with_name("bounded_pnp.py"),
                      Path(__file__).with_name("shadow_geometry.py")])
    return {str(p.resolve()): digest(p) for p in files}


class WindowModelInference:
    """Model-only consumer: immutable sensor identity, no live device handles."""

    def __init__(self, sensor_profile, worker_run_id, window_size,
                 export_dir, reference_map, tile_layout, calibration, camera_to_body, *,
                 retrieval_export, retrieval_index, retrieval_top_k=4, local_verification_config=None,
                 min_sharpness=20., max_saturated_fraction=.65):
        if (not np.isfinite([min_sharpness, max_saturated_fraction]).all()
                or min_sharpness < 0 or not 0 <= max_saturated_fraction <= 1):
            raise ValueError("Invalid live image quality policy")
        self.fixed_hashes = inference_files(export_dir, reference_map, tile_layout, calibration,
            camera_to_body, retrieval_export, retrieval_index, local_verification_config)
        sensor_profile = Path(sensor_profile).resolve()
        self.profile, _, _ = load_sensor_profile(sensor_profile)
        self.fixed_hashes[str(sensor_profile / "sensor_profile.json")] = digest(sensor_profile / "sensor_profile.json")
        self.fixed_hashes.update({str(sensor_profile / p): value for p, value in self.profile["checksums"].items()})
        self.worker_run_id = worker_run_id
        camera, extrinsics = _calibration_binding(sensor_profile, calibration, camera_to_body)
        # Existing learned rectification uses OpenCV radtan, not fisheye.
        if camera.distortion_model not in {"plumb_bob", "radtan"}:
            raise ValueError("Live learned rectification currently requires radtan camera calibration")
        self.models = MapWindowModels(export_dir, reference_map, tile_layout, calibration,
            retrieval_export=retrieval_export, retrieval_index=retrieval_index, retrieval_top_k=retrieval_top_k,
            local_verification_config=local_verification_config)
        if self.models.retriever is None:
            raise ValueError("Live learned windows require image-based retrieval")
        if self.models.probe.manifest.get("training_timing_policies") != ["exposure_native_attitude_v1"]:
            raise ValueError("Live learned model must use exposure/native-attitude training context")
        if window_size != self.models.probe.config.window_size:
            raise ValueError("Sensor window length differs from exported student")
        context = pose_context({"camera_to_body": extrinsics.to_log_dict()})
        if not context["camera_extrinsics_valid"]:
            raise ValueError("Measured camera extrinsics are required for learned context")
        self.camera_context = _camera_context(camera, [{
            "camera_rotation_camera_optical_to_body_frd_xyzw": context["camera_rotation_camera_optical_to_body_frd_xyzw"].tolist(),
            "camera_translation_body_frd_m": context["camera_translation_body_frd_m"].tolist()}])
        self.window = CameraWindow(self.models.probe.config.window_size, 250.)
        self.min_sharpness, self.max_saturated = min_sharpness, max_saturated_fraction
        self.last_sequence = -1
        self.verify_sources()
        self.provenance = {"files_sha256": self.fixed_hashes,
            "map_release_id": self.models.reference["release_id"], "calibration_id": camera.calibration_id,
            "model_sha256": self.models.probe.manifest["onnx"]["sha256"],
            "preprocessing": self.models.preprocessing, "retrieval": self.models.retriever.provenance,
            "local_verification": self.models.local_verifier.provenance if self.models.local_verifier else None,
            "worker_run_id": worker_run_id, "image_source": "immutable_native_frame_ticket",
            "attitude_policy": "nearest_exposure_native_sample_no_receipt_time_pairing",
            "gps_used_for_candidate_selection_or_model_inputs": False,
            "min_sharpness": min_sharpness, "max_saturated_fraction": max_saturated_fraction}

    def verify_sources(self):
        for name, expected in self.fixed_hashes.items():
            if digest(name) != expected:
                raise ValueError("Live learned source/release changed: "+name)

    def _observe(self, ticket, context):
        if not isinstance(ticket, LiveFrameTicket) or ticket.worker_run_id != self.worker_run_id:
            raise ValueError("Learned frame belongs to a different native epoch")
        if (context.get("sequence") != ticket.sequence or context.get("worker_run_id") != ticket.worker_run_id
                or context.get("pixel_sha256") != ticket.pixel_sha256
                or context.get("conversion", {}).get("px4_timestamp_ns") != ticket.timestamp_ns
                or context.get("calibration_id") != self.models.calibration.calibration_id):
            raise ValueError("Learned context does not belong to its native frame")
        if context.get("status") != "paired":
            raise ValueError(context.get("reason") or "exposure_attitude_unavailable")
        if context.get("policy") != "exposure_native_attitude_v1":
            raise ValueError("Learned context has unsupported timing policy")
        angles, envelope = context.get("attitude_rpy_rad"), context.get("total_timing_envelope_ms")
        profile = self.profile
        if (not isinstance(angles, list) or len(angles) != 3
                or not all(type(v) in (int, float) and np.isfinite(v) for v in angles)
                or type(envelope) not in (int, float) or not np.isfinite(envelope) or not 0 <= envelope <= 25.
                or (context.get("source_system"), context.get("source_component")) !=
                   (profile["source_system"], profile["source_component"])):
            raise ValueError("Learned attitude source or timing envelope is invalid")
        if (ticket.pixels.flags.writeable
                or hashlib.sha256(ticket.pixels.tobytes()).hexdigest() != ticket.pixel_sha256):
            raise ValueError("Learned frame pixels changed after native submission")
        pixels = ticket.pixels
        if pixels.ndim == 2:
            pixels = cv2.cvtColor(pixels, cv2.COLOR_GRAY2BGR)
        quality = image_quality_metrics(pixels)
        if (quality["laplacian_variance"] < self.min_sharpness
                or quality["saturated_fraction"] > self.max_saturated):
            raise ValueError("frame_quality_rejected")
        reset = self.window.push({"sequence": ticket.sequence,
            "sensor_ns": context["camera"]["sensor_timestamp_ns"],
            "host_ns": context["camera"]["host_monotonic_ns"],
            "roll_pitch": context["attitude_rpy_rad"][:2],
            "image": pixels_tensor(pixels, self.models.probe.config.image_size, calibration=self.models.calibration)})
        if reset:
            raise ValueError("Live learned window is not consecutive: "+reset)
        return pixels, quality

    def process(self, item):
        row = {"schema_version": SCHEMA, "state": "REJECTED", "navigation_mode": "NO_GLOBAL_FIX",
            "position_authority": False, "px4_external_vision": "disabled", "global_fusion_connected": False,
            "worker_run_id": self.worker_run_id,
            "provenance_sha256": _canonical_digest(self.provenance),
            "started_host_monotonic_ns": time.monotonic_ns(), "stage_latency_ms": {}}
        started = time.perf_counter()
        self.window.clear()

        @contextmanager
        def measured(name):
            began = time.perf_counter()
            try:
                yield
            finally:
                row["stage_latency_ms"][name] = (time.perf_counter()-began)*1000

        try:
            self.verify_sources()
            tickets, contexts = item["tickets"], item["contexts"]
            if (item["worker_run_id"] != self.worker_run_id
                    or len(tickets) != self.window.size or len(contexts) != len(tickets)):
                raise ValueError("Live learned window epoch or length mismatch")
            anchor = tickets[-1]
            row.update(sequence=anchor.sequence, anchor_vio_timestamp_ns=anchor.timestamp_ns,
                anchor_pixel_sha256=anchor.pixel_sha256, anchor_png_sha256=anchor.image_sha256,
                window_frame_identities=[{"sequence": t.sequence, "timestamp_ns": t.timestamp_ns,
                    "pixel_sha256": t.pixel_sha256} for t in tickets], exposure_contexts=contexts)
            if anchor.sequence <= self.last_sequence:
                raise ValueError("Duplicate or reordered learned window")
            self.last_sequence = anchor.sequence
            with measured("window_quality_rectification"):
                for ticket, context in zip(tickets, contexts):
                    pixels, quality = self._observe(ticket, context)
            row["quality"] = quality
            with measured("retrieval"):
                candidates = self.models.retriever.search(pixels)
            row["retrieval"] = candidates
            evidence = []
            with measured("map_conditioned_student"):
                for selected in candidates:
                    candidate = self.models.candidate_for(selected["tile_id"])
                    inputs = self.window.inputs(candidate["image"], candidate["map_context"], self.camera_context)
                    evidence.append({**self.models.probe.run(inputs, tile_id=candidate["tile_id"],
                        map_release_id=self.models.reference["release_id"], calibration_id=self.models.calibration.calibration_id),
                        "map_image_sha256": candidate["image_sha256"]})
            row.update(candidates=evidence, reason="uncalibrated_model_only_no_geometric_pose")
            if self.models.local_verifier:
                with measured("local_correspondence_and_geometry"):
                    row["local_verification"] = self.models.local_verifier.verify_pixels(pixels, [c["tile_id"] for c in candidates])
                row["reason"] = "learned_and_geometric_shadow_evidence_not_navigation"
            self.verify_sources()
            row["state"] = "MODEL_EVIDENCE"
        except (ValueError, RuntimeError, OSError, KeyError, TypeError, cv2.error) as exc:
            row.update(state="REJECTED", reason=str(exc))
        finally:
            self.window.clear()
        row.update(completed_host_monotonic_ns=time.monotonic_ns(),
            processing_latency_ms=(time.perf_counter()-started)*1000)
        return row


def attach_native_progress(session, row, before):
    """Only the resource owner can attest current native state, never a model."""
    row.update(native_before=before, native_after=session.native.status())
    if session.status()["state"] in TERMINAL-SUCCESS:
        row.update(state="REJECTED", reason="Sensor session failed during inference")
    if "anchor_vio_timestamp_ns" in row:
        row["clone_retention"] = session.native.clone_retention_snapshot(row["anchor_vio_timestamp_ns"])
    return row


class LiveWindowInference(WindowModelInference):
    """In-process reference implementation for parity and fault tests."""

    def __init__(self, session, *args, **kwargs):
        if session.status()["state"] != "not_started":
            raise ValueError("Bind learned releases before starting sensor acquisition")
        self.session = session
        super().__init__(session.native.capture / "sensor_profile", session.native.worker_run_id,
                         session._window_size, *args, **kwargs)

    def process(self, item):
        before = self.session.native.status()
        return attach_native_progress(self.session, super().process(item), before)


def run_live_learned_session(session, inference, output, *, alignment_observer=None):
    """Own a finite session lifecycle; never start a second sensor reader."""
    if inference.session is not session:
        raise ValueError("Inference and native session identity differ")
    if alignment_observer is not None and alignment_observer.session is not session:
        raise ValueError("Alignment observer and native session identity differ")
    output = Path(output).resolve()
    if output.exists() or output.is_relative_to(session.root) or session.root.is_relative_to(output):
        raise ValueError("Learned evidence output must be new and separate from sensor evidence")
    inference.verify_sources()
    output.mkdir(parents=True)
    count, errors, latencies, alignment_latencies = 0, 0, [], []
    anchor_skipped = 0
    failure = None
    try:
        with (output / "trace.jsonl").open("x") as stream:
            if callable(getattr(inference, "start", None)):
                inference.start()
            if alignment_observer is not None:
                alignment_observer.start(inference.provenance,
                    evidence_path=output / "alignment-trace.jsonl" if alignment_observer.refresh_interval is not None else None)
            session.start()
            while True:
                item = session.take_latest_window()
                admission = alignment_observer.begin_inference(item) if item is not None and alignment_observer is not None else None
                if admission is not None and admission["status"] == "withheld":
                    anchor_skipped += 1
                    item = None
                if item is not None:
                    row = inference.process(item)
                    if admission is not None and admission["status"] == "dispatched":
                        row["query_anchor_admission"] = admission
                    if alignment_observer is not None:
                        began = time.perf_counter()
                        row["research_alignment"] = alignment_observer.observe(item, row)
                        row["research_alignment_latency_ms"] = (time.perf_counter()-began)*1000
                        row["research_alignment_completed_host_monotonic_ns"] = time.monotonic_ns()
                        alignment_latencies.append(row["research_alignment_latency_ms"])
                    count += 1
                    errors += row["state"] == "REJECTED"
                    latencies.append(row["processing_latency_ms"])
                    line = json.dumps(row, sort_keys=True, allow_nan=False)+"\n"
                    if len(line.encode()) > 4*1024*1024 or stream.tell()+len(line.encode()) > 256*1024*1024:
                        raise ValueError("Learned trace exceeds bounded evidence budget")
                    stream.write(line)
                    stream.flush()
                if session.join(.01) and not session.status()["pending_learned_window"]:
                    break
            if alignment_observer is not None and not alignment_observer.finish():
                raise RuntimeError("Continuous alignment did not finish its pending work")
    except (Exception, KeyboardInterrupt) as exc:
        failure = f"{type(exc).__name__}:{exc}"
    finally:
        alignment_closed = alignment_observer.stop() if alignment_observer is not None else True
        closed = session.stop()
        closed = closed and alignment_closed
        if callable(getattr(inference, "stop", None)):
            closed = inference.stop() and closed
    try:
        inference.verify_sources()
    except (ValueError, OSError) as exc:
        failure = failure or f"final_release_verification_failed:{exc}"
    status = session.status()
    worker = inference.status() if callable(getattr(inference, "status", None)) else None
    if worker and worker["error"]:
        failure = failure or worker["error"]
    alignment_status = alignment_observer.status() if alignment_observer is not None else None
    if alignment_status and alignment_status["error"]:
        failure = failure or alignment_status["error"]
    result = {"schema_version": SCHEMA, "status": "completed" if closed and status["state"] in SUCCESS and not failure else "incomplete",
        "error": failure, "source_session": status, "provenance": inference.provenance,
        "selected_windows": count, "model_windows": count-errors, "rejected_windows": errors,
        "position_authority": False, "navigation_mode": "NO_GLOBAL_FIX", "px4_external_vision": "disabled",
        "global_fusion_connected": False, "inference_worker": worker,
        "research_alignment": alignment_status,
        "research_alignment_connected": alignment_observer is not None,
        "research_alignment_latency_scope": "submission_only_background_work_in_alignment_trace" if alignment_observer is not None
            and alignment_observer.refresh_interval is not None else "synchronous_observation",
        "research_alignment_latency_ms": {str(p): float(np.percentile(alignment_latencies, p)) for p in (50, 95, 99)} if alignment_latencies else {},
        "trace_sha256": digest(output / "trace.jsonl"),
        "alignment_trace_sha256": digest(output / "alignment-trace.jsonl") if (output / "alignment-trace.jsonl").is_file() else None,
        "processing_latency_ms": {str(p): float(np.percentile(latencies, p)) for p in (50, 95, 99)} if latencies else {},
        "limitations": ["no_global_fusion_or_publication", "uncalibrated_model_evidence",
            "finite_prop_off_research_not_pi_service",
            "hardware_timing_accuracy_and_thermal_load_unqualified"]}
    if worker is None:
        result["limitations"].append("in_process_reference_has_no_provider_hard_timeout")
    if alignment_observer is not None:
        result["query_anchor_skipped_windows"] = anchor_skipped
        result["limitations"][0] = "no_navigation_authority_or_aircraft_publication"
        if not alignment_status["provider_hard_timeout"]:
            result["limitations"].append("research_estimator_callbacks_have_no_hard_timeout")
    (output / "summary.json").write_text(json.dumps(result, indent=2, allow_nan=False)+"\n")
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("binary", "checkout", "sensor-profile", "output", "export-dir", "reference-map",
                 "tile-layout", "calibration", "camera-to-body", "retrieval-export", "retrieval-index"):
        parser.add_argument("--"+name, required=True, type=Path)
    parser.add_argument("--endpoint", required=True)
    parser.add_argument("--frames", type=int, default=120)
    parser.add_argument("--interval-s", type=float, default=.05)
    parser.add_argument("--max-clones", type=int)
    parser.add_argument("--model-startup-timeout-s", type=float, default=120.)
    parser.add_argument("--inference-timeout-s", type=float, default=5.)
    parser.add_argument("--local-verification-config", type=Path)
    parser.add_argument("--alignment-config", type=Path,
                        help="Opt-in research estimator config; audit-only unless an explicit noise provider is supplied")
    parser.add_argument("--alignment-noise-provider", type=Path,
                        help="Trusted local research .py provider, imported only in an owned child process")
    parser.add_argument("--alignment-noise-config", type=Path,
                        help="JSON configuration for the explicit provider; included in evidence, no secrets")
    parser.add_argument("--alignment-startup-timeout-s", type=float, default=20.)
    parser.add_argument("--alignment-operation-timeout-s", type=float, default=2.)
    parser.add_argument("--alignment-refresh-interval-s", type=float,
                        help="Opt-in independent joint-snapshot refresh cadence; requires an isolated noise provider")
    parser.add_argument("--retain-query-anchors", action="store_true",
                        help="Retain exact native poses before learned inference; requires continuous alignment and image recording")
    parser.add_argument("--accepted-evidence-max-age-s", type=float, default=30.,
                        help="Original sensor-time horizon for accepted research fixes, not a freshness override")
    parser.add_argument("--record-images", action="store_true")
    parser.add_argument("--max-image-bytes", type=int, default=1024**3)
    parser.add_argument("--propellers-removed", action="store_true")
    args = parser.parse_args(argv)
    if not args.propellers_removed:
        parser.error("This finite bench command requires --propellers-removed")
    if args.alignment_noise_provider and not args.alignment_config:
        parser.error("An alignment noise provider requires --alignment-config")
    if args.alignment_noise_config and not args.alignment_noise_provider:
        parser.error("A noise configuration requires --alignment-noise-provider")
    if args.alignment_refresh_interval_s is not None and not args.alignment_noise_provider:
        parser.error("Continuous alignment requires --alignment-noise-provider")
    if args.retain_query_anchors and (args.alignment_refresh_interval_s is None or not args.record_images
            or not args.local_verification_config):
        parser.error("Query anchors require continuous alignment, --record-images and --local-verification-config")
    if not 0 < args.accepted_evidence_max_age_s <= 300:
        parser.error("Accepted evidence horizon must be finite and in (0,300] seconds")
    root = args.output.resolve()
    if root.exists():
        parser.error("Output must be new")
    manifest = json.loads((args.export_dir / "manifest.json").read_text())
    session = LiveSensorSession(args.binary, args.checkout, args.sensor_profile, root / "sensors",
        endpoint=args.endpoint, frame_count=args.frames, interval_s=args.interval_s,
        window_size=manifest["config"]["window_size"], max_clones=args.max_clones,
        record_images=args.record_images, max_image_bytes=args.max_image_bytes,
        anchor_capacity=4 if args.retain_query_anchors else 0)
    from .inference_worker import ProcessWindowInference
    inference = ProcessWindowInference(session, args.export_dir, args.reference_map, args.tile_layout,
        args.calibration, args.camera_to_body, retrieval_export=args.retrieval_export,
        retrieval_index=args.retrieval_index, local_verification_config=args.local_verification_config,
        startup_timeout_s=args.model_startup_timeout_s, inference_timeout_s=args.inference_timeout_s)
    observer = None
    if args.alignment_config:
        if args.alignment_config.stat().st_size > 32*1024:
            parser.error("Alignment config exceeds 32 KiB")
        from ..estimation.live_alignment import LiveAlignmentObserver
        engine = None
        if args.alignment_noise_provider:
            from ..estimation.alignment_worker import ProcessAlignmentEngine
            provider_config = None
            if args.alignment_noise_config:
                if args.alignment_noise_config.stat().st_size > 32*1024:
                    parser.error("Alignment noise config exceeds 32 KiB")
                provider_config = json.loads(args.alignment_noise_config.read_text())
            engine = ProcessAlignmentEngine(args.alignment_noise_provider, provider_config=provider_config,
                startup_timeout_s=args.alignment_startup_timeout_s, operation_timeout_s=args.alignment_operation_timeout_s)
        observer = LiveAlignmentObserver(session, json.loads(args.alignment_config.read_text()), process_engine=engine,
            continuous_refresh_interval_s=args.alignment_refresh_interval_s,
            retain_query_anchors=args.retain_query_anchors,
            accepted_evidence_max_age_ns=int(args.accepted_evidence_max_age_s*1e9))
    report = run_live_learned_session(session, inference, root / "learned", alignment_observer=observer)
    print(json.dumps(report, indent=2))
    return 0 if report["status"] == "completed" and report["model_windows"] > 0 else 2


if __name__ == "__main__":
    raise SystemExit(main())
