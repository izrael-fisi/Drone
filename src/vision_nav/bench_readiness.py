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
    allow_missing_px4_params: bool = False,
    allow_missing_replay_gates: bool = False,
) -> dict[str, Any]:
    checks = [
        check_bundle_health(manifest),
        check_runtime_logs(manifest),
        check_replay_gates(manifest, allow_missing=allow_missing_replay_gates),
        check_px4_evidence(manifest, allow_missing=allow_missing_px4_evidence),
        check_px4_params(manifest, allow_missing=allow_missing_px4_params),
    ]
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
