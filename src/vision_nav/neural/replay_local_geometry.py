"""Learned correspondence and optional surface-backed pose evidence for replay."""

from collections import OrderedDict
import json
from pathlib import Path
from typing import Any

import cv2
import numpy as np

from vision_nav.camera import validate_image_size
from vision_nav.training_capture import sha256_file
from .candidate_groups import landmark_bounds, select_candidate_groups
from .bounded_pnp import FIT_POLICY, verify_bounded_pnp
from .learned_correspondence import load_local_matcher, resized_camera_matrix
from .map_asset_compiler import _read_layout
from .map_landmarks import MapLandmarkSampler
from .retrieval_export import export_member
from .shadow_geometry import verify_planar_correspondences


class ReplayLocalVerifier:
    def __init__(self, config_path: str | Path, layout_path: Path, calibration: Any):
        path = Path(config_path).expanduser().resolve()
        config = json.loads(path.read_text(encoding="utf-8"))
        self.matcher = load_local_matcher(path)
        self.layout_path, self.layout = _read_layout(layout_path)
        self.calibration = calibration
        self.tiles = {tile["tile_id"]: tile for tile in self.layout["tiles"]}
        if len(self.tiles) != len(self.layout["tiles"]):
            raise ValueError("Duplicate tile IDs in local geometry layout")
        self.elevation_spec = None
        self.surface_reports = {}
        self._surface_stats = {}
        self.provenance = {**self.matcher.provenance, "tile_layout_sha256": sha256_file(self.layout_path),
                           "geometry_mode": "planar_consistency_only", "map_feature_cache_limit": 4,
                           "camera_geometry": "undistorted_full_frame_then_pixel_center_resize"}
        self.provenance["metric_fit_policy"] = dict(FIT_POLICY)
        if config.get("elevation_spec"):
            self.elevation_spec = (path.parent / Path(config["elevation_spec"]).expanduser()).resolve()
            with MapLandmarkSampler(self.layout, self.elevation_spec) as sampler:
                self.provenance.update(geometry_mode="surface_backed_pnp_camera_candidate",
                                       elevation_spec_sha256=sha256_file(self.elevation_spec), landmarks=sampler.provenance)
                self._surface_stats = {p: self._file_identity(p) for p in sampler.source_files}
        self.cache = OrderedDict()

    @staticmethod
    def _file_identity(path):
        stat = Path(path).stat()
        return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns

    def _map_features(self, tile_id: str):
        # Avoid rehashing large rasters per frame, but never reuse a cached tile
        # after local source replacement/modification during this session.
        if any(self._file_identity(p) != expected for p, expected in self._surface_stats.items()):
            raise ValueError("Map surface/mask source changed during replay; rebuild/restart required")
        if tile_id in self.cache:
            self.cache.move_to_end(tile_id)
            return self.cache[tile_id]
        if tile_id not in self.tiles:
            raise ValueError("Local geometry tile is absent from selected layout")
        tile = self.tiles[tile_id]
        image_path = export_member(self.layout_path.parent, tile["image_uri"])
        if sha256_file(image_path) != tile["image_sha256"]:
            raise ValueError("Local geometry tile image checksum mismatch")
        image = cv2.imread(str(image_path))
        if image is None or list(image.shape[1::-1]) != tile["pixel_geometry"]["image_size"]:
            raise ValueError("Local geometry tile image dimensions mismatch")
        features = self.matcher.extract(image)
        points, priors = None, None
        if self.elevation_spec:
            if sha256_file(self.elevation_spec) != self.provenance["elevation_spec_sha256"]:
                raise ValueError("Elevation specification changed during replay")
            with MapLandmarkSampler(self.layout, self.elevation_spec) as sampler:
                keep, points, priors = sampler.sample(tile, features.keypoints,
                    (self.matcher.policy.image_width, self.matcher.policy.image_height))
                self.surface_reports[tile_id] = sampler.last_sample_report
            features = features.selected(keep)
        value = (features, points, priors)
        if len(self.cache) >= 4:
            evicted, _ = self.cache.popitem(last=False)
            self.surface_reports.pop(evicted, None)
        self.cache[tile_id] = value
        return value

    def verify(self, query_path: Path, tile_ids: list[str]) -> dict[str, Any]:
        image = cv2.imread(str(query_path))
        if image is None:
            raise ValueError("Local geometry camera image cannot be decoded")
        return self.verify_pixels(image, tile_ids)

    def verify_pixels(self, image: np.ndarray, tile_ids: list[str]) -> dict[str, Any]:
        if not tile_ids or len(tile_ids) > 4 or len(set(tile_ids)) != len(tile_ids):
            raise ValueError("Local verification requires one to four distinct candidates")
        if not isinstance(image, np.ndarray) or image.dtype != np.uint8 or image.ndim not in (2, 3):
            raise ValueError("Local geometry requires uint8 gray or BGR pixels")
        if image.ndim == 2:
            image = cv2.cvtColor(image, cv2.COLOR_GRAY2BGR)
        if image.shape[2] != 3:
            raise ValueError("Local geometry requires three BGR channels")
        validate_image_size(image, self.calibration)
        source_size = image.shape[1::-1]
        rectified = cv2.undistort(image, self.calibration.camera_matrix, self.calibration.distortion_coefficients)
        query = self.matcher.extract(rectified)
        policy = self.matcher.policy
        matrix = resized_camera_matrix(self.calibration.camera_matrix, source_size, (policy.image_width, policy.image_height))
        attempts = []
        for tile_id in tile_ids:
            attempt = {"tile_id": tile_id, "position_authority": False, "covariance": None}
            try:
                tile, points, priors = self._map_features(tile_id)
                pairs, scores = self.matcher.match(query, tile)
                common = {"query_keypoints": query.keypoints, "match_pairs": pairs, "scores": scores,
                          "query_width": policy.image_width, "query_height": policy.image_height, **policy.geometry_kwargs()}
                if points is None:
                    geometry = verify_planar_correspondences(map_keypoints=tile.keypoints, **common)
                else:
                    geometry, selected = verify_bounded_pnp(map_points_enu_m=points, camera_matrix=matrix, **common)
                    pairs, scores = pairs[selected], scores[selected]
                attempt.update(status="verified", feature_counts={"query": len(query.keypoints), "map": len(tile.keypoints)},
                               match_count=len(pairs), geometry=geometry,
                               matched_query_pixels=query.keypoints[pairs[:, 0]].tolist(),
                               matched_map_pixels=tile.keypoints[pairs[:, 1]].tolist(),
                               matched_query_feature_indices=pairs[:, 0].tolist(),
                               matched_map_feature_indices=pairs[:, 1].tolist(),
                               matcher_scores=scores.tolist(), image_sha256=self.tiles[tile_id]["image_sha256"])
                if points is not None:
                    attempt.update(matched_map_points_enu_m=points[pairs[:, 1]].tolist(),
                                   matched_map_variance_enu_m2=priors[pairs[:, 1]].tolist(),
                                   landmark_support_bounds_enu_m=landmark_bounds(points),
                                   calibrated_camera_matrix=matrix.tolist(),
                                   surface_filter=self.surface_reports[tile_id])
            except (ValueError, RuntimeError, OSError, cv2.error) as exc:
                attempt.update(status="unavailable", reason=str(exc))
            attempts.append(attempt)
        incomplete = any(a["status"] == "unavailable" for a in attempts)
        selection = select_candidate_groups(attempts, complete=not incomplete)
        reason = {"unique_geometry_within_retrieved_set": "single_consistent_candidate_not_global_authority",
                  "candidate_verification_incomplete": "incomplete_local_verification"}.get(selection["reason"], selection["reason"])
        return {"geometry_mode": self.provenance["geometry_mode"], "attempts": attempts,
                "accepted_tile_ids": selection["accepted_tile_ids"], "reason": reason,
                "complete": selection["complete"], "candidate_selection": selection,
                "position_authority": False, "covariance": None, "px4_external_vision": "disabled"}
