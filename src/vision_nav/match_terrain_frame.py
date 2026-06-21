from __future__ import annotations

import argparse
import json

from vision_nav.terrain_bundle import load_terrain_bundle
from vision_nav.terrain_estimator import TerrainEstimator
from vision_nav.terrain_matcher import TerrainMatchOptions, match_terrain_frame


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Match a frame against a tiled terrain vision bundle.")
    parser.add_argument("--bundle", required=True, help="Bundle directory or manifest.json path.")
    parser.add_argument("--frame", required=True, help="Query/camera frame image path.")
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
    parser.add_argument("--max-candidates", type=int, default=64)
    parser.add_argument("--prior-east-m", type=float)
    parser.add_argument("--prior-north-m", type=float)
    parser.add_argument("--search-radius-m", type=float)
    parser.add_argument("--camera-calibration", help="Optional camera calibration YAML for frame undistortion.")
    parser.add_argument("--baro-altitude-m", type=float, help="Optional pressure altitude estimate in meters.")
    parser.add_argument("--baro-relative-m", type=float, help="Optional relative altitude estimate in local ENU meters.")
    return parser.parse_args()


def options_from_args(args: argparse.Namespace) -> TerrainMatchOptions:
    return TerrainMatchOptions(
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
        max_candidates=args.max_candidates,
        prior_east_m=args.prior_east_m,
        prior_north_m=args.prior_north_m,
        search_radius_m=args.search_radius_m,
        camera_calibration=args.camera_calibration,
    )


def main() -> None:
    args = parse_args()
    bundle = load_terrain_bundle(args.bundle)
    result = match_terrain_frame(bundle, args.frame, options_from_args(args))
    if args.baro_altitude_m is not None or args.baro_relative_m is not None:
        result = TerrainEstimator().update_from_match(
            result,
            barometer_sample={
                "altitude_m": args.baro_altitude_m,
                "relative_altitude_m": args.baro_relative_m,
                "source": "cli",
            },
        )
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
