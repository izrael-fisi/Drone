"""Saved-image load measurement for the actual two-stage ONNX local matcher.

This is not a live-camera or full navigation benchmark. No vehicle transport,
camera ownership, service control, or model promotion is introduced.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict, dataclass
import hashlib
import json
from pathlib import Path
import time

import cv2
import numpy as np

from vision_nav.training_capture import sha256_file
from .learned_correspondence import load_local_matcher
from .runtime_benchmark import _latency_summary, _utc_now
from .hardware_resources import hardware_identity, parse_throttled, resource_snapshot
from .shadow_geometry import verify_planar_correspondences


SCHEMA = "vision_nav_local_onnx_benchmark_v1"


@dataclass(frozen=True)
class LocalBenchmarkPolicy:
    duration_s: float = 60.0
    max_iterations: int = 100000
    minimum_iterations: int = 20
    warmup_count: int = 3
    cache_mode: str = "warm"
    max_p95_ms: float = 1000.0
    max_rss_mib: float = 2048.0
    max_temperature_c: float = 80.0
    require_pi: bool = False

    def validate(self):
        for name in ("duration_s", "max_p95_ms", "max_rss_mib", "max_temperature_c"):
            value = getattr(self, name)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if self.duration_s > 3600:
            raise ValueError("Benchmark duration is limited to one hour per invocation")
        for name, low, high in (("max_iterations", 1, 100000), ("minimum_iterations", 1, 100000), ("warmup_count", 0, 100)):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f"Invalid benchmark {name}")
        if self.minimum_iterations > self.max_iterations or self.cache_mode not in {"warm", "cold"}:
            raise ValueError("Invalid benchmark iteration limits or map cache mode")
        if type(self.require_pi) is not bool:
            raise ValueError("require_pi must be boolean")


def _inputs(paths, maximum: int) -> list[dict]:
    if not 1 <= len(paths) <= maximum:
        raise ValueError(f"Provide one to {maximum} distinct image paths")
    resolved = [Path(path).expanduser().resolve() for path in paths]
    if len(set(resolved)) != len(resolved):
        raise ValueError("Duplicate benchmark image paths")
    result = []
    for path in resolved:
        if not path.is_file() or not 0 < path.stat().st_size <= 64 * 1024 * 1024:
            raise ValueError("Benchmark images must be nonempty local files no larger than 64 MiB")
        result.append({"path": str(path), "sha256": sha256_file(path)})
    return result


def _decode(entry: dict) -> np.ndarray:
    raw = Path(entry["path"]).read_bytes()
    if hashlib.sha256(raw).hexdigest() != entry["sha256"]:
        raise ValueError("Benchmark input image changed during the run")
    image = cv2.imdecode(np.frombuffer(raw, np.uint8), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError("Cannot decode benchmark image")
    return image


def assess_limits(records, snapshots, elapsed: float, policy: LocalBenchmarkPolicy, *, startup_error=None) -> dict:
    measured = [row for row in records if row["phase"] == "measured"]
    successful = [row for row in measured if row["status"] == "completed"]
    exceeded, missing = [], []
    if startup_error or any(row["status"] != "completed" for row in records):
        exceeded.append("inference_or_input_failure")
    if len(successful) < policy.minimum_iterations:
        missing.append("insufficient_completed_iterations")
    if elapsed < policy.duration_s:
        missing.append("requested_duration_not_reached")
    if not any(row.get("attention_calls", 0) for row in successful):
        missing.append("learned_attention_not_exercised")
    latencies = [row["latency_ms"]["total"] for row in successful]
    if latencies and np.percentile(latencies, 95) > policy.max_p95_ms:
        exceeded.append("local_iteration_p95_exceeds_limit")
    rss = [s["rss_bytes"] for s in snapshots if s["rss_bytes"] is not None]
    temperatures = [s["max_temperature_c"] for s in snapshots if s["max_temperature_c"] is not None]
    if not rss or any(s["rss_bytes"] is None for s in snapshots):
        missing.append("rss_measurement_incomplete")
    if rss and max(rss) > policy.max_rss_mib * 1024 ** 2:
        exceeded.append("process_memory_exceeds_limit")
    if not temperatures or any(s["max_temperature_c"] is None for s in snapshots):
        missing.append("temperature_measurement_incomplete")
    if temperatures and max(temperatures) > policy.max_temperature_c:
        exceeded.append("temperature_exceeds_limit")
    if any(s["throttle"]["current"] for s in snapshots):
        exceeded.append("current_power_or_throttle_flag")
    baseline = set(snapshots[0]["throttle"]["since_boot"]) if snapshots else set()
    if snapshots and snapshots[0]["throttle"]["status"] == "available" and any(set(s["throttle"]["since_boot"]) - baseline for s in snapshots):
        exceeded.append("new_latched_power_or_throttle_flag")
    if policy.require_pi and (not snapshots or any(s["throttle"]["status"] != "available" for s in snapshots)):
        missing.append("pi_throttle_measurement_incomplete")
    return {"status": "exceeded" if exceeded else "incomplete" if missing else "within_measured_limits",
            "exceeded": exceeded, "missing": missing, "preexisting_latched_flags": sorted(baseline),
            "promotion_decision": "not_a_navigation_or_flight_acceptance"}


def benchmark_local(config_path, query_paths, map_paths, output_dir, *, policy=None) -> dict:
    policy = policy or LocalBenchmarkPolicy()
    policy.validate()
    hardware = hardware_identity()
    if policy.require_pi and not hardware["is_raspberry_pi"]:
        raise ValueError("--require-pi needs a Raspberry Pi, not a desktop benchmark")
    config = Path(config_path).expanduser().resolve()
    config_digest = sha256_file(config)
    if json.loads(config.read_text()).get("backend") != "onnx_cpu":
        raise ValueError("Local deployment benchmark requires backend=onnx_cpu")
    queries, maps = _inputs(query_paths, 256), _inputs(map_paths, 4)
    output = Path(output_dir).expanduser().resolve()
    if output.exists():
        raise ValueError("Benchmark output directory already exists")
    output.mkdir(parents=True)
    benchmark_digest = sha256_file(Path(__file__))
    records, snapshots = [], []
    stage_values = {}
    startup_error, provenance = None, None
    start, measured_start, measured_elapsed, cpu_elapsed = time.perf_counter(), None, 0.0, 0.0
    stop_reason = "startup_failure"

    def sample(phase):
        snapshot = resource_snapshot(hardware["is_raspberry_pi"])
        snapshot.update(phase=phase, elapsed_s=time.perf_counter() - start)
        snapshots.append(snapshot)
        return snapshot

    def hardware_exceeded(snapshot):
        return (snapshot["throttle"]["current"] or (snapshot["max_temperature_c"] is not None
                and snapshot["max_temperature_c"] > policy.max_temperature_c)
                or (snapshot["rss_bytes"] is not None and snapshot["rss_bytes"] > policy.max_rss_mib * 1024 ** 2))

    before_load = sample("before_load")
    startup_ms, cache_preparation_ms = None, None
    with (output / "iterations.jsonl").open("w", encoding="utf-8") as stream:
        try:
            if hardware_exceeded(before_load):
                raise RuntimeError("Hardware already exceeds policy; models not loaded")
            load_start = time.perf_counter()
            matcher = load_local_matcher(config)
            provenance = matcher.provenance
            startup_ms = (time.perf_counter() - load_start) * 1000
            if hardware_exceeded(sample("after_load_and_startup_parity")):
                raise RuntimeError("Hardware exceeds policy after model load; inference not started")
            cache_start = time.perf_counter()
            warm_cache = [matcher.extract(_decode(entry)) for entry in maps] if policy.cache_mode == "warm" else None
            cache_preparation_ms = (time.perf_counter() - cache_start) * 1000

            def iteration(index, phase):
                latencies, attention_calls, attempts = {}, 0, []
                began = time.perf_counter()
                row = {"index": index, "phase": phase, "query_index": index % len(queries), "status": "completed"}
                def timed(stage, fn):
                    clock = time.perf_counter()
                    try:
                        return fn()
                    finally:
                        latencies[stage] = latencies.get(stage, 0.0) + (time.perf_counter() - clock) * 1000
                try:
                    image = timed("query_read_hash_decode", lambda: _decode(queries[row["query_index"]]))
                    query = timed("query_preprocess_and_xfeat", lambda: matcher.extract(image))
                    for i, entry in enumerate(maps):
                        if warm_cache is None:
                            map_image = timed("map_read_hash_decode", lambda: _decode(entry))
                            tile = timed("map_preprocess_and_xfeat", lambda: matcher.extract(map_image))
                        else:
                            tile = warm_cache[i]
                        will_run = min(len(query.keypoints), len(tile.keypoints)) >= 6
                        pairs, scores = timed("lighterglue", lambda: matcher.match(query, tile))
                        attention_calls += int(will_run)
                        geometry = timed("planar_geometry", lambda: verify_planar_correspondences(
                            query_keypoints=query.keypoints, map_keypoints=tile.keypoints, match_pairs=pairs, scores=scores,
                            query_width=matcher.policy.image_width, query_height=matcher.policy.image_height,
                            **matcher.policy.geometry_kwargs()))
                        attempts.append({"map_index": i, "query_features": len(query.keypoints), "map_features": len(tile.keypoints),
                                         "attention_executed": will_run, "match_count": len(pairs), "geometry": geometry})
                except Exception as exc:
                    row.update(status="failed", reason=f"{type(exc).__name__}: {exc}")
                latencies["total"] = (time.perf_counter() - began) * 1000
                row.update(latency_ms=latencies, attention_calls=attention_calls, candidates=attempts,
                           position_authority=False, px4_external_vision="disabled")
                # Keep complete candidate evidence on disk, not in a growing
                # in-memory list that would distort the model RSS measurement.
                records.append({"phase": phase, "status": row["status"], "attention_calls": attention_calls,
                                "latency_ms": {"total": latencies["total"]}})
                stream.write(json.dumps(row, sort_keys=True, allow_nan=False) + "\n")
                stream.flush()
                if phase == "measured" and row["status"] == "completed":
                    for stage, value in latencies.items():
                        stage_values.setdefault(stage, []).append(value)
                return row

            for index in range(policy.warmup_count):
                if iteration(index, "warmup")["status"] != "completed":
                    raise RuntimeError("Warmup failed; inspect iterations.jsonl")
            before = sample("before_measured")
            if hardware_exceeded(before):
                raise RuntimeError("Hardware already exceeds power/thermal policy; benchmark not started")
            measured_start, cpu_start = time.perf_counter(), time.process_time()
            last_sample = measured_start
            stop_reason = "iteration_limit"
            for index in range(policy.max_iterations):
                if time.perf_counter() - measured_start >= policy.duration_s:
                    stop_reason = "duration_reached"
                    break
                row = iteration(index, "measured")
                if row["status"] != "completed":
                    stop_reason = "inference_failure"
                    break
                if time.perf_counter() - last_sample >= 1:
                    snapshot = sample("measured")
                    last_sample = time.perf_counter()
                    if hardware_exceeded(snapshot):
                        stop_reason = "hardware_limit"
                        break
            measured_elapsed = time.perf_counter() - measured_start
            cpu_elapsed = time.process_time() - cpu_start
        except Exception as exc:
            startup_error = f"{type(exc).__name__}: {exc}"
    sample("after_run")
    report = {"schema_version": SCHEMA, "generated_at_utc": _utc_now(),
        "status": "failed" if startup_error or any(row["status"] == "failed" for row in records) else "completed",
        "scope": "saved_image_local_correspondence_only", "hardware": hardware, "policy": asdict(policy),
        "provenance": provenance, "config_sha256": config_digest, "query_images": queries, "map_images": maps,
        "benchmark_source_sha256": benchmark_digest, "startup_and_parity_ms": startup_ms,
        "cache_preparation_ms": cache_preparation_ms, "stop_reason": stop_reason, "startup_error": startup_error,
        "measured_wall_s": measured_elapsed, "measured_process_cpu_s": cpu_elapsed,
        "process_cpu_percent_one_core_scale": cpu_elapsed / measured_elapsed * 100 if measured_elapsed else None,
        "iteration_count": sum(row["phase"] == "measured" for row in records),
        "stage_latency_ms": {name: _latency_summary(values) for name, values in stage_values.items()},
        "resource_samples": snapshots, "limits": assess_limits(records, snapshots, measured_elapsed, policy, startup_error=startup_error),
        "iterations_sha256": sha256_file(output / "iterations.jsonl"),
        "position_authority": False, "px4_external_vision": "disabled",
        "limitations": ["not_camera_or_navigation_latency", "not_live_drop_or_deadline_measurement", "no_vio_or_retrieval_or_student",
                        "repeated_saved_inputs_not_accuracy_evidence", "os_file_cache_not_flushed", "sampled_resources_can_miss_short_peaks"]}
    (output / "summary.json").write_text(json.dumps(report, indent=2, sort_keys=True, allow_nan=False) + "\n")
    return report


def main():
    parser = argparse.ArgumentParser(description="Measure learned local ONNX stages on saved images; never connects to a vehicle")
    parser.add_argument("--config", type=Path, required=True)
    parser.add_argument("--query", type=Path, action="append", required=True)
    parser.add_argument("--map", type=Path, action="append", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--duration-s", type=float, default=60)
    parser.add_argument("--max-iterations", type=int, default=100000)
    parser.add_argument("--minimum-iterations", type=int, default=20)
    parser.add_argument("--warmup-count", type=int, default=3)
    parser.add_argument("--cache-mode", choices=("warm", "cold"), default="warm")
    parser.add_argument("--max-p95-ms", type=float, default=1000)
    parser.add_argument("--max-rss-mib", type=float, default=2048)
    parser.add_argument("--max-temperature-c", type=float, default=80)
    parser.add_argument("--require-pi", action="store_true")
    args = parser.parse_args()
    policy = LocalBenchmarkPolicy(**{name: getattr(args, name) for name in LocalBenchmarkPolicy.__dataclass_fields__})
    try:
        report = benchmark_local(args.config, args.query, args.map, args.output_dir, policy=policy)
    except (OSError, ValueError, RuntimeError) as exc:
        parser.exit(2, f"Benchmark rejected: {exc}\n")
    print(json.dumps(report, indent=2, sort_keys=True))
    if report["status"] != "completed" or report["limits"]["status"] != "within_measured_limits":
        raise SystemExit(2)


if __name__ == "__main__":
    main()
