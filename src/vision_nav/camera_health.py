from __future__ import annotations

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from vision_nav.capture_frame import capture_frame
from vision_nav.features import extract_features, load_gray_image
from vision_nav.quality import feature_density, frame_quality_metrics


DEFAULT_SHARPNESS_MIN = 60.0
DEFAULT_ENTROPY_MIN = 3.5
DEFAULT_FEATURE_DENSITY_MIN = 300.0
DEFAULT_SATURATION_MAX = 0.15


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Capture and score Raspberry Pi camera image quality.")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--capture", action="store_true", help="Capture a fresh frame with rpicam/libcamera tools.")
    source.add_argument("--image", help="Analyze an existing image instead of capturing.")
    parser.add_argument("--output-dir", required=True, help="Directory for report and captured frame.")
    parser.add_argument("--width", type=int, default=1456)
    parser.add_argument("--height", type=int, default=1088)
    parser.add_argument("--timeout-ms", type=int, default=1000)
    parser.add_argument("--method", choices=["orb", "akaze", "sift"], default="orb")
    parser.add_argument("--max-features", type=int, default=3000)
    parser.add_argument("--sharpness-min", type=float, default=DEFAULT_SHARPNESS_MIN)
    parser.add_argument("--entropy-min", type=float, default=DEFAULT_ENTROPY_MIN)
    parser.add_argument("--feature-density-min", type=float, default=DEFAULT_FEATURE_DENSITY_MIN)
    parser.add_argument("--saturation-max", type=float, default=DEFAULT_SATURATION_MAX)
    parser.add_argument("--fail-on-warning", action="store_true")
    return parser.parse_args()


def exposure_metrics(gray_image: np.ndarray) -> dict[str, float]:
    pixel_count = max(int(gray_image.size), 1)
    dark_pixels = int(np.count_nonzero(gray_image <= 5))
    bright_pixels = int(np.count_nonzero(gray_image >= 250))
    return {
        "mean": float(gray_image.mean()),
        "stddev": float(gray_image.std()),
        "min": float(gray_image.min()),
        "max": float(gray_image.max()),
        "dark_pixel_fraction": float(dark_pixels / pixel_count),
        "bright_pixel_fraction": float(bright_pixels / pixel_count),
    }


def warning_messages(
    *,
    quality: dict[str, float],
    exposure: dict[str, float],
    density_per_megapixel: float,
    sharpness_min: float,
    entropy_min: float,
    feature_density_min: float,
    saturation_max: float,
) -> list[str]:
    warnings: list[str] = []
    if quality["sharpness_laplacian_var"] < sharpness_min:
        warnings.append("low_sharpness")
    if quality["entropy_bits"] < entropy_min:
        warnings.append("low_texture_entropy")
    if density_per_megapixel < feature_density_min:
        warnings.append("low_feature_density")
    if exposure["dark_pixel_fraction"] > saturation_max:
        warnings.append("underexposed_or_lens_covered")
    if exposure["bright_pixel_fraction"] > saturation_max:
        warnings.append("overexposed_or_saturated")
    return warnings


def analyze_frame_file(
    image_path: Path,
    *,
    method: str = "orb",
    max_features: int = 3000,
    sharpness_min: float = DEFAULT_SHARPNESS_MIN,
    entropy_min: float = DEFAULT_ENTROPY_MIN,
    feature_density_min: float = DEFAULT_FEATURE_DENSITY_MIN,
    saturation_max: float = DEFAULT_SATURATION_MAX,
) -> dict[str, object]:
    gray = load_gray_image(str(image_path))
    height, width = gray.shape[:2]
    quality = frame_quality_metrics(gray)
    exposure = exposure_metrics(gray)
    features = extract_features(gray, method=method, max_features=max_features)
    density = feature_density(int(features.keypoints_xy.shape[0]), gray.shape[:2])
    warnings = warning_messages(
        quality=quality,
        exposure=exposure,
        density_per_megapixel=density,
        sharpness_min=sharpness_min,
        entropy_min=entropy_min,
        feature_density_min=feature_density_min,
        saturation_max=saturation_max,
    )

    return {
        "status": "warning" if warnings else "passed",
        "image_path": str(image_path),
        "resolution": {"width": int(width), "height": int(height)},
        "feature_method": method,
        "feature_count": int(features.keypoints_xy.shape[0]),
        "feature_density_per_megapixel": density,
        "frame_quality": quality,
        "exposure": exposure,
        "thresholds": {
            "sharpness_min": sharpness_min,
            "entropy_min": entropy_min,
            "feature_density_min": feature_density_min,
            "saturation_max": saturation_max,
        },
        "warnings": warnings,
    }


def write_report(report: dict[str, object], output_dir: Path) -> Path:
    report_path = output_dir / "camera_health_report.json"
    report_path.write_text(json.dumps(report, indent=2) + "\n")
    return report_path


def main() -> None:
    args = parse_args()
    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.capture:
        image_path = capture_frame(
            output_dir / "global_shutter_health_capture.jpg",
            width=args.width,
            height=args.height,
            timeout_ms=args.timeout_ms,
        )
    else:
        image_path = Path(args.image)

    report = analyze_frame_file(
        image_path,
        method=args.method,
        max_features=args.max_features,
        sharpness_min=args.sharpness_min,
        entropy_min=args.entropy_min,
        feature_density_min=args.feature_density_min,
        saturation_max=args.saturation_max,
    )
    report["report_path"] = str(output_dir / "camera_health_report.json")
    write_report(report, output_dir)
    print(json.dumps(report, indent=2))

    if args.fail_on_warning and report["warnings"]:
        raise SystemExit(2)


if __name__ == "__main__":
    main()
