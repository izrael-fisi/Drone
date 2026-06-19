from __future__ import annotations

from dataclasses import asdict, dataclass

import cv2
import numpy as np

from vision_nav.features import FeatureMethod, descriptor_norm


@dataclass(frozen=True)
class MatchResult:
    status: str
    confidence: float
    total_matches: int
    inliers: int
    inlier_ratio: float
    reprojection_error_px: float | None
    homography: list[list[float]] | None
    geometry: dict[str, float] | None = None
    reason: str | None = None

    def to_dict(self) -> dict:
        return asdict(self)


def match_descriptors(
    map_descriptors: np.ndarray,
    frame_descriptors: np.ndarray,
    method: FeatureMethod,
    ratio: float = 0.75,
) -> list[cv2.DMatch]:
    if len(map_descriptors) == 0 or len(frame_descriptors) == 0:
        return []

    matcher = cv2.BFMatcher(descriptor_norm(method), crossCheck=False)
    knn = matcher.knnMatch(frame_descriptors, map_descriptors, k=2)

    good: list[cv2.DMatch] = []
    for pair in knn:
        if len(pair) != 2:
            continue
        best, second = pair
        if best.distance < ratio * second.distance:
            good.append(best)
    return good


def homography_geometry_metrics(homography: np.ndarray) -> dict[str, float]:
    homography = homography.astype(np.float64)
    if abs(float(homography[2, 2])) > 1e-12:
        homography = homography / float(homography[2, 2])
    affine = homography[:2, :2].astype(np.float64)
    scale_x = float(np.linalg.norm(affine[:, 0]))
    scale_y = float(np.linalg.norm(affine[:, 1]))
    scale_mean = float((scale_x + scale_y) / 2.0)
    scale_anisotropy = float(max(scale_x, scale_y) / max(min(scale_x, scale_y), 1e-9))
    rotation_rad = float(np.arctan2(affine[1, 0], affine[0, 0]))
    perspective = float(np.linalg.norm(homography[2, :2]))
    return {
        "scale_x": scale_x,
        "scale_y": scale_y,
        "scale_mean": scale_mean,
        "scale_anisotropy": scale_anisotropy,
        "rotation_deg": float(np.degrees(rotation_rad)),
        "perspective_norm": perspective,
    }


def geometry_rejection_reason(
    geometry: dict[str, float],
    *,
    min_scale: float,
    max_scale: float,
    max_rotation_deg: float,
    max_scale_anisotropy: float,
    max_perspective_norm: float,
) -> str | None:
    if geometry["scale_mean"] < min_scale:
        return "scale_too_small"
    if geometry["scale_mean"] > max_scale:
        return "scale_too_large"
    if geometry["scale_anisotropy"] > max_scale_anisotropy:
        return "scale_anisotropy_too_high"
    if abs(geometry["rotation_deg"]) > max_rotation_deg:
        return "rotation_too_large"
    if geometry["perspective_norm"] > max_perspective_norm:
        return "perspective_too_high"
    return None


def estimate_homography(
    frame_keypoints_xy: np.ndarray,
    map_keypoints_xy: np.ndarray,
    matches: list[cv2.DMatch],
    min_inliers: int = 18,
    ransac_reproj_threshold: float = 4.0,
    min_scale: float = 0.2,
    max_scale: float = 5.0,
    max_rotation_deg: float = 90.0,
    max_scale_anisotropy: float = 3.0,
    max_perspective_norm: float = 0.01,
) -> MatchResult:
    if len(matches) < 4:
        return MatchResult(
            status="failed",
            confidence=0.0,
            total_matches=len(matches),
            inliers=0,
            inlier_ratio=0.0,
            reprojection_error_px=None,
            homography=None,
            geometry=None,
            reason="not_enough_matches",
        )

    src = np.float32([frame_keypoints_xy[m.queryIdx] for m in matches]).reshape(-1, 1, 2)
    dst = np.float32([map_keypoints_xy[m.trainIdx] for m in matches]).reshape(-1, 1, 2)

    homography, mask = cv2.findHomography(
        src,
        dst,
        cv2.RANSAC,
        ransac_reproj_threshold,
    )

    if homography is None or mask is None:
        return MatchResult(
            status="failed",
            confidence=0.0,
            total_matches=len(matches),
            inliers=0,
            inlier_ratio=0.0,
            reprojection_error_px=None,
            homography=None,
            geometry=None,
            reason="homography_failed",
        )

    inlier_mask = mask.ravel().astype(bool)
    inliers = int(inlier_mask.sum())
    inlier_ratio = float(inliers / max(len(matches), 1))

    if inliers < min_inliers:
        return MatchResult(
            status="rejected",
            confidence=0.0,
            total_matches=len(matches),
            inliers=inliers,
            inlier_ratio=inlier_ratio,
            reprojection_error_px=None,
            homography=homography.tolist(),
            geometry=homography_geometry_metrics(homography),
            reason="not_enough_inliers",
        )

    src_inliers = src[inlier_mask]
    dst_inliers = dst[inlier_mask]
    projected = cv2.perspectiveTransform(src_inliers, homography)
    errors = np.linalg.norm(projected.reshape(-1, 2) - dst_inliers.reshape(-1, 2), axis=1)
    reprojection_error = float(np.median(errors))

    reprojection_score = max(0.0, 1.0 - (reprojection_error / 12.0))
    inlier_score = min(1.0, inliers / 80.0)
    ratio_score = min(1.0, inlier_ratio / 0.55)
    confidence = float(0.45 * inlier_score + 0.35 * ratio_score + 0.20 * reprojection_score)
    geometry = homography_geometry_metrics(homography)
    geometry_reason = geometry_rejection_reason(
        geometry,
        min_scale=min_scale,
        max_scale=max_scale,
        max_rotation_deg=max_rotation_deg,
        max_scale_anisotropy=max_scale_anisotropy,
        max_perspective_norm=max_perspective_norm,
    )

    if geometry_reason:
        return MatchResult(
            status="rejected",
            confidence=0.0,
            total_matches=len(matches),
            inliers=inliers,
            inlier_ratio=inlier_ratio,
            reprojection_error_px=reprojection_error,
            homography=homography.tolist(),
            geometry=geometry,
            reason=geometry_reason,
        )

    status = "accepted" if confidence >= 0.45 else "rejected"
    reason = None if status == "accepted" else "low_confidence"

    return MatchResult(
        status=status,
        confidence=confidence,
        total_matches=len(matches),
        inliers=inliers,
        inlier_ratio=inlier_ratio,
        reprojection_error_px=reprojection_error,
        homography=homography.tolist(),
        geometry=geometry,
        reason=reason,
    )
