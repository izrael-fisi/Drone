"""Bound delayed PnP results to native VIO snapshots, without navigation authority."""

from __future__ import annotations

from collections import Counter
from copy import deepcopy
from dataclasses import asdict
import re
import threading
import time
import uuid

from .correlated_window import WindowAlignmentConfig, payload_digest
from .bounded_json import owned as _owned
from .pnp_window_alignment import align
from .pose_smoother import timestamp
from vision_nav.vio.pose_window import JointPoseWindow


class DelayedPnPAlignment:
    """One worker epoch, latest joint posterior, bounded outstanding frame jobs.

    The caller supplies qualified clock/provenance and joint uncertainty. No
    old snapshots become new factors, and no unknown cross-covariance is filled.
    A solve returns a historical research estimate at its original sensor time.
    """

    def __init__(self, config, worker_run_id, *, capacity=8, max_query_age_ns=10_000_000_000,
                 monotonic_ns=time.monotonic_ns, alignment_solver=None, max_accepted_age_ns=None):
        cfg = WindowAlignmentConfig(**config)
        if any(cfg.map_common_left_covariance.flat):
            raise ValueError("PnP owns map error; no additional map covariance floor")
        self._config = asdict(cfg)
        self._config["imu_T_camera"] = cfg.imu_T_camera.tolist()
        self._config["map_common_left_covariance"] = cfg.map_common_left_covariance.tolist()
        self.worker_run_id = str(uuid.UUID(worker_run_id))
        if type(capacity) is not int or not 1 <= capacity <= 16:
            raise ValueError("Expected 1..16 outstanding frame jobs")
        if type(max_query_age_ns) is not int or not 0 < max_query_age_ns <= 60_000_000_000:
            raise ValueError("Query lifetime must be positive and at most 60 seconds")
        if max_accepted_age_ns is not None and (type(max_accepted_age_ns) is not int or not 0 < max_accepted_age_ns <= 300_000_000_000):
            raise ValueError("Accepted evidence horizon must be positive and at most 300 seconds")
        self.capacity, self.max_age = capacity, max_query_age_ns
        self.max_accepted_age = max_accepted_age_ns
        self.epoch_id = str(uuid.uuid4())
        self._clock, self._lock = monotonic_ns, threading.RLock()
        self._last_host = self._last_query = self._sequence = 0
        self._record = self._window = self._prepared = None
        self._snapshot_sha256 = None
        self._received_ns = self._fault = None
        self._jobs, self._counts = {}, Counter()
        self._solving = False
        self._refresh_lock = threading.Lock()
        if alignment_solver is not None and not callable(alignment_solver):
            raise ValueError("Alignment solver must be callable")
        self._solver = alignment_solver

    def _now(self):
        now = timestamp(self._clock())
        if now < self._last_host:
            self._invalidate("host_clock_regressed")
            raise ValueError("Host monotonic clock regressed")
        self._last_host = now
        return now

    def _event(self, status, reason, **fields):
        self._counts[reason.split(":", 1)[0]] += 1
        return {"schema_version": "vision_nav_delayed_alignment_v1", "epoch_id": self.epoch_id,
                "worker_run_id": self.worker_run_id, "status": status, "reason": reason,
                "position_authority": False, "navigation_mode": "NO_GLOBAL_FIX",
                "px4_external_vision": "disabled", "covariance_calibrated": False, **fields}

    def _invalidate(self, reason):
        self._fault = reason
        self._record = self._window = self._prepared = None
        self._snapshot_sha256 = None
        self._jobs.clear()

    def invalidate(self, reason):
        """On sensor/worker failure or reset, retire this epoch permanently."""
        if not isinstance(reason, str) or not reason.strip() or len(reason) > 256:
            raise ValueError("A bounded failure reason is required")
        with self._lock:
            self._invalidate(reason)
            return self._event("withheld", "epoch_invalidated", detail=reason)

    def _expire(self, now):
        retired = []
        for identity, job in list(self._jobs.items()):
            reason = None
            if job["ticket"]["timestamp_ns"] not in self._window.timestamps_ns:
                reason = "camera_state_expired"
            elif job.get("accepted_host_monotonic_ns") is not None and self.max_accepted_age is not None:
                if self._window.posterior_ns-job["ticket"]["timestamp_ns"] > self.max_accepted_age:
                    reason = "accepted_evidence_horizon_exceeded"
            elif now - job["ticket"]["dispatched_host_monotonic_ns"] > self.max_age:
                reason = "query_deadline_exceeded"
            if reason:
                del self._jobs[identity]
                retired.append({"query_id": identity, "reason": reason})
                self._counts[reason] += 1
        if self._prepared and any(f["fix_id"] not in self._jobs for f in self._prepared["request"]["fixes"]):
            self._prepared = None
        return retired

    def retire_query(self, query_id, reason):
        """Explicit owner retirement invalidates drafts and in-flight solve use."""
        if not isinstance(reason, str) or not reason or len(reason) > 256:
            raise ValueError("A bounded retirement reason is required")
        with self._lock:
            self._now()
            job = self._jobs.pop(query_id, None)
            if job is None:
                return self._event("withheld", "unknown_or_retired_query")
            if self._prepared and any(f["fix_id"] == query_id for f in self._prepared["request"]["fixes"]):
                self._prepared = None
            return self._event("retired", reason, query_id=query_id, timestamp_ns=job["ticket"]["timestamp_ns"])

    def update_snapshot(self, snapshot):
        """Consume NativeVioStreamWorker.take_latest(), with a checked run ID.

        Missing/non-local windows are a discontinuity, not permission to reuse
        the last pose. The owning service must create a new epoch after recovery.
        """
        with self._lock:
            now = self._now()
            if self._fault:
                return self._event("withheld", "epoch_invalidated")
            try:
                owned = _owned(snapshot, 2*1024*1024)
                if not isinstance(owned, dict) or not isinstance(owned.get("record"), dict):
                    raise ValueError("Expected a native worker snapshot object")
                if owned.get("worker_run_id") != self.worker_run_id or owned.get("position_authority") is not False:
                    raise ValueError("Worker epoch/authority mismatch")
                received = timestamp(owned["received_host_monotonic_ns"])
                if received > now or (self._received_ns is not None and received < self._received_ns):
                    raise ValueError("Invalid native receipt clock")
                window = JointPoseWindow.from_record(owned["record"])
                if self._window and window.posterior_ns <= self._window.posterior_ns:
                    raise ValueError("Non-increasing VIO posterior; explicit reset required")
                snapshot_sha256 = payload_digest(owned["record"])
            except (ValueError, KeyError, TypeError, OverflowError, RecursionError) as exc:
                self._invalidate("invalid_vio_snapshot")
                return self._event("withheld", "invalid_vio_snapshot", detail=str(exc)[:256])
            self._record, self._window, self._received_ns = owned["record"], window, received
            # The private owned record is only replaced, never mutated or exposed.
            self._snapshot_sha256 = snapshot_sha256
            retired = self._expire(now)
            return self._event("updated", "joint_snapshot_replaced", posterior_timestamp_ns=window.posterior_ns,
                               retired_queries=retired)

    def begin_query(self, timestamp_ns, frame_sha256, *, dispatched_host_monotonic_ns=None):
        with self._lock:
            now = self._now()
            if self._fault or self._window is None:
                return self._event("withheld", "no_usable_vio_snapshot")
            self._expire(now)
            ns = timestamp(timestamp_ns)
            dispatched = now if dispatched_host_monotonic_ns is None else timestamp(dispatched_host_monotonic_ns)
            if dispatched > now:
                raise ValueError("Inference dispatch cannot be in the future")
            if now-dispatched > self.max_age:
                return self._event("withheld", "query_deadline_exceeded_before_binding")
            if not isinstance(frame_sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", frame_sha256):
                raise ValueError("Exact frame SHA-256 required")
            if ns not in self._window.timestamps_ns:
                return self._event("withheld", "camera_state_not_retained")
            if ns <= self._last_query:
                return self._event("withheld", "duplicate_or_reordered_query")
            if len(self._jobs) >= self.capacity:
                return self._event("withheld", "query_capacity_reached")
            self._sequence += 1
            identity = f"{self.epoch_id}:{self._sequence}"
            ticket = {"query_id": identity, "epoch_id": self.epoch_id, "timestamp_ns": ns,
                      "frame_sha256": frame_sha256, "dispatched_host_monotonic_ns": dispatched}
            self._jobs[identity] = {"ticket": ticket, "request": None, "accepted_host_monotonic_ns": None}
            self._last_query = ns
            return self._event("dispatched", "exact_camera_state_bound", ticket=deepcopy(ticket))

    def complete_query(self, ticket, pnp_request):
        with self._lock:
            now = self._now()
            if self._fault or self._window is None:
                return self._event("withheld", "no_usable_vio_snapshot")
            retired = self._expire(now)
            identity = ticket.get("query_id") if isinstance(ticket, dict) else None
            job = self._jobs.get(identity) if isinstance(identity, str) else None
            if job is None:
                return self._event("withheld", "unknown_or_retired_query", retired_queries=retired)
            if job["ticket"] != ticket:
                return self._event("withheld", "query_ticket_mismatch")
            if job["request"] is not None:
                return self._event("withheld", "duplicate_query_result")
            try:
                request = _owned(pnp_request, 2*1024*1024)
                if (not isinstance(request, dict) or not isinstance(request.get("context"), dict)
                        or not isinstance(request.get("attempt"), dict)
                        or not isinstance(request.get("uncertainty"), dict)):
                    raise ValueError("Expected PnP request/context/attempt/uncertainty objects")
                if not isinstance(request["attempt"].get("geometry"), dict):
                    raise ValueError("Expected a PnP geometry object")
                context = request["context"]
                if (request.get("schema_version") != "vision_nav_pnp_uncertainty_request_v1"
                        or type(context.get("timestamp_ns")) is not int
                        or context.get("timestamp_ns") != ticket["timestamp_ns"]
                        or context.get("frame_sha256") != ticket["frame_sha256"]
                        or any(context.get(k) != self._config[k] for k in
                               ("map_release_id", "model_release_id", "calibration_id", "clock_id", "uncertainty_model_id"))):
                    raise ValueError("Frame/time/release binding mismatch")
                pixels = request["attempt"]["matched_query_pixels"]
                if not isinstance(pixels, list) or not 6 <= len(pixels) <= 128:
                    raise ValueError("Expected 6..128 PnP input correspondences")
            except (ValueError, KeyError, TypeError, OverflowError, RecursionError) as exc:
                del self._jobs[identity]
                return self._event("withheld", "invalid_pnp_binding", detail=str(exc)[:256])
            job["request"] = request
            return self._event("ready", "pnp_result_bound_not_yet_validated", query_id=identity,
                               query_age_ms=(now-ticket["dispatched_host_monotonic_ns"])/1e6)

    def prepare(self):
        """Freeze one exact request so a caller can supply its joint noise model."""
        return self._prepare(export=True)

    def _prepare(self, *, export):
        with self._lock:
            now = self._now()
            if self._fault or self._window is None:
                return self._event("withheld", "no_usable_vio_snapshot")
            self._expire(now)
            if self._solving:
                return self._event("withheld", "alignment_already_running")
            fixes = [{"fix_id": key, "request": job["request"]} for key, job in self._jobs.items()
                     if job["request"] is not None]
            fixes.sort(key=lambda f: f["request"]["context"]["timestamp_ns"])
            if not fixes:
                return self._event("withheld", "no_completed_retained_queries")
            # No partial selection: callers must tune keyframe rate/capacity.
            dimension = sum(5*len(f["request"]["attempt"]["matched_query_pixels"])+4 for f in fixes)
            if dimension > 1024:
                return self._event("withheld", "joint_input_dimension_exceeded")
            request = deepcopy({"schema_version": "vision_nav_joint_pnp_alignment_request_v1",
                                "native_record": self._record, "config": self._config, "fixes": fixes})
            prepared = {"draft_id": str(uuid.uuid4()), "request": request,
                        "request_sha256": payload_digest(request), "prepared_host_monotonic_ns": now,
                        "native_record_sha256": self._snapshot_sha256}
            self._prepared = prepared
            # Internal refresh owns this draft until solve; only public callers
            # need another isolated copy of the already-private request.
            return self._event("prepared", "explicit_joint_uncertainty_required",
                               **(deepcopy(prepared) if export else prepared))

    def solve(self, draft_id, joint_uncertainty=None, *, joint_executor=None):
        """Solve outside the ingestion lock; no feedback or persistent graph prior."""
        if joint_executor is not None and (not callable(joint_executor) or joint_uncertainty is not None):
            raise ValueError("Use explicit noise or one combined joint executor, not both")
        with self._lock:
            now = self._now()
            if self._fault or self._window is None:
                return self._event("withheld", "no_usable_vio_snapshot")
            self._expire(now)
            if self._solving or self._prepared is None or self._prepared["draft_id"] != draft_id:
                return self._event("withheld", "unknown_or_retired_draft")
            draft, self._prepared = self._prepared, None
            request = draft["request"]
            deadlines = [self._jobs[f["fix_id"]]["ticket"]["dispatched_host_monotonic_ns"] + self.max_age
                         for f in request["fixes"] if self.max_accepted_age is None
                         or self._jobs[f["fix_id"]]["accepted_host_monotonic_ns"] is None]
            self._solving = True
        try:
            if joint_executor is not None:
                response = joint_executor(deepcopy(request))
                if not isinstance(response, dict) or response.get("input_request_sha256") != payload_digest(request):
                    raise ValueError("Combined alignment input identity mismatch")
                joint_uncertainty = response["joint_uncertainty"]
            request["joint_uncertainty"] = _owned(joint_uncertainty, 32*1024*1024)
            if not isinstance(request["joint_uncertainty"], dict):
                raise ValueError("Expected an explicit joint uncertainty object")
            if joint_executor is not None:
                result = _owned(response["result"], 32*1024*1024)
                if (not isinstance(result, dict) or result.get("request_sha256") != payload_digest(request)
                        or result.get("schema_version") != "vision_nav_joint_pnp_alignment_v1"
                        or result.get("position_authority") is not False
                        or result.get("navigation_mode") != "NO_GLOBAL_FIX"
                        or result.get("px4_external_vision") != "disabled"):
                    raise ValueError("Combined result must bind the exact noise/request and shadow policy")
            else:
                result = (self._solver or align)(request)
                if payload_digest(request["native_record"]) != draft["native_record_sha256"]:
                    raise ValueError("Alignment callback changed the bound native snapshot")
            error = None
        except (ValueError, KeyError, TypeError, RuntimeError, OverflowError) as exc:
            result, error = None, f"{type(exc).__name__}: {exc}"[:256]
        finally:
            with self._lock:
                self._solving = False
        with self._lock:
            finished = self._now()
            base = {"draft_id": draft_id, "request_sha256": payload_digest(request),
                    "started_host_monotonic_ns": now, "finished_host_monotonic_ns": finished,
                    "sensor_clock_is_host_clock": False, "result": result,
                    "snapshot_policy": "replace_do_not_accumulate", "estimate_scope": "historical_snapshot_only"}
            if self._fault:
                return self._event("withheld", "epoch_invalidated_during_alignment", **base)
            if deadlines and finished > min(deadlines):
                self._expire(finished)
                return self._event("withheld", "query_deadline_exceeded_during_alignment", **base)
            self._expire(finished)
            if any(f["fix_id"] not in self._jobs for f in request["fixes"]):
                return self._event("withheld", "queries_retired_during_alignment", **base)
            if error:
                return self._event("withheld", "invalid_joint_alignment", detail=error, **base)
            base["snapshot_is_latest"] = self._snapshot_sha256 == draft["native_record_sha256"]
            base["latest_vio_timestamp_ns"] = self._window.posterior_ns
            if result["status"] == "research_alignment":
                over_horizon = [f["fix_id"] for f in request["fixes"] if self.max_accepted_age is not None
                    and self._window.posterior_ns-self._jobs[f["fix_id"]]["ticket"]["timestamp_ns"] > self.max_accepted_age]
                if over_horizon:
                    for identity in over_horizon:
                        self.retire_query(identity, "accepted_evidence_horizon_exceeded")
                    return self._event("withheld", "accepted_evidence_horizon_exceeded", **base)
                for fix in request["fixes"]:
                    job = self._jobs[fix["fix_id"]]
                    if job["accepted_host_monotonic_ns"] is None:
                        job["accepted_host_monotonic_ns"] = finished
                        self._counts["query_conditionally_accepted"] += 1
            return self._event(result["status"], result["reason"], **base)

    def refresh_current(self, uncertainty_provider=None, *, joint_executor=None, max_snapshot_age_ns, max_attempts=2):
        """Re-solve original map evidence against the latest retained posterior.

        The provider receives an owned alignment request and must return its
        exact joint input noise model, including ALL current VIO cross blocks.
        It runs outside the sensor lock. Attempts are bounded, not execution
        time: this synchronous research API does not isolate a hung provider.
        'Current' means latest snapshot at the final check, not flight freshness.
        """
        if joint_executor is not None and (not callable(joint_executor) or uncertainty_provider is not None):
            raise ValueError("Use one uncertainty provider or one combined joint executor")
        if joint_executor is None and not callable(uncertainty_provider):
            raise ValueError("An explicit snapshot-specific uncertainty provider is required")
        if type(max_snapshot_age_ns) is not int or not 0 < max_snapshot_age_ns <= 10_000_000_000:
            raise ValueError("Explicit snapshot receipt-age limit must be positive and at most 10 seconds")
        if type(max_attempts) is not int or not 1 <= max_attempts <= 4:
            raise ValueError("Expected 1..4 current-snapshot solve attempts")
        attempts = []

        def finish(status, reason, **fields):
            return self._event(status, reason, refresh_attempts=deepcopy(attempts),
                current_estimate=None, current_scope="latest_snapshot_at_check_only",
                snapshot_policy="replace_do_not_accumulate", **fields)

        if not self._refresh_lock.acquire(blocking=False):
            with self._lock:
                return finish("withheld", "current_refresh_already_running")
        try:
            for _ in range(max_attempts):
                with self._lock:
                    now = self._now()
                    if self._fault or self._window is None:
                        return finish("withheld", "no_usable_vio_snapshot")
                    if now - self._received_ns > max_snapshot_age_ns:
                        return finish("withheld", "vio_snapshot_receipt_stale")
                    if self._window.timestamps_ns[-1] != self._window.posterior_ns:
                        return finish("withheld", "posterior_has_no_exact_pose")
                    draft = self._prepare(export=False)
                    if draft["status"] != "prepared":
                        return finish("withheld", draft["reason"])
                if joint_executor is not None:
                    result = self.solve(draft["draft_id"], joint_executor=joint_executor)
                else:
                    try:
                        noise = uncertainty_provider(deepcopy(draft["request"]))
                    except Exception as exc:
                        with self._lock:
                            if self._prepared and self._prepared["draft_id"] == draft["draft_id"]:
                                self._prepared = None
                            return finish("withheld", "joint_uncertainty_provider_failed",
                                          detail=f"{type(exc).__name__}: {exc}"[:256])
                    result = self.solve(draft["draft_id"], noise)
                attempts.append({"draft_id": draft["draft_id"],
                    "snapshot_sha256": draft["native_record_sha256"],
                    "posterior_timestamp_ns": draft["request"]["native_record"]["timestamp_ns"],
                    "request_sha256": result.get("request_sha256"),
                    "status": result["status"], "reason": result["reason"]})
                with self._lock:
                    checked = self._now()
                    if self._fault or self._window is None:
                        return finish("withheld", "epoch_invalidated_during_refresh")
                    if checked - self._received_ns > max_snapshot_age_ns:
                        return finish("withheld", "vio_snapshot_receipt_stale")
                    # Never advance an old solution by attaching a new timestamp.
                    # Rebuild from original PnP evidence and the NEW joint model.
                    if self._snapshot_sha256 != attempts[-1]["snapshot_sha256"]:
                        continue
                    if result["status"] != "research_alignment":
                        return finish("withheld", result["reason"], last_alignment=result)
                    estimate = result["result"]["alignment"]
                    if estimate["pose_timestamp_ns"] != self._window.posterior_ns:
                        return finish("withheld", "posterior_has_no_exact_pose")
                    output = finish("research_current_alignment", "latest_joint_snapshot_resolved",
                        checked_host_monotonic_ns=checked,
                        snapshot_received_host_monotonic_ns=self._received_ns,
                        snapshot_receipt_age_ns=checked-self._received_ns,
                        sensor_clock_is_host_clock=False, last_alignment=result)
                    output["current_estimate"] = deepcopy(estimate)
                    return output
            with self._lock:
                return finish("withheld", "current_refresh_attempt_budget_exhausted")
        finally:
            self._refresh_lock.release()

    def status(self):
        with self._lock:
            now = self._now()
            if self._window:
                self._expire(now)
            return {"schema_version": "vision_nav_delayed_alignment_status_v1",
                    "epoch_id": self.epoch_id, "worker_run_id": self.worker_run_id,
                    "fault": self._fault, "outstanding_queries": len(self._jobs),
                    "ready_queries": sum(j["request"] is not None for j in self._jobs.values()),
                    "ready_query_ids": sorted(k for k, j in self._jobs.items() if j["request"] is not None),
                    "accepted_query_ids": sorted(k for k, j in self._jobs.items() if j["accepted_host_monotonic_ns"] is not None),
                    "retained_camera_timestamps_ns": list(self._window.timestamps_ns) if self._window else [],
                    "passive_anchor_timestamps_ns": list(self._record["pose_window"].get("passive_anchor_timestamps_ns", [])) if self._record else [],
                    "query_max_age_ns": self.max_age, "accepted_evidence_max_age_ns": self.max_accepted_age,
                    "queries": [{"query_id": key, **deepcopy(job["ticket"]),
                        "state": "accepted" if job["accepted_host_monotonic_ns"] is not None else "ready" if job["request"] is not None else "pending",
                        "accepted_host_monotonic_ns": job["accepted_host_monotonic_ns"],
                        "evidence_age_at_posterior_ns": self._window.posterior_ns-job["ticket"]["timestamp_ns"] if self._window else None}
                        for key, job in self._jobs.items()],
                    "posterior_timestamp_ns": self._window.posterior_ns if self._window else None,
                    "snapshot_received_host_monotonic_ns": self._received_ns,
                    "latest_map_evidence_timestamp_ns": max((j["ticket"]["timestamp_ns"] for j in self._jobs.values()
                        if j["request"] is not None), default=None),
                    "alignment_running": self._solving, "current_refresh_running": self._refresh_lock.locked(),
                    "event_counts": dict(self._counts),
                    "navigation_mode": "NO_GLOBAL_FIX", "position_authority": False,
                    "px4_external_vision": "disabled"}
