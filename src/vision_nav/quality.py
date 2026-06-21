from __future__ import annotations

import cv2
import numpy as np


def frame_quality_metrics(gray_image: np.ndarray) -> dict[str, float]:
    if gray_image.ndim != 2:
        raise ValueError("Expected a grayscale image")

    laplacian = cv2.Laplacian(gray_image, cv2.CV_64F)
    sharpness = float(laplacian.var())
    contrast = float(gray_image.std())

    # Entropy approximates usable texture for matching. A very low value often
    # means blank ground, overexposure, water, snow, or strong blur.
    hist = cv2.calcHist([gray_image], [0], None, [256], [0, 256]).ravel()
    probabilities = hist / max(float(hist.sum()), 1.0)
    probabilities = probabilities[probabilities > 0]
    entropy = float(-(probabilities * np.log2(probabilities)).sum())

    return {
        "sharpness_laplacian_var": sharpness,
        "contrast_stddev": contrast,
        "entropy_bits": entropy,
    }


def feature_density(feature_count: int, image_shape: tuple[int, int]) -> float:
    height, width = image_shape[:2]
    megapixels = max((width * height) / 1_000_000.0, 1e-9)
    return float(feature_count / megapixels)


def estimate_visual_position_confidence(match_confidence: float, georef_confidence: float | None) -> float:
    """Combine visual match quality with map georeference quality.

    The matcher confidence answers "did this frame match this map image?".
    The georef confidence answers "is this map image anchored to Earth well?".
    Flight-control consumers need both, so the position confidence is the more
    conservative product used for measurement covariance.
    """

    match = min(max(float(match_confidence), 0.0), 1.0)
    georef = 1.0 if georef_confidence is None else min(max(float(georef_confidence), 0.0), 1.0)
    return float(match * georef)


def estimate_position_covariance_m2(
    confidence: float,
    reprojection_error_px: float | None,
    gsd_m: float,
) -> dict[str, object]:
    residual_px = max(float(reprojection_error_px or 0.0), 1.0)
    confidence_scale = max(1.0 - float(confidence), 0.05)
    sigma_m = max(residual_px * float(gsd_m) * (1.0 + 4.0 * confidence_scale), float(gsd_m))
    variance_m2 = sigma_m * sigma_m
    return {
        "frame": "local_enu",
        "x_m2": variance_m2,
        "y_m2": variance_m2,
        "z_m2": None,
        "yaw_rad2": None,
        "model": "reprojection_gsd_confidence",
        "sigma_xy_m": sigma_m,
    }
