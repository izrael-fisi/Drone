import cv2
import numpy as np

from vision_nav.features import extract_features
from vision_nav.matching import estimate_homography, geometry_rejection_reason, match_descriptors


def test_synthetic_feature_match_accepts_translated_scene():
    image = np.zeros((500, 500), dtype=np.uint8)
    cv2.rectangle(image, (80, 80), (160, 180), 255, 3)
    cv2.circle(image, (320, 140), 45, 255, 3)
    cv2.line(image, (70, 350), (420, 300), 255, 4)
    cv2.putText(image, "MAP", (180, 420), cv2.FONT_HERSHEY_SIMPLEX, 2.0, 255, 4)

    transform = np.float32([[1, 0, 20], [0, 1, -15]])
    frame = cv2.warpAffine(image, transform, (500, 500))

    map_features = extract_features(image, method="orb", max_features=1000)
    frame_features = extract_features(frame, method="orb", max_features=1000)
    matches = match_descriptors(
        map_descriptors=map_features.descriptors,
        frame_descriptors=frame_features.descriptors,
        method="orb",
        ratio=0.85,
    )
    result = estimate_homography(
        frame_keypoints_xy=frame_features.keypoints_xy,
        map_keypoints_xy=map_features.keypoints_xy,
        matches=matches,
        min_inliers=8,
    )

    assert result.status == "accepted"
    assert result.inliers >= 8
    assert result.confidence > 0
    assert result.geometry is not None
    assert 0.8 <= result.geometry["scale_mean"] <= 1.2


def test_geometry_rejection_reason_flags_unsafe_transform():
    geometry = {
        "scale_mean": 1.0,
        "scale_anisotropy": 1.0,
        "rotation_deg": 120.0,
        "perspective_norm": 0.0,
    }
    reason = geometry_rejection_reason(
        geometry,
        min_scale=0.2,
        max_scale=5.0,
        max_rotation_deg=90.0,
        max_scale_anisotropy=3.0,
        max_perspective_norm=0.01,
    )

    assert reason == "rotation_too_large"
