"""Real raster and learned-map plumbing; synthetic geography, not field accuracy."""

import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from vision_nav.neural.map_landmarks import MapLandmarkSampler, MASKED_ELEVATION_SCHEMA, SURFACE_MASK_SCHEMA
from vision_nav.neural.map_asset_compiler import compile_learned_map_assets
from vision_nav.neural.local_feature_cache import load_local_feature_cache
from vision_nav.neural.replay_local_geometry import ReplayLocalVerifier
from vision_nav.neural.surface_mask import build
from vision_nav.training_capture import sha256_file
from test_metric_map_landmarks import _fixture, _raster, _model, _Runner
from test_learned_correspondence import FixtureMatcher


def masked_fixture(tmp_path, *, surface="dtm", different_crs=False):
    import rasterio
    from pyproj import Transformer
    result, path, spec = _fixture(tmp_path)
    with rasterio.open(tmp_path / "ortho.tif") as image:
        affine, crs = image.transform, image.crs
    if different_crs:
        lon, lat = Transformer.from_crs(32610, 4326, always_xy=True).transform(500000, 4200000)
        affine, crs = rasterio.transform.from_origin(lon-.001, lat+.001, .0002, .0002), "EPSG:4326"
    values = np.ones((16, 16), np.uint8)
    _raster(tmp_path / "support.tif", values, affine, crs)
    mask = {"schema_version": SURFACE_MASK_SCHEMA, "raster_path": "support.tif",
        "sha256": sha256_file(tmp_path / "support.tif"), "imagery_sha256": result["layout"]["cog"]["sha256"],
        "valid_value": 1, "unknown_policy": "reject", "supported_surface": "bare_earth_ground",
        "source": "synthetic", "acquisition_date": "2026-09-05", "license": "test-only",
        "alignment_evidence": "known transform", "support_basis": "synthetic known ground", "exclusion_margin_m": 1.}
    spec.update(schema_version=MASKED_ELEVATION_SCHEMA, surface_type=surface, surface_mask=mask)
    path.write_text(json.dumps(spec))
    return result, path, spec


def replace_mask(tmp_path, path, spec, values):
    import rasterio
    with rasterio.open(tmp_path / "support.tif", "r+") as dest:
        dest.write(values, 1)
    spec["surface_mask"]["sha256"] = sha256_file(tmp_path / "support.tif")
    path.write_text(json.dumps(spec))


@pytest.mark.parametrize("surface", ["dsm", "dtm"])
@pytest.mark.parametrize("different_crs", [False, True])
def test_v2_masked_surfaces_and_crs_transform(tmp_path, surface, different_crs):
    result, path, _ = masked_fixture(tmp_path, surface=surface, different_crs=different_crs)
    with MapLandmarkSampler(result["layout"], path) as sampler:
        valid, points, _ = sampler.sample(result["layout"]["tiles"][0], [[0, 0], [1, 1], [2, 2]], (8, 8))
        assert valid.tolist() == [True, False, True]
        assert sampler.last_sample_report["invalid_elevation"] == 1
        assert sampler.last_sample_report["surface_type"] == surface
        np.testing.assert_allclose(points[:, 2], 5, atol=.001)


def test_excluded_unknown_nodata_outside_and_height_failures(tmp_path):
    import rasterio
    result, path, spec = masked_fixture(tmp_path)
    values = np.ones((16, 16), np.uint8)
    values[1, 1], values[15, 15], values[5, 5] = 0, 255, 2
    replace_mask(tmp_path, path, spec, values)
    with MapLandmarkSampler(result["layout"], path) as sampler:
        valid, points, _ = sampler.sample(result["layout"]["tiles"][0], [[0, 0], [1, 1], [7, 7], [2, 2], [3, 3], [8, 3]], (8, 8))
        assert valid.tolist() == [False, False, False, False, True, False]
        assert len(points) == 1
        assert sampler.last_sample_report["excluded_or_unknown_surface"] == 3
        assert sampler.last_sample_report["invalid_elevation"] == 1
    with rasterio.open(tmp_path / "support.tif", "r+") as dest:
        dest.transform = rasterio.transform.from_origin(600000, 4200000, 2, 2)
    spec["surface_mask"]["sha256"] = sha256_file(tmp_path / "support.tif")
    path.write_text(json.dumps(spec))
    with MapLandmarkSampler(result["layout"], path) as sampler:
        valid, points, _ = sampler.sample(result["layout"]["tiles"][0], [[3, 3]], (8, 8))
        assert not valid.any() and len(points) == 0


@pytest.mark.parametrize("key,value", [("sha256", "0"*64), ("imagery_sha256", "0"*64), ("valid_value", True),
    ("unknown_policy", "allow"), ("support_basis", ""), ("exclusion_margin_m", 0), ("supported_surface", "buildings_only")])
def test_mask_contract_rejects_unbound_or_unsafe_input(tmp_path, key, value):
    result, path, spec = masked_fixture(tmp_path)
    spec["surface_mask"][key] = value
    path.write_text(json.dumps(spec))
    with pytest.raises(ValueError):
        MapLandmarkSampler(result["layout"], path)


def test_v2_dtm_without_support_is_rejected(tmp_path):
    result, path, spec = masked_fixture(tmp_path)
    del spec["surface_mask"]
    path.write_text(json.dumps(spec))
    with pytest.raises(ValueError, match="support"):
        MapLandmarkSampler(result["layout"], path)


def test_compiler_removes_matching_descriptors_and_records_mask_provenance(tmp_path):
    result, path, spec = masked_fixture(tmp_path)
    values = np.ones((16, 16), np.uint8)
    values[7, 7] = 0
    replace_mask(tmp_path, path, spec, values)
    compiled = compile_learned_map_assets(result["layout_path"], map_release_id="area@1", model_release=_model(),
                                         runner=_Runner(), elevation_spec=path)["compilation"]
    root = tmp_path / "release"
    features = load_local_feature_cache(root / compiled["assets"]["local_feature_metadata_uri"]).load("tile_000000")
    assert features["map_points_enu_m"].shape == (14, 3)
    assert 20 not in features["descriptors"][0, :, 0]  # original feature 5, excluded by mask
    provenance = json.loads((root / compiled["assets"]["landmark_provenance_uri"]).read_text())
    assert provenance["elevation"]["surface_mask"]["sha256"] == spec["surface_mask"]["sha256"]
    assert provenance["tiles"][0]["surface_filter"]["excluded_or_unknown_surface"] == 1


def test_all_excluded_fails_compilation_without_planar_fallback(tmp_path):
    result, path, spec = masked_fixture(tmp_path)
    replace_mask(tmp_path, path, spec, np.zeros((16, 16), np.uint8))
    with pytest.raises(ValueError, match="fewer than six"):
        compile_learned_map_assets(result["layout_path"], map_release_id="area@1", model_release=_model(),
                                  runner=_Runner(), elevation_spec=path)


def test_replay_mask_filter_and_cached_source_invalidation(tmp_path, monkeypatch):
    import rasterio
    result, path, spec = masked_fixture(tmp_path)
    values = np.ones((16, 16), np.uint8)
    values[2, 2] = 0
    replace_mask(tmp_path, path, spec, values)
    monkeypatch.setattr("vision_nav.neural.replay_local_geometry.load_local_matcher", FixtureMatcher)
    config = tmp_path / "matcher.json"
    config.write_text(json.dumps({"elevation_spec": str(path)}))
    camera = SimpleNamespace(camera_matrix=np.array([[40, 0, 31.5], [0, 40, 31.5], [0, 0, 1]]),
                             distortion_coefficients=np.zeros(5), image_size=(64, 64))
    verifier = ReplayLocalVerifier(config, Path(result["layout_path"]), camera)
    image = np.ones((64, 64, 3), np.uint8)*120
    first = verifier.verify_pixels(image, ["tile_000000"])["attempts"][0]
    assert first["status"] == "verified" and first["feature_counts"]["map"] == 15
    assert 0 not in first["matched_query_feature_indices"]
    assert first["surface_filter"]["excluded_or_unknown_surface"] == 1
    with rasterio.open(tmp_path / "support.tif", "r+") as dest:
        dest.write(np.zeros((16, 16), np.uint8), 1)
    last = verifier.verify_pixels(image, ["tile_000000"])
    assert not last["complete"] and not last["accepted_tile_ids"]
    assert "source changed" in last["attempts"][0]["reason"]


def annotations(tmp_path, *, only_exclude=False):
    from pyproj import Transformer
    project = Transformer.from_crs(32610, 4326, always_xy=True)
    def polygon(x0, y0, x1, y1, kind):
        ring = [list(project.transform(x, y)) for x, y in [(x0,y0),(x1,y0),(x1,y1),(x0,y1),(x0,y0)]]
        return {"type": "Feature", "properties": {"surface": kind},
                "geometry": {"type": "Polygon", "coordinates": [ring]}}
    rows = [] if only_exclude else [polygon(500000, 4199968, 500032, 4200000, "ground_support")]
    rows.append(polygon(500012, 4199980, 500020, 4199988, "exclude"))
    path = tmp_path / "support.geojson"
    path.write_text(json.dumps({"type": "FeatureCollection", "features": rows}))
    return path


def build_mask(tmp_path, **kwargs):
    return build(tmp_path / "ortho.tif", annotations(tmp_path), tmp_path / "mask", margin_m=1.,
                 source="synthetic", acquisition_date="2026-09-05", license="test-only",
                 alignment_evidence="known synthetic map", support_basis="reviewed synthetic ground", **kwargs)


def test_polygon_builder_is_conservative_and_generated_mask_loads(tmp_path):
    import rasterio
    result, path, spec = _fixture(tmp_path)
    mask = build_mask(tmp_path)
    with rasterio.open(tmp_path / "mask/ground-support.tif") as raster:
        values = raster.read(1)
    assert values[0].sum() == 0 and values[:, 0].sum() == 0
    assert values[7:9, 7:9].sum() == 0  # expanded non-ground exclusion
    assert values[3, 3] == 1
    assert 0 < mask["supported_pixels"] < mask["total_pixels"]
    spec.update(schema_version=MASKED_ELEVATION_SCHEMA, surface_type="dtm",
                surface_mask={**mask, "raster_path": "mask/ground-support.tif"})
    path.write_text(json.dumps(spec))
    with MapLandmarkSampler(result["layout"], path) as sampler:
        valid, _, _ = sampler.sample(result["layout"]["tiles"][0], [[0, 0], [7, 7], [3, 11]], (16, 16))
        assert valid.tolist() == [False, False, True]
    with pytest.raises(ValueError, match="new directory"):
        build_mask(tmp_path)


def test_missing_reviewed_ground_cannot_invert_building_footprints_into_support(tmp_path):
    _fixture(tmp_path)
    path = annotations(tmp_path, only_exclude=True)
    with pytest.raises(ValueError, match="No reviewed ground"):
        build(tmp_path / "ortho.tif", path, tmp_path / "mask", margin_m=1., source="test",
              acquisition_date="2026-09-05", license="test", alignment_evidence="test", support_basis="test")
    assert not (tmp_path / "mask").exists()


def test_cli_builds_manifest_and_registered_entrypoint(tmp_path, capsys):
    import tomllib
    from vision_nav.neural.surface_mask import main
    _fixture(tmp_path)
    path = annotations(tmp_path)
    assert main(["--imagery", str(tmp_path / "ortho.tif"), "--annotations", str(path),
        "--output", str(tmp_path / "cli-mask"), "--margin-m", "1", "--source", "synthetic",
        "--acquisition-date", "2026-09-05", "--license", "test-only",
        "--alignment-evidence", "known transform", "--support-basis", "synthetic ground"]) == 0
    report = json.loads(capsys.readouterr().out)
    assert report["supported_pixels"] > 0
    assert report["sha256"] == sha256_file(tmp_path / "cli-mask/ground-support.tif")
    config = tomllib.loads((Path(__file__).resolve().parents[1] / "pyproject.toml").read_text())
    assert config["project"]["scripts"]["vision-nav-build-surface-mask"] == "vision_nav.neural.surface_mask:main"


@pytest.mark.parametrize("fault", ["geographic", "rotated", "invalid_polygon", "legacy_crs", "all_excluded"])
def test_builder_rejects_unsupported_or_empty_maps(tmp_path, fault):
    import rasterio
    _fixture(tmp_path)
    path = annotations(tmp_path)
    if fault in {"geographic", "rotated"}:
        with rasterio.open(tmp_path / "ortho.tif", "r+") as dest:
            if fault == "geographic":
                dest.crs = "EPSG:4326"
            else:
                dest.transform = dest.transform * rasterio.Affine.rotation(20)
    else:
        data = json.loads(path.read_text())
        if fault == "legacy_crs":
            data["crs"] = {"type": "name", "properties": {"name": "EPSG:32610"}}
        elif fault == "all_excluded":
            data["features"].append({**data["features"][0], "properties": {"surface": "exclude"}})
        else:
            data["features"][0]["geometry"] = {"type": "Point", "coordinates": [-122, 37]}
        path.write_text(json.dumps(data))
    with pytest.raises(ValueError):
        build(tmp_path / "ortho.tif", path, tmp_path / "mask", margin_m=1., source="test",
              acquisition_date="2026-09-05", license="test", alignment_evidence="test", support_basis="test")
    assert not (tmp_path / "mask/surface-mask.json").exists()


def test_external_mask_sidecar_is_not_silently_unchecksummed(tmp_path):
    import rasterio
    result, path, _ = masked_fixture(tmp_path)
    with rasterio.Env(GDAL_TIFF_INTERNAL_MASK=False):
        with rasterio.open(tmp_path / "support.tif", "r+") as dest:
            dest.write_mask(np.ones((16, 16), np.uint8)*255)
    assert (tmp_path / "support.tif.msk").exists()
    with pytest.raises(ValueError, match="self-contained|checksum"):
        MapLandmarkSampler(result["layout"], path)


def test_actual_xfeat_lighterglue_use_only_masked_ground_points(tmp_path):
    import os
    import rasterio
    from vision_nav.neural.map_tile_layout import build_map_tile_layout

    location = os.environ.get("VISION_NAV_LOCAL_EXPORT")
    if not location:
        pytest.skip("VISION_NAV_LOCAL_EXPORT required for actual ONNX integration")
    export = Path(location).resolve()
    manifest = json.loads((export / "manifest.json").read_text())
    sample = next(v for v in manifest["parity_samples"] if v["kind"] == "image")
    assert sha256_file(export / sample["path"]) == sample["sha256"]
    with np.load(export / sample["path"], allow_pickle=False) as archive:
        rgb = np.rint(archive["image"][0].transpose(1, 2, 0)*255).astype(np.uint8)
    height, width = rgb.shape[:2]
    original, path, spec = masked_fixture(tmp_path)
    affine = rasterio.transform.from_origin(500000, 4200000, 2, 2)
    _raster(tmp_path / "ortho.tif", rgb.transpose(2, 0, 1), affine)
    _raster(tmp_path / "surface.tif", np.full((height, width), 105., np.float32), affine)
    mask = np.ones((height, width), np.uint8)
    mask[:, width*3//4:] = 0
    _raster(tmp_path / "support.tif", mask, affine)
    origin = original["layout"]["geodetic_origin_wgs84"]
    layout = build_map_tile_layout(tmp_path / "ortho.tif", tmp_path / "actual-layout",
        origin_lat_deg=origin["lat_deg"], origin_lon_deg=origin["lon_deg"], origin_altitude_m=100.,
        tile_size_px=width, allow_partial_tiles=True)
    spec["sha256"] = sha256_file(tmp_path / "surface.tif")
    spec["surface_mask"].update(sha256=sha256_file(tmp_path / "support.tif"),
                                imagery_sha256=sha256_file(tmp_path / "ortho.tif"))
    path.write_text(json.dumps(spec))
    config = tmp_path / "actual-matcher.json"
    config.write_text(json.dumps({"schema_version": "vision_nav_learned_local_reference_v1", "backend": "onnx_cpu",
        "px4_external_vision": "disabled", "export_dir": str(export), "elevation_spec": str(path)}))
    calibration = SimpleNamespace(camera_matrix=np.array([[400,0,(width-1)/2], [0,400,(height-1)/2], [0,0,1]]),
                                  distortion_coefficients=np.zeros(5), image_size=(width, height))
    verifier = ReplayLocalVerifier(config, Path(layout["layout_path"]), calibration)
    evidence = verifier.verify_pixels(rgb[:, :, ::-1].copy(), ["tile_000000"])
    attempt = evidence["attempts"][0]
    assert attempt["status"] == "verified", attempt
    assert 12 <= attempt["feature_counts"]["map"] < attempt["feature_counts"]["query"]
    assert attempt["surface_filter"]["excluded_or_unknown_surface"] > 0
    assert 12 <= attempt["match_count"] <= 24
    assert all(p[0] < width*3//4 for p in attempt["matched_map_pixels"])
    assert attempt["geometry"]["status"] == "accepted", attempt["geometry"]
    from vision_nav.neural.bounded_pnp import validate_bound_attempt
    validate_bound_attempt(attempt)
    assert attempt["geometry"]["fit_selection"]["original_match_count"] > 24
    assert not evidence["position_authority"] and evidence["covariance"] is None
    from vision_nav.estimation.correlated_window import payload_digest
    from vision_nav.estimation.reprojection_uncertainty import assess
    n = attempt["match_count"]
    noise = np.zeros((5*n+4, 5*n+4))
    noise[:2*n, :2*n] = np.eye(2*n)*.04
    uncertainty_request = {"schema_version": "vision_nav_pnp_uncertainty_request_v1", "attempt": attempt,
        "context": {"map_release_id": "synthetic-height-map", "model_release_id": str(export),
                    "calibration_id": "synthetic-pinhole", "clock_id": "fixture", "timestamp_ns": 1_000_000_000,
                    "uncertainty_model_id": "synthetic-independent-pixel-noise-not-field-calibrated"},
        "uncertainty": {"attempt_sha256": payload_digest(attempt),
            "measurement_order": "all_query_uv_then_all_map_xyz_then_fx_fy_cx_cy",
            "correspondences": "fixed_correct_inliers_conditional",
            "map_systematics": "included_in_joint_measurement_covariance",
            "rectification_error": "included_in_joint_measurement_covariance",
            "timing_error": "not_modeled", "camera_extrinsic_error": "not_modeled",
            "scope": "synthetic_contract_test", "noise_model_description": "Independent synthetic 0.2 pixel error; fixed K and map",
            "joint_measurement_covariance": noise.tolist()}}
    assessment = assess(uncertainty_request)
    assert assessment["status"] == "conditional_research_covariance", assessment
    assert assessment["publication_covariance"] is None and not assessment["covariance_calibrated"]
    (tmp_path / "actual-mask-evidence.json").write_text(json.dumps({
        "scope": "actual_onnx_parity_imagery_synthetic_map_heights_and_mask_not_field_accuracy",
        "provenance": verifier.provenance, "result": evidence,
        "uncertainty_request": uncertainty_request, "conditional_assessment": assessment}, allow_nan=False))
