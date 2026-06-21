from __future__ import annotations

import argparse
import json
import time
from pathlib import Path

from vision_nav.terrain_bundle import load_terrain_bundle
from vision_nav.terrain_estimator import TerrainEstimator
from vision_nav.terrain_matcher import TerrainMatchOptions, match_terrain_frame


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay frame records through the tiled terrain matcher.")
    parser.add_argument("--bundle", required=True, help="Bundle directory or manifest.json path.")
    parser.add_argument("--log", required=True, help="JSONL replay log with frame_path entries.")
    parser.add_argument("--output-dir", help="Output folder. Defaults next to the input log.")
    parser.add_argument("--method", choices=["orb", "akaze", "sift"], help="Override feature method.")
    parser.add_argument("--max-features", type=int, default=3000)
    parser.add_argument("--ratio", type=float, default=0.75)
    parser.add_argument("--min-inliers", type=int, default=18)
    parser.add_argument("--ransac-threshold", type=float, default=4.0)
    parser.add_argument("--max-candidates", type=int, default=64)
    parser.add_argument("--search-radius-m", type=float, default=80.0)
    parser.add_argument("--camera-calibration", help="Optional camera calibration YAML for frame undistortion.")
    return parser.parse_args()


def frame_path_from_record(record: dict) -> str | None:
    for key in ("frame_path", "frame", "image_path", "path"):
        value = record.get(key)
        if value:
            return str(value)
    nested = record.get("capture") or {}
    return str(nested["frame_path"]) if nested.get("frame_path") else None


def barometer_from_record(record: dict) -> dict | None:
    for key in ("barometer", "baro"):
        value = record.get(key)
        if isinstance(value, dict):
            return value
    telemetry = record.get("telemetry")
    if isinstance(telemetry, list):
        for sample in telemetry:
            if not isinstance(sample, dict):
                continue
            if sample.get("pressure_altitude_m") is not None or sample.get("relative_altitude_m") is not None:
                return {
                    "timestamp_us": sample.get("timestamp_us"),
                    "altitude_m": sample.get("pressure_altitude_m"),
                    "relative_altitude_m": sample.get("relative_altitude_m"),
                    "pressure_hpa": sample.get("pressure_hpa"),
                    "source": f"replay:{sample.get('message_type', 'telemetry')}",
                }
    return None


def main() -> None:
    args = parse_args()
    bundle = load_terrain_bundle(args.bundle)
    estimator = TerrainEstimator()
    input_log = Path(args.log)
    output_dir = Path(args.output_dir) if args.output_dir else input_log.parent
    output_dir.mkdir(parents=True, exist_ok=True)
    output_log = output_dir / "terrain_replay_matches.jsonl"

    records = [json.loads(line) for line in input_log.read_text().splitlines() if line.strip()]
    print(f"Replaying {len(records)} record(s)")
    print(f"Writing terrain replay log: {output_log}")

    with output_log.open("a", encoding="utf-8") as out:
        for sequence, record in enumerate(records, start=1):
            frame_path = frame_path_from_record(record)
            if not frame_path:
                result = {"status": "rejected", "reason": "missing_frame_path", "sequence": sequence}
            else:
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
                start = time.monotonic()
                result = match_terrain_frame(bundle, frame_path, options)
                result = estimator.update_from_match(result, barometer_sample=barometer_from_record(record))
                result["match_duration_s"] = time.monotonic() - start
            out.write(json.dumps({"sequence": sequence, "input": record, "result": result}, sort_keys=True) + "\n")
            out.flush()
            print(
                f"[{sequence:06d}] {result.get('status')} tile={result.get('tile_id')} "
                f"conf={result.get('confidence', 0.0):.3f} reason={result.get('reason') or ''}".rstrip()
            )


if __name__ == "__main__":
    main()
