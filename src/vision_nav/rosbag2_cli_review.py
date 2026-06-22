from __future__ import annotations

import argparse
import json
from pathlib import Path
import shutil
import subprocess
from typing import Any

from vision_nav.rosbag_export_check import validate_rosbag_export


SCHEMA_VERSION = "vision_nav_rosbag2_cli_review_v1"
NATIVE_ROSBAG2_FORMAT = "vision_nav_rosbag2_v1"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Create a review artifact for a native rosbag2 export using validation plus ros2 bag info."
    )
    parser.add_argument("--artifact", required=True, help="Native rosbag2 export directory or metadata JSON.")
    parser.add_argument("--output", help="Optional JSON review report path.")
    parser.add_argument("--ros2-command", default="ros2", help="ROS 2 CLI command. Default: ros2")
    parser.add_argument("--timeout-s", type=float, default=30.0, help="Timeout for ros2 bag info. Default: 30")
    parser.add_argument(
        "--require-ros2",
        action="store_true",
        help="Fail instead of degrade when the ROS 2 CLI is unavailable.",
    )
    parser.add_argument(
        "--skip-ros2",
        action="store_true",
        help="Only run artifact validation and mark the missing CLI review as degraded.",
    )
    parser.add_argument("--json", action="store_true", help="Print the full JSON report.")
    return parser.parse_args()


def review_rosbag2_cli(
    artifact: str | Path,
    *,
    output_path: str | Path | None = None,
    ros2_command: str = "ros2",
    timeout_s: float = 30.0,
    require_ros2: bool = False,
    skip_ros2: bool = False,
) -> dict[str, Any]:
    artifact_path = Path(artifact).expanduser()
    validation = validate_rosbag_export(artifact_path)
    issues: list[dict[str, str]] = []
    if validation.get("status") == "failed":
        add_issue(issues, "error", "ROS replay export validation failed.")
    elif validation.get("status") == "degraded":
        add_issue(issues, "warning", "ROS replay export validation is degraded.")

    if validation.get("format") != NATIVE_ROSBAG2_FORMAT:
        add_issue(
            issues,
            "error",
            f"Native rosbag2 review requires format {NATIVE_ROSBAG2_FORMAT}; got {validation.get('format') or 'missing'}.",
        )

    bag_dir = resolve_bag_dir(artifact_path, validation)
    cli_review = run_ros2_bag_info(
        bag_dir,
        ros2_command=ros2_command,
        timeout_s=timeout_s,
        require_ros2=require_ros2,
        skip_ros2=skip_ros2,
        issues=issues,
    )
    status = status_from_issues(issues)
    report = {
        "schema_version": SCHEMA_VERSION,
        "status": status,
        "artifact_path": str(artifact_path),
        "bag_dir": str(bag_dir) if bag_dir is not None else None,
        "validation_status": validation.get("status"),
        "validation_format": validation.get("format"),
        "validation_report": validation,
        "ros2_cli": cli_review,
        "issues": issues,
    }
    if output_path is not None:
        destination = Path(output_path).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        report["report_path"] = str(destination)
    return report


def resolve_bag_dir(artifact_path: Path, validation: dict[str, Any]) -> Path | None:
    output_dir = (validation.get("details") or {}).get("output_dir")
    if isinstance(output_dir, str) and output_dir:
        return Path(output_dir).expanduser()
    if artifact_path.is_dir():
        return artifact_path
    if artifact_path.is_file():
        return artifact_path.parent
    return None


def run_ros2_bag_info(
    bag_dir: Path | None,
    *,
    ros2_command: str,
    timeout_s: float,
    require_ros2: bool,
    skip_ros2: bool,
    issues: list[dict[str, str]],
) -> dict[str, Any]:
    command = [ros2_command, "bag", "info", str(bag_dir)] if bag_dir is not None else []
    if bag_dir is None or not bag_dir.exists():
        add_issue(issues, "error", "Native rosbag2 output directory is missing.")
        return {
            "status": "failed",
            "command": command,
            "stdout": "",
            "stderr": "Native rosbag2 output directory is missing.",
            "exit_code": None,
        }
    if skip_ros2:
        add_issue(issues, "warning", "ROS 2 CLI review was skipped.")
        return {
            "status": "skipped",
            "command": command,
            "stdout": "",
            "stderr": "",
            "exit_code": None,
        }
    if shutil.which(ros2_command) is None and Path(ros2_command).expanduser().name == ros2_command:
        severity = "error" if require_ros2 else "warning"
        add_issue(issues, severity, f"ROS 2 CLI command not found: {ros2_command}.")
        return {
            "status": "failed" if require_ros2 else "degraded",
            "command": command,
            "stdout": "",
            "stderr": f"ROS 2 CLI command not found: {ros2_command}.",
            "exit_code": None,
        }
    try:
        completed = subprocess.run(
            command,
            check=False,
            capture_output=True,
            text=True,
            timeout=timeout_s,
        )
    except subprocess.TimeoutExpired as exc:
        add_issue(issues, "error", f"ros2 bag info timed out after {timeout_s:g}s.")
        return {
            "status": "failed",
            "command": command,
            "stdout": exc.stdout or "",
            "stderr": exc.stderr or "",
            "exit_code": None,
        }
    except Exception as exc:
        add_issue(issues, "error", f"Could not run ros2 bag info: {exc}.")
        return {
            "status": "failed",
            "command": command,
            "stdout": "",
            "stderr": str(exc),
            "exit_code": None,
        }
    if completed.returncode != 0:
        add_issue(issues, "error", f"ros2 bag info exited with {completed.returncode}.")
        status = "failed"
    else:
        status = "passed"
    return {
        "status": status,
        "command": command,
        "stdout": completed.stdout,
        "stderr": completed.stderr,
        "exit_code": completed.returncode,
    }


def add_issue(issues: list[dict[str, str]], severity: str, message: str) -> None:
    issues.append({"severity": severity, "message": message})


def status_from_issues(issues: list[dict[str, str]]) -> str:
    if any(issue.get("severity") == "error" for issue in issues):
        return "failed"
    if issues:
        return "degraded"
    return "passed"


def main() -> None:
    args = parse_args()
    report = review_rosbag2_cli(
        args.artifact,
        output_path=args.output,
        ros2_command=args.ros2_command,
        timeout_s=args.timeout_s,
        require_ros2=args.require_ros2,
        skip_ros2=args.skip_ros2,
    )
    if args.json or args.output is None:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"Native rosbag2 CLI review: {report['status']}")
        print(f"__VISION_NAV_ROSBAG2_CLI_REVIEW__={report['report_path']}")
    if report["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
