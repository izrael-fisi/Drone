from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any
import zipfile


PASSING = {"passed", "healthy"}
WARNING = {"degraded", "warming_up"}
MISSING = {"not_provided", None}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate a vision-nav support bundle for bench readiness.")
    parser.add_argument("--support-bundle", required=True, help="support_manifest.json or support bundle ZIP.")
    parser.add_argument("--json", action="store_true", help="Emit JSON only.")
    parser.add_argument(
        "--allow-missing-px4-evidence",
        action="store_true",
        help="Do not fail when PX4 receiver evidence is absent. Use only for local software smoke checks.",
    )
    parser.add_argument(
        "--allow-missing-px4-params",
        action="store_true",
        help="Do not fail when PX4 parameter export evidence is absent. Use only before autopilot setup.",
    )
    parser.add_argument(
        "--require-ardupilot-params",
        action="store_true",
        help="Fail when ArduPilot ExternalNav parameter evidence is absent.",
    )
    parser.add_argument(
        "--require-feature-method-benchmark",
        action="store_true",
        help="Fail when feature-method benchmark evidence is absent.",
    )
    parser.add_argument(
        "--require-field-evidence",
        action="store_true",
        help="Fail when field-evidence gate reports are absent.",
    )
    parser.add_argument(
        "--allow-missing-replay-gates",
        action="store_true",
        help="Do not fail when replay-gate cases are absent. Use only for packaging smoke checks.",
    )
    return parser.parse_args()


def load_support_manifest(path: str | Path) -> dict[str, Any]:
    source = Path(path).expanduser()
    if source.suffix.lower() == ".zip":
        with zipfile.ZipFile(source) as archive:
            with archive.open("support_manifest.json") as handle:
                return json.loads(handle.read().decode("utf-8"))
    return json.loads(source.read_text())


def evaluate_bench_readiness(
    manifest: dict[str, Any],
    *,
    allow_missing_px4_evidence: bool = False,
    require_px4_evidence: bool = True,
    allow_missing_px4_params: bool = False,
    require_ardupilot_params: bool = False,
    require_feature_method_benchmark: bool = False,
    require_field_evidence: bool = False,
    allow_missing_replay_gates: bool = False,
) -> dict[str, Any]:
    checks = [
        check_bundle_health(manifest),
        check_runtime_logs(manifest),
        check_runtime_status(manifest),
        check_replay_gates(manifest, allow_missing=allow_missing_replay_gates),
        check_px4_params(manifest, allow_missing=allow_missing_px4_params),
    ]
    if require_px4_evidence:
        checks.append(check_px4_evidence(manifest, allow_missing=allow_missing_px4_evidence))
    ardupilot_check = check_ardupilot_params(manifest, require=require_ardupilot_params)
    if ardupilot_check is not None:
        checks.append(ardupilot_check)
    feature_benchmark_check = check_feature_method_benchmark(manifest, require=require_feature_method_benchmark)
    if feature_benchmark_check is not None:
        checks.append(feature_benchmark_check)
    field_evidence_check = check_field_evidence(manifest, require=require_field_evidence)
    if field_evidence_check is not None:
        checks.append(field_evidence_check)
    status = readiness_status(checks)
    return {
        "status": status,
        "support_bundle": manifest.get("name"),
        "generated_at": manifest.get("metadata", {}).get("generated_at"),
        "checks": checks,
        "summary": {
            "failed": sum(1 for check in checks if check["status"] == "failed"),
            "degraded": sum(1 for check in checks if check["status"] == "degraded"),
            "passed": sum(1 for check in checks if check["status"] == "passed"),
        },
    }


def evaluate_bench_readiness_file(path: str | Path, **kwargs: Any) -> dict[str, Any]:
    report = evaluate_bench_readiness(load_support_manifest(path), **kwargs)
    report["support_bundle_path"] = str(Path(path).expanduser())
    return report


def check_bundle_health(manifest: dict[str, Any]) -> dict[str, Any]:
    bundle = manifest.get("bundle")
    if not bundle:
        return failed("bundle_health", "Support bundle has no terrain bundle metadata.")
    health = bundle.get("health") or {}
    status = normalize_status(health.get("status"))
    if status in PASSING:
        return passed("bundle_health", "Terrain bundle health passed.", {"bundle_id": bundle.get("bundle_id")})
    if status in WARNING:
        return degraded("bundle_health", "Terrain bundle health is degraded.", {"bundle_id": bundle.get("bundle_id")})
    return failed("bundle_health", f"Terrain bundle health is {status or 'missing'}.", {"bundle_id": bundle.get("bundle_id")})


def check_runtime_logs(manifest: dict[str, Any]) -> dict[str, Any]:
    logs = manifest.get("logs") or {}
    copied = logs.get("copied") or []
    missing = logs.get("missing") or []
    summaries = logs.get("summaries") or []
    accepted_rates = [summary.get("accepted_rate") for summary in summaries if summary.get("accepted_rate") is not None]
    details = {"copied": len(copied), "missing": len(missing), "summary_count": len(summaries), "accepted_rates": accepted_rates[:5]}
    if missing:
        return failed("runtime_logs", "One or more configured runtime/replay logs were missing.", details)
    if not copied or not summaries:
        return failed("runtime_logs", "Support bundle has no runtime/replay logs to inspect.", details)
    return passed("runtime_logs", "Runtime/replay logs were copied and summarized.", details)


def check_runtime_status(manifest: dict[str, Any]) -> dict[str, Any]:
    logs = manifest.get("logs") or {}
    statuses = [item for item in logs.get("runtime_statuses") or [] if isinstance(item, dict)]
    latest = statuses[-1] if statuses else {}
    active_map = latest.get("active_map") if isinstance(latest.get("active_map"), dict) else {}
    last_match = latest.get("last_match") if isinstance(latest.get("last_match"), dict) else {}
    estimator = latest.get("estimator") if isinstance(latest.get("estimator"), dict) else {}
    output = latest.get("output") if isinstance(latest.get("output"), dict) else {}
    external = (
        latest.get("external_position")
        if isinstance(latest.get("external_position"), dict)
        else latest.get("external_position_health")
        if isinstance(latest.get("external_position_health"), dict)
        else {}
    )
    status_counts = latest.get("status_counts") if isinstance(latest.get("status_counts"), dict) else {}
    details = {
        "snapshot_count": len(statuses),
        "active_map": active_map.get("bundle_id") or active_map.get("map_id"),
        "output_path": output.get("output_dir") or latest.get("output_path"),
        "log_path": output.get("log_path") or latest.get("log_path"),
        "last_match_status": last_match.get("status"),
        "last_match_reason": last_match.get("reason"),
        "estimator_health": estimator.get("health") or estimator.get("status"),
        "external_position_status": external.get("status"),
        "accepted_count": status_counts.get("accepted"),
        "rejected_count": status_counts.get("rejected"),
    }
    if not statuses:
        return degraded("runtime_status", "Runtime status snapshot was not provided.", details)
    if not details["active_map"] or not details["last_match_status"]:
        return failed("runtime_status", "Runtime status is missing active-map or last-match state.", details)

    estimator_status = normalize_status(details["estimator_health"])
    match_status = normalize_status(details["last_match_status"])
    external_status = normalize_status(details["external_position_status"])
    if estimator_status in {"failed", "error", "unhealthy"}:
        return failed("runtime_status", "Runtime estimator health is failed.", details)
    if match_status in {"failed", "error"}:
        return failed("runtime_status", "Runtime last-match status is failed.", details)
    if external_status in {"failed", "error"}:
        return failed("runtime_status", "Runtime external-position health is failed.", details)
    if not details["output_path"] or not details["log_path"]:
        return degraded("runtime_status", "Runtime status is missing output or log path metadata.", details)
    return passed("runtime_status", "Runtime status snapshot is present and usable.", details)


def check_replay_gates(manifest: dict[str, Any], *, allow_missing: bool) -> dict[str, Any]:
    replay = manifest.get("replay_gates") or {}
    case_count = int(replay.get("case_count") or 0)
    status = normalize_status(replay.get("status"))
    details = {"case_count": case_count}
    if case_count <= 0:
        if allow_missing:
            return degraded("replay_gates", "Replay gates were not provided.", details)
        return failed("replay_gates", "Replay gates are required for bench readiness.", details)
    if status in PASSING:
        return passed("replay_gates", "Replay gates passed.", details)
    if status in WARNING:
        return degraded("replay_gates", "Replay gates are degraded.", details)
    return failed("replay_gates", f"Replay gates are {status or 'missing'}.", details)


def check_px4_evidence(manifest: dict[str, Any], *, allow_missing: bool) -> dict[str, Any]:
    evidence = manifest.get("px4_sitl_evidence") or {}
    status = normalize_status(evidence.get("status"))
    listener = evidence.get("listener") or {}
    details = {
        "sample_count": listener.get("sample_count"),
        "expected_message": evidence.get("expected_message"),
    }
    if status in MISSING:
        if allow_missing:
            return degraded("px4_sitl_evidence", "PX4 receiver evidence was not provided.", details)
        return failed("px4_sitl_evidence", "PX4 receiver evidence is required for bench readiness.", details)
    if status in PASSING:
        return passed("px4_sitl_evidence", "PX4 receiver evidence passed.", details)
    if status in WARNING:
        return degraded("px4_sitl_evidence", "PX4 receiver evidence is degraded.", details)
    return failed("px4_sitl_evidence", f"PX4 receiver evidence is {status}.", details)


def check_px4_params(manifest: dict[str, Any], *, allow_missing: bool) -> dict[str, Any]:
    params = manifest.get("px4_params") or {}
    status = normalize_status(params.get("status"))
    values = params.get("parameters") or {}
    details = {
        "EKF2_EV_CTRL": values.get("EKF2_EV_CTRL"),
        "EKF2_HGT_REF": values.get("EKF2_HGT_REF"),
        "EKF2_GPS_CTRL": values.get("EKF2_GPS_CTRL"),
    }
    if status in MISSING:
        if allow_missing:
            return degraded("px4_params", "PX4 parameter check was not provided.", details)
        return failed("px4_params", "PX4 parameter check is required for bench readiness.", details)
    if status in PASSING:
        return passed("px4_params", "PX4 parameter check passed.", details)
    if status in WARNING:
        return degraded("px4_params", "PX4 parameter check is degraded.", details)
    return failed("px4_params", f"PX4 parameter check is {status}.", details)


def check_ardupilot_params(manifest: dict[str, Any], *, require: bool) -> dict[str, Any] | None:
    params = manifest.get("ardupilot_params") or {}
    status = normalize_status(params.get("status"))
    values = params.get("parameters") or {}
    source_set = optional_int(values.get("source_set"))
    details = {
        "source_set": source_set,
        "AHRS_EKF_TYPE": values.get("AHRS_EKF_TYPE"),
        "VISO_TYPE": values.get("VISO_TYPE"),
        "EK3_SRC_POSXY": values.get(f"EK3_SRC{source_set}_POSXY") if source_set in {1, 2, 3} else None,
    }
    if status in MISSING:
        if require:
            return failed("ardupilot_params", "ArduPilot parameter check is required for this readiness gate.", details)
        return None
    if status in PASSING:
        return passed("ardupilot_params", "ArduPilot ExternalNav parameter check passed.", details)
    if status in WARNING:
        return degraded("ardupilot_params", "ArduPilot ExternalNav parameter check is degraded.", details)
    return failed("ardupilot_params", f"ArduPilot ExternalNav parameter check is {status}.", details)


def check_feature_method_benchmark(manifest: dict[str, Any], *, require: bool) -> dict[str, Any] | None:
    benchmark = manifest.get("feature_method_benchmarks") or {}
    status = normalize_status(benchmark.get("status"))
    details = {
        "report_count": benchmark.get("report_count"),
        "recommended_methods": [
            report.get("recommended_method")
            for report in benchmark.get("reports", [])
            if isinstance(report, dict) and report.get("recommended_method")
        ][:5],
    }
    if status in MISSING:
        if require:
            return failed("feature_method_benchmarks", "Feature-method benchmark report is required for this readiness gate.", details)
        return None
    if status in PASSING:
        return passed("feature_method_benchmarks", "Feature-method benchmark report passed.", details)
    if status in WARNING:
        return degraded("feature_method_benchmarks", "Feature-method benchmark report is degraded.", details)
    return failed("feature_method_benchmarks", f"Feature-method benchmark report is {status}.", details)


def check_field_evidence(manifest: dict[str, Any], *, require: bool) -> dict[str, Any] | None:
    evidence = manifest.get("field_evidence") or {}
    status = normalize_status(evidence.get("status"))
    details = {
        "report_count": evidence.get("report_count"),
        "field_case_count": evidence.get("field_case_count"),
        "covered_conditions": evidence.get("covered_conditions"),
    }
    if status in MISSING:
        if require:
            return failed("field_evidence", "Field-evidence gate report is required for this readiness gate.", details)
        return None
    if status in PASSING:
        return passed("field_evidence", "Field-evidence gate passed.", details)
    if status in WARNING:
        return degraded("field_evidence", "Field-evidence gate is degraded.", details)
    return failed("field_evidence", f"Field-evidence gate is {status}.", details)


def readiness_status(checks: list[dict[str, Any]]) -> str:
    if any(check["status"] == "failed" for check in checks):
        return "failed"
    if any(check["status"] == "degraded" for check in checks):
        return "degraded"
    return "passed"


def normalize_status(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip().lower()
    return text or None


def optional_int(value: Any) -> int | None:
    if value is None:
        return None
    try:
        numeric = float(value)
    except (TypeError, ValueError):
        return None
    if not numeric.is_integer():
        return None
    return int(numeric)


def passed(name: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"name": name, "status": "passed", "message": message, "details": details or {}}


def degraded(name: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"name": name, "status": "degraded", "message": message, "details": details or {}}


def failed(name: str, message: str, details: dict[str, Any] | None = None) -> dict[str, Any]:
    return {"name": name, "status": "failed", "message": message, "details": details or {}}


def print_human(report: dict[str, Any]) -> None:
    print(f"Bench readiness: {report.get('support_bundle_path') or report.get('support_bundle')}")
    print(f"Status: {report['status']}")
    for check in report["checks"]:
        print(f"- {check['name']}: {check['status']} - {check['message']}")


def main() -> None:
    args = parse_args()
    try:
        report = evaluate_bench_readiness_file(
            args.support_bundle,
            allow_missing_px4_evidence=args.allow_missing_px4_evidence,
            allow_missing_px4_params=args.allow_missing_px4_params,
            require_ardupilot_params=args.require_ardupilot_params,
            require_feature_method_benchmark=args.require_feature_method_benchmark,
            require_field_evidence=args.require_field_evidence,
            allow_missing_replay_gates=args.allow_missing_replay_gates,
        )
    except Exception as exc:
        report = {
            "status": "failed",
            "support_bundle_path": args.support_bundle,
            "checks": [
                failed("support_bundle", f"Could not read support bundle manifest: {exc}"),
            ],
            "summary": {"failed": 1, "degraded": 0, "passed": 0},
        }
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human(report)
    if report["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
