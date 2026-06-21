from __future__ import annotations

from dataclasses import asdict, dataclass
import json
import math
from typing import Any


EARTH_RADIUS_M = 6378137.0


@dataclass(frozen=True)
class SimpleGeoReference:
    """Small-area georeference for prototype orthophoto matching.

    The default frame assumes a north-up map image:
    - increasing image x is local east
    - increasing image y is local south
    - rotation_deg rotates that local ENU vector counter-clockwise
    """

    origin_lat: float
    origin_lon: float
    gsd_m: float
    origin_pixel_x: float = 0.0
    origin_pixel_y: float = 0.0
    rotation_deg: float = 0.0
    source: str = "manual"
    confidence: float = 1.0
    crs: str | None = None

    def pixel_to_local_m(self, x_px: float, y_px: float) -> tuple[float, float]:
        dx = (x_px - self.origin_pixel_x) * self.gsd_m
        dy = (y_px - self.origin_pixel_y) * self.gsd_m

        # Image y grows downward, so north is negative image dy.
        east = dx
        north = -dy

        theta = math.radians(self.rotation_deg)
        cos_t = math.cos(theta)
        sin_t = math.sin(theta)
        rotated_east = east * cos_t - north * sin_t
        rotated_north = east * sin_t + north * cos_t
        return rotated_east, rotated_north

    def pixel_to_latlon(self, x_px: float, y_px: float) -> tuple[float, float]:
        east_m, north_m = self.pixel_to_local_m(x_px, y_px)
        dlat = north_m / EARTH_RADIUS_M
        lat = self.origin_lat + math.degrees(dlat)

        origin_lat_rad = math.radians(self.origin_lat)
        meters_per_lon_rad = EARTH_RADIUS_M * max(math.cos(origin_lat_rad), 1e-9)
        dlon = east_m / meters_per_lon_rad
        lon = self.origin_lon + math.degrees(dlon)
        return lat, lon

    def latlon_to_local_m(self, lat: float, lon: float) -> tuple[float, float]:
        origin_lat_rad = math.radians(self.origin_lat)
        east_m = math.radians(lon - self.origin_lon) * EARTH_RADIUS_M * max(math.cos(origin_lat_rad), 1e-9)
        north_m = math.radians(lat - self.origin_lat) * EARTH_RADIUS_M
        return east_m, north_m

    def local_m_to_pixel(self, east_m: float, north_m: float) -> tuple[float, float]:
        theta = math.radians(self.rotation_deg)
        cos_t = math.cos(theta)
        sin_t = math.sin(theta)

        # Invert the counter-clockwise rotation used by pixel_to_local_m.
        unrotated_east = east_m * cos_t + north_m * sin_t
        unrotated_north = -east_m * sin_t + north_m * cos_t
        x_px = self.origin_pixel_x + unrotated_east / self.gsd_m
        y_px = self.origin_pixel_y - unrotated_north / self.gsd_m
        return x_px, y_px

    def latlon_to_pixel(self, lat: float, lon: float) -> tuple[float, float]:
        east_m, north_m = self.latlon_to_local_m(lat, lon)
        return self.local_m_to_pixel(east_m, north_m)

    def to_dict(self) -> dict[str, float]:
        return asdict(self)

    @classmethod
    def from_dict(cls, value: dict[str, Any]) -> "SimpleGeoReference":
        return cls(
            origin_lat=float(value["origin_lat"]),
            origin_lon=float(value["origin_lon"]),
            gsd_m=float(value["gsd_m"]),
            origin_pixel_x=float(value.get("origin_pixel_x", 0.0)),
            origin_pixel_y=float(value.get("origin_pixel_y", 0.0)),
            rotation_deg=float(value.get("rotation_deg", 0.0)),
            source=str(value.get("source") or value.get("georef_source") or "manual"),
            confidence=float(value.get("confidence", value.get("georef_confidence", 1.0))),
            crs=value.get("crs") or value.get("georef_crs"),
        )


def georef_to_json(georef: SimpleGeoReference | None) -> str:
    if georef is None:
        return ""
    return json.dumps(georef.to_dict(), sort_keys=True)


def georef_from_json(value: str | bytes | None) -> SimpleGeoReference | None:
    if value is None:
        return None
    if isinstance(value, bytes):
        value = value.decode("utf-8")
    if not value:
        return None
    return SimpleGeoReference.from_dict(json.loads(value))


def build_georef_from_cli(
    origin_lat: float | None,
    origin_lon: float | None,
    gsd_m: float | None,
    origin_pixel_x: float,
    origin_pixel_y: float,
    rotation_deg: float,
    source: str = "manual",
    confidence: float = 1.0,
    crs: str | None = None,
) -> SimpleGeoReference | None:
    values = [origin_lat, origin_lon, gsd_m]
    if all(value is None for value in values):
        return None
    if any(value is None for value in values):
        raise ValueError("--origin-lat, --origin-lon, and --gsd-m must be provided together")
    if gsd_m is None or gsd_m <= 0:
        raise ValueError("--gsd-m must be greater than zero")
    if not 0.0 <= float(confidence) <= 1.0:
        raise ValueError("--georef-confidence must be between 0 and 1")
    return SimpleGeoReference(
        origin_lat=float(origin_lat),
        origin_lon=float(origin_lon),
        gsd_m=float(gsd_m),
        origin_pixel_x=origin_pixel_x,
        origin_pixel_y=origin_pixel_y,
        rotation_deg=rotation_deg,
        source=source,
        confidence=float(confidence),
        crs=crs,
    )
