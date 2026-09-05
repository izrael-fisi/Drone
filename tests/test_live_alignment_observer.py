"""Live observer contracts with explicitly synthetic learned/PnP observations."""

from collections import deque
from copy import deepcopy
import threading
import time
from types import SimpleNamespace
import uuid

import numpy as np
import pytest

from vision_nav.estimation.correlated_window import payload_digest
from vision_nav.estimation.live_alignment import LiveAlignmentObserver
from test_delayed_alignment import later_record
from test_learned_pnp_binding import envelope
from test_pnp_window_alignment import fixture


class Source:
    def __init__(self, config):
        self.state, self.native_state = "not_started", "not_started"
        self.lock, self.snapshots = threading.Lock(), deque()
        self.native = SimpleNamespace(worker_run_id=str(uuid.uuid4()),
            _profile={"camera_calibration_id": config["calibration_id"]},
            _camera={"T_imu_cam": config["imu_T_camera"]},
            status=lambda: {"status": self.native_state}, take_latest=self.take_latest)

    def status(self):
        return {"state": self.state}

    def push(self, row):
        with self.lock:
            self.snapshots.append({"record": deepcopy(row), "worker_run_id": self.native.worker_run_id,
                "received_host_monotonic_ns": time.monotonic_ns(), "position_authority": False})

    def take_latest(self):
        with self.lock:
            return self.snapshots.popleft() if self.snapshots else None


def wait_for(predicate):
    deadline = time.monotonic()+5
    while not predicate():
        assert time.monotonic() < deadline
        time.sleep(.005)


def attempt_noise(request):
    n = len(request["attempt"]["matched_query_pixels"])
    covariance = np.zeros((5*n+4, 5*n+4))
    covariance[:2*n, :2*n] = np.eye(2*n)*.04
    return {"attempt_sha256": payload_digest(request["attempt"]),
        "measurement_order": "all_query_uv_then_all_map_xyz_then_fx_fy_cx_cy",
        "correspondences": "fixed_correct_inliers_conditional", "map_systematics": "included_in_joint_measurement_covariance",
        "rectification_error": "included_in_joint_measurement_covariance", "timing_error": "not_modeled",
        "camera_extrinsic_error": "not_modeled", "scope": "synthetic_contract_test",
        "noise_model_description": "Synthetic independent pixel errors, fixed K/map, not measured aircraft noise",
        "joint_measurement_covariance": covariance.tolist()}


def joint_noise(request):
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


def setup(*, providers=True, **kwargs):
    request, _ = fixture(shared=False, vio_cross=False)
    source = Source(request["config"])
    row, binding, _ = envelope(request["fixes"][0]["request"], worker_run_id=source.native.worker_run_id)
    observer = LiveAlignmentObserver(source, request["config"], poll_interval_s=.005,
        **({"attempt_noise_provider": attempt_noise, "joint_noise_provider": joint_noise} if providers else {}),
        **{"max_snapshot_age_ns": 5_000_000_000, **kwargs})
    observer.start(binding["provenance"])
    source.state, source.native_state = "collecting", "running"
    source.push(request["native_record"])
    wait_for(lambda: observer.status()["counts"].get("native_snapshots") == 1)
    return source, observer, row, {"tickets": [binding["frame_ticket"]]}, request


def test_online_observer_connects_binding_noise_and_latest_joint_pose():
    source, observer, row, item, request = setup()
    try:
        first = observer.observe(item, row)
        assert first["status"] == "research_current_alignment", first
        assert first["current_estimate"]["pose_timestamp_ns"] == 2*10**9
        source.push(later_record())
        wait_for(lambda: observer.status()["counts"].get("native_snapshots") == 2)
        second, binding, _ = envelope(request["fixes"][1]["request"], worker_run_id=source.native.worker_run_id)
        result = observer.observe({"tickets": [binding["frame_ticket"]]}, second)
        assert result["status"] == "research_current_alignment", result
        assert result["current_estimate"]["pose_timestamp_ns"] == 3*10**9
        assert len(result["replay_inputs"][-1]["fixes"]) == 2
        assert not result["position_authority"] and result["px4_external_vision"] == "disabled"
        from vision_nav.estimation.pnp_window_alignment import align
        assert align(result["replay_inputs"][-1])["alignment"] == result["current_estimate"]
    finally:
        assert observer.stop()
    assert not observer.status()["thread_alive"]
    assert observer.observe(item, row)["current_estimate"] is None


def test_no_providers_is_explicit_audit_mode():
    _, observer, row, item, _ = setup(providers=False)
    try:
        result = observer.observe(item, row)
        assert result["status"] == "awaiting_uncertainty"
        assert result["current_estimate"] is None
        assert observer.status()["uncertainty_mode"] == "audit_only_no_noise"
        assert observer.coordinator.status()["outstanding_queries"] == 0
    finally:
        observer.stop()


def test_sensor_failure_during_noise_work_invalidates_output_and_stops_monitor():
    source, observer, row, item, _ = setup()
    entered, release = threading.Event(), threading.Event()
    output = []

    def delayed(request):
        entered.set()
        assert release.wait(5)
        return attempt_noise(request)

    observer.attempt_noise = delayed
    thread = threading.Thread(target=lambda: output.append(observer.observe(item, row)))
    thread.start()
    try:
        assert entered.wait(5)
        source.state = "failed"
        wait_for(lambda: observer.status()["error"] is not None)
    finally:
        release.set()
        thread.join(10)
        assert observer.stop()
    assert output[0]["status"] == "withheld" and output[0]["current_estimate"] is None
    assert not thread.is_alive()


def test_native_snapshots_continue_while_attempt_provider_runs():
    source, observer, row, item, _ = setup()

    def advance(request):
        source.push(later_record())
        wait_for(lambda: observer.status()["counts"].get("native_snapshots") == 2)
        return attempt_noise(request)

    observer.attempt_noise = advance
    try:
        result = observer.observe(item, row)
        assert result["status"] == "research_current_alignment"
        assert result["current_estimate"]["pose_timestamp_ns"] == 3*10**9
    finally:
        observer.stop()


def test_normal_source_completion_keeps_original_receipt_age():
    source, observer, row, item, _ = setup(max_snapshot_age_ns=1)
    source.state = "complete"
    wait_for(lambda: not observer.status()["thread_alive"])
    try:
        result = observer.observe(item, row)
        assert result["reason"] == "vio_snapshot_receipt_stale"
    finally:
        observer.stop()


def test_initialization_is_waiting_but_loss_after_tracking_invalidates_epoch():
    source, observer, _, _, request = setup()
    invalid = deepcopy(request["native_record"])
    invalid.update(timestamp_ns=3*10**9, local_state="INITIALIZING_OR_NO_UPDATE", pose_window=None)
    source.push(invalid)
    wait_for(lambda: observer.status()["error"] is not None)
    assert observer.stop()
    assert observer.coordinator.status()["fault"] is not None


@pytest.mark.parametrize("kind", ["extrinsic", "calibration", "half_provider", "noncallable", "age", "attempts", "poll"])
def test_invalid_configuration_cannot_open_monitor(kind):
    request, _ = fixture()
    config = deepcopy(request["config"])
    source, kwargs = Source(config), {}
    if kind == "extrinsic":
        config["imu_T_camera"][0][3] = 1.
        source.native._camera["T_imu_cam"] = np.eye(4).tolist()
    elif kind == "calibration":
        config["calibration_id"] = "wrong"
    elif kind == "half_provider":
        kwargs["attempt_noise_provider"] = attempt_noise
    elif kind == "noncallable":
        kwargs.update(attempt_noise_provider=1, joint_noise_provider=2)
    else:
        kwargs[{"age": "max_snapshot_age_ns", "attempts": "max_refresh_attempts", "poll": "poll_interval_s"}[kind]] = 0
    with pytest.raises(ValueError):
        LiveAlignmentObserver(source, config, **kwargs)


def test_wrong_loaded_map_and_restart_are_rejected():
    source, observer, _, _, _ = setup()
    assert observer.stop()
    with pytest.raises(RuntimeError):
        observer.start(observer._provenance)
    other = deepcopy(observer._provenance)
    other["map_release_id"] = "wrong"
    with pytest.raises(ValueError):
        observer.start(other)


@pytest.mark.parametrize("execution", ["in_process", "isolated", "isolated_timeout", "continuous"])
def test_finite_runner_records_replayable_conditional_alignment_and_closes(tmp_path, execution):
    import json
    from pathlib import Path
    from vision_nav.neural.live_sensor_inference import run_live_learned_session
    from vision_nav.estimation.alignment_worker import ProcessAlignmentEngine

    request, _ = fixture(shared=False, vio_cross=False)

    class Session(Source):
        def __init__(self):
            super().__init__(request["config"])
            self.root, self.items, self.closed = tmp_path / "source", deque(), False
        def start(self):
            self.state, self.native_state = "collecting", "running"
            self.push(request["native_record"])
        def take_latest_window(self):
            return self.items.popleft() if self.items else None
        def join(self, timeout):
            if not self.items:
                self.state, self.native_state = "complete", "complete"
                return True
            return False
        def status(self):
            return {"state": self.state, "pending_learned_window": bool(self.items)}
        def stop(self):
            self.closed = True
            return True

    source = Session()
    for fix in request["fixes"]:
        row, binding, _ = envelope(fix["request"], worker_run_id=source.native.worker_run_id)
        row["processing_latency_ms"] = .01
        source.items.append({"tickets": [binding["frame_ticket"]], "row": row})
    if execution == "in_process":
        providers = dict(attempt_noise_provider=attempt_noise, joint_noise_provider=joint_noise)
    else:
        providers = dict(process_engine=ProcessAlignmentEngine(Path(__file__).parent / "fixtures/alignment_noise.py",
            provider_config={"fault_stage": "bind", "delay_s": 60} if execution == "isolated_timeout" else {},
            operation_timeout_s=.2 if execution == "isolated_timeout" else 5.))
    observer = LiveAlignmentObserver(source, request["config"], **providers, max_snapshot_age_ns=5_000_000_000,
        continuous_refresh_interval_s=.02 if execution == "continuous" else None)

    class Inference:
        session, provenance = source, binding["provenance"]
        def verify_sources(self):
            pass
        def process(self, item):
            wait_for(lambda: observer.status()["counts"].get("native_snapshots") == 1)
            return item["row"]

    report = run_live_learned_session(source, Inference(), tmp_path / "learned", alignment_observer=observer)
    if execution == "isolated_timeout":
        assert report["status"] == "incomplete" and report["error"]
        assert not report["research_alignment"]["estimator_worker"]["process_alive"]
        assert source.closed and not observer.status()["thread_alive"]
        return
    assert report["status"] == "completed", report
    assert report["research_alignment_connected"] and not report["global_fusion_connected"]
    rows = [json.loads(line) for line in (tmp_path / "learned/trace.jsonl").read_text().splitlines()]
    assert len(rows) == 2
    if execution == "continuous":
        assert all(r["research_alignment"]["status"] == "queued_for_alignment" for r in rows)
        updates = [json.loads(line) for line in (tmp_path / "learned/alignment-trace.jsonl").read_text().splitlines()]
        assert updates[-1]["map_evidence_timestamp_ns"] == 2*10**9
        assert updates[-1]["current_estimate"]["pose_timestamp_ns"] == 2*10**9
        assert report["alignment_trace_sha256"]
        assert report["research_alignment"]["continuous_refresh"]["finished"]
        assert source.closed and not observer.status()["continuous_refresh"]["thread_alive"]
        return
    assert all(r["research_alignment"]["status"] == "research_current_alignment" for r in rows)
    assert rows[-1]["research_alignment"]["replay_inputs"]
    assert source.closed and not observer.status()["thread_alive"]
    if execution == "isolated":
        assert report["research_alignment"]["estimator_worker"]["returncode"] == 0
        assert "research_estimator_callbacks_have_no_hard_timeout" not in report["limitations"]


def test_stop_reports_active_provider_and_late_result_is_withheld():
    _, observer, row, item, _ = setup()
    entered, release = threading.Event(), threading.Event()
    output = []

    def slow(request):
        entered.set()
        assert release.wait(5)
        return attempt_noise(request)

    observer.attempt_noise = slow
    worker = threading.Thread(target=lambda: output.append(observer.observe(item, row)))
    worker.start()
    try:
        assert entered.wait(5)
        assert observer.stop() is False
        assert observer.status()["state"] == "shutdown_incomplete"
    finally:
        release.set()
        worker.join(10)
        assert observer.stop()
    assert output[0]["current_estimate"] is None


def test_status_normalizes_array_config_and_owns_its_values():
    import numpy as np

    request, _ = fixture(shared=False, vio_cross=False)
    config = deepcopy(request["config"])
    source = Source(config)
    config["imu_T_camera"] = np.array(config["imu_T_camera"])
    config["map_common_left_covariance"] = np.array(config["map_common_left_covariance"])
    observer = LiveAlignmentObserver(source, config)
    status = observer.status()
    assert isinstance(status["config"]["imu_T_camera"], list)
    assert status["config"]["max_iterations"] == 30
    status["config"]["imu_T_camera"][0][0] = 123
    assert observer.status()["config"]["imu_T_camera"][0][0] != 123
    assert observer.stop()
