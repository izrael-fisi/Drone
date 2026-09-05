"""Opt-in finite-session research fusion observer; no aircraft transport."""

from collections import Counter
from copy import deepcopy
from dataclasses import asdict
import json
from pathlib import Path
import threading
import time

import numpy as np

from .correlated_window import WindowAlignmentConfig
from .delayed_alignment import DelayedPnPAlignment, _owned
from .learned_pnp_binding import prepare, bind_uncertainty


class LiveAlignmentObserver:
    """Sole consumer of native take_latest; never another camera/serial owner.

    Prefer an owned ProcessAlignmentEngine for explicit research noise/solves.
    Direct callbacks remain an unbounded in-process test reference. Neither
    supplies a measured noise model; no engine/providers means audit-only.
    """

    def __init__(self, session, config, *, attempt_noise_provider=None, joint_noise_provider=None,
                 max_snapshot_age_ns=500_000_000, max_refresh_attempts=2, poll_interval_s=.02,
                 process_engine=None, continuous_refresh_interval_s=None,
                 retain_query_anchors=False, accepted_evidence_max_age_ns=30_000_000_000):
        if session.status()["state"] != "not_started":
            raise ValueError("Bind research alignment before opening sensors")
        cfg = WindowAlignmentConfig(**config)
        if (cfg.calibration_id != session.native._profile["camera_calibration_id"]
                or not np.allclose(cfg.imu_T_camera, session.native._camera["T_imu_cam"], atol=1e-8, rtol=0)):
            raise ValueError("Alignment calibration/extrinsic must match the native sensor profile")
        if (attempt_noise_provider is None) != (joint_noise_provider is None):
            raise ValueError("Supply both uncertainty providers or neither")
        if attempt_noise_provider is not None and (not callable(attempt_noise_provider) or not callable(joint_noise_provider)):
            raise ValueError("Uncertainty providers must be callable")
        if process_engine is not None:
            from .alignment_worker import ProcessAlignmentEngine
            if not isinstance(process_engine, ProcessAlignmentEngine) or attempt_noise_provider is not None:
                raise ValueError("Use either one owned estimator process or explicit in-process providers")
        if type(max_snapshot_age_ns) is not int or not 0 < max_snapshot_age_ns <= 10_000_000_000:
            raise ValueError("Invalid explicit native receipt-age limit")
        if type(max_refresh_attempts) is not int or not 1 <= max_refresh_attempts <= 4:
            raise ValueError("Expected 1..4 refresh attempts")
        if type(poll_interval_s) not in (int, float) or not .005 <= poll_interval_s <= 1:
            raise ValueError("Expected finite monitor interval in [0.005, 1] seconds")
        if continuous_refresh_interval_s is not None and (
                process_engine is None or type(continuous_refresh_interval_s) not in (int, float)
                or not .02 <= continuous_refresh_interval_s <= 5.):
            raise ValueError("Continuous refresh requires an owned estimator process and interval in [0.02, 5] seconds")
        if type(retain_query_anchors) is not bool or retain_query_anchors and (
                continuous_refresh_interval_s is None or not session.native._record_images):
            raise ValueError("Automatic anchors require continuous isolated alignment and image recording")
        self.session, self.config = session, cfg
        record = asdict(cfg)
        record["imu_T_camera"] = cfg.imu_T_camera.tolist()
        record["map_common_left_covariance"] = cfg.map_common_left_covariance.tolist()
        self._config_record = _owned(record, 32*1024)
        self.engine = process_engine
        self.coordinator = DelayedPnPAlignment(config, session.native.worker_run_id,
            alignment_solver=self.engine.align if self.engine else None,
            max_accepted_age_ns=accepted_evidence_max_age_ns if retain_query_anchors else None)
        self.anchors = None
        if retain_query_anchors:
            from .query_anchors import QueryAnchorOwner
            self.anchors = QueryAnchorOwner(session.native, self.coordinator)
        self.attempt_noise, self.joint_noise = attempt_noise_provider, joint_noise_provider
        self.max_age, self.max_attempts, self.interval = max_snapshot_age_ns, max_refresh_attempts, poll_interval_s
        self._lock, self._observe_lock = threading.RLock(), threading.Lock()
        self._stop = threading.Event()
        self._thread = self._provenance = self._fault = None
        self._state, self._seen_local, self._counts = "not_started", False, Counter()
        self.refresh_interval = continuous_refresh_interval_s
        self._condition = threading.Condition(self._lock)
        self._updates_thread = self._updates_stream = self._updates_path = None
        self._pending_draft = self._last_update = None
        self._current_update = None
        self._last_submitted_ns = self._update_count = 0
        self._updates_busy = self._finishing = self._updates_finished = False
        self._snapshot_generation = 0

    def _fail(self, reason):
        with self._lock:
            self._fault = self._fault or reason[:256]
            self._state = "failed"
        self.coordinator.invalidate(self._fault)
        if self.engine:
            self.engine.cancel()
        self._stop.set()
        with self._condition:
            self._condition.notify_all()

    def start(self, provenance, *, evidence_path=None):
        owned = _owned(provenance, 256*1024)
        if (owned.get("worker_run_id") != self.session.native.worker_run_id
                or owned.get("map_release_id") != self.config.map_release_id
                or owned.get("calibration_id") != self.config.calibration_id):
            raise ValueError("Alignment map/calibration/epoch differs from loaded learned pipeline")
        with self._lock:
            if self._state != "not_started":
                raise RuntimeError("Alignment observer cannot restart")
            if self.refresh_interval is not None:
                if evidence_path is None:
                    raise ValueError("Continuous alignment requires a new dedicated evidence file")
                self._updates_path = Path(evidence_path).resolve()
                self._updates_stream = self._updates_path.open("x")
                self._updates_thread = threading.Thread(target=self._update_loop,
                    name="vision-nav-continuous-alignment", daemon=True)
            self._provenance, self._state = owned, "monitoring"
            self._thread = threading.Thread(target=self._monitor, name="vision-nav-joint-snapshot-monitor", daemon=True)
            try:
                if self.anchors:
                    self.anchors.start(self._updates_path.with_name("query-anchor-trace.jsonl"))
                if self.engine:
                    self.engine.start()
                self._thread.start()
                if self._updates_thread:
                    self._updates_thread.start()
            except Exception:
                if self._thread.ident is None:
                    self._thread = None
                self._fail("snapshot_monitor_start_failed")
                if self.engine:
                    self.engine.stop()
                if self._updates_stream and not (self._updates_thread and self._updates_thread.is_alive()):
                    self._updates_stream.close()
                if self.anchors:
                    self.anchors.close()
                raise

    def _check_source(self):
        source, native = self.session.status(), self.session.native.status()
        if (source["state"] in {"failed", "stopped", "shutdown_incomplete"}
                or native["status"] in {"failed", "stopped", "shutdown_incomplete"}):
            raise RuntimeError("Sensor/native owner failed or stopped")
        if self.engine and not self._stop.is_set():
            worker = self.engine.status()
            if (worker["error"] or not worker["process_alive"]) and not self._stop.is_set():
                raise RuntimeError("Research estimator process failed or stopped")
        return source, native

    def _monitor(self):
        try:
            while not self._stop.is_set():
                source, _ = self._check_source()
                snapshot = self.session.native.take_latest()
                if snapshot is not None:
                    if snapshot["record"]["local_state"] == "LOCAL_ESTIMATE" or self._seen_local:
                        update = self.coordinator.update_snapshot(snapshot)
                        if update["status"] != "updated":
                            raise RuntimeError(update["reason"])
                        self._seen_local = True
                        with self._lock:
                            self._counts["native_snapshots"] += 1
                            self._snapshot_generation += 1
                            self._condition.notify_all()
                if source["state"] in {"complete", "completed_without_local_estimate"}:
                    with self._lock:
                        self._state = "source_complete"
                    return
                self._stop.wait(self.interval)
        except Exception as exc:
            self._fail(f"snapshot_monitor_failed:{type(exc).__name__}:{exc}")

    def _event(self, status, reason, **fields):
        with self._lock:
            self._counts[status] += 1
        return {"schema_version": "vision_nav_live_research_alignment_v1", "status": status, "reason": reason,
            "position_authority": False, "navigation_mode": "NO_GLOBAL_FIX", "px4_external_vision": "disabled",
            "covariance_calibrated": False, "current_estimate": None, **fields}

    def begin_inference(self, item):
        """Acquire the original pose before any expensive learned processing."""
        if not self.anchors:
            return {"status": "not_enabled"}
        try:
            self._check_source()
            if self._stop.is_set():
                return self._event("withheld", "alignment_observer_not_available")
            return self.anchors.acquire(item)
        except Exception as exc:
            self._fail(f"query_anchor_acquisition_failed:{type(exc).__name__}:{exc}")
            raise

    def observe(self, item, row):
        if self.refresh_interval is not None:
            return self._enqueue(item, row)
        if not self._observe_lock.acquire(blocking=False):
            return self._event("withheld", "alignment_observation_already_running")
        try:
            if self._provenance is None or self._stop.is_set():
                return self._event("withheld", "alignment_observer_not_available")
            self._check_source()
            draft = prepare(row, frame_ticket=item["tickets"][-1], provenance=self._provenance,
                model_release_id=self.config.model_release_id, clock_id=self.config.clock_id,
                uncertainty_model_id=self.config.uncertainty_model_id)
            if draft["status"] != "awaiting_joint_noise":
                return self._event("withheld", draft["reason"], binding=draft)
            if self.attempt_noise is None and self.engine is None:
                return self._event("awaiting_uncertainty", "no_joint_noise_providers", binding=draft)
            binding = self._bind(draft)
            if binding["status"] != "ready":
                return binding
            return self._refresh(binding=binding["binding"])
        except Exception as exc:
            self._fail(f"alignment_observation_failed:{type(exc).__name__}:{exc}")
            return self._event("withheld", "alignment_observer_failed", detail=self._fault)
        finally:
            self._observe_lock.release()

    def _bind(self, draft):
        context = draft["request_without_uncertainty"]["context"]
        query = self.anchors.query_for(context) if self.anchors else None
        if self.anchors and query is None:
            return self._event("withheld", "query_not_acquired_before_inference")
        bound = (self.engine.bind(draft) if self.engine else
                 bind_uncertainty(draft, self.attempt_noise(deepcopy(draft["request_without_uncertainty"]))))
        self._check_source()
        if self._stop.is_set():
            return self._event("withheld", "alignment_observer_not_available")
        if bound["status"] != "conditional_pnp_request":
            if self.anchors:
                self.anchors.reject(context["timestamp_ns"], bound["reason"])
            return self._event("withheld", bound["reason"], binding=bound)
        context = bound["request"]["context"]
        if not self.anchors:
            dispatched = self.coordinator.begin_query(context["timestamp_ns"], context["frame_sha256"])
            if dispatched["status"] != "dispatched":
                return self._event("withheld", dispatched["reason"], binding=bound)
            query = dispatched["ticket"]
        completion = self.coordinator.complete_query(query, bound["request"])
        if self.anchors and completion["status"] != "ready":
            self.anchors.reject(context["timestamp_ns"], completion["reason"])
        return self._event(completion["status"], completion["reason"], binding=bound)

    def _refresh(self, *, binding=None):
        inputs = []
        estimator_operations = []

        def noise(request):
            joint = _owned(self.joint_noise(deepcopy(request)), 32*1024*1024)
            inputs.append({**request, "joint_uncertainty": joint})
            return joint

        def execute(request):
            response = self.engine.align_with_noise(request)
            estimator_operations.append(self.engine.status()["last_operation"])
            inputs.append({**request, "joint_uncertainty": response["joint_uncertainty"]})
            return response

        result = self.coordinator.refresh_current(None if self.engine else noise,
            joint_executor=execute if self.engine else None,
            max_snapshot_age_ns=self.max_age, max_attempts=self.max_attempts)
        if self.engine and self.engine.status()["error"]:
            raise RuntimeError(self.engine.status()["error"])
        self._check_source()
        if self._stop.is_set():
            return self._event("withheld", "alignment_observer_not_available")
        estimate = result["current_estimate"]
        last_fix = max((f["request"]["context"]["timestamp_ns"] for f in inputs[-1]["fixes"]), default=None) if inputs else None
        age = estimate["pose_timestamp_ns"]-last_fix if estimate is not None and last_fix is not None else None
        event = self._event(result["status"], result["reason"], binding=binding, refresh=result,
            replay_inputs=inputs, estimator_operations=estimator_operations,
            current_estimate=estimate, map_evidence_timestamp_ns=last_fix,
            map_evidence_age_at_pose_ns=age, position_source=("research_vio_between_fixes" if age else
                "research_map_correction") if age is not None else "no_research_position")
        # Continuous mode owns these callback/process results until journaling.
        # Do not copy its full replay payload before exposing the compact state.
        return event if self.refresh_interval is not None else _owned(event, 4*1024*1024)

    def _enqueue(self, item, row):
        try:
            if self._provenance is None or self._stop.is_set():
                return self._event("withheld", "alignment_observer_not_available")
            self._check_source()
            draft = prepare(row, frame_ticket=item["tickets"][-1], provenance=self._provenance,
                model_release_id=self.config.model_release_id, clock_id=self.config.clock_id,
                uncertainty_model_id=self.config.uncertainty_model_id)
            if draft["status"] != "awaiting_joint_noise":
                if self.anchors:
                    self.anchors.reject(item["tickets"][-1].timestamp_ns, draft["reason"])
                return self._event("withheld", draft["reason"], binding=draft)
            ns = draft["request_without_uncertainty"]["context"]["timestamp_ns"]
            with self._condition:
                if self._stop.is_set() or self._finishing:
                    return self._event("withheld", "alignment_observer_not_available")
                if ns <= self._last_submitted_ns:
                    return self._event("withheld", "duplicate_or_reordered_alignment_submission")
                previous = self._pending_draft
                superseded = previous["draft_sha256"] if previous else None
                if superseded:
                    self._counts["superseded_pending_drafts"] += 1
                self._pending_draft, self._last_submitted_ns = draft, ns
                self._condition.notify_all()
            if previous and self.anchors:
                self.anchors.reject(previous["request_without_uncertainty"]["context"]["timestamp_ns"],
                    "pending_draft_superseded")
            return self._event("queued_for_alignment", "latest_pending_draft_queued",
                draft_sha256=draft["draft_sha256"], superseded_draft_sha256=superseded,
                map_evidence_timestamp_ns=ns)
        except Exception as exc:
            self._fail(f"alignment_submission_failed:{type(exc).__name__}:{exc}")
            return self._event("withheld", "alignment_observer_failed", detail=self._fault)

    def _write_update(self, event, *, trigger, draft_id):
        record = {**event, "trigger": trigger, "draft_sha256": draft_id,
            "recorded_host_monotonic_ns": time.monotonic_ns(),
            "estimate_scope": "latest_snapshot_at_check_only", "event_sequence": self._update_count+1}
        summary = _owned({k: record.get(k) for k in ("status", "reason", "current_estimate",
            "recorded_host_monotonic_ns", "estimate_scope", "map_evidence_timestamp_ns",
            "map_evidence_age_at_pose_ns", "position_source", "processing_latency_ms", "event_sequence")}, 64*1024)
        # Status checks native time, fix membership, receipt age and health again.
        # Pending evidence is visible only as conditional research, never a
        # durable flight record; a journal failure terminates the observer.
        with self._condition:
            self._current_update = {**summary, "evidence_state": "pending"}
        event = _owned(record, 4*1024*1024)
        if self._stop.is_set():
            event.update(status="withheld", reason="alignment_observer_not_available", current_estimate=None)
        line = json.dumps(event, sort_keys=True, allow_nan=False)+"\n"
        size = len(line.encode())
        if size > 4*1024*1024 or self._updates_stream.tell()+size > 256*1024*1024:
            raise ValueError("Continuous alignment evidence exceeds its byte budget")
        self._updates_stream.write(line)
        self._updates_stream.flush()
        with self._condition:
            self._update_count += 1
            if self._stop.is_set():
                summary.update(status="withheld", reason="alignment_observer_not_available", current_estimate=None)
            self._last_update = {**summary, "evidence_state": "written"}
            self._current_update = self._last_update

    def _update_loop(self):
        token, deadline, earliest_start, generation = None, 0., 0., 0
        try:
            while not self._stop.is_set():
                with self._condition:
                    while not self._pending_draft and not self._finishing and not self._stop.is_set():
                        now = time.monotonic()
                        changed = self._snapshot_generation != generation
                        if now >= deadline or changed and now >= earliest_start:
                            if changed and now < deadline:
                                self._counts["native_refresh_wakeups"] += 1
                            break
                        wake_at = min(deadline, earliest_start) if changed else deadline
                        self._condition.wait(max(0., wake_at-now))
                    if self._stop.is_set():
                        break
                    draft, self._pending_draft = self._pending_draft, None
                    self._updates_busy = True
                    generation = self._snapshot_generation
                    earliest_start = time.monotonic()+self.refresh_interval
                began = time.perf_counter()
                self._check_source()
                if self.anchors:
                    self.anchors.reconcile()
                binding = self._bind(draft) if draft else None
                if binding and binding["status"] != "ready":
                    self._write_update({**binding, "processing_latency_ms": (time.perf_counter()-began)*1000},
                        trigger="new_map_evidence", draft_id=draft["draft_sha256"])
                state = self.coordinator.status()
                received = state["snapshot_received_host_monotonic_ns"]
                next_token = (state["posterior_timestamp_ns"], state["latest_map_evidence_timestamp_ns"],
                    state["ready_queries"], received is not None and time.monotonic_ns()-received > self.max_age)
                if draft or next_token != token or self._finishing:
                    update = self._refresh(binding=binding.get("binding") if binding else None)
                    if self.anchors:
                        self.anchors.reconcile()
                        # Retirement changes the exact set of conditioning fixes.
                        if update.get("current_estimate") is not None and sorted(update["current_estimate"]["fix_ids"]) != self.coordinator.status()["ready_query_ids"]:
                            update.update(status="withheld", reason="query_set_changed_after_refresh", current_estimate=None)
                        native = self.session.native.status()
                        if update.get("current_estimate") is not None and (native.get("native_returncode") is not None
                                or native.get("last_sensor_timestamp_ns") != update["current_estimate"]["pose_timestamp_ns"]):
                            update.update(status="withheld", reason="native_snapshot_no_longer_current", current_estimate=None)
                    update["processing_latency_ms"] = (time.perf_counter()-began)*1000
                    self._write_update(update, trigger="new_map_evidence" if draft else "vio_snapshot_refresh",
                                       draft_id=draft["draft_sha256"] if draft else None)
                token, deadline = next_token, time.monotonic()+self.refresh_interval
                with self._condition:
                    self._updates_busy = False
                    self._condition.notify_all()
                    if self._finishing and self._pending_draft is None:
                        self._updates_finished = True
                        return
        except Exception as exc:
            self._fail(f"continuous_alignment_failed:{type(exc).__name__}:{exc}")
        finally:
            self._updates_stream.close()
            with self._condition:
                self._updates_busy = False
                self._condition.notify_all()

    def finish(self, timeout_s=15.):
        """Drain the final submitted map draft before finite-session shutdown."""
        if self.refresh_interval is None:
            return True
        if type(timeout_s) not in (int, float) or not 0 < timeout_s <= 60:
            raise ValueError("Expected finish timeout in (0, 60] seconds")
        deadline = time.monotonic()+timeout_s
        # A finite source may finish just before the monitor consumes its last
        # snapshot. Drain that sole consumer before the final refresh.
        if self.session.status()["state"] in {"complete", "completed_without_local_estimate"} and self._thread:
            self._thread.join(max(0., deadline-time.monotonic()))
            if self._thread.is_alive():
                self._fail("final_snapshot_drain_incomplete")
                return False
        with self._condition:
            self._finishing = True
            self._condition.notify_all()
            while not self._updates_finished and not self._stop.is_set() and time.monotonic() < deadline:
                self._condition.wait(max(0., deadline-time.monotonic()))
            complete = self._updates_finished and not self._fault
        if not complete:
            self._fail("continuous_alignment_drain_incomplete")
        return complete

    def stop(self):
        self._stop.set()
        with self._condition:
            if self._pending_draft:
                self._counts["pending_drafts_cancelled_on_stop"] += 1
                self._pending_draft = None
            self._condition.notify_all()
        self.coordinator.invalidate("alignment_observer_stopped")
        engine_closed = self.engine.stop() if self.engine else True
        if self._thread:
            self._thread.join(2.)
        if self._updates_thread and self._updates_thread.ident is not None:
            self._updates_thread.join(2.)
        anchors_closed = self.anchors.close() if self.anchors else True
        with self._lock:
            alive = bool(self._thread and self._thread.is_alive())
            active = alive or self._observe_lock.locked() or not engine_closed or not anchors_closed or bool(self._updates_thread and self._updates_thread.is_alive())
            self._state = "shutdown_incomplete" if active else "failed" if self._fault else "stopped"
        return not active

    def status(self):
        snapshot = self.coordinator.status()
        worker = self.engine.status() if self.engine else None
        native = self.session.native.status()
        anchors = self.anchors.status() if self.anchors else {"enabled": False}
        source_ok = (self.session.status()["state"] not in {"failed", "stopped", "shutdown_incomplete"}
                     and self.session.native.status()["status"] not in {"failed", "stopped", "shutdown_incomplete"})
        with self._lock:
            estimate = self._current_update.get("current_estimate") if self._current_update else None
            current = (estimate is not None and source_ok and not self._stop.is_set()
                and (not self.anchors or not anchors.get("busy") and not anchors.get("error")
                    and native.get("native_returncode") is None and native.get("last_sensor_timestamp_ns") == snapshot["posterior_timestamp_ns"])
                and (worker is None or worker["process_alive"] and not worker["error"])
                and estimate["pose_timestamp_ns"] == snapshot["posterior_timestamp_ns"]
                and sorted(estimate["fix_ids"]) == snapshot["ready_query_ids"]
                and self._current_update["map_evidence_timestamp_ns"] == snapshot["latest_map_evidence_timestamp_ns"]
                and snapshot["ready_queries"] > 0 and snapshot["snapshot_received_host_monotonic_ns"] is not None
                and time.monotonic_ns()-snapshot["snapshot_received_host_monotonic_ns"] <= self.max_age)
            return {"schema_version": "vision_nav_live_research_alignment_status_v1", "state": self._state,
                "error": self._fault, "thread_alive": bool(self._thread and self._thread.is_alive()),
                "observation_running": self._observe_lock.locked(), "counts": dict(self._counts),
                "config": deepcopy(self._config_record), "max_snapshot_receipt_age_ns": self.max_age,
                "max_refresh_attempts": self.max_attempts, "snapshot_poll_interval_s": self.interval,
                "uncertainty_mode": ("isolated_research_providers" if self.engine else
                    "explicit_research_providers" if self.attempt_noise else "audit_only_no_noise"),
                "estimator_worker": worker,
                "query_anchors": anchors,
                "continuous_refresh": {"enabled": self.refresh_interval is not None,
                    "interval_s": self.refresh_interval, "thread_alive": bool(self._updates_thread and self._updates_thread.is_alive()),
                    "busy": self._updates_busy, "pending_drafts": int(self._pending_draft is not None),
                    "finished": self._updates_finished, "event_count": self._update_count,
                    "evidence_path": str(self._updates_path) if self._updates_path else None,
                    "last_event": deepcopy(self._last_update),
                    "latest_snapshot_estimate": deepcopy(estimate) if current else None,
                    "latest_snapshot_evidence": {"event_sequence": self._current_update["event_sequence"],
                        "state": self._current_update["evidence_state"]} if current else None,
                    "estimate_scope": "latest_snapshot_at_status_check_only"},
                "provider_hard_timeout": self.engine is not None, "position_authority": False, "navigation_mode": "NO_GLOBAL_FIX",
                "px4_external_vision": "disabled", "covariance_calibrated": False}
