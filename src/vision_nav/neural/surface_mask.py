"""Build conservative offline ground-support rasters from reviewed polygons."""

import argparse
import json
from pathlib import Path

import numpy as np

from ..training_capture import sha256_file
from .map_landmarks import SURFACE_MASK_SCHEMA


def build(cog_path, annotations_path, output, *, margin_m, source, acquisition_date,
          license, alignment_evidence, support_basis):
    import rasterio
    from rasterio.features import rasterize
    from rasterio.windows import Window, bounds
    from pyproj import CRS, Transformer
    from shapely.geometry import shape, mapping, box
    from shapely.ops import transform

    if type(margin_m) not in (int, float) or not np.isfinite(margin_m) or not 0 < margin_m <= 1000:
        raise ValueError("Choose a finite positive ground/exclusion margin of at most 1000 metres")
    metadata = dict(source=source, acquisition_date=acquisition_date, license=license,
                    alignment_evidence=alignment_evidence, support_basis=support_basis)
    if any(not isinstance(v, str) or not v.strip() for v in metadata.values()):
        raise ValueError("Explicit source, date, license, alignment evidence and reviewed support basis are required")
    cog, annotations, out = Path(cog_path).resolve(), Path(annotations_path).resolve(), Path(output).resolve()
    if out.exists():
        raise ValueError("Ground-support output must be a new directory")
    if annotations.stat().st_size > 10*1024*1024:
        raise ValueError("Ground-support GeoJSON exceeds 10 MiB")
    data = json.loads(annotations.read_text())
    if (data.get("type") != "FeatureCollection" or "crs" in data
            or not isinstance(data.get("features"), list) or not 1 <= len(data["features"]) <= 1000):
        raise ValueError("Expected 1..1000 RFC7946 WGS84 polygon features without a legacy CRS")
    hashes = {str(cog): sha256_file(cog), str(annotations): sha256_file(annotations)}
    with rasterio.open(cog) as image:
        crs = CRS.from_user_input(image.crs)
        if not crs.is_projected or any(axis.unit_conversion_factor != 1 for axis in crs.axis_info[:2]):
            raise ValueError("Mask building requires imagery in a projected metre CRS")
        a = image.transform
        if a.b != 0 or a.d != 0 or a.a <= 0 or a.e >= 0:
            raise ValueError("Mask building requires north-up imagery; reproject rotated rasters first")
        if image.width*image.height > 268_435_456:
            raise ValueError("Select a map area of at most 268 million pixels")
        project = Transformer.from_crs(4326, crs, always_xy=True).transform
        # A full half-pixel diagonal protects the cell footprint, not just its center.
        effective = float(margin_m)+float(np.hypot(a.a, a.e))/2
        ground, excluded = [], []
        for feature in data["features"]:
            kind = feature.get("properties", {}).get("surface")
            if feature.get("type") != "Feature" or kind not in {"ground_support", "exclude"}:
                raise ValueError("Each polygon must declare surface=ground_support or exclude")
            geometry = shape(feature["geometry"])
            if (geometry.geom_type not in {"Polygon", "MultiPolygon"} or not geometry.is_valid or geometry.is_empty
                    or geometry.has_z or not np.isfinite(geometry.bounds).all()):
                raise ValueError("Expected valid, nonempty 2D ground/exclusion polygons")
            x0, y0, x1, y1 = geometry.bounds
            if not (-180 <= x0 <= x1 <= 180 and -90 <= y0 <= y1 <= 90) or x1-x0 > 180:
                raise ValueError("Polygon must use WGS84 lon/lat without crossing the antimeridian")
            projected = transform(project, geometry)
            if not projected.is_valid or not np.isfinite(projected.bounds).all():
                raise ValueError("Polygon reprojection is invalid")
            buffered = projected.buffer(-effective if kind == "ground_support" else effective)
            if not buffered.is_empty:
                (ground if kind == "ground_support" else excluded).append(buffered)
        if not ground:
            raise ValueError("No reviewed ground support remains after the boundary margin")
        out.mkdir(parents=True)
        raster = out / "ground-support.tif"
        profile = dict(driver="GTiff", width=image.width, height=image.height, count=1, dtype="uint8",
                       crs=image.crs, transform=a, nodata=255, compress="deflate", tiled=True,
                       blockxsize=256, blockysize=256)
        accepted = 0
        with rasterio.open(raster, "w", **profile) as dest:
            for row in range(0, image.height, 512):
                for col in range(0, image.width, 512):
                    window = Window(col, row, min(512, image.width-col), min(512, image.height-row))
                    extent = box(*bounds(window, a))
                    shapes = [(mapping(g), 1) for g in ground if g.intersects(extent)]
                    shapes += [(mapping(g), 0) for g in excluded if g.intersects(extent)]
                    size = int(window.height), int(window.width)
                    pixels = rasterize(shapes, out_shape=size, transform=image.window_transform(window),
                                       fill=0, all_touched=False, dtype="uint8") if shapes else np.zeros(size, dtype=np.uint8)
                    accepted += int(np.count_nonzero(pixels))
                    dest.write(pixels, 1, window=window)
        if not accepted:
            raise ValueError("No ground pixels overlap the selected imagery; no mask manifest was published")
        if any(sha256_file(Path(p)) != expected for p, expected in hashes.items()):
            raise ValueError("Mask source changed during compilation")
        manifest = {"schema_version": SURFACE_MASK_SCHEMA, "raster_path": raster.name,
            "sha256": sha256_file(raster), "imagery_sha256": hashes[str(cog)],
            "annotations_sha256": hashes[str(annotations)], "valid_value": 1, "unknown_policy": "reject",
            "supported_surface": "bare_earth_ground", "exclusion_margin_m": float(margin_m),
            "effective_raster_margin_m": effective, "method": "reviewed_support_eroded_exclusions_dilated",
            "pixel_policy": "cell_center_after_half_diagonal_margin", "crs": image.crs.to_string(),
            "supported_pixels": accepted, "total_pixels": image.width*image.height,
            "position_authority": False, **metadata}
        (out / "surface-mask.json").write_text(json.dumps(manifest, indent=2, allow_nan=False)+"\n")
        return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("imagery", "annotations", "output"):
        parser.add_argument("--"+name, type=Path, required=True)
    parser.add_argument("--margin-m", type=float, required=True)
    for name in ("source", "acquisition-date", "license", "alignment-evidence", "support-basis"):
        parser.add_argument("--"+name, required=True)
    args = parser.parse_args(argv)
    print(json.dumps(build(args.imagery, args.annotations, args.output, margin_m=args.margin_m,
        source=args.source, acquisition_date=args.acquisition_date, license=args.license,
        alignment_evidence=args.alignment_evidence, support_basis=args.support_basis), indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
