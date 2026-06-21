from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from vision_nav.camera import load_camera_calibration, validate_image_size
from vision_nav.features import extract_features, load_feature_index, load_gray_image
from vision_nav.matching import estimate_homography, match_descriptors
from vision_nav.quality import (
    estimate_position_covariance_m2,
    estimate_visual_position_confidence,
    feature_density,
    frame_quality_metrics,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Match a camera frame to a feature-indexed map image.")
    parser.add_argument("--map-image", required=True, help="Map image path for optional visualization.")
    parser.add_argument("--features", required=True, help="Map feature index .npz path.")
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


def draw_visualization(
    frame: np.ndarray,
    map_path: str,
    frame_features,
    map_keypoints_xy,
    matches,
    output_path: str,
) -> None:
    map_image = cv2.imread(map_path, cv2.IMREAD_GRAYSCALE)
    frame_kps = [cv2.KeyPoint(float(x), float(y), 1) for x, y in frame_features.keypoints_xy]
    map_kps = [cv2.KeyPoint(float(x), float(y), 1) for x, y in map_keypoints_xy]
    vis = cv2.drawMatches(
        frame,
        frame_kps,
        map_image,
        map_kps,
        matches[:80],
        None,
        flags=cv2.DrawMatchesFlags_NOT_DRAW_SINGLE_POINTS,
    )
    cv2.imwrite(output_path, vis)


def load_frame_for_matching(frame_path: str, camera_calibration: str | None) -> tuple[np.ndarray, dict | None]:
    frame = load_gray_image(frame_path)
    if not camera_calibration:
        return frame, None

    calibration = load_camera_calibration(Path(camera_calibration))
    validate_image_size(frame, calibration)
    undistorted = cv2.undistort(frame, calibration.camera_matrix, calibration.distortion_coefficients)
    return undistorted, {
        **calibration.to_log_dict(),
        "undistorted": True,
    }


def match_frame_to_map(args: argparse.Namespace) -> dict:
    feature_index = load_feature_index(args.features)
    method = args.method or feature_index["method"]

    frame, calibration_log = load_frame_for_matching(args.frame, getattr(args, "camera_calibration", None))
    quality = frame_quality_metrics(frame)
    frame_features = extract_features(frame, method=method, max_features=args.max_features)

    matches = match_descriptors(
        map_descriptors=feature_index["descriptors"],
        frame_descriptors=frame_features.descriptors,
        method=method,
        ratio=args.ratio,
    )
    result = estimate_homography(
        frame_keypoints_xy=frame_features.keypoints_xy,
        map_keypoints_xy=feature_index["keypoints_xy"],
        matches=matches,
        min_inliers=args.min_inliers,
        ransac_reproj_threshold=args.ransac_threshold,
        min_scale=getattr(args, "min_scale", 0.2),
        max_scale=getattr(args, "max_scale", 5.0),
        max_rotation_deg=getattr(args, "max_rotation_deg", 90.0),
        max_scale_anisotropy=getattr(args, "max_scale_anisotropy", 3.0),
        max_perspective_norm=getattr(args, "max_perspective_norm", 0.01),
    )

    if args.viz:
        draw_visualization(
            frame=frame,
            map_path=args.map_image,
            frame_features=frame_features,
            map_keypoints_xy=feature_index["keypoints_xy"],
            matches=matches,
            output_path=args.viz,
        )

    output = result.to_dict()
    output["method"] = method
    output["frame_keypoints"] = int(frame_features.keypoints_xy.shape[0])
    output["map_keypoints"] = int(feature_index["keypoints_xy"].shape[0])
    output["frame_quality"] = {
        **quality,
        "feature_density_per_megapixel": feature_density(int(frame_features.keypoints_xy.shape[0]), frame.shape[:2]),
    }
    if calibration_log is not None:
        output["camera_calibration"] = calibration_log

    if result.homography:
        height, width = frame.shape[:2]
        frame_center = np.float32([[[width / 2.0, height / 2.0]]])
        homography = np.array(result.homography, dtype=np.float64)
        map_center = cv2.perspectiveTransform(frame_center, homography)[0][0]
        output["estimated_map_pixel"] = {
            "x": float(map_center[0]),
            "y": float(map_center[1]),
        }

        georef = feature_index.get("georef")
        if georef is not None:
            east_m, north_m = georef.pixel_to_local_m(float(map_center[0]), float(map_center[1]))
            lat, lon = georef.pixel_to_latlon(float(map_center[0]), float(map_center[1]))
            position_confidence = estimate_visual_position_confidence(
                match_confidence=output["confidence"],
                georef_confidence=georef.confidence,
            )
            output["map_georef"] = {
                "source": georef.source,
                "confidence": georef.confidence,
                "crs": georef.crs,
                "gsd_m": georef.gsd_m,
                "origin_lat": georef.origin_lat,
                "origin_lon": georef.origin_lon,
                "origin_pixel_x": georef.origin_pixel_x,
                "origin_pixel_y": georef.origin_pixel_y,
                "rotation_deg": georef.rotation_deg,
            }
            output["position_confidence"] = position_confidence
            output["confidence_model"] = "match_confidence_times_georef_confidence"
            output["estimated_position"] = {
                "latitude": lat,
                "longitude": lon,
                "east_m": east_m,
                "north_m": north_m,
                "source": "homography_center_georef",
            }
            output["measurement"] = {
                "frame": "local_enu",
                "x_m": east_m,
                "y_m": north_m,
                "z_m": None,
                "yaw_rad": None,
                "confidence": position_confidence,
                "covariance": estimate_position_covariance_m2(
                    confidence=position_confidence,
                    reprojection_error_px=output["reprojection_error_px"],
                    gsd_m=georef.gsd_m,
                ),
                "source": "vision_map_match",
            }
    return output


def main() -> None:
    args = parse_args()
    print(json.dumps(match_frame_to_map(args), indent=2))


if __name__ == "__main__":
    main()
