from __future__ import annotations

import argparse
import json

from vision_nav.bundle import load_manifest, manifest_features_path, manifest_orthophoto_path
from vision_nav.match_frame_to_map import match_frame_to_map


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Match a frame against a built map bundle.")
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
    parser.add_argument("--camera-calibration", help="Optional camera calibration YAML for frame undistortion.")
    parser.add_argument("--viz", help="Optional output path for match visualization.")
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    bundle_dir, manifest = load_manifest(args.bundle)

    match_args = argparse.Namespace(
        map_image=str(manifest_orthophoto_path(bundle_dir, manifest)),
        features=str(manifest_features_path(bundle_dir, manifest)),
        frame=args.frame,
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
        viz=args.viz,
    )
    print(json.dumps(match_frame_to_map(match_args), indent=2))


if __name__ == "__main__":
    main()
