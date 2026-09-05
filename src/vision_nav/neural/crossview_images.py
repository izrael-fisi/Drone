"""Shared, versioned image preprocessing for learned training and shadow replay."""

from pathlib import Path
from typing import Any

import cv2
import numpy as np

from vision_nav.camera import validate_image_size


PREPROCESSING_ID = "rectified_rgb_linear_resize_zero_one_v1"


def image_tensor(path: str | Path, image_size: int, *, calibration: Any | None = None) -> np.ndarray:
    value = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if value is None:
        raise ValueError(f"Cross-view image cannot be decoded: {path}")
    return pixels_tensor(value, image_size, calibration=calibration)


def pixels_tensor(value: np.ndarray, image_size: int, *, calibration: Any | None = None) -> np.ndarray:
    if type(image_size) is not int or image_size <= 0:
        raise ValueError("Model image size must be a positive integer")
    if not isinstance(value, np.ndarray) or value.dtype != np.uint8 or value.ndim not in (2, 3):
        raise ValueError("Cross-view pixels must be uint8 gray or BGR")
    if value.ndim == 2:
        value = cv2.cvtColor(value, cv2.COLOR_GRAY2BGR)
    if value.shape[2] != 3:
        raise ValueError("Cross-view pixels must have three BGR channels")
    if calibration is not None:
        validate_image_size(value, calibration)
        value = cv2.undistort(value, calibration.camera_matrix, calibration.distortion_coefficients)
    value = cv2.cvtColor(cv2.resize(value, (image_size, image_size), interpolation=cv2.INTER_LINEAR), cv2.COLOR_BGR2RGB)
    return np.ascontiguousarray(value.transpose(2, 0, 1), dtype=np.float32) / np.float32(255)
