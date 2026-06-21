from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from vision_nav.build_map_bundle import build_map_bundle
from vision_nav.bundle import load_manifest, manifest_features_path, manifest_orthophoto_path
from vision_nav.capture_frame import capture_frame
from vision_nav.external_position_health import ExternalPositionHealthConfig, ExternalPositionStreamHealth
from vision_nav.match_frame_to_map import match_frame_to_map
from vision_nav.mavlink_bridge import MavlinkVisionBridge


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Capture frames from the Pi camera and match them against a map bundle."
    )
    parser.add_argument("--bundle", required=True, help="Bundle directory or manifest.json path.")
    parser.add_argument("--output-dir", required=True, help="Output folder for frames, logs, and debug images.")
    parser.add_argument("--count", type=int, default=10, help="Number of frames to process. Use 0 for endless.")
    parser.add_argument("--interval-s", type=float, default=1.0, help="Target delay between loop starts.")
    parser.add_argument("--width", type=int, default=1456)
    parser.add_argument("--height", type=int, default=1088)
    parser.add_argument("--timeout-ms", type=int, default=1000)
    parser.add_argument("--method", choices=["orb", "akaze", "sift"], help="Override feature method.")
    parser.add_argument("--max-features", type=int, default=3000)
    parser.add_argument("--ratio", type=float, default=0.75)
    parser.add_argument("--min-inliers", type=int, default=18)
    parser.add_argument("--ransac-threshold", type=float, default=4.0)
    parser.add_argument("--min-scale", type=float, default=0.2)
    parser.add_argument("--max-scale", type=float, default=5.0)
    parser.add_argument("--max-rotation-deg", type=float, default=90.0)
    parser.add_argument("--max-scale-anisotropy", type=float, default=3.0)
    parser.add_argument("--max-perspective-norm", type=float, default=0.01)
    parser.add_argument("--camera-calibration", help="Optional camera calibration YAML for frame undistortion.")
    parser.add_argument(
        "--viz-every",
        type=int,
        default=0,
        help="Write a match visualization every N frames. Use 0 to disable.",
    )
    parser.add_argument(
        "--build-if-missing",
        action="store_true",
        help="Build the bundle feature index before starting if it is missing.",
    )
    parser.add_argument("--mavlink-endpoint", help="Optional MAVLink endpoint for accepted vision measurements.")
    parser.add_argument("--mavlink-ev-delay-ms", type=int, default=50)
    parser.add_argument("--mavlink-system-id", type=int, default=1)
    parser.add_argument("--mavlink-component-id", type=int, default=197)
    parser.add_argument("--mavlink-source-system", type=int, default=42)
    parser.add_argument("--mavlink-source-component", type=int, default=197)
    parser.add_argument(
        "--mavlink-message",
        choices=["vision_position_estimate", "odometry"],
        default="vision_position_estimate",
        help="MAVLink external-position message to send when --mavlink-endpoint is set.",
    )
    parser.add_argument("--external-position-min-rate-hz", type=float, default=1.0)
    parser.add_argument("--external-position-max-latency-ms", type=float, default=500.0)
    parser.add_argument("--external-position-max-horizontal-var-m2", type=float, default=400.0)
    return parser.parse_args()


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")


def status_line(sequence: int, result: dict, capture_duration_s: float, match_duration_s: float) -> str:
    reason = result.get("reason") or ""
    position = result.get("estimated_position") or {}
    lat = position.get("latitude")
    lon = position.get("longitude")
    position_text = f" lat={lat:.7f} lon={lon:.7f}" if lat is not None and lon is not None else ""
    return (
        f"[{sequence:06d}] {result.get('status')} "
        f"conf={result.get('confidence', 0.0):.3f} "
        f"inliers={result.get('inliers', 0)} "
        f"capture={capture_duration_s:.2f}s match={match_duration_s:.2f}s "
        f"{reason}{position_text}"
    ).rstrip()


def ensure_feature_index(bundle_arg: str, build_if_missing: bool) -> tuple[Path, Path, Path]:
    bundle_dir, manifest = load_manifest(bundle_arg)
    map_path = manifest_orthophoto_path(bundle_dir, manifest)
    features_path = manifest_features_path(bundle_dir, manifest)

    if not features_path.exists():
        if not build_if_missing:
            raise SystemExit(
                f"Feature index missing: {features_path}\n"
                "Run vision-nav-build-bundle first, or add --build-if-missing."
            )
        build_map_bundle(str(bundle_dir))

    return bundle_dir, map_path, features_path


def main() -> None:
    args = parse_args()
    _, map_path, features_path = ensure_feature_index(args.bundle, args.build_if_missing)
    mavlink_bridge = None
    external_position_health = None
    if args.mavlink_endpoint:
        mavlink_bridge = MavlinkVisionBridge(
            args.mavlink_endpoint,
            system_id=args.mavlink_system_id,
            component_id=args.mavlink_component_id,
            source_system=args.mavlink_source_system,
            source_component=args.mavlink_source_component,
            ev_delay_ms=args.mavlink_ev_delay_ms,
        )
        mavlink_bridge.connect()
        external_position_health = ExternalPositionStreamHealth(
            ExternalPositionHealthConfig(
                min_rate_hz=args.external_position_min_rate_hz,
                max_latency_ms=args.external_position_max_latency_ms,
                max_horizontal_variance_m2=args.external_position_max_horizontal_var_m2,
            )
        )

    output_dir = Path(args.output_dir)
    frames_dir = output_dir / "frames"
    viz_dir = output_dir / "viz"
    frames_dir.mkdir(parents=True, exist_ok=True)
    if args.viz_every > 0:
        viz_dir.mkdir(parents=True, exist_ok=True)

    log_path = output_dir / "matches.jsonl"
    sequence = 0
    print(f"Writing match log: {log_path}")

    try:
        log_file_context = log_path.open("a", encoding="utf-8")
    except Exception:
        if mavlink_bridge:
            mavlink_bridge.close()
        raise

    with log_file_context as log_file:
        try:
            while args.count == 0 or sequence < args.count:
                sequence += 1
                loop_start = time.monotonic()
                stamp = utc_stamp()
                frame_path = frames_dir / f"frame_{sequence:06d}_{stamp}.jpg"

                capture_start = time.monotonic()
                capture_frame(frame_path, args.width, args.height, args.timeout_ms)
                capture_duration_s = time.monotonic() - capture_start

                viz_path = None
                if args.viz_every > 0 and sequence % args.viz_every == 0:
                    viz_path = viz_dir / f"match_{sequence:06d}_{stamp}.jpg"

                match_args = argparse.Namespace(
                    map_image=str(map_path),
                    features=str(features_path),
                    frame=str(frame_path),
                    method=args.method,
                    max_features=args.max_features,
                    ratio=args.ratio,
                    min_inliers=args.min_inliers,
                    ransac_threshold=args.ransac_threshold,
                    min_scale=args.min_scale,
                    max_scale=args.max_scale,
                    max_rotation_deg=args.max_rotation_deg,
                    max_scale_anisotropy=args.max_scale_anisotropy,
                    max_perspective_norm=args.max_perspective_norm,
                    camera_calibration=args.camera_calibration,
                    viz=str(viz_path) if viz_path else None,
                )

                match_start = time.monotonic()
                result = match_frame_to_map(match_args)
                match_duration_s = time.monotonic() - match_start
                mavlink_result = None
                external_position_health_snapshot = None
                if mavlink_bridge is not None:
                    mavlink_result = mavlink_bridge.send_match_result(
                        result,
                        message_type=args.mavlink_message,
                    ).to_dict()
                    if external_position_health is not None:
                        external_position_health_snapshot = external_position_health.update(
                            result=result,
                            mavlink_result=mavlink_result,
                            message_type=args.mavlink_message,
                        ).to_dict()

                record = {
                    "sequence": sequence,
                    "timestamp_utc": stamp,
                    "frame_path": str(frame_path),
                    "viz_path": str(viz_path) if viz_path else None,
                    "capture_duration_s": capture_duration_s,
                    "match_duration_s": match_duration_s,
                    "mavlink": mavlink_result,
                    "external_position_health": external_position_health_snapshot,
                    "result": result,
                }
                log_file.write(json.dumps(record, sort_keys=True) + "\n")
                log_file.flush()
                print(status_line(sequence, result, capture_duration_s, match_duration_s))

                sleep_s = args.interval_s - (time.monotonic() - loop_start)
                if sleep_s > 0:
                    time.sleep(sleep_s)
        except KeyboardInterrupt:
            print("\nStopped by user.")
        finally:
            if mavlink_bridge:
                mavlink_bridge.close()


if __name__ == "__main__":
    main()
