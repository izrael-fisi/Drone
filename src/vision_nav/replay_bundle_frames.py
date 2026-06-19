from __future__ import annotations

import argparse
import glob
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from vision_nav.match_frame_to_map import match_frame_to_map
from vision_nav.run_bundle_match_loop import ensure_feature_index, status_line


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Replay saved frames against a map bundle.")
    parser.add_argument("--bundle", required=True, help="Bundle directory or manifest.json path.")
    parser.add_argument("--frames", nargs="+", required=True, help="Frame paths or glob patterns.")
    parser.add_argument("--output-dir", required=True, help="Output folder for logs and debug images.")
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
    return parser.parse_args()


def expand_frames(patterns: list[str]) -> list[Path]:
    frame_paths: list[Path] = []
    for pattern in patterns:
        pattern = str(Path(pattern).expanduser())
        matches = glob.glob(pattern)
        if matches:
            frame_paths.extend(Path(path) for path in matches)
        else:
            path = Path(pattern)
            if path.exists():
                frame_paths.append(path)
    return sorted(set(frame_paths))


def utc_stamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S.%fZ")


def main() -> None:
    args = parse_args()
    _, map_path, features_path = ensure_feature_index(args.bundle, args.build_if_missing)
    frame_paths = expand_frames(args.frames)
    if not frame_paths:
        raise SystemExit(f"No frames matched: {args.frames}")

    output_dir = Path(args.output_dir)
    viz_dir = output_dir / "viz"
    output_dir.mkdir(parents=True, exist_ok=True)
    if args.viz_every > 0:
        viz_dir.mkdir(parents=True, exist_ok=True)

    log_path = output_dir / "replay_matches.jsonl"
    print(f"Replaying {len(frame_paths)} frame(s)")
    print(f"Writing replay log: {log_path}")

    with log_path.open("a", encoding="utf-8") as log_file:
        for sequence, frame_path in enumerate(frame_paths, start=1):
            stamp = utc_stamp()
            viz_path = None
            if args.viz_every > 0 and sequence % args.viz_every == 0:
                viz_path = viz_dir / f"replay_match_{sequence:06d}_{stamp}.jpg"

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

            record = {
                "sequence": sequence,
                "timestamp_utc": stamp,
                "frame_path": str(frame_path),
                "viz_path": str(viz_path) if viz_path else None,
                "match_duration_s": match_duration_s,
                "result": result,
            }
            log_file.write(json.dumps(record, sort_keys=True) + "\n")
            log_file.flush()
            print(status_line(sequence, result, 0.0, match_duration_s))


if __name__ == "__main__":
    main()
