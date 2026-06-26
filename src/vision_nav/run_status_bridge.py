from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from vision_nav.mavlink_bridge import MavlinkVisionBridge
from vision_nav.position_telemetry import FixCadenceTracker, GpsHealthConfig, UdpPositionBroadcaster, build_position_update
from vision_nav.product_profiles import camera_profile, hardware_profile, parse_float_or_none, runtime_profile
from vision_nav.runtime_status import bridge_status_snapshot, write_runtime_status


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run a lightweight Pi status bridge without requiring a terrain bundle.")
    parser.add_argument("--output-dir", required=True, help="Output folder for status logs.")
    parser.add_argument("--count", type=int, default=0, help="Number of status packets. Use 0 for endless.")
    parser.add_argument("--interval-s", type=float, default=1.0)
    parser.add_argument("--mavlink-endpoint", help="Optional MAVLink endpoint to read PX4 telemetry.")
    parser.add_argument("--mavlink-source-system", type=int, default=42)
    parser.add_argument("--mavlink-source-component", type=int, default=197)
    parser.add_argument("--position-udp-target", help="Ground-station UDP target, e.g. 255.255.255.255:17660.")
    parser.add_argument("--active-bundle", help="Configured active bundle path, even if it is not valid yet.")
    parser.add_argument("--runtime-profile", default="pi5_full", choices=["pi5_full", "pi5_low_memory", "desktop_high_compute"])
    parser.add_argument(
        "--camera-profile",
        default="rgb_global_shutter",
        choices=["rgb_global_shutter", "rgb_rolling_shutter", "thermal_low_res", "eo_generic"],
    )
    parser.add_argument("--module-weight-g")
    parser.add_argument("--estimated-bom-usd")
    parser.add_argument("--camera-cost-usd")
    parser.add_argument("--sensor-compliance-notes", default="")
    parser.add_argument("--mount-vibration-notes", default="")
    parser.add_argument("--gps-min-fix-type", type=int, default=3)
    parser.add_argument("--gps-min-satellites", type=int, default=6)
    parser.add_argument("--gps-max-eph-m", type=float, default=3.0)
    parser.add_argument("--gps-max-h-acc-m", type=float, default=3.0)
    return parser.parse_args()


def utc_stamp() -> str:
    return datetime.now(timezone.utc).isoformat()


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir).expanduser()
    output_dir.mkdir(parents=True, exist_ok=True)
    log_path = output_dir / "runtime_status_bridge.jsonl"
    status_path = output_dir / "runtime_status.json"
    runtime = runtime_profile(args.runtime_profile)
    camera = camera_profile(args.camera_profile)
    hardware = hardware_profile(
        runtime=runtime,
        camera=camera,
        module_weight_g=parse_float_or_none(args.module_weight_g),
        estimated_bom_usd=parse_float_or_none(args.estimated_bom_usd),
        camera_cost_usd=parse_float_or_none(args.camera_cost_usd),
        sensor_compliance_notes=args.sensor_compliance_notes,
        mount_vibration_notes=args.mount_vibration_notes,
    )
    gps_config = GpsHealthConfig(
        min_fix_type=args.gps_min_fix_type,
        min_satellites=args.gps_min_satellites,
        max_eph_m=args.gps_max_eph_m,
        max_h_acc_m=args.gps_max_h_acc_m,
    )
    fix_tracker = FixCadenceTracker()
    broadcaster = UdpPositionBroadcaster(args.position_udp_target) if args.position_udp_target else None
    mavlink_bridge = None
    mavlink_error = None
    if args.mavlink_endpoint:
        try:
            mavlink_bridge = MavlinkVisionBridge(
                args.mavlink_endpoint,
                source_system=args.mavlink_source_system,
                source_component=args.mavlink_source_component,
            )
            mavlink_bridge.connect()
        except Exception as exc:  # pragma: no cover - depends on host MAVLink setup
            mavlink_error = str(exc)
            mavlink_bridge = None

    status_counts: dict[str, int] = {}
    started_at_utc = utc_stamp()
    sequence = 0
    print(f"Writing status bridge log: {log_path}")
    print(f"Writing runtime status: {status_path}")

    try:
        with log_path.open("a", encoding="utf-8") as log_file:
            while args.count == 0 or sequence < args.count:
                sequence += 1
                telemetry_samples = []
                if mavlink_bridge is not None:
                    for _ in range(6):
                        telemetry = mavlink_bridge.try_read_telemetry(timeout_s=0.0)
                        if telemetry is not None:
                            telemetry_samples.append(telemetry.to_dict())
                result = {
                    "status": "not_running",
                    "reason": "terrain_loop_not_started",
                    "lat_lon": {"lat": None, "lon": None},
                    "local_enu_m": {"x": None, "y": None, "z": None},
                    "confidence": 0.0,
                }
                packet = build_position_update(
                    sequence=sequence,
                    timestamp_utc=utc_stamp(),
                    result=result,
                    telemetry_samples=telemetry_samples,
                    gps_config=gps_config,
                    fix_tracker=fix_tracker,
                )
                record = {
                    "sequence": sequence,
                    "timestamp_utc": packet["timestamp_utc"],
                    "telemetry": telemetry_samples,
                    "mavlink": {"connected": mavlink_bridge is not None, "error": mavlink_error},
                    "camera_health": {
                        "status": "ready" if camera["id"] == "rgb_global_shutter" else "metadata_only",
                        "message": camera.get("notes"),
                    },
                    "position_update": packet,
                    "result": result,
                    "runtime_profile": runtime,
                    "camera_profile": camera,
                    "hardware_profile": hardware,
                }
                status_counts[str(packet.get("source_state") or "no_position")] = (
                    status_counts.get(str(packet.get("source_state") or "no_position"), 0) + 1
                )
                if broadcaster is not None:
                    try:
                        broadcaster.send(packet)
                    except OSError as exc:
                        record["position_broadcast_error"] = str(exc)
                log_file.write(json.dumps(record, sort_keys=True) + "\n")
                log_file.flush()
                write_runtime_status(
                    status_path,
                    bridge_status_snapshot(
                        output_dir=output_dir,
                        sequence=sequence,
                        record=record,
                        status_counts=status_counts,
                        started_at_utc=started_at_utc,
                        active_bundle_path=args.active_bundle,
                        runtime_profile=runtime,
                        camera_profile=camera,
                        hardware_profile=hardware,
                    ),
                )
                print(
                    f"[{sequence:06d}] source_state={packet.get('source_state')} "
                    f"gps={packet.get('gps_health', {}).get('reason')} mavlink={'connected' if mavlink_bridge else 'offline'}"
                )
                time.sleep(max(args.interval_s, 0.05))
    except KeyboardInterrupt:
        print("\nStopped by user.")
    finally:
        if mavlink_bridge:
            mavlink_bridge.close()
        if broadcaster:
            broadcaster.close()


if __name__ == "__main__":
    main()
