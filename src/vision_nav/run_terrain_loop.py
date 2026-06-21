from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from vision_nav.capture_frame import capture_frame
from vision_nav.mavlink_bridge import MavlinkVisionBridge
from vision_nav.terrain_bundle import load_terrain_bundle
from vision_nav.terrain_estimator import TerrainEstimator
from vision_nav.terrain_matcher import TerrainMatchOptions, match_terrain_frame


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture frames and localize them against a tiled terrain bundle.")
    parser.add_argument("--bundle", required=True, help="Bundle directory or manifest.json path.")
    parser.add_argument("--output-dir", required=True, help="Output folder for frames and logs.")
    parser.add_argument("--count", type=int, default=10, help="Number of frames to process. Use 0 for endless.")
    parser.add_argument("--interval-s", type=float, default=1.0)
    parser.add_argument("--width", type=int, default=1456)
    parser.add_argument("--height", type=int, default=1088)
    parser.add_argument("--timeout-ms", type=int, default=1000)
    parser.add_argument("--method", choices=["orb", "akaze", "sift"], help="Override feature method.")
    parser.add_argument("--max-features", type=int, default=3000)
    parser.add_argument("--ratio", type=float, default=0.75)
    parser.add_argument("--min-inliers", type=int, default=18)
    parser.add_argument("--ransac-threshold", type=float, default=4.0)
    parser.add_argument("--max-candidates", type=int, default=64)
    parser.add_argument("--search-radius-m", type=float, default=80.0)
    parser.add_argument("--camera-calibration", help="Optional camera calibration YAML for frame undistortion.")
    parser.add_argument("--mavlink-endpoint", help="Optional MAVLink endpoint for accepted vision measurements.")
    parser.add_argument("--mavlink-ev-delay-ms", type=int, default=50)
    parser.add_argument("--mavlink-system-id", type=int, default=1)
    parser.add_argument("--mavlink-component-id", type=int, default=197)
    parser.add_argument("--mavlink-source-system", type=int, default=42)
    parser.add_argument("--mavlink-source-component", type=int, default=197)
    return parser.parse_args()


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")


def status_line(sequence: int, result: dict, capture_duration_s: float, match_duration_s: float) -> str:
    reason = result.get("reason") or ""
    lat_lon = result.get("lat_lon") or {}
    lat = lat_lon.get("lat")
    lon = lat_lon.get("lon")
    position = f" lat={lat:.7f} lon={lon:.7f}" if lat is not None and lon is not None else ""
    return (
        f"[{sequence:06d}] {result.get('status')} tile={result.get('tile_id')} "
        f"conf={result.get('confidence', 0.0):.3f} scale={result.get('scale_confidence', 0.0):.3f} "
        f"inliers={result.get('inliers', 0)} capture={capture_duration_s:.2f}s match={match_duration_s:.2f}s "
        f"{reason}{position}"
    ).rstrip()


def main() -> None:
    args = parse_args()
    bundle = load_terrain_bundle(args.bundle)
    estimator = TerrainEstimator()
    mavlink_bridge = None
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

    output_dir = Path(args.output_dir)
    frames_dir = output_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "terrain_matches.jsonl"
    sequence = 0
    print(f"Writing terrain match log: {log_path}")

    try:
        with log_path.open("a", encoding="utf-8") as log_file:
            while args.count == 0 or sequence < args.count:
                sequence += 1
                loop_start = time.monotonic()
                stamp = utc_stamp()
                frame_path = frames_dir / f"terrain_frame_{sequence:06d}_{stamp}.jpg"
                capture_start = time.monotonic()
                capture_frame(frame_path, args.width, args.height, args.timeout_ms)
                capture_duration_s = time.monotonic() - capture_start

                state = estimator.state
                options = TerrainMatchOptions(
                    method=args.method,
                    max_features=args.max_features,
                    ratio=args.ratio,
                    min_inliers=args.min_inliers,
                    ransac_threshold=args.ransac_threshold,
                    max_candidates=args.max_candidates,
                    prior_east_m=state.east_m,
                    prior_north_m=state.north_m,
                    search_radius_m=args.search_radius_m if state.initialized else None,
                    camera_calibration=args.camera_calibration,
                )
                match_start = time.monotonic()
                result = match_terrain_frame(bundle, str(frame_path), options)
                telemetry_samples = []
                barometer_sample = None
                if mavlink_bridge is not None:
                    for _ in range(4):
                        telemetry = mavlink_bridge.try_read_telemetry(timeout_s=0.0)
                        if telemetry is None:
                            continue
                        telemetry_dict = telemetry.to_dict()
                        telemetry_samples.append(telemetry_dict)
                        if telemetry.yaw_rad is not None:
                            estimator.update_attitude(yaw_rad=telemetry.yaw_rad)
                        if (
                            telemetry.pressure_altitude_m is not None
                            or telemetry.relative_altitude_m is not None
                            or telemetry.pressure_hpa is not None
                        ):
                            barometer_sample = {
                                "timestamp_us": telemetry.timestamp_us,
                                "altitude_m": telemetry.pressure_altitude_m,
                                "relative_altitude_m": telemetry.relative_altitude_m,
                                "pressure_hpa": telemetry.pressure_hpa,
                                "source": f"mavlink:{telemetry.message_type}",
                            }
                result = estimator.update_from_match(result, barometer_sample=barometer_sample)
                match_duration_s = time.monotonic() - match_start

                mavlink_result = None
                if mavlink_bridge is not None:
                    mavlink_result = mavlink_bridge.send_match_result(result).to_dict()

                record = {
                    "sequence": sequence,
                    "timestamp_utc": stamp,
                    "frame_path": str(frame_path),
                    "capture_duration_s": capture_duration_s,
                    "match_duration_s": match_duration_s,
                    "telemetry": telemetry_samples,
                    "mavlink": mavlink_result,
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
