from __future__ import annotations

import argparse
import base64
import json
import math
import mimetypes
import time
from pathlib import Path
from typing import Any

from vision_nav.external_position import ExternalPositionEstimate, current_time_us, external_position_from_match_result


DIAG_OK = 0
DIAG_WARN = 1
DIAG_ERROR = 2
DIAG_STALE = 3
DEFAULT_MAX_FRAME_BYTES = 2_000_000


def ros_stamp_from_us(timestamp_us: int | None) -> dict[str, int]:
    value = int(timestamp_us if timestamp_us is not None else current_time_us())
    sec = value // 1_000_000
    nanosec = (value % 1_000_000) * 1000
    return {"sec": sec, "nanosec": nanosec}


def ros_quaternion_xyzw_from_yaw(yaw_rad: float | None) -> dict[str, float]:
    yaw = 0.0 if yaw_rad is None or not math.isfinite(yaw_rad) else float(yaw_rad)
    half = yaw / 2.0
    return {
        "x": 0.0,
        "y": 0.0,
        "z": math.sin(half),
        "w": math.cos(half),
    }


def ros_pose_covariance_36_from_estimate(estimate: ExternalPositionEstimate) -> list[float]:
    covariance = estimate.covariance.with_mavlink_defaults()
    output = [0.0] * 36
    output[0] = float(covariance.east_m2)
    output[7] = float(covariance.north_m2)
    output[14] = float(covariance.up_m2)
    output[35] = float(covariance.yaw_rad2)
    return output


def odometry_dict_from_estimate(
    estimate: ExternalPositionEstimate,
    *,
    frame_id: str = "map",
    child_frame_id: str = "base_link",
) -> dict[str, Any]:
    return {
        "header": {
            "stamp": ros_stamp_from_us(estimate.timestamp_us),
            "frame_id": frame_id,
        },
        "child_frame_id": child_frame_id,
        "pose": {
            "pose": {
                "position": {
                    "x": float(estimate.east_m),
                    "y": float(estimate.north_m),
                    "z": float(estimate.up_m or 0.0),
                },
                "orientation": ros_quaternion_xyzw_from_yaw(estimate.yaw_enu_rad),
            },
            "covariance": ros_pose_covariance_36_from_estimate(estimate),
        },
        "twist": {
            "twist": {
                "linear": {
                    "x": _nan_if_none(estimate.velocity_east_mps),
                    "y": _nan_if_none(estimate.velocity_north_mps),
                    "z": _nan_if_none(estimate.velocity_up_mps),
                },
                "angular": {
                    "x": math.nan,
                    "y": math.nan,
                    "z": math.nan,
                },
            },
            "covariance": [math.nan] * 36,
        },
        "metadata": {
            "source": estimate.source,
            "confidence": estimate.confidence,
            "coordinate_frame": "local_enu",
        },
    }


def odometry_dict_from_match_result(
    result: dict[str, Any],
    *,
    frame_id: str = "map",
    child_frame_id: str = "base_link",
) -> tuple[dict[str, Any] | None, str | None]:
    estimate, reason = external_position_from_match_result(result)
    if estimate is None:
        return None, reason
    return odometry_dict_from_estimate(estimate, frame_id=frame_id, child_frame_id=child_frame_id), None


def diagnostic_status_from_health(
    health: dict[str, Any] | None,
    *,
    name: str = "vision_nav/external_position",
    hardware_id: str = "vision_nav",
) -> dict[str, Any]:
    health = health or {}
    status = str(health.get("status") or "inactive")
    warnings = list(health.get("last_warnings") or [])
    sent_count = int(health.get("sent_count") or 0)
    if status == "healthy":
        level = DIAG_OK
        message = "external position stream healthy"
    elif status == "degraded" and sent_count == 0:
        level = DIAG_ERROR
        message = "external position stream has no sent measurements"
    elif status == "inactive":
        level = DIAG_STALE
        message = "external position stream inactive"
    else:
        level = DIAG_WARN
        message = f"external position stream {status}"

    values = {
        "status": status,
        "message_type": health.get("message_type"),
        "attempt_count": health.get("attempt_count"),
        "sent_count": health.get("sent_count"),
        "skipped_count": health.get("skipped_count"),
        "send_rate_hz": health.get("send_rate_hz"),
        "last_latency_ms": health.get("last_latency_ms"),
        "last_skip_reason": health.get("last_skip_reason"),
        "warnings": ",".join(str(item) for item in warnings),
    }
    return {
        "level": level,
        "name": name,
        "message": message,
        "hardware_id": hardware_id,
        "values": {key: "" if value is None else str(value) for key, value in values.items()},
    }


def diagnostic_status_from_result(
    result: dict[str, Any],
    *,
    health: dict[str, Any] | None = None,
    name: str = "vision_nav/terrain_matcher",
    hardware_id: str = "vision_nav",
) -> dict[str, Any]:
    if health:
        return diagnostic_status_from_health(health, name="vision_nav/external_position", hardware_id=hardware_id)

    status = str(result.get("status") or "unknown")
    reason = str(result.get("reason") or "")
    if status == "accepted":
        level = DIAG_OK
        message = "terrain match accepted"
    elif status == "rejected":
        level = DIAG_WARN
        message = f"terrain match rejected: {reason or 'unspecified'}"
    else:
        level = DIAG_STALE
        message = f"terrain match status {status}"

    values = {
        "status": status,
        "reason": reason,
        "confidence": result.get("confidence"),
        "position_confidence": result.get("position_confidence"),
        "tile_id": result.get("tile_id"),
        "inliers": result.get("inliers"),
        "reprojection_error_px": result.get("reprojection_error_px"),
    }
    return {
        "level": level,
        "name": name,
        "message": message,
        "hardware_id": hardware_id,
        "values": {key: "" if value is None else str(value) for key, value in values.items()},
    }


def ros_record_from_runtime_record(
    record: dict[str, Any],
    *,
    frame_id: str = "map",
    child_frame_id: str = "base_link",
) -> dict[str, Any]:
    result = record.get("result", record)
    odometry, reason = odometry_dict_from_match_result(result, frame_id=frame_id, child_frame_id=child_frame_id)
    health = record.get("external_position_health")
    diagnostic = diagnostic_status_from_result(result, health=health)
    return {
        "sequence": record.get("sequence"),
        "timestamp_us": record.get("timestamp_us", result.get("timestamp_us") if isinstance(result, dict) else None),
        "timestamp_utc": record.get("timestamp_utc"),
        "frame_path": frame_path_from_runtime_record(record),
        "published": odometry is not None,
        "skip_reason": reason,
        "odometry": odometry,
        "diagnostic": diagnostic,
    }


def ros_records_from_log(
    log_path: str | Path,
    *,
    frame_id: str = "map",
    child_frame_id: str = "base_link",
) -> list[dict[str, Any]]:
    return [
        ros_record_from_runtime_record(record, frame_id=frame_id, child_frame_id=child_frame_id)
        for record in _load_jsonl(log_path)
    ]


def rosbag_jsonl_events_from_records(
    records: list[dict[str, Any]],
    *,
    odometry_topic: str = "/vision_nav/odometry",
    diagnostics_topic: str = "/diagnostics",
    frame_topic: str | None = None,
    camera_frame_id: str = "down_camera",
    frame_root: str | Path | None = None,
    max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES,
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for record in records:
        stamp = _record_stamp(record)
        if frame_topic:
            frame_event = compressed_image_event_from_record(
                record,
                frame_topic=frame_topic,
                camera_frame_id=camera_frame_id,
                frame_root=frame_root,
                max_frame_bytes=max_frame_bytes,
            )
            if frame_event is not None:
                events.append(frame_event)
        if record.get("odometry") is not None:
            events.append(
                {
                    "topic": odometry_topic,
                    "type": "nav_msgs/msg/Odometry",
                    "timestamp_ns": _stamp_to_ns(record["odometry"]["header"]["stamp"]),
                    "sequence": record.get("sequence"),
                    "message": record["odometry"],
                }
            )
        events.append(
            {
                "topic": diagnostics_topic,
                "type": "diagnostic_msgs/msg/DiagnosticArray",
                "timestamp_ns": _stamp_to_ns(stamp),
                "sequence": record.get("sequence"),
                "message": {
                    "header": {
                        "stamp": stamp,
                        "frame_id": "",
                    },
                    "status": [record["diagnostic"]],
                },
            }
        )
    topic_rank = {}
    if frame_topic:
        topic_rank[frame_topic] = 0
    topic_rank[odometry_topic] = 1
    topic_rank[diagnostics_topic] = 2
    events.sort(key=lambda event: (int(event["timestamp_ns"]), topic_rank.get(str(event["topic"]), 99), str(event["topic"])))
    return events


def export_rosbag_jsonl(
    records: list[dict[str, Any]],
    output_dir: str | Path,
    *,
    source_log: str | Path | None = None,
    odometry_topic: str = "/vision_nav/odometry",
    diagnostics_topic: str = "/diagnostics",
    frame_topic: str | None = None,
    camera_frame_id: str = "down_camera",
    frame_root: str | Path | None = None,
    max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES,
) -> dict[str, Any]:
    output_path = Path(output_dir).expanduser()
    output_path.mkdir(parents=True, exist_ok=True)
    messages_path = output_path / "messages.jsonl"
    metadata_path = output_path / "metadata.json"
    effective_frame_root = frame_root
    if effective_frame_root is None and source_log is not None:
        effective_frame_root = Path(source_log).expanduser().parent
    events = rosbag_jsonl_events_from_records(
        records,
        odometry_topic=odometry_topic,
        diagnostics_topic=diagnostics_topic,
        frame_topic=frame_topic,
        camera_frame_id=camera_frame_id,
        frame_root=effective_frame_root,
        max_frame_bytes=max_frame_bytes,
    )
    with messages_path.open("w", encoding="utf-8") as stream:
        for event in events:
            stream.write(json.dumps(event, sort_keys=True, allow_nan=True) + "\n")
    topic_counts: dict[str, int] = {}
    for event in events:
        topic = str(event["topic"])
        topic_counts[topic] = topic_counts.get(topic, 0) + 1
    topics = [
        {
            "name": odometry_topic,
            "type": "nav_msgs/msg/Odometry",
            "message_count": topic_counts.get(odometry_topic, 0),
        },
        {
            "name": diagnostics_topic,
            "type": "diagnostic_msgs/msg/DiagnosticArray",
            "message_count": topic_counts.get(diagnostics_topic, 0),
        },
    ]
    if frame_topic:
        topics.insert(
            0,
            {
                "name": frame_topic,
                "type": "sensor_msgs/msg/CompressedImage",
                "message_count": topic_counts.get(frame_topic, 0),
            },
        )
    metadata = {
        "format": "vision_nav_rosbag_jsonl_v1",
        "source_log": str(source_log) if source_log is not None else None,
        "message_file": messages_path.name,
        "message_count": len(events),
        "source_record_count": len(records),
        "frame_export": {
            "enabled": bool(frame_topic),
            "topic": frame_topic,
            "camera_frame_id": camera_frame_id if frame_topic else None,
            "frame_root": str(effective_frame_root) if effective_frame_root is not None and frame_topic else None,
            "max_frame_bytes": max_frame_bytes if frame_topic else None,
        },
        "topics": topics,
    }
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "output_dir": str(output_path),
        "metadata_path": str(metadata_path),
        "messages_path": str(messages_path),
        "metadata": metadata,
    }


def export_rosbag_mcap(
    records: list[dict[str, Any]],
    output_path: str | Path,
    *,
    source_log: str | Path | None = None,
    odometry_topic: str = "/vision_nav/odometry",
    diagnostics_topic: str = "/diagnostics",
    frame_topic: str | None = None,
    camera_frame_id: str = "down_camera",
    frame_root: str | Path | None = None,
    max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES,
    writer_factory: Any | None = None,
) -> dict[str, Any]:
    writer_cls = writer_factory or _load_mcap_writer()
    path = Path(output_path).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    effective_frame_root = frame_root
    if effective_frame_root is None and source_log is not None:
        effective_frame_root = Path(source_log).expanduser().parent
    events = rosbag_jsonl_events_from_records(
        records,
        odometry_topic=odometry_topic,
        diagnostics_topic=diagnostics_topic,
        frame_topic=frame_topic,
        camera_frame_id=camera_frame_id,
        frame_root=effective_frame_root,
        max_frame_bytes=max_frame_bytes,
    )
    topic_counts: dict[str, int] = {}
    for event in events:
        topic = str(event["topic"])
        topic_counts[topic] = topic_counts.get(topic, 0) + 1

    with path.open("wb") as stream:
        writer = writer_cls(stream)
        writer.start()
        channels: dict[tuple[str, str], int] = {}
        schemas: dict[str, int] = {}
        for event in events:
            message_type = str(event["type"])
            if message_type not in schemas:
                schemas[message_type] = writer.register_schema(
                    name=message_type,
                    encoding="jsonschema",
                    data=json.dumps(_mcap_json_schema(message_type), sort_keys=True).encode("utf-8"),
                )
            key = (str(event["topic"]), message_type)
            if key not in channels:
                channels[key] = writer.register_channel(
                    topic=key[0],
                    message_encoding="json",
                    schema_id=schemas[message_type],
                )
            writer.add_message(
                channel_id=channels[key],
                log_time=int(event["timestamp_ns"]),
                publish_time=int(event["timestamp_ns"]),
                data=json.dumps(event["message"], sort_keys=True, allow_nan=True).encode("utf-8"),
            )
        writer.finish()

    metadata = {
        "format": "vision_nav_mcap_json_v1",
        "message_encoding": "json",
        "schema_encoding": "jsonschema",
        "source_log": str(source_log) if source_log is not None else None,
        "mcap_path": str(path),
        "message_count": len(events),
        "source_record_count": len(records),
        "topics": [
            {
                "name": topic,
                "type": next(str(event["type"]) for event in events if str(event["topic"]) == topic),
                "message_count": count,
            }
            for topic, count in sorted(topic_counts.items())
        ],
        "frame_export": {
            "enabled": bool(frame_topic),
            "topic": frame_topic,
            "camera_frame_id": camera_frame_id if frame_topic else None,
            "frame_root": str(effective_frame_root) if effective_frame_root is not None and frame_topic else None,
            "max_frame_bytes": max_frame_bytes if frame_topic else None,
        },
    }
    metadata_path = path.with_suffix(path.suffix + ".metadata.json")
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"mcap_path": str(path), "metadata_path": str(metadata_path), "metadata": metadata}


def export_rosbag2(
    records: list[dict[str, Any]],
    output_dir: str | Path,
    *,
    source_log: str | Path | None = None,
    odometry_topic: str = "/vision_nav/odometry",
    diagnostics_topic: str = "/diagnostics",
    frame_topic: str | None = None,
    camera_frame_id: str = "down_camera",
    frame_root: str | Path | None = None,
    max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES,
    storage_id: str = "sqlite3",
    writer_factory: Any | None = None,
    storage_options_cls: Any | None = None,
    converter_options_cls: Any | None = None,
    topic_metadata_cls: Any | None = None,
    message_classes: dict[str, Any] | None = None,
    serializer: Any | None = None,
) -> dict[str, Any]:
    runtime = (
        {
            "writer_cls": writer_factory,
            "storage_options_cls": storage_options_cls,
            "converter_options_cls": converter_options_cls,
            "topic_metadata_cls": topic_metadata_cls,
            "message_classes": message_classes,
            "serializer": serializer,
        }
        if writer_factory
        and storage_options_cls
        and converter_options_cls
        and topic_metadata_cls
        and message_classes
        and serializer
        else _load_rosbag2_runtime()
    )
    path = Path(output_dir).expanduser()
    path.parent.mkdir(parents=True, exist_ok=True)
    effective_frame_root = frame_root
    if effective_frame_root is None and source_log is not None:
        effective_frame_root = Path(source_log).expanduser().parent
    events = rosbag_jsonl_events_from_records(
        records,
        odometry_topic=odometry_topic,
        diagnostics_topic=diagnostics_topic,
        frame_topic=frame_topic,
        camera_frame_id=camera_frame_id,
        frame_root=effective_frame_root,
        max_frame_bytes=max_frame_bytes,
    )

    writer = runtime["writer_cls"]()
    writer.open(
        runtime["storage_options_cls"](uri=str(path), storage_id=storage_id),
        runtime["converter_options_cls"](input_serialization_format="cdr", output_serialization_format="cdr"),
    )
    topics: dict[tuple[str, str], dict[str, Any]] = {}
    for event in events:
        key = (str(event["topic"]), str(event["type"]))
        if key in topics:
            continue
        metadata = _make_rosbag2_topic_metadata(runtime["topic_metadata_cls"], topic=key[0], message_type=key[1])
        writer.create_topic(metadata)
        topics[key] = {"name": key[0], "type": key[1], "message_count": 0}

    for event in events:
        message = _rosbag2_message_from_event(event, runtime["message_classes"])
        serialized = runtime["serializer"](message)
        writer.write(str(event["topic"]), serialized, int(event["timestamp_ns"]))
        topics[(str(event["topic"]), str(event["type"]))]["message_count"] += 1

    path.mkdir(parents=True, exist_ok=True)
    metadata = {
        "format": "vision_nav_rosbag2_v1",
        "storage_id": storage_id,
        "serialization_format": "cdr",
        "source_log": str(source_log) if source_log is not None else None,
        "output_dir": str(path),
        "message_count": len(events),
        "source_record_count": len(records),
        "topics": sorted(topics.values(), key=lambda item: str(item["name"])),
        "frame_export": {
            "enabled": bool(frame_topic),
            "topic": frame_topic,
            "camera_frame_id": camera_frame_id if frame_topic else None,
            "frame_root": str(effective_frame_root) if effective_frame_root is not None and frame_topic else None,
            "max_frame_bytes": max_frame_bytes if frame_topic else None,
        },
    }
    metadata_path = path / "vision_nav_rosbag2_metadata.json"
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"output_dir": str(path), "metadata_path": str(metadata_path), "metadata": metadata}


def _load_mcap_writer() -> Any:
    try:
        from mcap.writer import Writer
    except ImportError as exc:
        raise RuntimeError(
            "MCAP export requires the optional 'mcap' Python package. "
            "Install with `pip install .[rosbag]` or use --export-rosbag-jsonl."
        ) from exc
    return Writer


def _load_rosbag2_runtime() -> dict[str, Any]:
    try:
        import rosbag2_py
        from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
        from nav_msgs.msg import Odometry
        from rclpy.serialization import serialize_message
        from sensor_msgs.msg import CompressedImage
    except ImportError as exc:
        raise RuntimeError(
            "Native rosbag2 export requires a sourced ROS 2 Python environment "
            "with rosbag2_py, rclpy, nav_msgs, diagnostic_msgs, and sensor_msgs. "
            "Use --export-rosbag-jsonl or --export-mcap outside ROS 2."
        ) from exc
    return {
        "writer_cls": rosbag2_py.SequentialWriter,
        "storage_options_cls": rosbag2_py.StorageOptions,
        "converter_options_cls": rosbag2_py.ConverterOptions,
        "topic_metadata_cls": rosbag2_py.TopicMetadata,
        "message_classes": {
            "nav_msgs/msg/Odometry": Odometry,
            "diagnostic_msgs/msg/DiagnosticArray": DiagnosticArray,
            "diagnostic_msgs/msg/DiagnosticStatus": DiagnosticStatus,
            "diagnostic_msgs/msg/KeyValue": KeyValue,
            "sensor_msgs/msg/CompressedImage": CompressedImage,
        },
        "serializer": serialize_message,
    }


def _mcap_json_schema(message_type: str) -> dict[str, Any]:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "title": message_type,
        "type": "object",
        "additionalProperties": True,
    }


class Ros2RuntimePublisher:
    def __init__(
        self,
        *,
        node_name: str = "vision_nav_runtime",
        odometry_topic: str = "/vision_nav/odometry",
        diagnostics_topic: str = "/diagnostics",
    ) -> None:
        try:
            import rclpy
            from diagnostic_msgs.msg import DiagnosticArray, DiagnosticStatus, KeyValue
            from nav_msgs.msg import Odometry
        except ImportError as exc:
            raise RuntimeError("ROS 2 live publish mode requires rclpy, nav_msgs, and diagnostic_msgs.") from exc

        self.rclpy = rclpy
        self.DiagnosticArray = DiagnosticArray
        self.DiagnosticStatus = DiagnosticStatus
        self.KeyValue = KeyValue
        self.Odometry = Odometry
        self.rclpy.init(args=None)
        self.node = self.rclpy.create_node(node_name)
        self.odometry_pub = self.node.create_publisher(self.Odometry, odometry_topic, 10)
        self.diagnostics_pub = self.node.create_publisher(self.DiagnosticArray, diagnostics_topic, 10)

    def publish_record(self, record: dict[str, Any], *, frame_id: str = "map", child_frame_id: str = "base_link") -> dict[str, Any]:
        ros_record = ros_record_from_runtime_record(record, frame_id=frame_id, child_frame_id=child_frame_id)
        if ros_record.get("odometry") is not None:
            self.odometry_pub.publish(_odometry_msg_from_dict(self.Odometry, ros_record["odometry"]))
        self.diagnostics_pub.publish(
            _diagnostic_array_from_dict(
                self.DiagnosticArray,
                self.DiagnosticStatus,
                self.KeyValue,
                ros_record["diagnostic"],
            )
        )
        self.rclpy.spin_once(self.node, timeout_sec=0.0)
        return ros_record

    def close(self) -> None:
        self.node.destroy_node()
        self.rclpy.shutdown()


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay vision-nav logs as ROS 2 odometry/diagnostic messages.")
    parser.add_argument("--log", required=True, help="terrain_matches.jsonl or matches.jsonl path.")
    parser.add_argument("--frame-id", default="map")
    parser.add_argument("--child-frame-id", default="base_link")
    parser.add_argument("--publish", action="store_true", help="Publish with rclpy instead of printing JSON.")
    parser.add_argument("--odometry-topic", default="/vision_nav/odometry")
    parser.add_argument("--diagnostics-topic", default="/diagnostics")
    parser.add_argument("--rate-hz", type=float, default=2.0)
    parser.add_argument(
        "--export-rosbag-jsonl",
        help="Write a dependency-free ROS-bag-like directory with metadata.json and topic messages.jsonl.",
    )
    parser.add_argument(
        "--export-mcap",
        help="Write an optional MCAP archive with JSON-encoded ROS-shaped topic messages.",
    )
    parser.add_argument(
        "--export-rosbag2",
        help="Write a native rosbag2 directory with serialized ROS messages. Requires a sourced ROS 2 Python environment.",
    )
    parser.add_argument("--include-frame-topic", action="store_true", help="Include bounded camera frame messages in exported replay artifacts.")
    parser.add_argument("--frame-topic", default="/vision_nav/camera/image/compressed")
    parser.add_argument("--camera-frame-id", default="down_camera")
    parser.add_argument("--frame-root", help="Resolve relative frame_path entries from this directory. Defaults to the log directory.")
    parser.add_argument("--max-frame-bytes", type=int, default=DEFAULT_MAX_FRAME_BYTES)
    parser.add_argument("--rosbag2-storage-id", default="sqlite3", help="Native rosbag2 storage id for --export-rosbag2.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    records = ros_records_from_log(args.log, frame_id=args.frame_id, child_frame_id=args.child_frame_id)
    if args.export_rosbag_jsonl:
        result = export_rosbag_jsonl(
            records,
            args.export_rosbag_jsonl,
            source_log=args.log,
            odometry_topic=args.odometry_topic,
            diagnostics_topic=args.diagnostics_topic,
            frame_topic=args.frame_topic if args.include_frame_topic else None,
            camera_frame_id=args.camera_frame_id,
            frame_root=args.frame_root,
            max_frame_bytes=args.max_frame_bytes,
        )
        print(json.dumps(result["metadata"], indent=2, sort_keys=True))
        return
    if args.export_rosbag2:
        result = export_rosbag2(
            records,
            args.export_rosbag2,
            source_log=args.log,
            odometry_topic=args.odometry_topic,
            diagnostics_topic=args.diagnostics_topic,
            frame_topic=args.frame_topic if args.include_frame_topic else None,
            camera_frame_id=args.camera_frame_id,
            frame_root=args.frame_root,
            max_frame_bytes=args.max_frame_bytes,
            storage_id=args.rosbag2_storage_id,
        )
        print(json.dumps(result["metadata"], indent=2, sort_keys=True))
        return
    if args.export_mcap:
        result = export_rosbag_mcap(
            records,
            args.export_mcap,
            source_log=args.log,
            odometry_topic=args.odometry_topic,
            diagnostics_topic=args.diagnostics_topic,
            frame_topic=args.frame_topic if args.include_frame_topic else None,
            camera_frame_id=args.camera_frame_id,
            frame_root=args.frame_root,
            max_frame_bytes=args.max_frame_bytes,
        )
        print(json.dumps(result["metadata"], indent=2, sort_keys=True))
        return
    if args.publish:
        _publish_with_rclpy(
            records,
            odometry_topic=args.odometry_topic,
            diagnostics_topic=args.diagnostics_topic,
            rate_hz=args.rate_hz,
        )
        return
    print(json.dumps(records, indent=2, sort_keys=True, allow_nan=True))


def _load_jsonl(log_path: str | Path) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    with Path(log_path).expanduser().open("r", encoding="utf-8") as stream:
        for line_number, line in enumerate(stream, start=1):
            line = line.strip()
            if not line:
                continue
            try:
                records.append(json.loads(line))
            except json.JSONDecodeError as exc:
                raise ValueError(f"{log_path}:{line_number}: invalid JSONL record") from exc
    return records


def _publish_with_rclpy(
    records: list[dict[str, Any]],
    *,
    odometry_topic: str,
    diagnostics_topic: str,
    rate_hz: float,
) -> None:
    publisher = Ros2RuntimePublisher(
        node_name="vision_nav_log_replay",
        odometry_topic=odometry_topic,
        diagnostics_topic=diagnostics_topic,
    )
    period_s = 1.0 / max(float(rate_hz), 0.1)
    try:
        for record in records:
            if record.get("odometry") is not None:
                publisher.odometry_pub.publish(_odometry_msg_from_dict(publisher.Odometry, record["odometry"]))
            publisher.diagnostics_pub.publish(
                _diagnostic_array_from_dict(
                    publisher.DiagnosticArray,
                    publisher.DiagnosticStatus,
                    publisher.KeyValue,
                    record["diagnostic"],
                )
            )
            publisher.rclpy.spin_once(publisher.node, timeout_sec=0.0)
            time.sleep(period_s)
    finally:
        publisher.close()


def _stamp_to_ns(stamp: dict[str, int]) -> int:
    return int(stamp.get("sec") or 0) * 1_000_000_000 + int(stamp.get("nanosec") or 0)


def frame_path_from_runtime_record(record: dict[str, Any]) -> str | None:
    for key in ("frame_path", "frame", "image_path", "path"):
        value = record.get(key)
        if isinstance(value, str) and value:
            return value
    capture = record.get("capture")
    if isinstance(capture, dict):
        value = capture.get("frame_path")
        if isinstance(value, str) and value:
            return value
    return None


def resolve_frame_path(frame_path: str, frame_root: str | Path | None = None) -> Path:
    path = Path(frame_path).expanduser()
    if path.is_absolute():
        return path
    if frame_root is not None:
        return Path(frame_root).expanduser() / path
    return path


def compressed_image_event_from_record(
    record: dict[str, Any],
    *,
    frame_topic: str,
    camera_frame_id: str,
    frame_root: str | Path | None = None,
    max_frame_bytes: int = DEFAULT_MAX_FRAME_BYTES,
) -> dict[str, Any] | None:
    frame_path = record.get("frame_path")
    if not isinstance(frame_path, str) or not frame_path:
        return None
    path = resolve_frame_path(frame_path, frame_root)
    try:
        stat = path.stat()
    except OSError:
        return None
    if stat.st_size <= 0 or stat.st_size > max_frame_bytes:
        return None
    data = path.read_bytes()
    mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
    if mime_type == "image/jpeg":
        image_format = "jpeg"
    elif mime_type == "image/png":
        image_format = "png"
    elif mime_type.startswith("image/"):
        image_format = mime_type.removeprefix("image/")
    else:
        image_format = path.suffix.lower().lstrip(".") or "unknown"
    stamp = _record_stamp(record)
    return {
        "topic": frame_topic,
        "type": "sensor_msgs/msg/CompressedImage",
        "timestamp_ns": _stamp_to_ns(stamp),
        "sequence": record.get("sequence"),
        "message": {
            "header": {
                "stamp": stamp,
                "frame_id": camera_frame_id,
            },
            "format": image_format,
            "data_base64": base64.b64encode(data).decode("ascii"),
            "metadata": {
                "source_path": str(path),
                "source_name": path.name,
                "size_bytes": stat.st_size,
                "mime_type": mime_type,
            },
        },
    }


def _record_stamp(record: dict[str, Any]) -> dict[str, int]:
    odometry = record.get("odometry")
    if isinstance(odometry, dict):
        header = odometry.get("header") or {}
        stamp = header.get("stamp")
        if isinstance(stamp, dict):
            return {"sec": int(stamp.get("sec") or 0), "nanosec": int(stamp.get("nanosec") or 0)}
    timestamp_us = record.get("timestamp_us")
    if timestamp_us is not None:
        return ros_stamp_from_us(int(timestamp_us))
    return ros_stamp_from_us(None)


def _odometry_msg_from_dict(odometry_cls: Any, data: dict[str, Any]) -> Any:
    msg = odometry_cls()
    stamp = data["header"]["stamp"]
    msg.header.stamp.sec = int(stamp["sec"])
    msg.header.stamp.nanosec = int(stamp["nanosec"])
    msg.header.frame_id = data["header"]["frame_id"]
    msg.child_frame_id = data["child_frame_id"]
    position = data["pose"]["pose"]["position"]
    orientation = data["pose"]["pose"]["orientation"]
    msg.pose.pose.position.x = float(position["x"])
    msg.pose.pose.position.y = float(position["y"])
    msg.pose.pose.position.z = float(position["z"])
    msg.pose.pose.orientation.x = float(orientation["x"])
    msg.pose.pose.orientation.y = float(orientation["y"])
    msg.pose.pose.orientation.z = float(orientation["z"])
    msg.pose.pose.orientation.w = float(orientation["w"])
    msg.pose.covariance = list(data["pose"]["covariance"])
    linear = data["twist"]["twist"]["linear"]
    angular = data["twist"]["twist"]["angular"]
    msg.twist.twist.linear.x = float(linear["x"])
    msg.twist.twist.linear.y = float(linear["y"])
    msg.twist.twist.linear.z = float(linear["z"])
    msg.twist.twist.angular.x = float(angular["x"])
    msg.twist.twist.angular.y = float(angular["y"])
    msg.twist.twist.angular.z = float(angular["z"])
    msg.twist.covariance = list(data["twist"]["covariance"])
    return msg


def _make_rosbag2_topic_metadata(topic_metadata_cls: Any, *, topic: str, message_type: str) -> Any:
    try:
        return topic_metadata_cls(
            name=topic,
            type=message_type,
            serialization_format="cdr",
            offered_qos_profiles="",
        )
    except TypeError:
        return topic_metadata_cls(name=topic, type=message_type, serialization_format="cdr")


def _rosbag2_message_from_event(event: dict[str, Any], message_classes: dict[str, Any]) -> Any:
    message_type = str(event["type"])
    message = event["message"]
    if message_type == "nav_msgs/msg/Odometry":
        return _odometry_msg_from_dict(message_classes[message_type], message)
    if message_type == "diagnostic_msgs/msg/DiagnosticArray":
        return _diagnostic_array_msg_from_dict(
            message_classes["diagnostic_msgs/msg/DiagnosticArray"],
            message_classes["diagnostic_msgs/msg/DiagnosticStatus"],
            message_classes["diagnostic_msgs/msg/KeyValue"],
            message,
        )
    if message_type == "sensor_msgs/msg/CompressedImage":
        return _compressed_image_msg_from_dict(message_classes[message_type], message)
    raise ValueError(f"Unsupported rosbag2 message type: {message_type}")


def _diagnostic_array_msg_from_dict(
    diagnostic_array_cls: Any,
    diagnostic_status_cls: Any,
    key_value_cls: Any,
    data: dict[str, Any],
) -> Any:
    msg = diagnostic_array_cls()
    header = data.get("header") or {}
    stamp = header.get("stamp") or {"sec": 0, "nanosec": 0}
    msg.header.stamp.sec = int(stamp.get("sec") or 0)
    msg.header.stamp.nanosec = int(stamp.get("nanosec") or 0)
    msg.header.frame_id = str(header.get("frame_id") or "")
    msg.status = [
        _diagnostic_status_msg_from_dict(diagnostic_status_cls, key_value_cls, status)
        for status in data.get("status", [])
    ]
    return msg


def _diagnostic_status_msg_from_dict(diagnostic_status_cls: Any, key_value_cls: Any, data: dict[str, Any]) -> Any:
    status = diagnostic_status_cls()
    status.level = int(data["level"])
    status.name = data["name"]
    status.message = data["message"]
    status.hardware_id = data["hardware_id"]
    status.values = []
    for key, value in data.get("values", {}).items():
        pair = key_value_cls()
        pair.key = key
        pair.value = str(value)
        status.values.append(pair)
    return status


def _compressed_image_msg_from_dict(compressed_image_cls: Any, data: dict[str, Any]) -> Any:
    msg = compressed_image_cls()
    stamp = data["header"]["stamp"]
    msg.header.stamp.sec = int(stamp["sec"])
    msg.header.stamp.nanosec = int(stamp["nanosec"])
    msg.header.frame_id = data["header"]["frame_id"]
    msg.format = data["format"]
    msg.data = base64.b64decode(data.get("data_base64") or "")
    return msg


def _diagnostic_array_from_dict(
    diagnostic_array_cls: Any,
    diagnostic_status_cls: Any,
    key_value_cls: Any,
    data: dict[str, Any],
) -> Any:
    msg = diagnostic_array_cls()
    msg.status = [_diagnostic_status_msg_from_dict(diagnostic_status_cls, key_value_cls, data)]
    return msg


def _nan_if_none(value: float | None) -> float:
    if value is None or not math.isfinite(value):
        return math.nan
    return float(value)


if __name__ == "__main__":
    main()
