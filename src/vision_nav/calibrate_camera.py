from __future__ import annotations

import argparse
import glob
from pathlib import Path

import cv2
import numpy as np
import yaml


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Calibrate a camera from chessboard images.")
    parser.add_argument("--images", required=True, help="Glob pattern for calibration images.")
    parser.add_argument("--output", required=True, help="Output YAML calibration file.")
    parser.add_argument("--camera-name", default="down_global_shutter")
    parser.add_argument("--cols", type=int, required=True, help="Interior chessboard corners per row.")
    parser.add_argument("--rows", type=int, required=True, help="Interior chessboard corners per column.")
    parser.add_argument("--square-size-m", type=float, required=True, help="Chessboard square size in meters.")
    parser.add_argument("--show-rejections", action="store_true")
    return parser.parse_args()


def make_object_points(cols: int, rows: int, square_size_m: float) -> np.ndarray:
    objp = np.zeros((rows * cols, 3), np.float32)
    objp[:, :2] = np.mgrid[0:cols, 0:rows].T.reshape(-1, 2)
    objp *= square_size_m
    return objp


def calibration_yaml(
    camera_name: str,
    image_size: tuple[int, int],
    camera_matrix: np.ndarray,
    dist_coeffs: np.ndarray,
    reprojection_error: float,
    image_count: int,
) -> dict:
    width, height = image_size
    coeffs = dist_coeffs.reshape(-1).astype(float).tolist()
    return {
        "camera_name": camera_name,
        "image_width": int(width),
        "image_height": int(height),
        "camera_model": "pinhole",
        "distortion_model": "plumb_bob",
        "camera_matrix": {
            "rows": 3,
            "cols": 3,
            "data": camera_matrix.reshape(-1).astype(float).tolist(),
        },
        "distortion_coefficients": {
            "rows": 1,
            "cols": len(coeffs),
            "data": coeffs,
        },
        "calibration": {
            "reprojection_error_px": float(reprojection_error),
            "accepted_images": int(image_count),
        },
    }


def main() -> None:
    args = parse_args()
    image_paths = sorted(glob.glob(args.images))
    if not image_paths:
        raise SystemExit(f"No images matched: {args.images}")

    object_template = make_object_points(args.cols, args.rows, args.square_size_m)
    object_points: list[np.ndarray] = []
    image_points: list[np.ndarray] = []
    image_size: tuple[int, int] | None = None

    criteria = (
        cv2.TERM_CRITERIA_EPS + cv2.TERM_CRITERIA_MAX_ITER,
        30,
        0.001,
    )

    for path in image_paths:
        image = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
        if image is None:
            if args.show_rejections:
                print(f"[reject] unreadable: {path}")
            continue

        if image_size is None:
            image_size = (image.shape[1], image.shape[0])
        elif image_size != (image.shape[1], image.shape[0]):
            if args.show_rejections:
                print(f"[reject] size mismatch: {path}")
            continue

        found, corners = cv2.findChessboardCorners(image, (args.cols, args.rows), None)
        if not found:
            if args.show_rejections:
                print(f"[reject] no chessboard: {path}")
            continue

        refined = cv2.cornerSubPix(image, corners, (11, 11), (-1, -1), criteria)
        object_points.append(object_template)
        image_points.append(refined)
        print(f"[accept] {path}")

    if image_size is None or len(object_points) < 5:
        raise SystemExit(
            "Need at least 5 accepted calibration images. "
            f"Accepted {len(object_points)} from {len(image_paths)}."
        )

    reprojection_error, camera_matrix, dist_coeffs, _, _ = cv2.calibrateCamera(
        object_points,
        image_points,
        image_size,
        None,
        None,
    )

    output = calibration_yaml(
        camera_name=args.camera_name,
        image_size=image_size,
        camera_matrix=camera_matrix,
        dist_coeffs=dist_coeffs,
        reprojection_error=reprojection_error,
        image_count=len(object_points),
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(yaml.safe_dump(output, sort_keys=False))
    print(f"Wrote calibration: {output_path}")


if __name__ == "__main__":
    main()

