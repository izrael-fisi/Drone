"""Lift learned map pixels onto a declared metric surface, never an assumed plane."""

from __future__ import annotations

from contextlib import ExitStack
import json
from pathlib import Path
from typing import Any

import numpy as np

from vision_nav.training_capture import sha256_file
from .training_labels import geodetic_to_enu


ELEVATION_SCHEMA = "vision_nav_elevation_source_v1"
MASKED_ELEVATION_SCHEMA = "vision_nav_elevation_source_v2"
SURFACE_MASK_SCHEMA = "vision_nav_surface_support_mask_v1"
LANDMARK_SCHEMA = "vision_nav_map_landmark_provenance_v1"


def validate_surface_contract(spec):
    version = spec.get("schema_version")
    if version not in {ELEVATION_SCHEMA, MASKED_ELEVATION_SCHEMA}:
        raise ValueError("Unsupported elevation source schema")
    surface = spec.get("surface_type")
    if surface not in {"dsm", "dtm"} or version == ELEVATION_SCHEMA and surface != "dsm":
        raise ValueError("v1 requires DSM; masked ground-only DTM requires elevation v2")
    mask = spec.get("surface_mask")
    if surface == "dtm" and mask is None:
        raise ValueError("DTM requires explicit reviewed ground-surface support")
    if mask is not None:
        if version != MASKED_ELEVATION_SCHEMA or not isinstance(mask, dict):
            raise ValueError("Surface masks require elevation v2 and a mask object")
        if (mask.get("schema_version") != SURFACE_MASK_SCHEMA
                or mask.get("valid_value") != 1 or type(mask.get("valid_value")) is not int
                or mask.get("unknown_policy") != "reject"
                or mask.get("supported_surface") != "bare_earth_ground"):
            raise ValueError("Invalid conservative ground-support mask contract")
        for key in ("source", "acquisition_date", "license", "alignment_evidence", "support_basis"):
            if not isinstance(mask.get(key), str) or not mask[key].strip():
                raise ValueError("Ground-support mask requires "+key)
        margin = mask.get("exclusion_margin_m")
        if type(margin) not in (int, float) or not np.isfinite(margin) or margin <= 0:
            raise ValueError("Ground-support mask requires a positive declared exclusion margin")
        if not isinstance(mask.get("raster_path"), str) or not mask["raster_path"]:
            raise ValueError("Ground-support mask requires a raster path")
        for key in ("sha256", "imagery_sha256"):
            digest = mask.get(key)
            if not isinstance(digest, str) or len(digest) != 64 or any(c not in "0123456789abcdef" for c in digest):
                raise ValueError("Ground-support mask requires "+key)
    return mask


class MapLandmarkSampler:
    """Offline bounded raster sampling; input elevations must already be WGS84 heights.

    Orthometric/geoid conversion is deliberately not guessed. Optional v2 ground
    support excludes non-ground/unknown regions before height lifting. Bare-earth
    DTM is allowed only through that explicit masked research path.
    """

    def __init__(self, layout: dict[str, Any], spec_path: str | Path):
        try:
            import rasterio
            from pyproj import Transformer
        except ImportError as exc:
            raise RuntimeError("Metric landmarks require drone-vision-nav[map-production].") from exc
        path = Path(spec_path).expanduser().resolve()
        spec = json.loads(path.read_text(encoding="utf-8"))
        mask_spec = validate_surface_contract(spec)
        if spec.get("vertical_datum") != "wgs84_ellipsoid" or spec.get("units") != "m":
            raise ValueError("Elevation must be converted to WGS84 ellipsoidal metres before compilation.")
        for key in ("source", "acquisition_date", "license", "alignment_evidence"):
            if not isinstance(spec.get(key), str) or not spec[key].strip():
                raise ValueError(f"Elevation source requires {key} provenance.")
        variance = []
        for key in ("horizontal_sigma_m", "vertical_sigma_m"):
            value = spec.get(key)
            if isinstance(value, bool) or not isinstance(value, (int, float)) or not np.isfinite(value) or value <= 0:
                raise ValueError(f"Elevation source requires a finite positive {key}.")
            variance.append(float(value) ** 2)
        self.variance = np.asarray([variance[0], variance[0], variance[1]], dtype=np.float64)
        origin = layout.get("geodetic_origin_wgs84", {})
        values = [origin.get(key) for key in ("lat_deg", "lon_deg", "altitude_m")]
        if not all(isinstance(v, (int, float)) and np.isfinite(v) for v in values):
            raise ValueError("Landmarks require a finite WGS84 geodetic origin.")
        if not -90 <= values[0] <= 90 or not -180 <= values[1] <= 180:
            raise ValueError("Landmark origin is outside WGS84 bounds.")
        if spec.get("origin_vertical_datum") != "wgs84_ellipsoid":
            raise ValueError("The layout origin height must explicitly use wgs84_ellipsoid.")
        self.origin = origin
        cog_info = layout.get("cog", {})
        if mask_spec and mask_spec["imagery_sha256"] != cog_info.get("sha256"):
            raise ValueError("Ground-support mask belongs to different source imagery")
        cog = Path(cog_info.get("path", "")).expanduser().resolve()
        elevation = path.parent / spec.get("raster_path", "")
        if not cog.is_file() or sha256_file(cog) != cog_info.get("sha256"):
            raise ValueError("Layout source COG is missing or its checksum changed.")
        if not elevation.is_file() or sha256_file(elevation) != spec.get("sha256"):
            raise ValueError("Elevation raster is missing or its checksum changed.")
        mask_path = (path.parent / mask_spec["raster_path"]).resolve() if mask_spec else None
        if mask_path and (not mask_path.is_file() or sha256_file(mask_path) != mask_spec["sha256"]):
            raise ValueError("Ground-support mask is missing or its checksum changed")
        self.source_files = {str(path): sha256_file(path), str(cog): cog_info["sha256"],
                             str(elevation.resolve()): spec["sha256"]}
        if mask_path:
            self.source_files[str(mask_path)] = mask_spec["sha256"]
        self._stack = ExitStack()
        try:
            self.imagery = self._stack.enter_context(rasterio.open(cog))
            self.surface = self._stack.enter_context(rasterio.open(elevation))
            self.support = self._stack.enter_context(rasterio.open(mask_path)) if mask_path else None
            if self.imagery.crs is None or self.surface.crs is None:
                raise ValueError("Imagery and DSM both require an explicit CRS.")
            if self.surface.count != 1:
                raise ValueError("Elevation DSM must contain one height band.")
            if self.surface.scales != (1.0,) or self.surface.offsets != (0.0,):
                raise ValueError("Normalize DSM scale/offset to metre values before compilation.")
            self.to_surface = Transformer.from_crs(self.imagery.crs, self.surface.crs, always_xy=True)
            self.to_geodetic = Transformer.from_crs(self.imagery.crs, "EPSG:4326", always_xy=True)
            if self.support:
                if (self.support.driver != "GTiff" or {Path(p).resolve() for p in self.support.files} != {mask_path}
                        or self.support.crs is None or self.support.count != 1 or self.support.dtypes != ("uint8",)
                        or self.support.scales != (1.0,) or self.support.offsets != (0.0,)
                        or self.support.nodata == 1):
                    raise ValueError("Ground-support mask must be a self-contained georeferenced, unscaled uint8 GeoTIFF; 1 is valid")
                self.to_support = Transformer.from_crs(self.imagery.crs, self.support.crs, always_xy=True)
                spec = {**spec, "surface_mask": {**mask_spec, "raster_path": str(mask_path),
                                                  "crs": self.support.crs.to_string()}}
            self.provenance = {
                "schema_version": LANDMARK_SCHEMA,
                "frame": "ENU",
                "geodetic_origin_wgs84": origin,
                "origin_vertical_datum": "wgs84_ellipsoid",
                "imagery_sha256": cog_info["sha256"],
                "imagery_crs": self.imagery.crs.to_string(),
                "elevation": {**spec, "raster_path": str(elevation.resolve()), "crs": self.surface.crs.to_string()},
                "sampling": "nearest_valid_pixel_no_extrapolation",
                "keypoint_coordinates": "model_input_pixel_centers",
                "variance_semantics": "declared_map_error_prior_not_pose_covariance",
                "surface_support": "reviewed_ground_mask" if self.support else "dsm_valid_heights_only",
                "ground_mask_is_not_field_validation": True,
            }
        except BaseException:
            self._stack.close()
            raise

    def __enter__(self) -> MapLandmarkSampler:
        return self

    def __exit__(self, *args: Any) -> None:
        self._stack.close()

    def sample(self, tile: dict[str, Any], keypoints: np.ndarray, image_size: tuple[int, int]) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Return valid mask, ENU points and diagonal map-error priors.

        Resize is inverted at pixel centres, then the original raster affine
        transform is applied. ENU bounds are never used as a pixel transform.
        Invalid heights remove the corresponding descriptor, never become zero.
        """
        points = np.asarray(keypoints, dtype=np.float64).reshape(-1, 2)
        width, height = image_size
        if width <= 0 or height <= 0 or not np.isfinite(points).all():
            raise ValueError("Invalid learned feature coordinates or image size.")
        window = tile["source_window_px"]
        column, row, source_width, source_height = [window[k] for k in ("column", "row", "width", "height")]
        if (column < 0 or row < 0 or source_width <= 0 or source_height <= 0
                or column + source_width > self.imagery.width or row + source_height > self.imagery.height):
            raise ValueError("Tile window is outside the source imagery.")
        valid = (points[:, 0] >= 0) & (points[:, 0] < width) & (points[:, 1] >= 0) & (points[:, 1] < height)
        columns = column + (points[:, 0] + 0.5) * source_width / width
        rows = row + (points[:, 1] + 0.5) * source_height / height
        xs, ys = self.imagery.transform * (columns, rows)
        sx, sy = self.to_surface.transform(xs, ys)
        lon, lat = self.to_geodetic.transform(xs, ys)
        valid &= np.isfinite(sx) & np.isfinite(sy) & np.isfinite(lon) & np.isfinite(lat)
        initially_valid = valid.copy()
        if self.support:
            mx, my = self.to_support.transform(xs, ys)
            for i in np.flatnonzero(valid):
                c, r = (~self.support.transform) * (mx[i], my[i])
                if not (np.isfinite(c) and np.isfinite(r) and 0 <= c < self.support.width and 0 <= r < self.support.height):
                    valid[i] = False
                    continue
                value = next(self.support.sample([(mx[i], my[i])], indexes=1, masked=True))[0]
                if np.ma.is_masked(value) or value != 1:
                    valid[i] = False
        support_valid = valid.copy()
        enu = []
        for i in np.flatnonzero(valid):
            # Explicit extent check covers rasters that have no nodata value.
            c, r = (~self.surface.transform) * (sx[i], sy[i])
            if not (0 <= c < self.surface.width and 0 <= r < self.surface.height):
                valid[i] = False
                continue
            value = next(self.surface.sample([(sx[i], sy[i])], indexes=1, masked=True))[0]
            if np.ma.is_masked(value) or not np.isfinite(value):
                valid[i] = False
                continue
            enu.append(geodetic_to_enu(
                float(lat[i]), float(lon[i]), float(value),
                origin_lat_deg=self.origin["lat_deg"], origin_lon_deg=self.origin["lon_deg"],
                origin_altitude_m=self.origin["altitude_m"],
            ))
        result = np.asarray(enu, dtype=np.float64).reshape(-1, 3)
        if not np.isfinite(result).all():
            raise ValueError("Georeferencing produced nonfinite map points.")
        self.last_sample_report = {"input_features": len(points), "valid_features": int(valid.sum()),
            "outside_imagery_or_invalid_coordinates": int((~initially_valid).sum()),
            "excluded_or_unknown_surface": int((initially_valid & ~support_valid).sum()),
            "invalid_elevation": int((support_valid & ~valid).sum()),
            "surface_type": self.provenance["elevation"]["surface_type"],
            "mask_sha256": self.provenance["elevation"].get("surface_mask", {}).get("sha256")}
        return valid, result, np.tile(self.variance, (len(result), 1))


def local_feature_image_size(contract: dict[str, Any]) -> tuple[int, int]:
    inputs = [spec for spec in contract.get("inputs", {}).values() if spec.get("shadow_source") == "frame_rgb"]
    outputs = [spec for spec in contract.get("outputs", {}).values() if spec.get("shadow_output") == "local_keypoints"]
    if len(inputs) != 1 or len(outputs) != 1 or outputs[0].get("coordinate_space") != "input_pixel_centers":
        raise ValueError("Metric landmarks require local_keypoints coordinate_space=input_pixel_centers.")
    size = inputs[0].get("preprocess", {}).get("image_size")
    if not isinstance(size, list) or len(size) != 2 or not all(isinstance(v, int) and v > 0 for v in size):
        raise ValueError("Metric landmarks require the local feature input image size.")
    return tuple(size)
