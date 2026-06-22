from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
from pathlib import Path
import platform
import shutil
import subprocess
import sys
import zipfile
from typing import Any

from vision_nav.ardupilot_params import evaluate_ardupilot_param_file
from vision_nav.bench_readiness import evaluate_bench_readiness
from vision_nav.bundle import load_manifest
from vision_nav.geospatial_health import write_geospatial_health_report
from vision_nav.px4_params import evaluate_px4_param_file
from vision_nav.px4_sitl_evidence import Px4SitlEvidenceConfig, evaluate_px4_sitl_evidence
from vision_nav.px4_sitl_session import evaluate_px4_sitl_session
from vision_nav.replay_gates import ReplayGateConfig, evaluate_replay_log
from vision_nav.replay_case_schema import evaluate_replay_case_schema
from vision_nav.summarize_match_log import summarize_log


DEFAULT_MAX_LOG_BYTES = 50 * 1024 * 1024


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Create a field support bundle for a vision-nav bench run.")
    parser.add_argument("--bundle", help="Mission/terrain bundle directory or manifest.json path.")
    parser.add_argument("--log", action="append", default=[], help="Runtime or replay JSONL log. Can be repeated.")
    parser.add_argument("--extra", action="append", default=[], help="Extra file or directory to include. Can be repeated.")
    parser.add_argument("--repo", default=".", help="Repo path for git/app metadata. Defaults to current directory.")
    parser.add_argument("--output-dir", default="support-bundles", help="Directory for generated support bundles.")
    parser.add_argument("--name", help="Stable bundle name. Defaults to timestamp plus bundle id.")
    parser.add_argument("--autopilot-metadata", help="Optional JSON file with PX4/ArduPilot metadata.")
    parser.add_argument("--mavlink-endpoint", help="Optional MAVLink endpoint used during the run.")
    parser.add_argument("--px4-listener", help="Optional PX4 `listener vehicle_visual_odometry` capture text file.")
    parser.add_argument("--px4-mavlink-status", help="Optional PX4 `mavlink status` capture text file.")
    parser.add_argument("--px4-sitl-session", help="Optional PX4 SITL evidence session directory or manifest.")
    parser.add_argument("--px4-params", help="Optional PX4 parameter export file to check and include.")
    parser.add_argument("--ardupilot-params", help="Optional ArduPilot parameter export file to check and include.")
    parser.add_argument(
        "--px4-expected-message",
        choices=["odometry", "vision_position_estimate"],
        default="odometry",
        help="Expected MAVLink message path used for the PX4 SITL receiver evidence.",
    )
    parser.add_argument("--replay-case-manifest", help="Optional JSON replay-case manifest to evaluate and include.")
    parser.add_argument(
        "--feature-method-benchmark",
        action="append",
        default=[],
        help="Feature-method benchmark JSON report or output directory. Can be repeated.",
    )
    parser.add_argument(
        "--field-evidence-report",
        action="append",
        default=[],
        help="Field evidence gate JSON report. Can be repeated.",
    )
    parser.add_argument(
        "--field-collection-plan",
        action="append",
        default=[],
        help="Field collection plan JSON report. Can be repeated. Sibling Markdown checklists are copied when present.",
    )
    parser.add_argument(
        "--threshold-tuning-report",
        action="append",
        default=[],
        help="Threshold tuning JSON report. Can be repeated.",
    )
    parser.add_argument(
        "--rosbag-export-validation",
        action="append",
        default=[],
        help="ROS bag export validation JSON report or directory. Can be repeated.",
    )
    parser.add_argument(
        "--replay-case",
        action="append",
        default=[],
        help="Inline replay case as case_name:expected:log_path. Can be repeated.",
    )
    parser.add_argument("--include-map-assets", action="store_true", help="Include orthophoto/tile/descriptor assets.")
    parser.add_argument("--max-log-bytes", type=int, default=DEFAULT_MAX_LOG_BYTES, help="Copy full logs up to this size; tail larger logs.")
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON.")
    return parser.parse_args()


def safe_relpath(path: Path, root: Path | None = None) -> str:
    path = path.expanduser().resolve()
    if root is not None:
        try:
            return str(path.relative_to(root.expanduser().resolve())).replace("\\", "/")
        except ValueError:
            pass
    return path.name


def copy_file(src: Path, dst: Path) -> dict[str, Any]:
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    return {"source": str(src), "path": str(dst), "bytes": dst.stat().st_size, "truncated": False}


def copy_log(src: Path, dst: Path, *, max_bytes: int) -> dict[str, Any]:
    dst.parent.mkdir(parents=True, exist_ok=True)
    size = src.stat().st_size
    if max_bytes > 0 and size > max_bytes:
        with src.open("rb") as source:
            source.seek(max(size - max_bytes, 0))
            data = source.read()
        marker = (
            f'{{"support_bundle_note":"log truncated to last {max_bytes} bytes",'
            f'"original_bytes":{size}}}\n'
        ).encode("utf-8")
        dst.write_bytes(marker + data)
        return {"source": str(src), "path": str(dst), "bytes": dst.stat().st_size, "original_bytes": size, "truncated": True}
    return copy_file(src, dst)


def copy_tree(src: Path, dst: Path) -> dict[str, Any]:
    if dst.exists():
        shutil.rmtree(dst)
    shutil.copytree(src, dst)
    files = [path for path in dst.rglob("*") if path.is_file()]
    return {"source": str(src), "path": str(dst), "file_count": len(files), "bytes": sum(path.stat().st_size for path in files)}


def read_os_release() -> dict[str, str]:
    path = Path("/etc/os-release")
    if not path.exists():
        return {}
    values: dict[str, str] = {}
    for line in path.read_text(errors="replace").splitlines():
        if "=" not in line:
            continue
        key, value = line.split("=", 1)
        values[key] = value.strip().strip('"')
    return values


def git_metadata(repo: Path) -> dict[str, Any]:
    if not (repo / ".git").exists():
        return {"available": False, "reason": "not a git repository"}

    def run_git(*args: str) -> str | None:
        try:
            return subprocess.check_output(["git", *args], cwd=repo, text=True, stderr=subprocess.DEVNULL).strip()
        except Exception:
            return None

    return {
        "available": True,
        "commit": run_git("rev-parse", "HEAD"),
        "branch": run_git("branch", "--show-current"),
        "dirty": bool(run_git("status", "--short")),
        "remote": run_git("remote", "get-url", "origin"),
    }


def project_version(repo: Path) -> str | None:
    pyproject = repo / "pyproject.toml"
    if not pyproject.exists():
        return None
    for line in pyproject.read_text(errors="replace").splitlines():
        if line.strip().startswith("version"):
            _, value = line.split("=", 1)
            return value.strip().strip('"')
    return None


def metadata_snapshot(repo: Path, *, mavlink_endpoint: str | None = None, autopilot_metadata: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "vision_nav": {
            "project_version": project_version(repo),
            "repo": str(repo),
            "git": git_metadata(repo),
        },
        "host": {
            "platform": platform.platform(),
            "machine": platform.machine(),
            "python": sys.version,
            "os_release": read_os_release(),
        },
        "runtime": {
            "mavlink_endpoint": mavlink_endpoint,
        },
        "autopilot": autopilot_metadata,
    }


def default_name(bundle_id: str | None) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    suffix = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in (bundle_id or "vision-nav"))
    return f"{stamp}-{suffix}-support"


def copy_bundle_metadata(bundle_path: str | Path, support_dir: Path, *, include_map_assets: bool) -> dict[str, Any]:
    bundle_dir, manifest = load_manifest(bundle_path)
    bundle_root = support_dir / "bundle"
    bundle_root.mkdir(parents=True, exist_ok=True)
    copied: list[dict[str, Any]] = []
    missing: list[str] = []

    for rel in [
        "manifest.json",
        "manifest.stac.json",
        "bundle_health.json",
        "checksums.sha256",
        "config/terrain_nav.yaml",
        "calibration/down_camera.yaml",
        "calibration/camera_to_body.yaml",
        "mission/mission_plan.json",
        "mission/qgc.plan",
    ]:
        src = bundle_dir / rel
        if src.exists():
            copied.append(copy_file(src, bundle_root / rel))
        else:
            missing.append(rel)

    try:
        health = write_geospatial_health_report(bundle_dir, bundle_root / "bundle_health.generated.json")
    except Exception as exc:
        health = {"status": "failed", "error": str(exc)}

    if include_map_assets:
        for rel in ["ortho", "elevation", "imagery/tiles", "index/descriptors", "index/tiles.sqlite", "features/map_features.npz"]:
            src = bundle_dir / rel
            if src.is_dir():
                copied.append(copy_tree(src, bundle_root / rel))
            elif src.exists():
                copied.append(copy_file(src, bundle_root / rel))
            else:
                missing.append(rel)

    return {
        "bundle_dir": str(bundle_dir),
        "bundle_id": manifest.get("bundle_id"),
        "include_map_assets": include_map_assets,
        "copied": copied,
        "missing_optional": missing,
        "health": health,
        "mission_plan": summarize_mission_plan(bundle_dir, manifest),
    }


def summarize_mission_plan(bundle_dir: Path, manifest: dict[str, Any]) -> dict[str, Any]:
    mission = manifest.get("mission") if isinstance(manifest.get("mission"), dict) else {}
    candidates = [
        mission.get("desktop_plan_path"),
        "mission/mission_plan.json",
        mission.get("qgc_plan_path"),
        "mission/qgc.plan",
    ]
    seen: set[str] = set()
    for candidate in candidates:
        if not isinstance(candidate, str) or not candidate or candidate in seen:
            continue
        seen.add(candidate)
        path = bundle_dir / candidate
        if not path.exists():
            continue
        try:
            plan = json.loads(path.read_text())
        except Exception as exc:
            return {"status": "failed", "path": candidate, "error": str(exc)}
        return {
            "status": "loaded",
            "path": candidate,
            "mission_item_count": len(plan.get("mission", {}).get("items") or []),
            "gnss_denied": summarize_gnss_denied_plan(plan),
        }
    return {"status": "not_provided", "path": None}


def summarize_gnss_denied_plan(plan: dict[str, Any]) -> dict[str, Any]:
    raw = plan.get("gnss_denied") or plan.get("gnssDenied")
    vision_navigation = plan.get("visionNavigation")
    if not isinstance(raw, dict) and isinstance(vision_navigation, dict):
        raw = vision_navigation.get("gnss_denied") or vision_navigation.get("gnssDenied")
    if not isinstance(raw, dict):
        return {"status": "not_provided", "checks": []}

    checks = []
    for check in raw.get("checks") or []:
        if not isinstance(check, dict):
            continue
        checks.append(
            {
                "name": check.get("name"),
                "label": check.get("label"),
                "status": check.get("status"),
            }
        )

    return {
        "status": raw.get("status"),
        "checks": checks,
        "satellite_source_disabled": raw.get("satellite_source_disabled") is True,
        "map_position_reset_set": raw.get("map_position_reset") is not None,
        "home_position_set": raw.get("home_position") is not None,
        "heading_set": isinstance(raw.get("heading_deg"), (int, float)),
        "estimator_health": raw.get("estimator_health"),
        "updated_at": raw.get("updated_at"),
    }


def copy_logs(logs: list[str], support_dir: Path, *, max_log_bytes: int) -> dict[str, Any]:
    copied: list[dict[str, Any]] = []
    summaries: list[dict[str, Any]] = []
    runtime_statuses: list[dict[str, Any]] = []
    missing: list[str] = []
    logs_dir = support_dir / "logs"
    summaries_dir = support_dir / "summaries"
    for log in logs:
        src = Path(log).expanduser()
        if not src.exists():
            missing.append(str(src))
            continue
        dst = logs_dir / src.name
        copied_info = copy_log(src, dst, max_bytes=max_log_bytes)
        copied.append(copied_info)
        try:
            summary = summarize_log(src)
        except Exception as exc:
            summary = {"log_path": str(src), "status": "failed", "error": str(exc)}
        summaries.append(summary)
        summary_path = summaries_dir / f"{src.stem}.summary.json"
        summary_path.parent.mkdir(parents=True, exist_ok=True)
        summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True) + "\n")
        status_path = src.parent / "runtime_status.json"
        if status_path.exists() and status_path.is_file():
            status_dst = logs_dir / f"{src.stem}.runtime_status.json"
            status_info = copy_file(status_path, status_dst)
            try:
                status_summary = json.loads(status_path.read_text())
            except Exception as exc:
                status_summary = {"status": "failed", "error": str(exc)}
            runtime_statuses.append(
                {
                    **status_info,
                    "schema_version": status_summary.get("schema_version") if isinstance(status_summary, dict) else None,
                    "active_map": status_summary.get("active_map") if isinstance(status_summary, dict) else None,
                    "output": status_summary.get("output") if isinstance(status_summary, dict) else None,
                    "last_match": status_summary.get("last_match") if isinstance(status_summary, dict) else None,
                    "estimator": status_summary.get("estimator") if isinstance(status_summary, dict) else None,
                    "external_position": status_summary.get("external_position") if isinstance(status_summary, dict) else None,
                    "status_counts": status_summary.get("status_counts") if isinstance(status_summary, dict) else None,
                    "updated_at_utc": status_summary.get("updated_at_utc") if isinstance(status_summary, dict) else None,
                }
            )
    return {"copied": copied, "summaries": summaries, "runtime_statuses": runtime_statuses, "missing": missing}


def copy_extras(extras: list[str], support_dir: Path) -> dict[str, Any]:
    copied: list[dict[str, Any]] = []
    missing: list[str] = []
    for extra in extras:
        src = Path(extra).expanduser()
        if not src.exists():
            missing.append(str(src))
            continue
        dst = support_dir / "extras" / safe_relpath(src)
        copied.append(copy_tree(src, dst) if src.is_dir() else copy_file(src, dst))
    return {"copied": copied, "missing": missing}


def sanitize_filename(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_", "."} else "-" for ch in value)
    return safe.strip("-") or "replay-case"


def parse_inline_replay_case(value: str) -> dict[str, Any]:
    parts = value.split(":", 2)
    if len(parts) != 3:
        raise ValueError("--replay-case must use case_name:expected:log_path")
    case_name, expected, log_path = parts
    return {"case_name": case_name, "expected": expected, "log": log_path, "source": "inline"}


def load_replay_cases(
    *,
    replay_case_manifest: str | None = None,
    inline_replay_cases: list[str] | None = None,
) -> dict[str, Any]:
    inline_replay_cases = inline_replay_cases or []
    cases: list[dict[str, Any]] = []
    sources: list[str] = []
    schema: dict[str, Any] | None = None
    if replay_case_manifest:
        manifest_path = Path(replay_case_manifest).expanduser()
        raw = json.loads(manifest_path.read_text())
        schema = evaluate_replay_case_schema(raw, manifest_path=manifest_path)
        if not isinstance(raw, dict):
            raw = {}
        sources.append(str(manifest_path))
        manifest_cases = raw.get("cases") or []
        for case in manifest_cases:
            if not isinstance(case, dict):
                continue
            normalized = dict(case)
            if normalized.get("log"):
                log_path = Path(str(normalized["log"])).expanduser()
                if not log_path.is_absolute():
                    log_path = manifest_path.parent / log_path
                normalized["log"] = str(log_path)
            normalized["source"] = str(manifest_path)
            cases.append(normalized)
    for inline in inline_replay_cases:
        cases.append(parse_inline_replay_case(inline))
    return {"sources": sources, "cases": cases, "schema": schema}


def evaluate_replay_cases(replay_cases: dict[str, Any], support_dir: Path) -> dict[str, Any]:
    reports: list[dict[str, Any]] = []
    missing: list[str] = []
    report_dir = support_dir / "summaries" / "replay_gates"
    report_dir.mkdir(parents=True, exist_ok=True)
    for index, case in enumerate(replay_cases.get("cases") or [], start=1):
        case_name = str(case.get("case_name") or f"replay-case-{index}")
        expected = str(case.get("expected") or "")
        log_path = case.get("log")
        if expected not in {"good_map", "degraded", "wrong_map"}:
            report = {
                "case_name": case_name,
                "expected": expected,
                "status": "failed",
                "issues": [{"severity": "error", "message": f"Unsupported expected behavior: {expected}"}],
            }
        elif not log_path or not Path(str(log_path)).expanduser().exists():
            missing.append(str(log_path or case_name))
            report = {
                "case_name": case_name,
                "expected": expected,
                "status": "failed",
                "log_path": str(log_path) if log_path else None,
                "issues": [{"severity": "error", "message": "Replay case log is missing."}],
            }
        else:
            report = evaluate_replay_log(
                str(log_path),
                case_name=case_name,
                expected=expected,
                config=ReplayGateConfig(),
            )
        report["notes"] = case.get("notes")
        report["source"] = case.get("source")
        report_path = report_dir / f"{sanitize_filename(case_name)}.gate.json"
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        report["report_path"] = str(report_path)
        reports.append(report)
    return {
        "sources": replay_cases.get("sources") or [],
        "schema": replay_cases.get("schema"),
        "case_count": len(replay_cases.get("cases") or []),
        "reports": reports,
        "missing_logs": missing,
        "status": "failed"
        if any(report.get("status") == "failed" for report in reports)
        or ((replay_cases.get("schema") or {}).get("status") == "failed")
        else "passed",
    }


def evaluate_px4_receiver_evidence(
    *,
    session_path: str | None = None,
    listener_path: str | None = None,
    mavlink_status_path: str | None = None,
    expected_message: str = "odometry",
    support_dir: Path,
) -> dict[str, Any]:
    if session_path:
        session = Path(session_path).expanduser()
        evidence_dir = support_dir / "summaries" / "px4_sitl_evidence"
        raw_dir = support_dir / "extras" / "px4_sitl_session"
        evidence_dir.mkdir(parents=True, exist_ok=True)
        raw_dir.parent.mkdir(parents=True, exist_ok=True)
        if not session.exists():
            report = {
                "status": "failed",
                "expected_message": expected_message,
                "session_path": str(session),
                "issues": [{"severity": "error", "message": "PX4 SITL evidence session is missing."}],
            }
        else:
            copied = copy_tree(session, raw_dir) if session.is_dir() else copy_file(session, raw_dir / session.name)
            report = evaluate_px4_sitl_session(session, output_path=evidence_dir / "receiver_evidence.json")
            report["session_path"] = str(session)
            report["session_copy"] = copied
            report["source"] = "px4_sitl_session"
        report_path = evidence_dir / "receiver_evidence.json"
        report["report_path"] = str(report_path)
        report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
        return report

    if not listener_path:
        return {"status": "not_provided", "expected_message": expected_message}

    listener = Path(listener_path).expanduser()
    mavlink_status = Path(mavlink_status_path).expanduser() if mavlink_status_path else None
    evidence_dir = support_dir / "summaries" / "px4_sitl_evidence"
    raw_dir = support_dir / "extras" / "px4_sitl_evidence"
    evidence_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    if not listener.exists():
        report = {
            "status": "failed",
            "expected_message": expected_message,
            "listener_path": str(listener),
            "issues": [{"severity": "error", "message": "PX4 listener capture is missing."}],
        }
    else:
        listener_copy = copy_file(listener, raw_dir / "vehicle_visual_odometry.txt")
        mavlink_copy = None
        mavlink_text = None
        if mavlink_status is not None:
            if mavlink_status.exists():
                mavlink_copy = copy_file(mavlink_status, raw_dir / "mavlink_status.txt")
                mavlink_text = mavlink_status.read_text(errors="replace")
            else:
                mavlink_text = ""
        report = evaluate_px4_sitl_evidence(
            listener_text=listener.read_text(errors="replace"),
            mavlink_status_text=mavlink_text,
            expected_message=expected_message,
            config=Px4SitlEvidenceConfig(),
        )
        report["listener_path"] = str(listener)
        report["listener_copy"] = listener_copy
        report["mavlink_status_path"] = str(mavlink_status) if mavlink_status else None
        report["mavlink_status_copy"] = mavlink_copy
        if mavlink_status is not None and not mavlink_status.exists():
            report.setdefault("issues", []).append(
                {"severity": "warning", "message": "PX4 mavlink status capture is missing."}
            )
            if report.get("status") == "passed":
                report["status"] = "degraded"

    report_path = evidence_dir / "receiver_evidence.json"
    report["report_path"] = str(report_path)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def evaluate_px4_param_export(
    *,
    params_path: str | None = None,
    support_dir: Path,
) -> dict[str, Any]:
    if not params_path:
        return {"status": "not_provided"}

    src = Path(params_path).expanduser()
    report_dir = support_dir / "summaries" / "px4_params"
    raw_dir = support_dir / "extras" / "px4_params"
    report_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    if not src.exists():
        report = {
            "status": "failed",
            "param_file": str(src),
            "issues": [{"severity": "error", "message": "PX4 parameter export is missing."}],
        }
    else:
        copied = copy_file(src, raw_dir / src.name)
        report = evaluate_px4_param_file(src)
        report["param_copy"] = copied

    report_path = report_dir / "param_check.json"
    report["report_path"] = str(report_path)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def evaluate_ardupilot_param_export(
    *,
    params_path: str | None = None,
    support_dir: Path,
) -> dict[str, Any]:
    if not params_path:
        return {"status": "not_provided"}

    src = Path(params_path).expanduser()
    report_dir = support_dir / "summaries" / "ardupilot_params"
    raw_dir = support_dir / "extras" / "ardupilot_params"
    report_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    if not src.exists():
        report = {
            "status": "failed",
            "param_file": str(src),
            "issues": [{"severity": "error", "message": "ArduPilot parameter export is missing."}],
        }
    else:
        copied = copy_file(src, raw_dir / src.name)
        report = evaluate_ardupilot_param_file(src)
        report["param_copy"] = copied

    report_path = report_dir / "param_check.json"
    report["report_path"] = str(report_path)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def summarize_feature_method_report(report: dict[str, Any], *, report_path: Path) -> dict[str, Any]:
    methods = []
    for method_report in report.get("methods") or []:
        if not isinstance(method_report, dict):
            continue
        gate = method_report.get("gate") if isinstance(method_report.get("gate"), dict) else {}
        metrics = gate.get("metrics") if isinstance(gate.get("metrics"), dict) else {}
        methods.append(
            {
                "method": method_report.get("method"),
                "status": method_report.get("status"),
                "accepted_rate": metrics.get("accepted_rate"),
                "total_records": metrics.get("total_records"),
            }
        )
    return {
        "path": str(report_path),
        "status": report.get("status"),
        "case_name": report.get("case_name"),
        "expected": report.get("expected"),
        "recommended_method": report.get("recommended_method"),
        "methods": methods,
    }


def copy_feature_method_benchmarks(paths: list[str], support_dir: Path) -> dict[str, Any]:
    if not paths:
        return {"status": "not_provided", "report_count": 0}

    summary_dir = support_dir / "summaries" / "feature_method_benchmarks"
    raw_dir = support_dir / "extras" / "feature_method_benchmarks"
    summary_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    copied: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []
    missing: list[str] = []
    issues: list[dict[str, str]] = []
    for raw_path in paths:
        source = Path(raw_path).expanduser()
        if not source.exists():
            missing.append(str(source))
            issues.append({"severity": "error", "message": f"Feature-method benchmark path is missing: {source}"})
            continue
        copied.append(copy_tree(source, raw_dir / safe_relpath(source)) if source.is_dir() else copy_file(source, raw_dir / source.name))
        json_files = sorted(source.rglob("*.json")) if source.is_dir() else [source]
        for report_file in json_files:
            try:
                report = json.loads(report_file.read_text(errors="replace"))
            except Exception as exc:
                issues.append({"severity": "warning", "message": f"Could not parse feature-method benchmark report {report_file}: {exc}"})
                continue
            if not isinstance(report, dict) or "methods" not in report:
                continue
            report_summary = summarize_feature_method_report(report, report_path=report_file)
            reports.append(report_summary)
            output_name = f"{sanitize_filename(report_summary.get('case_name') or report_file.stem)}-{len(reports):02d}.json"
            (summary_dir / output_name).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    statuses = {str(report.get("status") or "").lower() for report in reports}
    if missing or "failed" in statuses:
        status = "failed"
    elif not reports:
        status = "degraded" if issues else "not_provided"
    elif statuses.intersection({"degraded", "not_available"}):
        status = "degraded"
    else:
        status = "passed"
    return {
        "status": status,
        "report_count": len(reports),
        "reports": reports,
        "copied": copied,
        "missing": missing,
        "issues": issues,
    }


def summarize_field_evidence_report(report: dict[str, Any], *, report_path: Path) -> dict[str, Any]:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    return {
        "path": str(report_path),
        "status": report.get("status"),
        "manifest_path": report.get("manifest_path"),
        "coverage_status": summary.get("coverage_status"),
        "replay_status": summary.get("replay_status"),
        "case_count": summary.get("case_count"),
        "field_case_count": summary.get("field_case_count"),
        "covered_conditions": summary.get("covered_conditions") or [],
        "required_conditions": summary.get("required_conditions") or [],
    }


def copy_field_evidence_reports(paths: list[str], support_dir: Path) -> dict[str, Any]:
    if not paths:
        return {"status": "not_provided", "report_count": 0}

    summary_dir = support_dir / "summaries" / "field_evidence"
    raw_dir = support_dir / "extras" / "field_evidence"
    summary_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    copied: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []
    missing: list[str] = []
    issues: list[dict[str, str]] = []
    for raw_path in paths:
        source = Path(raw_path).expanduser()
        if not source.exists():
            missing.append(str(source))
            issues.append({"severity": "error", "message": f"Field evidence report is missing: {source}"})
            continue
        if source.is_dir():
            copied.append(copy_tree(source, raw_dir / safe_relpath(source)))
            json_files = sorted(source.rglob("*.json"))
        else:
            copied.append(copy_file(source, raw_dir / source.name))
            json_files = [source]
        for report_file in json_files:
            try:
                report = json.loads(report_file.read_text(errors="replace"))
            except Exception as exc:
                issues.append({"severity": "warning", "message": f"Could not parse field evidence report {report_file}: {exc}"})
                continue
            if not isinstance(report, dict) or "coverage" not in report or "replay_gates" not in report:
                continue
            report_summary = summarize_field_evidence_report(report, report_path=report_file)
            reports.append(report_summary)
            output_name = f"{sanitize_filename(Path(str(report_summary.get('manifest_path') or report_file.stem)).stem)}-{len(reports):02d}.json"
            (summary_dir / output_name).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    statuses = {str(report.get("status") or "").lower() for report in reports}
    if missing or "failed" in statuses:
        status = "failed"
    elif not reports:
        status = "degraded" if issues else "not_provided"
    elif statuses.intersection({"degraded", "warming_up"}):
        status = "degraded"
    else:
        status = "passed"
    return {
        "status": status,
        "report_count": len(reports),
        "reports": reports,
        "field_case_count": max((int(report.get("field_case_count") or 0) for report in reports), default=0),
        "covered_conditions": sorted(
            {
                str(condition)
                for report in reports
                for condition in report.get("covered_conditions", [])
            }
        ),
        "copied": copied,
        "missing": missing,
        "issues": issues,
    }


def summarize_field_collection_plan(report: dict[str, Any], *, report_path: Path) -> dict[str, Any]:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    conditions = []
    for item in report.get("conditions") or []:
        if not isinstance(item, dict):
            continue
        conditions.append(
            {
                "condition": item.get("condition"),
                "label": item.get("label"),
                "status": item.get("status"),
                "expected": item.get("expected"),
                "case_name": item.get("case_name"),
                "manifest_log_exists": item.get("manifest_log_exists"),
            }
        )
    return {
        "path": str(report_path),
        "status": report.get("status"),
        "site_name": report.get("site_name"),
        "manifest_path": report.get("manifest_path"),
        "bundle": report.get("bundle"),
        "source_log": report.get("source_log"),
        "required_count": summary.get("required_count"),
        "registered_count": summary.get("registered_count"),
        "registered_missing_log_count": summary.get("registered_missing_log_count"),
        "placeholder_count": summary.get("placeholder_count"),
        "missing_count": summary.get("missing_count"),
        "conditions": conditions,
    }


def copy_field_collection_plans(paths: list[str], support_dir: Path) -> dict[str, Any]:
    if not paths:
        return {"status": "not_provided", "report_count": 0}

    summary_dir = support_dir / "summaries" / "field_collection_plans"
    raw_dir = support_dir / "extras" / "field_collection_plans"
    summary_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    copied: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []
    missing: list[str] = []
    issues: list[dict[str, str]] = []
    for raw_path in paths:
        source = Path(raw_path).expanduser()
        if not source.exists():
            missing.append(str(source))
            issues.append({"severity": "error", "message": f"Field collection plan is missing: {source}"})
            continue
        if source.is_dir():
            copied.append(copy_tree(source, raw_dir / safe_relpath(source)))
            json_files = sorted(source.rglob("*.json"))
        else:
            copied.append(copy_file(source, raw_dir / source.name))
            markdown_sibling = source.with_suffix(".md")
            if markdown_sibling.exists() and markdown_sibling.is_file():
                copied.append(copy_file(markdown_sibling, raw_dir / markdown_sibling.name))
            json_files = [source]
        for report_file in json_files:
            try:
                report = json.loads(report_file.read_text(errors="replace"))
            except Exception as exc:
                issues.append({"severity": "warning", "message": f"Could not parse field collection plan {report_file}: {exc}"})
                continue
            if not isinstance(report, dict) or report.get("schema_version") != "vision_nav_field_collection_plan_v1":
                continue
            report_summary = summarize_field_collection_plan(report, report_path=report_file)
            reports.append(report_summary)
            output_name = f"{sanitize_filename(Path(str(report_summary.get('manifest_path') or report_file.stem)).stem)}-{len(reports):02d}.json"
            (summary_dir / output_name).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    statuses = {str(report.get("status") or "").lower() for report in reports}
    if missing or "failed" in statuses:
        status = "failed"
    elif not reports:
        status = "degraded" if issues else "not_provided"
    elif statuses.intersection({"degraded", "warming_up"}):
        status = "degraded"
    else:
        status = "passed"
    return {
        "status": status,
        "report_count": len(reports),
        "reports": reports,
        "registered_count": max((int(report.get("registered_count") or 0) for report in reports), default=0),
        "required_count": max((int(report.get("required_count") or 0) for report in reports), default=0),
        "copied": copied,
        "missing": missing,
        "issues": issues,
    }


def summarize_threshold_tuning_report(report: dict[str, Any], *, report_path: Path) -> dict[str, Any]:
    summary = report.get("summary") if isinstance(report.get("summary"), dict) else {}
    margins = {}
    metrics = report.get("metrics") if isinstance(report.get("metrics"), dict) else {}
    if isinstance(metrics.get("margins"), dict):
        margins = metrics["margins"]
    return {
        "path": str(report_path),
        "status": report.get("status"),
        "method": report.get("method"),
        "manifest_path": report.get("manifest_path"),
        "coverage_status": summary.get("coverage_status"),
        "replay_status": summary.get("replay_status"),
        "case_count": summary.get("case_count"),
        "field_case_count": summary.get("field_case_count"),
        "covered_conditions": summary.get("covered_conditions") or report.get("conditions") or [],
        "margins": margins,
    }


def copy_threshold_tuning_reports(paths: list[str], support_dir: Path) -> dict[str, Any]:
    if not paths:
        return {"status": "not_provided", "report_count": 0}

    summary_dir = support_dir / "summaries" / "threshold_tuning"
    raw_dir = support_dir / "extras" / "threshold_tuning"
    summary_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    copied: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []
    missing: list[str] = []
    issues: list[dict[str, str]] = []
    for raw_path in paths:
        source = Path(raw_path).expanduser()
        if not source.exists():
            missing.append(str(source))
            issues.append({"severity": "error", "message": f"Threshold tuning report is missing: {source}"})
            continue
        if source.is_dir():
            copied.append(copy_tree(source, raw_dir / safe_relpath(source)))
            json_files = sorted(source.rglob("*.json"))
        else:
            copied.append(copy_file(source, raw_dir / source.name))
            json_files = [source]
        for report_file in json_files:
            try:
                report = json.loads(report_file.read_text(errors="replace"))
            except Exception as exc:
                issues.append({"severity": "warning", "message": f"Could not parse threshold tuning report {report_file}: {exc}"})
                continue
            if not isinstance(report, dict) or report.get("method") != "field-replay-gate-threshold-audit":
                continue
            report_summary = summarize_threshold_tuning_report(report, report_path=report_file)
            reports.append(report_summary)
            output_name = f"{sanitize_filename(Path(str(report_summary.get('manifest_path') or report_file.stem)).stem)}-{len(reports):02d}.json"
            (summary_dir / output_name).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    statuses = {str(report.get("status") or "").lower() for report in reports}
    if missing or "failed" in statuses:
        status = "failed"
    elif not reports:
        status = "degraded" if issues else "not_provided"
    elif statuses.intersection({"degraded", "warming_up"}):
        status = "degraded"
    else:
        status = "passed"
    return {
        "status": status,
        "report_count": len(reports),
        "reports": reports,
        "field_case_count": max((int(report.get("field_case_count") or 0) for report in reports), default=0),
        "covered_conditions": sorted(
            {
                str(condition)
                for report in reports
                for condition in report.get("covered_conditions", [])
            }
        ),
        "copied": copied,
        "missing": missing,
        "issues": issues,
    }


def summarize_rosbag_export_validation(report: dict[str, Any], *, report_path: Path) -> dict[str, Any]:
    topics = []
    for topic in report.get("topics") or []:
        if not isinstance(topic, dict):
            continue
        topics.append(
            {
                "name": topic.get("name"),
                "type": topic.get("type"),
                "message_count": topic.get("message_count"),
            }
        )
    issues = []
    for issue in report.get("issues") or []:
        if isinstance(issue, dict):
            issues.append({"severity": issue.get("severity"), "message": issue.get("message")})
    return {
        "path": str(report_path),
        "status": report.get("status"),
        "format": report.get("format"),
        "artifact_path": report.get("artifact_path"),
        "metadata_path": report.get("metadata_path"),
        "message_count": report.get("message_count"),
        "topic_count": report.get("topic_count"),
        "topics": topics,
        "issues": issues,
    }


def copy_rosbag_export_validations(paths: list[str], support_dir: Path) -> dict[str, Any]:
    if not paths:
        return {"status": "not_provided", "report_count": 0}

    summary_dir = support_dir / "summaries" / "rosbag_export_validations"
    raw_dir = support_dir / "extras" / "rosbag_export_validations"
    summary_dir.mkdir(parents=True, exist_ok=True)
    raw_dir.mkdir(parents=True, exist_ok=True)

    copied: list[dict[str, Any]] = []
    reports: list[dict[str, Any]] = []
    missing: list[str] = []
    issues: list[dict[str, str]] = []
    for raw_path in paths:
        source = Path(raw_path).expanduser()
        if not source.exists():
            missing.append(str(source))
            issues.append({"severity": "error", "message": f"ROS bag export validation path is missing: {source}"})
            continue
        if source.is_dir():
            copied.append(copy_tree(source, raw_dir / safe_relpath(source)))
            json_files = sorted(source.rglob("*.json"))
        else:
            copied.append(copy_file(source, raw_dir / source.name))
            json_files = [source]
        for report_file in json_files:
            try:
                report = json.loads(report_file.read_text(errors="replace"))
            except Exception as exc:
                issues.append({"severity": "warning", "message": f"Could not parse ROS bag export validation report {report_file}: {exc}"})
                continue
            if not isinstance(report, dict) or report.get("schema_version") != "vision_nav_rosbag_export_validation_v1":
                continue
            report_summary = summarize_rosbag_export_validation(report, report_path=report_file)
            reports.append(report_summary)
            output_name = f"{sanitize_filename(str(report.get('format') or report_file.stem))}-{len(reports):02d}.json"
            (summary_dir / output_name).write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")

    statuses = {str(report.get("status") or "").lower() for report in reports}
    if missing or "failed" in statuses:
        status = "failed"
    elif not reports:
        status = "degraded" if issues else "not_provided"
    elif statuses.intersection({"degraded", "warning", "warnings"}):
        status = "degraded"
    else:
        status = "passed"
    return {
        "status": status,
        "report_count": len(reports),
        "reports": reports,
        "formats": sorted({str(report.get("format")) for report in reports if report.get("format")}),
        "message_count": sum(int(report.get("message_count") or 0) for report in reports),
        "topic_count": sum(int(report.get("topic_count") or 0) for report in reports),
        "copied": copied,
        "missing": missing,
        "issues": issues,
    }


def zip_directory(source_dir: Path, zip_path: Path) -> None:
    if zip_path.exists():
        zip_path.unlink()
    with zipfile.ZipFile(zip_path, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for path in sorted(source_dir.rglob("*")):
            if path.is_file():
                archive.write(path, path.relative_to(source_dir))


def create_support_bundle(
    *,
    bundle: str | None = None,
    logs: list[str] | None = None,
    extras: list[str] | None = None,
    repo: str | Path = ".",
    output_dir: str | Path = "support-bundles",
    name: str | None = None,
    autopilot_metadata_path: str | None = None,
    mavlink_endpoint: str | None = None,
    px4_listener_path: str | None = None,
    px4_mavlink_status_path: str | None = None,
    px4_sitl_session_path: str | None = None,
    px4_params_path: str | None = None,
    ardupilot_params_path: str | None = None,
    px4_expected_message: str = "odometry",
    replay_case_manifest_path: str | None = None,
    inline_replay_cases: list[str] | None = None,
    feature_method_benchmark_paths: list[str] | None = None,
    field_evidence_report_paths: list[str] | None = None,
    field_collection_plan_paths: list[str] | None = None,
    threshold_tuning_report_paths: list[str] | None = None,
    rosbag_export_validation_paths: list[str] | None = None,
    include_map_assets: bool = False,
    max_log_bytes: int = DEFAULT_MAX_LOG_BYTES,
) -> dict[str, Any]:
    logs = logs or []
    extras = extras or []
    repo_path = Path(repo).expanduser().resolve()
    autopilot_metadata = None
    if autopilot_metadata_path:
        autopilot_metadata = json.loads(Path(autopilot_metadata_path).expanduser().read_text())

    bundle_id: str | None = None
    bundle_summary: dict[str, Any] | None = None
    if bundle:
        _, manifest = load_manifest(bundle)
        bundle_id = manifest.get("bundle_id")

    output_root = Path(output_dir).expanduser().resolve()
    output_root.mkdir(parents=True, exist_ok=True)
    support_name = name or default_name(bundle_id)
    support_dir = output_root / support_name
    if support_dir.exists():
        shutil.rmtree(support_dir)
    support_dir.mkdir(parents=True)

    if bundle:
        bundle_summary = copy_bundle_metadata(bundle, support_dir, include_map_assets=include_map_assets)

    log_summary = copy_logs(logs, support_dir, max_log_bytes=max_log_bytes)
    extra_summary = copy_extras(extras, support_dir)
    replay_cases = load_replay_cases(
        replay_case_manifest=replay_case_manifest_path,
        inline_replay_cases=inline_replay_cases,
    )
    replay_gate_summary = evaluate_replay_cases(replay_cases, support_dir)
    px4_evidence_summary = evaluate_px4_receiver_evidence(
        session_path=px4_sitl_session_path,
        listener_path=px4_listener_path,
        mavlink_status_path=px4_mavlink_status_path,
        expected_message=px4_expected_message,
        support_dir=support_dir,
    )
    px4_params_summary = evaluate_px4_param_export(
        params_path=px4_params_path,
        support_dir=support_dir,
    )
    ardupilot_params_summary = evaluate_ardupilot_param_export(
        params_path=ardupilot_params_path,
        support_dir=support_dir,
    )
    feature_method_benchmarks = copy_feature_method_benchmarks(
        feature_method_benchmark_paths or [],
        support_dir,
    )
    field_evidence = copy_field_evidence_reports(
        field_evidence_report_paths or [],
        support_dir,
    )
    field_collection_plans = copy_field_collection_plans(
        field_collection_plan_paths or [],
        support_dir,
    )
    threshold_tuning = copy_threshold_tuning_reports(
        threshold_tuning_report_paths or [],
        support_dir,
    )
    rosbag_export_validations = copy_rosbag_export_validations(
        rosbag_export_validation_paths or [],
        support_dir,
    )
    manifest = {
        "support_bundle_version": "0.1.0",
        "name": support_name,
        "metadata": metadata_snapshot(repo_path, mavlink_endpoint=mavlink_endpoint, autopilot_metadata=autopilot_metadata),
        "bundle": bundle_summary,
        "logs": log_summary,
        "replay_gates": replay_gate_summary,
        "px4_sitl_evidence": px4_evidence_summary,
        "px4_params": px4_params_summary,
        "ardupilot_params": ardupilot_params_summary,
        "feature_method_benchmarks": feature_method_benchmarks,
        "field_evidence": field_evidence,
        "field_collection_plans": field_collection_plans,
        "threshold_tuning": threshold_tuning,
        "rosbag_export_validations": rosbag_export_validations,
        "extras": extra_summary,
    }
    bench_readiness = evaluate_bench_readiness(manifest)
    readiness_path = support_dir / "summaries" / "bench_readiness.json"
    readiness_path.parent.mkdir(parents=True, exist_ok=True)
    bench_readiness["report_path"] = str(readiness_path)
    readiness_path.write_text(json.dumps(bench_readiness, indent=2, sort_keys=True) + "\n")
    manifest["bench_readiness"] = bench_readiness
    manifest_path = support_dir / "support_manifest.json"
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n")

    zip_path = output_root / f"{support_name}.zip"
    zip_directory(support_dir, zip_path)
    result = {
        "status": "passed",
        "support_dir": str(support_dir),
        "zip_path": str(zip_path),
        "zip_bytes": zip_path.stat().st_size,
        "manifest_path": str(manifest_path),
        "manifest": manifest,
    }
    return result


def print_human(result: dict[str, Any]) -> None:
    manifest = result["manifest"]
    print(f"Support bundle: {manifest['name']}")
    print(f"Directory: {result['support_dir']}")
    print(f"Zip: {result['zip_path']} ({result['zip_bytes']} bytes)")
    bundle = manifest.get("bundle") or {}
    if bundle:
        health = bundle.get("health") or {}
        print(f"Bundle: {bundle.get('bundle_id') or '(unnamed)'} health={health.get('status') or 'unknown'}")
    logs = manifest.get("logs") or {}
    print(f"Logs: {len(logs.get('copied') or [])} copied, {len(logs.get('missing') or [])} missing")
    replay_gates = manifest.get("replay_gates") or {}
    if replay_gates.get("case_count"):
        print(f"Replay gates: {replay_gates.get('status')} ({replay_gates.get('case_count')} case(s))")
    px4_evidence = manifest.get("px4_sitl_evidence") or {}
    if px4_evidence.get("status") not in {None, "not_provided"}:
        print(f"PX4 SITL evidence: {px4_evidence.get('status')}")
    px4_params = manifest.get("px4_params") or {}
    if px4_params.get("status") not in {None, "not_provided"}:
        print(f"PX4 params: {px4_params.get('status')}")
    ardupilot_params = manifest.get("ardupilot_params") or {}
    if ardupilot_params.get("status") not in {None, "not_provided"}:
        print(f"ArduPilot params: {ardupilot_params.get('status')}")
    feature_benchmarks = manifest.get("feature_method_benchmarks") or {}
    if feature_benchmarks.get("status") not in {None, "not_provided"}:
        print(f"Feature method benchmarks: {feature_benchmarks.get('status')} ({feature_benchmarks.get('report_count')} report(s))")
    field_evidence = manifest.get("field_evidence") or {}
    if field_evidence.get("status") not in {None, "not_provided"}:
        print(f"Field evidence: {field_evidence.get('status')} ({field_evidence.get('report_count')} report(s))")
    field_collection_plans = manifest.get("field_collection_plans") or {}
    if field_collection_plans.get("status") not in {None, "not_provided"}:
        print(
            "Field collection plans: "
            f"{field_collection_plans.get('status')} ({field_collection_plans.get('report_count')} plan(s))"
        )
    threshold_tuning = manifest.get("threshold_tuning") or {}
    if threshold_tuning.get("status") not in {None, "not_provided"}:
        print(f"Threshold tuning: {threshold_tuning.get('status')} ({threshold_tuning.get('report_count')} report(s))")
    rosbag_validations = manifest.get("rosbag_export_validations") or {}
    if rosbag_validations.get("status") not in {None, "not_provided"}:
        print(f"ROS bag exports: {rosbag_validations.get('status')} ({rosbag_validations.get('report_count')} validation(s))")
    readiness = manifest.get("bench_readiness") or {}
    print(f"Bench readiness: {readiness.get('status') or 'unknown'}")
    print(f"__VISION_NAV_SUPPORT_ZIP__={result['zip_path']}")


def main() -> None:
    args = parse_args()
    result = create_support_bundle(
        bundle=args.bundle,
        logs=args.log,
        extras=args.extra,
        repo=args.repo,
        output_dir=args.output_dir,
        name=args.name,
        autopilot_metadata_path=args.autopilot_metadata,
        mavlink_endpoint=args.mavlink_endpoint,
        px4_listener_path=args.px4_listener,
        px4_mavlink_status_path=args.px4_mavlink_status,
        px4_sitl_session_path=args.px4_sitl_session,
        px4_params_path=args.px4_params,
        ardupilot_params_path=args.ardupilot_params,
        px4_expected_message=args.px4_expected_message,
        replay_case_manifest_path=args.replay_case_manifest,
        inline_replay_cases=args.replay_case,
        feature_method_benchmark_paths=args.feature_method_benchmark,
        field_evidence_report_paths=args.field_evidence_report,
        field_collection_plan_paths=args.field_collection_plan,
        threshold_tuning_report_paths=args.threshold_tuning_report,
        rosbag_export_validation_paths=args.rosbag_export_validation,
        include_map_assets=args.include_map_assets,
        max_log_bytes=args.max_log_bytes,
    )
    if args.json:
        print(json.dumps(result, indent=2, sort_keys=True))
    else:
        print_human(result)


if __name__ == "__main__":
    main()
