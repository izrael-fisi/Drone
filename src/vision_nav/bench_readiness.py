from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
from typing import Any
import zipfile


PASSING = {"passed", "healthy"}
WARNING = {"degraded", "warming_up"}
MISSING = {"not_provided", None}
REQUIRED_GNSS_DENIED_CHECKS = {
    "satellite_source_disabled",
    "map_position_reset",
    "home_position",
    "heading",
    "estimator_health",
}
REQUIRED_PX4_RECEIVER_MESSAGE = "odometry"
VALIDATE_TERRAIN_BUNDLE_COMMAND = "./scripts/pi/validate_terrain_bundle.sh"
CHECK_GNSS_DENIED_PLAN_COMMAND = "./scripts/pi/check_gnss_denied_plan.sh"
GNSS_DENIED_BUNDLE_COMMAND = f"{CHECK_GNSS_DENIED_PLAN_COMMAND} && {VALIDATE_TERRAIN_BUNDLE_COMMAND}"
BENCH_NEXT_ACTIONS = {
    "bundle_health": {
        "title": "Rebuild or validate the terrain bundle.",
        "desktop_action": "Mission Planner > Build Bundle, then Module Setup > Bench Report",
        "command": VALIDATE_TERRAIN_BUNDLE_COMMAND,
        "notes": "The support bundle must include passing terrain bundle health before bench readiness can pass.",
    },
    "gnss_denied_plan": {
        "title": "Complete GNSS-denied mission prep before rebuilding the bundle.",
        "desktop_action": "Mission Planner > GNSS-Denied Prep, then Build/Upload Bundle and Bench Report",
        "command": GNSS_DENIED_BUNDLE_COMMAND,
        "notes": "Rebuild the bundle after satellite source, map reset, home reset, heading, and estimator checks are ready.",
    },
    "runtime_logs": {
        "title": "Capture a terrain runtime log.",
        "desktop_action": "Module Setup > Field Log Capture, then Bench Report",
        "command": "VISION_NAV_COUNT=30 ./scripts/pi/run_terrain_nav_loop.sh",
        "notes": "Create the support bundle after Field Log Capture produces terrain_matches.jsonl.",
    },
    "runtime_status": {
        "title": "Capture runtime status with the terrain log.",
        "desktop_action": "Module Setup > Field Log Capture, Runtime Status, then Bench Report",
        "command": "VISION_NAV_COUNT=30 ./scripts/pi/run_terrain_nav_loop.sh && ./scripts/pi/read_runtime_status.sh",
        "notes": "Runtime status proves active map, output path, estimator health, and latest match state.",
    },
    "replay_gates": {
        "title": "Run guided field replay evidence.",
        "desktop_action": "Module Setup > Load Next Field Condition, then Evidence Workflow",
        "command": "./scripts/pi/run_autonomy_evidence_workflow.sh",
        "notes": "The workflow captures, validates, and registers condition-specific logs.",
    },
    "px4_sitl_evidence": {
        "title": "Capture PX4 receiver proof.",
        "desktop_action": "Module Setup > PX4 SITL Receiver Capture, then Bench Report",
        "command": "VISION_NAV_SITL_SMOKE_DIR=$PWD/px4-sitl-evidence ./scripts/dev/run_px4_sitl_external_vision_capture.sh",
        "notes": "Receiver proof must show the MAVLink ODOMETRY path arriving as vehicle_visual_odometry samples.",
    },
    "px4_params": {
        "title": "Export and check PX4 external-vision parameters.",
        "desktop_action": "Module Setup > PX4 parameter check, then Bench Report",
        "command": "./scripts/pi/check_px4_params.sh",
        "notes": "Export PX4 parameters from QGroundControl or the PX4 shell before creating the support bundle.",
    },
    "feature_method_benchmarks": {
        "title": "Benchmark feature methods on field logs.",
        "desktop_action": "Module Setup > Feature Benchmark",
        "command": "./scripts/pi/run_feature_method_benchmark.sh",
        "notes": "Use real field logs to compare ORB, AKAZE, SIFT, and neural descriptor options.",
    },
    "field_evidence": {
        "title": "Collect and register field replay proof.",
        "desktop_action": "Module Setup > Evidence Workflow",
        "command": "./scripts/pi/run_autonomy_evidence_workflow.sh",
        "notes": "Field evidence must cover all required terrain conditions with real captured logs.",
    },
    "threshold_tuning": {
        "title": "Tune replay gates against field logs.",
        "desktop_action": "Module Setup > Threshold Tuning",
        "command": "./scripts/pi/run_threshold_tuning_report.sh",
        "notes": "Threshold tuning should run after the field-evidence manifest passes.",
    },
    "rosbag_export_validations": {
        "title": "Export and validate the ROS replay artifact.",
        "desktop_action": "Module Setup > ROS Bag Validation, then Bench Report",
        "command": "./scripts/pi/run_rosbag_export_validation.sh && ./scripts/pi/create_support_bundle.sh",
        "notes": "Support bundles should include a passed ROS replay export validation summary.",
    },
    "rosbag2_cli_reviews": {
        "title": "Review the native rosbag2 export.",
        "desktop_action": "Module Setup > Native rosbag2 Review, then Bench Report",
        "command": "./scripts/dev/run_rosbag2_cli_review.sh && ./scripts/pi/create_support_bundle.sh",
        "notes": "Run on a sourced ROS 2 workstation when native rosbag2 export is part of the evidence package.",
    },
    "ardupilot_params": {
        "title": "Review ArduPilot ExternalNav parameters.",
        "desktop_action": "Module Setup > ArduPilot parameter check",
        "command": "./scripts/pi/check_ardupilot_params.sh",
        "notes": "ArduPilot remains optional for the PX4-first bench path unless explicitly required.",
    },
}


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
        check_gnss_denied_plan(manifest),
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
    rosbag_export_check = check_rosbag_export_validations(manifest)
    if rosbag_export_check is not None:
        checks.append(rosbag_export_check)
    rosbag2_cli_check = check_rosbag2_cli_reviews(manifest)
    if rosbag2_cli_check is not None:
        checks.append(rosbag2_cli_check)
    status = readiness_status(checks)
    next_actions = next_actions_for_checks(
        checks,
        field_next_condition=field_collection_next_condition(manifest),
    )
    return {
        "status": status,
        "support_bundle": manifest.get("name"),
        "generated_at": manifest.get("metadata", {}).get("generated_at"),
        "checks": checks,
        "next_actions": next_actions,
        "summary": {
            "failed": sum(1 for check in checks if check["status"] == "failed"),
            "degraded": sum(1 for check in checks if check["status"] == "degraded"),
            "passed": sum(1 for check in checks if check["status"] == "passed"),
            "next_actions": len(next_actions),
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


def check_gnss_denied_plan(manifest: dict[str, Any]) -> dict[str, Any]:
    bundle = manifest.get("bundle") if isinstance(manifest.get("bundle"), dict) else {}
    mission_plan = bundle.get("mission_plan") if isinstance(bundle.get("mission_plan"), dict) else {}
    gnss = mission_plan.get("gnss_denied") if isinstance(mission_plan.get("gnss_denied"), dict) else {}
    checks = [check for check in gnss.get("checks") or [] if isinstance(check, dict)]
    check_statuses = {str(check.get("name")): normalize_status(check.get("status")) for check in checks if check.get("name")}
    missing_checks = sorted(REQUIRED_GNSS_DENIED_CHECKS - set(check_statuses))
    failed_checks = sorted(name for name, status in check_statuses.items() if name in REQUIRED_GNSS_DENIED_CHECKS and status not in PASSING)
    field_ready = {
        "satellite_source_disabled": gnss.get("satellite_source_disabled") is True,
        "map_position_reset": gnss.get("map_position_reset_set") is True,
        "home_position": gnss.get("home_position_set") is True,
        "heading": gnss.get("heading_set") is True,
        "estimator_health": normalize_status(gnss.get("estimator_health")) == "ready",
    }
    legacy_ready = all(field_ready.values())
    status = normalize_status(gnss.get("status"))
    details = {
        "mission_plan_path": mission_plan.get("path"),
        "mission_plan_status": mission_plan.get("status"),
        "gnss_denied_status": status,
        "mission_item_count": mission_plan.get("mission_item_count"),
        "missing_checks": missing_checks,
        "failed_checks": failed_checks,
        "field_ready": field_ready,
    }

    if not mission_plan or normalize_status(mission_plan.get("status")) in MISSING:
        return degraded("gnss_denied_plan", "Support bundle has no mission-plan GNSS-denied summary.", details)
    if normalize_status(mission_plan.get("status")) == "failed":
        return failed("gnss_denied_plan", "Mission plan could not be parsed for GNSS-denied readiness.", details)
    if not gnss or status in MISSING:
        if legacy_ready and not checks:
            return passed("gnss_denied_plan", "GNSS-denied mission-plan readiness is complete from legacy fields.", details)
        return failed("gnss_denied_plan", "Mission plan does not declare GNSS-denied readiness.", details)
    if status in {"ready", "passed"} and not failed_checks and (not missing_checks or legacy_ready):
        return passed("gnss_denied_plan", "GNSS-denied mission-plan readiness is complete.", details)
    return failed("gnss_denied_plan", "GNSS-denied mission-plan readiness is incomplete.", details)


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
    external_warnings = external.get("last_warnings") if isinstance(external.get("last_warnings"), list) else []
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
        "external_position_message_type": external.get("message_type"),
        "external_position_warnings": external_warnings,
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
    if external_status in WARNING or external_warnings:
        return degraded("runtime_status", "Runtime external-position health is degraded.", details)
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
    config = evidence.get("config") or {}
    details = {
        "sample_count": listener.get("sample_count"),
        "observed_rate_hz": listener.get("observed_rate_hz"),
        "latest_sample_age_s": listener.get("latest_sample_age_s"),
        "expected_message": evidence.get("expected_message"),
        "expected_rate_hz": config.get("expected_rate_hz"),
        "min_rate_ratio": config.get("min_rate_ratio"),
        "required_message": REQUIRED_PX4_RECEIVER_MESSAGE,
    }
    if status in MISSING:
        if allow_missing:
            return degraded("px4_sitl_evidence", "PX4 receiver evidence was not provided.", details)
        return failed("px4_sitl_evidence", "PX4 receiver evidence is required for bench readiness.", details)
    if str(details.get("expected_message") or "").lower() != REQUIRED_PX4_RECEIVER_MESSAGE:
        return failed(
            "px4_sitl_evidence",
            "PX4 receiver evidence must prove the MAVLink ODOMETRY path for bench readiness.",
            details,
        )
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
        "capture_metadata_issue_count": evidence.get("capture_metadata_issue_count"),
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


def check_rosbag_export_validations(manifest: dict[str, Any]) -> dict[str, Any] | None:
    validations = manifest.get("rosbag_export_validations") or {}
    status = normalize_status(validations.get("status"))
    reports = validations.get("reports") if isinstance(validations.get("reports"), list) else []
    details = {
        "report_count": validations.get("report_count"),
        "formats": validations.get("formats"),
        "message_count": validations.get("message_count"),
        "topic_count": validations.get("topic_count"),
        "failed_formats": [
            report.get("format")
            for report in reports
            if isinstance(report, dict) and normalize_status(report.get("status")) == "failed"
        ][:5],
    }
    if status in MISSING:
        return None
    if status in PASSING:
        return passed("rosbag_export_validations", "ROS bag export validation reports passed.", details)
    if status in WARNING:
        return degraded("rosbag_export_validations", "ROS bag export validation reports are degraded.", details)
    return failed("rosbag_export_validations", f"ROS bag export validation reports are {status}.", details)


def check_rosbag2_cli_reviews(manifest: dict[str, Any]) -> dict[str, Any] | None:
    reviews = manifest.get("rosbag2_cli_reviews") or {}
    status = normalize_status(reviews.get("status"))
    reports = reviews.get("reports") if isinstance(reviews.get("reports"), list) else []
    details = {
        "report_count": reviews.get("report_count"),
        "failed_bags": [
            report.get("bag_dir") or report.get("artifact_path")
            for report in reports
            if isinstance(report, dict) and normalize_status(report.get("status")) == "failed"
        ][:5],
        "degraded_bags": [
            report.get("bag_dir") or report.get("artifact_path")
            for report in reports
            if isinstance(report, dict) and normalize_status(report.get("status")) == "degraded"
        ][:5],
    }
    if status in MISSING:
        return None
    if status in PASSING:
        return passed("rosbag2_cli_reviews", "Native rosbag2 CLI review reports passed.", details)
    if status in WARNING:
        return degraded("rosbag2_cli_reviews", "Native rosbag2 CLI review reports are degraded.", details)
    return failed("rosbag2_cli_reviews", f"Native rosbag2 CLI review reports are {status}.", details)


def readiness_status(checks: list[dict[str, Any]]) -> str:
    if any(check["status"] == "failed" for check in checks):
        return "failed"
    if any(check["status"] == "degraded" for check in checks):
        return "degraded"
    return "passed"


def next_actions_for_checks(
    checks: list[dict[str, Any]],
    *,
    field_next_condition: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for check in checks:
        name = str(check.get("name") or "")
        status = normalize_status(check.get("status"))
        if status == "passed":
            continue
        spec = BENCH_NEXT_ACTIONS.get(name)
        if spec is None:
            continue
        action = {
            "check": name,
            "status": status or "unknown",
            **spec,
        }
        if check.get("message"):
            action["message"] = str(check["message"])
        if name in {"bundle_health", "gnss_denied_plan"}:
            enrich_action_with_field_bundle(action, field_next_condition)
        elif name == "runtime_logs":
            enrich_action_with_field_capture(action, field_next_condition)
        elif name == "runtime_status":
            enrich_action_with_field_capture(action, field_next_condition, append_runtime_status_read=True)
        actions.append(action)
    return actions


def field_collection_next_condition(manifest: dict[str, Any]) -> dict[str, Any] | None:
    plans = manifest.get("field_collection_plans") if isinstance(manifest.get("field_collection_plans"), dict) else {}
    reports = plans.get("reports") if isinstance(plans.get("reports"), list) else []
    for report in reports:
        if not isinstance(report, dict):
            continue
        raw_condition = report.get("next_condition") if isinstance(report.get("next_condition"), dict) else None
        if raw_condition:
            condition = dict(raw_condition)
            if not condition.get("bundle") and isinstance(report.get("bundle"), str):
                condition["bundle"] = report["bundle"]
            return condition
    raw_condition = plans.get("next_condition") if isinstance(plans.get("next_condition"), dict) else None
    if raw_condition:
        return dict(raw_condition)
    return None


def enrich_action_with_field_bundle(action: dict[str, Any], condition: dict[str, Any] | None) -> None:
    if not condition:
        return
    bundle = condition.get("bundle")
    if not isinstance(bundle, str) or not bundle.strip():
        return
    action["command"] = field_bundle_action_command(bundle, action)
    action["field_bundle"] = bundle
    action["notes"] = " ".join([str(action.get("notes") or ""), f"Selected field-plan bundle: {bundle}."]).strip()


def field_bundle_action_command(bundle: str, action: dict[str, Any]) -> str:
    if action_targets_gnss_denied_plan(action):
        quoted = shlex.quote(str(bundle))
        return (
            f"VISION_NAV_BUNDLE={quoted} {CHECK_GNSS_DENIED_PLAN_COMMAND} && "
            f"VISION_NAV_BUNDLE={quoted} {VALIDATE_TERRAIN_BUNDLE_COMMAND}"
        )
    return shell_command({"VISION_NAV_BUNDLE": bundle}, VALIDATE_TERRAIN_BUNDLE_COMMAND)


def action_targets_gnss_denied_plan(action: dict[str, Any]) -> bool:
    return (
        action.get("check") == "gnss_denied_plan"
        or action.get("bench_subcheck") == "gnss_denied_plan"
        or action.get("desktop_action") == "Mission Planner > GNSS-Denied Prep, Build Bundle, Upload Bundle"
    )


def enrich_action_with_field_capture(
    action: dict[str, Any],
    condition: dict[str, Any] | None,
    *,
    append_runtime_status_read: bool = False,
) -> None:
    if not condition:
        return
    capture_command = condition.get("capture_command")
    if isinstance(capture_command, str) and capture_command.strip():
        action["command"] = command_with_runtime_status_read(capture_command) if append_runtime_status_read else capture_command

    field_mappings = {
        "field_condition": "condition",
        "field_label": "label",
        "field_expected": "expected",
        "field_capture_output_dir": "capture_output_dir",
        "field_source_log": "source_log",
        "field_runtime_status_path": "runtime_status_path",
        "field_bundle": "bundle",
        "field_metadata_update_command": "metadata_update_command",
        "field_register_command": "register_command",
    }
    for target_key, source_key in field_mappings.items():
        value = condition.get(source_key)
        if isinstance(value, str) and value.strip():
            action[target_key] = value

    detail_lines = []
    label = condition.get("label") or condition.get("condition")
    condition_name = condition.get("condition")
    if label and condition_name:
        detail_lines.append(f"Next pending field condition: {label} ({condition_name}).")
    elif condition_name:
        detail_lines.append(f"Next pending field condition: {condition_name}.")
    if condition.get("source_log"):
        detail_lines.append(f"Expected log: {condition['source_log']}.")
    if condition.get("capture_output_dir"):
        detail_lines.append(f"Output: {condition['capture_output_dir']}.")
    if condition.get("runtime_status_path"):
        detail_lines.append(f"Runtime status: {condition['runtime_status_path']}.")
    if detail_lines:
        action["notes"] = " ".join([str(action.get("notes") or ""), *detail_lines]).strip()


def command_with_runtime_status_read(command: str) -> str:
    if "read_runtime_status.sh" in command:
        return command
    return f"{command} && ./scripts/pi/read_runtime_status.sh"


def shell_command(env: dict[str, str], command: str) -> str:
    parts = [f"{key}={shlex.quote(str(value))}" for key, value in env.items() if str(value)]
    return " \\\n  ".join(parts + [command])


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
    if report.get("next_actions"):
        print("Next actions:")
        for action in report["next_actions"]:
            print(f"- {action.get('title')} [{action.get('status')}]")
            if action.get("desktop_action"):
                print(f"  app: {action['desktop_action']}")
            if action.get("command"):
                print(f"  command: {action['command']}")


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
