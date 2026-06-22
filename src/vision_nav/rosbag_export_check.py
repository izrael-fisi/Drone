from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any


SUPPORTED_FORMATS = {
    "vision_nav_rosbag_jsonl_v1",
    "vision_nav_mcap_json_v1",
    "vision_nav_rosbag2_v1",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Validate vision-nav ROS 2 replay export artifacts for support review."
    )
    parser.add_argument(
        "--artifact",
        required=True,
        help="Export directory, metadata JSON, or MCAP file produced by vision-nav-ros2-replay-log.",
    )
    parser.add_argument("--output", help="Optional JSON validation report path.")
    parser.add_argument("--json", action="store_true", help="Print the full JSON report.")
    return parser.parse_args()


def validate_rosbag_export(artifact: str | Path, *, output_path: str | Path | None = None) -> dict[str, Any]:
    artifact_path = Path(artifact).expanduser()
    issues: list[dict[str, str]] = []
    metadata_path = resolve_metadata_path(artifact_path, issues)
    metadata: dict[str, Any] = {}
    if metadata_path is not None:
        metadata = read_metadata(metadata_path, issues)

    format_name = str(metadata.get("format") or "")
    if metadata and format_name not in SUPPORTED_FORMATS:
        add_issue(issues, "error", f"Unsupported ROS replay export format: {format_name or 'missing'}.")

    topics = metadata.get("topics") if isinstance(metadata.get("topics"), list) else []
    topic_summary = summarize_topics(topics)
    message_count = int(metadata.get("message_count") or 0) if metadata else 0
    if metadata and message_count <= 0:
        add_issue(issues, "error", "Export metadata has no messages.")
    if metadata and not topic_summary:
        add_issue(issues, "error", "Export metadata has no topics.")

    details: dict[str, Any] = {}
    if format_name == "vision_nav_rosbag_jsonl_v1":
        details = validate_rosbag_jsonl(metadata_path, metadata, issues)
    elif format_name == "vision_nav_mcap_json_v1":
        details = validate_mcap_export(artifact_path, metadata_path, metadata, issues)
    elif format_name == "vision_nav_rosbag2_v1":
        details = validate_rosbag2_export(metadata_path, metadata, issues)

    status = status_from_issues(issues)
    report = {
        "schema_version": "vision_nav_rosbag_export_validation_v1",
        "status": status,
        "artifact_path": str(artifact_path),
        "metadata_path": str(metadata_path) if metadata_path is not None else None,
        "format": format_name or None,
        "message_count": message_count,
        "topic_count": len(topic_summary),
        "topics": topic_summary,
        "details": details,
        "issues": issues,
    }
    if output_path is not None:
        destination = Path(output_path).expanduser()
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        report["report_path"] = str(destination)
    return report


def resolve_metadata_path(artifact_path: Path, issues: list[dict[str, str]]) -> Path | None:
    if artifact_path.is_dir():
        candidates = [
            artifact_path / "metadata.json",
            artifact_path / "vision_nav_rosbag2_metadata.json",
        ]
        for candidate in candidates:
            if candidate.is_file():
                return candidate
        add_issue(issues, "error", f"No supported metadata JSON found in {artifact_path}.")
        return None
    if artifact_path.is_file():
        if artifact_path.suffix == ".json":
            return artifact_path
        if artifact_path.suffix == ".mcap":
            candidate = artifact_path.with_suffix(artifact_path.suffix + ".metadata.json")
            if candidate.is_file():
                return candidate
            add_issue(issues, "error", f"Missing MCAP metadata sidecar: {candidate}.")
            return None
    add_issue(issues, "error", f"ROS replay export artifact does not exist or is unsupported: {artifact_path}.")
    return None


def read_metadata(path: Path, issues: list[dict[str, str]]) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:
        add_issue(issues, "error", f"Could not read export metadata: {exc}.")
        return {}
    if not isinstance(value, dict):
        add_issue(issues, "error", "Export metadata is not a JSON object.")
        return {}
    return value


def validate_rosbag_jsonl(
    metadata_path: Path | None,
    metadata: dict[str, Any],
    issues: list[dict[str, str]],
) -> dict[str, Any]:
    if metadata_path is None:
        return {}
    message_file = str(metadata.get("message_file") or "messages.jsonl")
    messages_path = metadata_path.parent / message_file
    if not messages_path.is_file():
        add_issue(issues, "error", f"JSONL message file is missing: {messages_path}.")
        return {"messages_path": str(messages_path), "line_count": 0}

    line_count = 0
    topic_counts: dict[str, int] = {}
    invalid_lines = 0
    with messages_path.open("r", encoding="utf-8") as stream:
        for index, line in enumerate(stream, start=1):
            text = line.strip()
            if not text:
                continue
            line_count += 1
            try:
                event = json.loads(text)
            except json.JSONDecodeError:
                invalid_lines += 1
                add_issue(issues, "error", f"Invalid JSONL event at line {index}.")
                continue
            validate_event(event, index, topic_counts, issues)

    expected_count = int(metadata.get("message_count") or 0)
    if line_count != expected_count:
        add_issue(issues, "error", f"Metadata message_count={expected_count} but JSONL has {line_count} events.")
    compare_topic_counts(metadata.get("topics"), topic_counts, issues)
    return {
        "messages_path": str(messages_path),
        "line_count": line_count,
        "invalid_line_count": invalid_lines,
        "topic_counts": topic_counts,
    }


def validate_event(
    event: Any,
    line_number: int,
    topic_counts: dict[str, int],
    issues: list[dict[str, str]],
) -> None:
    if not isinstance(event, dict):
        add_issue(issues, "error", f"JSONL event at line {line_number} is not an object.")
        return
    for key in ("topic", "type", "timestamp_ns", "message"):
        if key not in event:
            add_issue(issues, "error", f"JSONL event at line {line_number} is missing {key}.")
    topic = event.get("topic")
    if isinstance(topic, str) and topic:
        topic_counts[topic] = topic_counts.get(topic, 0) + 1


def validate_mcap_export(
    artifact_path: Path,
    metadata_path: Path | None,
    metadata: dict[str, Any],
    issues: list[dict[str, str]],
) -> dict[str, Any]:
    if metadata_path is None:
        return {}
    raw_path = metadata.get("mcap_path")
    mcap_path = resolve_sidecar_path(raw_path, metadata_path.parent) if raw_path else artifact_path
    if not mcap_path.is_file():
        add_issue(issues, "error", f"MCAP file is missing: {mcap_path}.")
        size_bytes = 0
    else:
        size_bytes = mcap_path.stat().st_size
        if size_bytes <= 0:
            add_issue(issues, "error", f"MCAP file is empty: {mcap_path}.")
    return {"mcap_path": str(mcap_path), "size_bytes": size_bytes}


def validate_rosbag2_export(
    metadata_path: Path | None,
    metadata: dict[str, Any],
    issues: list[dict[str, str]],
) -> dict[str, Any]:
    if metadata_path is None:
        return {}
    output_dir = resolve_sidecar_path(metadata.get("output_dir"), metadata_path.parent)
    if not output_dir.is_dir():
        add_issue(issues, "error", f"rosbag2 output directory is missing: {output_dir}.")
        storage_files: list[Path] = []
    else:
        storage_files = [
            path
            for path in output_dir.iterdir()
            if path.name not in {"vision_nav_rosbag2_metadata.json"}
            and (path.name == "metadata.yaml" or path.suffix in {".db3", ".mcap"})
        ]
        if not storage_files:
            add_issue(issues, "error", f"rosbag2 output directory has no storage files: {output_dir}.")
    return {
        "output_dir": str(output_dir),
        "storage_id": metadata.get("storage_id"),
        "serialization_format": metadata.get("serialization_format"),
        "storage_files": [path.name for path in sorted(storage_files)],
    }


def resolve_sidecar_path(raw_path: Any, base: Path) -> Path:
    if isinstance(raw_path, str) and raw_path:
        path = Path(raw_path).expanduser()
        return path if path.is_absolute() else base / path
    return base


def summarize_topics(value: list[Any]) -> list[dict[str, Any]]:
    topics = []
    for item in value:
        if not isinstance(item, dict):
            continue
        topics.append(
            {
                "name": item.get("name"),
                "type": item.get("type"),
                "message_count": item.get("message_count"),
            }
        )
    return topics


def compare_topic_counts(
    metadata_topics: Any,
    observed_counts: dict[str, int],
    issues: list[dict[str, str]],
) -> None:
    if not isinstance(metadata_topics, list):
        return
    for topic in metadata_topics:
        if not isinstance(topic, dict):
            continue
        name = topic.get("name")
        if not isinstance(name, str) or not name:
            continue
        expected = int(topic.get("message_count") or 0)
        observed = observed_counts.get(name, 0)
        if expected != observed:
            add_issue(issues, "error", f"Topic {name} expected {expected} messages but JSONL has {observed}.")


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
    report = validate_rosbag_export(args.artifact, output_path=args.output)
    if args.json or args.output is None:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print(f"ROS replay export validation: {report['status']}")
        print(f"__VISION_NAV_ROSBAG_EXPORT_VALIDATION__={report['report_path']}")
    if report["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
