"""Actual native poses and isolated alignment with synthetic map/noise evidence."""

from copy import deepcopy
import json
from pathlib import Path
import threading
import time
from types import SimpleNamespace

import numpy as np
import pytest

from vision_nav.estimation.alignment_worker import ProcessAlignmentEngine
from vision_nav.estimation.correlated_window import CorrelatedWindowAligner, WindowAlignmentConfig
from vision_nav.estimation.live_alignment import LiveAlignmentObserver
from vision_nav.vio.openvins_replay import digest
from vision_nav.vio.pose_window import JointPoseWindow
from test_learned_pnp_binding import envelope
from test_native_live_worker import arrival_inputs, make_worker, offer
from test_prepare_vio_capture import sources
from test_pnp_window_alignment import fixture
from test_window_marginalization import single_fix_covariance
from test_vio_stream_worker import wait_for


@pytest.mark.parametrize("wire_format", ["json_v1", "msgpack_v1"])
@pytest.mark.parametrize("stream_frames", [20, 60])
def test_query_owned_before_slow_result_survives_normal_window_and_releases(arrival_inputs, tmp_path, wire_format, stream_frames):
    native = make_worker(arrival_inputs, tmp_path, anchor_capacity=4, record_images=True)
    source = SimpleNamespace(state="not_started", native=native)
    source.status = lambda: {"state": source.state}
    config = fixture()[0]["config"]
    config.update(calibration_id=native._profile["camera_calibration_id"], imu_T_camera=native._camera["T_imu_cam"])
    provenance = {"worker_run_id": native.worker_run_id, "map_release_id": config["map_release_id"],
        "calibration_id": config["calibration_id"], "model_sha256": "a"*64}
    engine = ProcessAlignmentEngine(Path(__file__).parent/"fixtures/alignment_noise.py", wire_format=wire_format)
    observer = LiveAlignmentObserver(source, config, process_engine=engine, continuous_refresh_interval_s=.02,
        poll_interval_s=.005, retain_query_anchors=True)
    observer.start(provenance, evidence_path=tmp_path/"alignment.jsonl")
    source.state = "collecting"
    native.start()
    thread = None
    try:
        # Advance the actual estimator to its first initialized camera pose.
        for index in range(len(arrival_inputs[-1])):
            ticket = offer(native, arrival_inputs, index)
            wait_for(lambda: native.status()["completed_batch_count"] == index+1)
            if native.status()["last_local_state"] == "LOCAL_ESTIMATE":
                break
        wait_for(lambda: observer.coordinator.status()["posterior_timestamp_ns"] == ticket.timestamp_ns)
        original = deepcopy(observer.coordinator._record)
        item = {"worker_run_id": native.worker_run_id, "tickets": [ticket]}
        admission, failures = [], []
        def acquire():
            try:
                admission.append(observer.begin_inference(item))
            except Exception as exc:
                failures.append(exc)
        thread = threading.Thread(target=acquire)
        thread.start()
        wait_for(lambda: native.status()["anchor_control"]["acknowledged"] == 1)
        # ACK alone is insufficient: the next actual camera exports membership.
        offer(native, arrival_inputs, index+1)
        wait_for(lambda: native.status()["completed_batch_count"] == index+2)
        thread.join(5.)
        assert not thread.is_alive() and not failures, failures
        assert admission[0]["status"] == "dispatched", admission
        query = admission[0]["ticket"]
        assert query["dispatched_host_monotonic_ns"] < time.monotonic_ns()
        for later in range(index+2, index+27):
            offer(native, arrival_inputs, later)
            wait_for(lambda: native.status()["completed_batch_count"] == later+1)
        wait_for(lambda: observer.coordinator.status()["posterior_timestamp_ns"] == native.status()["last_sensor_timestamp_ns"])
        retained = observer.coordinator._record
        assert ticket.timestamp_ns not in retained["pose_window"]["normal_clone_timestamps_ns"]
        assert ticket.timestamp_ns in retained["pose_window"]["passive_anchor_timestamps_ns"]
        assert retained["timestamp_ns"]-ticket.timestamp_ns > 1_000_000_000
        assert np.linalg.norm(JointPoseWindow.from_record(retained).covariance_right_local[:6, -6:]) > 0

        # Geometry is projected from synthetic landmarks, NOT measured accuracy.
        request, _ = fixture(original, shared=False, vio_cross=False)
        pnp = request["fixes"][-1]["request"]
        pnp["context"].update({key: config[key] for key in
            ("map_release_id", "model_release_id", "calibration_id", "clock_id", "uncertainty_model_id")})
        row, binding, _ = envelope(pnp, worker_run_id=native.worker_run_id)
        row.update(sequence=ticket.sequence, anchor_pixel_sha256=ticket.pixel_sha256,
            anchor_png_sha256=ticket.image_sha256, window_frame_identities=[{
                "sequence": ticket.sequence, "timestamp_ns": ticket.timestamp_ns, "pixel_sha256": ticket.pixel_sha256}])
        assert observer.observe(item, row)["status"] == "queued_for_alignment"
        wait_for(lambda: query["query_id"] in observer.coordinator.status()["accepted_query_ids"])
        state = observer.coordinator.status()
        assert state["queries"][0]["dispatched_host_monotonic_ns"] == query["dispatched_host_monotonic_ns"]
        assert state["queries"][0]["timestamp_ns"] == ticket.timestamp_ns
        # Continue at the fixture's 20 Hz exposure cadence. Do not pause native
        # input for each estimator solve or increase the 500 ms freshness policy.
        stream_initial_posterior = observer.coordinator.status()["posterior_timestamp_ns"]
        stream_started = time.monotonic_ns()
        streamed = list(range(index+27, min(index+27+stream_frames, len(arrival_inputs[-1]))))
        assert len(streamed) == stream_frames
        target = time.monotonic()
        status_samples = []
        for later in streamed:
            target += .05
            while time.monotonic() < target:
                live_status = observer.status()["continuous_refresh"]
                available = live_status["latest_snapshot_estimate"]
                status_samples.append({"checked_host_monotonic_ns": time.monotonic_ns(),
                    "pose_timestamp_ns": available["pose_timestamp_ns"] if available else None,
                    "evidence": live_status["latest_snapshot_evidence"]})
                time.sleep(min(.005, max(0., target-time.monotonic())))
            offer(native, arrival_inputs, later)
        stream_finished = time.monotonic_ns()
        wait_for(lambda: native.status()["completed_batch_count"] == streamed[-1]+1)
        assert observer.finish()
        assert observer.stop()
        assert native.status()["anchor_control"]["active_anchor_timestamps_ns"] == []
        assert native.status()["anchor_control"]["acknowledged"] == 2
        native.finish()
        assert native.join(10.) and native.status()["native_returncode"] == 0
        status = observer.status()
        assert status["query_anchors"]["closed"] and not status["query_anchors"]["owned_timestamps_ns"]
        assert status["query_anchors"]["evidence_sha256"] == digest(tmp_path/"query-anchor-trace.jsonl")
        rows = [json.loads(line) for line in (tmp_path/"alignment.jsonl").read_text().splitlines()]
        assert any(r["refresh"]["last_alignment"]["status"] == "research_alignment"
            for r in rows if r.get("refresh", {}).get("last_alignment"))
        successful = next(r for r in rows if r.get("current_estimate") and r.get("replay_inputs"))
        replay = successful["replay_inputs"][-1]
        map_batch = successful["refresh"]["last_alignment"]["result"]["map_batch"]
        reduced = successful["current_estimate"]
        full = CorrelatedWindowAligner(WindowAlignmentConfig(**config)).solve(
            replay["native_record"], map_batch, marginalize_unused_poses=False)
        assert reduced["status"] == full["status"] == "research_alignment"
        assert reduced["nuisance_elimination"]["marginalized_pose_count"] > 0
        for key in ("map_T_imu", "covariance_right_imu", "local_T_nominal_map"):
            np.testing.assert_allclose(reduced[key], full[key], atol=2e-6, rtol=2e-5)
        expected = single_fix_covariance(CorrelatedWindowAligner(WindowAlignmentConfig(**config)),
                                         replay["native_record"], map_batch)
        np.testing.assert_allclose(reduced["covariance_right_imu"], expected, atol=1e-8, rtol=1e-5)
        assert reduced["pose_timestamp_ns"] == full["pose_timestamp_ns"]
        assert engine.status()["last_operation"]["operation"] == "align_with_noise"
        (tmp_path/"benchmark-request.json").write_text(json.dumps({
            "schema_version": "vision_nav_correlated_alignment_request_v1", "config": config,
            "native_record": replay["native_record"], "map_batch": map_batch})+"\n")
        (tmp_path/"joint-benchmark-request.json").write_text(json.dumps({
            k: v for k, v in replay.items() if k != "joint_uncertainty"})+"\n")
        checked_during_stream = [r for r in rows if r.get("current_estimate") and
            stream_started <= r["refresh"]["checked_host_monotonic_ns"] < stream_finished]
        current_during_stream = [r for r in checked_during_stream
            if r["current_estimate"]["pose_timestamp_ns"] > stream_initial_posterior]
        # Numerical replay remains independent of whether the live scheduling
        # makes a pose observable. Verify every successful streaming decision.
        from vision_nav.estimation.pnp_window_alignment import align
        for update in current_during_stream:
            assert align(update["replay_inputs"][-1])["alignment"] == update["current_estimate"]
        by_sequence = {r["event_sequence"]: r for r in rows}
        for sample in status_samples:
            if sample["pose_timestamp_ns"] is not None:
                event = by_sequence[sample["evidence"]["event_sequence"]]
                assert event["current_estimate"]["pose_timestamp_ns"] == sample["pose_timestamp_ns"]
                assert sample["evidence"]["state"] in {"pending", "written"}
        (tmp_path/"streaming-observation.json").write_text(json.dumps({
            "scope": "finite_native_synthetic_map_observation_not_Pi_or_flight_qualification",
            "wire_format": wire_format,
            "source_cadence_hz": 20, "streamed_frames": len(streamed),
            "snapshot_receipt_age_limit_ns": observer.max_age,
            "stream_started_host_monotonic_ns": stream_started, "stream_finished_host_monotonic_ns": stream_finished,
            "stream_initial_posterior_timestamp_ns": stream_initial_posterior,
            "checked_update_count_during_stream": len(checked_during_stream),
            "selection_policy": "checked_during_stream_and_pose_newer_than_stream_initial_posterior",
            "current_update_count_during_stream": len(current_during_stream),
            "current_pose_timestamps_ns": [r["current_estimate"]["pose_timestamp_ns"] for r in current_during_stream],
            "operator_status_sample_count": len(status_samples),
            "operator_status_advancing_pose_timestamps_ns": sorted({s["pose_timestamp_ns"] for s in status_samples
                if s["pose_timestamp_ns"] is not None and s["pose_timestamp_ns"] > stream_initial_posterior}),
            "operator_status_samples": status_samples,
            "native_refresh_wakeups": status["counts"].get("native_refresh_wakeups", 0),
            "all_attempt_processing_ms": [r["processing_latency_ms"] for r in rows if r.get("replay_inputs")],
            "position_authority": False, "px4_external_vision": "disabled"}, indent=2)+"\n")
        assert all(not r["position_authority"] and r["px4_external_vision"] == "disabled" for r in rows)
    finally:
        if thread: thread.join(5.)
        assert observer.stop()
        assert native.stop()
