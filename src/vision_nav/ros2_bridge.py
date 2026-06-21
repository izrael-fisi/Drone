from __future__ import annotations

import argparse
import json
import math
import time
from pathlib import Path
from typing import Any

from vision_nav.external_position import ExternalPositionEstimate, current_time_us, external_position_from_match_result


DIAG_OK = 0
DIAG_WARN = 1
DIAG_ERROR = 2
DIAG_STALE = 3


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
) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    for record in records:
        stamp = _record_stamp(record)
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
    topic_rank = {odometry_topic: 0, diagnostics_topic: 1}
    events.sort(key=lambda event: (int(event["timestamp_ns"]), topic_rank.get(str(event["topic"]), 99), str(event["topic"])))
    return events


def export_rosbag_jsonl(
    records: list[dict[str, Any]],
    output_dir: str | Path,
    *,
    source_log: str | Path | None = None,
    odometry_topic: str = "/vision_nav/odometry",
    diagnostics_topic: str = "/diagnostics",
) -> dict[str, Any]:
    output_path = Path(output_dir).expanduser()
    output_path.mkdir(parents=True, exist_ok=True)
    messages_path = output_path / "messages.jsonl"
    metadata_path = output_path / "metadata.json"
    events = rosbag_jsonl_events_from_records(
        records,
        odometry_topic=odometry_topic,
        diagnostics_topic=diagnostics_topic,
    )
    with messages_path.open("w", encoding="utf-8") as stream:
        for event in events:
            stream.write(json.dumps(event, sort_keys=True, allow_nan=True) + "\n")
    topic_counts: dict[str, int] = {}
    for event in events:
        topic = str(event["topic"])
        topic_counts[topic] = topic_counts.get(topic, 0) + 1
    metadata = {
        "format": "vision_nav_rosbag_jsonl_v1",
        "source_log": str(source_log) if source_log is not None else None,
        "message_file": messages_path.name,
        "message_count": len(events),
        "source_record_count": len(records),
        "topics": [
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
        ],
    }
    metadata_path.write_text(json.dumps(metadata, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {
        "output_dir": str(output_path),
        "metadata_path": str(metadata_path),
        "messages_path": str(messages_path),
        "metadata": metadata,
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


def _diagnostic_array_from_dict(
    diagnostic_array_cls: Any,
    diagnostic_status_cls: Any,
    key_value_cls: Any,
    data: dict[str, Any],
) -> Any:
    msg = diagnostic_array_cls()
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
    msg.status = [status]
    return msg


def _nan_if_none(value: float | None) -> float:
    if value is None or not math.isfinite(value):
        return math.nan
    return float(value)


if __name__ == "__main__":
    main()
