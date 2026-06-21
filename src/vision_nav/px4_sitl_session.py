from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from vision_nav.px4_sitl_evidence import Px4SitlEvidenceConfig, evaluate_px4_sitl_evidence


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Evaluate a PX4 SITL external-vision evidence session folder.")
    parser.add_argument("--session", required=True, help="Session directory or px4_sitl_evidence_session.json.")
    parser.add_argument("--output", help="Optional report path. Defaults to receiver_report from the session manifest.")
    parser.add_argument("--min-samples", type=int, default=Px4SitlEvidenceConfig.min_samples)
    parser.add_argument("--max-sample-age-s", type=float, default=Px4SitlEvidenceConfig.max_sample_age_s)
    parser.add_argument("--json", action="store_true", help="Emit JSON only.")
    parser.add_argument("--allow-degraded", action="store_true", help="Exit zero for warning-only degraded evidence.")
    return parser.parse_args()


def load_session_manifest(path: str | Path) -> tuple[Path, dict[str, Any]]:
    source = Path(path).expanduser()
    manifest_path = source / "px4_sitl_evidence_session.json" if source.is_dir() else source
    raw = json.loads(manifest_path.read_text())
    if not isinstance(raw, dict):
        raise ValueError("PX4 SITL evidence session manifest must be a JSON object.")
    return manifest_path, raw


def evaluate_px4_sitl_session(
    session_path: str | Path,
    *,
    output_path: str | Path | None = None,
    config: Px4SitlEvidenceConfig | None = None,
) -> dict[str, Any]:
    manifest_path, session = load_session_manifest(session_path)
    root = manifest_path.parent
    expected_message = str(session.get("message_type") or "odometry")
    captures = session.get("expected_captures") or {}
    listener_path = resolve_session_path(root, captures.get("vehicle_visual_odometry"))
    mavlink_status_path = resolve_session_path(root, captures.get("mavlink_status"))
    report_path = Path(output_path).expanduser() if output_path else resolve_session_path(root, session.get("receiver_report"))
    if report_path is None:
        report_path = root / "receiver_evidence.json"

    issues: list[dict[str, str]] = []
    listener_text = ""
    mavlink_status_text = None

    if listener_path is None or not listener_path.exists():
        issues.append({"severity": "error", "message": "Missing vehicle_visual_odometry listener capture."})
    else:
        listener_text = listener_path.read_text(errors="replace")

    if mavlink_status_path is not None and mavlink_status_path.exists():
        mavlink_status_text = mavlink_status_path.read_text(errors="replace")
    elif mavlink_status_path is not None:
        issues.append({"severity": "warning", "message": "Missing mavlink status capture."})

    if listener_text:
        report = evaluate_px4_sitl_evidence(
            listener_text=listener_text,
            mavlink_status_text=mavlink_status_text,
            expected_message=expected_message,
            config=config or Px4SitlEvidenceConfig(),
        )
        report.setdefault("issues", []).extend(issues)
        if any(issue["severity"] == "error" for issue in report["issues"]):
            report["status"] = "failed"
        elif report.get("status") == "passed" and any(issue["severity"] == "warning" for issue in report["issues"]):
            report["status"] = "degraded"
    else:
        report = {
            "status": "failed",
            "expected_message": expected_message,
            "config": config_dict(config or Px4SitlEvidenceConfig()),
            "listener": None,
            "mavlink_status": None,
            "issues": issues,
        }

    report["session_manifest"] = str(manifest_path)
    report["session_dir"] = str(root)
    report["listener_path"] = str(listener_path) if listener_path is not None else None
    report["mavlink_status_path"] = str(mavlink_status_path) if mavlink_status_path is not None else None
    report["report_path"] = str(report_path)
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    return report


def resolve_session_path(root: Path, value: Any) -> Path | None:
    if value is None or value == "":
        return None
    path = Path(str(value)).expanduser()
    return path if path.is_absolute() else root / path


def config_dict(config: Px4SitlEvidenceConfig) -> dict[str, Any]:
    return {
        "min_samples": config.min_samples,
        "max_sample_age_s": config.max_sample_age_s,
        "require_position": config.require_position,
        "require_covariance": config.require_covariance,
    }


def print_human(report: dict[str, Any]) -> None:
    print(f"PX4 SITL evidence session: {report.get('session_dir')}")
    print(f"Status: {report['status']}")
    print(f"Expected sender message: {report.get('expected_message')}")
    print(f"Report: {report.get('report_path')}")
    listener = report.get("listener") or {}
    print(f"Listener samples: {listener.get('sample_count')}")
    print(f"Last position: {listener.get('last_position')}")
    for issue in report.get("issues") or []:
        print(f"[{str(issue.get('severity') or 'info').upper()}] {issue.get('message')}")


def main() -> None:
    args = parse_args()
    report = evaluate_px4_sitl_session(
        args.session,
        output_path=args.output,
        config=Px4SitlEvidenceConfig(
            min_samples=args.min_samples,
            max_sample_age_s=args.max_sample_age_s,
        ),
    )
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human(report)
    if report["status"] == "failed" or (report["status"] == "degraded" and not args.allow_degraded):
        raise SystemExit(1)


if __name__ == "__main__":
    main()
