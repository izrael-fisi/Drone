"""Exact pixel-space labels for georeferenced map crops; no ENU-box approximation."""

from typing import Any
import numpy as np

from .training_labels import _geodetic_to_ecef, geodetic_to_enu


def reference_enu_to_tile_uv(position: Any, geometry: dict[str, Any]) -> np.ndarray:
    """Project a reference position to normalized crop coordinates (edges 0..1).

    This is a training label for the declared reference point, not a camera-ray
    terrain intersection or an inferred camera pose.
    """
    try:
        from pyproj import Transformer
    except ImportError as exc:
        raise RuntimeError("Map-conditioned labels require the map-production extras.") from exc
    if geometry.get("schema_version") != "vision_nav_tile_pixel_geometry_v1":
        raise ValueError("Rebuild tiles with exact pixel geometry before map-conditioned training.")
    origin = geometry.get("geodetic_origin_wgs84", {})
    vector = np.asarray(position, dtype=float)
    affine = np.asarray(geometry.get("affine"), dtype=float)
    size = np.asarray(geometry.get("image_size"), dtype=float)
    anchor = np.asarray([origin.get(k) for k in ("lat_deg", "lon_deg", "altitude_m")], dtype=float)
    if (vector.shape != (3,) or affine.shape != (6,) or size.shape != (2,) or not np.all(size > 0)
            or not all(np.isfinite(v).all() for v in (vector, affine, size, anchor))):
        raise ValueError("Invalid metric tile pixel geometry or reference position.")
    if not -90 <= anchor[0] <= 90 or not -180 <= anchor[1] <= 180:
        raise ValueError("Invalid map geodetic origin.")
    lat, lon = np.deg2rad(anchor[:2])
    ecef_to_enu = np.asarray([
        [-np.sin(lon), np.cos(lon), 0],
        [-np.sin(lat) * np.cos(lon), -np.sin(lat) * np.sin(lon), np.cos(lat)],
        [np.cos(lat) * np.cos(lon), np.cos(lat) * np.sin(lon), np.sin(lat)],
    ])
    xyz = np.asarray(_geodetic_to_ecef(*anchor)) + ecef_to_enu.T @ vector
    longitude, latitude, _ = Transformer.from_crs(4978, 4979, always_xy=True).transform(*xyz)
    x, y = Transformer.from_crs(4326, geometry["crs"], always_xy=True).transform(longitude, latitude)
    a, b, c, d, e, f = affine
    try:
        pixel_corner = np.linalg.solve(np.asarray([[a, b], [d, e]]), np.asarray([x - c, y - f]))
    except np.linalg.LinAlgError as exc:
        raise ValueError("Tile affine is singular.") from exc
    uv = pixel_corner / size
    if not np.isfinite(uv).all():
        raise ValueError("Tile projection produced nonfinite coordinates.")
    return uv


def tile_metric_axes(geometry: dict[str, Any]) -> list[float]:
    """Local EN vectors for one normalized horizontal/vertical crop step."""
    from pyproj import Transformer
    a, b, c, d, e, f = geometry["affine"]
    width, height = geometry["image_size"]
    origin = geometry["geodetic_origin_wgs84"]
    transformer = Transformer.from_crs(geometry["crs"], 4326, always_xy=True)
    points = []
    for column, row in ((0, 0), (width, 0), (0, height)):
        lon, lat = transformer.transform(a * column + b * row + c, d * column + e * row + f)
        points.append(np.asarray(geodetic_to_enu(lat, lon, origin["altitude_m"], origin_lat_deg=origin["lat_deg"],
                                               origin_lon_deg=origin["lon_deg"], origin_altitude_m=origin["altitude_m"])))
    axes = np.concatenate(((points[1] - points[0])[:2], (points[2] - points[0])[:2]))
    if not np.isfinite(axes).all() or abs(np.linalg.det(axes.reshape(2, 2))) < 1e-6:
        raise ValueError("Degenerate map metric axes.")
    return axes.tolist()
