from __future__ import annotations

import argparse
from dataclasses import asdict
import json
from pathlib import Path
import time
from typing import Any

from vision_nav.replay_gates import ReplayGateConfig, evaluate_replay_records
from vision_nav.summarize_match_log import load_records, summarize_records


SUPPORTED_CLASSICAL_METHODS = {"orb", "akaze", "sift"}
NEURAL_METHOD_ALIASES = {"neural", "superpoint", "lightglue", "superpoint-lightglue", "superpoint_lightglue"}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Compare terrain replay behavior across feature methods.")
    parser.add_argument("--bundle", help="Terrain bundle directory or manifest.json path for rerunning replay frames.")
    parser.add_argument("--replay-log", help="JSONL replay log with frame_path entries for rerunning methods.")
    parser.add_argument("--method-log", action="append", default=[], help="Existing method log as method=path. Can be repeated.")
    parser.add_argument("--methods", default="orb,akaze", help="Comma-separated methods to compare. Default: orb,akaze.")
    parser.add_argument("--expected", choices=["good_map", "degraded", "wrong_map"], required=True)
    parser.add_argument("--case-name", default="feature-method-benchmark")
    parser.add_argument("--output-dir", help="Directory for generated per-method logs and summary JSON.")
    parser.add_argument("--max-features", type=int, default=3000)
    parser.add_argument("--ratio", type=float, default=0.75)
    parser.add_argument("--min-inliers", type=int, default=18)
    parser.add_argument("--ransac-threshold", type=float, default=4.0)
    parser.add_argument("--max-candidates", type=int, default=64)
    parser.add_argument("--search-radius-m", type=float, default=80.0)
    parser.add_argument("--camera-calibration", help="Optional camera calibration YAML for frame undistortion.")
    parser.add_argument("--output", help="Optional JSON summary output path.")
    parser.add_argument("--json", action="store_true", help="Print JSON only.")
    return parser.parse_args()


def parse_methods(value: str) -> list[str]:
    methods = []
    seen = set()
    for item in value.split(","):
        method = item.strip().lower()
        if method and method not in seen:
            methods.append(method)
            seen.add(method)
    if not methods:
        raise ValueError("--methods must contain at least one method")
    return methods


def parse_method_logs(values: list[str]) -> dict[str, Path]:
    logs: dict[str, Path] = {}
    for value in values:
        if "=" not in value:
            raise ValueError("--method-log must use method=path")
        method, raw_path = value.split("=", 1)
        method = method.strip().lower()
        if not method:
            raise ValueError("--method-log method cannot be empty")
        logs[method] = Path(raw_path).expanduser()
    return logs


def benchmark_feature_methods(
    *,
    expected: str,
    methods: list[str],
    case_name: str = "feature-method-benchmark",
    method_logs: dict[str, Path] | None = None,
    bundle_path: str | Path | None = None,
    replay_log_path: str | Path | None = None,
    output_dir: str | Path | None = None,
    options: dict[str, Any] | None = None,
    gate_config: ReplayGateConfig | None = None,
) -> dict[str, Any]:
    method_logs = method_logs or {}
    options = options or {}
    generated_dir = Path(output_dir).expanduser() if output_dir else None
    if generated_dir is not None:
        generated_dir.mkdir(parents=True, exist_ok=True)

    input_records = None
    bundle = None
    if bundle_path and replay_log_path:
        from vision_nav.terrain_bundle import load_terrain_bundle

        bundle = load_terrain_bundle(bundle_path)
        input_records = load_records(Path(replay_log_path).expanduser())

    method_reports = []
    for method in methods:
        if method in method_logs:
            report = evaluate_method_log(
                method=method,
                log_path=method_logs[method],
                expected=expected,
                case_name=case_name,
                gate_config=gate_config,
            )
        elif method in NEURAL_METHOD_ALIASES:
            report = unavailable_method(method, "Neural SuperPoint/LightGlue descriptors are not generated yet.")
        elif method not in SUPPORTED_CLASSICAL_METHODS:
            report = unavailable_method(method, f"Unsupported feature method: {method}")
        elif bundle is not None and input_records is not None:
            if generated_dir is None:
                generated_dir = Path(replay_log_path).expanduser().parent / "feature_method_benchmark"
                generated_dir.mkdir(parents=True, exist_ok=True)
            method_log = generated_dir / f"{safe_name(method)}_terrain_replay_matches.jsonl"
            records = run_replay_for_method(
                bundle=bundle,
                input_records=input_records,
                method=method,
                output_log=method_log,
                options=options,
            )
            report = evaluate_method_records(
                method=method,
                records=records,
                expected=expected,
                case_name=case_name,
                gate_config=gate_config,
                log_path=method_log,
            )
        else:
            report = unavailable_method(method, "No method log was provided and --bundle/--replay-log were not both set.")
        method_reports.append(report)

    return {
        "status": benchmark_status(method_reports),
        "case_name": case_name,
        "expected": expected,
        "methods": method_reports,
        "recommended_method": recommended_method(method_reports),
        "config": {
            "gate": asdict(gate_config or ReplayGateConfig()),
            "matcher": options,
            "bundle": str(bundle_path) if bundle_path else None,
            "replay_log": str(replay_log_path) if replay_log_path else None,
            "output_dir": str(generated_dir) if generated_dir else None,
        },
    }


def evaluate_method_log(
    *,
    method: str,
    log_path: Path,
    expected: str,
    case_name: str,
    gate_config: ReplayGateConfig | None,
) -> dict[str, Any]:
    records = load_records(log_path)
    return evaluate_method_records(
        method=method,
        records=records,
        expected=expected,
        case_name=case_name,
        gate_config=gate_config,
        log_path=log_path,
    )


def evaluate_method_records(
    *,
    method: str,
    records: list[dict[str, Any]],
    expected: str,
    case_name: str,
    gate_config: ReplayGateConfig | None,
    log_path: Path | None = None,
) -> dict[str, Any]:
    gate = evaluate_replay_records(
        records,
        case_name=f"{case_name}-{method}",
        expected=expected,
        config=gate_config or ReplayGateConfig(),
    )
    summary = summarize_records(records)
    return {
        "method": method,
        "status": gate["status"],
        "log_path": str(log_path) if log_path else None,
        "record_count": len(records),
        "gate": gate,
        "summary": {
            "accepted_rate": summary.get("accepted_rate"),
            "total_records": summary.get("total_records"),
            "status_counts": summary.get("status_counts"),
            "confidence": summary.get("confidence"),
            "inliers": summary.get("inliers"),
            "reprojection_error_px": summary.get("reprojection_error_px"),
            "match_duration_s": summary.get("match_duration_s"),
        },
    }


def run_replay_for_method(
    *,
    bundle: Any,
    input_records: list[dict[str, Any]],
    method: str,
    output_log: Path,
    options: dict[str, Any],
) -> list[dict[str, Any]]:
    from vision_nav.terrain_estimator import TerrainEstimator
    from vision_nav.terrain_matcher import TerrainMatchOptions, match_terrain_frame

    output_log.parent.mkdir(parents=True, exist_ok=True)
    estimator = TerrainEstimator()
    output_records: list[dict[str, Any]] = []
    with output_log.open("w", encoding="utf-8") as out:
        for sequence, record in enumerate(input_records, start=1):
            frame_path = frame_path_from_record(record)
            if not frame_path:
                result = {"status": "rejected", "reason": "missing_frame_path", "sequence": sequence}
            else:
                state = estimator.state
                match_options = TerrainMatchOptions(
                    method=method,
                    max_features=int(options.get("max_features", 3000)),
                    ratio=float(options.get("ratio", 0.75)),
                    min_inliers=int(options.get("min_inliers", 18)),
                    ransac_threshold=float(options.get("ransac_threshold", 4.0)),
                    max_candidates=int(options.get("max_candidates", 64)),
                    prior_east_m=state.east_m,
                    prior_north_m=state.north_m,
                    search_radius_m=float(options.get("search_radius_m", 80.0)) if state.initialized else None,
                    camera_calibration=options.get("camera_calibration"),
                )
                start = time.monotonic()
                result = match_terrain_frame(bundle, frame_path, match_options)
                result = estimator.update_from_match(result, barometer_sample=barometer_from_record(record))
                result["match_duration_s"] = time.monotonic() - start
            output_record = {
                "sequence": sequence,
                "input": record,
                "match_duration_s": result.get("match_duration_s"),
                "result": result,
            }
            out.write(json.dumps(output_record, sort_keys=True) + "\n")
            output_records.append(output_record)
    return output_records


def frame_path_from_record(record: dict[str, Any]) -> str | None:
    for key in ("frame_path", "frame", "image_path", "path"):
        value = record.get(key)
        if value:
            return str(value)
    nested = record.get("capture")
    if isinstance(nested, dict) and nested.get("frame_path"):
        return str(nested["frame_path"])
    return None


def barometer_from_record(record: dict[str, Any]) -> dict[str, Any] | None:
    for key in ("barometer", "baro"):
        value = record.get(key)
        if isinstance(value, dict):
            return value
    telemetry = record.get("telemetry")
    if isinstance(telemetry, list):
        for sample in telemetry:
            if not isinstance(sample, dict):
                continue
            if sample.get("pressure_altitude_m") is not None or sample.get("relative_altitude_m") is not None:
                return {
                    "timestamp_us": sample.get("timestamp_us"),
                    "altitude_m": sample.get("pressure_altitude_m"),
                    "relative_altitude_m": sample.get("relative_altitude_m"),
                    "pressure_hpa": sample.get("pressure_hpa"),
                    "source": f"replay:{sample.get('message_type', 'telemetry')}",
                }
    return None


def unavailable_method(method: str, reason: str) -> dict[str, Any]:
    return {
        "method": method,
        "status": "not_available",
        "reason": reason,
        "record_count": 0,
        "gate": None,
        "summary": {},
    }


def benchmark_status(method_reports: list[dict[str, Any]]) -> str:
    statuses = [report.get("status") for report in method_reports]
    if "passed" in statuses:
        return "passed"
    if "degraded" in statuses:
        return "degraded"
    return "failed"


def recommended_method(method_reports: list[dict[str, Any]]) -> str | None:
    for desired in ("passed", "degraded"):
        for report in method_reports:
            if report.get("status") == desired:
                return str(report.get("method"))
    return None


def safe_name(value: str) -> str:
    return "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in value).strip("-") or "method"


def print_human(report: dict[str, Any]) -> None:
    print(f"Feature method benchmark: {report['case_name']}")
    print(f"Expected: {report['expected']}")
    print(f"Status: {report['status']}")
    print(f"Recommended: {report.get('recommended_method') or 'none'}")
    for method in report["methods"]:
        print(f"- {method['method']}: {method['status']}")
        if method.get("reason"):
            print(f"  {method['reason']}")
            continue
        gate = method.get("gate") or {}
        metrics = gate.get("metrics") or {}
        print(f"  accepted_rate: {metrics.get('accepted_rate')}")
        print(f"  records: {metrics.get('total_records')}")


def main() -> None:
    args = parse_args()
    options = {
        "max_features": args.max_features,
        "ratio": args.ratio,
        "min_inliers": args.min_inliers,
        "ransac_threshold": args.ransac_threshold,
        "max_candidates": args.max_candidates,
        "search_radius_m": args.search_radius_m,
        "camera_calibration": args.camera_calibration,
    }
    report = benchmark_feature_methods(
        expected=args.expected,
        methods=parse_methods(args.methods),
        case_name=args.case_name,
        method_logs=parse_method_logs(args.method_log),
        bundle_path=args.bundle,
        replay_log_path=args.replay_log,
        output_dir=args.output_dir,
        options=options,
    )
    if args.output:
        Path(args.output).expanduser().write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human(report)
    if report["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
