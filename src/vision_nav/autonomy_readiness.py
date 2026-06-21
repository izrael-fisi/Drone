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
        "--field-evidence-report",
        help="Optional field-evidence gate report to evaluate directly.",
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
    field_evidence_report_path: str | Path | None = None,
    threshold_tuning_report_path: str | Path | None = None,
) -> dict[str, Any]:
    support_report = evaluate_support_bundle(support_bundle_path)
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
        check_px4_receiver_proof(px4_sitl_session_path, support_checks),
        check_field_evidence_proof(field_evidence_report_path, support_checks),
        check_feature_method_proof(support_checks),
        check_threshold_tuning(threshold_tuning_report_path, support_manifest=support_manifest),
    ]
    report = {
        "status": readiness_status(checks),
        "checks": checks,
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
            "field_evidence_report": str(Path(field_evidence_report_path).expanduser()) if field_evidence_report_path else None,
            "threshold_tuning_report": str(Path(threshold_tuning_report_path).expanduser()) if threshold_tuning_report_path else None,
        },
    }
    if support_report.get("report") is not None:
        report["bench_readiness"] = support_report["report"]
    return report


def evaluate_support_bundle(path: str | Path | None) -> dict[str, Any]:
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
            require_feature_method_benchmark=True,
            require_field_evidence=True,
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


def check_px4_receiver_proof(path: str | Path | None, support_checks: dict[str, dict[str, Any]]) -> dict[str, Any]:
    if path is not None:
        try:
            report = evaluate_px4_sitl_session(path)
        except Exception as exc:
            return failed("px4_receiver_proof", f"Could not evaluate PX4 SITL session: {exc}", {"path": str(path)})
        status = normalize_status(report.get("status"))
        details = {
            "source": "px4_sitl_session",
            "session_dir": report.get("session_dir"),
            "sample_count": (report.get("listener") or {}).get("sample_count") if isinstance(report.get("listener"), dict) else None,
            "expected_message": report.get("expected_message"),
        }
        if status == "passed":
            return passed("px4_receiver_proof", "PX4 receiver evidence passed.", details)
        if status == "degraded":
            return degraded("px4_receiver_proof", "PX4 receiver evidence is degraded.", details)
        return failed("px4_receiver_proof", f"PX4 receiver evidence is {status or 'missing'}.", details)

    support_check = support_checks.get("px4_sitl_evidence")
    if support_check and support_check.get("status") == "passed":
        return passed("px4_receiver_proof", "PX4 receiver proof is present in the support bundle.", {"source": "support_bundle"})
    if support_check and support_check.get("status") == "degraded":
        return degraded("px4_receiver_proof", "PX4 receiver proof in the support bundle is degraded.", {"source": "support_bundle"})
    return failed("px4_receiver_proof", "PX4 SITL or bench receiver proof is required.", {"source": "support_bundle"})


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


def check_feature_method_proof(support_checks: dict[str, dict[str, Any]]) -> dict[str, Any]:
    support_check = support_checks.get("feature_method_benchmarks")
    if support_check and support_check.get("status") == "passed":
        return passed("feature_method_benchmark", "Feature-method benchmark evidence is present in the support bundle.", support_check.get("details") or {})
    if support_check and support_check.get("status") == "degraded":
        return degraded("feature_method_benchmark", "Feature-method benchmark evidence is degraded.", support_check.get("details") or {})
    return failed("feature_method_benchmark", "Feature-method benchmark evidence on field logs is required.", {})


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


def main() -> None:
    args = parse_args()
    report = evaluate_autonomy_readiness(
        research_doc_path=args.research_doc,
        implementation_plan_path=args.implementation_plan,
        support_bundle_path=args.support_bundle,
        px4_sitl_session_path=args.px4_sitl_session,
        field_evidence_report_path=args.field_evidence_report,
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
