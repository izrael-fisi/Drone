from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from vision_nav.bench_readiness import (
    degraded,
    evaluate_bench_readiness_file,
    failed,
    load_support_manifest,
    normalize_status,
    passed,
    readiness_status,
)
from vision_nav.px4_sitl_session import evaluate_px4_sitl_session


REQUIRED_FIELD_CONDITIONS = [
    "good_texture",
    "low_texture",
    "blur",
    "seasonal_change",
    "lighting_change",
    "altitude_scale_change",
    "repeated_patterns",
    "wrong_map",
]

RESEARCH_DOC_MARKERS = [
    "Highest-Value References",
    "Recommended Product Architecture Changes",
    "Near-Term Repo Integration Plan",
]

IMPLEMENTATION_PLAN_MARKERS = [
    "Track 1: External Position Output",
    "Track 2: ROS 2 Companion Runtime",
    "Track 3: Terrain Map Bundle Pipeline",
    "Track 4: Desktop Setup And Mission UX",
    "Track 5: Validation And Product Risk Controls",
]


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Audit whether the autonomy ground-control implementation plan has enough evidence to be considered executed."
    )
    parser.add_argument(
        "--research-doc",
        default="docs/autonomy-ground-control-research.md",
        help="Research document path.",
    )
    parser.add_argument(
        "--implementation-plan",
        default="docs/autonomy-ground-control-implementation-plan.md",
        help="Implementation plan document path.",
    )
    parser.add_argument(
        "--support-bundle",
        help="support_manifest.json or support bundle ZIP containing bench evidence.",
    )
    parser.add_argument(
        "--px4-sitl-session",
        help="Optional PX4 SITL evidence-session folder to evaluate directly.",
    )
    parser.add_argument(
        "--px4-sitl-report",
        help="Optional receiver_evidence.json report to evaluate directly.",
    )
    parser.add_argument(
        "--field-evidence-report",
        help="Optional field-evidence gate report to evaluate directly.",
    )
    parser.add_argument(
        "--feature-method-benchmark-report",
        help="Optional feature-method benchmark report to evaluate directly.",
    )
    parser.add_argument(
        "--threshold-tuning-report",
        help="JSON report proving replay thresholds were tuned against real field logs.",
    )
    parser.add_argument("--output", help="Optional JSON report output path.")
    parser.add_argument("--json", action="store_true", help="Emit JSON only.")
    return parser.parse_args()


def evaluate_autonomy_readiness(
    *,
    research_doc_path: str | Path = "docs/autonomy-ground-control-research.md",
    implementation_plan_path: str | Path = "docs/autonomy-ground-control-implementation-plan.md",
    support_bundle_path: str | Path | None = None,
    px4_sitl_session_path: str | Path | None = None,
    px4_sitl_report_path: str | Path | None = None,
    field_evidence_report_path: str | Path | None = None,
    feature_method_benchmark_report_path: str | Path | None = None,
    threshold_tuning_report_path: str | Path | None = None,
) -> dict[str, Any]:
    support_report = evaluate_support_bundle(
        support_bundle_path,
        require_px4_evidence=px4_sitl_session_path is None and px4_sitl_report_path is None,
        require_feature_method_benchmark=feature_method_benchmark_report_path is None,
        require_field_evidence=field_evidence_report_path is None,
    )
    support_checks = support_checks_by_name(support_report)
    support_manifest = support_report.get("manifest") if isinstance(support_report.get("manifest"), dict) else None
    checks = [
        check_doc_markers(
            "research_doc",
            research_doc_path,
            RESEARCH_DOC_MARKERS,
            "Autonomy research doc covers references, architecture, and integration plan.",
        ),
        check_doc_markers(
            "implementation_plan",
            implementation_plan_path,
            IMPLEMENTATION_PLAN_MARKERS,
            "Implementation plan covers the required delivery tracks.",
        ),
        support_report["check"],
        check_px4_receiver_proof(px4_sitl_session_path, px4_sitl_report_path, support_checks),
        check_field_evidence_proof(field_evidence_report_path, support_checks),
        check_feature_method_proof(feature_method_benchmark_report_path, support_checks),
        check_threshold_tuning(threshold_tuning_report_path, support_manifest=support_manifest),
    ]
    report = {
        "status": readiness_status(checks),
        "checks": checks,
        "next_actions": next_actions_for_checks(checks),
        "summary": {
            "failed": sum(1 for check in checks if check["status"] == "failed"),
            "degraded": sum(1 for check in checks if check["status"] == "degraded"),
            "passed": sum(1 for check in checks if check["status"] == "passed"),
        },
        "inputs": {
            "research_doc": str(Path(research_doc_path).expanduser()),
            "implementation_plan": str(Path(implementation_plan_path).expanduser()),
            "support_bundle": str(Path(support_bundle_path).expanduser()) if support_bundle_path else None,
            "px4_sitl_session": str(Path(px4_sitl_session_path).expanduser()) if px4_sitl_session_path else None,
            "px4_sitl_report": str(Path(px4_sitl_report_path).expanduser()) if px4_sitl_report_path else None,
            "field_evidence_report": str(Path(field_evidence_report_path).expanduser()) if field_evidence_report_path else None,
            "feature_method_benchmark_report": (
                str(Path(feature_method_benchmark_report_path).expanduser())
                if feature_method_benchmark_report_path
                else None
            ),
            "threshold_tuning_report": str(Path(threshold_tuning_report_path).expanduser()) if threshold_tuning_report_path else None,
        },
    }
    if support_report.get("report") is not None:
        report["bench_readiness"] = support_report["report"]
    return report


def next_actions_for_checks(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actions = {
        "support_bundle_bench_readiness": {
            "title": "Create a support bundle with bench evidence.",
            "desktop_action": "Module Setup > Bench Report",
            "command": "./scripts/pi/create_support_bundle.sh",
            "notes": "Run after the terrain bundle, runtime logs, PX4 parameter export, and receiver proof are available.",
        },
        "px4_receiver_proof": {
            "title": "Capture PX4 external-vision receiver proof.",
            "desktop_action": "PX4 SITL capture harness, then Module Setup > Autonomy Readiness",
            "command": "VISION_NAV_SITL_SMOKE_DIR=$PWD/px4-sitl-evidence ./scripts/dev/run_px4_sitl_external_vision_capture.sh",
            "notes": "The final report must show fresh vehicle_visual_odometry samples and covariance/variance fields.",
        },
        "field_evidence_proof": {
            "title": "Register real field replay cases.",
            "desktop_action": "Module Setup > Field Evidence Case",
            "command": "./scripts/pi/register_field_replay_case.sh",
            "notes": "Cover good texture, low texture, blur, seasonal change, lighting change, altitude/scale change, repeated patterns, and wrong map.",
        },
        "feature_method_benchmark": {
            "title": "Benchmark feature methods on field logs.",
            "desktop_action": "Module Setup > Feature Benchmark",
            "command": "./scripts/pi/run_feature_method_benchmark.sh",
            "notes": "Use the same field replay log to compare ORB, AKAZE, SIFT, and the neural placeholder path.",
        },
        "threshold_tuning": {
            "title": "Tune replay gates against field logs.",
            "desktop_action": "Module Setup > Threshold Tuning",
            "command": "./scripts/pi/run_threshold_tuning_report.sh",
            "notes": "Run after all required field conditions have passing replay evidence.",
        },
    }
    next_actions: list[dict[str, Any]] = []
    for check in checks:
        name = str(check.get("name") or "")
        if check.get("status") == "passed" or name not in actions:
            continue
        details = check.get("details") if isinstance(check.get("details"), dict) else {}
        missing_conditions = next_action_missing_conditions(name, details)
        action = {
            "check": name,
            "status": str(check.get("status") or "unknown"),
            **actions[name],
        }
        if missing_conditions:
            action["missing_conditions"] = missing_conditions
            action["notes"] = f"{action['notes']} Missing conditions: {', '.join(missing_conditions)}."
        next_actions.append(
            action
        )
    return next_actions


def next_action_missing_conditions(name: str, details: dict[str, Any]) -> list[str]:
    raw = details.get("missing_conditions")
    if raw is None and name in {"field_evidence_proof", "threshold_tuning"}:
        raw = details.get("required_conditions") or REQUIRED_FIELD_CONDITIONS
    if not isinstance(raw, (list, tuple, set)):
        return []
    return [str(value) for value in raw if str(value)]


def evaluate_support_bundle(
    path: str | Path | None,
    *,
    require_px4_evidence: bool = True,
    require_feature_method_benchmark: bool = True,
    require_field_evidence: bool = True,
) -> dict[str, Any]:
    if path is None:
        return {
            "check": failed(
                "support_bundle_bench_readiness",
                "Strict autonomy readiness requires a support bundle with bench evidence.",
            ),
            "report": None,
            "manifest": None,
        }
    try:
        manifest = load_support_manifest(path)
        report = evaluate_bench_readiness_file(
            path,
            require_px4_evidence=require_px4_evidence,
            require_feature_method_benchmark=require_feature_method_benchmark,
            require_field_evidence=require_field_evidence,
        )
    except Exception as exc:
        return {
            "check": failed("support_bundle_bench_readiness", f"Could not evaluate support bundle: {exc}"),
            "report": None,
            "manifest": None,
        }
    status = normalize_status(report.get("status"))
    details = {
        "support_bundle_path": report.get("support_bundle_path"),
        "summary": report.get("summary"),
    }
    if status == "passed":
        return {"check": passed("support_bundle_bench_readiness", "Support bundle bench-readiness gate passed.", details), "report": report, "manifest": manifest}
    if status == "degraded":
        return {
            "check": degraded("support_bundle_bench_readiness", "Support bundle bench-readiness gate is degraded.", details),
            "report": report,
            "manifest": manifest,
        }
    return {
        "check": failed("support_bundle_bench_readiness", f"Support bundle bench-readiness gate is {status or 'missing'}.", details),
        "report": report,
        "manifest": manifest,
    }


def support_checks_by_name(support_report: dict[str, Any]) -> dict[str, dict[str, Any]]:
    report = support_report.get("report")
    if not isinstance(report, dict):
        return {}
    return {str(check.get("name")): check for check in report.get("checks", []) if isinstance(check, dict)}


def check_doc_markers(name: str, path: str | Path, markers: list[str], message: str) -> dict[str, Any]:
    source = Path(path).expanduser()
    if not source.exists():
        return failed(name, f"Missing {source}.", {"path": str(source)})
    text = source.read_text(errors="replace")
    missing = [marker for marker in markers if marker not in text]
    details = {"path": str(source), "missing_markers": missing}
    if missing:
        return failed(name, f"{source} is missing required plan sections.", details)
    return passed(name, message, details)


def check_px4_receiver_proof(
    session_path: str | Path | None,
    report_path: str | Path | None,
    support_checks: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    if session_path is not None:
        try:
            report = evaluate_px4_sitl_session(session_path)
        except Exception as exc:
            return failed("px4_receiver_proof", f"Could not evaluate PX4 SITL session: {exc}", {"path": str(session_path)})
        return px4_receiver_check_from_report(report, source="px4_sitl_session")

    if report_path is not None:
        source = Path(report_path).expanduser()
        try:
            report = json.loads(source.read_text())
        except Exception as exc:
            return failed("px4_receiver_proof", f"Could not read PX4 receiver report: {exc}", {"path": str(source)})
        return px4_receiver_check_from_report(report, source=str(source))

    support_check = support_checks.get("px4_sitl_evidence")
    if support_check and support_check.get("status") == "passed":
        return passed("px4_receiver_proof", "PX4 receiver proof is present in the support bundle.", {"source": "support_bundle"})
    if support_check and support_check.get("status") == "degraded":
        return degraded("px4_receiver_proof", "PX4 receiver proof in the support bundle is degraded.", {"source": "support_bundle"})
    return failed("px4_receiver_proof", "PX4 SITL or bench receiver proof is required.", {"source": "support_bundle"})


def px4_receiver_check_from_report(report: dict[str, Any], *, source: str) -> dict[str, Any]:
    status = normalize_status(report.get("status"))
    listener = report.get("listener") if isinstance(report.get("listener"), dict) else {}
    details = {
        "source": source,
        "session_dir": report.get("session_dir"),
        "report_path": report.get("report_path"),
        "sample_count": listener.get("sample_count"),
        "expected_message": report.get("expected_message"),
        "latest_sample_age_s": listener.get("latest_sample_age_s"),
    }
    if status == "passed":
        return passed("px4_receiver_proof", "PX4 receiver evidence passed.", details)
    if status == "degraded":
        return degraded("px4_receiver_proof", "PX4 receiver evidence is degraded.", details)
    return failed("px4_receiver_proof", f"PX4 receiver evidence is {status or 'missing'}.", details)


def check_field_evidence_proof(path: str | Path | None, support_checks: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if path is not None:
        try:
            report = json.loads(Path(path).expanduser().read_text())
        except Exception as exc:
            return failed("field_evidence_proof", f"Could not read field evidence report: {exc}", {"path": str(path)})
        return field_evidence_check_from_report(report, source=str(Path(path).expanduser()))

    support_check = support_checks.get("field_evidence")
    if support_check and support_check.get("status") == "passed":
        details = {"source": "support_bundle", **(support_check.get("details") or {})}
        missing_conditions = missing_required_conditions(details.get("covered_conditions") or [])
        if missing_conditions:
            return failed("field_evidence_proof", "Field evidence is missing required real-world conditions.", {**details, "missing_conditions": missing_conditions})
        return passed("field_evidence_proof", "Field evidence proof is present in the support bundle.", details)
    if support_check and support_check.get("status") == "degraded":
        return degraded("field_evidence_proof", "Field evidence proof in the support bundle is degraded.", {"source": "support_bundle"})
    return failed("field_evidence_proof", "Real field evidence is required for autonomy readiness.", {"source": "support_bundle"})


def field_evidence_check_from_report(report: dict[str, Any], *, source: str) -> dict[str, Any]:
    status = normalize_status(report.get("status"))
    summary = report.get("summary") or {}
    covered = summary.get("covered_conditions") or []
    missing_conditions = missing_required_conditions(covered)
    details = {
        "source": source,
        "coverage_status": summary.get("coverage_status"),
        "replay_status": summary.get("replay_status"),
        "case_count": summary.get("case_count"),
        "field_case_count": summary.get("field_case_count"),
        "covered_conditions": covered,
        "missing_conditions": missing_conditions,
    }
    if status == "passed" and not missing_conditions:
        return passed("field_evidence_proof", "Field evidence gate passed for all required conditions.", details)
    if status == "passed":
        return failed("field_evidence_proof", "Field evidence gate passed but did not cover all required conditions.", details)
    if status == "degraded":
        return degraded("field_evidence_proof", "Field evidence gate is degraded.", details)
    return failed("field_evidence_proof", f"Field evidence gate is {status or 'missing'}.", details)


def check_feature_method_proof(path: str | Path | None, support_checks: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if path is not None:
        source = Path(path).expanduser()
        try:
            report = json.loads(source.read_text())
        except Exception as exc:
            return failed(
                "feature_method_benchmark",
                f"Could not read feature-method benchmark report: {exc}",
                {"path": str(source)},
            )
        return feature_method_check_from_report(report, source=str(source))

    support_check = support_checks.get("feature_method_benchmarks")
    if support_check and support_check.get("status") == "passed":
        return passed("feature_method_benchmark", "Feature-method benchmark evidence is present in the support bundle.", support_check.get("details") or {})
    if support_check and support_check.get("status") == "degraded":
        return degraded("feature_method_benchmark", "Feature-method benchmark evidence is degraded.", support_check.get("details") or {})
    return failed("feature_method_benchmark", "Feature-method benchmark evidence on field logs is required.", {})


def feature_method_check_from_report(report: dict[str, Any], *, source: str) -> dict[str, Any]:
    status = normalize_status(report.get("status"))
    methods = report.get("methods") if isinstance(report.get("methods"), list) else []
    passed_methods = [
        str(method.get("method"))
        for method in methods
        if isinstance(method, dict) and normalize_status(method.get("status")) == "passed" and method.get("method")
    ]
    degraded_methods = [
        str(method.get("method"))
        for method in methods
        if isinstance(method, dict) and normalize_status(method.get("status")) == "degraded" and method.get("method")
    ]
    recommended_method = report.get("recommended_method")
    details = {
        "source": source,
        "case_name": report.get("case_name"),
        "expected": report.get("expected"),
        "recommended_method": recommended_method,
        "method_count": len(methods),
        "passed_methods": passed_methods,
        "degraded_methods": degraded_methods,
    }
    if status == "passed" and recommended_method and passed_methods:
        return passed("feature_method_benchmark", "Feature-method benchmark report passed.", details)
    if status == "passed":
        return failed("feature_method_benchmark", "Feature-method benchmark report passed without a usable recommended method.", details)
    if status == "degraded":
        return degraded("feature_method_benchmark", "Feature-method benchmark report is degraded.", details)
    return failed("feature_method_benchmark", f"Feature-method benchmark report is {status or 'missing'}.", details)


def check_threshold_tuning(path: str | Path | None, *, support_manifest: dict[str, Any] | None = None) -> dict[str, Any]:
    if path is None:
        threshold_tuning = (support_manifest or {}).get("threshold_tuning") if support_manifest else None
        if isinstance(threshold_tuning, dict):
            return threshold_tuning_check_from_summary(threshold_tuning)
        return failed(
            "threshold_tuning",
            "Threshold tuning report is required after real field logs are collected.",
            {"required_conditions": REQUIRED_FIELD_CONDITIONS},
        )
    source = Path(path).expanduser()
    try:
        report = json.loads(source.read_text())
    except Exception as exc:
        return failed("threshold_tuning", f"Could not read threshold tuning report: {exc}", {"path": str(source)})
    return threshold_tuning_check_from_report(report, source=str(source))


def threshold_tuning_check_from_summary(summary: dict[str, Any]) -> dict[str, Any]:
    reports = summary.get("reports") if isinstance(summary.get("reports"), list) else []
    status = normalize_status(summary.get("status"))
    covered = summary.get("covered_conditions") or []
    if not covered and reports:
        covered = [
            condition
            for report in reports
            if isinstance(report, dict)
            for condition in report.get("covered_conditions", [])
        ]
    details = {
        "source": "support_bundle",
        "report_count": summary.get("report_count"),
        "field_case_count": summary.get("field_case_count"),
        "covered_conditions": sorted({str(condition) for condition in covered}),
        "missing_conditions": missing_required_conditions(covered),
    }
    if status == "not_provided" or status is None:
        return failed("threshold_tuning", "Threshold tuning report is required after real field logs are collected.", details)
    if status == "passed" and not details["missing_conditions"]:
        return passed("threshold_tuning", "Threshold tuning proof is present in the support bundle.", details)
    if status == "passed":
        return failed("threshold_tuning", "Threshold tuning proof is missing required conditions.", details)
    if status == "degraded":
        return degraded("threshold_tuning", "Threshold tuning proof in the support bundle is degraded.", details)
    return failed("threshold_tuning", f"Threshold tuning proof in the support bundle is {status}.", details)


def threshold_tuning_check_from_report(report: dict[str, Any], *, source: str) -> dict[str, Any]:
    status = normalize_status(report.get("status"))
    covered = report_conditions(report)
    missing_conditions = missing_required_conditions(covered)
    details = {
        "source": source,
        "covered_conditions": sorted(covered),
        "missing_conditions": missing_conditions,
        "method": report.get("method"),
    }
    if status == "passed" and not missing_conditions:
        return passed("threshold_tuning", "Thresholds were tuned against all required field conditions.", details)
    if status == "passed":
        return failed("threshold_tuning", "Threshold tuning report is missing required conditions.", details)
    if status == "degraded":
        return degraded("threshold_tuning", "Threshold tuning report is degraded.", details)
    return failed("threshold_tuning", f"Threshold tuning report is {status or 'missing'}.", details)


def report_conditions(report: dict[str, Any]) -> set[str]:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    raw = (
        report.get("conditions")
        or report.get("covered_conditions")
        or summary.get("covered_conditions")
        or summary.get("tuned_conditions")
        or []
    )
    if not isinstance(raw, list):
        return set()
    return {str(value) for value in raw if str(value)}


def missing_required_conditions(covered: Any) -> list[str]:
    if not isinstance(covered, (list, tuple, set)):
        covered_set: set[str] = set()
    else:
        covered_set = {str(value) for value in covered if str(value)}
    return [condition for condition in REQUIRED_FIELD_CONDITIONS if condition not in covered_set]


def print_human(report: dict[str, Any]) -> None:
    print("Autonomy readiness audit")
    print(f"Status: {report['status']}")
    for check in report["checks"]:
        print(f"- {check['name']}: {check['status']} - {check['message']}")
    if report.get("next_actions"):
        print("Next actions:")
        for action in report["next_actions"]:
            print(f"- {action.get('check')}: {action.get('desktop_action')} / {action.get('command')}")


def main() -> None:
    args = parse_args()
    report = evaluate_autonomy_readiness(
        research_doc_path=args.research_doc,
        implementation_plan_path=args.implementation_plan,
        support_bundle_path=args.support_bundle,
        px4_sitl_session_path=args.px4_sitl_session,
        px4_sitl_report_path=args.px4_sitl_report,
        field_evidence_report_path=args.field_evidence_report,
        feature_method_benchmark_report_path=args.feature_method_benchmark_report,
        threshold_tuning_report_path=args.threshold_tuning_report,
    )
    if args.output:
        destination = Path(args.output).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human(report)
    if report["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
