from __future__ import annotations

from dataclasses import dataclass
import time
from typing import Any

import cv2
import numpy as np

from vision_nav.features import FeatureMethod, extract_features
from vision_nav.match_frame_to_map import load_frame_for_matching, match_frame_to_map
from vision_nav.matching import MatchResult, estimate_homography, match_descriptors
from vision_nav.quality import (
    estimate_position_covariance_m2,
    estimate_visual_position_confidence,
    feature_density,
    frame_quality_metrics,
)
from vision_nav.terrain_bundle import TerrainBundle
from vision_nav.terrain_tiles import (
    TerrainTile,
    global_descriptor_distance,
    image_global_descriptor,
    load_tile_descriptor,
    query_tiles_with_metadata,
)


@dataclass(frozen=True)
class TerrainMatchOptions:
    method: FeatureMethod | None = None
    max_features: int = 3000
    ratio: float = 0.75
    min_inliers: int = 18
    ransac_threshold: float = 4.0
    min_scale: float = 0.2
    max_scale: float = 5.0
    max_rotation_deg: float = 90.0
    max_scale_anisotropy: float = 3.0
    max_perspective_norm: float = 0.01
    max_candidates: int = 64
    global_retrieval_multiplier: int = 4
    prior_east_m: float | None = None
    prior_north_m: float | None = None
    search_radius_m: float | None = None
    camera_calibration: str | None = None


def estimate_scale_confidence(result: MatchResult) -> float:
    if result.status != "accepted" or result.geometry is None:
        return 0.0
    geometry = result.geometry
    scale = float(geometry.get("scale_mean", 0.0))
    anisotropy = float(geometry.get("scale_anisotropy", 99.0))
    perspective = float(geometry.get("perspective_norm", 1.0))
    scale_score = 1.0 - min(abs(np.log(max(scale, 1e-6))) / np.log(5.0), 1.0)
    anisotropy_score = 1.0 - min(max(anisotropy - 1.0, 0.0) / 2.0, 1.0)
    perspective_score = 1.0 - min(perspective / 0.01, 1.0)
    return float(max(0.0, min(1.0, 0.40 * result.confidence + 0.25 * scale_score + 0.20 * anisotropy_score + 0.15 * perspective_score)))


def _terrain_args_for_legacy(bundle: TerrainBundle, frame_path: str, options: TerrainMatchOptions):
    import argparse

    features_path = bundle.manifest.get("features", {}).get("path")
    if not features_path:
        raise ValueError("Legacy bundle fallback requires features.path")
    return argparse.Namespace(
        map_image=str(bundle.orthophoto_path),
        features=str(bundle.bundle_dir / features_path),
        frame=frame_path,
        method=options.method,
        max_features=options.max_features,
        ratio=options.ratio,
        min_inliers=options.min_inliers,
        ransac_threshold=options.ransac_threshold,
        min_scale=options.min_scale,
        max_scale=options.max_scale,
        max_rotation_deg=options.max_rotation_deg,
        max_scale_anisotropy=options.max_scale_anisotropy,
        max_perspective_norm=options.max_perspective_norm,
        camera_calibration=options.camera_calibration,
        viz=None,
    )


def _wrap_legacy_result(bundle: TerrainBundle, result: dict[str, Any]) -> dict[str, Any]:
    measurement = result.get("measurement") or {}
    position = result.get("estimated_position") or {}
    covariance = measurement.get("covariance") or {}
    wrapped = {
        **result,
        "timestamp_us": int(time.time() * 1_000_000),
        "map_id": bundle.manifest.get("bundle_id") or bundle.bundle_dir.name,
        "tile_id": "legacy_map",
        "local_enu_m": {
            "x": position.get("east_m"),
            "y": position.get("north_m"),
            "z": measurement.get("z_m"),
        },
        "lat_lon": {
            "lat": position.get("latitude"),
            "lon": position.get("longitude"),
        },
        "scale_confidence": estimate_scale_confidence(
            MatchResult(
                status=str(result.get("status")),
                confidence=float(result.get("confidence", 0.0)),
                total_matches=int(result.get("total_matches", 0)),
                inliers=int(result.get("inliers", 0)),
                inlier_ratio=float(result.get("inlier_ratio", 0.0)),
                reprojection_error_px=result.get("reprojection_error_px"),
                homography=result.get("homography"),
                geometry=result.get("geometry"),
                reason=result.get("reason"),
            )
        ),
        "covariance": {
            "x_m2": covariance.get("x_m2"),
            "y_m2": covariance.get("y_m2"),
            "z_m2": covariance.get("z_m2"),
            "yaw_rad2": covariance.get("yaw_rad2"),
        },
        "altitude_source": "unset",
        "baro_altitude_m": None,
        "baro_relative_m": None,
        "baro_health": "unavailable",
    }
    return wrapped


def _candidate_result(
    *,
    tile: TerrainTile,
    frame_center: np.ndarray,
    frame_shape: tuple[int, int],
    frame_features,
    tile_descriptor: dict,
    matches: list[cv2.DMatch],
    result: MatchResult,
    bundle: TerrainBundle,
    quality: dict[str, float],
    method: str,
) -> dict[str, Any]:
    output = result.to_dict()
    output["tile_id"] = tile.tile_id
    output["method"] = method
    output["frame_keypoints"] = int(frame_features.keypoints_xy.shape[0])
    output["tile_keypoints"] = int(tile_descriptor["keypoints_xy"].shape[0])
    output["total_matches"] = int(len(matches))
    output["frame_quality"] = {
        **quality,
        "feature_density_per_megapixel": feature_density(
            int(frame_features.keypoints_xy.shape[0]),
            frame_shape,
        ),
    }
    if not result.homography:
        return output

    homography = np.array(result.homography, dtype=np.float64)
    tile_center = cv2.perspectiveTransform(frame_center, homography)[0][0]
    map_x = float(tile.x0_px + tile_center[0])
    map_y = float(tile.y0_px + tile_center[1])
    output["estimated_map_pixel"] = {"x": map_x, "y": map_y}
    output["scale_confidence"] = estimate_scale_confidence(result)
    output["altitude_source"] = "unset"
    output["baro_altitude_m"] = None
    output["baro_relative_m"] = None
    output["baro_health"] = "unavailable"

    if bundle.georef is None:
        return output

    east_m, north_m = bundle.georef.pixel_to_local_m(map_x, map_y)
    lat, lon = bundle.georef.pixel_to_latlon(map_x, map_y)
    position_confidence = estimate_visual_position_confidence(
        match_confidence=output["confidence"],
        georef_confidence=bundle.georef.confidence,
    )
    covariance = estimate_position_covariance_m2(
        confidence=position_confidence,
        reprojection_error_px=output["reprojection_error_px"],
        gsd_m=bundle.georef.gsd_m,
    )
    covariance["z_m2"] = None
    covariance["yaw_rad2"] = None
    output["map_georef"] = bundle.georef.to_dict()
    output["position_confidence"] = position_confidence
    output["local_enu_m"] = {"x": east_m, "y": north_m, "z": None}
    output["lat_lon"] = {"lat": lat, "lon": lon}
    output["estimated_position"] = {
        "latitude": lat,
        "longitude": lon,
        "east_m": east_m,
        "north_m": north_m,
        "source": "terrain_tile_homography_center_georef",
    }
    output["covariance"] = {
        "x_m2": covariance.get("x_m2"),
        "y_m2": covariance.get("y_m2"),
        "z_m2": None,
        "yaw_rad2": None,
    }
    output["measurement"] = {
        "frame": "local_enu",
        "x_m": east_m,
        "y_m": north_m,
        "z_m": None,
        "yaw_rad": None,
        "confidence": position_confidence,
        "covariance": covariance,
        "source": "terrain_vision_map_match",
    }
    return output


def _rank_candidates_by_global_descriptor(
    candidates: list[TerrainTile],
    *,
    frame_global_descriptor: np.ndarray,
    max_candidates: int,
) -> tuple[list[tuple[TerrainTile, dict]], dict[str, Any]]:
    scored: list[tuple[float, int, str, TerrainTile, dict]] = []
    unscored: list[tuple[TerrainTile, dict]] = []
    for tile in candidates:
        descriptor = load_tile_descriptor(tile.descriptor_path)
        distance = global_descriptor_distance(frame_global_descriptor, descriptor.get("global_descriptor"))
        if distance is None:
            unscored.append((tile, descriptor))
        else:
            scored.append((distance, -tile.keypoint_count, tile.tile_id, tile, descriptor))

    scored.sort(key=lambda item: (item[0], item[1], item[2]))
    selected: list[tuple[TerrainTile, dict]] = [(tile, descriptor) for _, _, _, tile, descriptor in scored[:max_candidates]]
    if len(selected) < max_candidates:
        selected.extend(unscored[: max_candidates - len(selected)])
    distances = [
        {"tile_id": tile.tile_id, "distance": distance}
        for distance, _, _, tile, _ in scored[: min(len(scored), 10)]
    ]
    return selected, {
        "enabled": True,
        "descriptor": "grayscale_histogram_v1",
        "input_tiles": len(candidates),
        "scored_tiles": len(scored),
        "selected_tiles": len(selected),
        "selected_tile_ids": [tile.tile_id for tile, _ in selected],
        "best_distances": distances,
    }


def match_terrain_frame(bundle: TerrainBundle, frame_path: str, options: TerrainMatchOptions) -> dict[str, Any]:
    if not bundle.has_tile_index:
        return _wrap_legacy_result(bundle, match_frame_to_map(_terrain_args_for_legacy(bundle, frame_path, options)))

    frame, calibration_log = load_frame_for_matching(frame_path, options.camera_calibration)
    quality = frame_quality_metrics(frame)
    method = options.method or str(bundle.manifest.get("features", {}).get("method", "orb"))
    frame_features = extract_features(frame, method=method, max_features=options.max_features)  # type: ignore[arg-type]
    height, width = frame.shape[:2]
    frame_center = np.float32([[[width / 2.0, height / 2.0]]])
    query_max_candidates = max(int(options.max_candidates), 0)
    has_prior = options.prior_east_m is not None and options.prior_north_m is not None and options.search_radius_m is not None
    if not has_prior:
        query_max_candidates = max(query_max_candidates, int(options.max_candidates) * max(int(options.global_retrieval_multiplier), 1))
    query_result = query_tiles_with_metadata(
        bundle.tile_index_path,
        bundle.bundle_dir,
        prior_east_m=options.prior_east_m,
        prior_north_m=options.prior_north_m,
        search_radius_m=options.search_radius_m,
        max_candidates=query_max_candidates,
    )
    candidate_descriptors, global_retrieval = _rank_candidates_by_global_descriptor(
        query_result.tiles,
        frame_global_descriptor=image_global_descriptor(frame),
        max_candidates=options.max_candidates,
    )
    query_metadata = {
        **query_result.metadata,
        "pre_global_candidate_tiles": len(query_result.tiles),
        "selected_tiles": len(candidate_descriptors),
        "selected_tile_ids": [tile.tile_id for tile, _ in candidate_descriptors],
        "global_retrieval": global_retrieval,
    }

    best: dict[str, Any] | None = None
    rejected: list[dict[str, Any]] = []
    for tile, descriptor in candidate_descriptors:
        matches = match_descriptors(
            map_descriptors=descriptor["descriptors"],
            frame_descriptors=frame_features.descriptors,
            method=method,  # type: ignore[arg-type]
            ratio=options.ratio,
        )
        result = estimate_homography(
            frame_keypoints_xy=frame_features.keypoints_xy,
            map_keypoints_xy=descriptor["keypoints_xy"],
            matches=matches,
            min_inliers=options.min_inliers,
            ransac_reproj_threshold=options.ransac_threshold,
            min_scale=options.min_scale,
            max_scale=options.max_scale,
            max_rotation_deg=options.max_rotation_deg,
            max_scale_anisotropy=options.max_scale_anisotropy,
            max_perspective_norm=options.max_perspective_norm,
        )
        candidate = _candidate_result(
            tile=tile,
            frame_center=frame_center,
            frame_shape=frame.shape[:2],
            frame_features=frame_features,
            tile_descriptor=descriptor,
            matches=matches,
            result=result,
            bundle=bundle,
            quality=quality,
            method=method,
        )
        if calibration_log is not None:
            candidate["camera_calibration"] = calibration_log
        if candidate.get("status") == "accepted":
            if best is None or (
                float(candidate.get("confidence", 0.0)),
                int(candidate.get("inliers", 0)),
            ) > (
                float(best.get("confidence", 0.0)),
                int(best.get("inliers", 0)),
            ):
                best = candidate
        else:
            rejected.append(
                {
                    "tile_id": tile.tile_id,
                    "status": candidate.get("status"),
                    "reason": candidate.get("reason"),
                    "confidence": candidate.get("confidence", 0.0),
                    "inliers": candidate.get("inliers", 0),
                }
            )

    if best is not None:
        best["timestamp_us"] = int(time.time() * 1_000_000)
        best["map_id"] = bundle.manifest.get("bundle_id") or bundle.bundle_dir.name
        best["candidate_tiles"] = len(candidate_descriptors)
        best["tile_query"] = query_metadata
        return best

    return {
        "timestamp_us": int(time.time() * 1_000_000),
        "status": "rejected",
        "reason": "no_candidate_tile_accepted",
        "map_id": bundle.manifest.get("bundle_id") or bundle.bundle_dir.name,
        "tile_id": None,
        "candidate_tiles": len(candidate_descriptors),
        "tile_query": query_metadata,
        "frame_keypoints": int(frame_features.keypoints_xy.shape[0]),
        "frame_quality": {
            **quality,
            "feature_density_per_megapixel": feature_density(int(frame_features.keypoints_xy.shape[0]), frame.shape[:2]),
        },
        "scale_confidence": 0.0,
        "confidence": 0.0,
        "covariance": {"x_m2": None, "y_m2": None, "z_m2": None, "yaw_rad2": None},
        "local_enu_m": {"x": None, "y": None, "z": None},
        "lat_lon": {"lat": None, "lon": None},
        "altitude_source": "unset",
        "baro_altitude_m": None,
        "baro_relative_m": None,
        "baro_health": "unavailable",
        "rejected_candidates": rejected[:10],
    }
