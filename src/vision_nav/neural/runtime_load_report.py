"""Run-bound descriptive load reports, not navigation or model-promotion gates."""

import argparse
from collections import Counter, defaultdict
import hashlib
import json
import math
from pathlib import Path
from statistics import quantiles
from uuid import UUID

from .status_heartbeat import write_status_snapshot


def _number(value, *, nonnegative=True):
    if type(value) not in (int, float) or not math.isfinite(value) or nonnegative and value < 0:
        raise ValueError("Expected finite numeric measurement")
    return value


def _integer(value, *, minimum=0):
    if type(value) is not int or value < minimum:
        raise ValueError("Expected bounded nonnegative integer measurement")
    return value


def _object(value):
    if not isinstance(value, dict):
        raise ValueError("Expected measurement object")
    return value


def _json_object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("Duplicate JSON object key")
        result[key] = value
    return result


def _reject_constant(value):
    raise ValueError(f"Nonfinite JSON constant: {value}")


def distribution(values):
    if not values:
        return {"count": 0, "min": None, "p50": None, "p95": None, "p99": None, "max": None}
    cutoffs = quantiles(values, n=100, method="inclusive") if len(values) > 1 else [values[0]] * 99
    return {"count": len(values), "min": min(values), "p50": cutoffs[49],
            "p95": cutoffs[94], "p99": cutoffs[98], "max": max(values)}


class JsonlSource:
    def __init__(self, path: Path, *, max_line_bytes: int, max_records: int):
        self.path = path.resolve()
        self.max_line_bytes, self.max_records = max_line_bytes, max_records
        self.evidence = None

    def __iter__(self):
        before = self.path.stat()
        if not self.path.is_file() or before.st_size > 512 * 1024 * 1024:
            raise ValueError("Evidence must be a regular file no larger than 512 MiB")
        digest, count = hashlib.sha256(), 0
        with self.path.open("rb") as handle:
            while line := handle.readline(self.max_line_bytes + 1):
                if len(line) > self.max_line_bytes or count >= self.max_records:
                    raise ValueError("Evidence record count or line length exceeds report bounds")
                digest.update(line)
                try:
                    value = json.loads(line, parse_constant=_reject_constant, object_pairs_hook=_json_object,
                                       parse_float=lambda text: _number(float(text), nonnegative=False))
                except (ValueError, UnicodeError) as exc:
                    raise ValueError(f"Invalid JSONL record {count + 1} in {self.path.name}") from exc
                count += 1
                yield _object(value)
        after = self.path.stat()
        if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
            raise ValueError("Evidence changed during reporting; stop recording or use a stable copy")
        self.evidence = {"path": str(self.path), "sha256": digest.hexdigest(), "bytes": before.st_size, "records": count}


class NativeProcessMeasurements:
    """Child intervals are recomputed separately, never summed as unique RAM."""

    def __init__(self, header):
        self.requested = header.get("native_vio_measurement_requested", False)
        if type(self.requested) is not bool:
            raise ValueError("Invalid native VIO measurement policy")
        self.parent_pid = _integer(header.get("process_id"), minimum=1) if self.requested else None
        self.states, self.values, self.gaps = Counter(), defaultdict(list), set()
        self.identity = self.previous = None
        self.last_observed = None
        self.io_intervals, self.io_totals = 0, Counter()

    def add(self, sample, parent_now, parent_probe_ms):
        row = _object(sample)
        state = row.get("status", "missing")
        if state not in {"available", "unavailable", "not_configured", "missing"}:
            raise ValueError("Invalid child measurement status")
        if state != "missing" and row.get("position_authority") is not False:
            raise ValueError("Child resource measurements must be non-authoritative")
        if not self.requested and state not in {"missing", "not_configured"}:
            raise ValueError("Undeclared child process measurements")
        self.states[state] += 1
        if state != "available":
            self.previous = None
            if self.requested:
                self.gaps.add("native_vio_process_samples_incomplete")
            return
        if (row.get("schema_version") != "vision_nav_owned_process_resources_v1"
                or row.get("position_authority") is not False or row.get("role") != "native_vio"
                or row.get("cpu_scope") != "process_user_plus_system_all_threads_excludes_descendants"
                or row.get("rss_scope") != "current_process_rss_shared_pages_not_deduplicated"):
            raise ValueError("Unqualified child measurement scope")
        identity = _object(row.get("identity"))
        pid = _integer(identity.get("pid"), minimum=1)
        parent = _integer(identity.get("parent_pid"), minimum=1)
        born = _number(identity.get("created_at_epoch_s"))
        worker = identity.get("worker_run_id")
        if (born <= 0 or parent != self.parent_pid or pid == parent or not isinstance(worker, str)
                or str(UUID(worker)) != worker):
            raise ValueError("Invalid owned native-process identity")
        canonical = {"pid": pid, "parent_pid": parent, "created_at_epoch_s": born, "worker_run_id": worker}
        if self.identity is not None and canonical != self.identity:
            raise ValueError("Native process identity changed within resource recording")
        self.identity = canonical
        started = _integer(row.get("sample_started_host_monotonic_ns"), minimum=1)
        now = _integer(row.get("observed_host_monotonic_ns"), minimum=1)
        if (not parent_now - parent_probe_ms*1e6 - 1 <= started <= now <= parent_now
                or self.last_observed is not None and now <= self.last_observed):
            raise ValueError("Child observation clock lies outside its parent sampling bracket")
        self.last_observed = now
        cpu = _integer(row.get("process_cpu_ns"))
        self.values["rss_bytes"].append(_integer(row.get("rss_bytes")))
        self.values["probe_duration_ms"].append((now-started)/1e6)
        self.values["parent_sample_skew_ms"].append((parent_now-now)/1e6)
        io = _object(row.get("process_io"))
        keys = ("read_bytes", "write_bytes", "cancelled_write_bytes")
        if io.get("status") == "available":
            for key in keys:
                _integer(io.get(key))
        if self.previous:
            old_time, old_cpu, old_io = self.previous
            if cpu >= old_cpu:
                self.values["cpu_percent_one_core_scale"].append(100*(cpu-old_cpu)/(now-old_time))
            else:
                self.gaps.add("native_vio_cpu_counter_reversed")
            if io.get("status") == old_io.get("status") == "available":
                deltas = {key: io[key]-old_io[key] for key in keys}
                if all(v >= 0 for v in deltas.values()):
                    self.io_totals.update(deltas)
                    self.io_intervals += 1
                else:
                    self.gaps.add("native_vio_io_counter_reversed")
        self.previous = now, cpu, io

    def summary(self):
        count = sum(self.states.values())
        intervals = len(self.values["cpu_percent_one_core_scale"])
        if self.requested and (count < 2 or intervals != count-1):
            self.gaps.add("native_vio_cpu_intervals_incomplete")
        if self.requested and (count < 2 or self.io_intervals != count-1):
            self.gaps.add("native_vio_io_intervals_incomplete")
        return {"requested": self.requested, "identity": self.identity,
                "status_sample_counts": dict(self.states),
                "resource_distributions": {key: distribution(values) for key, values in sorted(self.values.items())},
                "process_io": {"observed_interval_count": self.io_intervals,
                               "interval_byte_totals": dict(self.io_totals), "physical_flash_writes": None},
                "parent_and_child_unique_rss_bytes": None, "parent_and_child_cpu_percent": None,
                "scope": "separate_native_child_measurements_not_atomic_combined_usage"}


class AttitudeMeasurements:
    """Summarize sampled reader health without claiming sensor synchronization."""

    def __init__(self):
        self.states, self.reasons, self.ages = Counter(), Counter(), []
        self.first = self.last = self.sender = None
        self.gaps = set()

    def add(self, snapshot):
        snapshot = _object(snapshot)
        state = snapshot.get("status", "missing")
        if state not in {"missing", "not_configured", "not_started", "starting", "waiting", "receiving",
                         "invalid", "stale", "failed", "stopped", "shutdown_timeout"}:
            raise ValueError("Invalid attitude health state")
        self.states[state] += 1
        if state in {"missing", "not_configured"}:
            self.gaps.add("attitude_context_measurements_incomplete")
            return
        if (snapshot.get("schema_version") != "vision_nav_attitude_context_v1"
                or snapshot.get("time_basis") != "host_receive_unqualified"
                or snapshot.get("sensor_timing_calibrated") is not False
                or snapshot.get("position_authority") is not False
                or snapshot.get("mavlink_transmission_enabled") is not False):
            raise ValueError("Expected read-only, unqualified attitude context")
        sender = tuple(_integer(snapshot.get(key), minimum=1) for key in ("target_system", "target_component"))
        if any(value > 255 for value in sender) or self.sender is not None and sender != self.sender:
            raise ValueError("Invalid or changed attitude sender in one run")
        self.sender = sender
        keys = ("received_attitude_count", "accepted_count", "filtered_source_count", "invalid_count",
                "overflow_count", "generation")
        counters = {key: _integer(snapshot.get(key)) for key in keys}
        if self.last and any(counters[key] < self.last[key] for key in keys):
            raise ValueError("Attitude counters reset within one runtime run")
        self.first, self.last = self.first or counters, counters
        age = snapshot.get("last_attitude_age_ms")
        if age is not None:
            self.ages.append(_number(age, nonnegative=False))
            if age < 0:
                self.gaps.add("attitude_receive_clock_reversed")
        if state == "receiving" and (age is None or age < 0 or age > _number(snapshot.get("stale_after_ms"))):
            raise ValueError("Receiving attitude state has invalid freshness evidence")
        if state in {"invalid", "stale", "failed", "shutdown_timeout"}:
            self.gaps.add("attitude_unhealthy_samples_observed")
        reason = snapshot.get("last_discontinuity_reason")
        if reason is not None:
            if not isinstance(reason, str) or len(reason) > 200 or len(self.reasons) >= 32 and reason not in self.reasons:
                raise ValueError("Invalid attitude discontinuity reason")
            self.reasons[reason] += 1

    def summary(self):
        if self.last is None or not self.last["accepted_count"] or not self.states["receiving"]:
            self.gaps.add("no_healthy_attitude_context_observed")
        return {"status_sample_counts": dict(self.states), "receive_age_ms": distribution(self.ages),
                "sender": {"system": self.sender[0], "component": self.sender[1]} if self.sender else None,
                "first_observed_counters": self.first, "last_observed_counters": self.last,
                "observed_counter_change": {key: self.last[key] - self.first[key] for key in self.last} if self.last else None,
                "last_discontinuity_reason_sample_counts": dict(self.reasons),
                "sensor_timing_calibrated": False, "raw_imu_rate_hz": None,
                "scope": "sampled_attitude_reader_health_not_raw_imu_or_camera_synchronization"}


def summarize_runtime_load(metrics_path: Path, decision_path: Path | None = None) -> dict:
    source = JsonlSource(metrics_path, max_line_bytes=65536, max_records=36002)
    rows = iter(source)
    header = next(rows, None)
    if not header or header.get("record_type") != "header" or header.get("schema_version") != "vision_nav_runtime_metrics_v1":
        raise ValueError("Runtime metrics header required")
    run_id = header.get("run_id")
    if not isinstance(run_id, str) or str(UUID(run_id)) != run_id or header.get("position_authority") is not False:
        raise ValueError("Invalid shadow metrics run identity")
    max_samples = _integer(header.get("max_samples"), minimum=1)
    if max_samples > 36000 or _number(header.get("interval_s")) <= 0:
        raise ValueError("Invalid recorder policy")
    hardware = _object(header.get("hardware"))
    if header.get("cpu_scope", "runtime_process_all_threads_excludes_child_processes") != "runtime_process_all_threads_excludes_child_processes":
        raise ValueError("Unsupported parent CPU accounting scope")
    native = NativeProcessMeasurements(header)
    values, phases, gaps = defaultdict(list), Counter(), set()
    bindings = {"map_release_id": set(), "model_release_id": set()}
    first_ns = last_ns = None
    previous_cpu = previous_io = None
    count, footer = 0, None
    camera_first = camera_last = None
    camera_samples = 0
    attitude = AttitudeMeasurements()
    throttle_current, throttle_latched = Counter(), set()
    throttle_baseline, throttle_baseline_index = None, None
    io_totals, io_intervals = Counter(), 0
    for row in rows:
        if footer is not None or row.get("run_id") != run_id or row.get("position_authority") is not False:
            raise ValueError("Mixed run, non-shadow data or records after footer")
        if row.get("record_type") == "footer":
            if _integer(row.get("sample_count")) != count or row.get("status") not in {"stopped", "sample_limit_reached"}:
                raise ValueError("Metrics footer does not match samples")
            footer_time = _integer(row.get("host_monotonic_ns"), minimum=1)
            if last_ns is not None and footer_time < last_ns:
                raise ValueError("Footer precedes last sample")
            if row["status"] == "sample_limit_reached" and count != max_samples:
                raise ValueError("Sample-limit footer count mismatch")
            footer = row
            continue
        if row.get("record_type") != "sample" or _integer(row.get("sample_index")) != count or count >= max_samples:
            raise ValueError("Metrics samples must be contiguous and within the declared limit")
        now = _integer(row.get("host_monotonic_ns"), minimum=1)
        cpu = _integer(row.get("process_cpu_ns"))
        if last_ns is not None:
            if now <= last_ns:
                raise ValueError("Metrics clock is not strictly increasing")
            values["sample_interval_ms"].append((now - last_ns) / 1e6)
            if cpu >= previous_cpu:
                values["cpu_percent_one_core_scale"].append(100 * (cpu - previous_cpu) / (now - last_ns))
            else:
                gaps.add("process_cpu_counter_reversed")
        resources = _object(row.get("resources"))
        for key in ("rss_bytes", "max_temperature_c"):
            if resources.get(key) is not None:
                values[key].append(_number(resources[key], nonnegative=key != "max_temperature_c"))
        values["probe_duration_ms"].append(_number(row.get("probe_duration_ms")))
        native.add(row.get("native_vio_process", {}), now, row["probe_duration_ms"])
        throttle = _object(resources.get("throttle", {}))
        if throttle.get("status") == "available":
            for key in ("current", "since_boot"):
                flags = throttle.get(key)
                allowed = {"undervoltage", "frequency_capped", "throttled", "soft_temperature_limit"}
                if (not isinstance(flags, list) or any(not isinstance(v, str) or v not in allowed for v in flags)
                        or len(set(flags)) != len(flags)):
                    raise ValueError("Invalid throttle indicators")
            throttle_current.update(throttle["current"])
            throttle_latched.update(throttle["since_boot"])
            if throttle_baseline is None:
                throttle_baseline, throttle_baseline_index = set(throttle["since_boot"]), count
        else:
            gaps.add("throttle_measurements_incomplete")
        io = _object(row.get("process_io"))
        if io.get("status") == "available":
            for key in ("read_bytes", "write_bytes", "cancelled_write_bytes"):
                _integer(io.get(key))
        if previous_io is not None and previous_io.get("status") == io.get("status") == "available":
            keys = ("read_bytes", "write_bytes", "cancelled_write_bytes")
            deltas = {key: _integer(io.get(key)) - _integer(previous_io.get(key)) for key in keys}
            if all(value >= 0 for value in deltas.values()):
                io_totals.update(deltas)
                io_intervals += 1
            else:
                gaps.add("process_io_counter_reversed")
        activity = _object(row.get("activity"))
        attitude.add(activity.get("attitude", {}))
        phases[str(activity.get("phase", "unknown"))[:80]] += 1
        if len(phases) > 32:
            raise ValueError("Too many runtime phases")
        for name in bindings:
            identity = activity.get(name)
            if identity is not None:
                if not isinstance(identity, str) or not identity or len(identity) > 512:
                    raise ValueError("Invalid release identity")
                bindings[name].add(identity)
        camera = _object(_object(activity.get("camera", {})).get("capture_worker", {}))
        if camera:
            camera_samples += 1
            counters = {key: _integer(camera.get(key)) for key in ("captured_count", "superseded_count", "delivered_count")}
            if camera_last and any(counters[key] < camera_last[key] for key in counters):
                raise ValueError("Camera counters reset within one runtime run")
            camera_first = camera_first or counters
            camera_last = counters
        previous_cpu, previous_io = cpu, io
        first_ns, last_ns = first_ns if first_ns is not None else now, now
        count += 1
    if footer is None:
        gaps.add("metrics_footer_missing_recording_may_be_incomplete")
    elif footer["status"] == "sample_limit_reached":
        gaps.add("measurement_stopped_at_sample_limit_runtime_may_have_continued")
    if count < 2:
        gaps.add("fewer_than_two_resource_samples")
    if hardware.get("is_raspberry_pi") is not True:
        gaps.add("not_raspberry_pi_hardware_evidence")
    if hardware.get("rss_measurement") != "current_proc_vmrss":
        gaps.add("current_rss_not_measured")
    for key in ("rss_bytes", "max_temperature_c"):
        if len(values[key]) != count or not values[key]:
            gaps.add(f"{key}_measurements_incomplete")
    if io_intervals != max(0, count - 1) or io_intervals == 0:
        gaps.add("process_io_intervals_incomplete")
    if camera_first is None:
        gaps.add("live_camera_counters_missing")
    elif camera_samples != count:
        gaps.add("live_camera_counter_samples_incomplete")
    if any(len(ids) > 1 for ids in bindings.values()):
        raise ValueError("Metrics contain multiple map/model releases in one run")

    decisions = _summarize_decisions(decision_path, run_id, bindings, (first_ns, last_ns))
    gaps.update(decisions.pop("gaps"))
    if any(not ids for ids in bindings.values()):
        gaps.add("map_or_model_release_identity_missing")
    camera_delta = ({key: camera_last[key] - camera_first[key] for key in camera_last} if camera_last else None)
    attitude_report = attitude.summary()
    gaps.update(attitude.gaps)
    native_report = native.summary()
    gaps.update(native.gaps)
    return {"schema_version": "vision_nav_live_load_report_v1", "run_id": run_id,
        "evidence_status": "incomplete" if gaps else "summarized_measurements",
        "evidence_gaps": sorted(gaps), "inputs": {"metrics": source.evidence, "decisions": decisions.pop("input")},
        "hardware": hardware, "bindings": {key: next(iter(ids), None) for key, ids in bindings.items()},
        "parent_process_scope": "all_threads_excludes_children", "native_vio_process": native_report,
        "recording": {"sample_count": count, "footer_status": footer.get("status") if footer else None,
            "observed_span_s": (last_ns - first_ns) / 1e9 if count > 1 else 0, "phase_sample_counts": dict(phases)},
        "resource_distributions": {key: distribution(items) for key, items in sorted(values.items())},
        "throttle": {"current_flag_sample_counts": dict(throttle_current), "since_boot_flags_observed": sorted(throttle_latched),
                     "initial_observed_latched_flags": sorted(throttle_baseline) if throttle_baseline is not None else None,
                     "baseline_sample_index": throttle_baseline_index,
                     "new_latched_flags_after_baseline": sorted(throttle_latched - (throttle_baseline or set()))},
        "process_io": {"observed_interval_count": io_intervals, "interval_byte_totals": dict(io_totals),
                       "physical_flash_writes": None, "scope": "kernel_process_storage_demand_only"},
        "camera": {"first_observed_counters": camera_first, "last_observed_counters": camera_last,
                   "observed_sample_count": camera_samples,
                   "observed_counter_change": camera_delta, "hardware_dropped_frames": None,
                   "superseded_frames_are_latest_only_handoff_discards": True},
        "attitude": attitude_report, "decisions": decisions, "position_authority": False, "px4_publication_enabled": False,
        "promotion_decision": "not_a_performance_acceptance_or_navigation_qualification"}


def _summarize_decisions(path, run_id, bindings, resource_window):
    result = {"input": None, "count": 0, "ignored_other_or_unbound_run_count": 0, "gaps": set()}
    ages, latency, executed = [], defaultdict(list), Counter()
    window_ages, window_latency = [], defaultdict(list)
    reasons, last_sequence = Counter(), None
    latency_count = 0
    if path is not None:
        source = JsonlSource(path, max_line_bytes=16 * 1024 * 1024, max_records=100000)
        for row in source:
            if row.get("runtime_run_id") != run_id:
                result["ignored_other_or_unbound_run_count"] += 1
                continue
            if (row.get("schema_version") != "vision_nav_runtime_status_v3" or row.get("emission_kind") == "heartbeat"
                    or row.get("shadow_mode") is not True or _object(row.get("px4_publication")).get("enabled") is not False
                    or _object(row.get("publication_decision")).get("published") is not False):
                raise ValueError("Decision evidence must be shadow-only completed iterations, not heartbeats")
            sequence = _integer(row.get("sequence"))
            if last_sequence is not None and sequence <= last_sequence:
                raise ValueError("Duplicate or reordered decision sequence")
            if sequence != (last_sequence + 1 if last_sequence is not None else 0):
                result["gaps"].add("decision_sequence_gaps")
            last_sequence = sequence
            for key, field in (("map_release_id", "active_map"), ("model_release_id", "active_model")):
                identity = _object(row.get(field)).get("release_id")
                if not isinstance(identity, str) or not identity:
                    result["gaps"].add("decision_release_identity_missing")
                elif bindings[key] and identity not in bindings[key]:
                    raise ValueError("Decision map/model identity does not match resource evidence")
                else:
                    bindings[key].add(identity)
            reason = str(row["publication_decision"].get("reason"))[:200]
            reasons[reason] += 1
            if len(reasons) > 256:
                raise ValueError("Too many distinct decision reasons")
            inference = _object(row.get("inference"))
            probe = _object(inference.get("frame_probe", {}))
            timing = _object(probe.get("result_timing", {}))
            camera = _object(timing.get("camera", {}))
            within_window = False
            if camera.get("status") == "available" and timing.get("age_ms") is not None:
                captured = _integer(camera.get("host_monotonic_ns"), minimum=1)
                observed = _integer(timing.get("observed_host_monotonic_ns"), minimum=1)
                age = _number(timing["age_ms"], nonnegative=False)
                expected = (observed - captured) / 1e6
                if not math.isclose(age, expected, rel_tol=1e-6, abs_tol=1e-3):
                    raise ValueError("Capture-to-result age disagrees with recorded monotonic clocks")
                if expected >= 0:
                    ages.append(expected)
                    within_window = (resource_window[0] is not None and
                                     resource_window[0] <= captured <= observed <= resource_window[1])
                    if within_window:
                        window_ages.append(expected)
                else:
                    result["gaps"].add("future_capture_timestamp")
            else:
                result["gaps"].add("capture_to_result_timing_incomplete")
            for key, value in _object(inference.get("latency_ms", {})).items():
                if not isinstance(key, str) or len(key) > 128 or len(latency) >= 128 and key not in latency:
                    raise ValueError("Too many or invalid latency roles")
                latency[key].append(_number(value))
                if within_window:
                    window_latency[key].append(value)
                latency_count += 1
                if latency_count > 1_000_000:
                    raise ValueError("Decision latency sample budget exceeded")
            stages = probe.get("stages", [])
            if not isinstance(stages, list) or len(stages) > 128:
                raise ValueError("Invalid inference stage records")
            for stage in stages:
                stage = _object(stage)
                if stage.get("status") == "executed":
                    role = stage.get("role")
                    if not isinstance(role, str) or len(role) > 128 or len(executed) >= 128 and role not in executed:
                        raise ValueError("Invalid executed stage role")
                    executed[role] += 1
            result["count"] += 1
        result["input"] = source.evidence
    if result["count"] == 0:
        result["gaps"].add("no_run_bound_decisions")
    if not window_ages:
        result["gaps"].add("no_complete_capture_to_result_interval_inside_resource_window")
    if not {"global_retriever", "local_features", "local_matcher", "temporal_student"}.issubset(executed):
        result["gaps"].add("not_all_learned_stages_observed")
    result.update({"capture_to_result_ms": distribution(ages),
        "capture_to_result_scope": "all_run_bound_decisions_with_verified_clocks",
        "resource_window_host_monotonic_ns": list(resource_window),
        "within_resource_window_capture_to_result_ms": distribution(window_ages),
        "outside_or_unverified_resource_window_count": result["count"] - len(window_ages),
        "within_resource_window_stage_latency_ms": {key: distribution(items) for key, items in sorted(window_latency.items())},
        "reported_stage_latency_ms": {key: distribution(items) for key, items in sorted(latency.items())},
        "executed_stage_counts": dict(executed), "decision_reason_counts": dict(reasons)})
    return result


def main():
    parser = argparse.ArgumentParser(description="Summarize one stopped shadow runtime load recording without promoting it.")
    parser.add_argument("--metrics", required=True, type=Path)
    parser.add_argument("--decisions", type=Path)
    parser.add_argument("--output", required=True, type=Path)
    args = parser.parse_args()
    inputs = {args.metrics.resolve()} | ({args.decisions.resolve()} if args.decisions else set())
    if args.output.resolve() in inputs:
        parser.error("Report output must not overwrite an input evidence file")
    try:
        report = summarize_runtime_load(args.metrics, args.decisions)
        write_status_snapshot(args.output, report)
    except (OSError, ValueError) as exc:
        parser.exit(2, f"Load report rejected: {exc}\n")
    print(json.dumps(report, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()
