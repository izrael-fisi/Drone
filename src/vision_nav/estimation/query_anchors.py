"""Own native frame anchors for asynchronous learned queries, never sensors."""

from collections import Counter
from copy import deepcopy
import json
from pathlib import Path
import threading
import time

from ..vio.live_worker import LiveFrameTicket
from ..vio.openvins_replay import digest
from ..vio.anchor_control import AnchorInputClosed


class QueryAnchorOwner:
    """Serialized retain/release and bounded evidence; monitor owns snapshots.

    Two accepted anchors leave two slots for pending/in-flight work. Acceptance
    is conditional research geometry, not a navigation or covariance guarantee.
    Native command waits never hold the coordinator or sensor ingestion locks.
    """

    def __init__(self, native, coordinator, *, snapshot_wait_s=1.):
        if (native.anchor_capacity != 4 or not 0 < native.timeout <= 30
                or type(snapshot_wait_s) not in (int, float) or not .01 <= snapshot_wait_s <= 5):
            raise ValueError("Query ownership requires four native anchors and bounded waits")
        if coordinator.worker_run_id != native.worker_run_id or coordinator.max_accepted_age is None:
            raise ValueError("Query ownership requires matching epoch and accepted-evidence horizon")
        self.native, self.coordinator = native, coordinator
        self.wait_s = snapshot_wait_s
        self._lock = threading.RLock()
        self._entries, self._counts = {}, Counter()
        self._path = self._stream = None
        self._last_ns, self._closed, self._fault = 0, False, None
        self._source_sha = digest(Path(__file__))

    def start(self, path):
        with self._lock:
            if self._stream is not None or self._closed:
                raise RuntimeError("Query anchor owner cannot restart")
            self._path = Path(path).resolve()
            self._stream = self._path.open("x")
            self._event("started", "query_anchor_ownership_enabled", capacity=4,
                accepted_capacity=2, query_max_age_ns=self.coordinator.max_age,
                accepted_evidence_max_age_ns=self.coordinator.max_accepted_age,
                source_sha256=self._source_sha)

    def _event(self, status, reason, **fields):
        event = {"schema_version": "vision_nav_query_anchor_event_v1",
            "worker_run_id": self.native.worker_run_id, "status": status, "reason": reason,
            "recorded_host_monotonic_ns": time.monotonic_ns(), "position_authority": False,
            "navigation_mode": "NO_GLOBAL_FIX", "px4_external_vision": "disabled", **fields}
        line = json.dumps(event, sort_keys=True, allow_nan=False)+"\n"
        if self._stream is None or self._stream.closed:
            raise RuntimeError("Query ownership evidence is not open")
        if len(line.encode()) > 16*1024 or self._stream.tell()+len(line.encode()) > 32*1024*1024:
            raise ValueError("Query ownership evidence exceeds its byte budget")
        self._stream.write(line)
        self._stream.flush()
        self._counts[reason] += 1
        return event

    def _ticket(self, item):
        ticket = item["tickets"][-1]
        if (not isinstance(ticket, LiveFrameTicket) or ticket.worker_run_id != self.native.worker_run_id
                or item.get("worker_run_id") != self.native.worker_run_id):
            raise ValueError("Query requires an issued frame from this native epoch")
        return ticket

    def _command(self, entry, operation):
        # Keep a sent receipt until settled. A caller timeout is not cancellation.
        receipt = entry.get("receipt")
        if receipt is None:
            receipt = getattr(self.native, operation+"_frame")(entry["frame"])
            if receipt is None:
                return None
            entry["receipt"], entry["operation"] = receipt, operation
            self._event("submitted", "native_"+operation+"_submitted", request=receipt.request,
                query_id=entry.get("query", {}).get("query_id"))
        if entry["operation"] != operation:
            raise RuntimeError("Unsettled native command must be resolved before another operation")
        ack = receipt.result(self.native.timeout+1.)
        self._event("acknowledged", "native_"+operation+"_settled", acknowledgement=ack,
            query_id=entry.get("query", {}).get("query_id"))
        entry.pop("receipt")
        entry.pop("operation")
        if ack["status"] == "indeterminate":
            entry["uncertain"] = True
            raise RuntimeError("Native anchor command outcome is indeterminate")
        return ack

    def _retire(self, ns, reason):
        entry = self._entries[ns]
        query = entry.get("query")
        if query:
            self.coordinator.retire_query(query["query_id"], reason)
        entry["retiring"] = reason
        native = self.native.status()
        if native.get("native_returncode") is not None and not native["thread_alive"]:
            self._event("released", "native_epoch_exited", timestamp_ns=ns,
                query_id=query["query_id"] if query else None, retirement_reason=reason,
                native_returncode=native["native_returncode"])
            del self._entries[ns]
            return
        if entry.get("uncertain"):
            raise RuntimeError("Uncertain ownership requires confirmed native epoch exit")
        if entry.get("receipt") is not None and entry["operation"] == "retain":
            ack = self._command(entry, "retain")
            entry["retained"] = ack["status"] == "applied"
        if entry.get("retained"):
            if native.get("input_closed"):
                return self._retire_after_exit(ns, reason)
            try:
                ack = self._command(entry, "release")
            except AnchorInputClosed:
                return self._retire_after_exit(ns, reason)
            if ack is None or ack["status"] == "not_applied":
                return  # Retry only an explicitly unsent operation on maintenance.
            if ack["status"] != "applied":
                raise RuntimeError("Owned native anchor release was rejected")
        self._event("released", reason, timestamp_ns=ns, query_id=query["query_id"] if query else None)
        del self._entries[ns]

    def _retire_after_exit(self, ns, reason):
        # Only the sensor owner may close native input; unsent is not released.
        self.native.join(self.native.timeout+1.)
        native = self.native.status()
        if native.get("native_returncode") is not None and not native["thread_alive"]:
            return self._retire(ns, reason)
        raise RuntimeError("Native input closed without confirmed epoch exit")

    def reconcile(self):
        with self._lock:
            state = self.coordinator.status()
            jobs = {q["query_id"]: q for q in state["queries"]}
            for ns, entry in list(self._entries.items()):
                query = entry.get("query")
                if query and query["query_id"] in state["accepted_query_ids"] and not entry.get("accepted"):
                    entry["accepted"] = True
                    self._event("accepted", "query_conditionally_accepted", timestamp_ns=ns,
                        query_id=query["query_id"], accepted_host_monotonic_ns=jobs[query["query_id"]]["accepted_host_monotonic_ns"])
                if entry.get("retiring") or query and query["query_id"] not in jobs:
                    reason = "coordinator_query_retired"
                    if state["fault"]:
                        reason = "coordinator_epoch_invalidated"
                    elif ns not in state["retained_camera_timestamps_ns"]:
                        reason = "camera_state_expired"
                    elif entry.get("accepted") and state["posterior_timestamp_ns"]-ns > self.coordinator.max_accepted_age:
                        reason = "accepted_evidence_horizon_exceeded"
                    elif not entry.get("accepted") and query and time.monotonic_ns()-query["dispatched_host_monotonic_ns"] > self.coordinator.max_age:
                        reason = "query_deadline_exceeded"
                    self._retire(ns, entry.get("retiring") or reason)
            accepted = sorted(ns for ns, e in self._entries.items()
                if e.get("query", {}).get("query_id") in state["accepted_query_ids"] and not e.get("retiring"))
            for ns in accepted[:-2]:
                self._retire(ns, "accepted_anchor_capacity_retirement")
            return state

    def acquire(self, item):
        with self._lock:
            if self._closed or self._fault:
                return self._event("withheld", "query_anchor_owner_unavailable")
            if digest(Path(__file__)) != self._source_sha:
                raise RuntimeError("Query ownership source changed during session")
            self.reconcile()
            frame = self._ticket(item)
            ns = frame.timestamp_ns
            if ns <= self._last_ns:
                return self._event("withheld", "duplicate_or_reordered_query_frame", timestamp_ns=ns)
            self._last_ns = ns
            if len(self._entries) >= 4:
                return self._event("withheld", "query_anchor_capacity_reached", timestamp_ns=ns)
            native = self.native.status()
            # Reserve command budget for every retained pose's eventual release.
            if native["anchor_control"]["submitted"]+len(self._entries)+2 > 1024:
                return self._event("withheld", "query_anchor_command_budget_reserved", timestamp_ns=ns)
            if native.get("input_closed") or native.get("native_returncode") is not None:
                return self._event("withheld", "native_input_not_accepting_queries", timestamp_ns=ns)
            dispatched = time.monotonic_ns()
            deadline = time.monotonic()+self.wait_s
            while ns not in self.coordinator.status()["retained_camera_timestamps_ns"]:
                if time.monotonic() >= deadline or self.coordinator.status()["fault"]:
                    return self._event("withheld", "query_frame_not_in_native_snapshot", timestamp_ns=ns)
                time.sleep(.005)
            entry = {"frame": frame, "retained": False}
            self._entries[ns] = entry
            try:
                ack = self._command(entry, "retain")
            except AnchorInputClosed:
                del self._entries[ns]
                return self._event("withheld", "native_input_not_accepting_queries", timestamp_ns=ns)
            if ack is None or ack["status"] != "applied":
                if ack and ack.get("reason") == "already_retained":
                    raise RuntimeError("Native anchor is owned outside the query lifecycle")
                del self._entries[ns]
                return self._event("withheld", "query_anchor_not_acquired", timestamp_ns=ns, acknowledgement=ack)
            entry["retained"] = True
            deadline = time.monotonic()+self.wait_s
            while ns not in self.coordinator.status()["passive_anchor_timestamps_ns"]:
                if time.monotonic() >= deadline or self.coordinator.status()["fault"]:
                    self._retire(ns, "retained_anchor_snapshot_unavailable")
                    return self._event("withheld", "retained_anchor_snapshot_unavailable", timestamp_ns=ns)
                time.sleep(.005)
            query = self.coordinator.begin_query(ns, frame.pixel_sha256,
                dispatched_host_monotonic_ns=dispatched)
            if query["status"] != "dispatched":
                self._retire(ns, query["reason"])
                return self._event("withheld", query["reason"], timestamp_ns=ns)
            entry["query"] = query["ticket"]
            return self._event("dispatched", "query_anchor_acquired_before_inference", ticket=query["ticket"])

    def query_for(self, context):
        with self._lock:
            entry = self._entries.get(context["timestamp_ns"])
            if (entry is None or entry.get("retiring") or not entry.get("query")
                    or entry["query"]["frame_sha256"] != context["frame_sha256"]):
                return None
            if entry["query"]["query_id"] not in {q["query_id"] for q in self.coordinator.status()["queries"]}:
                return None
            return deepcopy(entry["query"])

    def reject(self, timestamp_ns, reason):
        with self._lock:
            if timestamp_ns in self._entries:
                self._retire(timestamp_ns, reason)

    def close(self):
        if not self._lock.acquire(timeout=self.native.timeout+2.):
            return False
        try:
            if self._closed:
                return not self._entries
            for ns in list(self._entries):
                try:
                    self._retire(ns, "query_owner_stopped")
                except (RuntimeError, TimeoutError, ValueError) as exc:
                    self._fault = str(exc)[:256]
            if self._entries:
                self._event("withheld", "query_owner_cleanup_incomplete", timestamps_ns=sorted(self._entries))
                return False
            self._closed = True
            if self._stream:
                self._event("closed", "all_owned_queries_retired")
                self._stream.close()
            return True
        finally:
            self._lock.release()

    def status(self):
        # UI/status must not wait for native control acknowledgements.
        if not self._lock.acquire(blocking=False):
            return {"enabled": True, "busy": True, "position_authority": False}
        try:
            return {"enabled": True, "busy": False, "closed": self._closed, "error": self._fault,
                "owned_timestamps_ns": sorted(self._entries), "counts": dict(self._counts),
                "evidence_path": str(self._path) if self._path else None,
                "evidence_sha256": digest(self._path) if self._closed and self._path else None,
                "position_authority": False}
        finally:
            self._lock.release()
