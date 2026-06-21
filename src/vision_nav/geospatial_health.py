from __future__ import annotations

import argparse
import json
import math
from pathlib import Path
import sqlite3
import struct
from statistics import median
from typing import Any

import numpy as np

from vision_nav.bundle_checksums import CHECKSUM_FILENAME, verify_checksum_file
from vision_nav.terrain_bundle import TerrainBundle, load_terrain_bundle


PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
TIFF_TAGS = {
    256: "image_width",
    257: "image_length",
    258: "bits_per_sample",
    259: "compression",
    262: "photometric_interpretation",
    273: "strip_offsets",
    277: "samples_per_pixel",
    278: "rows_per_strip",
    279: "strip_byte_counts",
    322: "tile_width",
    323: "tile_length",
    324: "tile_offsets",
    339: "sample_format",
    33550: "model_pixel_scale",
    33922: "model_tiepoint",
    34264: "model_transformation",
    34735: "geokey_directory",
    34736: "geodouble_params",
    34737: "geoascii_params",
}


def _add_issue(issues: list[dict[str, str]], severity: str, message: str) -> None:
    issues.append({"severity": severity, "message": message})


def _status_from_issues(issues: list[dict[str, str]]) -> str:
    if any(issue["severity"] == "error" for issue in issues):
        return "failed"
    if any(issue["severity"] == "warning" for issue in issues):
        return "degraded"
    return "passed"


def _relative(path: Path, root: Path) -> str:
    try:
        return str(path.relative_to(root)).replace("\\", "/")
    except ValueError:
        return str(path)


def _png_size(path: Path) -> tuple[int, int] | None:
    with path.open("rb") as handle:
        header = handle.read(24)
    if len(header) < 24 or not header.startswith(PNG_SIGNATURE) or header[12:16] != b"IHDR":
        return None
    return struct.unpack(">II", header[16:24])


def _read_tiff_ifds(path: Path) -> dict[str, Any]:
    raw = path.read_bytes()
    if len(raw) < 8:
        raise ValueError("TIFF header is too short")

    byte_order = raw[:2]
    if byte_order == b"II":
        endian = "<"
    elif byte_order == b"MM":
        endian = ">"
    else:
        raise ValueError("TIFF byte order marker is missing")

    version = struct.unpack(endian + "H", raw[2:4])[0]
    if version != 42:
        raise ValueError("Only classic TIFF headers are supported by the lightweight validator")

    first_ifd_offset = struct.unpack(endian + "I", raw[4:8])[0]
    ifds: list[dict[str, Any]] = []
    offset = first_ifd_offset
    visited: set[int] = set()
    while offset and offset not in visited and offset + 2 <= len(raw):
        visited.add(offset)
        entry_count = struct.unpack(endian + "H", raw[offset : offset + 2])[0]
        cursor = offset + 2
        tags: dict[str, Any] = {}
        present_tag_ids: list[int] = []
        for _ in range(entry_count):
            if cursor + 12 > len(raw):
                break
            tag, tag_type, count, value_or_offset = struct.unpack(endian + "HHII", raw[cursor : cursor + 12])
            present_tag_ids.append(tag)
            name = TIFF_TAGS.get(tag)
            if name:
                tags[name] = _decode_tiff_inline_value(raw, endian, tag_type, count, value_or_offset)
            cursor += 12
        if cursor + 4 > len(raw):
            break
        offset = struct.unpack(endian + "I", raw[cursor : cursor + 4])[0]
        ifds.append({"tags": tags, "tag_ids": sorted(present_tag_ids)})

    if not ifds:
        raise ValueError("No TIFF image file directories found")
    return {
        "format": "TIFF",
        "byte_order": "little" if endian == "<" else "big",
        "ifd_count": len(ifds),
        "first_ifd": ifds[0],
    }


def _decode_tiff_inline_value(raw: bytes, endian: str, tag_type: int, count: int, value_or_offset: int) -> Any:
    type_sizes = {1: 1, 2: 1, 3: 2, 4: 4, 5: 8, 12: 8}
    size = type_sizes.get(tag_type)
    if size is None:
        return {"type": tag_type, "count": count}

    byte_count = size * count
    if byte_count <= 4:
        data = struct.pack(endian + "I", value_or_offset)[:byte_count]
    else:
        if value_or_offset + byte_count > len(raw):
            return {"type": tag_type, "count": count, "offset": value_or_offset}
        data = raw[value_or_offset : value_or_offset + byte_count]

    if tag_type == 2:
        return data.rstrip(b"\x00").decode("ascii", errors="replace")
    if tag_type == 3:
        values = list(struct.unpack(endian + f"{count}H", data))
    elif tag_type == 4:
        values = list(struct.unpack(endian + f"{count}I", data))
    elif tag_type == 1:
        values = list(data)
    elif tag_type == 5:
        raw_values = struct.unpack(endian + f"{count * 2}I", data)
        values = [
            raw_values[index] / raw_values[index + 1] if raw_values[index + 1] else float("nan")
            for index in range(0, len(raw_values), 2)
        ]
    elif tag_type == 12:
        values = list(struct.unpack(endian + f"{count}d", data))
    else:
        return {"type": tag_type, "count": count}
    return values[0] if len(values) == 1 else values


def raster_metadata(path: Path) -> dict[str, Any]:
    suffix = path.suffix.lower()
    if suffix == ".png":
        size = _png_size(path)
        return {
            "format": "PNG",
            "width_px": size[0] if size else None,
            "height_px": size[1] if size else None,
            "metadata_readable": size is not None,
            "cog": {"status": "not_applicable", "reason": "PNG is not a Cloud Optimized GeoTIFF."},
            "gdal": gdal_raster_metadata(path),
        }
    if suffix in {".tif", ".tiff"}:
        tiff = _read_tiff_ifds(path)
        tags = tiff["first_ifd"]["tags"]
        has_tiling = "tile_width" in tags and "tile_length" in tags and "tile_offsets" in tags
        has_overviews = int(tiff["ifd_count"]) > 1
        has_geotiff = any(
            key in tags
            for key in (
                "model_pixel_scale",
                "model_tiepoint",
                "model_transformation",
                "geokey_directory",
            )
        )
        reasons: list[str] = []
        if not has_tiling:
            reasons.append("not tiled")
        if not has_overviews:
            reasons.append("no internal overviews")
        if not has_geotiff:
            reasons.append("no GeoTIFF georeference tags")
        gdal = gdal_raster_metadata(path)
        if gdal.get("available") and gdal.get("openable"):
            gdal_cog = gdal.get("cog") or {}
            if gdal_cog.get("status") == "passed":
                reasons = []
            elif gdal_cog.get("reasons"):
                reasons = sorted(set(reasons + list(gdal_cog.get("reasons") or [])))
        return {
            "format": "TIFF",
            "width_px": tags.get("image_width"),
            "height_px": tags.get("image_length"),
            "metadata_readable": True,
            "ifd_count": tiff["ifd_count"],
            "tiff_tags": tags,
            "cog": {
                "status": "passed" if not reasons else "degraded",
                "candidate": not reasons,
                "tiled": has_tiling,
                "has_overviews": has_overviews,
                "has_geotiff_tags": has_geotiff,
                "reasons": reasons,
            },
            "gdal": gdal,
        }
    return {
        "format": suffix.lstrip(".").upper() or "unknown",
        "width_px": None,
        "height_px": None,
        "metadata_readable": False,
        "cog": {"status": "not_applicable", "reason": "Unsupported raster header for lightweight inspection."},
        "gdal": gdal_raster_metadata(path),
    }


def gdal_raster_metadata(path: Path) -> dict[str, Any]:
    try:
        from osgeo import gdal  # type: ignore
    except Exception as exc:
        return {
            "available": False,
            "status": "not_available",
            "reason": f"GDAL Python bindings are not available: {exc.__class__.__name__}",
        }

    try:
        gdal.UseExceptions()
    except Exception:
        pass

    try:
        dataset = gdal.OpenEx(str(path), gdal.OF_RASTER | gdal.OF_READONLY)
    except Exception as exc:
        return {
            "available": True,
            "status": "failed",
            "openable": False,
            "error": str(exc),
            "issues": [{"severity": "error", "message": f"GDAL could not open raster: {exc}"}],
        }
    if dataset is None:
        return {
            "available": True,
            "status": "failed",
            "openable": False,
            "error": "GDAL returned no dataset.",
            "issues": [{"severity": "error", "message": "GDAL returned no dataset."}],
        }

    driver = dataset.GetDriver().ShortName if dataset.GetDriver() else None
    projection = dataset.GetProjectionRef() or ""
    geotransform = _gdal_geotransform(dataset)
    band = dataset.GetRasterBand(1) if dataset.RasterCount else None
    block_size = tuple(int(value) for value in band.GetBlockSize()) if band else None
    overview_count = int(band.GetOverviewCount()) if band else 0
    image_structure = dataset.GetMetadata("IMAGE_STRUCTURE") or {}
    layout_metadata = dataset.GetMetadata("LAYOUT") or {}
    issues: list[dict[str, str]] = []

    if not projection:
        _add_issue(issues, "warning", "GDAL reports no raster projection.")
    if not geotransform:
        _add_issue(issues, "warning", "GDAL reports no geotransform.")

    cog = _gdal_cog_health(
        driver=driver,
        block_size=block_size,
        overview_count=overview_count,
        projection=projection,
        geotransform=geotransform,
        image_structure=image_structure,
        layout_metadata=layout_metadata,
    )
    for issue in cog["issues"]:
        if issue.get("severity") == "warning":
            issues.append(issue)

    return {
        "available": True,
        "status": _status_from_issues(issues),
        "openable": True,
        "driver": driver,
        "width_px": int(dataset.RasterXSize),
        "height_px": int(dataset.RasterYSize),
        "band_count": int(dataset.RasterCount),
        "projection_present": bool(projection),
        "projection_wkt_sample": projection[:160] if projection else None,
        "geotransform": list(geotransform) if geotransform else None,
        "block_size": list(block_size) if block_size else None,
        "overview_count": overview_count,
        "image_structure": image_structure,
        "layout_metadata": layout_metadata,
        "cog": cog,
        "issues": issues,
    }


def _gdal_geotransform(dataset: Any) -> tuple[float, ...] | None:
    try:
        transform = dataset.GetGeoTransform(can_return_null=True)
    except TypeError:
        try:
            transform = dataset.GetGeoTransform()
        except Exception:
            return None
        if transform == (0.0, 1.0, 0.0, 0.0, 0.0, 1.0):
            return None
    except Exception:
        return None
    return tuple(float(value) for value in transform) if transform else None


def _gdal_cog_health(
    *,
    driver: str | None,
    block_size: tuple[int, int] | None,
    overview_count: int,
    projection: str,
    geotransform: tuple[float, ...] | None,
    image_structure: dict[str, str],
    layout_metadata: dict[str, str],
) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    reasons: list[str] = []
    is_tiff = driver in {"GTiff", "COG"}
    tiled = bool(block_size and block_size[0] > 1 and block_size[1] > 1)
    layout = (layout_metadata.get("LAYOUT") or image_structure.get("LAYOUT") or "").upper()

    if not is_tiff:
        reasons.append(f"driver is {driver or 'unknown'}, not GTiff/COG")
    if not tiled:
        reasons.append("not tiled")
    if overview_count <= 0:
        reasons.append("no internal overviews")
    if not projection:
        reasons.append("no projection")
    if not geotransform:
        reasons.append("no geotransform")

    for reason in reasons:
        _add_issue(issues, "warning", f"GDAL COG readiness: {reason}.")

    return {
        "status": "passed" if not reasons else "degraded",
        "candidate": not reasons,
        "driver": driver,
        "layout": layout or None,
        "tiled": tiled,
        "overview_count": overview_count,
        "has_projection": bool(projection),
        "has_geotransform": bool(geotransform),
        "reasons": reasons,
        "issues": issues,
    }


def georef_bounds(bundle: TerrainBundle, raster: dict[str, Any]) -> dict[str, Any] | None:
    if bundle.georef is None:
        return None
    width = raster.get("width_px")
    height = raster.get("height_px")
    source_region = bundle.manifest.get("source_region") or {}
    width = int(width or source_region.get("width_px") or 0)
    height = int(height or source_region.get("height_px") or 0)
    if width <= 0 or height <= 0:
        return None
    corners = [
        bundle.georef.pixel_to_latlon(0, 0),
        bundle.georef.pixel_to_latlon(width, 0),
        bundle.georef.pixel_to_latlon(width, height),
        bundle.georef.pixel_to_latlon(0, height),
    ]
    lats = [lat for lat, _ in corners]
    lons = [lon for _, lon in corners]
    local_corners = [
        bundle.georef.pixel_to_local_m(0, 0),
        bundle.georef.pixel_to_local_m(width, 0),
        bundle.georef.pixel_to_local_m(width, height),
        bundle.georef.pixel_to_local_m(0, height),
    ]
    east = [value[0] for value in local_corners]
    north = [value[1] for value in local_corners]
    return {
        "lat_min": min(lats),
        "lat_max": max(lats),
        "lon_min": min(lons),
        "lon_max": max(lons),
        "east_min_m": min(east),
        "east_max_m": max(east),
        "north_min_m": min(north),
        "north_max_m": max(north),
        "width_m": max(east) - min(east),
        "height_m": max(north) - min(north),
    }


def _gdal_validation_issues(raster: dict[str, Any], *, label: str) -> list[dict[str, str]]:
    if raster.get("format") != "TIFF":
        return []
    gdal = raster.get("gdal")
    if not isinstance(gdal, dict) or not gdal.get("available"):
        return []
    issues: list[dict[str, str]] = []
    if gdal.get("status") == "failed" or gdal.get("openable") is False:
        _add_issue(issues, "error", f"GDAL could not validate {label}.")
        return issues
    for issue in gdal.get("issues") or []:
        message = issue.get("message") if isinstance(issue, dict) else None
        severity = issue.get("severity", "warning") if isinstance(issue, dict) else "warning"
        if message:
            _add_issue(issues, severity, f"{label}: {message}")
    return issues


def stac_health(bundle: TerrainBundle) -> dict[str, Any]:
    path = bundle.bundle_dir / "manifest.stac.json"
    issues: list[dict[str, str]] = []
    if not path.exists():
        _add_issue(issues, "warning", "Missing manifest.stac.json.")
        return {"path": str(path), "exists": False, "issues": issues, "status": _status_from_issues(issues)}

    try:
        raw = json.loads(path.read_text())
    except Exception as exc:
        _add_issue(issues, "error", f"STAC manifest is not valid JSON: {exc}")
        return {"path": str(path), "exists": True, "issues": issues, "status": _status_from_issues(issues)}

    if raw.get("stac_version") != "1.0.0":
        _add_issue(issues, "warning", "STAC manifest should use stac_version 1.0.0.")
    if raw.get("type") != "Feature":
        _add_issue(issues, "error", "STAC manifest type must be Feature.")
    assets = raw.get("assets")
    if not isinstance(assets, dict) or not assets:
        _add_issue(issues, "error", "STAC manifest has no assets.")
        assets = {}
    for name, asset in assets.items():
        href = asset.get("href") if isinstance(asset, dict) else None
        if not href:
            _add_issue(issues, "error", f"STAC asset {name} has no href.")
            continue
        asset_path = bundle.bundle_dir / href
        if not asset_path.exists():
            _add_issue(issues, "error", f"STAC asset {name} is missing: {href}")
    return {
        "path": str(path),
        "exists": True,
        "asset_count": len(assets),
        "issues": issues,
        "status": _status_from_issues(issues),
    }


def checksum_health(bundle: TerrainBundle) -> dict[str, Any]:
    summary = verify_checksum_file(bundle.bundle_dir)
    if summary["status"] == "missing":
        return {
            "status": "missing",
            "required": False,
            "checksum_file": summary["checksum_file"],
            "entry_count": 0,
            "covered_file_count": 0,
            "extra_file_count": 0,
            "issues": [
                {
                    "severity": "info",
                    "message": f"Missing {CHECKSUM_FILENAME}; strict transfer validation can require it.",
                }
            ],
        }

    issues: list[dict[str, str]] = []
    if summary["status"] != "passed":
        _add_issue(issues, "error", f"Checksum verification failed: {summary['checksum_file']}")
    extra_files = list(summary.get("extra_files") or [])
    ignored_entries = list(summary.get("ignored_entries") or [])
    return {
        "status": summary["status"],
        "required": False,
        "checksum_file": summary["checksum_file"],
        "entry_count": int(summary.get("entry_count") or 0),
        "covered_file_count": int(summary.get("entry_count") or 0) - len(ignored_entries),
        "missing": summary.get("missing") or [],
        "mismatched": summary.get("mismatched") or [],
        "extra_file_count": len(extra_files),
        "extra_files_sample": extra_files[:10],
        "ignored_volatile_entries": ignored_entries,
        "issues": issues,
    }


def source_provenance(bundle: TerrainBundle) -> dict[str, Any]:
    manifest = bundle.manifest
    source_region = manifest.get("source_region") if isinstance(manifest.get("source_region"), dict) else {}
    orthophoto = manifest.get("orthophoto") if isinstance(manifest.get("orthophoto"), dict) else {}
    terrain = manifest.get("terrain_bundle") if isinstance(manifest.get("terrain_bundle"), dict) else {}
    features = manifest.get("features") if isinstance(manifest.get("features"), dict) else {}
    pipeline = manifest.get("pipeline") if isinstance(manifest.get("pipeline"), dict) else {}
    georef_source = (
        source_region.get("georef_source")
        or orthophoto.get("georef_source")
        or orthophoto.get("source")
        or (bundle.georef.source if bundle.georef else None)
    )
    georef_confidence = (
        source_region.get("georef_confidence")
        if source_region.get("georef_confidence") is not None
        else orthophoto.get("georef_confidence")
    )
    if georef_confidence is None and bundle.georef:
        georef_confidence = bundle.georef.confidence
    return {
        "bundle_id": manifest.get("bundle_id"),
        "map_source": source_region.get("source") or "unknown",
        "map_id": source_region.get("id"),
        "map_name": source_region.get("name"),
        "metadata_path": source_region.get("metadata_path"),
        "original_file": source_region.get("original_file"),
        "orthophoto_path": _relative(bundle.orthophoto_path, bundle.bundle_dir),
        "width_px": source_region.get("width_px"),
        "height_px": source_region.get("height_px"),
        "zoom": source_region.get("zoom"),
        "georef_source": georef_source,
        "georef_confidence": georef_confidence,
        "georef_crs": source_region.get("georef_crs") or orthophoto.get("georef_crs") or bundle.crs,
        "gsd_m": source_region.get("gsd_m_per_px") or orthophoto.get("gsd_m") or bundle.gsd_m,
        "origin_lat": source_region.get("origin_lat") or orthophoto.get("origin_lat"),
        "origin_lon": source_region.get("origin_lon") or orthophoto.get("origin_lon"),
        "terrain_builder": terrain.get("builder"),
        "terrain_built_at": terrain.get("built_at"),
        "feature_method": features.get("method"),
        "max_features": features.get("max_features"),
        "pipeline": pipeline.get("mode") or pipeline.get("pipeline"),
    }


def elevation_health(bundle: TerrainBundle) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    assets: list[dict[str, Any]] = []
    for kind, rel in sorted(bundle.elevation_assets.items()):
        path = bundle.bundle_dir / rel
        asset: dict[str, Any] = {
            "kind": kind,
            "path": rel,
            "exists": path.exists(),
            "status": "missing",
        }
        if not path.exists():
            _add_issue(issues, "error", f"Declared {kind.upper()} asset is missing: {rel}")
            assets.append(asset)
            continue
        try:
            raster = raster_metadata(path)
            asset["raster"] = raster
            asset["status"] = "passed" if raster.get("metadata_readable") else "degraded"
            cog = raster.get("cog") or {}
            asset["geotiff_tags"] = bool(cog.get("has_geotiff_tags"))
            asset["cog_ready"] = bool(cog.get("candidate"))
            gdal = raster.get("gdal") if isinstance(raster.get("gdal"), dict) else {}
            asset["gdal_status"] = gdal.get("status")
            asset["gdal_cog_ready"] = bool((gdal.get("cog") or {}).get("candidate")) if gdal.get("available") else None
            for issue in _gdal_validation_issues(raster, label=f"{kind.upper()} asset"):
                _add_issue(issues, issue["severity"], issue["message"])
        except Exception as exc:
            asset["status"] = "degraded"
            asset["error"] = str(exc)
            _add_issue(issues, "warning", f"Could not inspect {kind.upper()} raster metadata: {exc}")
        assets.append(asset)

    asset_count = len(assets)
    usable_assets = [asset for asset in assets if asset.get("exists") and asset.get("status") in {"passed", "degraded"}]
    asset_kinds = {str(asset["kind"]) for asset in usable_assets}
    return {
        "status": "not_provided" if asset_count == 0 else _status_from_issues(issues),
        "required": False,
        "asset_count": asset_count,
        "dem_present": "dem" in asset_kinds,
        "dsm_present": "dsm" in asset_kinds,
        "vertical_sanity_ready": bool(usable_assets and bundle.georef is not None),
        "assets": assets,
        "issues": issues,
    }


def _as_list(value: Any) -> list[Any]:
    if value is None:
        return []
    if isinstance(value, list):
        return value
    return [value]


def _first_number(value: Any, default: float | int | None = None) -> float | int | None:
    values = _as_list(value)
    if not values:
        return default
    try:
        return values[0]
    except IndexError:
        return default


def read_scalar_elevation_array(path: Path) -> np.ndarray:
    try:
        return _read_scalar_tiff_array(path)
    except Exception as lightweight_error:
        try:
            import tifffile  # type: ignore

            array = tifffile.imread(path)
        except Exception as tifffile_error:
            raise ValueError(
                f"Could not read uncompressed scalar elevation raster ({lightweight_error}); "
                f"tifffile fallback also failed ({tifffile_error})"
            ) from tifffile_error
        if array.ndim > 2:
            array = array[..., 0]
        return np.asarray(array, dtype=float)


def _read_scalar_tiff_array(path: Path) -> np.ndarray:
    raw = path.read_bytes()
    if raw[:2] == b"II":
        endian = "<"
    elif raw[:2] == b"MM":
        endian = ">"
    else:
        raise ValueError("TIFF byte order marker is missing")
    tiff = _read_tiff_ifds(path)
    tags = tiff["first_ifd"]["tags"]
    width = int(tags.get("image_width") or 0)
    height = int(tags.get("image_length") or 0)
    if width <= 0 or height <= 0:
        raise ValueError("TIFF raster width/height is missing")
    compression = int(_first_number(tags.get("compression"), 1) or 1)
    if compression != 1:
        raise ValueError(f"Only uncompressed TIFF elevation rasters are supported by the lightweight reader; compression={compression}")
    samples_per_pixel = int(_first_number(tags.get("samples_per_pixel"), 1) or 1)
    if samples_per_pixel != 1:
        raise ValueError("Only single-band elevation rasters are supported by the lightweight reader")
    bits_per_sample = int(_first_number(tags.get("bits_per_sample"), 8) or 8)
    sample_format = int(_first_number(tags.get("sample_format"), 1) or 1)
    offsets = [int(value) for value in _as_list(tags.get("strip_offsets"))]
    byte_counts = [int(value) for value in _as_list(tags.get("strip_byte_counts"))]
    if not offsets or not byte_counts:
        raise ValueError("TIFF strip offsets/byte counts are missing")
    if len(byte_counts) == 1 and len(offsets) > 1:
        byte_counts = byte_counts * len(offsets)
    if len(offsets) != len(byte_counts):
        raise ValueError("TIFF strip offset/count lengths do not match")

    dtype_map = {
        (8, 1): np.dtype("u1"),
        (16, 1): np.dtype(endian + "u2"),
        (32, 1): np.dtype(endian + "u4"),
        (8, 2): np.dtype("i1"),
        (16, 2): np.dtype(endian + "i2"),
        (32, 2): np.dtype(endian + "i4"),
        (32, 3): np.dtype(endian + "f4"),
        (64, 3): np.dtype(endian + "f8"),
    }
    dtype = dtype_map.get((bits_per_sample, sample_format))
    if dtype is None:
        raise ValueError(f"Unsupported TIFF elevation sample format: bits={bits_per_sample} sample_format={sample_format}")

    payload = bytearray()
    for offset, byte_count in zip(offsets, byte_counts):
        if offset < 0 or byte_count < 0 or offset + byte_count > len(raw):
            raise ValueError("TIFF strip data points outside the file")
        payload.extend(raw[offset : offset + byte_count])
    array = np.frombuffer(bytes(payload), dtype=dtype)
    required = width * height
    if array.size < required:
        raise ValueError(f"TIFF elevation data is too short: {array.size} samples for {width}x{height}")
    return array[:required].astype(float).reshape((height, width))


def _mission_plan_path(bundle: TerrainBundle) -> Path | None:
    mission = bundle.manifest.get("mission") if isinstance(bundle.manifest.get("mission"), dict) else {}
    candidates = [
        mission.get("desktop_plan_path"),
        mission.get("mission_plan_path"),
        "mission/mission_plan.json",
    ]
    for candidate in candidates:
        if isinstance(candidate, str) and candidate:
            path = bundle.bundle_dir / candidate
            if path.exists():
                return path
    return None


def _mission_items_from_bundle(bundle: TerrainBundle) -> tuple[list[dict[str, Any]], str | None]:
    path = _mission_plan_path(bundle)
    if path is None:
        return [], None
    raw = json.loads(path.read_text())
    mission = raw.get("mission") if isinstance(raw.get("mission"), dict) else {}
    default_altitude = mission.get("altitude_m")
    items: list[dict[str, Any]] = []
    for item in mission.get("items") or []:
        if not isinstance(item, dict):
            continue
        lat = item.get("lat")
        lon = item.get("lon")
        altitude = item.get("altitudeM", item.get("altitude_m", default_altitude))
        if lat is None or lon is None or altitude is None:
            continue
        try:
            items.append(
                {
                    "type": item.get("type") or "waypoint",
                    "lat": float(lat),
                    "lon": float(lon),
                    "altitude_m": float(altitude),
                }
            )
        except (TypeError, ValueError):
            continue
    return items, _relative(path, bundle.bundle_dir)


def _mission_samples(bundle: TerrainBundle, items: list[dict[str, Any]], spacing_m: float = 25.0) -> tuple[list[dict[str, float]], float]:
    if bundle.georef is None or not items:
        return [], 0.0
    if len(items) == 1:
        item = items[0]
        return [{"lat": item["lat"], "lon": item["lon"], "altitude_m": item["altitude_m"], "distance_m": 0.0}], 0.0

    samples: list[dict[str, float]] = []
    cumulative_m = 0.0
    for index, (start, end) in enumerate(zip(items, items[1:])):
        start_local = bundle.georef.latlon_to_local_m(start["lat"], start["lon"])
        end_local = bundle.georef.latlon_to_local_m(end["lat"], end["lon"])
        segment_m = math.hypot(end_local[0] - start_local[0], end_local[1] - start_local[1])
        steps = max(int(math.ceil(segment_m / max(spacing_m, 1.0))), 1)
        first_step = 0 if index == 0 else 1
        for step in range(first_step, steps + 1):
            t = step / steps
            samples.append(
                {
                    "lat": start["lat"] + (end["lat"] - start["lat"]) * t,
                    "lon": start["lon"] + (end["lon"] - start["lon"]) * t,
                    "altitude_m": start["altitude_m"] + (end["altitude_m"] - start["altitude_m"]) * t,
                    "distance_m": cumulative_m + segment_m * t,
                }
            )
        cumulative_m += segment_m
    return samples, cumulative_m


def _geotiff_pixel_from_latlon(raster: dict[str, Any], lat: float, lon: float) -> tuple[float, float] | None:
    tags = raster.get("tiff_tags") if isinstance(raster.get("tiff_tags"), dict) else {}
    scale = _as_list(tags.get("model_pixel_scale"))
    tiepoint = _as_list(tags.get("model_tiepoint"))
    if len(scale) < 2 or len(tiepoint) < 6:
        return None
    try:
        tie_x_px = float(tiepoint[0])
        tie_y_px = float(tiepoint[1])
        model_x = float(tiepoint[3])
        model_y = float(tiepoint[4])
        scale_x = float(scale[0])
        scale_y = float(scale[1])
    except (TypeError, ValueError):
        return None
    if not (-180.0 <= model_x <= 180.0 and -90.0 <= model_y <= 90.0 and scale_x > 0 and scale_y > 0):
        return None
    return tie_x_px + (lon - model_x) / scale_x, tie_y_px + (model_y - lat) / scale_y


def _same_grid_pixel_from_latlon(
    bundle: TerrainBundle,
    raster: dict[str, Any],
    orthophoto_raster: dict[str, Any],
    lat: float,
    lon: float,
) -> tuple[float, float] | None:
    if bundle.georef is None:
        return None
    source_region = bundle.manifest.get("source_region") if isinstance(bundle.manifest.get("source_region"), dict) else {}
    map_width = int(orthophoto_raster.get("width_px") or source_region.get("width_px") or 0)
    map_height = int(orthophoto_raster.get("height_px") or source_region.get("height_px") or 0)
    raster_width = int(raster.get("width_px") or 0)
    raster_height = int(raster.get("height_px") or 0)
    if map_width <= 0 or map_height <= 0 or raster_width != map_width or raster_height != map_height:
        return None
    return bundle.georef.latlon_to_pixel(lat, lon)


def _sample_elevation_nearest(array: np.ndarray, x_px: float, y_px: float) -> float | None:
    x = int(round(x_px))
    y = int(round(y_px))
    if y < 0 or y >= array.shape[0] or x < 0 or x >= array.shape[1]:
        return None
    value = float(array[y, x])
    return value if math.isfinite(value) else None


def _downsample_profile_points(points: list[dict[str, float]], limit: int = 96) -> list[dict[str, float]]:
    if len(points) <= limit:
        return points
    if limit <= 2:
        return points[:limit]
    selected: list[dict[str, float]] = []
    last_index = len(points) - 1
    for slot in range(limit):
        index = round(slot * last_index / (limit - 1))
        selected.append(points[index])
    return selected


def terrain_profile_health(
    bundle: TerrainBundle,
    elevation: dict[str, Any],
    orthophoto_raster: dict[str, Any],
) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    if not elevation.get("asset_count"):
        return {"status": "not_provided", "required": False, "issues": issues}
    if bundle.georef is None:
        _add_issue(issues, "warning", "Terrain profile needs a map georeference.")
        return {"status": "degraded", "required": False, "issues": issues}

    items, mission_path = _mission_items_from_bundle(bundle)
    if not items:
        return {
            "status": "not_available",
            "required": False,
            "reason": "No desktop mission plan found in the bundle.",
            "issues": issues,
        }

    assets = elevation.get("assets") if isinstance(elevation.get("assets"), list) else []
    surface_asset = next((asset for asset in assets if asset.get("kind") == "dsm" and asset.get("exists")), None)
    surface_asset = surface_asset or next((asset for asset in assets if asset.get("kind") == "dem" and asset.get("exists")), None)
    if surface_asset is None:
        _add_issue(issues, "warning", "No readable DEM/DSM asset is available for terrain profile sampling.")
        return {"status": "degraded", "required": False, "issues": issues}

    surface_rel = str(surface_asset.get("path") or "")
    surface_path = bundle.bundle_dir / surface_rel
    raster = surface_asset.get("raster") if isinstance(surface_asset.get("raster"), dict) else raster_metadata(surface_path)
    try:
        array = read_scalar_elevation_array(surface_path)
    except Exception as exc:
        _add_issue(issues, "warning", f"Could not read elevation samples from {surface_asset.get('kind', 'asset').upper()}: {exc}")
        return {
            "status": "degraded",
            "required": False,
            "mission_path": mission_path,
            "surface_source": surface_asset.get("kind"),
            "surface_path": surface_rel,
            "reason": "Elevation raster metadata was readable, but sample values were not.",
            "issues": issues,
        }

    samples, path_length_m = _mission_samples(bundle, items)
    if not samples:
        return {"status": "not_available", "required": False, "reason": "Mission path has no sampleable points.", "issues": issues}

    sampled: list[dict[str, float]] = []
    transform = "geotiff_tiepoint_scale"
    for sample in samples:
        pixel = _geotiff_pixel_from_latlon(raster, sample["lat"], sample["lon"])
        if pixel is None:
            transform = "same_grid_orthophoto"
            pixel = _same_grid_pixel_from_latlon(bundle, raster, orthophoto_raster, sample["lat"], sample["lon"])
        if pixel is None:
            _add_issue(
                issues,
                "warning",
                "Terrain profile needs DEM/DSM GeoTIFF georeference tags or an elevation raster on the same grid as the orthophoto.",
            )
            return {
                "status": "degraded",
                "required": False,
                "mission_path": mission_path,
                "surface_source": surface_asset.get("kind"),
                "surface_path": surface_rel,
                "reason": "Elevation raster cannot be mapped to mission coordinates without GDAL or same-grid metadata.",
                "issues": issues,
            }
        elevation_m = _sample_elevation_nearest(array, pixel[0], pixel[1])
        if elevation_m is None:
            continue
        sampled.append({**sample, "elevation_m": elevation_m})

    if not sampled:
        _add_issue(issues, "warning", "Mission path does not overlap readable DEM/DSM pixels.")
        return {
            "status": "degraded",
            "required": False,
            "mission_path": mission_path,
            "surface_source": surface_asset.get("kind"),
            "surface_path": surface_rel,
            "sample_count": len(samples),
            "sampled_count": 0,
            "issues": issues,
        }
    if len(sampled) < len(samples):
        missing_ratio = 1.0 - len(sampled) / max(len(samples), 1)
        if missing_ratio > 0.2:
            _add_issue(issues, "warning", f"{missing_ratio:.0%} of terrain profile samples fell outside readable elevation pixels.")

    terrain_values = [sample["elevation_m"] for sample in sampled]
    altitude_values = [sample["altitude_m"] for sample in sampled]
    reference_elevation_m = terrain_values[0]
    agl_values = [
        sample["altitude_m"] - (sample["elevation_m"] - reference_elevation_m)
        for sample in sampled
    ]
    profile_points = [
        {
            "distance_m": sample["distance_m"],
            "terrain_elevation_m": sample["elevation_m"],
            "mission_altitude_m": sample["altitude_m"],
            "estimated_agl_m": agl,
        }
        for sample, agl in zip(sampled, agl_values)
    ]
    min_agl_m = min(agl_values)
    min_agl_to_gsd_ratio = min_agl_m / bundle.georef.gsd_m if bundle.georef.gsd_m > 0 else None
    if min_agl_m <= 0:
        _add_issue(issues, "error", "Mission path intersects terrain/surface under the relative AGL assumption.")
    elif min_agl_m < 10.0:
        _add_issue(issues, "warning", f"Minimum estimated AGL is low: {min_agl_m:.1f} m.")
    if min_agl_to_gsd_ratio is not None and min_agl_to_gsd_ratio < 30.0:
        _add_issue(
            issues,
            "warning",
            f"Map GSD may be coarse for the planned low-altitude profile: min AGL/GSD ratio is {min_agl_to_gsd_ratio:.1f}.",
        )

    return {
        "status": _status_from_issues(issues),
        "required": False,
        "mission_path": mission_path,
        "mission_item_count": len(items),
        "sample_count": len(samples),
        "sampled_count": len(sampled),
        "sample_spacing_m": 25.0,
        "path_length_m": path_length_m,
        "surface_source": surface_asset.get("kind"),
        "surface_path": surface_rel,
        "coordinate_mapping": transform,
        "altitude_reference": "mission_altitude_relative_to_first_sampled_surface",
        "reference_elevation_m": reference_elevation_m,
        "terrain_elevation_m": {
            "min": min(terrain_values),
            "max": max(terrain_values),
            "start": terrain_values[0],
            "end": terrain_values[-1],
            "relief": max(terrain_values) - min(terrain_values),
        },
        "mission_altitude_m": {
            "min": min(altitude_values),
            "max": max(altitude_values),
        },
        "estimated_agl_m": {
            "min": min_agl_m,
            "max": max(agl_values),
            "mean": sum(agl_values) / len(agl_values),
        },
        "min_agl_to_map_gsd_ratio": min_agl_to_gsd_ratio,
        "preview_points": _downsample_profile_points(profile_points),
        "issues": issues,
    }


def tile_index_health(bundle: TerrainBundle) -> dict[str, Any]:
    issues: list[dict[str, str]] = []
    if bundle.tile_index_path is None:
        _add_issue(issues, "warning", "Manifest has no terrain_bundle.tile_index_path.")
        return {"path": None, "exists": False, "issues": issues, "status": _status_from_issues(issues)}
    if not bundle.tile_index_path.exists():
        _add_issue(issues, "error", f"Missing terrain tile index: {bundle.tile_index_path}")
        return {"path": str(bundle.tile_index_path), "exists": False, "issues": issues, "status": _status_from_issues(issues)}

    with sqlite3.connect(bundle.tile_index_path) as conn:
        conn.row_factory = sqlite3.Row
        table = conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='tiles'"
        ).fetchone()
        if table is None:
            _add_issue(issues, "error", "Tile index has no tiles table.")
            return {"path": str(bundle.tile_index_path), "exists": True, "issues": issues, "status": _status_from_issues(issues)}
        row = conn.execute(
            """
            SELECT
              COUNT(*) AS tile_count,
              COALESCE(SUM(keypoint_count), 0) AS feature_count,
              MIN(min_east_m) AS min_east_m,
              MAX(max_east_m) AS max_east_m,
              MIN(min_north_m) AS min_north_m,
              MAX(max_north_m) AS max_north_m
            FROM tiles
            """
        ).fetchone()
        methods = [str(value[0]) for value in conn.execute("SELECT DISTINCT method FROM tiles ORDER BY method").fetchall()]
        tile_rows = conn.execute(
            """
            SELECT
              tile_id, row, col, x0_px, y0_px, x1_px, y1_px,
              min_east_m, max_east_m, min_north_m, max_north_m,
              image_path, descriptor_path, keypoint_count
            FROM tiles
            ORDER BY tile_id
            """
        ).fetchall()

    missing_assets: list[str] = []
    for asset_row in tile_rows:
        for key in ("image_path", "descriptor_path"):
            rel = str(asset_row[key])
            if not (bundle.bundle_dir / rel).exists():
                missing_assets.append(rel)
                if len(missing_assets) >= 5:
                    break
        if len(missing_assets) >= 5:
            break
    if missing_assets:
        _add_issue(issues, "error", f"Tile index references missing assets: {', '.join(missing_assets)}")

    tile_count = int(row["tile_count"] or 0)
    if tile_count <= 0:
        _add_issue(issues, "error", "Tile index has no tiles.")
    feature_count = int(row["feature_count"] or 0)
    if feature_count <= 0:
        _add_issue(issues, "warning", "Tile index has no extracted features.")
    quality = map_quality_from_tiles(tile_rows, tile_count=tile_count, feature_count=feature_count)
    if quality["low_texture_ratio"] > 0.25:
        _add_issue(
            issues,
            "warning",
            f"{quality['low_texture_tile_count']} of {tile_count} tiles have low feature density.",
        )

    return {
        "path": str(bundle.tile_index_path),
        "exists": True,
        "tile_count": tile_count,
        "feature_count": feature_count,
        "methods": methods,
        "local_bounds_m": {
            "east_min": row["min_east_m"],
            "east_max": row["max_east_m"],
            "north_min": row["min_north_m"],
            "north_max": row["max_north_m"],
        },
        "quality": quality,
        "missing_assets": missing_assets,
        "issues": issues,
        "status": _status_from_issues(issues),
    }


def map_quality_from_tiles(tile_rows: list[sqlite3.Row], *, tile_count: int, feature_count: int) -> dict[str, Any]:
    density_threshold_per_mpx = 150.0
    densities: list[float] = []
    low_texture_tiles: list[str] = []
    heatmap_cells: list[dict[str, Any]] = []
    heatmap_cell_limit = 512
    max_row = -1
    max_col = -1
    for row in tile_rows:
        width_px = max(int(row["x1_px"]) - int(row["x0_px"]), 1)
        height_px = max(int(row["y1_px"]) - int(row["y0_px"]), 1)
        area_mpx = (width_px * height_px) / 1_000_000.0
        density = float(row["keypoint_count"]) / max(area_mpx, 1e-9)
        densities.append(density)
        tile_quality = _tile_quality_class(density, density_threshold_per_mpx)
        if density < density_threshold_per_mpx:
            low_texture_tiles.append(str(row["tile_id"]))
        tile_row = int(row["row"])
        tile_col = int(row["col"])
        max_row = max(max_row, tile_row)
        max_col = max(max_col, tile_col)
        if len(heatmap_cells) < heatmap_cell_limit:
            heatmap_cells.append(
                {
                    "tile_id": str(row["tile_id"]),
                    "row": tile_row,
                    "col": tile_col,
                    "keypoint_count": int(row["keypoint_count"] or 0),
                    "feature_density_per_mpx": density,
                    "quality": tile_quality,
                    "local_bounds_m": {
                        "east_min": row["min_east_m"],
                        "east_max": row["max_east_m"],
                        "north_min": row["min_north_m"],
                        "north_max": row["max_north_m"],
                    },
                }
            )

    if not densities:
        density_summary = {"min": 0.0, "median": 0.0, "mean": 0.0, "max": 0.0}
    else:
        density_summary = {
            "min": min(densities),
            "median": median(densities),
            "mean": sum(densities) / len(densities),
            "max": max(densities),
        }

    if tile_count <= 64 and feature_count <= 100_000:
        runtime_cost = "low"
    elif tile_count <= 256 and feature_count <= 400_000:
        runtime_cost = "moderate"
    else:
        runtime_cost = "high"

    return {
        "feature_density_per_mpx": density_summary,
        "low_texture_threshold_per_mpx": density_threshold_per_mpx,
        "low_texture_tile_count": len(low_texture_tiles),
        "low_texture_ratio": (len(low_texture_tiles) / tile_count) if tile_count else 1.0,
        "low_texture_tile_ids_sample": low_texture_tiles[:10],
        "estimated_pi_runtime_cost": runtime_cost,
        "heatmap": {
            "row_count": max_row + 1 if max_row >= 0 else 0,
            "col_count": max_col + 1 if max_col >= 0 else 0,
            "cell_count": len(heatmap_cells),
            "omitted_tile_count": max(tile_count - len(heatmap_cells), 0),
            "quality_legend": {
                "low": f"< {density_threshold_per_mpx:.0f} features/Mpx",
                "fair": f"{density_threshold_per_mpx:.0f}-{density_threshold_per_mpx * 2:.0f} features/Mpx",
                "good": f"{density_threshold_per_mpx * 2:.0f}-{density_threshold_per_mpx * 5:.0f} features/Mpx",
                "dense": f">= {density_threshold_per_mpx * 5:.0f} features/Mpx",
            },
            "cells": heatmap_cells,
        },
    }


def _tile_quality_class(density_per_mpx: float, low_threshold: float) -> str:
    if density_per_mpx < low_threshold:
        return "low"
    if density_per_mpx < low_threshold * 2:
        return "fair"
    if density_per_mpx < low_threshold * 5:
        return "good"
    return "dense"


def geospatial_health_report(bundle_path: str | Path) -> dict[str, Any]:
    bundle = load_terrain_bundle(bundle_path)
    issues: list[dict[str, str]] = []

    if not bundle.orthophoto_path.exists():
        _add_issue(issues, "error", f"Missing orthophoto: {bundle.orthophoto_path}")
        raster = {"format": "missing", "metadata_readable": False}
    else:
        try:
            raster = raster_metadata(bundle.orthophoto_path)
        except Exception as exc:
            raster = {"format": bundle.orthophoto_path.suffix.lstrip(".").upper(), "metadata_readable": False, "error": str(exc)}
            _add_issue(issues, "warning", f"Could not inspect raster metadata: {exc}")

    if bundle.georef is None:
        _add_issue(issues, "error", "Missing georeference; terrain map upload should be blocked.")
    else:
        if bundle.georef.gsd_m <= 0:
            _add_issue(issues, "error", f"Invalid GSD: {bundle.georef.gsd_m}")
        if not bundle.crs:
            _add_issue(issues, "warning", "CRS is missing; prefer EPSG-tagged GeoTIFF/COG or STAC metadata.")
        if bundle.georef.confidence < 0.7:
            _add_issue(issues, "warning", f"Low georeference confidence: {bundle.georef.confidence:.2f}")

    cog = raster.get("cog") or {}
    if cog.get("status") == "degraded":
        _add_issue(issues, "warning", f"GeoTIFF is not COG-ready: {', '.join(cog.get('reasons') or [])}")
    for issue in _gdal_validation_issues(raster, label="Orthophoto"):
        issues.append(issue)

    stac = stac_health(bundle)
    tile_index = tile_index_health(bundle)
    checksums = checksum_health(bundle)
    elevation = elevation_health(bundle)
    terrain_profile = terrain_profile_health(bundle, elevation, raster)
    provenance = source_provenance(bundle)
    for child in (stac, tile_index, checksums, elevation, terrain_profile):
        for issue in child.get("issues", []):
            if issue.get("severity") != "info":
                issues.append(issue)

    report = {
        "bundle_dir": str(bundle.bundle_dir),
        "bundle_id": bundle.manifest.get("bundle_id"),
        "orthophoto_path": str(bundle.orthophoto_path),
        "source_provenance": provenance,
        "raster": raster,
        "georef": {
            "source": bundle.georef.source if bundle.georef else None,
            "confidence": bundle.georef.confidence if bundle.georef else None,
            "crs": bundle.crs,
            "gsd_m": bundle.gsd_m,
            "local_origin": (bundle.manifest.get("terrain_bundle") or {}).get("local_origin"),
            "bounds": georef_bounds(bundle, raster),
        },
        "stac": stac,
        "tile_index": tile_index,
        "checksums": checksums,
        "elevation": elevation,
        "terrain_profile": terrain_profile,
        "map_quality": tile_index.get("quality"),
        "issues": issues,
        "status": _status_from_issues(issues),
    }
    return report


def write_geospatial_health_report(bundle_path: str | Path, output_path: str | Path | None = None) -> dict[str, Any]:
    report = geospatial_health_report(bundle_path)
    bundle = load_terrain_bundle(bundle_path)
    path = Path(output_path) if output_path else bundle.bundle_dir / "bundle_health.json"
    path.write_text(json.dumps(report, indent=2, sort_keys=True) + "\n")
    report["path"] = str(path)
    return report


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Inspect terrain bundle geospatial health.")
    parser.add_argument("--bundle", required=True, help="Bundle directory or manifest.json path.")
    parser.add_argument("--write", action="store_true", help="Write bundle_health.json.")
    parser.add_argument("--output", help="Custom output path for --write.")
    parser.add_argument("--json", action="store_true", help="Emit JSON only.")
    return parser.parse_args()


def print_human(report: dict[str, Any]) -> None:
    elevation = report.get("elevation") or {}
    terrain_profile = report.get("terrain_profile") or {}
    print(f"Geospatial health: {report.get('bundle_id') or '(unnamed)'}")
    print(f"Status: {report['status']}")
    print(f"Raster: {report['raster'].get('format')} {report['raster'].get('width_px')}x{report['raster'].get('height_px')}")
    print(f"CRS: {report['georef'].get('crs') or '(missing)'}")
    print(f"GSD: {report['georef'].get('gsd_m')}")
    print(f"Tiles: {report['tile_index'].get('tile_count') or 0}")
    print(f"Features: {report['tile_index'].get('feature_count') or 0}")
    print(
        "Elevation: "
        f"{elevation.get('status') or 'not_provided'} "
        f"({elevation.get('asset_count') or 0} asset(s), vertical sanity "
        f"{'ready' if elevation.get('vertical_sanity_ready') else 'not ready'})"
    )
    if terrain_profile.get("status") not in {None, "not_provided"}:
        agl = terrain_profile.get("estimated_agl_m") or {}
        relief = (terrain_profile.get("terrain_elevation_m") or {}).get("relief")
        print(
            "Terrain profile: "
            f"{terrain_profile.get('status')} "
            f"(min AGL {agl.get('min') if agl else 'n/a'} m, relief {relief if relief is not None else 'n/a'} m)"
        )
    for issue in report["issues"]:
        print(f"[{issue['severity'].upper()}] {issue['message']}")


def main() -> None:
    args = parse_args()
    report = write_geospatial_health_report(args.bundle, args.output) if args.write else geospatial_health_report(args.bundle)
    if args.json:
        print(json.dumps(report, indent=2, sort_keys=True))
    else:
        print_human(report)
    if report["status"] == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()
