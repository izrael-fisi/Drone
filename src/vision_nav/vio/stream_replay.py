"""Exercise the independent native VIO worker with a sealed recording."""

import argparse
import json
from pathlib import Path
import time

from .openvins_replay import REVISION, digest, validate_capture
from .stream_worker import NativeVioStreamWorker
from vision_nav.neural.runtime_metrics import RuntimeMetricsRecorder


def sensor_batches(path):
    """One camera and its preceding IMU look-ahead per bounded handoff."""
    batch, size = [], 0
    with path.open("rb") as source:
        while line := source.readline(65537):
            size += len(line)
            if len(line) > 65536 or size > 262144 or len(batch) >= 1024:
                raise ValueError("Recorded sensor batch exceeds stream limits")
            event = json.loads(line)
            if not isinstance(event, dict) or event.get("type") not in {"imu", "camera"}:
                raise ValueError("Only camera/IMU sensor events may enter VIO")
            batch.append(event)
            if event["type"] == "camera":
                yield batch
                batch, size = [], 0
    if batch:
        raise ValueError("Trailing IMU-only batch requires an explicit policy; not silently discarded")


def run(binary, capture, output, *, capacity=2, batch_timeout_s=5., record_runtime_metrics=False,
        metrics_interval_s=1., metrics_max_samples=3600):
    worker = NativeVioStreamWorker(binary, capture, output, capacity=capacity, batch_timeout_s=batch_timeout_s)
    start = time.monotonic()
    error = None
    metrics = (RuntimeMetricsRecorder(output, run_id=worker.worker_run_id,
        activity=lambda: {"phase": "native_vio_recorded_replay", "vio": worker.status()},
        native_vio_probe=worker.resource_snapshot, interval_s=metrics_interval_s,
        max_samples=metrics_max_samples) if record_runtime_metrics else None)
    worker.start()
    try:
        if metrics:
            metrics.start()
        for batch in sensor_batches(capture / "events.jsonl"):
            while not worker.submit(batch):
                if time.monotonic() - start > 1800:
                    raise TimeoutError("Streaming replay exceeded 1800 seconds")
                time.sleep(.005)
        worker.finish()
        while not worker.join(.1):
            if time.monotonic() - start > 1800:
                raise TimeoutError("Streaming replay exceeded 1800 seconds")
        validate_capture(capture)
        if digest(capture / "input_manifest.json") != worker.manifest_sha256 or digest(binary) != worker.binary_sha256:
            raise ValueError("Source manifest or native binary changed during replay")
    except Exception as exc:
        error = f"{type(exc).__name__}:{exc}"
    finally:
        if not worker.join(0):
            worker.stop()
        if metrics:
            metrics.stop()
    state = worker.status()
    states_path = output / "local_states.jsonl"
    complete = (error is None and not state["thread_alive"] and state["status"] == "complete"
                and state["completed_batch_count"] == state["submitted_batch_count"])
    metrics_state = metrics.status() if metrics else None
    if metrics_state and (metrics_state["status"] != "stopped" or metrics_state["sample_count"] < 2
                          or metrics_state["native_vio_available_sample_count"] < 2):
        complete = False
        error = error or "Requested metrics failed, hit their sample cap, or contain fewer than two available native samples"
    report = {"schema_version": "vision_nav_openvins_replay_v1", "execution_mode": "bounded_sensor_stream_replay",
        "complete": complete, "returncode": state["native_returncode"], "execution_error": error or state["error"],
        "scope": worker.manifest["scope"], "states": state["local_state_counts"], "worker": state,
        "native_build": worker.native_build, "source_revision": REVISION, "wall_time_s": time.monotonic() - start,
        "input_manifest_sha256": worker.manifest_sha256, "binary_sha256": worker.binary_sha256,
        "python_adapter_sha256": digest(__file__), "worker_sha256": digest(Path(__file__).with_name("stream_worker.py")),
        "states_sha256": digest(states_path) if states_path.exists() else None, "pose_window_requested": True,
        "runtime_metrics": metrics_state,
        "global_pose_available": False, "position_authority": False, "px4_external_vision": "disabled",
        "smoother_factor_export": "disabled_pending_live_correlation_contract",
        "timing_scope": "as_fast_as_processed_replay_not_live_sensor_latency"}
    (output / "report.json").write_text(json.dumps(report, indent=2) + "\n")
    return report


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("binary", "capture", "output"):
        parser.add_argument("--" + name, required=True, type=Path)
    parser.add_argument("--capacity", type=int, default=2)
    parser.add_argument("--batch-timeout-s", type=float, default=5.)
    parser.add_argument("--record-runtime-metrics", action="store_true")
    parser.add_argument("--metrics-interval-s", type=float, default=1.)
    parser.add_argument("--metrics-max-samples", type=int, default=3600)
    args = parser.parse_args(argv)
    try:
        report = run(args.binary, args.capture, args.output, capacity=args.capacity, batch_timeout_s=args.batch_timeout_s,
                     record_runtime_metrics=args.record_runtime_metrics, metrics_interval_s=args.metrics_interval_s,
                     metrics_max_samples=args.metrics_max_samples)
    except (ValueError, OSError) as exc:
        parser.exit(2, f"Native stream replay rejected: {exc}\n")
    print(json.dumps(report, indent=2))
    return 0 if report["complete"] else 2


if __name__ == "__main__":
    raise SystemExit(main())
