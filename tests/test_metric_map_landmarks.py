"""Real raster integration tests for learned, surface-backed map features."""

import json

import numpy as np
import pytest

from vision_nav.neural.map_landmarks import MapLandmarkSampler, ELEVATION_SCHEMA
from vision_nav.neural.map_tile_layout import build_map_tile_layout
from vision_nav.neural.map_asset_compiler import compile_learned_map_assets
from vision_nav.neural.local_feature_cache import load_local_feature_cache
from vision_nav.neural.map_asset_validation import validate_learned_map_assets
from vision_nav.neural.releases import ModelRelease
from vision_nav.neural.training_labels import geodetic_to_enu
from vision_nav.training_capture import sha256_file


def _raster(path, values, transform, crs="EPSG:32610"):
    rasterio = pytest.importorskip("rasterio")
    with rasterio.open(path, "w", driver="GTiff", height=values.shape[-2], width=values.shape[-1],
                       count=1 if values.ndim == 2 else values.shape[0], dtype=values.dtype,
                       crs=crs, transform=transform, nodata=-9999 if values.dtype != np.uint8 else None) as dest:
        dest.write(values[None] if values.ndim == 2 else values)


def _fixture(tmp_path, *, different_crs=False):
    rasterio = pytest.importorskip("rasterio")
    Transformer = pytest.importorskip("pyproj").Transformer
    transform = rasterio.transform.from_origin(500000, 4200000, 2, 2)
    _raster(tmp_path / "ortho.tif", np.ones((3, 16, 16), dtype=np.uint8) * 120, transform)
    elevation_transform, elevation_crs = transform, "EPSG:32610"
    if different_crs:
        lon, lat = Transformer.from_crs(32610, 4326, always_xy=True).transform(500000, 4200000)
        elevation_transform = rasterio.transform.from_origin(lon - 0.001, lat + 0.001, 0.0002, 0.0002)
        elevation_crs = "EPSG:4326"
    elevation = np.full((16, 16), 105.0, dtype=np.float32)
    elevation[3, 3] = -9999
    _raster(tmp_path / "surface.tif", elevation, elevation_transform, elevation_crs)
    lon, lat = Transformer.from_crs(32610, 4326, always_xy=True).transform(500000, 4200000)
    result = build_map_tile_layout(tmp_path / "ortho.tif", tmp_path / "release",
                                   origin_lat_deg=lat, origin_lon_deg=lon, origin_altitude_m=100,
                                   tile_size_px=16)
    spec = {"schema_version": ELEVATION_SCHEMA, "raster_path": "surface.tif",
            "sha256": sha256_file(tmp_path / "surface.tif"), "vertical_datum": "wgs84_ellipsoid",
            "origin_vertical_datum": "wgs84_ellipsoid", "units": "m", "surface_type": "dsm",
            "source": "synthetic", "acquisition_date": "2026-09-05", "license": "test-only",
            "alignment_evidence": "known synthetic transform", "horizontal_sigma_m": 0.5, "vertical_sigma_m": 1.0}
    spec_path = tmp_path / "elevation.json"
    spec_path.write_text(json.dumps(spec))
    return result, spec_path, spec


@pytest.mark.parametrize("different_crs", [False, True])
def test_lifts_resized_pixel_centres_with_crs_and_nodata(tmp_path, different_crs):
    result, spec_path, _ = _fixture(tmp_path, different_crs=different_crs)
    points = np.asarray([[0, 0], [1, 1], [7, 7], [-1, 3], [8, 3]], dtype=float)
    with MapLandmarkSampler(result["layout"], spec_path) as sampler:
        valid, enu, variance = sampler.sample(result["layout"]["tiles"][0], points, (8, 8))
    assert valid.tolist() == ([True, True, True, False, False] if different_crs else [True, False, True, False, False])
    Transformer = pytest.importorskip("pyproj").Transformer
    # (0,0) in a half-size model input maps to raster corner coordinates (1,1).
    lon, lat = Transformer.from_crs(32610, 4326, always_xy=True).transform(500002, 4199998)
    origin = result["layout"]["geodetic_origin_wgs84"]
    expected = geodetic_to_enu(lat, lon, 105, origin_lat_deg=origin["lat_deg"],
                               origin_lon_deg=origin["lon_deg"], origin_altitude_m=100)
    np.testing.assert_allclose(enu[0], expected, atol=1e-7)
    np.testing.assert_allclose(variance, np.tile([0.25, 0.25, 1.0], (len(enu), 1)))
    assert enu[0, 2] == pytest.approx(5, abs=0.001)


@pytest.mark.parametrize("key,value", [("vertical_datum", "egm96"), ("units", "ft"),
                                      ("surface_type", "dtm"), ("horizontal_sigma_m", 0),
                                      ("vertical_sigma_m", float("nan")), ("alignment_evidence", "")])
def test_rejects_undeclared_or_unusable_surface(tmp_path, key, value):
    result, path, spec = _fixture(tmp_path)
    spec[key] = value
    path.write_text(json.dumps(spec))
    with pytest.raises(ValueError):
        MapLandmarkSampler(result["layout"], path)


def test_checksum_and_extent_rejection(tmp_path):
    result, path, spec = _fixture(tmp_path)
    with MapLandmarkSampler(result["layout"], path) as sampler:
        tile = {**result["layout"]["tiles"][0], "source_window_px": {"column": 100, "row": 0, "width": 16, "height": 16}}
        with pytest.raises(ValueError, match="outside"):
            sampler.sample(tile, np.asarray([[1, 1]]), (8, 8))
    spec["sha256"] = "0" * 64
    path.write_text(json.dumps(spec))
    with pytest.raises(ValueError, match="checksum"):
        MapLandmarkSampler(result["layout"], path)


def _model():
    image = {"shadow_source": "frame_rgb", "preprocess": {"image_size": [8, 8], "color_space": "rgb", "layout": "nchw"}}
    return ModelRelease(release_id="test@1", profile_id="pi5_crossview_nav_v1", semantic_version="1.0.0",
                        checkpoint_digest_sha256="a" * 64, onnx_digest_sha256="b" * 64,
                        training_dataset_releases=("test@1",), source_revision="test", license_summary="test-only",
                        redistribution_allowed=False, calibration_ids=("cam@1",), artifact_paths={}, artifact_interfaces={
                            "global_retriever": {"inputs": {"image": image}, "outputs": {"embedding": {"shadow_output": "retrieval_embedding"}}},
                            "local_features": {"inputs": {"image": image}, "outputs": {
                                "keypoints": {"shadow_output": "local_keypoints", "coordinate_space": "input_pixel_centers"},
                                "descriptors": {"shadow_output": "local_descriptors"}}}})


class _Runner:
    def run(self, role, inputs):
        if role == "global_retriever":
            return {"embedding": np.asarray([[1, 0]], dtype=np.float32)}
        points = np.asarray([[[x, y] for y in [1, 3, 5, 7] for x in [1, 3, 5, 7]]], dtype=np.float32)
        return {"keypoints": points, "descriptors": np.arange(16 * 4, dtype=np.float32).reshape(1, 16, 4)}


def test_compiler_cache_and_release_deep_validation(tmp_path):
    layout, spec_path, _ = _fixture(tmp_path)
    result = compile_learned_map_assets(layout["layout_path"], map_release_id="area@1", model_release=_model(),
                                       runner=_Runner(), elevation_spec=spec_path)
    root = tmp_path / "release"
    assets = result["compilation"]["assets"]
    cache = load_local_feature_cache(root / assets["local_feature_metadata_uri"])
    features = cache.load("tile_000000")
    assert features["map_points_enu_m"].shape == (15, 3)
    assert features["keypoints"].shape == (1, 15, 2)
    # The masked first point is removed from both descriptor and landmark arrays.
    np.testing.assert_array_equal(features["descriptors"][0, 0], [4, 5, 6, 7])
    assert result["compilation"]["supported_geometry_modes"][0] == "pnp_3d"
    (root / "item.json").write_text("{}")
    # Deep validation uses real release artifacts and checksummed provenance.
    assets.update(cog_uri="../ortho.tif", stac_item_uri="item.json")
    release = {"schema_version": "vision_nav_map_release_v2", "release_id": "area@1", "map_id": "area", "version": "1",
               "profile_id": "pi5_crossview_nav_v1", "model_release_id": "test@1", "crs": "EPSG:32610",
               "local_origin_enu": [0, 0, 0], "geodetic_origin_wgs84": layout["layout"]["geodetic_origin_wgs84"],
               "gsd_m_per_px": 2, "calibration_ids": ["cam@1"], "geometry_modes": ["pnp_3d"], **assets,
               "checksums_sha256": {value: sha256_file(root / value) for value in assets.values()}}
    release_path = root / "map_release.json"
    release_path.write_text(json.dumps(release))
    assert validate_learned_map_assets(release_path)["status"] == "valid"
    release["geodetic_origin_wgs84"] = {**release["geodetic_origin_wgs84"], "altitude_m": 200}
    release_path.write_text(json.dumps(release))
    report = validate_learned_map_assets(release_path)
    assert report["status"] == "invalid" and "origin" in report["errors"][0]
    del release["landmark_provenance_uri"]
    release_path.write_text(json.dumps(release))
    assert validate_learned_map_assets(release_path)["status"] == "invalid"


def test_explicit_feature_coordinate_contract_required(tmp_path):
    layout, spec, _ = _fixture(tmp_path)
    model = _model()
    del model.artifact_interfaces["local_features"]["outputs"]["keypoints"]["coordinate_space"]
    with pytest.raises(ValueError, match="coordinate_space"):
        compile_learned_map_assets(layout["layout_path"], map_release_id="area@1", model_release=model,
                                   runner=_Runner(), elevation_spec=spec)
    assert not (tmp_path / "release" / "features").exists()
