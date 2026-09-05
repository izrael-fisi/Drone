from __future__ import annotations

import argparse
import glob
import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
import yaml


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Calibrate a camera from chessboard images.")
    parser.add_argument("--images", required=True, help="Glob pattern for calibration images.")
    parser.add_argument("--output", required=True, help="Output YAML calibration file.")
    parser.add_argument("--calibration-id", required=True, help="Immutable ID for this measured camera/lens/resolution calibration.")
    parser.add_argument("--camera-name", default="down_global_shutter")
    parser.add_argument("--cols", type=int, required=True, help="Interior chessboard corners per row.")
    parser.add_argument("--rows", type=int, required=True, help="Interior chessboard corners per column.")
    parser.add_argument("--square-size-m", type=float, required=True, help="Chessboard square size in meters.")
    parser.add_argument("--min-accepted-images", type=int, default=12)
    parser.add_argument("--max-reprojection-error-px", type=float, default=0.8)
    parser.add_argument("--show-rejections", action="store_true")
    parser.add_argument("--capture-manifest", help="Optional calibration capture_manifest.json for source provenance.")
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
    *,
    calibration_id: str,
    board_cols: int,
    board_rows: int,
    square_size_m: float,
    accepted_images: list[dict[str, object]],
    per_view_error_px: list[float],
    capture_manifest: dict[str, object] | None,
    capture_manifest_sha256: str | None,
    acceptance_thresholds: dict[str, float | int] | None = None,
) -> dict:
    width, height = image_size
    coeffs = dist_coeffs.reshape(-1).astype(float).tolist()
    return {
        "calibration_id": calibration_id,
        "calibration_status": "measured",
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
            "board": {"cols": board_cols, "rows": board_rows, "square_size_m": square_size_m},
            "per_view_reprojection_error_px": per_view_error_px,
            "source_images": accepted_images,
            "capture_manifest_path": capture_manifest.get("path") if capture_manifest else None,
            "capture_manifest_sha256": capture_manifest_sha256,
            "acceptance_thresholds": acceptance_thresholds or {},
        },
    }


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _load_capture_manifest(path: Path | None) -> tuple[dict[str, object] | None, str | None]:
    if path is None or not path.is_file():
        return None, None
    raw = json.loads(path.read_text(encoding="utf-8"))
    raw["path"] = str(path.resolve())
    return raw, sha256_file(path)


def per_view_reprojection_errors(
    object_points: list[np.ndarray],
    image_points: list[np.ndarray],
    rvecs: list[np.ndarray],
    tvecs: list[np.ndarray],
    camera_matrix: np.ndarray,
    dist_coeffs: np.ndarray,
) -> list[float]:
    errors: list[float] = []
    for object_point, image_point, rvec, tvec in zip(object_points, image_points, rvecs, tvecs):
        projected, _ = cv2.projectPoints(object_point, rvec, tvec, camera_matrix, dist_coeffs)
        errors.append(float(cv2.norm(image_point, projected, cv2.NORM_L2) / len(projected)))
    return errors


def main() -> None:
    args = parse_args()
    image_paths = sorted(glob.glob(args.images))
    if not image_paths:
        raise SystemExit(f"No images matched: {args.images}")

    object_template = make_object_points(args.cols, args.rows, args.square_size_m)
    object_points: list[np.ndarray] = []
    image_points: list[np.ndarray] = []
    accepted_paths: list[Path] = []
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
        accepted_paths.append(Path(path))
        print(f"[accept] {path}")

    if image_size is None or len(object_points) < args.min_accepted_images:
        raise SystemExit(
            f"Need at least {args.min_accepted_images} accepted calibration images. "
            f"Accepted {len(object_points)} from {len(image_paths)}."
        )

    reprojection_error, camera_matrix, dist_coeffs, rvecs, tvecs = cv2.calibrateCamera(
        object_points,
        image_points,
        image_size,
        None,
        None,
    )
    if reprojection_error > args.max_reprojection_error_px:
        raise SystemExit(
            "Calibration reprojection error exceeds the measured-calibration threshold: "
            f"{reprojection_error:.4f}px > {args.max_reprojection_error_px:.4f}px. "
            "Recapture with sharper, more diverse chessboard views."
        )

    manifest_path = Path(args.capture_manifest) if args.capture_manifest else Path(image_paths[0]).parent / "capture_manifest.json"
    capture_manifest, capture_manifest_digest = _load_capture_manifest(manifest_path)
    accepted_images = [{"path": str(path.resolve()), "sha256": sha256_file(path)} for path in accepted_paths]
    output = calibration_yaml(
        camera_name=args.camera_name,
        image_size=image_size,
        camera_matrix=camera_matrix,
        dist_coeffs=dist_coeffs,
        reprojection_error=reprojection_error,
        image_count=len(object_points),
        calibration_id=args.calibration_id,
        board_cols=args.cols,
        board_rows=args.rows,
        square_size_m=args.square_size_m,
        accepted_images=accepted_images,
        per_view_error_px=per_view_reprojection_errors(
            object_points, image_points, rvecs, tvecs, camera_matrix, dist_coeffs
        ),
        capture_manifest=capture_manifest,
        capture_manifest_sha256=capture_manifest_digest,
        acceptance_thresholds={
            "min_accepted_images": args.min_accepted_images,
            "max_reprojection_error_px": args.max_reprojection_error_px,
        },
    )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(yaml.safe_dump(output, sort_keys=False), encoding="utf-8")
    print(json.dumps({
        "status": "measured",
        "calibration_id": args.calibration_id,
        "output": str(output_path),
        "accepted_images": len(object_points),
        "reprojection_error_px": reprojection_error,
    }, indent=2))


if __name__ == "__main__":
    main()
