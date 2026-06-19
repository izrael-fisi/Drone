from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import cv2
import numpy as np

from vision_nav.georef import SimpleGeoReference, georef_from_json, georef_to_json

FeatureMethod = Literal["orb", "akaze", "sift"]


@dataclass(frozen=True)
class ExtractedFeatures:
    keypoints_xy: np.ndarray
    descriptors: np.ndarray
    method: FeatureMethod


def load_gray_image(path: str) -> np.ndarray:
    image = cv2.imread(path, cv2.IMREAD_GRAYSCALE)
    if image is None:
        raise FileNotFoundError(f"Could not read image: {path}")
    return image


def create_detector(method: FeatureMethod, max_features: int):
    if method == "orb":
        return cv2.ORB_create(nfeatures=max_features, fastThreshold=12)
    if method == "akaze":
        return cv2.AKAZE_create()
    if method == "sift":
        return cv2.SIFT_create(nfeatures=max_features)
    raise ValueError(f"Unsupported feature method: {method}")


def descriptor_norm(method: FeatureMethod) -> int:
    if method in {"orb", "akaze"}:
        return cv2.NORM_HAMMING
    if method == "sift":
        return cv2.NORM_L2
    raise ValueError(f"Unsupported feature method: {method}")


def preprocess_gray(image: np.ndarray) -> np.ndarray:
    if image.ndim != 2:
        raise ValueError("Expected a grayscale image")
    clahe = cv2.createCLAHE(clipLimit=2.0, tileGridSize=(8, 8))
    return clahe.apply(image)


def extract_features(
    image: np.ndarray,
    method: FeatureMethod = "orb",
    max_features: int = 3000,
) -> ExtractedFeatures:
    processed = preprocess_gray(image)
    detector = create_detector(method, max_features)
    keypoints, descriptors = detector.detectAndCompute(processed, None)

    if not keypoints or descriptors is None:
        return ExtractedFeatures(
            keypoints_xy=np.empty((0, 2), dtype=np.float32),
            descriptors=np.empty((0, 32), dtype=np.uint8),
            method=method,
        )

    keypoints_xy = np.array([kp.pt for kp in keypoints], dtype=np.float32)
    return ExtractedFeatures(
        keypoints_xy=keypoints_xy,
        descriptors=descriptors,
        method=method,
    )


def save_feature_index(
    output_path: str,
    image_path: str,
    image_shape: tuple[int, int],
    features: ExtractedFeatures,
    georef: SimpleGeoReference | None = None,
) -> None:
    np.savez_compressed(
        output_path,
        image_path=np.array(image_path),
        image_shape=np.array(image_shape, dtype=np.int32),
        method=np.array(features.method),
        keypoints_xy=features.keypoints_xy,
        descriptors=features.descriptors,
        georef_json=np.array(georef_to_json(georef)),
    )


def load_feature_index(path: str) -> dict:
    with np.load(path, allow_pickle=False) as data:
        georef_json = str(data["georef_json"]) if "georef_json" in data.files else ""
        return {
            "image_path": str(data["image_path"]),
            "image_shape": tuple(int(v) for v in data["image_shape"]),
            "method": str(data["method"]),
            "keypoints_xy": data["keypoints_xy"].astype(np.float32),
            "descriptors": data["descriptors"],
            "georef": georef_from_json(georef_json),
        }
