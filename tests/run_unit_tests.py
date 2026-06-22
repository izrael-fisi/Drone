from __future__ import annotations

import contextlib
import io
import json
import math
from pathlib import Path
import shutil
import shlex
import sqlite3
import struct
import sys
import tarfile
import tempfile
import time
from types import SimpleNamespace
import zipfile

import numpy as np

from vision_nav.barometer import BarometerSample, BarometerTracker, pressure_to_altitude_m
from vision_nav.ardupilot_params import check_ardupilot_external_nav_params, params_from_text as ardupilot_params_from_text
from vision_nav.autonomy_evidence_package import create_evidence_package, missing_artifact_lines
from vision_nav.autonomy_evidence_workflow import (
    REQUIRED_WORKFLOW_STEPS,
    print_human as print_workflow_validation_human,
    validate_workflow_report,
    validation_exit_code,
)
from vision_nav.autonomy_handoff import field_collection_next_condition as handoff_field_collection_next_condition
from vision_nav.autonomy_handoff import render_handoff_markdown
from vision_nav.autonomy_readiness import (
    REQUIRED_FIELD_CONDITIONS,
    evaluate_autonomy_readiness,
    field_collection_next_condition as readiness_field_collection_next_condition,
)
from vision_nav.bench_readiness import evaluate_bench_readiness, evaluate_bench_readiness_file
from vision_nav.benchmark_retrieval import benchmark_retrieval
from vision_nav.bundle import (
    load_manifest,
    manifest_feature_options,
    manifest_features_path,
    manifest_georef,
    manifest_orthophoto_path,
)
from vision_nav.bundle_checksums import verify_checksum_file, write_checksum_file
from vision_nav.bundle_diagnostics import compact_bundle_diagnostic, diagnose_bundle_inputs
from vision_nav.camera import load_camera_calibration, validate_image_size
from vision_nav.external_position import (
    ExternalPositionEstimate,
    OdometryResetTracker,
    build_odometry_payload,
    build_vision_position_estimate_payload,
    external_position_from_match_result,
    yaw_enu_to_ned,
)
from vision_nav.external_position_health import ExternalPositionHealthConfig, ExternalPositionStreamHealth
from vision_nav.feature_method_benchmark import benchmark_feature_methods
from vision_nav.field_capture_metadata import (
    CAPTURE_CHECKLIST_SCHEMA_VERSION,
    CAPTURE_METADATA_SCHEMA_VERSION,
)
from vision_nav.field_capture_metadata_update import update_field_capture_metadata
from vision_nav.field_capture_preflight import evaluate_field_capture_preflight
from vision_nav.field_collection_plan import (
    create_field_collection_plan,
    print_human as print_field_collection_human,
    render_field_collection_markdown,
)
from vision_nav.field_evidence_template import create_field_evidence_template
from vision_nav.field_evidence_gate import evaluate_field_evidence_gate
from vision_nav.field_workflow_selection import select_next_field_condition, shell_assignments
from vision_nav.geospatial_health import gdal_raster_metadata, geospatial_health_report
from vision_nav.georef import SimpleGeoReference, build_georef_from_cli, georef_from_json, georef_to_json
from vision_nav.mavlink_bridge import (
    MavlinkSendResult,
    MavlinkVisionBridge,
    _load_mavutil,
    parse_mavlink_endpoint,
    send_records_once,
)
from vision_nav.px4_sitl_evidence import Px4SitlEvidenceConfig, evaluate_px4_sitl_evidence
from vision_nav.px4_sitl_session import evaluate_px4_sitl_session
from vision_nav.px4_params import check_px4_external_vision_params, evaluate_px4_param_file, params_from_text
from vision_nav.ros2_bridge import DIAG_ERROR, DIAG_OK, diagnostic_status_from_health, odometry_dict_from_match_result
from vision_nav.ros2_bridge import export_rosbag_jsonl, export_rosbag_mcap, export_rosbag2, ros_records_from_log
from vision_nav.rosbag2_cli_review import review_rosbag2_cli
from vision_nav.rosbag_export_check import validate_rosbag_export
from vision_nav.runtime_status import runtime_status_snapshot, write_runtime_status
from vision_nav.replay_case_manifest import evaluate_replay_case_manifest
from vision_nav.replay_case_registry import register_replay_case
from vision_nav.replay_case_schema import REPLAY_CASE_MANIFEST_SCHEMA, evaluate_replay_case_schema
from vision_nav.replay_dataset_audit import audit_replay_dataset_coverage
from vision_nav.replay_gates import evaluate_replay_records
from vision_nav.summarize_match_log import summarize_records
from vision_nav.support_bundle import create_support_bundle, load_replay_cases, print_human
from vision_nav.threshold_tuning import evaluate_threshold_tuning
from vision_nav.terrain_estimator import TerrainEstimator
from vision_nav.terrain_bundle import load_terrain_bundle
from vision_nav.terrain_tiles import (
    TerrainTile,
    create_tile_schema,
    global_descriptor_distance,
    image_global_descriptor,
    load_tile_retrieval_descriptor,
    query_tiles_with_metadata,
    save_tile_descriptor,
    tile_origins,
)
from vision_nav.validate_map_bundle import validate_bundle


def assert_equal(actual, expected, label: str) -> None:
    if actual != expected:
        raise AssertionError(f"{label}: expected {expected!r}, got {actual!r}")


def write_minimal_png(path: Path, width: int, height: int) -> None:
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + struct.pack(">I", 13)
        + b"IHDR"
        + struct.pack(">II", width, height)
        + b"\x08\x02\x00\x00\x00"
    )


def write_minimal_tiff(path: Path, width: int, height: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(
        b"II"
        + struct.pack("<H", 42)
        + struct.pack("<I", 8)
        + struct.pack("<H", 2)
        + struct.pack("<HHII", 256, 4, 1, width)
        + struct.pack("<HHII", 257, 4, 1, height)
        + struct.pack("<I", 0)
    )


def field_capture_metadata_fixture(
    condition: str,
    expected: str,
    *,
    bundle: str = "field-bundle",
    site_name: str = "unit-field-site",
) -> dict[str, object]:
    return {
        "schema_version": CAPTURE_METADATA_SCHEMA_VERSION,
        "site_name": site_name,
        "condition": condition,
        "expected_behavior": expected,
        "bundle": bundle,
        "operator": "unit-operator",
        "capture_date_utc": "2026-06-22T12:00:00Z",
        "location_label": "unit-test-range",
        "flight_altitude_agl_m": 35,
        "speed_mps": 4,
        "lighting": "nominal daylight",
        "weather": "clear",
        "terrain_texture": "mixed pavement and grass",
        "map_age_or_season_notes": "same season as map",
        "camera_focus_exposure_notes": "manual focus checked before capture",
        "imu_px4_state_notes": "PX4 attitude stream healthy during capture",
        "safety_notes": "closed test area",
        "notes": f"Unit metadata for {condition}.",
    }


def write_scalar_float_tiff(path: Path, values: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    array = np.asarray(values, dtype="<f4")
    height, width = array.shape
    entries = [
        (256, 4, 1, width),
        (257, 4, 1, height),
        (258, 3, 1, 32),
        (259, 3, 1, 1),
        (262, 3, 1, 1),
        (273, 4, 1, 0),
        (277, 3, 1, 1),
        (278, 4, 1, height),
        (279, 4, 1, array.nbytes),
        (339, 3, 1, 3),
    ]
    data_offset = 8 + 2 + len(entries) * 12 + 4
    payload = bytearray()
    payload.extend(b"II")
    payload.extend(struct.pack("<H", 42))
    payload.extend(struct.pack("<I", 8))
    payload.extend(struct.pack("<H", len(entries)))
    for tag, tag_type, count, value in entries:
        if tag == 273:
            value = data_offset
        payload.extend(struct.pack("<HHII", tag, tag_type, count, value))
    payload.extend(struct.pack("<I", 0))
    payload.extend(array.tobytes())
    path.write_bytes(bytes(payload))


def create_minimal_terrain_bundle(root: Path, *, include_tile_index: bool = True, include_elevation: bool = False) -> Path:
    bundle = root / "bundle"
    (bundle / "ortho").mkdir(parents=True)
    (bundle / "imagery" / "tiles").mkdir(parents=True)
    (bundle / "index" / "descriptors").mkdir(parents=True)
    write_minimal_png(bundle / "ortho" / "map.png", 100, 60)
    write_minimal_png(bundle / "imagery" / "tiles" / "tile_000000.png", 64, 60)
    if include_elevation:
        write_minimal_tiff(bundle / "elevation" / "dem.tif", 100, 60)
        write_minimal_tiff(bundle / "elevation" / "dsm.tif", 100, 60)
    np.savez_compressed(
        bundle / "index" / "descriptors" / "tile_000000.npz",
        tile_id=np.array("tile_000000"),
        image_path=np.array("imagery/tiles/tile_000000.png"),
        image_shape=np.array((60, 64), dtype=np.int32),
        method=np.array("orb"),
        keypoints_xy=np.zeros((4, 2), dtype=np.float32),
        descriptors=np.zeros((4, 32), dtype=np.uint8),
        offset_xy_px=np.array((0, 0), dtype=np.int32),
    )
    (bundle / "features").mkdir(parents=True)
    np.savez_compressed(
        bundle / "features" / "map_features.npz",
        method=np.array("orb"),
        keypoints_xy=np.zeros((4, 2), dtype=np.float32),
        descriptors=np.zeros((4, 32), dtype=np.uint8),
        image_shape=np.array((60, 100), dtype=np.int32),
    )
    if include_tile_index:
        index_path = bundle / "index" / "tiles.sqlite"
        with sqlite3.connect(index_path) as conn:
            create_tile_schema(conn)
            conn.execute(
                """
                INSERT INTO tiles (
                    tile_id, row, col, x0_px, y0_px, x1_px, y1_px,
                    min_east_m, max_east_m, min_north_m, max_north_m,
                    image_path, descriptor_path, keypoint_count, method
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "tile_000000",
                    0,
                    0,
                    0,
                    0,
                    64,
                    60,
                    0.0,
                    32.0,
                    -30.0,
                    0.0,
                    "imagery/tiles/tile_000000.png",
                    "index/descriptors/tile_000000.npz",
                    4,
                    "orb",
                ),
            )
            conn.commit()
    terrain_bundle = {
        "version": "0.1.0",
        "tile_index_path": "index/tiles.sqlite",
        "tile_size_px": 64,
        "overlap_px": 8,
        "local_origin": {"latitude": 40.0, "longitude": -75.0, "east_m": 0.0, "north_m": 0.0},
        "crs": "EPSG:4326",
        "gsd_m": 0.5,
    }
    stac_assets = {
        "orthophoto": {"href": "ortho/map.png"},
        "tile_index": {"href": "index/tiles.sqlite"},
    }
    if include_elevation:
        terrain_bundle["elevation_assets"] = {"dem": "elevation/dem.tif", "dsm": "elevation/dsm.tif"}
        stac_assets["dem"] = {"href": "elevation/dem.tif"}
        stac_assets["dsm"] = {"href": "elevation/dsm.tif"}
    (bundle / "manifest.json").write_text(
        json.dumps(
            {
                "bundle_id": "health-test",
                "orthophoto": {
                    "path": "ortho/map.png",
                    "origin_lat": 40.0,
                    "origin_lon": -75.0,
                    "gsd_m": 0.5,
                    "georef_source": "geotiff_embedded",
                    "georef_confidence": 0.95,
                    "georef_crs": "EPSG:4326",
                },
                "features": {"path": "features/map_features.npz", "method": "orb", "max_features": 100},
                "terrain_bundle": terrain_bundle,
                "source_region": {
                    "id": "unit-region",
                    "name": "Unit Test Region",
                    "metadata_path": "metadata.json",
                    "origin_lat": 40.0,
                    "origin_lon": -75.0,
                    "gsd_m_per_px": 0.5,
                    "width_px": 100,
                    "height_px": 60,
                    "zoom": 18,
                    "source": "uploaded_geotiff",
                    "original_file": "unit-map.tif",
                    "georef_source": "geotiff_embedded",
                    "georef_confidence": 0.95,
                    "georef_crs": "EPSG:4326",
                },
            }
        )
    )
    (bundle / "manifest.stac.json").write_text(
        json.dumps(
            {
                "stac_version": "1.0.0",
                "type": "Feature",
                "id": "health-test",
                "properties": {},
                "geometry": {"type": "Point", "coordinates": [-75.0, 40.0]},
                "links": [],
                "assets": stac_assets,
            }
        )
    )
    return bundle


def write_ready_gnss_denied_mission_plan(bundle: Path) -> None:
    (bundle / "mission").mkdir(exist_ok=True)
    (bundle / "mission" / "mission_plan.json").write_text(
        json.dumps(
            {
                "version": "0.3.0",
                "mission": {
                    "altitude_m": 35,
                    "speed_mps": 4,
                    "items": [
                        {"id": "takeoff", "type": "takeoff", "lat": 40.0, "lon": -75.0, "altitudeM": 35},
                        {"id": "wp1", "type": "waypoint", "lat": 39.9999, "lon": -74.9999, "altitudeM": 35},
                        {"id": "land", "type": "land", "lat": 39.9998, "lon": -74.9998, "altitudeM": 35},
                    ],
                },
                "gnss_denied": {
                    "status": "ready",
                    "satellite_source_disabled": True,
                    "map_position_reset": {"id": "wp1", "lat": 39.9999, "lon": -74.9999},
                    "home_position": {"id": "takeoff", "lat": 40.0, "lon": -75.0},
                    "heading_deg": 92.0,
                    "estimator_health": "ready",
                    "updated_at": "2026-06-21T00:00:00Z",
                    "checks": [
                        {"name": "satellite_source_disabled", "label": "Satellite off", "status": "passed"},
                        {"name": "map_position_reset", "label": "Map reset", "status": "passed"},
                        {"name": "home_position", "label": "Home reset", "status": "passed"},
                        {"name": "heading", "label": "Heading", "status": "passed"},
                        {"name": "estimator_health", "label": "Estimator ready", "status": "passed"},
                    ],
                },
            }
        )
    )
    manifest_path = bundle / "manifest.json"
    manifest = json.loads(manifest_path.read_text())
    manifest["mission"] = {"desktop_plan_path": "mission/mission_plan.json"}
    manifest_path.write_text(json.dumps(manifest))


class SkipTest(Exception):
    pass


def test_pixel_to_local_default_north_up_axes() -> None:
    georef = SimpleGeoReference(origin_lat=40.0, origin_lon=-75.0, gsd_m=0.5)
    east_m, north_m = georef.pixel_to_local_m(10, 8)
    assert_equal(east_m, 5.0, "east_m")
    assert_equal(north_m, -4.0, "north_m")
    lat, lon = georef.pixel_to_latlon(10, 8)
    x_px, y_px = georef.latlon_to_pixel(lat, lon)
    if abs(x_px - 10) > 1e-6 or abs(y_px - 8) > 1e-6:
        raise AssertionError(f"Expected lat/lon to invert to pixel (10, 8), got ({x_px}, {y_px})")


def test_georef_json_round_trip() -> None:
    georef = SimpleGeoReference(
        origin_lat=40.0,
        origin_lon=-75.0,
        gsd_m=0.25,
        origin_pixel_x=100,
        origin_pixel_y=200,
        rotation_deg=10,
        source="geotiff_embedded",
        confidence=0.95,
        crs="EPSG:32618",
    )
    assert_equal(georef_from_json(georef_to_json(georef)), georef, "georef round trip")


def test_build_georef_requires_core_fields_together() -> None:
    try:
        build_georef_from_cli(
            origin_lat=40.0,
            origin_lon=None,
            gsd_m=0.25,
            origin_pixel_x=0,
            origin_pixel_y=0,
            rotation_deg=0,
        )
    except ValueError as exc:
        if "must be provided together" not in str(exc):
            raise
    else:
        raise AssertionError("Expected incomplete georef arguments to fail")


def test_load_manifest_and_resolve_paths() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        bundle = Path(tmp) / "bundle"
        bundle.mkdir()
        manifest = bundle / "manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "bundle_id": "unit-test",
                    "orthophoto": {
                        "path": "ortho/map.png",
                        "origin_lat": 40.0,
                        "origin_lon": -75.0,
                        "gsd_m": 0.2,
                    },
                    "features": {
                        "path": "features/map_features.npz",
                        "method": "orb",
                        "max_features": 123,
                    },
                }
            )
        )

        bundle_dir, loaded = load_manifest(bundle)
        assert_equal(bundle_dir, bundle, "bundle_dir")
        assert_equal(manifest_orthophoto_path(bundle_dir, loaded), bundle / "ortho/map.png", "orthophoto path")
        assert_equal(manifest_features_path(bundle_dir, loaded), bundle / "features/map_features.npz", "features path")
        assert_equal(manifest_feature_options(loaded), {"method": "orb", "max_features": 123}, "feature options")
        assert_equal(manifest_georef(loaded)["origin_lat"], 40.0, "origin_lat")


def test_load_camera_calibration_and_validate_size() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "camera.yaml"
        path.write_text(
            """
camera_name: test_camera
image_width: 4
image_height: 3
distortion_model: plumb_bob
camera_matrix:
  rows: 3
  cols: 3
  data: [1.0, 0.0, 2.0, 0.0, 1.0, 1.5, 0.0, 0.0, 1.0]
distortion_coefficients:
  rows: 1
  cols: 5
  data: [0.1, 0.0, 0.0, 0.0, 0.0]
""".strip()
        )

        calibration = load_camera_calibration(path)
        assert_equal(calibration.camera_name, "test_camera", "camera_name")
        assert_equal(calibration.image_size, (4, 3), "image_size")
        validate_image_size(np.zeros((3, 4), dtype=np.uint8), calibration)

        try:
            validate_image_size(np.zeros((4, 4), dtype=np.uint8), calibration)
        except ValueError as exc:
            if "does not match calibration" not in str(exc):
                raise
        else:
            raise AssertionError("Expected mismatched calibration image size to fail")


def test_summarize_match_records() -> None:
    summary = summarize_records(
        [
            {
                "capture_duration_s": 0.2,
                "match_duration_s": 0.4,
                "external_position_health": {
                    "status": "healthy",
                    "message_type": "odometry",
                    "send_rate_hz": 1.0,
                    "last_latency_ms": 50.0,
                    "skip_reasons": {},
                    "last_warnings": [],
                },
                "mavlink": {"sent": True, "message": "ODOMETRY", "details": {"reset_counter": 0}},
                "result": {
                    "status": "accepted",
                    "confidence": 0.8,
                    "position_confidence": 0.76,
                    "inliers": 30,
                    "inlier_ratio": 0.6,
                    "reprojection_error_px": 2.0,
                    "geometry": {
                        "scale_mean": 1.0,
                        "rotation_deg": 2.0,
                        "scale_anisotropy": 1.1,
                        "perspective_norm": 0.001,
                    },
                    "frame_quality": {"sharpness_laplacian_var": 12.0, "entropy_bits": 5.0},
                    "estimated_position": {"latitude": 40.0, "longitude": -75.0},
                    "map_georef": {"confidence": 0.95, "source": "geotiff_embedded"},
                    "measurement": {"covariance": {"sigma_xy_m": 1.2}},
                },
            },
            {
                "capture_duration_s": 0.3,
                "match_duration_s": 0.5,
                "external_position_health": {
                    "status": "degraded",
                    "message_type": "odometry",
                    "send_rate_hz": 0.5,
                    "last_latency_ms": 250.0,
                    "skip_reasons": {"match_not_accepted": 1},
                    "last_warnings": ["match_not_accepted"],
                },
                "mavlink": {"sent": True, "message": "ODOMETRY", "details": {"reset_counter": 1}},
                "result": {
                    "status": "rejected",
                    "confidence": 0.2,
                    "inliers": 5,
                    "inlier_ratio": 0.1,
                    "reprojection_error_px": None,
                    "reason": "not_enough_inliers",
                },
            },
        ]
    )

    assert_equal(summary["total_records"], 2, "summary total_records")
    assert_equal(summary["status_counts"], {"accepted": 1, "rejected": 1}, "summary status_counts")
    assert_equal(summary["reason_counts"], {"not_enough_inliers": 1}, "summary reason_counts")
    assert_equal(summary["accepted_rate"], 0.5, "summary accepted_rate")
    assert_equal(summary["confidence"]["mean"], 0.5, "summary confidence mean")
    assert_equal(summary["position_confidence"]["mean"], 0.76, "summary position confidence mean")
    assert_equal(summary["georef_confidence"]["mean"], 0.95, "summary georef confidence mean")
    assert_equal(summary["geometry_scale_mean"]["mean"], 1.0, "summary geometry scale mean")
    assert_equal(summary["geometry_rotation_deg"]["mean"], 2.0, "summary geometry rotation mean")
    assert_equal(summary["estimated_position"]["count"], 1, "summary estimated_position count")
    assert_equal(summary["covariance_sigma_xy_m"]["mean"], 1.2, "summary covariance sigma mean")
    assert_equal(
        summary["external_position"]["status_counts"],
        {"degraded": 1, "healthy": 1},
        "summary external position status counts",
    )
    assert_equal(summary["external_position"]["message_counts"], {"odometry": 2}, "summary external position messages")
    assert_equal(
        summary["external_position"]["skip_reasons"],
        {"match_not_accepted": 1},
        "summary external position skip reasons",
    )
    assert_equal(
        summary["external_position"]["warning_counts"],
        {"match_not_accepted": 1},
        "summary external position warnings",
    )
    assert_equal(summary["external_position"]["send_rate_hz"]["mean"], 0.75, "summary external position rate")
    assert_equal(summary["external_position"]["latency_ms"]["mean"], 150.0, "summary external position latency")
    assert_equal(summary["external_position"]["reset_counter"]["max"], 1.0, "summary reset counter max")
    assert_equal(summary["external_position"]["last_reset_counter"], 1, "summary last reset counter")


def test_quality_metrics_and_covariance() -> None:
    try:
        from vision_nav.quality import (
            estimate_position_covariance_m2,
            estimate_visual_position_confidence,
            feature_density,
            frame_quality_metrics,
        )
    except ModuleNotFoundError as exc:
        if exc.name == "cv2":
            raise SkipTest("requires cv2") from exc
        raise

    image = np.zeros((8, 8), dtype=np.uint8)
    image[2:6, 2:6] = 255
    quality = frame_quality_metrics(image)
    if quality["sharpness_laplacian_var"] <= 0:
        raise AssertionError("Expected edge-rich test image to have nonzero sharpness")
    if quality["entropy_bits"] <= 0:
        raise AssertionError("Expected test image to have nonzero entropy")

    assert_equal(feature_density(100, image.shape), 1562500.0, "feature density")
    covariance = estimate_position_covariance_m2(confidence=0.8, reprojection_error_px=2.0, gsd_m=0.25)
    if covariance["sigma_xy_m"] <= 0:
        raise AssertionError("Expected positive covariance sigma")
    assert_equal(estimate_visual_position_confidence(0.8, 0.95), 0.76, "position confidence")


def test_mavlink_endpoint_parsing_and_axis_mapping() -> None:
    assert_equal(parse_mavlink_endpoint("udp:14550"), ("udpout:127.0.0.1:14550", None), "udp port alias")
    assert_equal(
        parse_mavlink_endpoint("serial:/dev/ttyAMA0:921600"),
        ("/dev/ttyAMA0", 921600),
        "serial endpoint",
    )

    calls = []

    class FakeMav:
        def vision_position_estimate_send(self, *args):
            calls.append(args)

    class FakeConnection:
        mav = FakeMav()

    bridge = MavlinkVisionBridge("udp:14550")
    bridge._conn = FakeConnection()
    bridge._last_heartbeat_s = time.monotonic()
    send_result = bridge.send_match_result(
        {
            "status": "accepted",
            "measurement": {
                "frame": "local_enu",
                "x_m": 4.0,
                "y_m": 7.0,
                "z_m": None,
                "yaw_rad": None,
                "covariance": {"x_m2": 9.0, "y_m2": 16.0, "z_m2": None, "yaw_rad2": None},
            },
        },
        message_type="vision_position_estimate",
    )
    assert_equal(send_result.sent, True, "MAVLink send status")
    _, x_north, y_east, z_down, *_rest, covariance = calls[0]
    assert_equal(x_north, 7.0, "MAVLink north axis")
    assert_equal(y_east, 4.0, "MAVLink east axis")
    assert_equal(z_down, -0.0, "MAVLink down axis")
    assert_equal(covariance[0], 16.0, "MAVLink north covariance")
    assert_equal(covariance[6], 9.0, "MAVLink east covariance")


def test_mavlink_log_sender_uses_selected_message_type() -> None:
    calls = []

    class FakeBridge:
        def send_match_result(self, result, *, message_type="vision_position_estimate"):
            calls.append((result, message_type))
            if result.get("status") != "accepted":
                return MavlinkSendResult(False, reason="match_not_accepted")
            return MavlinkSendResult(True, message="ODOMETRY" if message_type == "odometry" else "VISION_POSITION_ESTIMATE")

    report = send_records_once(
        [
            {"result": {"status": "accepted", "measurement": {"frame": "local_enu", "x_m": 1.0, "y_m": 2.0}}},
            {"result": {"status": "rejected", "reason": "low_inliers"}},
        ],
        FakeBridge(),
        message_type="odometry",
        repeat=2,
    )
    assert_equal(report["message_type"], "odometry", "mavlink log sender message type")
    assert_equal(report["sent"], 2, "mavlink log sender sent count")
    assert_equal(report["skipped"], 2, "mavlink log sender skipped count")
    assert_equal(report["skip_reasons"], {"match_not_accepted": 2}, "mavlink log sender skip reasons")
    assert_equal({message_type for _result, message_type in calls}, {"odometry"}, "mavlink log sender dispatch type")


def test_mavlink_odometry_requires_mavlink2_dialect() -> None:
    class FakeMavlink:
        class MAVLink:
            pass

    class FakeMavutil:
        mavlink = FakeMavlink()

        @staticmethod
        def set_dialect(_dialect):
            return None

    previous_package = sys.modules.get("pymavlink")
    sys.modules["pymavlink"] = SimpleNamespace(mavutil=FakeMavutil)
    try:
        _load_mavutil(require_odometry=True)
    except RuntimeError as exc:
        if "MAVLink 2" not in str(exc):
            raise AssertionError("MAVLink 1 odometry failure should explain MAVLink 2 requirement") from exc
    else:
        raise AssertionError("MAVLink 1 dialect should not satisfy ODOMETRY output")
    finally:
        if previous_package is None:
            sys.modules.pop("pymavlink", None)
        else:
            sys.modules["pymavlink"] = previous_package


def test_mavlink_odometry_reports_unsupported_connection() -> None:
    class FakeMav:
        pass

    class FakeConnection:
        mav = FakeMav()

    bridge = MavlinkVisionBridge("udp:14550")
    bridge._conn = FakeConnection()
    bridge._last_heartbeat_s = time.monotonic()
    result = bridge.send_odometry_match_result(
        {
            "status": "accepted",
            "measurement": {"frame": "local_enu", "x_m": 1.0, "y_m": 2.0},
        }
    )
    assert_equal(result.sent, False, "unsupported ODOMETRY connection send status")
    assert_equal(result.reason, "mavlink_odometry_unsupported", "unsupported ODOMETRY connection reason")


def test_px4_sitl_receiver_evidence_gate() -> None:
    listener = """
TOPIC: vehicle_visual_odometry
 vehicle_visual_odometry
    timestamp: 5000000 (0.010000 seconds ago)
    timestamp_sample: 4999000
    pose_frame: 1
    position: [0.10000, 0.20000, -1.50000]
    q: [1.00000, 0.00000, 0.00000, 0.00000]
    velocity_frame: 1
    velocity: [nan, nan, nan]
    position_variance: [1.50000, 1.50000, 4.00000]
    orientation_variance: [0.25000, 0.25000, 0.25000]
    reset_counter: 0
    quality: 82
TOPIC: vehicle_visual_odometry
 vehicle_visual_odometry
    timestamp: 5200000 (0.020000 seconds ago)
    timestamp_sample: 5199000
    pose_frame: 1
    position: [0.35000, 0.30000, -1.50000]
    q: [1.00000, 0.00000, 0.00000, 0.00000]
    position_variance: [1.50000, 1.50000, 4.00000]
    reset_counter: 0
    quality: 82
"""
    mavlink_status = """
instance #0:
    mavlink chan: #0
    MAVLink version: 2
    transport protocol: UDP (14550)
    accepting commands: YES
    rates:
      tx: 1.2 kB/s
      rx: 0.8 kB/s
"""
    report = evaluate_px4_sitl_evidence(
        listener_text=listener,
        mavlink_status_text=mavlink_status,
        expected_message="odometry",
        config=Px4SitlEvidenceConfig(min_samples=2, max_sample_age_s=1.0, expected_rate_hz=5.0),
    )
    assert_equal(report["status"], "passed", "px4 sitl evidence passed")
    assert_equal(report["listener"]["sample_count"], 2, "px4 sitl evidence sample count")
    assert_equal(report["listener"]["observed_rate_hz"], 5.0, "px4 sitl evidence observed rate")
    assert_equal(report["listener"]["last_position"], [0.35, 0.3, -1.5], "px4 sitl evidence position")
    assert_equal(report["mavlink_status"]["has_udp_14550"], True, "px4 sitl evidence mavlink udp")

    slow = evaluate_px4_sitl_evidence(
        listener_text=listener.replace("timestamp: 5200000", "timestamp: 9000000"),
        expected_message="odometry",
        config=Px4SitlEvidenceConfig(min_samples=2, max_sample_age_s=1.0, expected_rate_hz=5.0),
    )
    assert_equal(slow["status"], "failed", "px4 sitl evidence slow rate failed")
    if "Observed receiver rate" not in " ".join(issue["message"] for issue in slow["issues"]):
        raise AssertionError("Expected slow PX4 receiver evidence to report observed-rate issue")

    failed = evaluate_px4_sitl_evidence(
        listener_text="TOPIC: vehicle_visual_odometry\nnever published\n",
        expected_message="odometry",
        config=Px4SitlEvidenceConfig(min_samples=1),
    )
    assert_equal(failed["status"], "failed", "px4 sitl evidence failed")


def test_px4_sitl_session_evaluator_writes_report_and_flags_missing_captures() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        session_dir = Path(tmp)
        capture_dir = session_dir / "receiver_capture"
        capture_dir.mkdir()
        listener_path = capture_dir / "vehicle_visual_odometry.txt"
        mavlink_status_path = capture_dir / "mavlink_status.txt"
        report_path = session_dir / "receiver_evidence.json"
        listener_path.write_text(
            """
TOPIC: vehicle_visual_odometry
 vehicle_visual_odometry
    timestamp: 5000000 (0.010000 seconds ago)
    timestamp_sample: 4999000
    position: [0.10000, 0.20000, -1.50000]
    q: [1.00000, 0.00000, 0.00000, 0.00000]
    position_variance: [1.50000, 1.50000, 4.00000]
    reset_counter: 0
    quality: 82
TOPIC: vehicle_visual_odometry
 vehicle_visual_odometry
    timestamp: 5200000 (0.020000 seconds ago)
    timestamp_sample: 5199000
    position: [0.35000, 0.30000, -1.50000]
    q: [1.00000, 0.00000, 0.00000, 0.00000]
    position_variance: [1.50000, 1.50000, 4.00000]
    reset_counter: 0
    quality: 82
""".strip()
        )
        mavlink_status_path.write_text(
            """
instance #0:
    mavlink chan: #0
    MAVLink version: 2
    transport protocol: UDP (14550)
    accepting commands: YES
""".strip()
        )
        (session_dir / "px4_sitl_evidence_session.json").write_text(
            json.dumps(
                {
                    "schema_version": "vision_nav_px4_sitl_evidence_session_v1",
                    "version": "0.1.0",
                    "endpoint": "udp:14580",
                    "message_type": "odometry",
                    "rate_hz": 5.0,
                    "repeat_count": 6,
                    "synthetic_log": str(session_dir / "synthetic_external_vision.jsonl"),
                    "capture_instructions": str(session_dir / "receiver_capture" / "README.md"),
                    "expected_captures": {
                        "vehicle_visual_odometry": "receiver_capture/vehicle_visual_odometry.txt",
                        "mavlink_status": "receiver_capture/mavlink_status.txt",
                    },
                    "receiver_report": "receiver_evidence.json",
                    "operator_commands": {
                        "send_synthetic_stream": "VISION_NAV_SITL_SMOKE_DIR=/tmp/px4 ./scripts/dev/px4_sitl_external_vision_smoke.sh",
                        "px4_shell_capture": [
                            "listener vehicle_visual_odometry",
                            "listener vehicle_visual_odometry",
                            "mavlink status",
                        ],
                        "evaluate_session": "./scripts/dev/evaluate_px4_sitl_session.sh /tmp/px4",
                        "automated_capture": "VISION_NAV_SITL_SMOKE_DIR=/tmp/px4 ./scripts/dev/run_px4_sitl_external_vision_capture.sh",
                    },
                    "markers": {
                        "__VISION_NAV_PX4_SITL_SESSION__": str(session_dir),
                        "__VISION_NAV_PX4_SITL_REPORT__": str(session_dir / "receiver_evidence.json"),
                    },
                }
            )
        )

        report = evaluate_px4_sitl_session(
            session_dir,
            config=Px4SitlEvidenceConfig(min_samples=2, max_sample_age_s=1.0),
        )
        assert_equal(report["status"], "passed", "px4 sitl session passed")
        assert_equal(report["listener"]["sample_count"], 2, "px4 sitl session sample count")
        assert_equal(report["config"]["expected_rate_hz"], 5.0, "px4 sitl session manifest rate")
        if not report_path.exists():
            raise AssertionError("Expected px4 sitl session evaluator to write receiver_evidence.json")

        listener_path.unlink()
        failed = evaluate_px4_sitl_session(session_dir)
        assert_equal(failed["status"], "failed", "px4 sitl session missing listener failed")
        messages = " ".join(issue["message"] for issue in failed["issues"])
        if "vehicle_visual_odometry" not in messages:
            raise AssertionError(f"Expected missing listener issue, got {messages}")


def test_px4_param_checker_flags_external_vision_readiness() -> None:
    params = params_from_text(
        """
# QGC/PX4 style parameter export
1 1 EKF2_EV_CTRL 1 6
1 1 EKF2_HGT_REF 0 6
1 1 EKF2_GPS_CTRL 0 6
1 1 EKF2_EV_NOISE_MD 0 6
1 1 EKF2_EV_DELAY 80 9
1 1 EKF2_EV_POS_X 0.0 9
1 1 EKF2_EV_POS_Y 0.0 9
1 1 EKF2_EV_POS_Z -0.05 9
"""
    )
    report = check_px4_external_vision_params(params, gnss_denied=True, extrinsics_measured=True)
    assert_equal(report["status"], "passed", "px4 param checker passed")
    assert_equal(report["parameters"]["EKF2_EV_CTRL_bits"], [0], "px4 ev ctrl bits")

    risky = check_px4_external_vision_params(
        {
            "EKF2_EV_CTRL": 0,
            "EKF2_HGT_REF": 3,
            "EKF2_GPS_CTRL": 7,
            "EKF2_EV_NOISE_MD": 1,
            "EKF2_EV_DELAY": 900,
            "EKF2_EV_POS_X": 0,
            "EKF2_EV_POS_Y": 0,
            "EKF2_EV_POS_Z": 0,
        },
        gnss_denied=True,
    )
    assert_equal(risky["status"], "failed", "px4 param checker risky status")
    messages = " ".join(issue["message"] for issue in risky["issues"])
    for expected in ["bit 0", "Vision", "GNSS-denied"]:
        if expected not in messages:
            raise AssertionError(f"Expected PX4 param issue containing {expected!r}, got {messages}")


def test_ardupilot_param_checker_flags_external_nav_readiness() -> None:
    params = ardupilot_params_from_text(
        """
# Mission Planner style parameter export
EK3_ENABLE,1
EK2_ENABLE,0
AHRS_EKF_TYPE,3
VISO_TYPE,3
VISO_POS_X,0.02
VISO_POS_Y,0.01
VISO_POS_Z,-0.04
EK3_SRC1_POSXY,6
EK3_SRC1_VELXY,0
EK3_SRC1_POSZ,1
EK3_SRC1_VELZ,0
EK3_SRC1_YAW,1
EK3_SRC_OPTIONS,0
GPS_TYPE,0
RC8_OPTION,90
"""
    )
    report = check_ardupilot_external_nav_params(
        params,
        gnss_denied=True,
        extrinsics_measured=True,
        require_source_switch=True,
    )
    assert_equal(report["status"], "passed", "ardupilot param checker passed")
    assert_equal(report["parameters"]["EK3_SRC1_POSXY"], 6, "ardupilot external nav xy source")
    assert_equal(report["parameters"]["source_switch_channels"], ["RC8_OPTION"], "ardupilot source switch")

    risky = check_ardupilot_external_nav_params(
        {
            "EK3_ENABLE": 0,
            "AHRS_EKF_TYPE": 2,
            "VISO_TYPE": 0,
            "VISO_POS_X": 0,
            "VISO_POS_Y": 0,
            "VISO_POS_Z": 0,
            "EK3_SRC1_POSXY": 3,
            "EK3_SRC1_VELXY": 6,
            "EK3_SRC1_POSZ": 6,
            "EK3_SRC1_VELZ": 6,
            "EK3_SRC1_YAW": 6,
            "EK3_SRC_OPTIONS": 1,
            "GPS_TYPE": 1,
        },
        gnss_denied=True,
        require_source_switch=True,
    )
    assert_equal(risky["status"], "failed", "ardupilot param checker risky status")
    messages = " ".join(issue["message"] for issue in risky["issues"])
    for expected in ["EK3_ENABLE", "AHRS_EKF_TYPE", "VISO_TYPE", "POSXY", "RCx_OPTION=90"]:
        if expected not in messages:
            raise AssertionError(f"Expected ArduPilot param issue containing {expected!r}, got {messages}")


def test_external_position_payloads() -> None:
    estimate, reason = external_position_from_match_result(
        {
            "status": "accepted",
            "confidence": 0.73,
            "measurement": {
                "frame": "local_enu",
                "x_m": 4.0,
                "y_m": 7.0,
                "z_m": 3.0,
                "yaw_rad": 0.0,
                "velocity": {
                    "frame": "local_enu",
                    "x_mps": 1.5,
                    "y_mps": -2.5,
                    "z_mps": 0.75,
                    "covariance": {"x_m2": 0.25, "y_m2": 0.36, "z_m2": 0.49},
                },
                "covariance": {"x_m2": 9.0, "y_m2": 16.0, "z_m2": 25.0, "yaw_rad2": 0.2},
            },
        }
    )
    assert_equal(reason, None, "external position parse reason")
    ned = estimate.to_local_ned()
    assert_equal(ned.north_m, 7.0, "external position north")
    assert_equal(ned.east_m, 4.0, "external position east")
    assert_equal(ned.down_m, -3.0, "external position down")
    assert_equal(ned.velocity_north_mps, -2.5, "external position north velocity")
    assert_equal(ned.velocity_east_mps, 1.5, "external position east velocity")
    assert_equal(ned.velocity_down_mps, -0.75, "external position down velocity")
    if not math.isclose(ned.yaw_rad, math.pi / 2.0):
        raise AssertionError("Expected ENU yaw 0 to map to NED yaw pi/2")
    if not math.isclose(yaw_enu_to_ned(math.pi / 2.0), 0.0):
        raise AssertionError("Expected ENU north yaw to map to NED yaw 0")

    vision_payload = build_vision_position_estimate_payload(
        ExternalPositionEstimate(timestamp_us=1, east_m=4.0, north_m=7.0),
        time_usec=555,
    )
    assert_equal(vision_payload.x_north_m, 7.0, "vision payload x north")
    assert_equal(vision_payload.y_east_m, 4.0, "vision payload y east")
    assert_equal(vision_payload.covariance_urt[11], 100.0, "vision payload default z covariance")

    odometry_payload = build_odometry_payload(estimate, time_usec=999, reset_counter=4)
    assert_equal(odometry_payload.frame_id, "MAV_FRAME_LOCAL_FRD", "odometry frame")
    assert_equal(odometry_payload.child_frame_id, "MAV_FRAME_BODY_FRD", "odometry child frame")
    assert_equal(odometry_payload.vx_mps, -2.5, "odometry north velocity")
    assert_equal(odometry_payload.vy_mps, 1.5, "odometry east velocity")
    assert_equal(odometry_payload.vz_mps, -0.75, "odometry down velocity")
    assert_equal(odometry_payload.velocity_covariance_urt[0], 0.36, "odometry north velocity covariance")
    assert_equal(odometry_payload.velocity_covariance_urt[6], 0.25, "odometry east velocity covariance")
    assert_equal(odometry_payload.velocity_covariance_urt[11], 0.49, "odometry down velocity covariance")
    assert_equal(odometry_payload.quality, 73, "odometry quality")

    body_velocity_estimate, body_velocity_reason = external_position_from_match_result(
        {
            "status": "accepted",
            "measurement": {
                "frame": "local_enu",
                "x_m": 1.0,
                "y_m": 2.0,
                "velocity": {"frame": "body_frd", "x_mps": 9.0, "y_mps": 8.0, "z_mps": 7.0},
            },
        }
    )
    assert_equal(body_velocity_reason, None, "external position body velocity parse reason")
    body_velocity_payload = build_odometry_payload(body_velocity_estimate, time_usec=1000)
    if not math.isnan(body_velocity_payload.vx_mps) or not math.isnan(body_velocity_payload.vy_mps):
        raise AssertionError("Expected non-local velocity frame to be ignored")
    if not math.isnan(body_velocity_payload.velocity_covariance_urt[0]):
        raise AssertionError("Expected non-local velocity covariance frame to be ignored")

    tracker = OdometryResetTracker()
    assert_equal(
        tracker.update_from_result({"timestamp_us": 100, "map_id": "a", "estimator": {"reset_counter": 1}}),
        0,
        "odometry reset counter first result",
    )
    assert_equal(
        tracker.update_from_result({"timestamp_us": 110, "map_id": "a", "estimator": {"reset_counter": 2}}),
        1,
        "odometry reset counter estimator reset",
    )
    assert_equal(
        tracker.update_from_result({"timestamp_us": 120, "map_id": "b", "estimator": {"reset_counter": 2}}),
        2,
        "odometry reset counter map change",
    )


def test_external_position_stream_health() -> None:
    health = ExternalPositionStreamHealth(ExternalPositionHealthConfig(min_rate_hz=0.5, max_latency_ms=200.0))
    result = {
        "status": "accepted",
        "timestamp_us": 1_000_000,
        "measurement": {
            "frame": "local_enu",
            "x_m": 1.0,
            "y_m": 2.0,
            "covariance": {"x_m2": 4.0, "y_m2": 5.0, "z_m2": None, "yaw_rad2": None},
        },
    }
    first = health.update(
        result=result,
        mavlink_result={"sent": True},
        message_type="odometry",
        now_monotonic_s=10.0,
        now_time_us=1_050_000,
    ).to_dict()
    assert_equal(first["status"], "warming_up", "external position health first status")
    assert_equal(first["last_latency_ms"], 50.0, "external position health latency")
    second = health.update(
        result={**result, "timestamp_us": 2_000_000},
        mavlink_result={"sent": True},
        message_type="odometry",
        now_monotonic_s=11.0,
        now_time_us=2_050_000,
    ).to_dict()
    assert_equal(second["status"], "healthy", "external position health second status")
    assert_equal(second["send_rate_hz"], 1.0, "external position health rate")

    velocity_health = ExternalPositionStreamHealth(
        ExternalPositionHealthConfig(min_rate_hz=0.1, max_velocity_variance_m2ps2=0.5)
    )
    velocity_result = {
        **result,
        "measurement": {
            **result["measurement"],
            "velocity": {"frame": "local_enu", "x_mps": 1.0, "y_mps": 2.0},
        },
    }
    missing_velocity_covariance = velocity_health.update(
        result=velocity_result,
        mavlink_result={
            "sent": True,
            "message": "ODOMETRY",
            "details": {"has_velocity": True, "has_velocity_covariance": False},
        },
        message_type="odometry",
        now_monotonic_s=20.0,
        now_time_us=1_050_000,
    ).to_dict()
    if "velocity_covariance_missing" not in missing_velocity_covariance["last_warnings"]:
        raise AssertionError("Expected external position health to warn when sent velocity lacks covariance")

    high_velocity_covariance = velocity_health.update(
        result={
            **velocity_result,
            "timestamp_us": 2_000_000,
            "measurement": {
                **velocity_result["measurement"],
                "velocity": {
                    "frame": "local_enu",
                    "x_mps": 1.0,
                    "y_mps": 2.0,
                    "covariance": {"x_m2": 0.2, "y_m2": 0.8},
                },
            },
        },
        mavlink_result={
            "sent": True,
            "message": "ODOMETRY",
            "details": {"has_velocity": True, "has_velocity_covariance": True},
        },
        message_type="odometry",
        now_monotonic_s=21.0,
        now_time_us=2_050_000,
    ).to_dict()
    if "velocity_covariance_high" not in high_velocity_covariance["last_warnings"]:
        raise AssertionError("Expected external position health to warn on high velocity covariance")


def test_ros2_odometry_and_diagnostics_adapters() -> None:
    odometry, reason = odometry_dict_from_match_result(
        {
            "status": "accepted",
            "timestamp_us": 1_234_567,
            "confidence": 0.8,
            "measurement": {
                "frame": "local_enu",
                "x_m": 4.0,
                "y_m": 7.0,
                "z_m": 3.0,
                "yaw_rad": math.pi / 2.0,
                "covariance": {"x_m2": 9.0, "y_m2": 16.0, "z_m2": 25.0, "yaw_rad2": 0.2},
            },
        }
    )
    assert_equal(reason, None, "ROS 2 odometry skip reason")
    assert_equal(odometry["header"]["stamp"], {"sec": 1, "nanosec": 234_567_000}, "ROS 2 stamp")
    assert_equal(odometry["pose"]["pose"]["position"], {"x": 4.0, "y": 7.0, "z": 3.0}, "ROS 2 position")
    assert_equal(odometry["pose"]["covariance"][0], 9.0, "ROS 2 east covariance")
    assert_equal(odometry["pose"]["covariance"][7], 16.0, "ROS 2 north covariance")
    healthy = diagnostic_status_from_health({"status": "healthy", "sent_count": 1, "message_type": "odometry"})
    degraded = diagnostic_status_from_health({"status": "degraded", "sent_count": 0, "message_type": "odometry"})
    assert_equal(healthy["level"], DIAG_OK, "ROS 2 healthy diagnostic level")
    assert_equal(degraded["level"], DIAG_ERROR, "ROS 2 degraded diagnostic level")


def test_ros2_bag_jsonl_export_writes_topic_records() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        frame = root / "frames" / "frame_000001.png"
        frame.parent.mkdir(parents=True)
        write_minimal_png(frame, 2, 2)
        log = root / "terrain_matches.jsonl"
        log.write_text(
            "\n".join(
                [
                    json.dumps(
                        {
                            "sequence": 1,
                            "timestamp_us": 1_000_000,
                            "frame_path": "frames/frame_000001.png",
                            "result": {
                                "status": "accepted",
                                "timestamp_us": 1_000_000,
                                "confidence": 0.8,
                                "measurement": {
                                    "frame": "local_enu",
                                    "x_m": 1.0,
                                    "y_m": 2.0,
                                    "z_m": 0.5,
                                    "covariance": {"x_m2": 4.0, "y_m2": 4.0, "z_m2": 9.0},
                                },
                            },
                        }
                    ),
                    json.dumps(
                        {
                            "sequence": 2,
                            "timestamp_us": 2_000_000,
                            "result": {"status": "rejected", "reason": "low_inliers"},
                        }
                    ),
                ]
            )
            + "\n"
        )
        records = ros_records_from_log(log)
        result = export_rosbag_jsonl(
            records,
            root / "rosbag-jsonl",
            source_log=log,
            frame_topic="/vision_nav/camera/image/compressed",
        )
        metadata = json.loads(Path(result["metadata_path"]).read_text())
        messages = [json.loads(line) for line in Path(result["messages_path"]).read_text().splitlines()]
        assert_equal(metadata["format"], "vision_nav_rosbag_jsonl_v1", "rosbag jsonl format")
        assert_equal(metadata["message_count"], 4, "rosbag jsonl message count")
        assert_equal(metadata["topics"][0]["type"], "sensor_msgs/msg/CompressedImage", "rosbag frame topic type")
        assert_equal(metadata["topics"][0]["message_count"], 1, "rosbag frame count")
        assert_equal(metadata["topics"][1]["message_count"], 1, "rosbag odometry count")
        assert_equal(metadata["topics"][2]["message_count"], 2, "rosbag diagnostics count")
        assert_equal(metadata["frame_export"]["enabled"], True, "rosbag frame export enabled")
        assert_equal(messages[0]["topic"], "/vision_nav/camera/image/compressed", "first rosbag jsonl topic")
        assert_equal(messages[0]["message"]["format"], "png", "rosbag frame format")
        assert_equal(messages[0]["message"]["metadata"]["source_name"], "frame_000001.png", "rosbag frame source")
        if not messages[0]["message"]["data_base64"]:
            raise AssertionError("Expected frame export to include base64 image payload")
        assert_equal(messages[1]["topic"], "/vision_nav/odometry", "second rosbag jsonl topic")
        assert_equal(messages[2]["topic"], "/diagnostics", "third rosbag jsonl topic")
        assert_equal(messages[3]["message"]["status"][0]["message"], "terrain match rejected: low_inliers", "rejected diagnostic message")
        validation = validate_rosbag_export(root / "rosbag-jsonl")
        assert_equal(validation["status"], "passed", "rosbag jsonl validation status")
        assert_equal(validation["message_count"], 4, "rosbag jsonl validation message count")
        assert_equal(validation["details"]["topic_counts"]["/diagnostics"], 2, "rosbag jsonl validation diagnostics count")

        broken_dir = root / "broken-rosbag-jsonl"
        shutil.copytree(root / "rosbag-jsonl", broken_dir)
        broken_metadata_path = broken_dir / "metadata.json"
        broken_metadata = json.loads(broken_metadata_path.read_text())
        broken_metadata["message_count"] = 99
        broken_metadata_path.write_text(json.dumps(broken_metadata))
        broken_validation = validate_rosbag_export(broken_dir)
        assert_equal(broken_validation["status"], "failed", "broken rosbag jsonl validation status")

        diagnostics_only_dir = root / "diagnostics-only-rosbag-jsonl"
        diagnostics_only_dir.mkdir()
        (diagnostics_only_dir / "metadata.json").write_text(
            json.dumps(
                {
                    "format": "vision_nav_rosbag_jsonl_v1",
                    "message_file": "messages.jsonl",
                    "message_count": 1,
                    "topics": [
                        {
                            "name": "/diagnostics",
                            "type": "diagnostic_msgs/msg/DiagnosticArray",
                            "message_count": 1,
                        }
                    ],
                }
            )
        )
        (diagnostics_only_dir / "messages.jsonl").write_text(
            json.dumps(
                {
                    "topic": "/diagnostics",
                    "type": "diagnostic_msgs/msg/DiagnosticArray",
                    "timestamp_ns": 1,
                    "message": {"status": []},
                }
            )
            + "\n"
        )
        missing_topic_validation = validate_rosbag_export(diagnostics_only_dir)
        assert_equal(missing_topic_validation["status"], "failed", "rosbag validation missing odometry topic status")
        assert_equal(
            missing_topic_validation["missing_required_topics"],
            ["/vision_nav/odometry"],
            "rosbag validation missing required topic",
        )

        class FakeMcapWriter:
            instances: list["FakeMcapWriter"] = []

            def __init__(self, stream) -> None:
                self.stream = stream
                self.schemas: list[dict[str, object]] = []
                self.channels: list[dict[str, object]] = []
                self.messages: list[dict[str, object]] = []
                FakeMcapWriter.instances.append(self)

            def start(self) -> None:
                self.stream.write(b"FAKE-MCAP\n")

            def register_schema(self, *, name: str, encoding: str, data: bytes) -> int:
                self.schemas.append({"name": name, "encoding": encoding, "data": data})
                return len(self.schemas)

            def register_channel(self, *, topic: str, message_encoding: str, schema_id: int) -> int:
                self.channels.append({"topic": topic, "message_encoding": message_encoding, "schema_id": schema_id})
                return len(self.channels)

            def add_message(self, *, channel_id: int, log_time: int, publish_time: int, data: bytes) -> None:
                self.messages.append(
                    {
                        "channel_id": channel_id,
                        "log_time": log_time,
                        "publish_time": publish_time,
                        "data": json.loads(data.decode("utf-8")),
                    }
                )

            def finish(self) -> None:
                self.stream.write(b"FINISH\n")

        mcap_result = export_rosbag_mcap(
            records,
            root / "rosbag.mcap",
            source_log=log,
            frame_topic="/vision_nav/camera/image/compressed",
            writer_factory=FakeMcapWriter,
        )
        mcap_metadata = json.loads(Path(mcap_result["metadata_path"]).read_text())
        fake_writer = FakeMcapWriter.instances[-1]
        assert_equal(mcap_metadata["format"], "vision_nav_mcap_json_v1", "mcap metadata format")
        assert_equal(mcap_metadata["message_count"], 4, "mcap message count")
        assert_equal(len(fake_writer.schemas), 3, "mcap schema count")
        assert_equal(len(fake_writer.channels), 3, "mcap channel count")
        assert_equal(len(fake_writer.messages), 4, "mcap writer message count")
        if not Path(mcap_result["mcap_path"]).read_bytes().startswith(b"FAKE-MCAP"):
            raise AssertionError("Expected fake MCAP writer to write output file")
        mcap_validation = validate_rosbag_export(mcap_result["mcap_path"])
        assert_equal(mcap_validation["status"], "passed", "mcap validation status")
        assert_equal(mcap_validation["format"], "vision_nav_mcap_json_v1", "mcap validation format")

        class FakeStamp:
            def __init__(self) -> None:
                self.sec = 0
                self.nanosec = 0

        class FakeHeader:
            def __init__(self) -> None:
                self.stamp = FakeStamp()
                self.frame_id = ""

        class FakeVector3:
            def __init__(self) -> None:
                self.x = 0.0
                self.y = 0.0
                self.z = 0.0

        class FakeQuaternion:
            def __init__(self) -> None:
                self.x = 0.0
                self.y = 0.0
                self.z = 0.0
                self.w = 1.0

        class FakePose:
            def __init__(self) -> None:
                self.position = FakeVector3()
                self.orientation = FakeQuaternion()

        class FakePoseWithCovariance:
            def __init__(self) -> None:
                self.pose = FakePose()
                self.covariance: list[float] = []

        class FakeTwist:
            def __init__(self) -> None:
                self.linear = FakeVector3()
                self.angular = FakeVector3()

        class FakeTwistWithCovariance:
            def __init__(self) -> None:
                self.twist = FakeTwist()
                self.covariance: list[float] = []

        class FakeOdometry:
            def __init__(self) -> None:
                self.header = FakeHeader()
                self.child_frame_id = ""
                self.pose = FakePoseWithCovariance()
                self.twist = FakeTwistWithCovariance()

        class FakeKeyValue:
            def __init__(self) -> None:
                self.key = ""
                self.value = ""

        class FakeDiagnosticStatus:
            def __init__(self) -> None:
                self.level = 0
                self.name = ""
                self.message = ""
                self.hardware_id = ""
                self.values: list[FakeKeyValue] = []

        class FakeDiagnosticArray:
            def __init__(self) -> None:
                self.header = FakeHeader()
                self.status: list[FakeDiagnosticStatus] = []

        class FakeCompressedImage:
            def __init__(self) -> None:
                self.header = FakeHeader()
                self.format = ""
                self.data = b""

        class FakeSequentialWriter:
            instances: list["FakeSequentialWriter"] = []

            def __init__(self) -> None:
                self.open_args = None
                self.topics: list[object] = []
                self.messages: list[tuple[str, bytes, int]] = []
                FakeSequentialWriter.instances.append(self)

            def open(self, storage_options, converter_options) -> None:
                self.open_args = (storage_options, converter_options)

            def create_topic(self, metadata) -> None:
                self.topics.append(metadata)

            def write(self, topic: str, data: bytes, timestamp_ns: int) -> None:
                self.messages.append((topic, data, timestamp_ns))

        class FakeStorageOptions:
            def __init__(self, *, uri: str, storage_id: str) -> None:
                self.uri = uri
                self.storage_id = storage_id

        class FakeConverterOptions:
            def __init__(self, *, input_serialization_format: str, output_serialization_format: str) -> None:
                self.input_serialization_format = input_serialization_format
                self.output_serialization_format = output_serialization_format

        class FakeTopicMetadata:
            def __init__(self, *, name: str, type: str, serialization_format: str, offered_qos_profiles: str = "") -> None:
                self.name = name
                self.type = type
                self.serialization_format = serialization_format
                self.offered_qos_profiles = offered_qos_profiles

        def fake_serialize_message(message) -> bytes:
            return message.__class__.__name__.encode("utf-8")

        rosbag2_result = export_rosbag2(
            records,
            root / "rosbag2-native",
            source_log=log,
            frame_topic="/vision_nav/camera/image/compressed",
            writer_factory=FakeSequentialWriter,
            storage_options_cls=FakeStorageOptions,
            converter_options_cls=FakeConverterOptions,
            topic_metadata_cls=FakeTopicMetadata,
            message_classes={
                "nav_msgs/msg/Odometry": FakeOdometry,
                "diagnostic_msgs/msg/DiagnosticArray": FakeDiagnosticArray,
                "diagnostic_msgs/msg/DiagnosticStatus": FakeDiagnosticStatus,
                "diagnostic_msgs/msg/KeyValue": FakeKeyValue,
                "sensor_msgs/msg/CompressedImage": FakeCompressedImage,
            },
            serializer=fake_serialize_message,
        )
        rosbag2_metadata = json.loads(Path(rosbag2_result["metadata_path"]).read_text())
        fake_rosbag2_writer = FakeSequentialWriter.instances[-1]
        assert_equal(rosbag2_metadata["format"], "vision_nav_rosbag2_v1", "rosbag2 metadata format")
        assert_equal(rosbag2_metadata["message_count"], 4, "rosbag2 message count")
        assert_equal(len(fake_rosbag2_writer.topics), 3, "rosbag2 topic count")
        assert_equal(len(fake_rosbag2_writer.messages), 4, "rosbag2 writer message count")
        assert_equal(fake_rosbag2_writer.open_args[0].storage_id, "sqlite3", "rosbag2 storage id")
        assert_equal(fake_rosbag2_writer.messages[0][0], "/vision_nav/camera/image/compressed", "rosbag2 first topic")
        if fake_rosbag2_writer.messages[0][1] != b"FakeCompressedImage":
            raise AssertionError("Expected rosbag2 frame event to serialize as FakeCompressedImage")
        fake_storage = Path(rosbag2_result["output_dir"]) / "rosbag2_0.db3"
        fake_storage.write_bytes(b"sqlite")
        rosbag2_validation = validate_rosbag_export(rosbag2_result["output_dir"])
        assert_equal(rosbag2_validation["status"], "passed", "rosbag2 validation status")
        assert_equal(rosbag2_validation["details"]["storage_files"], ["rosbag2_0.db3"], "rosbag2 validation storage files")
        fake_ros2 = root / "fake-ros2"
        fake_ros2.write_text(
            "#!/usr/bin/env python3\n"
            "import sys\n"
            "print('ros2 bag info review')\n"
            "print(' '.join(sys.argv[1:]))\n"
        )
        fake_ros2.chmod(0o755)
        cli_review_path = root / "rosbag2-cli-review.json"
        cli_review = review_rosbag2_cli(
            rosbag2_result["output_dir"],
            output_path=cli_review_path,
            ros2_command=str(fake_ros2),
        )
        assert_equal(cli_review["status"], "passed", "rosbag2 cli review status")
        assert_equal(cli_review["ros2_cli"]["exit_code"], 0, "rosbag2 cli review exit")
        if "ros2 bag info review" not in cli_review["ros2_cli"]["stdout"]:
            raise AssertionError("Expected rosbag2 CLI review to capture ros2 bag info output")
        written_review = json.loads(cli_review_path.read_text())
        assert_equal(written_review["schema_version"], "vision_nav_rosbag2_cli_review_v1", "rosbag2 cli review schema")
        skipped_review = review_rosbag2_cli(rosbag2_result["output_dir"], skip_ros2=True)
        assert_equal(skipped_review["status"], "degraded", "rosbag2 skipped cli review status")
        non_native_review = review_rosbag2_cli(root / "rosbag-jsonl", skip_ros2=True)
        assert_equal(non_native_review["status"], "failed", "rosbag2 cli review rejects non-native export")


def test_ros2_launch_profiles_static() -> None:
    root = Path(__file__).resolve().parents[1]
    live = (root / "ros2" / "launch" / "terrain_nav_live.launch.py").read_text()
    replay = (root / "ros2" / "launch" / "terrain_nav_replay.launch.py").read_text()
    for expected in (
        "vision_nav.run_terrain_loop",
        "--ros2-publish",
        "/vision_nav/odometry",
        "/diagnostics",
        "--mavlink-endpoint",
        "--mavlink-message",
        "odometry",
        "--external-position-min-rate-hz",
    ):
        if expected not in live:
            raise AssertionError(f"Live ROS 2 launch profile missing {expected}")
    for expected in ("vision_nav.ros2_bridge", "--publish", "/vision_nav/odometry", "/diagnostics"):
        if expected not in replay:
            raise AssertionError(f"Replay ROS 2 launch profile missing {expected}")


def test_ros2_package_wrapper_static() -> None:
    root = Path(__file__).resolve().parents[1]
    package_root = root / "ros2" / "drone_vision_nav"
    package_xml = (package_root / "package.xml").read_text()
    setup_py = (package_root / "setup.py").read_text()
    live_wrapper = (package_root / "drone_vision_nav" / "terrain_nav_live.py").read_text()
    replay_wrapper = (package_root / "drone_vision_nav" / "terrain_nav_replay.py").read_text()
    for expected in ("<name>drone_vision_nav</name>", "<build_type>ament_python</build_type>", "rclpy", "nav_msgs", "sensor_msgs"):
        if expected not in package_xml:
            raise AssertionError(f"ROS 2 package.xml missing {expected}")
    for expected in (
        "terrain_nav_live = drone_vision_nav.terrain_nav_live:main",
        "terrain_nav_replay = drone_vision_nav.terrain_nav_replay:main",
        "share/{PACKAGE_NAME}/launch",
        "vision_nav",
    ):
        if expected not in setup_py:
            raise AssertionError(f"ROS 2 setup.py missing {expected}")
    if "vision_nav.run_terrain_loop" not in live_wrapper:
        raise AssertionError("Live ROS 2 wrapper does not call terrain runtime")
    if "vision_nav.ros2_bridge" not in replay_wrapper:
        raise AssertionError("Replay ROS 2 wrapper does not call ROS 2 bridge")


def test_camera_health_report_on_synthetic_image() -> None:
    try:
        import cv2
        from vision_nav.camera_health import analyze_frame_file
    except ModuleNotFoundError as exc:
        if exc.name == "cv2":
            raise SkipTest("requires cv2") from exc
        raise

    with tempfile.TemporaryDirectory() as tmp:
        image_path = Path(tmp) / "synthetic_camera_frame.png"
        rng = np.random.default_rng(42)
        image = rng.integers(0, 256, size=(160, 220), dtype=np.uint8)
        cv2.rectangle(image, (20, 20), (90, 80), 180, -1)
        cv2.circle(image, (150, 80), 35, 255, -1)
        cv2.line(image, (0, 130), (219, 20), 120, 3)
        cv2.imwrite(str(image_path), image)

        report = analyze_frame_file(
            image_path,
            sharpness_min=0.0,
            entropy_min=0.0,
            feature_density_min=0.0,
            saturation_max=1.0,
        )

        assert_equal(report["status"], "passed", "camera health status")
        assert_equal(report["resolution"], {"width": 220, "height": 160}, "camera health resolution")
        if report["feature_count"] <= 0:
            raise AssertionError("Expected synthetic camera frame to produce features")


def test_bundle_checksums_detect_changed_file() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        bundle = Path(tmp) / "bundle"
        bundle.mkdir()
        (bundle / "manifest.json").write_text(
            json.dumps(
                {
                    "bundle_id": "checksum-test",
                    "orthophoto": {
                        "path": "map.png",
                        "origin_lat": 40.0,
                        "origin_lon": -75.0,
                        "gsd_m": 0.2,
                    },
                    "features": {"path": "features.npz"},
                }
            )
        )
        (bundle / "map.png").write_text("map-v1")
        (bundle / "features.npz").write_text("features-v1")
        (bundle / "bundle_health.json").write_text('{"status":"old"}\n')

        written = write_checksum_file(bundle)
        assert_equal(written["entry_count"], 3, "checksum entry count")
        if any(entry["path"] == "bundle_health.json" for entry in written["entries"]):
            raise AssertionError("bundle_health.json should not be included in bundle checksums")
        verified = verify_checksum_file(bundle)
        assert_equal(verified["status"], "passed", "checksum verified status")

        (bundle / "bundle_health.json").write_text('{"status":"new"}\n')
        verified_after_health_update = verify_checksum_file(bundle)
        assert_equal(verified_after_health_update["status"], "passed", "checksum ignores regenerated health")

        (bundle / "map.png").write_text("map-v2")
        failed = verify_checksum_file(bundle)
        assert_equal(failed["status"], "failed", "checksum mismatch status")
        assert_equal(failed["mismatched"][0]["path"], "map.png", "checksum mismatch path")


def test_validate_bundle_passes_complete_bundle() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        bundle = Path(tmp) / "bundle"
        (bundle / "ortho").mkdir(parents=True)
        (bundle / "features").mkdir()
        (bundle / "calibration").mkdir()
        (bundle / "ortho" / "map.png").write_bytes(b"not-a-real-image-but-present")
        np.savez_compressed(
            bundle / "features" / "map_features.npz",
            image_path=np.array("ortho/map.png"),
            image_shape=np.array((10, 20), dtype=np.int32),
            method=np.array("orb"),
            keypoints_xy=np.zeros((2, 2), dtype=np.float32),
            descriptors=np.zeros((2, 32), dtype=np.uint8),
        )
        (bundle / "calibration" / "down_camera.yaml").write_text(
            """
camera_name: test_camera
image_width: 20
image_height: 10
distortion_model: plumb_bob
camera_matrix:
  rows: 3
  cols: 3
  data: [1.0, 0.0, 10.0, 0.0, 1.0, 5.0, 0.0, 0.0, 1.0]
distortion_coefficients:
  rows: 1
  cols: 5
  data: [0.0, 0.0, 0.0, 0.0, 0.0]
""".strip()
        )
        (bundle / "calibration" / "camera_to_body.yaml").write_text(
            """
frame_id: body
child_frame_id: down_camera
translation_m: {x: 0.0, y: 0.0, z: 0.0}
rotation_quat_xyzw: {x: 0.0, y: 0.0, z: 0.0, w: 1.0}
""".strip()
        )
        (bundle / "manifest.json").write_text(
            json.dumps(
                {
                    "bundle_id": "validate-test",
                    "orthophoto": {
                        "path": "ortho/map.png",
                        "origin_lat": 40.0,
                        "origin_lon": -75.0,
                        "gsd_m": 0.2,
                    },
                    "features": {
                        "path": "features/map_features.npz",
                        "method": "orb",
                        "max_features": 100,
                    },
                    "calibration": {
                        "down_camera": "calibration/down_camera.yaml",
                        "camera_to_body": "calibration/camera_to_body.yaml",
                    },
                }
            )
        )

        write_checksum_file(bundle)
        summary = validate_bundle(bundle, require_features=True, require_calibration=True, require_checksums=True)
        assert_equal(summary["status"], "passed", "bundle validation status")
        assert_equal(summary["feature_index"]["keypoints"], 2, "bundle validation keypoints")
        assert_equal(summary["checksums"]["status"], "passed", "bundle validation checksums")


def test_bundle_diagnostics_finds_bundle_and_map_source_candidates() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        expected_bundle = root / "drone-data" / "map_bundles" / "mission_bundle"
        candidate_bundle = create_minimal_terrain_bundle(root / "candidate")
        map_source = root / "map-source" / "field-area"
        map_source.mkdir(parents=True)
        write_minimal_png(map_source / "satellite.png", 80, 50)
        (map_source / "metadata.json").write_text(
            json.dumps(
                {
                    "id": "field-area",
                    "name": "Field Area",
                    "source": "uploaded_geotiff",
                    "origin_lat": 40.0,
                    "origin_lon": -75.0,
                    "gsd_m_per_px": 0.2,
                    "georef_source": "geotiff_embedded",
                    "georef_confidence": 0.9,
                    "georef_crs": "EPSG:4326",
                    "width_px": 80,
                    "height_px": 50,
                }
            )
        )

        report = diagnose_bundle_inputs(expected_bundle, search_roots=[root])
        assert_equal(report["bundle_exists"], False, "bundle diagnostic missing expected bundle")
        if "manifest.json" not in report["missing_required_files"]:
            raise AssertionError("Expected diagnostic to list missing manifest")
        candidate_paths = {item["path"] for item in report["bundle_candidates"]}
        if str(candidate_bundle) not in candidate_paths:
            raise AssertionError(f"Expected candidate bundle {candidate_bundle}, got {candidate_paths}")
        source_paths = {item["path"] for item in report["map_source_candidates"]}
        if str(map_source) not in source_paths:
            raise AssertionError(f"Expected map source {map_source}, got {source_paths}")
        compact = compact_bundle_diagnostic(report)
        if compact["bundle_candidate_count"] < 1:
            raise AssertionError("Expected compact diagnostic to count bundle candidates")
        if compact["map_source_candidate_count"] < 1:
            raise AssertionError("Expected compact diagnostic to count map source candidates")
        if compact["recommended_actions"][0]["id"] != "build_or_upload_selected_bundle":
            raise AssertionError("Expected bundle build/upload to remain the first diagnostic action")


def test_geospatial_health_report_validates_stac_tiles_and_bounds() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        bundle = create_minimal_terrain_bundle(Path(tmp), include_elevation=True)

        report = geospatial_health_report(bundle)
        if report["status"] not in {"passed", "degraded"}:
            raise AssertionError(f"geospatial health status: expected passed/degraded, got {report['status']}")
        assert_equal(report["raster"]["format"], "PNG", "geospatial raster format")
        assert_equal("available" in report["raster"]["gdal"], True, "geospatial raster gdal metadata")
        assert_equal(report["tile_index"]["tile_count"], 1, "geospatial tile count")
        assert_equal(report["map_quality"]["estimated_pi_runtime_cost"], "low", "geospatial runtime cost")
        assert_equal(report["map_quality"]["heatmap"]["row_count"], 1, "geospatial heatmap rows")
        assert_equal(report["map_quality"]["heatmap"]["col_count"], 1, "geospatial heatmap cols")
        assert_equal(report["map_quality"]["heatmap"]["cells"][0]["tile_id"], "tile_000000", "geospatial heatmap tile")
        assert_equal(report["map_quality"]["heatmap"]["cells"][0]["quality"], "dense", "geospatial heatmap quality")
        assert_equal(report["checksums"]["status"], "missing", "geospatial missing checksum status")
        assert_equal(report["source_provenance"]["map_source"], "uploaded_geotiff", "source provenance source")
        assert_equal(report["source_provenance"]["original_file"], "unit-map.tif", "source provenance original")
        assert_equal(report["elevation"]["status"], "passed", "elevation health status")
        assert_equal(report["elevation"]["asset_count"], 2, "elevation asset count")
        assert_equal(report["elevation"]["dem_present"], True, "elevation dem present")
        assert_equal(report["elevation"]["dsm_present"], True, "elevation dsm present")
        assert_equal(report["elevation"]["vertical_sanity_ready"], True, "elevation vertical sanity ready")
        assert_equal("gdal_status" in report["elevation"]["assets"][0], True, "elevation gdal status")
        if report["georef"]["bounds"]["width_m"] <= 0:
            raise AssertionError("Expected geospatial bounds width to be positive")

        write_checksum_file(bundle)
        report_with_checksums = geospatial_health_report(bundle)
        if report_with_checksums["status"] not in {"passed", "degraded"}:
            raise AssertionError(f"geospatial checksum health status: expected passed/degraded, got {report_with_checksums['status']}")
        assert_equal(report_with_checksums["checksums"]["status"], "passed", "geospatial checksum passed")
        assert_equal(report_with_checksums["checksums"]["extra_file_count"], 0, "geospatial checksum ignores health file")


def test_gdal_metadata_degrades_gracefully_when_unavailable() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "tiny.tif"
        write_minimal_tiff(path, 4, 3)
        metadata = gdal_raster_metadata(path)
        assert_equal("available" in metadata, True, "gdal metadata availability flag")
        if metadata["available"]:
            assert_equal(metadata["openable"], True, "gdal opened tiny tiff")
            assert_equal(metadata["width_px"], 4, "gdal width")
            assert_equal(metadata["height_px"], 3, "gdal height")
            assert_equal("cog" in metadata, True, "gdal cog metadata")
        else:
            assert_equal(metadata["status"], "not_available", "gdal unavailable status")


def test_terrain_profile_reports_agl_and_gsd_warnings() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        bundle = create_minimal_terrain_bundle(Path(tmp), include_elevation=True)
        georef = SimpleGeoReference(origin_lat=40.0, origin_lon=-75.0, gsd_m=0.5, crs="EPSG:4326")
        values = np.tile(np.linspace(0.0, 20.0, 100, dtype=np.float32), (60, 1))
        write_scalar_float_tiff(bundle / "elevation" / "dsm.tif", values)
        start_lat, start_lon = georef.pixel_to_latlon(0, 10)
        end_lat, end_lon = georef.pixel_to_latlon(99, 10)
        (bundle / "mission").mkdir(exist_ok=True)
        (bundle / "mission" / "mission_plan.json").write_text(
            json.dumps(
                {
                    "mission": {
                        "altitude_m": 25,
                        "speed_mps": 4,
                        "items": [
                            {"id": "takeoff", "type": "takeoff", "lat": start_lat, "lon": start_lon, "altitudeM": 25},
                            {"id": "wp1", "type": "waypoint", "lat": end_lat, "lon": end_lon, "altitudeM": 25},
                        ],
                    }
                }
            )
        )
        manifest_path = bundle / "manifest.json"
        manifest = json.loads(manifest_path.read_text())
        manifest["mission"] = {"desktop_plan_path": "mission/mission_plan.json"}
        manifest_path.write_text(json.dumps(manifest))

        report = geospatial_health_report(bundle)
        profile = report["terrain_profile"]
        assert_equal(profile["status"], "degraded", "terrain profile status")
        assert_equal(profile["surface_source"], "dsm", "terrain profile source")
        if abs(profile["terrain_elevation_m"]["relief"] - 20.0) > 0.5:
            raise AssertionError(f"Expected terrain relief near 20 m, got {profile['terrain_elevation_m']['relief']}")
        if abs(profile["estimated_agl_m"]["min"] - 5.0) > 0.75:
            raise AssertionError(f"Expected min AGL near 5 m, got {profile['estimated_agl_m']['min']}")
        if len(profile["preview_points"]) < 2:
            raise AssertionError("Expected terrain profile preview points")
        assert_equal(profile["preview_points"][0]["distance_m"], 0.0, "terrain profile first preview distance")
        if profile["preview_points"][-1]["distance_m"] <= profile["preview_points"][0]["distance_m"]:
            raise AssertionError("Expected terrain profile preview distance to increase")
        messages = " ".join(issue["message"] for issue in profile["issues"])
        if "Minimum estimated AGL is low" not in messages:
            raise AssertionError(f"Expected low AGL warning, got {messages}")


def test_runtime_status_snapshot_reports_active_map_and_last_match() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bundle_path = create_minimal_terrain_bundle(root)
        bundle = load_terrain_bundle(bundle_path)
        output_dir = root / "terrain-match"
        log_path = output_dir / "terrain_matches.jsonl"
        record = {
            "timestamp_utc": "2026-06-21T00:00:00Z",
            "frame_path": str(output_dir / "frames" / "frame.jpg"),
            "capture_duration_s": 0.1,
            "match_duration_s": 0.2,
            "telemetry": [{"message_type": "ATTITUDE", "timestamp_us": 123}],
            "external_position_health": {"status": "healthy", "message_type": "odometry"},
            "mavlink": {"sent": True, "message": "ODOMETRY"},
            "result": {
                "status": "accepted",
                "tile_id": "tile_000000",
                "confidence": 0.82,
                "scale_confidence": 0.74,
                "inliers": 31,
                "reprojection_error_px": 1.8,
                "local_enu_m": {"x": 1.0, "y": 2.0, "z": None},
                "lat_lon": {"lat": 40.0, "lon": -75.0},
                "covariance": {"x_m2": 4.0, "y_m2": 5.0, "z_m2": None},
                "estimator": {"initialized": True, "health": "tracking", "last_update_timestamp_us": 123},
            },
        }
        status = runtime_status_snapshot(
            bundle=bundle,
            output_dir=output_dir,
            log_path=log_path,
            sequence=3,
            record=record,
            status_counts={"accepted": 2, "rejected": 1},
            started_at_utc="2026-06-21T00:00:00+00:00",
        )
        status_path = output_dir / "runtime_status.json"
        write_runtime_status(status_path, status)
        saved = json.loads(status_path.read_text())
        assert_equal(saved["schema_version"], "vision_nav_runtime_status_v1", "runtime status schema")
        assert_equal(saved["active_map"]["bundle_id"], "health-test", "runtime status bundle id")
        assert_equal(saved["active_map"]["has_tile_index"], True, "runtime status tile index")
        assert_equal(saved["output"]["log_path"], str(log_path), "runtime status log path")
        assert_equal(saved["last_match"]["status"], "accepted", "runtime status last match")
        assert_equal(saved["last_match"]["tile_id"], "tile_000000", "runtime status tile id")
        assert_equal(saved["estimator"]["health"], "tracking", "runtime status estimator health")
        assert_equal(saved["external_position"]["status"], "healthy", "runtime status external position")
        assert_equal(saved["status_counts"], {"accepted": 2, "rejected": 1}, "runtime status counts")


def test_support_bundle_collects_manifest_health_logs_and_summary() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bundle = create_minimal_terrain_bundle(root, include_elevation=True)
        write_ready_gnss_denied_mission_plan(bundle)
        flat_elevation = np.zeros((60, 100), dtype=np.float32)
        write_scalar_float_tiff(bundle / "elevation" / "dem.tif", flat_elevation)
        write_scalar_float_tiff(bundle / "elevation" / "dsm.tif", flat_elevation)
        terrain_bundle = load_terrain_bundle(bundle)
        log = root / "terrain_matches.jsonl"
        log.write_text(
            json.dumps(
                {
                    "result": {
                        "status": "accepted",
                        "confidence": 0.8,
                        "inliers": 20,
                        "reprojection_error_px": 1.5,
                        "scale_confidence": 0.7,
                        "covariance": {"x_m2": 4.0, "y_m2": 4.0, "z_m2": None, "yaw_rad2": None},
                    },
                    "external_position_health": {"status": "healthy", "message_type": "odometry"},
                }
            )
            + "\n"
        )
        runtime_status = runtime_status_snapshot(
            bundle=terrain_bundle,
            output_dir=root,
            log_path=log,
            sequence=1,
            record={
                "timestamp_utc": "2026-06-21T00:00:00Z",
                "frame_path": str(root / "frames" / "frame.jpg"),
                "capture_duration_s": 0.1,
                "match_duration_s": 0.2,
                "external_position_health": {"status": "healthy", "message_type": "odometry"},
                "result": {
                    "status": "accepted",
                    "tile_id": "tile_000000",
                    "confidence": 0.8,
                    "scale_confidence": 0.7,
                    "inliers": 20,
                    "covariance": {"x_m2": 4.0, "y_m2": 4.0, "z_m2": None},
                    "estimator": {"initialized": True, "health": "tracking"},
                },
            },
            status_counts={"accepted": 1},
            started_at_utc="2026-06-21T00:00:00+00:00",
        )
        write_runtime_status(root / "runtime_status.json", runtime_status)
        replay_manifest = root / "replay_cases.json"
        replay_manifest.write_text(
            json.dumps(
                {
                    "version": "0.1.0",
                    "cases": [
                        {
                            "case_name": "unit-good",
                            "expected": "good_map",
                            "dataset_type": "synthetic",
                            "conditions": ["good_texture"],
                            "bundle": str(bundle),
                            "log": str(log),
                            "notes": "Synthetic accepted support bundle case.",
                        }
                    ],
                }
            )
        )
        px4_listener = root / "vehicle_visual_odometry.txt"
        px4_listener.write_text(
            """
TOPIC: vehicle_visual_odometry
 vehicle_visual_odometry
    timestamp: 5000000 (0.010000 seconds ago)
    timestamp_sample: 4999000
    position: [0.10000, 0.20000, -1.50000]
    q: [1.00000, 0.00000, 0.00000, 0.00000]
    position_variance: [1.50000, 1.50000, 4.00000]
    reset_counter: 0
    quality: 82
TOPIC: vehicle_visual_odometry
 vehicle_visual_odometry
    timestamp: 5200000 (0.020000 seconds ago)
    timestamp_sample: 5199000
    position: [0.35000, 0.30000, -1.50000]
    q: [1.00000, 0.00000, 0.00000, 0.00000]
    position_variance: [1.50000, 1.50000, 4.00000]
    reset_counter: 0
    quality: 82
""".strip()
        )
        px4_status = root / "mavlink_status.txt"
        px4_status.write_text(
            """
instance #0:
    mavlink chan: #0
    MAVLink version: 2
    transport protocol: UDP (14550)
    accepting commands: YES
""".strip()
        )
        px4_session = root / "px4-sitl-session"
        px4_session_capture = px4_session / "receiver_capture"
        px4_session_capture.mkdir(parents=True)
        shutil.copy2(px4_listener, px4_session_capture / "vehicle_visual_odometry.txt")
        shutil.copy2(px4_status, px4_session_capture / "mavlink_status.txt")
        (px4_session / "px4_sitl_evidence_session.json").write_text(
            json.dumps(
                {
                    "schema_version": "vision_nav_px4_sitl_evidence_session_v1",
                    "version": "0.1.0",
                    "endpoint": "udp:14580",
                    "message_type": "odometry",
                    "rate_hz": 5.0,
                    "repeat_count": 6,
                    "synthetic_log": str(px4_session / "synthetic_external_vision.jsonl"),
                    "capture_instructions": str(px4_session / "receiver_capture" / "README.md"),
                    "expected_captures": {
                        "vehicle_visual_odometry": "receiver_capture/vehicle_visual_odometry.txt",
                        "mavlink_status": "receiver_capture/mavlink_status.txt",
                    },
                    "receiver_report": "receiver_evidence.json",
                    "operator_commands": {
                        "send_synthetic_stream": "VISION_NAV_SITL_SMOKE_DIR=/tmp/px4 ./scripts/dev/px4_sitl_external_vision_smoke.sh",
                        "px4_shell_capture": [
                            "listener vehicle_visual_odometry",
                            "listener vehicle_visual_odometry",
                            "mavlink status",
                        ],
                        "evaluate_session": "./scripts/dev/evaluate_px4_sitl_session.sh /tmp/px4",
                        "automated_capture": "VISION_NAV_SITL_SMOKE_DIR=/tmp/px4 ./scripts/dev/run_px4_sitl_external_vision_capture.sh",
                    },
                    "markers": {
                        "__VISION_NAV_PX4_SITL_SESSION__": str(px4_session),
                        "__VISION_NAV_PX4_SITL_REPORT__": str(px4_session / "receiver_evidence.json"),
                    },
                }
            )
        )
        (px4_session / "px4_sitl_capture_prereqs.json").write_text(
            json.dumps(
                {
                    "schema_version": "vision_nav_px4_sitl_capture_prereqs_v1",
                    "status": "failed",
                    "session_dir": str(px4_session),
                    "px4_dir": "/missing/PX4-Autopilot",
                    "px4_target": "px4_sitl gz_x500",
                    "tmux_session": "vision-nav-px4-sitl",
                    "receiver_report": str(px4_session / "receiver_evidence.json"),
                    "checks": [
                        {
                            "name": "tmux_installed",
                            "status": "passed",
                            "message": "tmux is installed.",
                        },
                        {
                            "name": "px4_autopilot_dir",
                            "status": "failed",
                            "message": "PX4-Autopilot directory not found.",
                        },
                    ],
                    "next_actions": ["Set VISION_NAV_PX4_AUTOPILOT_DIR."],
                    "fix_commands": [
                        {
                            "label": "Point the harness at an existing PX4 checkout",
                            "command": "export VISION_NAV_PX4_AUTOPILOT_DIR=/path/to/PX4-Autopilot",
                            "condition": "px4_autopilot_dir",
                        }
                    ],
                }
            )
        )
        px4_params = root / "px4.params"
        px4_params.write_text(
            """
1 1 EKF2_EV_CTRL 1 6
1 1 EKF2_HGT_REF 0 6
1 1 EKF2_GPS_CTRL 7 6
1 1 EKF2_EV_NOISE_MD 0 6
1 1 EKF2_EV_DELAY 80 9
1 1 EKF2_EV_POS_X 0.0 9
1 1 EKF2_EV_POS_Y 0.0 9
1 1 EKF2_EV_POS_Z 0.0 9
""".strip()
        )
        ardupilot_params = root / "ardupilot.params"
        ardupilot_params.write_text(
            """
EK3_ENABLE,1
EK2_ENABLE,0
AHRS_EKF_TYPE,3
VISO_TYPE,3
VISO_POS_X,0.02
VISO_POS_Y,0.01
VISO_POS_Z,-0.04
EK3_SRC1_POSXY,6
EK3_SRC1_VELXY,0
EK3_SRC1_POSZ,1
EK3_SRC1_VELZ,0
EK3_SRC1_YAW,1
EK3_SRC_OPTIONS,0
GPS_TYPE,0
RC8_OPTION,90
""".strip()
        )
        feature_benchmark_dir = root / "feature-method-bench"
        feature_benchmark_dir.mkdir()
        (feature_benchmark_dir / "unit-method-benchmark.json").write_text(
            json.dumps(
                {
                    "status": "passed",
                    "case_name": "unit-method-benchmark",
                    "expected": "good_map",
                    "recommended_method": "orb",
                    "methods": [
                        {
                            "method": "orb",
                            "status": "passed",
                            "gate": {"metrics": {"accepted_rate": 1.0, "total_records": 2}},
                        },
                        {
                            "method": "neural",
                            "status": "not_available",
                            "reason": "Neural descriptors are not generated yet.",
                        },
                    ],
                }
            )
        )
        field_evidence_report = root / "field_evidence_report.json"
        field_evidence_report.write_text(
            json.dumps(
                {
                    "status": "passed",
                    "manifest_path": "field_manifest.json",
                    "summary": {
                        "coverage_status": "passed",
                        "replay_status": "passed",
                        "case_count": 8,
                        "field_case_count": 8,
                        "capture_metadata_issue_count": 0,
                        "required_conditions": [
                            "good_texture",
                            "low_texture",
                            "blur",
                            "seasonal_change",
                            "lighting_change",
                            "altitude_scale_change",
                            "repeated_patterns",
                            "wrong_map",
                        ],
                        "covered_conditions": [
                            "good_texture",
                            "low_texture",
                            "blur",
                            "seasonal_change",
                            "lighting_change",
                            "altitude_scale_change",
                            "repeated_patterns",
                            "wrong_map",
                        ],
                    },
                    "coverage": {"status": "passed"},
                    "replay_gates": {"status": "passed", "case_count": 8, "reports": []},
                }
            )
        )
        field_collection_plan = root / "field_collection_plan.json"
        field_collection_plan.write_text(
            json.dumps(
                {
                    "schema_version": "vision_nav_field_collection_plan_v1",
                    "status": "passed",
                    "manifest_path": "field_manifest.json",
                    "site_name": "unit-field",
                    "bundle": str(bundle),
                    "source_log": str(log),
                    "capture_root": str(root / "field-captures"),
                    "pending_metadata_update_command_count": 0,
                    "summary": {
                        "required_count": len(REQUIRED_FIELD_CONDITIONS),
                        "registered_count": len(REQUIRED_FIELD_CONDITIONS),
                        "registered_missing_log_count": 0,
                        "placeholder_count": 0,
                        "missing_count": 0,
                    },
                    "conditions": [
                        {
                            "condition": condition,
                            "label": condition.replace("_", " ").title(),
                            "status": "registered",
                            "expected": "good_map",
                            "case_name": f"unit-{condition}",
                            "manifest_log_exists": True,
                            "source_log": str(root / "field-captures" / condition / "terrain_matches.jsonl"),
                            "capture_output_dir": str(root / "field-captures" / condition),
                            "runtime_status_path": str(root / "field-captures" / condition / "runtime_status.json"),
                            "capture_command": f"VISION_NAV_OUTPUT_DIR={root / 'field-captures' / condition} ./scripts/pi/run_terrain_nav_loop.sh",
                            "metadata_update_command": f"VISION_NAV_FIELD_CONDITION={condition} ./scripts/pi/update_field_capture_metadata.sh",
                            "register_command": f"VISION_NAV_FIELD_CONDITION={condition} ./scripts/pi/register_field_replay_case.sh",
                        }
                        for condition in REQUIRED_FIELD_CONDITIONS
                    ],
                }
            )
        )
        field_collection_plan.with_suffix(".md").write_text("# Field Evidence Collection Plan\n")
        field_capture_preflight = root / "field_capture_preflight.json"
        field_capture_preflight.write_text(
            json.dumps(
                {
                    "schema_version": "vision_nav_field_capture_preflight_v1",
                    "status": "failed",
                    "plan_path": str(field_collection_plan),
                    "repo_root": str(Path.cwd()),
                    "condition": "good_texture",
                    "case_name": "unit-good_texture",
                    "expected": "good_map",
                    "bundle_path": str(root / "missing-mission-bundle"),
                    "bundle_validation_command": f"VISION_NAV_BUNDLE={root / 'missing-mission-bundle'} ./scripts/pi/validate_terrain_bundle.sh",
                    "ready_for_capture": False,
                    "ready_for_registration": False,
                    "capture_output_dir": str(root / "field-captures" / "good_texture"),
                    "source_log": str(root / "field-captures" / "good_texture" / "terrain_matches.jsonl"),
                    "runtime_status_path": str(root / "field-captures" / "good_texture" / "runtime_status.json"),
                    "summary": {"passed": 5, "degraded": 1, "failed": 1},
                    "checks": [
                        {
                            "name": "bundle_path",
                            "status": "failed",
                            "message": "Mission bundle is missing.",
                            "details": {
                                "path": str(root / "missing-mission-bundle"),
                                "desktop_action": "Mission Planner > Build Bundle, Upload Bundle",
                                "validation_command": f"VISION_NAV_BUNDLE={root / 'missing-mission-bundle'} ./scripts/pi/validate_terrain_bundle.sh",
                            },
                        },
                        {
                            "name": "capture_metadata",
                            "status": "degraded",
                            "message": "Capture metadata still needs operator-filled field values.",
                            "details": {"issue_count": 2, "issues": ["operator is required", "lighting is required"]},
                        },
                    ],
                    "next_actions": [
                        {
                            "id": "prepare_bundle",
                            "status": "action_required",
                            "title": "Build, upload, or validate the selected terrain bundle.",
                            "desktop_action": "Mission Planner > Build Bundle, Upload Bundle",
                            "command": f"VISION_NAV_BUNDLE={root / 'missing-mission-bundle'} ./scripts/pi/validate_terrain_bundle.sh",
                            "bundle_path": str(root / "missing-mission-bundle"),
                        },
                        {
                            "id": "capture_field_terrain_log",
                            "status": "blocked",
                            "title": "Capture the terrain log and runtime status for this condition.",
                            "desktop_action": "Module Setup > Field Log Capture",
                            "command": "VISION_NAV_COUNT=30 ./scripts/pi/run_terrain_nav_loop.sh && ./scripts/pi/read_runtime_status.sh",
                            "waits_on": ["bundle_path"],
                            "source_log": str(root / "field-captures" / "good_texture" / "terrain_matches.jsonl"),
                            "runtime_status_path": str(root / "field-captures" / "good_texture" / "runtime_status.json"),
                        },
                    ],
                }
            )
        )
        field_capture_log = root / "field-captures" / "good_texture" / "terrain_matches.jsonl"
        field_capture_log.parent.mkdir(parents=True)
        field_capture_log.write_text(
            json.dumps(
                {
                    "result": {
                        "status": "accepted",
                        "confidence": 0.81,
                        "inliers": 21,
                        "reprojection_error_px": 1.4,
                        "scale_confidence": 0.72,
                        "covariance": {"x_m2": 3.0, "y_m2": 3.0, "z_m2": None, "yaw_rad2": None},
                    },
                    "external_position_health": {"status": "healthy", "message_type": "odometry"},
                }
            )
            + "\n"
        )
        field_capture_status = runtime_status_snapshot(
            bundle=terrain_bundle,
            output_dir=field_capture_log.parent,
            log_path=field_capture_log,
            sequence=1,
            record={
                "timestamp_utc": "2026-06-21T00:05:00Z",
                "external_position_health": {"status": "healthy", "message_type": "odometry"},
                "result": {
                    "status": "accepted",
                    "tile_id": "tile_000001",
                    "confidence": 0.81,
                    "scale_confidence": 0.72,
                    "inliers": 21,
                    "covariance": {"x_m2": 3.0, "y_m2": 3.0, "z_m2": None},
                    "estimator": {"initialized": True, "health": "tracking"},
                },
            },
            status_counts={"accepted": 1},
            started_at_utc="2026-06-21T00:05:00+00:00",
        )
        write_runtime_status(field_capture_log.parent / "runtime_status.json", field_capture_status)
        threshold_tuning_report = root / "threshold_tuning_report.json"
        threshold_tuning_report.write_text(
            json.dumps(
                {
                    "status": "passed",
                    "method": "field-replay-gate-threshold-audit",
                    "manifest_path": "field_manifest.json",
                    "conditions": REQUIRED_FIELD_CONDITIONS,
                    "summary": {
                        "coverage_status": "passed",
                        "replay_status": "passed",
                        "case_count": 8,
                        "field_case_count": 8,
                        "capture_metadata_issue_count": 0,
                        "covered_conditions": REQUIRED_FIELD_CONDITIONS,
                        "tuned_conditions": REQUIRED_FIELD_CONDITIONS,
                    },
                    "metrics": {
                        "margins": {
                            "good_map_accepted_rate": 0.25,
                            "degraded_accepted_rate": 0.4,
                            "wrong_map_accepted_rate": 0.0,
                        }
                    },
                }
            )
        )
        rosbag_export_validation = root / "rosbag-jsonl-validation.json"
        rosbag_export_validation.write_text(
            json.dumps(
                {
                    "schema_version": "vision_nav_rosbag_export_validation_v1",
                    "status": "passed",
                    "artifact_path": str(root / "rosbag-jsonl"),
                    "metadata_path": str(root / "rosbag-jsonl" / "metadata.json"),
                    "format": "vision_nav_rosbag_jsonl_v1",
                    "message_count": 4,
                    "topic_count": 3,
                    "topics": [
                        {"name": "/vision_nav/odometry", "type": "nav_msgs/msg/Odometry", "message_count": 1},
                        {"name": "/diagnostics", "type": "diagnostic_msgs/msg/DiagnosticArray", "message_count": 2},
                        {
                            "name": "/vision_nav/camera/image/compressed",
                            "type": "sensor_msgs/msg/CompressedImage",
                            "message_count": 1,
                        },
                    ],
                    "details": {"line_count": 4},
                    "issues": [],
                }
            )
        )
        rosbag2_cli_review = root / "rosbag2-cli-review.json"
        rosbag2_cli_review.write_text(
            json.dumps(
                {
                    "schema_version": "vision_nav_rosbag2_cli_review_v1",
                    "status": "passed",
                    "artifact_path": str(root / "rosbag2-native"),
                    "bag_dir": str(root / "rosbag2-native"),
                    "validation_status": "passed",
                    "validation_format": "vision_nav_rosbag2_v1",
                    "validation_report": {
                        "schema_version": "vision_nav_rosbag_export_validation_v1",
                        "status": "passed",
                        "format": "vision_nav_rosbag2_v1",
                        "message_count": 4,
                        "topic_count": 3,
                        "topics": [
                            {"name": "/vision_nav/odometry", "type": "nav_msgs/msg/Odometry", "message_count": 1},
                            {"name": "/diagnostics", "type": "diagnostic_msgs/msg/DiagnosticArray", "message_count": 2},
                        ],
                    },
                    "ros2_cli": {
                        "status": "passed",
                        "command": ["ros2", "bag", "info", str(root / "rosbag2-native")],
                        "stdout": "Files: rosbag2_0.db3\n",
                        "stderr": "",
                        "exit_code": 0,
                    },
                    "issues": [],
                }
            )
        )
        workflow_logs_dir = root / "workflow-logs"
        workflow_logs_dir.mkdir()
        for step_name in REQUIRED_WORKFLOW_STEPS:
            (workflow_logs_dir / f"{step_name}.log").write_text(f"{step_name}\n")
        workflow_log_archive = root / "autonomy_evidence_workflow.logs.tar.gz"
        with tarfile.open(workflow_log_archive, "w:gz") as archive:
            archive.add(workflow_logs_dir, arcname="logs")
        workflow_readiness_report = root / "autonomy_readiness_report.workflow.json"
        workflow_readiness_report.write_text(
            json.dumps(
                {
                    "schema_version": "vision_nav_autonomy_readiness_v1",
                    "status": "passed",
                    "summary": {"passed": 11, "failed": 0, "degraded": 0},
                }
            )
        )
        workflow_report = root / "autonomy_evidence_workflow.json"
        workflow_report.write_text(
            json.dumps(
                {
                    "schema_version": "vision_nav_autonomy_evidence_workflow_v1",
                    "generated_at": "2026-06-21T12:00:00Z",
                    "status": "passed",
                    "summary": {"passed": len(REQUIRED_WORKFLOW_STEPS), "failed": 0, "skipped": 0},
                    "repo_root": str(root),
                    "workflow_dir": str(root),
                    "workflow_provenance": {
                        "repo_commit": "unit-test",
                        "repo_dirty": False,
                        "script_path": str(root / "scripts/pi/run_autonomy_evidence_workflow.sh"),
                        "script_sha256": "1" * 64,
                        "required_steps": list(REQUIRED_WORKFLOW_STEPS),
                        "required_step_count": len(REQUIRED_WORKFLOW_STEPS),
                    },
                    "steps": [
                        {
                            "name": step_name,
                            "status": "passed",
                            "readiness_report_status": "passed" if step_name == "run_autonomy_readiness_audit" else None,
                            "exit_code": 0,
                            "log_path": str(workflow_logs_dir / f"{step_name}.log"),
                            "markers": {},
                        }
                        for step_name in REQUIRED_WORKFLOW_STEPS
                    ],
                    "markers": {
                        "__VISION_NAV_EVIDENCE_WORKFLOW_LOGS__": str(workflow_log_archive),
                        "__VISION_NAV_SUPPORT_ZIP__": str(root / "support.zip"),
                        "__VISION_NAV_PX4_SITL_PREREQS__": str(root / "px4_sitl_capture_prereqs.json"),
                        "__VISION_NAV_PX4_SITL_REPORT__": str(root / "receiver_evidence.json"),
                        "__VISION_NAV_FIELD_COLLECTION_PLAN__": str(root / "field_collection_plan.json"),
                        "__VISION_NAV_FIELD_COLLECTION_PLAN_MD__": str(root / "field_collection_plan.md"),
                        "__VISION_NAV_FIELD_CAPTURE_PREFLIGHT__": str(root / "field_capture_preflight.json"),
                        "__VISION_NAV_FIELD_EVIDENCE_REPORT__": str(root / "field_evidence_report.json"),
                        "__VISION_NAV_FEATURE_METHOD_REPORT__": str(root / "feature_method_benchmark.json"),
                        "__VISION_NAV_THRESHOLD_REPORT__": str(root / "threshold_tuning_report.json"),
                        "__VISION_NAV_TERRAIN_LOG__": str(root / "terrain_matches.jsonl"),
                        "__VISION_NAV_RUNTIME_STATUS__": str(root / "runtime_status.json"),
                        "__VISION_NAV_ROSBAG_EXPORT_VALIDATION__": str(root / "rosbag-jsonl-validation.json"),
                        "__VISION_NAV_ROSBAG2_CLI_REVIEW__": str(root / "rosbag2-cli-review.json"),
                        "__VISION_NAV_AUTONOMY_REPORT__": str(workflow_readiness_report),
                        "__VISION_NAV_AUTONOMY_HANDOFF__": str(root / "autonomy_readiness_report.md"),
                        "__VISION_NAV_AUTONOMY_EVIDENCE_PACKAGE__": str(root / "autonomy_readiness_report.evidence.zip"),
                    },
                }
            )
        )
        workflow_validation_report = root / "autonomy_evidence_workflow.validation.json"
        workflow_validation_report.write_text(json.dumps(validate_workflow_report(workflow_report)))

        result = create_support_bundle(
            bundle=str(bundle),
            logs=[str(log)],
            repo=".",
            output_dir=root / "support",
            name="unit-support",
            mavlink_endpoint="udp:14550",
            px4_sitl_session_path=str(px4_session),
            px4_params_path=str(px4_params),
            ardupilot_params_path=str(ardupilot_params),
            px4_expected_message="odometry",
            replay_case_manifest_path=str(replay_manifest),
            feature_method_benchmark_paths=[str(feature_benchmark_dir)],
            field_evidence_report_paths=[str(field_evidence_report)],
            field_collection_plan_paths=[str(field_collection_plan)],
            field_capture_preflight_paths=[str(field_capture_preflight)],
            threshold_tuning_report_paths=[str(threshold_tuning_report)],
            rosbag_export_validation_paths=[str(rosbag_export_validation)],
            rosbag2_cli_review_paths=[str(rosbag2_cli_review)],
            evidence_workflow_report_path=str(workflow_report),
            evidence_workflow_validation_path=str(workflow_validation_report),
            evidence_workflow_log_archive_path=str(workflow_log_archive),
            include_map_assets=True,
        )
        assert_equal(result["status"], "passed", "support bundle status")
        zip_path = Path(result["zip_path"])
        if not zip_path.exists():
            raise AssertionError("Expected support bundle zip to exist")
        human_output = io.StringIO()
        with contextlib.redirect_stdout(human_output):
            print_human(result)
        marker = f"__VISION_NAV_SUPPORT_ZIP__={zip_path}"
        if marker not in human_output.getvalue():
            raise AssertionError("Expected support bundle human output to include the stable ZIP marker")
        with zipfile.ZipFile(zip_path) as archive:
            names = set(archive.namelist())
        for expected in {
            "support_manifest.json",
            "bundle/manifest.json",
            "bundle/bundle_health.generated.json",
            "bundle/mission/mission_plan.json",
            "bundle/elevation/dem.tif",
            "bundle/elevation/dsm.tif",
            "logs/terrain_matches.jsonl",
            "logs/terrain_matches.runtime_status.json",
            "logs/good_texture-terrain_matches.jsonl",
            "logs/good_texture-terrain_matches.runtime_status.json",
            "summaries/terrain_matches.summary.json",
            "summaries/good_texture-terrain_matches.summary.json",
            "summaries/replay_gates/unit-good.gate.json",
            "summaries/px4_sitl_evidence/receiver_evidence.json",
            "summaries/px4_sitl_prereqs/px4_sitl_capture_prereqs.json",
            "summaries/px4_params/param_check.json",
            "summaries/ardupilot_params/param_check.json",
            "summaries/feature_method_benchmarks/unit-method-benchmark-01.json",
            "summaries/field_evidence/field_manifest-01.json",
            "summaries/field_collection_plans/field_manifest-01.json",
            "summaries/field_capture_preflights/good_texture-01.json",
            "summaries/threshold_tuning/field_manifest-01.json",
            "summaries/rosbag_export_validations/vision_nav_rosbag_jsonl_v1-01.json",
            "summaries/rosbag2_cli_reviews/rosbag2-native-01.json",
            "summaries/autonomy_evidence_workflow/workflow_validation.computed.json",
            "summaries/autonomy_evidence_workflow/workflow_validation.summary.json",
            "summaries/bench_readiness.json",
            "extras/autonomy_evidence_workflow/autonomy_evidence_workflow.json",
            "extras/autonomy_evidence_workflow/autonomy_evidence_workflow.validation.json",
            "extras/autonomy_evidence_workflow/autonomy_evidence_workflow.logs.tar.gz",
            "extras/field_collection_plans/field_collection_plan.json",
            "extras/field_collection_plans/field_collection_plan.md",
            "extras/px4_sitl_session/px4_sitl_evidence_session.json",
            "extras/px4_sitl_session/px4_sitl_capture_prereqs.json",
            "extras/px4_sitl_prereqs/px4_sitl_capture_prereqs.json",
            "extras/px4_sitl_session/receiver_capture/vehicle_visual_odometry.txt",
            "extras/px4_sitl_session/receiver_capture/mavlink_status.txt",
            "extras/px4_params/px4.params",
            "extras/ardupilot_params/ardupilot.params",
            "extras/feature_method_benchmarks/feature-method-bench/unit-method-benchmark.json",
            "extras/field_evidence/field_evidence_report.json",
            "extras/field_capture_preflights/field_capture_preflight.json",
            "extras/threshold_tuning/threshold_tuning_report.json",
            "extras/rosbag_export_validations/rosbag-jsonl-validation.json",
            "extras/rosbag2_cli_reviews/rosbag2-cli-review.json",
        }:
            if expected not in names:
                raise AssertionError(f"Missing {expected} from support bundle zip")
        manifest = json.loads(Path(result["manifest_path"]).read_text())
        assert_equal(manifest["bundle"]["mission_plan"]["status"], "loaded", "support mission plan loaded")
        assert_equal(manifest["bundle"]["mission_plan"]["gnss_denied"]["status"], "ready", "support gnss denied status")
        assert_equal(manifest["logs"]["summaries"][0]["accepted_rate"], 1.0, "support log accepted rate")
        assert_equal(
            manifest["logs"]["auto_added_field_collection_log_count"],
            1,
            "support auto-added field collection log count",
        )
        assert_equal(
            len(manifest["logs"]["field_collection_plan_logs"]),
            2,
            "support field collection plan discovered log count",
        )
        assert_equal(len(manifest["logs"]["copied"]), 2, "support copied explicit plus field collection logs")
        assert_equal(len(manifest["logs"]["runtime_statuses"]), 2, "support copied runtime statuses for field logs")
        assert_equal(manifest["field_collection_plans"]["status"], "passed", "support field collection plan status")
        assert_equal(manifest["field_collection_plans"]["report_count"], 1, "support field collection plan count")
        assert_equal(
            manifest["field_collection_plans"]["registered_count"],
            len(REQUIRED_FIELD_CONDITIONS),
            "support field collection plan registered count",
        )
        assert_equal(
            manifest["field_collection_plans"]["capture_output_dir_count"],
            len(REQUIRED_FIELD_CONDITIONS),
            "support field collection capture output count",
        )
        assert_equal(
            manifest["field_collection_plans"]["runtime_status_path_count"],
            len(REQUIRED_FIELD_CONDITIONS),
            "support field collection runtime status path count",
        )
        assert_equal(
            manifest["field_collection_plans"]["condition_source_log_count"],
            len(REQUIRED_FIELD_CONDITIONS),
            "support field collection source log count",
        )
        assert_equal(
            manifest["field_collection_plans"]["pending_metadata_update_command_count"],
            0,
            "support field collection pending metadata update command count",
        )
        assert_equal(
            manifest["field_collection_plans"]["reports"][0]["conditions"][0]["has_capture_command"],
            True,
            "support field collection condition capture command flag",
        )
        assert_equal(
            manifest["field_collection_plans"]["reports"][0]["conditions"][0]["has_metadata_update_command"],
            True,
            "support field collection condition metadata update command flag",
        )
        assert_equal(
            manifest["field_collection_plans"]["reports"][0]["conditions"][0]["has_register_command"],
            True,
            "support field collection condition registration command flag",
        )
        field_collection_condition = manifest["field_collection_plans"]["reports"][0]["conditions"][0]
        if "run_terrain_nav_loop.sh" not in field_collection_condition.get("capture_command", ""):
            raise AssertionError("Expected support field collection condition to preserve capture command text")
        if "read_runtime_status.sh" not in field_collection_condition.get("capture_command", ""):
            raise AssertionError("Expected support field collection condition to normalize runtime status capture")
        if "update_field_capture_metadata.sh" not in field_collection_condition.get("metadata_update_command", ""):
            raise AssertionError("Expected support field collection condition to preserve metadata update command text")
        if "register_field_replay_case.sh" not in field_collection_condition.get("register_command", ""):
            raise AssertionError("Expected support field collection condition to preserve registration command text")
        assert_equal(
            manifest["field_capture_preflights"]["status"],
            "failed",
            "support field capture preflight status",
        )
        assert_equal(
            manifest["field_capture_preflights"]["report_count"],
            1,
            "support field capture preflight count",
        )
        assert_equal(
            manifest["field_capture_preflights"]["failed_check_count"],
            1,
            "support field capture preflight failed check count",
        )
        assert_equal(
            manifest["field_capture_preflights"]["degraded_check_count"],
            1,
            "support field capture preflight degraded check count",
        )
        assert_equal(
            manifest["field_capture_preflights"]["blocked_action_count"],
            1,
            "support field capture preflight blocked action count",
        )
        assert_equal(
            manifest["field_capture_preflights"]["reports"][0]["failed_checks"][0]["name"],
            "bundle_path",
            "support field capture preflight failed check",
        )
        assert_equal(
            manifest["field_capture_preflights"]["reports"][0]["next_actions"][0]["id"],
            "prepare_bundle",
            "support field capture preflight next action",
        )
        assert_equal(
            manifest["logs"]["runtime_statuses"][0]["schema_version"],
            "vision_nav_runtime_status_v1",
            "support runtime status schema",
        )
        assert_equal(
            manifest["logs"]["runtime_statuses"][0]["last_match"]["status"],
            "accepted",
            "support runtime status last match",
        )
        assert_equal(
            manifest["logs"]["runtime_statuses"][0]["estimator"]["health"],
            "tracking",
            "support runtime status estimator health",
        )
        assert_equal(manifest["replay_gates"]["status"], "passed", "support replay gate status")
        assert_equal(manifest["replay_gates"]["reports"][0]["status"], "passed", "support replay gate report")
        assert_equal(manifest["px4_sitl_evidence"]["status"], "passed", "support px4 evidence status")
        assert_equal(manifest["px4_sitl_evidence"]["listener"]["sample_count"], 2, "support px4 evidence samples")
        assert_equal(manifest["px4_sitl_evidence"]["listener"]["observed_rate_hz"], 5.0, "support px4 evidence rate")
        assert_equal(manifest["px4_sitl_evidence"]["config"]["expected_rate_hz"], 5.0, "support px4 expected rate")
        assert_equal(
            manifest["px4_sitl_evidence"]["session_summary"]["schema_version"],
            "vision_nav_px4_sitl_evidence_session_v1",
            "support px4 session summary schema",
        )
        assert_equal(
            manifest["px4_sitl_evidence"]["session_summary"]["operator_commands"]["px4_shell_capture"][0],
            "listener vehicle_visual_odometry",
            "support px4 session shell capture command",
        )
        if "run_px4_sitl_external_vision_capture.sh" not in manifest["px4_sitl_evidence"]["session_summary"]["operator_commands"]["automated_capture"]:
            raise AssertionError("support px4 session summary missing automated capture command")
        assert_equal(
            manifest["px4_sitl_evidence"]["session_summary"]["markers"]["__VISION_NAV_PX4_SITL_REPORT__"],
            str(px4_session / "receiver_evidence.json"),
            "support px4 session summary report marker",
        )
        assert_equal(manifest["px4_sitl_prereqs"]["status"], "failed", "support px4 prereq status")
        assert_equal(
            manifest["px4_sitl_prereqs"]["failed_checks"][0]["name"],
            "px4_autopilot_dir",
            "support px4 prereq failed check",
        )
        assert_equal(
            manifest["px4_sitl_prereqs"]["fix_commands"][0]["condition"],
            "px4_autopilot_dir",
            "support px4 prereq fix command",
        )
        direct_px4_report = root / "direct_receiver_evidence.json"
        direct_px4_report.write_text(
            json.dumps(
                {
                    "status": "passed",
                    "expected_message": "odometry",
                    "listener": {"sample_count": 2, "observed_rate_hz": 5.0},
                    "config": {"expected_rate_hz": 5.0},
                    "issues": [],
                }
            )
        )
        direct_px4_result = create_support_bundle(
            repo=".",
            output_dir=root / "support-direct-px4",
            name="unit-support-direct-px4",
            px4_sitl_report_path=str(direct_px4_report),
            px4_expected_message="odometry",
        )
        with zipfile.ZipFile(direct_px4_result["zip_path"]) as archive:
            direct_names = set(archive.namelist())
        for expected in {
            "summaries/px4_sitl_evidence/receiver_evidence.json",
            "extras/px4_sitl_evidence/receiver_evidence.json",
        }:
            if expected not in direct_names:
                raise AssertionError(f"Missing direct PX4 report artifact from support bundle: {expected}")
        direct_px4_manifest = json.loads(Path(direct_px4_result["manifest_path"]).read_text())
        assert_equal(direct_px4_manifest["px4_sitl_evidence"]["status"], "passed", "direct support px4 evidence status")
        assert_equal(
            direct_px4_manifest["px4_sitl_evidence"]["source"],
            "px4_sitl_report",
            "direct support px4 evidence source",
        )
        assert_equal(
            direct_px4_manifest["px4_sitl_evidence"]["listener"]["observed_rate_hz"],
            5.0,
            "direct support px4 evidence observed rate",
        )
        assert_equal(manifest["px4_params"]["status"], "degraded", "support px4 params status")
        assert_equal(manifest["px4_params"]["parameters"]["EKF2_EV_CTRL"], 1, "support px4 ev ctrl")
        assert_equal(manifest["ardupilot_params"]["status"], "passed", "support ardupilot params status")
        assert_equal(manifest["ardupilot_params"]["parameters"]["EK3_SRC1_POSXY"], 6, "support ardupilot posxy")
        assert_equal(manifest["feature_method_benchmarks"]["status"], "passed", "support feature benchmark status")
        assert_equal(manifest["feature_method_benchmarks"]["reports"][0]["recommended_method"], "orb", "support feature benchmark recommendation")
        assert_equal(manifest["field_evidence"]["status"], "passed", "support field evidence status")
        assert_equal(manifest["field_evidence"]["field_case_count"], 8, "support field evidence case count")
        assert_equal(manifest["threshold_tuning"]["status"], "passed", "support threshold tuning status")
        assert_equal(manifest["threshold_tuning"]["field_case_count"], 8, "support threshold tuning field case count")
        assert_equal(manifest["rosbag_export_validations"]["status"], "passed", "support rosbag validation status")
        assert_equal(manifest["rosbag_export_validations"]["report_count"], 1, "support rosbag validation count")
        assert_equal(manifest["rosbag_export_validations"]["message_count"], 4, "support rosbag validation messages")
        assert_equal(manifest["rosbag2_cli_reviews"]["status"], "passed", "support rosbag2 cli review status")
        assert_equal(manifest["rosbag2_cli_reviews"]["report_count"], 1, "support rosbag2 cli review count")
        assert_equal(
            manifest["autonomy_evidence_workflow"]["status"],
            "passed",
            "support evidence workflow status",
        )
        assert_equal(
            manifest["autonomy_evidence_workflow"]["validation_summary"]["workflow_status"],
            "passed",
            "support evidence workflow validation workflow status",
        )
        assert_equal(
            manifest["autonomy_evidence_workflow"]["validation_summary"]["workflow_provenance"]["repo_commit"],
            "unit-test",
            "support evidence workflow provenance commit",
        )
        workflow_checks = {
            check["name"]: check["status"]
            for check in manifest["autonomy_evidence_workflow"]["validation_summary"]["checks"]
        }
        assert_equal(
            workflow_checks["workflow_provenance"],
            "passed",
            "support evidence workflow provenance check",
        )
        assert_equal(manifest["bench_readiness"]["status"], "degraded", "support bench readiness status")
        assert_equal(manifest["bench_readiness"]["summary"]["degraded"], 1, "support bench readiness degraded count")
        auto_logs_result = create_support_bundle(
            repo=".",
            output_dir=root / "support-auto-field-logs",
            name="unit-support-auto-field-logs",
            field_collection_plan_paths=[str(field_collection_plan)],
        )
        auto_logs_manifest = json.loads(Path(auto_logs_result["manifest_path"]).read_text())
        assert_equal(
            len(auto_logs_manifest["logs"]["copied"]),
            2,
            "support auto-ingests field collection source logs without explicit log args",
        )
        assert_equal(
            auto_logs_manifest["logs"]["auto_added_field_collection_log_count"],
            2,
            "support auto-only field collection log count",
        )
        assert_equal(
            len(auto_logs_manifest["logs"]["runtime_statuses"]),
            2,
            "support auto-ingests runtime statuses beside field collection logs",
        )
        readiness = evaluate_bench_readiness_file(zip_path)
        assert_equal(readiness["status"], "degraded", "bench readiness degraded on px4 param warning")
        readiness_checks = {check["name"]: check["status"] for check in readiness["checks"]}
        readiness_check_details = {check["name"]: check.get("details") or {} for check in readiness["checks"]}
        assert_equal(readiness_checks["bundle_health"], "passed", "bench readiness bundle health")
        assert_equal(readiness_checks["gnss_denied_plan"], "passed", "bench readiness gnss denied plan")
        assert_equal(readiness_checks["runtime_status"], "passed", "bench readiness runtime status")
        assert_equal(readiness_checks["px4_sitl_evidence"], "passed", "bench readiness px4 evidence")
        assert_equal(readiness_checks["rosbag_export_validations"], "passed", "bench readiness rosbag validation")
        assert_equal(readiness_checks["rosbag2_cli_reviews"], "passed", "bench readiness rosbag2 cli review")
        assert_equal(
            readiness_check_details["px4_sitl_evidence"]["observed_rate_hz"],
            5.0,
            "bench readiness px4 observed rate",
        )
        assert_equal(
            readiness_check_details["px4_sitl_evidence"]["expected_rate_hz"],
            5.0,
            "bench readiness px4 expected rate",
        )
        assert_equal(
            readiness_check_details["px4_sitl_evidence"]["required_message"],
            "odometry",
            "bench readiness px4 required message",
        )
        assert_equal(readiness_checks["px4_params"], "degraded", "bench readiness px4 params")
        assert_equal(readiness_checks["ardupilot_params"], "passed", "bench readiness ardupilot params")
        assert_equal(readiness_checks["feature_method_benchmarks"], "passed", "bench readiness feature benchmarks")
        assert_equal(readiness_checks["field_evidence"], "passed", "bench readiness field evidence")

        failed_rosbag_validation = dict(manifest)
        failed_rosbag_validation["rosbag_export_validations"] = {
            "status": "failed",
            "report_count": 1,
            "reports": [{"status": "failed", "format": "vision_nav_rosbag_jsonl_v1"}],
        }
        rosbag_failed = evaluate_bench_readiness(failed_rosbag_validation)
        rosbag_failed_checks = {check["name"]: check["status"] for check in rosbag_failed["checks"]}
        assert_equal(rosbag_failed["status"], "failed", "bench readiness failed rosbag validation")
        assert_equal(
            rosbag_failed_checks["rosbag_export_validations"],
            "failed",
            "failed rosbag validation check included",
        )

        incomplete_gnss = json.loads(json.dumps(manifest))
        incomplete_gnss["bundle"]["mission_plan"]["gnss_denied"]["status"] = "incomplete"
        incomplete_gnss["bundle"]["mission_plan"]["gnss_denied"]["checks"][1]["status"] = "failed"
        gnss_failed = evaluate_bench_readiness(incomplete_gnss)
        gnss_failed_checks = {check["name"]: check["status"] for check in gnss_failed["checks"]}
        assert_equal(gnss_failed["status"], "failed", "bench readiness incomplete gnss denied plan")
        assert_equal(gnss_failed_checks["gnss_denied_plan"], "failed", "failed gnss denied check included")

        missing_px4 = dict(manifest)
        missing_px4["px4_sitl_evidence"] = {"status": "not_provided"}
        failed = evaluate_bench_readiness(missing_px4)
        assert_equal(failed["status"], "failed", "bench readiness missing px4 evidence")
        allowed = evaluate_bench_readiness(missing_px4, allow_missing_px4_evidence=True)
        assert_equal(allowed["status"], "degraded", "bench readiness allow missing px4 evidence")

        compatibility_px4 = json.loads(json.dumps(manifest))
        compatibility_px4["px4_sitl_evidence"]["expected_message"] = "vision_position_estimate"
        compatibility_failed = evaluate_bench_readiness(compatibility_px4)
        compatibility_checks = {check["name"]: check["status"] for check in compatibility_failed["checks"]}
        assert_equal(compatibility_failed["status"], "failed", "bench readiness requires odometry px4 evidence")
        assert_equal(
            compatibility_checks["px4_sitl_evidence"],
            "failed",
            "bench readiness rejects compatibility-only px4 evidence",
        )

        missing_runtime_status = json.loads(json.dumps(manifest))
        missing_runtime_status["logs"]["runtime_statuses"] = []
        runtime_degraded = evaluate_bench_readiness(missing_runtime_status)
        runtime_checks = {check["name"]: check["status"] for check in runtime_degraded["checks"]}
        assert_equal(runtime_degraded["status"], "degraded", "bench readiness missing runtime status degrades")
        assert_equal(runtime_checks["runtime_status"], "degraded", "bench readiness runtime status degrade")

        degraded_external = json.loads(json.dumps(manifest))
        degraded_external["logs"]["runtime_statuses"][-1]["external_position"] = {
            "status": "degraded",
            "message_type": "odometry",
            "last_warnings": ["velocity_covariance_missing"],
        }
        external_degraded = evaluate_bench_readiness(degraded_external)
        external_checks = {check["name"]: check["status"] for check in external_degraded["checks"]}
        external_details = {check["name"]: check.get("details") or {} for check in external_degraded["checks"]}
        assert_equal(external_degraded["status"], "degraded", "bench readiness degraded external health")
        assert_equal(external_checks["runtime_status"], "degraded", "bench readiness external runtime status degrade")
        assert_equal(
            external_details["runtime_status"]["external_position_warnings"],
            ["velocity_covariance_missing"],
            "bench readiness external warning details",
        )

        failed_ardupilot = dict(manifest)
        failed_ardupilot["ardupilot_params"] = {
            "status": "failed",
            "parameters": {"source_set": 1, "EK3_SRC1_POSXY": 0},
        }
        ardupilot_failed = evaluate_bench_readiness(failed_ardupilot)
        assert_equal(ardupilot_failed["status"], "failed", "bench readiness failed ardupilot params")
        ardupilot_failed_checks = {check["name"]: check["status"] for check in ardupilot_failed["checks"]}
        assert_equal(ardupilot_failed_checks["ardupilot_params"], "failed", "failed ardupilot check included")

        missing_ardupilot = dict(manifest)
        missing_ardupilot["ardupilot_params"] = {"status": "not_provided"}
        optional_ardupilot = evaluate_bench_readiness(missing_ardupilot)
        optional_check_names = {check["name"] for check in optional_ardupilot["checks"]}
        if "ardupilot_params" in optional_check_names:
            raise AssertionError("ArduPilot params should be optional unless required")
        required_ardupilot = evaluate_bench_readiness(missing_ardupilot, require_ardupilot_params=True)
        assert_equal(required_ardupilot["status"], "failed", "bench readiness required missing ardupilot params")

        failed_feature_benchmarks = dict(manifest)
        failed_feature_benchmarks["feature_method_benchmarks"] = {"status": "failed", "report_count": 1, "reports": []}
        feature_failed = evaluate_bench_readiness(failed_feature_benchmarks)
        assert_equal(feature_failed["status"], "failed", "bench readiness failed feature benchmark")
        missing_feature_benchmarks = dict(manifest)
        missing_feature_benchmarks["feature_method_benchmarks"] = {"status": "not_provided", "report_count": 0}
        feature_optional = evaluate_bench_readiness(missing_feature_benchmarks)
        feature_optional_names = {check["name"] for check in feature_optional["checks"]}
        if "feature_method_benchmarks" in feature_optional_names:
            raise AssertionError("Feature-method benchmarks should be optional unless required")
        feature_required = evaluate_bench_readiness(missing_feature_benchmarks, require_feature_method_benchmark=True)
        assert_equal(feature_required["status"], "failed", "bench readiness required missing feature benchmark")

        failed_field_evidence = dict(manifest)
        failed_field_evidence["field_evidence"] = {"status": "failed", "report_count": 1, "reports": []}
        field_failed = evaluate_bench_readiness(failed_field_evidence)
        assert_equal(field_failed["status"], "failed", "bench readiness failed field evidence")
        missing_field_evidence = dict(manifest)
        missing_field_evidence["field_evidence"] = {"status": "not_provided", "report_count": 0}
        field_optional = evaluate_bench_readiness(missing_field_evidence)
        field_optional_names = {check["name"] for check in field_optional["checks"]}
        if "field_evidence" in field_optional_names:
            raise AssertionError("Field evidence should be optional unless required")
        field_required = evaluate_bench_readiness(missing_field_evidence, require_field_evidence=True)
        assert_equal(field_required["status"], "failed", "bench readiness required missing field evidence")


def test_autonomy_evidence_workflow_validation_checks_log_archive() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        log_dir = root / "logs"
        log_dir.mkdir()
        for step_name in REQUIRED_WORKFLOW_STEPS:
            (log_dir / f"{step_name}.log").write_text(f"{step_name}\n")
        archive_path = root / "autonomy_evidence_workflow.logs.tar.gz"
        with tarfile.open(archive_path, "w:gz") as archive:
            archive.add(log_dir, arcname="logs")
        readiness_report_path = root / "autonomy_readiness_report.json"
        readiness_report_path.write_text(
            json.dumps(
                {
                    "schema_version": "vision_nav_autonomy_readiness_v1",
                    "status": "failed",
                    "summary": {"passed": 2, "failed": 8, "degraded": 0},
                }
            )
        )
        report_path = root / "autonomy_evidence_workflow.json"
        report_path.write_text(
            json.dumps(
                {
                    "schema_version": "vision_nav_autonomy_evidence_workflow_v1",
                    "generated_at": "2026-06-21T12:00:00Z",
                    "status": "failed",
                    "summary": {"passed": 4, "failed": 1, "skipped": 2},
                    "workflow_dir": str(root),
                    "workflow_provenance": {
                        "repo_commit": "unit-test",
                        "repo_dirty": False,
                        "script_path": str(root / "scripts/pi/run_autonomy_evidence_workflow.sh"),
                        "script_sha256": "0" * 64,
                        "required_steps": list(REQUIRED_WORKFLOW_STEPS),
                        "required_step_count": len(REQUIRED_WORKFLOW_STEPS),
                    },
                    "steps": [
                        {
                            "name": step_name,
                            "status": "passed" if step_name != "run_autonomy_readiness_audit" else "failed",
                            "readiness_report_status": (
                                "failed" if step_name == "run_autonomy_readiness_audit" else None
                            ),
                            "exit_code": 0 if step_name != "run_autonomy_readiness_audit" else 1,
                            "log_path": str(log_dir / f"{step_name}.log"),
                            "markers": {},
                        }
                        for step_name in REQUIRED_WORKFLOW_STEPS
                    ],
                    "markers": {
                        "__VISION_NAV_EVIDENCE_WORKFLOW_LOGS__": str(archive_path),
                        "__VISION_NAV_SUPPORT_ZIP__": str(root / "support.zip"),
                        "__VISION_NAV_PX4_SITL_PREREQS__": str(root / "px4_sitl_capture_prereqs.json"),
                        "__VISION_NAV_PX4_SITL_REPORT__": str(root / "receiver_evidence.json"),
                        "__VISION_NAV_FIELD_COLLECTION_PLAN__": str(root / "field_collection_plan.json"),
                        "__VISION_NAV_FIELD_COLLECTION_PLAN_MD__": str(root / "field_collection_plan.md"),
                        "__VISION_NAV_FIELD_CAPTURE_PREFLIGHT__": str(root / "field_capture_preflight.json"),
                        "__VISION_NAV_FIELD_EVIDENCE_REPORT__": str(root / "field_evidence_report.json"),
                        "__VISION_NAV_FEATURE_METHOD_REPORT__": str(root / "feature_method_benchmark.json"),
                        "__VISION_NAV_THRESHOLD_REPORT__": str(root / "threshold_tuning_report.json"),
                        "__VISION_NAV_TERRAIN_LOG__": str(root / "terrain_matches.jsonl"),
                        "__VISION_NAV_RUNTIME_STATUS__": str(root / "runtime_status.json"),
                        "__VISION_NAV_ROSBAG_EXPORT_VALIDATION__": str(root / "rosbag-jsonl-validation.json"),
                        "__VISION_NAV_ROSBAG2_CLI_REVIEW__": str(root / "rosbag2-cli-review.json"),
                        "__VISION_NAV_AUTONOMY_REPORT__": str(root / "autonomy_readiness_report.json"),
                        "__VISION_NAV_AUTONOMY_HANDOFF__": str(root / "autonomy_readiness_report.md"),
                        "__VISION_NAV_AUTONOMY_EVIDENCE_PACKAGE__": str(root / "autonomy_readiness_report.evidence.zip"),
                    },
                }
            )
        )
        validation = validate_workflow_report(report_path)
        assert_equal(validation["status"], "degraded", "workflow validation preserves failed workflow status")
        assert_equal(validation_exit_code(validation), 0, "workflow validation degraded exit code")
        checks = {check["name"]: check["status"] for check in validation["checks"]}
        assert_equal(checks["log_archive"], "passed", "workflow validation log archive")
        assert_equal(checks["workflow_provenance"], "passed", "workflow validation provenance")
        assert_equal(checks["important_markers"], "passed", "workflow validation important markers")
        assert_equal(checks["final_proof_markers"], "passed", "workflow validation final proof markers")
        assert_equal(checks["required_step_results"], "degraded", "workflow validation required step results")
        assert_equal(checks["final_readiness_status"], "passed", "workflow validation final readiness status")
        assert_equal(validation["step_count"], len(REQUIRED_WORKFLOW_STEPS), "workflow validation step count")
        assert_equal(validation["issue_count"], len(validation["issues"]), "workflow validation issue count")
        assert_equal(
            validation["next_required_step"]["name"],
            "run_autonomy_readiness_audit",
            "workflow validation top-level next required step",
        )
        assert_equal(
            validation["next_required_step"]["command"],
            "./scripts/pi/run_autonomy_readiness_audit.sh",
            "workflow validation top-level next required command",
        )
        detailed_checks = {check["name"]: check for check in validation["checks"]}
        assert_equal(
            detailed_checks["required_step_results"]["details"]["non_passed_steps"][0]["name"],
            "run_autonomy_readiness_audit",
            "workflow validation reports non-passed final audit step",
        )
        assert_equal(
            detailed_checks["required_step_results"]["details"]["next_required_step"]["desktop_action"],
            "Module Setup > Autonomy Readiness",
            "workflow validation required-step detail next desktop action",
        )
        if "__VISION_NAV_PX4_SITL_PREREQS__" not in detailed_checks["important_markers"]["details"]["present_markers"]:
            raise AssertionError("workflow validation should list PX4 prereqs as an important diagnostic marker")
        if "__VISION_NAV_PX4_SITL_PREREQS__" in detailed_checks["final_proof_markers"]["details"]["present_markers"]:
            raise AssertionError("PX4 prereq diagnostics should not satisfy final proof markers")
        if "__VISION_NAV_FIELD_CAPTURE_PREFLIGHT__" in detailed_checks["final_proof_markers"]["details"]["present_markers"]:
            raise AssertionError("Field capture preflight diagnostics should not satisfy final proof markers")

        old_format_report = json.loads(report_path.read_text())
        old_format_report.pop("workflow_provenance", None)
        old_format_path = root / "old_format_autonomy_evidence_workflow.json"
        old_format_path.write_text(json.dumps(old_format_report))
        old_format_validation = validate_workflow_report(old_format_path)
        old_format_checks = {check["name"]: check for check in old_format_validation["checks"]}
        assert_equal(
            old_format_checks["workflow_provenance"]["status"],
            "degraded",
            "workflow validation flags old reports without provenance",
        )
        if "rerun the evidence workflow" not in old_format_checks["workflow_provenance"]["message"]:
            raise AssertionError("workflow provenance diagnostic should tell operators to rerun the workflow")

        preflight_blocked_report = json.loads(report_path.read_text())
        missing_bundle_path = root / "missing_mission_bundle"
        capture_output_dir = root / "field-captures" / "good_texture"
        capture_command = (
            f"VISION_NAV_BUNDLE={missing_bundle_path} "
            f"VISION_NAV_OUTPUT_DIR={capture_output_dir} "
            "VISION_NAV_COUNT=30 ./scripts/pi/run_terrain_nav_loop.sh"
        )
        capture_metadata_command = (
            "VISION_NAV_FIELD_MANIFEST=/tmp/field_manifest.json "
            "VISION_NAV_FIELD_CONDITION=good_texture "
            "./scripts/pi/update_field_capture_metadata.sh"
        )
        preflight_blocked_report["markers"]["__VISION_NAV_FIELD_CAPTURE_PREFLIGHT__"] = str(
            root / "field_capture_preflight.json"
        )
        preflight_blocked_report["markers"]["__VISION_NAV_FIELD_CAPTURE_PREFLIGHT_STATUS__"] = "failed"
        preflight_blocked_report["markers"]["__VISION_NAV_FIELD_CAPTURE_READY__"] = "0"
        preflight_blocked_report["markers"]["__VISION_NAV_FIELD_REGISTRATION_READY__"] = "0"
        preflight_blocked_report["markers"]["__VISION_NAV_EXPECTED_TERRAIN_LOG__"] = str(
            capture_output_dir / "terrain_matches.jsonl"
        )
        preflight_blocked_report["markers"]["__VISION_NAV_TERRAIN_BUNDLE__"] = str(missing_bundle_path)
        preflight_blocked_report["markers"]["__VISION_NAV_TERRAIN_BUNDLE_STATUS__"] = "missing"
        preflight_blocked_report["markers"]["__VISION_NAV_TERRAIN_CAPTURE_OUTPUT_DIR__"] = str(capture_output_dir)
        preflight_blocked_report["markers"]["__VISION_NAV_TERRAIN_CAPTURE_COMMAND__"] = capture_command
        preflight_blocked_report["markers"]["__VISION_NAV_FIELD_METADATA_UPDATE_COMMAND__"] = capture_metadata_command
        for step in preflight_blocked_report["steps"]:
            if step.get("name") == "preflight_field_capture":
                step["status"] = "failed"
                step["notes"] = "Field capture preflight found missing bundle."
            elif step.get("name") == "capture_field_terrain_log":
                step["status"] = "skipped"
                step["notes"] = "Missing terrain replay log and bundle."
        preflight_blocked_path = root / "preflight_blocked_autonomy_evidence_workflow.json"
        preflight_blocked_path.write_text(json.dumps(preflight_blocked_report))
        preflight_blocked_validation = validate_workflow_report(preflight_blocked_path)
        assert_equal(
            preflight_blocked_validation["next_required_step"]["name"],
            "preflight_field_capture",
            "workflow validation should surface preflight before capture when preflight fails",
        )
        assert_equal(
            preflight_blocked_validation["next_required_step"]["desktop_action"],
            "Mission Planner > Build Bundle, Upload Bundle, then Module Setup > Field Capture Preflight",
            "workflow validation missing-bundle preflight guidance routes through Mission Planner",
        )
        assert_equal(
            preflight_blocked_validation["next_required_step"]["command"],
            f"VISION_NAV_BUNDLE={shlex.quote(str(missing_bundle_path))} ./scripts/pi/validate_terrain_bundle.sh",
            "workflow validation missing-bundle preflight guidance validates selected bundle path",
        )
        assert_equal(
            preflight_blocked_validation["next_required_step"]["capture_command_after_preflight"],
            f"{capture_command} && ./scripts/pi/read_runtime_status.sh",
            "workflow validation preserves capture command after preflight",
        )
        assert_equal(
            preflight_blocked_validation["next_required_step"]["preflight_status"],
            "failed",
            "workflow validation preserves preflight status",
        )
        assert_equal(
            preflight_blocked_validation["next_required_step"]["ready_for_capture"],
            False,
            "workflow validation preserves preflight capture readiness",
        )
        preflight_blocked_output = io.StringIO()
        with contextlib.redirect_stdout(preflight_blocked_output):
            print_workflow_validation_human(preflight_blocked_validation)
        preflight_blocked_text = preflight_blocked_output.getvalue()
        if "Preflight report: " not in preflight_blocked_text:
            raise AssertionError("workflow validation human output should include preflight report")
        if "After preflight: " not in preflight_blocked_text:
            raise AssertionError("workflow validation human output should include post-preflight capture command")

        capture_blocked_report = json.loads(report_path.read_text())
        capture_blocked_report["markers"].pop("__VISION_NAV_TERRAIN_LOG__", None)
        capture_blocked_report["markers"].pop("__VISION_NAV_RUNTIME_STATUS__", None)
        capture_blocked_report["markers"]["__VISION_NAV_FIELD_SELECTED_CONDITION__"] = "good_texture"
        capture_blocked_report["markers"]["__VISION_NAV_FIELD_SELECTED_CASE__"] = "dronecompute-test-area-good_texture"
        capture_blocked_report["markers"]["__VISION_NAV_EXPECTED_TERRAIN_LOG__"] = str(capture_output_dir / "terrain_matches.jsonl")
        capture_blocked_report["markers"]["__VISION_NAV_TERRAIN_BUNDLE__"] = str(missing_bundle_path)
        capture_blocked_report["markers"]["__VISION_NAV_TERRAIN_BUNDLE_STATUS__"] = "missing"
        capture_blocked_report["markers"]["__VISION_NAV_TERRAIN_CAPTURE_OUTPUT_DIR__"] = str(capture_output_dir)
        capture_blocked_report["markers"]["__VISION_NAV_TERRAIN_CAPTURE_COMMAND__"] = capture_command
        capture_blocked_report["markers"]["__VISION_NAV_FIELD_METADATA_UPDATE_COMMAND__"] = capture_metadata_command
        for step in capture_blocked_report["steps"]:
            if step.get("name") == "select_field_collection_condition":
                step["status"] = "degraded"
                step["notes"] = "Loaded next field collection condition good_texture; capture metadata still needs completion before registration."
            elif step.get("name") == "capture_field_terrain_log":
                step["status"] = "skipped"
                step["notes"] = "Missing terrain replay log and bundle."
            elif step.get("name") == "register_field_replay_case":
                step["status"] = "skipped"
                step["notes"] = "Terrain log was not validated in this workflow run."
        capture_blocked_path = root / "capture_blocked_autonomy_evidence_workflow.json"
        capture_blocked_path.write_text(json.dumps(capture_blocked_report))
        capture_blocked_validation = validate_workflow_report(capture_blocked_path)
        assert_equal(
            capture_blocked_validation["next_required_step"]["name"],
            "capture_field_terrain_log",
            "workflow validation should surface capture after a condition is already selected",
        )
        assert_equal(
            capture_blocked_validation["next_required_step"]["desktop_action"],
            "Mission Planner > Build Bundle, Upload Bundle, then Module Setup > Field Log Capture",
            "workflow validation missing-bundle capture guidance routes through Mission Planner",
        )
        assert_equal(
            capture_blocked_validation["next_required_step"]["command"],
            f"VISION_NAV_BUNDLE={shlex.quote(str(missing_bundle_path))} ./scripts/pi/validate_terrain_bundle.sh",
            "workflow validation missing-bundle capture guidance validates selected bundle path",
        )
        assert_equal(
            capture_blocked_validation["next_required_step"]["capture_command_after_bundle"],
            f"{capture_command} && ./scripts/pi/read_runtime_status.sh",
            "workflow validation preserves capture command after missing-bundle fix",
        )
        assert_equal(
            capture_blocked_validation["next_required_step"]["runtime_status_path"],
            str(capture_output_dir / "runtime_status.json"),
            "workflow validation surfaces expected runtime status path",
        )
        assert_equal(
            capture_blocked_validation["next_required_step"]["metadata_update_command"],
            capture_metadata_command,
            "workflow validation preserves metadata update command alongside capture guidance",
        )
        capture_blocked_checks = {check["name"]: check for check in capture_blocked_validation["checks"]}
        assert_equal(
            capture_blocked_checks["required_step_results"]["details"]["next_required_step"]["name"],
            "capture_field_terrain_log",
            "workflow validation required-step detail should surface capture after selected condition",
        )
        capture_blocked_output = io.StringIO()
        with contextlib.redirect_stdout(capture_blocked_output):
            print_workflow_validation_human(capture_blocked_validation)
        capture_blocked_text = capture_blocked_output.getvalue()
        if "After bundle: " not in capture_blocked_text:
            raise AssertionError("workflow validation human output should include post-bundle capture command")
        if "Bundle: " not in capture_blocked_text:
            raise AssertionError("workflow validation human output should include selected bundle path")
        if "Expected log: " not in capture_blocked_text:
            raise AssertionError("workflow validation human output should include expected terrain log")
        if "Metadata update: " not in capture_blocked_text:
            raise AssertionError("workflow validation human output should include metadata update command")

        metadata_blocked_report = json.loads(report_path.read_text())
        metadata_command = (
            "VISION_NAV_FIELD_MANIFEST=/tmp/field_manifest.json "
            "VISION_NAV_FIELD_CONDITION=good_texture "
            "./scripts/pi/update_field_capture_metadata.sh"
        )
        metadata_blocked_report["markers"]["__VISION_NAV_FIELD_METADATA_UPDATE_COMMAND__"] = metadata_command
        for step in metadata_blocked_report["steps"]:
            if step.get("name") == "register_field_replay_case":
                step["status"] = "skipped"
                step["notes"] = "Loaded field condition has incomplete capture metadata."
        metadata_blocked_path = root / "metadata_blocked_autonomy_evidence_workflow.json"
        metadata_blocked_path.write_text(json.dumps(metadata_blocked_report))
        metadata_blocked_validation = validate_workflow_report(metadata_blocked_path)
        assert_equal(
            metadata_blocked_validation["next_required_step"]["name"],
            "register_field_replay_case",
            "workflow validation metadata-blocked next step name",
        )
        assert_equal(
            metadata_blocked_validation["next_required_step"]["command"],
            metadata_command,
            "workflow validation metadata-blocked next command",
        )
        assert_equal(
            metadata_blocked_validation["next_required_step"]["desktop_action"],
            "Module Setup > Field Evidence Case > Update Metadata",
            "workflow validation metadata-blocked desktop action",
        )
        metadata_blocked_checks = {check["name"]: check for check in metadata_blocked_validation["checks"]}
        assert_equal(
            metadata_blocked_checks["required_step_results"]["details"]["next_required_step"]["metadata_update_command"],
            metadata_command,
            "workflow validation required-step metadata command detail",
        )

        missing_required_step_report = json.loads(report_path.read_text())
        missing_required_step_report["steps"] = [
            step for step in missing_required_step_report["steps"] if step.get("name") != "capture_field_terrain_log"
        ]
        missing_required_step_path = root / "missing_required_step_autonomy_evidence_workflow.json"
        missing_required_step_path.write_text(json.dumps(missing_required_step_report))
        missing_required_step_validation = validate_workflow_report(missing_required_step_path)
        missing_required_step_checks = {check["name"]: check for check in missing_required_step_validation["checks"]}
        assert_equal(
            missing_required_step_validation["status"],
            "failed",
            "workflow validation fails missing required step result",
        )
        assert_equal(
            missing_required_step_checks["required_step_results"]["status"],
            "failed",
            "workflow validation missing required step result check",
        )
        assert_equal(
            missing_required_step_checks["required_step_results"]["details"]["missing_steps"],
            ["capture_field_terrain_log"],
            "workflow validation missing required step result detail",
        )
        assert_equal(
            missing_required_step_validation["next_required_step"]["name"],
            "capture_field_terrain_log",
            "workflow validation missing step becomes next required step",
        )
        assert_equal(
            missing_required_step_validation["next_required_step"]["status"],
            "missing",
            "workflow validation missing next required step status",
        )
        assert_equal(
            missing_required_step_validation["next_required_step"]["command"],
            "VISION_NAV_COUNT=30 ./scripts/pi/run_terrain_nav_loop.sh && ./scripts/pi/read_runtime_status.sh",
            "workflow validation missing capture guidance should include runtime status read",
        )
        missing_step_output = io.StringIO()
        with contextlib.redirect_stdout(missing_step_output):
            print_workflow_validation_human(missing_required_step_validation)
        missing_step_text = missing_step_output.getvalue()
        if "Details:" not in missing_step_text:
            raise AssertionError("workflow validation human output should include detailed diagnostics")
        if "Missing workflow steps: capture_field_terrain_log" not in missing_step_text:
            raise AssertionError("workflow validation human output should list missing required steps")
        if "Non-passing workflow step: run_autonomy_readiness_audit [failed]" not in missing_step_text:
            raise AssertionError("workflow validation human output should list non-passing steps")

        mismatched_readiness_report = json.loads(report_path.read_text())
        for step in mismatched_readiness_report["steps"]:
            if step.get("name") == "run_autonomy_readiness_audit":
                step["status"] = "passed"
                step.pop("readiness_report_status", None)
        mismatched_readiness_report["status"] = "passed"
        mismatched_readiness_path = root / "mismatched_readiness_autonomy_evidence_workflow.json"
        mismatched_readiness_path.write_text(json.dumps(mismatched_readiness_report))
        mismatched_readiness_validation = validate_workflow_report(mismatched_readiness_path)
        mismatched_readiness_checks = {check["name"]: check for check in mismatched_readiness_validation["checks"]}
        assert_equal(
            mismatched_readiness_validation["status"],
            "failed",
            "workflow validation fails mismatched final readiness status",
        )
        assert_equal(
            mismatched_readiness_checks["final_readiness_status"]["status"],
            "failed",
            "workflow validation final readiness mismatch check",
        )

        session_only_report = json.loads(report_path.read_text())
        session_only_report["markers"].pop("__VISION_NAV_PX4_SITL_REPORT__")
        session_only_report["markers"]["__VISION_NAV_PX4_SITL_SESSION__"] = str(root / "px4-sitl-evidence")
        session_only_path = root / "session_only_px4_autonomy_evidence_workflow.json"
        session_only_path.write_text(json.dumps(session_only_report))
        session_only_validation = validate_workflow_report(session_only_path)
        session_only_checks = {check["name"]: check for check in session_only_validation["checks"]}
        assert_equal(
            session_only_checks["important_markers"]["status"],
            "passed",
            "workflow validation px4 session satisfies important marker",
        )
        assert_equal(
            session_only_checks["final_proof_markers"]["status"],
            "degraded",
            "workflow validation px4 session alone does not satisfy final proof marker",
        )
        if "__VISION_NAV_PX4_SITL_SESSION__" in session_only_checks["final_proof_markers"]["details"]["present_markers"]:
            raise AssertionError("PX4 session scaffolds should not satisfy final proof markers")
        assert_equal(
            session_only_checks["final_proof_markers"]["details"]["missing_markers"],
            ["__VISION_NAV_PX4_SITL_REPORT__"],
            "workflow validation session-only missing px4 report marker",
        )

        missing_px4_report = json.loads(report_path.read_text())
        missing_px4_report["markers"].pop("__VISION_NAV_PX4_SITL_REPORT__")
        missing_px4_path = root / "missing_px4_autonomy_evidence_workflow.json"
        missing_px4_path.write_text(json.dumps(missing_px4_report))
        missing_px4_validation = validate_workflow_report(missing_px4_path)
        missing_px4_checks = {check["name"]: check for check in missing_px4_validation["checks"]}
        assert_equal(
            missing_px4_checks["final_proof_markers"]["status"],
            "degraded",
            "workflow validation missing all px4 proof markers degraded",
        )
        assert_equal(
            missing_px4_checks["final_proof_markers"]["details"]["missing_markers"],
            ["__VISION_NAV_PX4_SITL_REPORT__"],
            "workflow validation missing px4 proof marker",
        )
        if "__VISION_NAV_PX4_SITL_PREREQS__" in missing_px4_checks["final_proof_markers"]["details"]["present_markers"]:
            raise AssertionError("PX4 prereq diagnostics should not satisfy missing PX4 receiver proof")

        incomplete_marker_report = json.loads(report_path.read_text())
        incomplete_marker_report["markers"].pop("__VISION_NAV_THRESHOLD_REPORT__")
        incomplete_marker_report["markers"].pop("__VISION_NAV_ROSBAG2_CLI_REVIEW__")
        incomplete_marker_path = root / "incomplete_marker_autonomy_evidence_workflow.json"
        incomplete_marker_path.write_text(json.dumps(incomplete_marker_report))
        incomplete_marker_validation = validate_workflow_report(incomplete_marker_path)
        incomplete_marker_checks = {check["name"]: check for check in incomplete_marker_validation["checks"]}
        assert_equal(
            incomplete_marker_checks["final_proof_markers"]["status"],
            "degraded",
            "workflow validation missing final proof markers degraded",
        )
        assert_equal(
            incomplete_marker_checks["final_proof_markers"]["details"]["missing_markers"],
            ["__VISION_NAV_THRESHOLD_REPORT__", "__VISION_NAV_ROSBAG2_CLI_REVIEW__"],
            "workflow validation missing final proof marker list",
        )
        incomplete_marker_output = io.StringIO()
        with contextlib.redirect_stdout(incomplete_marker_output):
            print_workflow_validation_human(incomplete_marker_validation)
        if (
            "Missing final proof markers: __VISION_NAV_THRESHOLD_REPORT__, __VISION_NAV_ROSBAG2_CLI_REVIEW__"
            not in incomplete_marker_output.getvalue()
        ):
            raise AssertionError("workflow validation human output should list missing final proof markers")

        broken_archive_path = root / "broken.logs.tar.gz"
        with tarfile.open(broken_archive_path, "w:gz") as archive:
            archive.add(log_dir / "create_field_evidence_template.log", arcname="logs/create_field_evidence_template.log")
        broken_report = json.loads(report_path.read_text())
        broken_report["markers"]["__VISION_NAV_EVIDENCE_WORKFLOW_LOGS__"] = str(broken_archive_path)
        broken_report_path = root / "broken_autonomy_evidence_workflow.json"
        broken_report_path.write_text(json.dumps(broken_report))
        broken_validation = validate_workflow_report(broken_report_path)
        assert_equal(broken_validation["status"], "failed", "workflow validation broken archive status")
        assert_equal(validation_exit_code(broken_validation), 1, "workflow validation failed exit code")
        broken_checks = {check["name"]: check["status"] for check in broken_validation["checks"]}
        assert_equal(broken_checks["log_archive"], "failed", "workflow validation broken archive check")


def test_autonomy_readiness_requires_external_proof_artifacts() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        research_doc = root / "autonomy-ground-control-research.md"
        research_doc.write_text(
            "\n".join(
                [
                    "# Autonomy And Ground Control Research",
                    "## Fit Criteria",
                    "- Runs offline after map preparation.",
                    "## Highest-Value References",
                    "| Reference | What it proves | What to implement here |",
                    "| --- | --- | --- |",
                    "| PX4 External Vision | PX4 consumes external vision. | Keep ODOMETRY as the preferred path. |",
                    "## Recommended Product Architecture Changes",
                    "### 1. External-Position Interface",
                    "### 2. ROS 2 Companion Runtime",
                    "## Near-Term Repo Integration Plan",
                    "1. Add external-position conversion and proof gates.",
                    "## Implementation Choices To Avoid",
                    "- Do not send unchecked external-position estimates.",
                ]
            )
        )
        implementation_plan = root / "autonomy-ground-control-implementation-plan.md"
        implementation_plan.write_text(
            "\n".join(
                [
                    "# Autonomy And Ground Control Implementation Plan",
                    "### Track 1: External Position Output",
                    "### Track 2: ROS 2 Companion Runtime",
                    "### Track 3: Terrain Map Bundle Pipeline",
                    "### Track 4: Desktop Setup And Mission UX",
                    "### Track 5: Validation And Product Risk Controls",
                    "### Track 6: ArduPilot Adapter Path",
                    "Tasks:",
                    "1. Collect proof artifacts for the active implementation tracks.",
                    "Next tasks:",
                    "1. Re-run the final readiness audit after proof artifacts exist.",
                    "Acceptance checks:",
                    "- ArduPilot support never becomes the default output path.",
                    "- Runtime adapter work remains gated behind PX4 proof.",
                    "## Execution Order",
                    "1. Finish PX4 bench evidence before optional adapters.",
                ]
            )
        )
        support_manifest = root / "support_manifest.json"
        support_manifest.write_text(
            json.dumps(
                {
                    "name": "unit-support",
                    "metadata": {"generated_at": "2026-06-21T00:00:00Z"},
                    "bundle": {
                        "bundle_id": "unit-bundle",
                        "health": {"status": "passed"},
                        "mission_plan": {
                            "status": "loaded",
                            "path": "mission/mission_plan.json",
                            "mission_item_count": 3,
                            "gnss_denied": {
                                "status": "ready",
                                "satellite_source_disabled": True,
                                "map_position_reset_set": True,
                                "home_position_set": True,
                                "heading_set": True,
                                "estimator_health": "ready",
                                "checks": [
                                    {"name": "satellite_source_disabled", "status": "passed"},
                                    {"name": "map_position_reset", "status": "passed"},
                                    {"name": "home_position", "status": "passed"},
                                    {"name": "heading", "status": "passed"},
                                    {"name": "estimator_health", "status": "passed"},
                                ],
                            },
                        },
                    },
                    "logs": {
                        "copied": ["logs/terrain_matches.jsonl"],
                        "missing": [],
                        "summaries": [{"accepted_rate": 1.0}],
                        "runtime_statuses": [
                            {
                                "schema_version": "vision_nav_runtime_status_v1",
                                "active_map": {"bundle_id": "unit-bundle"},
                                "output_path": str(root),
                                "log_path": str(root / "terrain_matches.jsonl"),
                                "last_match": {"status": "accepted", "confidence": 0.85},
                                "estimator": {"health": "tracking"},
                                "external_position_health": {"status": "healthy", "message_type": "odometry"},
                                "status_counts": {"accepted": 2, "rejected": 0},
                            }
                        ],
                    },
                    "replay_gates": {"status": "passed", "case_count": 8},
                    "px4_sitl_evidence": {
                        "status": "passed",
                        "expected_message": "odometry",
                        "listener": {"sample_count": 5},
                    },
                    "px4_params": {
                        "status": "passed",
                        "parameters": {"EKF2_EV_CTRL": 1, "EKF2_HGT_REF": 0, "EKF2_GPS_CTRL": 7},
                    },
                    "feature_method_benchmarks": {
                        "status": "passed",
                        "report_count": 1,
                        "reports": [{"recommended_method": "orb"}],
                    },
                    "field_evidence": {
                        "status": "passed",
                        "report_count": 1,
                        "field_case_count": 8,
                        "capture_metadata_issue_count": 0,
                        "covered_conditions": REQUIRED_FIELD_CONDITIONS,
                    },
                    "rosbag_export_validations": {
                        "status": "passed",
                        "report_count": 1,
                        "formats": ["vision_nav_rosbag_jsonl_v1"],
                        "message_count": 4,
                        "topic_count": 3,
                        "reports": [
                            {
                                "status": "passed",
                                "format": "vision_nav_rosbag_jsonl_v1",
                                "message_count": 4,
                                "topic_count": 3,
                                "topics": [
                                    {"name": "/vision_nav/odometry", "message_count": 1},
                                    {"name": "/diagnostics", "message_count": 2},
                                    {"name": "/vision_nav/camera/image/compressed", "message_count": 1},
                                ],
                            }
                        ],
                    },
                    "rosbag2_cli_reviews": {
                        "status": "passed",
                        "report_count": 1,
                        "reports": [
                            {
                                "status": "passed",
                                "artifact_path": str(root / "rosbag2-native"),
                                "bag_dir": str(root / "rosbag2-native"),
                                "validation_status": "passed",
                                "validation_format": "vision_nav_rosbag2_v1",
                                "ros2_cli_status": "passed",
                                "ros2_cli_exit_code": 0,
                            }
                        ],
                    },
                }
            )
        )
        field_report = root / "field_evidence.json"
        field_report.write_text(
            json.dumps(
                {
                    "status": "passed",
                    "summary": {
                        "coverage_status": "passed",
                        "replay_status": "passed",
                        "case_count": 8,
                        "field_case_count": 8,
                        "capture_metadata_issue_count": 0,
                        "covered_conditions": REQUIRED_FIELD_CONDITIONS,
                    },
                }
            )
        )
        field_collection_plan = root / "field_collection_plan.json"
        field_collection_plan.write_text(
            json.dumps(
                {
                    "schema_version": "vision_nav_field_collection_plan_v1",
                    "status": "passed",
                    "manifest_path": str(root / "field_manifest.json"),
                    "site_name": "unit-field",
                    "capture_root": str(root / "field-captures"),
                    "pending_capture_command_count": 0,
                    "pending_metadata_update_command_count": 0,
                    "pending_registration_command_count": 0,
                    "capture_output_dir_count": len(REQUIRED_FIELD_CONDITIONS),
                    "runtime_status_path_count": len(REQUIRED_FIELD_CONDITIONS),
                    "condition_source_log_count": len(REQUIRED_FIELD_CONDITIONS),
                    "summary": {
                        "required_count": len(REQUIRED_FIELD_CONDITIONS),
                        "registered_count": len(REQUIRED_FIELD_CONDITIONS),
                        "registered_missing_log_count": 0,
                        "placeholder_count": 0,
                        "missing_count": 0,
                    },
                    "conditions": [
                        {
                            "condition": condition,
                            "status": "registered",
                            "expected": "wrong_map" if condition == "wrong_map" else "good_map",
                            "case_name": f"unit-{condition}",
                            "source_log": str(root / "field-captures" / condition / "terrain_matches.jsonl"),
                            "capture_output_dir": str(root / "field-captures" / condition),
                            "runtime_status_path": str(root / "field-captures" / condition / "runtime_status.json"),
                            "capture_command": f"./scripts/pi/run_terrain_nav_loop.sh --condition {condition}",
                            "metadata_update_command": f"./scripts/pi/update_field_capture_metadata.sh --condition {condition}",
                            "register_command": f"./scripts/pi/register_field_replay_case.sh --condition {condition}",
                        }
                        for condition in REQUIRED_FIELD_CONDITIONS
                    ],
                }
            )
        )
        field_collection_plan.with_suffix(".md").write_text("# Field Evidence Collection Plan\n")
        feature_report = root / "feature_method_benchmark.json"
        feature_report.write_text(
            json.dumps(
                {
                    "status": "passed",
                    "case_name": "unit-method-benchmark",
                    "expected": "good_map",
                    "recommended_method": "orb",
                    "methods": [
                        {"method": "orb", "status": "passed", "record_count": 2},
                        {"method": "akaze", "status": "failed", "record_count": 2},
                    ],
                }
            )
        )
        px4_receiver_report = root / "receiver_evidence.json"
        px4_receiver_report.write_text(
            json.dumps(
                {
                    "status": "passed",
                    "expected_message": "odometry",
                    "session_dir": str(root / "px4-sitl-evidence"),
                    "report_path": str(root / "receiver_evidence.json"),
                    "listener": {
                        "sample_count": 5,
                        "observed_rate_hz": 5.0,
                        "latest_sample_age_s": 0.25,
                        "last_position": [1.0, 2.0, -3.0],
                    },
                    "config": {"expected_rate_hz": 5.0, "min_rate_ratio": 0.5},
                    "issues": [],
                }
            )
        )
        px4_prereq_report = root / "px4_sitl_capture_prereqs.json"
        px4_prereq_report.write_text(
            json.dumps(
                {
                    "schema_version": "vision_nav_px4_sitl_capture_prereqs_v1",
                    "status": "failed",
                    "session_dir": str(root / "px4-sitl-evidence"),
                    "capture_dir": str(root / "px4-sitl-evidence" / "receiver_capture"),
                    "px4_dir": str(root / "PX4-Autopilot"),
                    "px4_target": "px4_sitl gz_x500",
                    "tmux_session": "vision-nav-px4-sitl",
                    "receiver_report": str(px4_receiver_report),
                    "checks": [
                        {"name": "tmux_installed", "status": "passed", "message": "tmux is installed."},
                        {
                            "name": "px4_autopilot_dir",
                            "status": "failed",
                            "message": "PX4-Autopilot directory not found.",
                        },
                    ],
                    "next_actions": ["PX4-Autopilot directory not found."],
                    "fix_commands": [
                        {
                            "label": "Point the harness at an existing PX4 checkout",
                            "command": "export VISION_NAV_PX4_AUTOPILOT_DIR=/path/to/PX4-Autopilot",
                            "condition": "px4_autopilot_dir",
                        }
                    ],
                }
            )
        )
        field_capture_preflight = root / "field_capture_preflight.json"
        field_capture_preflight.write_text(
            json.dumps(
                {
                    "schema_version": "vision_nav_field_capture_preflight_v1",
                    "status": "failed",
                    "plan_path": str(field_collection_plan),
                    "repo_root": str(Path.cwd()),
                    "condition": "good_texture",
                    "case_name": "unit-good_texture",
                    "expected": "good_map",
                    "bundle_path": str(root / "missing-mission-bundle"),
                    "bundle_validation_command": f"VISION_NAV_BUNDLE={root / 'missing-mission-bundle'} ./scripts/pi/validate_terrain_bundle.sh",
                    "ready_for_capture": False,
                    "ready_for_registration": False,
                    "capture_output_dir": str(root / "field-captures" / "good_texture"),
                    "source_log": str(root / "field-captures" / "good_texture" / "terrain_matches.jsonl"),
                    "runtime_status_path": str(root / "field-captures" / "good_texture" / "runtime_status.json"),
                    "summary": {"passed": 5, "degraded": 0, "failed": 1},
                    "checks": [
                        {
                            "name": "bundle_path",
                            "status": "failed",
                            "message": "Mission bundle is missing.",
                            "details": {
                                "path": str(root / "missing-mission-bundle"),
                                "desktop_action": "Mission Planner > Build Bundle, Upload Bundle",
                                "validation_command": f"VISION_NAV_BUNDLE={root / 'missing-mission-bundle'} ./scripts/pi/validate_terrain_bundle.sh",
                            },
                        }
                    ],
                    "next_actions": [
                        {
                            "id": "prepare_bundle",
                            "status": "action_required",
                            "title": "Build, upload, or validate the selected terrain bundle.",
                            "desktop_action": "Mission Planner > Build Bundle, Upload Bundle",
                            "command": f"VISION_NAV_BUNDLE={root / 'missing-mission-bundle'} ./scripts/pi/validate_terrain_bundle.sh",
                            "bundle_path": str(root / "missing-mission-bundle"),
                        },
                        {
                            "id": "capture_field_terrain_log",
                            "status": "blocked",
                            "title": "Capture the terrain log and runtime status for this condition.",
                            "desktop_action": "Module Setup > Field Log Capture",
                            "command": "VISION_NAV_COUNT=30 ./scripts/pi/run_terrain_nav_loop.sh && ./scripts/pi/read_runtime_status.sh",
                            "waits_on": ["bundle_path"],
                            "source_log": str(root / "field-captures" / "good_texture" / "terrain_matches.jsonl"),
                            "runtime_status_path": str(root / "field-captures" / "good_texture" / "runtime_status.json"),
                        },
                    ],
                }
            )
        )
        threshold_report = root / "threshold_tuning.json"
        threshold_report.write_text(
            json.dumps(
                {
                    "status": "passed",
                    "method": "unit-field-grid-search",
                    "conditions": REQUIRED_FIELD_CONDITIONS,
                    "summary": {
                        "field_case_count": 8,
                        "capture_metadata_issue_count": 0,
                        "covered_conditions": REQUIRED_FIELD_CONDITIONS,
                    },
                }
            )
        )
        rosbag_validation_report = root / "rosbag-jsonl-validation.json"
        rosbag_validation_report.write_text(
            json.dumps(
                {
                    "schema_version": "vision_nav_rosbag_export_validation_v1",
                    "status": "passed",
                    "artifact_path": str(root / "rosbag-jsonl"),
                    "metadata_path": str(root / "rosbag-jsonl" / "metadata.json"),
                    "format": "vision_nav_rosbag_jsonl_v1",
                    "message_count": 4,
                    "topic_count": 3,
                    "topics": [
                        {
                            "name": "/vision_nav/odometry",
                            "type": "nav_msgs/msg/Odometry",
                            "message_count": 1,
                        },
                        {
                            "name": "/diagnostics",
                            "type": "diagnostic_msgs/msg/DiagnosticArray",
                            "message_count": 2,
                        },
                        {
                            "name": "/vision_nav/camera/image/compressed",
                            "type": "sensor_msgs/msg/CompressedImage",
                            "message_count": 1,
                        },
                    ],
                    "issues": [],
                }
            )
        )
        rosbag2_cli_review = root / "rosbag2-cli-review.json"
        rosbag2_cli_review.write_text(
            json.dumps(
                {
                    "schema_version": "vision_nav_rosbag2_cli_review_v1",
                    "status": "passed",
                    "artifact_path": str(root / "rosbag2-native"),
                    "bag_dir": str(root / "rosbag2-native"),
                    "validation_status": "passed",
                    "validation_format": "vision_nav_rosbag2_v1",
                    "validation_report": {
                        "schema_version": "vision_nav_rosbag_export_validation_v1",
                        "status": "passed",
                        "format": "vision_nav_rosbag2_v1",
                        "message_count": 4,
                        "topic_count": 3,
                    },
                    "ros2_cli": {
                        "status": "passed",
                        "command": ["ros2", "bag", "info", str(root / "rosbag2-native")],
                        "stdout": "Files: rosbag2_0.db3\n",
                        "stderr": "",
                        "exit_code": 0,
                    },
                    "issues": [],
                }
            )
        )
        workflow_report = root / "autonomy_evidence_workflow.json"
        workflow_report.write_text(
            json.dumps(
                {
                    "schema_version": "vision_nav_autonomy_evidence_workflow_v1",
                    "status": "failed",
                    "summary": {"passed": 2, "failed": 1, "skipped": 3},
                    "markers": {
                        "__VISION_NAV_EVIDENCE_WORKFLOW_VALIDATION__": str(
                            root / "autonomy_evidence_workflow.validation.json"
                        )
                    },
                    "steps": [],
                }
            )
        )
        workflow_validation_report = root / "autonomy_evidence_workflow.validation.json"
        workflow_validation_report.write_text(
            json.dumps(
                {
                    "schema_version": "vision_nav_autonomy_evidence_workflow_validation_v1",
                    "status": "degraded",
                    "workflow_status": "failed",
                    "report_path": str(workflow_report),
                    "step_count": 11,
                    "marker_count": 9,
                    "issue_count": 1,
                    "issues": ["unit validation issue"],
                    "next_required_step": {
                        "name": "register_field_replay_case",
                        "status": "skipped",
                        "exit_code": 0,
                        "notes": "No field case variables supplied.",
                        "command": "./scripts/pi/register_field_replay_case.sh",
                        "desktop_action": "Module Setup > Field Evidence Case > Register",
                        "bundle_path": str(root / "mission_bundle"),
                        "expected_log": str(root / "field-captures/good_texture/terrain_matches.jsonl"),
                        "output_dir": str(root / "field-captures/good_texture"),
                        "runtime_status_path": str(root / "field-captures/good_texture/runtime_status.json"),
                        "capture_command_after_bundle": "VISION_NAV_COUNT=30 ./scripts/pi/run_terrain_nav_loop.sh && ./scripts/pi/read_runtime_status.sh",
                    },
                    "checks": [
                        {
                            "name": "required_step_results",
                            "status": "degraded",
                            "message": "Some required workflow steps did not pass.",
                            "details": {
                                "non_passed_count": 2,
                                "missing_steps": [],
                                "non_passed_steps": [
                                    {
                                        "name": "register_field_replay_case",
                                        "status": "skipped",
                                        "exit_code": 0,
                                        "notes": "No field case variables supplied.",
                                    },
                                    {
                                        "name": "run_autonomy_readiness_audit",
                                        "status": "failed",
                                        "exit_code": 1,
                                        "notes": "Final readiness failed.",
                                    },
                                ],
                            },
                        },
                        {
                            "name": "final_proof_markers",
                            "status": "degraded",
                            "message": "Workflow report is missing final-readiness proof artifact markers.",
                            "details": {
                                "missing_markers": ["__VISION_NAV_THRESHOLD_REPORT__"],
                                "present_markers": ["__VISION_NAV_SUPPORT_ZIP__"],
                                "marker_count": 9,
                            },
                        },
                    ],
                }
            )
        )
        workflow_log_archive = root / "autonomy_evidence_workflow.logs.tar.gz"
        workflow_logs_dir = root / "workflow-logs"
        workflow_logs_dir.mkdir()
        (workflow_logs_dir / "create_field_evidence_template.log").write_text("unit workflow log\n")
        with tarfile.open(workflow_log_archive, "w:gz") as archive:
            archive.add(workflow_logs_dir / "create_field_evidence_template.log", arcname="logs/create_field_evidence_template.log")
        workflow_validation_ready_report = root / "autonomy_evidence_workflow.ready.validation.json"
        workflow_validation_ready_report.write_text(
            json.dumps(
                {
                    "schema_version": "vision_nav_autonomy_evidence_workflow_validation_v1",
                    "status": "passed",
                    "workflow_status": "passed",
                    "report_path": str(workflow_report),
                    "step_count": len(REQUIRED_WORKFLOW_STEPS),
                    "marker_count": 16,
                    "log_archive": str(workflow_log_archive),
                    "issue_count": 0,
                    "issues": [],
                    "next_required_step": None,
                    "checks": [
                        {
                            "name": "schema",
                            "status": "passed",
                            "message": "Workflow report schema is valid.",
                        },
                        {
                            "name": "workflow_provenance",
                            "status": "passed",
                            "message": "Workflow report includes repo/script provenance and the current required-step contract.",
                        },
                        {
                            "name": "required_steps",
                            "status": "passed",
                            "message": "Workflow report includes every ordered evidence step.",
                            "details": {"step_count": len(REQUIRED_WORKFLOW_STEPS)},
                        },
                        {
                            "name": "step_statuses",
                            "status": "passed",
                            "message": "Workflow step statuses are parseable.",
                        },
                        {
                            "name": "required_step_results",
                            "status": "passed",
                            "message": "Every required workflow step passed.",
                            "details": {
                                "required_count": len(REQUIRED_WORKFLOW_STEPS),
                                "missing_steps": [],
                                "non_passed_steps": [],
                                "non_passed_count": 0,
                            },
                        },
                        {
                            "name": "important_markers",
                            "status": "passed",
                            "message": "Workflow report includes the high-value artifact markers.",
                        },
                        {
                            "name": "final_proof_markers",
                            "status": "passed",
                            "message": "Workflow report includes every final-readiness proof artifact marker.",
                        },
                        {
                            "name": "log_archive",
                            "status": "passed",
                            "message": "Workflow log archive includes logs for all reported steps.",
                            "details": {"path": str(workflow_log_archive)},
                        },
                        {
                            "name": "final_readiness_status",
                            "status": "passed",
                            "message": "Workflow final-audit step matches the generated autonomy-readiness report status.",
                            "details": {"readiness_status": "passed", "workflow_status": "passed"},
                        },
                        {
                            "name": "workflow_status",
                            "status": "passed",
                            "message": "Workflow completed without failed or skipped steps.",
                        },
                    ],
                }
            )
        )

        direct_report_support_manifest = root / "support_manifest_without_embedded_field_feature.json"
        direct_report_support_data = json.loads(support_manifest.read_text())
        direct_report_support_data.pop("px4_sitl_evidence", None)
        direct_report_support_data.pop("feature_method_benchmarks", None)
        direct_report_support_data.pop("field_evidence", None)
        direct_report_support_manifest.write_text(json.dumps(direct_report_support_data))

        missing_proof_ready = evaluate_autonomy_readiness(
            research_doc_path=research_doc,
            implementation_plan_path=implementation_plan,
            px4_sitl_prereq_path=px4_prereq_report,
            field_capture_preflight_path=field_capture_preflight,
        )
        missing_proof_phases = {phase["id"]: phase for phase in missing_proof_ready["proof_runbook"]["phases"]}
        assert_equal(
            missing_proof_ready["inputs"]["px4_sitl_prereqs"],
            str(px4_prereq_report),
            "autonomy readiness px4 prereq diagnostic input",
        )
        assert_equal(
            missing_proof_ready["diagnostics"]["px4_sitl_prereqs"]["status"],
            "failed",
            "autonomy readiness px4 prereq diagnostic status",
        )
        assert_equal(
            missing_proof_ready["inputs"]["field_capture_preflight"],
            str(field_capture_preflight),
            "autonomy readiness field capture preflight diagnostic input",
        )
        assert_equal(
            missing_proof_ready["diagnostics"]["field_capture_preflight"]["status"],
            "failed",
            "autonomy readiness field capture preflight diagnostic status",
        )
        assert_equal(
            missing_proof_ready["diagnostics"]["field_capture_preflight"]["failed_checks"][0]["name"],
            "bundle_path",
            "autonomy readiness field capture preflight failed check",
        )
        if "px4_sitl_prereqs" in {item.get("name") for item in missing_proof_ready["evidence_manifest"]["proof_items"]}:
            raise AssertionError("PX4 prerequisite diagnostics must not become a goal proof item")
        if "field_capture_preflight" in {
            item.get("name") for item in missing_proof_ready["evidence_manifest"]["proof_items"]
        }:
            raise AssertionError("Field capture preflight diagnostics must not become a goal proof item")
        prereq_diagnostic_items = [
            item
            for item in missing_proof_ready["evidence_manifest"]["diagnostic_items"]
            if item.get("name") == "px4_sitl_prereqs"
        ]
        assert_equal(len(prereq_diagnostic_items), 1, "autonomy readiness px4 prereq diagnostic item")
        assert_equal(
            prereq_diagnostic_items[0]["requires_external_proof"],
            False,
            "autonomy readiness px4 prereq diagnostic is not external proof",
        )
        preflight_diagnostic_items = [
            item
            for item in missing_proof_ready["evidence_manifest"]["diagnostic_items"]
            if item.get("name") == "field_capture_preflight"
        ]
        assert_equal(len(preflight_diagnostic_items), 1, "autonomy readiness field capture preflight diagnostic item")
        assert_equal(
            preflight_diagnostic_items[0]["requires_external_proof"],
            False,
            "autonomy readiness field capture preflight diagnostic is not external proof",
        )
        assert_equal(
            preflight_diagnostic_items[0]["ready_for_capture"],
            False,
            "autonomy readiness field capture preflight diagnostic capture readiness",
        )
        support_check = next(
            check
            for check in missing_proof_ready["checks"]
            if check.get("name") == "support_bundle_bench_readiness"
        )
        expected_bench_inputs = support_check["details"]["expected_bench_inputs"]
        if "runtime terrain log and runtime_status.json snapshot" not in expected_bench_inputs:
            raise AssertionError("autonomy readiness missing support bundle should list runtime evidence input")
        if "threshold tuning report from real field logs" not in expected_bench_inputs:
            raise AssertionError("autonomy readiness missing support bundle should list threshold tuning evidence input")
        if "ROS replay export validation report" not in expected_bench_inputs:
            raise AssertionError("autonomy readiness missing support bundle should list ROS bag validation evidence input")
        if "native rosbag2 CLI review report" not in expected_bench_inputs:
            raise AssertionError("autonomy readiness missing support bundle should list native rosbag2 review evidence input")
        assert_equal(
            support_check["details"]["support_bundle_command"],
            "./scripts/pi/create_support_bundle.sh",
            "autonomy readiness missing support bundle command",
        )
        px4_capture_command = "VISION_NAV_SITL_SMOKE_DIR=$PWD/px4-sitl-evidence ./scripts/dev/run_px4_sitl_external_vision_capture.sh"
        support_bundle_command = "./scripts/pi/create_support_bundle.sh"
        bench_actions = support_check["details"]["bench_evidence_actions"]
        bench_action_commands = [action.get("command") for action in bench_actions]
        bench_action_desktop_actions = [action.get("desktop_action") for action in bench_actions]
        if "VISION_NAV_COUNT=30 ./scripts/pi/run_terrain_nav_loop.sh" not in bench_action_commands:
            raise AssertionError("autonomy readiness missing support bundle should expose runtime capture action")
        if "./scripts/dev/setup_px4_sitl_prereqs.sh" not in bench_action_commands:
            raise AssertionError("autonomy readiness missing support bundle should expose PX4 prereq setup action")
        if "./scripts/pi/create_support_bundle.sh" not in bench_action_commands:
            raise AssertionError("autonomy readiness missing support bundle should expose support bundle action")
        if "Module Setup > Load Next Field Condition" not in bench_action_desktop_actions:
            raise AssertionError("autonomy readiness missing support bundle should expose next field condition action")
        if "Module Setup > Threshold Tuning" not in bench_action_desktop_actions:
            raise AssertionError("autonomy readiness missing support bundle should expose threshold tuning action")
        if "Module Setup > ROS Bag Validation" not in bench_action_desktop_actions:
            raise AssertionError("autonomy readiness missing support bundle should expose ROS bag validation action")
        if "Module Setup > Native rosbag2 Review, then Local Readiness Re-Audit" not in bench_action_desktop_actions:
            raise AssertionError("autonomy readiness missing support bundle should expose native rosbag2 review action")
        if bench_action_commands.index("./scripts/dev/setup_px4_sitl_prereqs.sh") > bench_action_commands.index(
            px4_capture_command
        ):
            raise AssertionError("autonomy readiness should surface PX4 prereq setup before receiver capture")
        if bench_action_desktop_actions.index("Module Setup > Create Plan") > bench_action_desktop_actions.index(
            "Module Setup > Load Next Field Condition"
        ):
            raise AssertionError("autonomy readiness should create the field plan before loading its next condition")
        if bench_action_desktop_actions.index(
            "Module Setup > Load Next Field Condition"
        ) > bench_action_desktop_actions.index("Module Setup > Evidence Workflow"):
            raise AssertionError("autonomy readiness should load the next field condition before evidence workflow")
        if bench_action_desktop_actions.index("Module Setup > Feature Benchmark") > bench_action_desktop_actions.index(
            "Module Setup > Threshold Tuning"
        ):
            raise AssertionError("autonomy readiness should benchmark methods before threshold tuning")
        if bench_action_desktop_actions.index("Module Setup > Threshold Tuning") > bench_action_desktop_actions.index(
            "Module Setup > ROS Bag Validation"
        ):
            raise AssertionError("autonomy readiness should tune thresholds before ROS bag validation")
        if bench_action_desktop_actions.index("Module Setup > ROS Bag Validation") > bench_action_desktop_actions.index(
            "Module Setup > Native rosbag2 Review, then Local Readiness Re-Audit"
        ):
            raise AssertionError("autonomy readiness should validate ROS bag before native rosbag2 review")
        if bench_action_desktop_actions.index(
            "Module Setup > Native rosbag2 Review, then Local Readiness Re-Audit"
        ) > bench_action_desktop_actions.index("Module Setup > Bench Report"):
            raise AssertionError("autonomy readiness should run ROS proof before refreshing support bundle")
        support_blocker = next(
            blocker
            for blocker in missing_proof_ready["evidence_manifest"]["external_blockers"]
            if blocker.get("name") == "support_bundle_bench_readiness"
        )
        if "PX4 ODOMETRY receiver evidence report" not in support_blocker.get("expected_bench_inputs", []):
            raise AssertionError("autonomy readiness blocker missing support bundle expected inputs")
        if not support_blocker.get("bench_evidence_actions"):
            raise AssertionError("autonomy readiness blocker missing support bundle action hints")
        bench_commands = missing_proof_phases["bench_foundation"]["commands"]
        if bench_commands.index(px4_capture_command) > bench_commands.index(support_bundle_command):
            raise AssertionError("autonomy proof runbook should capture PX4 receiver proof before creating support bundle")
        bench_action_checks = [action.get("check") for action in missing_proof_phases["bench_foundation"]["actions"]]
        assert_equal(
            bench_action_checks[:2],
            ["px4_receiver_proof", "support_bundle_bench_readiness"],
            "autonomy proof runbook bench action order",
        )
        missing_proof_command_bundle = missing_proof_ready["command_bundle"]
        missing_proof_next_commands = missing_proof_command_bundle["next_action_commands"]
        if (
            f"VISION_NAV_BUNDLE={root / 'missing-mission-bundle'} ./scripts/pi/validate_terrain_bundle.sh"
            not in missing_proof_command_bundle["field_capture_preflight_next_action_commands"]
        ):
            raise AssertionError("autonomy readiness should expose field preflight next-action commands")
        if (
            "export VISION_NAV_PX4_AUTOPILOT_DIR=/path/to/PX4-Autopilot"
            not in missing_proof_command_bundle["prerequisite_fix_commands"]
        ):
            raise AssertionError("autonomy readiness should expose PX4 prereq fix commands separately")
        missing_proof_command_items = missing_proof_command_bundle.get("command_items")
        if not isinstance(missing_proof_command_items, list):
            raise AssertionError("autonomy readiness should expose structured command items")
        if not any(
            item.get("group") == "immediate_next_action"
            and item.get("command") == px4_capture_command
            and item.get("desktop_action") == "Module Setup > PX4 SITL Receiver Capture, then Local Readiness Re-Audit"
            for item in missing_proof_command_items
            if isinstance(item, dict)
        ):
            raise AssertionError("autonomy readiness missing structured PX4 capture command item")
        if not any(
            item.get("group") == "field_capture_preflight_next_action"
            and item.get("desktop_action") == "Mission Planner > Build Bundle, Upload Bundle"
            for item in missing_proof_command_items
            if isinstance(item, dict)
        ):
            raise AssertionError("autonomy readiness missing structured field preflight command item")
        if (
            "export VISION_NAV_PX4_AUTOPILOT_DIR=/path/to/PX4-Autopilot"
            in missing_proof_command_bundle["next_action_commands"]
        ):
            raise AssertionError("autonomy readiness should not mix prereq fixes into proof next actions")
        if missing_proof_next_commands.index(px4_capture_command) > missing_proof_next_commands.index(support_bundle_command):
            raise AssertionError("autonomy command bundle should order PX4 receiver proof before support bundle")
        if "./scripts/pi/register_field_replay_case.sh" in missing_proof_next_commands:
            raise AssertionError("autonomy command bundle should not expose the bare replay-case registration wrapper")
        field_bootstrap_command = "./scripts/pi/create_field_evidence_template.sh && ./scripts/pi/create_field_collection_plan.sh"
        guided_workflow_command = "./scripts/pi/run_autonomy_evidence_workflow.sh"
        if missing_proof_next_commands.index(field_bootstrap_command) > missing_proof_next_commands.index(
            guided_workflow_command
        ):
            raise AssertionError("autonomy command bundle should create the field plan before the guided workflow")
        if missing_proof_next_commands.index(guided_workflow_command) > missing_proof_next_commands.index(
            support_bundle_command
        ):
            raise AssertionError("autonomy command bundle should create the support bundle after immediate evidence steps")
        if "./scripts/pi/run_feature_method_benchmark.sh" not in missing_proof_command_bundle["blocked_follow_up_commands"]:
            raise AssertionError("autonomy readiness should mark feature benchmarking as blocked follow-up")
        if "./scripts/pi/run_threshold_tuning_report.sh" not in missing_proof_command_bundle["blocked_follow_up_commands"]:
            raise AssertionError("autonomy readiness should mark threshold tuning as blocked follow-up")
        if "./scripts/pi/run_rosbag_export_validation.sh" not in missing_proof_command_bundle["blocked_follow_up_commands"]:
            raise AssertionError("autonomy readiness should mark ROS replay validation as blocked follow-up")
        if (
            "./scripts/pi/run_rosbag_export_validation.sh"
            in missing_proof_command_bundle["immediate_next_action_commands"]
        ):
            raise AssertionError("autonomy readiness should not copy blocked ROS replay validation as immediate")
        if missing_proof_next_commands.index(
            "./scripts/pi/run_rosbag_export_validation.sh"
        ) < missing_proof_next_commands.index(
            "./scripts/pi/run_autonomy_evidence_workflow.sh"
        ):
            raise AssertionError("autonomy command bundle should list blocked ROS replay after the guided field workflow")

        ready = evaluate_autonomy_readiness(
            research_doc_path=research_doc,
            implementation_plan_path=implementation_plan,
            support_bundle_path=direct_report_support_manifest,
            px4_sitl_report_path=px4_receiver_report,
            px4_sitl_prereq_path=px4_prereq_report,
            field_evidence_report_path=field_report,
            field_collection_plan_path=field_collection_plan,
            feature_method_benchmark_report_path=feature_report,
            threshold_tuning_report_path=threshold_report,
            evidence_workflow_report_path=workflow_report,
            evidence_workflow_validation_report_path=workflow_validation_ready_report,
            evidence_workflow_log_archive_path=workflow_log_archive,
        )
        assert_equal(ready["status"], "passed", "autonomy readiness full proof status")
        workflow_ready_check = next(
            check for check in ready["checks"] if check.get("name") == "evidence_workflow_validation"
        )
        assert_equal(
            workflow_ready_check["status"],
            "passed",
            "autonomy readiness workflow validation full proof status",
        )
        assert_equal(
            ready["metadata"]["schema_version"],
            "vision_nav_autonomy_readiness_audit_metadata_v1",
            "autonomy readiness audit metadata schema",
        )
        if not ready["metadata"]["generated_at_utc"]:
            raise AssertionError("autonomy readiness audit metadata timestamp missing")
        if not isinstance(ready["metadata"].get("repo"), dict):
            raise AssertionError("autonomy readiness audit metadata repo section missing")
        assert_equal(
            ready["inputs"]["field_collection_plan_markdown"],
            str(field_collection_plan.with_suffix(".md")),
            "autonomy readiness field collection markdown input",
        )
        assert_equal(
            ready["inputs"]["evidence_workflow_report"],
            str(workflow_report),
            "autonomy readiness workflow report input",
        )
        assert_equal(
            ready["inputs"]["evidence_workflow_validation_report"],
            str(workflow_validation_ready_report),
            "autonomy readiness workflow validation input",
        )
        assert_equal(
            ready["inputs"]["evidence_workflow_log_archive"],
            str(workflow_log_archive),
            "autonomy readiness workflow log archive input",
        )
        assert_equal(
            ready["diagnostics"]["px4_sitl_prereqs"]["failed_checks"][0]["name"],
            "px4_autopilot_dir",
            "autonomy readiness px4 prereq failed check",
        )
        assert_equal(
            ready["diagnostics"]["px4_sitl_prereqs"]["fix_commands"][0]["condition"],
            "px4_autopilot_dir",
            "autonomy readiness px4 prereq fix command",
        )
        degraded_workflow_ready = evaluate_autonomy_readiness(
            research_doc_path=research_doc,
            implementation_plan_path=implementation_plan,
            support_bundle_path=direct_report_support_manifest,
            px4_sitl_report_path=px4_receiver_report,
            px4_sitl_prereq_path=px4_prereq_report,
            field_evidence_report_path=field_report,
            field_collection_plan_path=field_collection_plan,
            feature_method_benchmark_report_path=feature_report,
            threshold_tuning_report_path=threshold_report,
            evidence_workflow_report_path=workflow_report,
            evidence_workflow_validation_report_path=workflow_validation_report,
            evidence_workflow_log_archive_path=workflow_log_archive,
        )
        assert_equal(
            degraded_workflow_ready["status"],
            "failed",
            "autonomy readiness degraded workflow validation should fail final proof",
        )
        degraded_workflow_check = next(
            check
            for check in degraded_workflow_ready["checks"]
            if check.get("name") == "evidence_workflow_validation"
        )
        assert_equal(
            degraded_workflow_check["status"],
            "failed",
            "autonomy readiness workflow validation strict gate",
        )
        degraded_workflow_actions = [
            action
            for action in degraded_workflow_ready["next_actions"]
            if action.get("check") == "evidence_workflow_validation"
        ]
        assert_equal(len(degraded_workflow_actions), 1, "autonomy readiness workflow validation next action")
        assert_equal(
            degraded_workflow_actions[0]["command"],
            "./scripts/pi/run_autonomy_evidence_workflow.sh",
            "autonomy readiness workflow validation recovery command",
        )
        assert_equal(
            ready["plan_snapshot"]["schema_version"],
            "vision_nav_autonomy_plan_snapshot_v1",
            "autonomy readiness plan snapshot schema",
        )
        assert_equal(
            ready["plan_snapshot"]["research_doc"]["missing_markers"],
            [],
            "autonomy readiness research snapshot markers",
        )
        assert_equal(
            len(ready["plan_snapshot"]["research_doc"]["source_sha256"]),
            64,
            "autonomy readiness research source hash",
        )
        if not ready["plan_snapshot"]["research_doc"]["source_size_bytes"]:
            raise AssertionError("autonomy readiness research source size missing")
        assert_equal(
            len(ready["plan_snapshot"]["implementation_plan"]["source_sha256"]),
            64,
            "autonomy readiness implementation source hash",
        )
        if not ready["plan_snapshot"]["implementation_plan"]["source_size_bytes"]:
            raise AssertionError("autonomy readiness implementation source size missing")
        assert_equal(
            ready["plan_snapshot"]["implementation_plan"]["track_count"],
            6,
            "autonomy readiness implementation track count",
        )
        assert_equal(
            ready["plan_snapshot"]["implementation_plan"]["acceptance_check_count"],
            2,
            "autonomy readiness implementation acceptance checks",
        )
        assert_equal(
            ready["plan_snapshot"]["implementation_plan"]["task_count"],
            1,
            "autonomy readiness implementation task count",
        )
        assert_equal(
            ready["plan_snapshot"]["implementation_plan"]["next_task_count"],
            1,
            "autonomy readiness implementation next task count",
        )
        assert_equal(
            ready["plan_snapshot"]["implementation_plan"]["execution_order_count"],
            1,
            "autonomy readiness implementation execution order",
        )
        ready_checks = {check["name"]: check["status"] for check in ready["checks"]}
        ready_check_details = {check["name"]: check.get("details") or {} for check in ready["checks"]}
        assert_equal(ready_checks["support_bundle_bench_readiness"], "passed", "autonomy readiness support bundle")
        assert_equal(ready_checks["px4_receiver_proof"], "passed", "autonomy readiness direct px4 receiver proof")
        assert_equal(
            ready_check_details["px4_receiver_proof"]["observed_rate_hz"],
            5.0,
            "autonomy readiness direct px4 observed rate",
        )
        assert_equal(
            ready_check_details["px4_receiver_proof"]["expected_rate_hz"],
            5.0,
            "autonomy readiness direct px4 expected rate",
        )
        assert_equal(
            ready_check_details["px4_receiver_proof"]["required_message"],
            "odometry",
            "autonomy readiness direct px4 required message",
        )
        assert_equal(ready_checks["field_evidence_proof"], "passed", "autonomy readiness field evidence")
        assert_equal(ready_checks["field_collection_plan"], "passed", "autonomy readiness field collection plan")
        assert_equal(ready_checks["feature_method_benchmark"], "passed", "autonomy readiness feature benchmark")
        assert_equal(ready_checks["threshold_tuning"], "passed", "autonomy readiness threshold tuning")
        assert_equal(ready_checks["rosbag_export_validation"], "passed", "autonomy readiness rosbag validation")
        assert_equal(ready_checks["rosbag2_cli_review"], "passed", "autonomy readiness rosbag2 cli review")
        assert_equal(len(ready["next_actions"]), 0, "autonomy readiness passing report next actions")
        assert_equal(
            ready["evidence_manifest"]["ready_for_goal_completion"],
            True,
            "autonomy readiness evidence manifest ready flag",
        )
        assert_equal(
            len(ready["evidence_manifest"]["external_blockers"]),
            0,
            "autonomy readiness evidence manifest no external blockers",
        )
        assert_equal(
            ready["proof_runbook"]["schema_version"],
            "vision_nav_autonomy_proof_runbook_v1",
            "autonomy readiness proof runbook schema",
        )
        assert_equal(
            ready["proof_runbook"]["summary"]["passed"],
            6,
            "autonomy readiness proof runbook all phases passed",
        )
        ready_runbook_phases = {phase["id"]: phase for phase in ready["proof_runbook"]["phases"]}
        assert_equal(
            ready_runbook_phases["final_audit"]["status"],
            "passed",
            "autonomy readiness proof runbook final phase passed",
        )
        if "./scripts/dev/run_local_autonomy_readiness_audit.sh" not in ready_runbook_phases["final_audit"]["commands"]:
            raise AssertionError("autonomy readiness proof runbook missing final local audit command")

        headings_only_research = root / "research_headings_only.md"
        headings_only_research.write_text(
            "\n".join(
                [
                    "# Autonomy And Ground Control Research",
                    "## Highest-Value References",
                    "## Recommended Product Architecture Changes",
                    "## Near-Term Repo Integration Plan",
                ]
            )
        )
        headings_only_research_ready = evaluate_autonomy_readiness(
            research_doc_path=headings_only_research,
            implementation_plan_path=implementation_plan,
            support_bundle_path=direct_report_support_manifest,
            px4_sitl_report_path=px4_receiver_report,
            field_evidence_report_path=field_report,
            field_collection_plan_path=field_collection_plan,
            feature_method_benchmark_report_path=feature_report,
            threshold_tuning_report_path=threshold_report,
            evidence_workflow_report_path=workflow_report,
            evidence_workflow_validation_report_path=workflow_validation_report,
            evidence_workflow_log_archive_path=workflow_log_archive,
        )
        headings_only_research_checks = {
            check["name"]: check["status"]
            for check in headings_only_research_ready["checks"]
        }
        assert_equal(headings_only_research_ready["status"], "failed", "autonomy readiness rejects headings-only research doc")
        assert_equal(
            headings_only_research_checks["research_doc"],
            "failed",
            "autonomy readiness research doc requires substantive sections",
        )
        research_doc_actions = [
            action
            for action in headings_only_research_ready["next_actions"]
            if action.get("check") == "research_doc"
        ]
        assert_equal(len(research_doc_actions), 1, "autonomy readiness research doc next action")
        if "autonomy-ground-control-research.md" not in research_doc_actions[0]["command"]:
            raise AssertionError("autonomy readiness research doc action should point to the research doc")
        headings_only_research_phases = {
            phase["id"]: phase
            for phase in headings_only_research_ready["proof_runbook"]["phases"]
        }
        assert_equal(
            headings_only_research_phases["plan_source"]["status"],
            "action_required",
            "autonomy readiness research source runbook plan phase action required",
        )
        assert_equal(
            headings_only_research_phases["bench_foundation"]["status"],
            "blocked",
            "autonomy readiness research source blocks bench phase",
        )
        assert_equal(
            headings_only_research_phases["field_dataset"]["dependency_status"]["plan_source"],
            "action_required",
            "autonomy readiness research source field dependency",
        )

        headings_only_plan = root / "implementation_plan_headings_only.md"
        headings_only_plan.write_text(
            "\n".join(
                [
                    "# Autonomy And Ground Control Implementation Plan",
                    "### Track 1: External Position Output",
                    "### Track 2: ROS 2 Companion Runtime",
                    "### Track 3: Terrain Map Bundle Pipeline",
                    "### Track 4: Desktop Setup And Mission UX",
                    "### Track 5: Validation And Product Risk Controls",
                    "### Track 6: ArduPilot Adapter Path",
                ]
            )
        )
        headings_only_ready = evaluate_autonomy_readiness(
            research_doc_path=research_doc,
            implementation_plan_path=headings_only_plan,
            support_bundle_path=direct_report_support_manifest,
            px4_sitl_report_path=px4_receiver_report,
            field_evidence_report_path=field_report,
            field_collection_plan_path=field_collection_plan,
            feature_method_benchmark_report_path=feature_report,
            threshold_tuning_report_path=threshold_report,
            evidence_workflow_report_path=workflow_report,
            evidence_workflow_validation_report_path=workflow_validation_report,
            evidence_workflow_log_archive_path=workflow_log_archive,
        )
        headings_only_checks = {check["name"]: check["status"] for check in headings_only_ready["checks"]}
        assert_equal(headings_only_ready["status"], "failed", "autonomy readiness rejects headings-only plan")
        assert_equal(
            headings_only_checks["implementation_plan"],
            "failed",
            "autonomy readiness implementation plan requires executable scaffolding",
        )
        implementation_plan_actions = [
            action
            for action in headings_only_ready["next_actions"]
            if action.get("check") == "implementation_plan"
        ]
        assert_equal(len(implementation_plan_actions), 1, "autonomy readiness implementation plan next action")
        if "autonomy-ground-control-implementation-plan.md" not in implementation_plan_actions[0]["command"]:
            raise AssertionError("autonomy readiness implementation plan action should point to the plan doc")
        headings_only_plan_phases = {
            phase["id"]: phase
            for phase in headings_only_ready["proof_runbook"]["phases"]
        }
        assert_equal(
            headings_only_plan_phases["plan_source"]["status"],
            "action_required",
            "autonomy readiness implementation source runbook plan phase action required",
        )
        assert_equal(
            headings_only_plan_phases["ros2_replay"]["status"],
            "blocked",
            "autonomy readiness implementation source blocks ros2 phase",
        )
        assert_equal(
            headings_only_plan_phases["final_audit"]["dependency_status"]["plan_source"],
            "action_required",
            "autonomy readiness implementation source final dependency",
        )

        missing_metadata_audit_field_report = root / "field_evidence_missing_metadata_audit.json"
        missing_metadata_field_data = json.loads(field_report.read_text())
        missing_metadata_field_data["summary"].pop("capture_metadata_issue_count", None)
        missing_metadata_audit_field_report.write_text(json.dumps(missing_metadata_field_data))
        missing_metadata_audit_ready = evaluate_autonomy_readiness(
            research_doc_path=research_doc,
            implementation_plan_path=implementation_plan,
            support_bundle_path=direct_report_support_manifest,
            px4_sitl_report_path=px4_receiver_report,
            field_evidence_report_path=missing_metadata_audit_field_report,
            field_collection_plan_path=field_collection_plan,
            feature_method_benchmark_report_path=feature_report,
            threshold_tuning_report_path=threshold_report,
            evidence_workflow_report_path=workflow_report,
            evidence_workflow_validation_report_path=workflow_validation_report,
            evidence_workflow_log_archive_path=workflow_log_archive,
        )
        missing_metadata_checks = {check["name"]: check["status"] for check in missing_metadata_audit_ready["checks"]}
        assert_equal(
            missing_metadata_checks["field_evidence_proof"],
            "failed",
            "autonomy readiness requires field capture metadata audit",
        )

        compatibility_receiver_report = root / "receiver_evidence_compatibility.json"
        compatibility_receiver_data = json.loads(px4_receiver_report.read_text())
        compatibility_receiver_data["expected_message"] = "vision_position_estimate"
        compatibility_receiver_report.write_text(json.dumps(compatibility_receiver_data))
        compatibility_ready = evaluate_autonomy_readiness(
            research_doc_path=research_doc,
            implementation_plan_path=implementation_plan,
            support_bundle_path=direct_report_support_manifest,
            px4_sitl_report_path=compatibility_receiver_report,
            field_evidence_report_path=field_report,
            field_collection_plan_path=field_collection_plan,
            feature_method_benchmark_report_path=feature_report,
            threshold_tuning_report_path=threshold_report,
            evidence_workflow_report_path=workflow_report,
            evidence_workflow_validation_report_path=workflow_validation_report,
            evidence_workflow_log_archive_path=workflow_log_archive,
        )
        compatibility_ready_checks = {check["name"]: check["status"] for check in compatibility_ready["checks"]}
        assert_equal(compatibility_ready["status"], "failed", "autonomy readiness requires odometry px4 proof")
        assert_equal(
            compatibility_ready_checks["px4_receiver_proof"],
            "failed",
            "autonomy readiness rejects compatibility-only px4 proof",
        )
        px4_actions = [
            action
            for action in compatibility_ready["next_actions"]
            if action.get("check") == "px4_receiver_proof"
        ]
        assert_equal(len(px4_actions), 1, "autonomy readiness px4 receiver next action")
        assert_equal(
            px4_actions[0]["desktop_action"],
            "Module Setup > PX4 SITL Receiver Capture, then Local Readiness Re-Audit",
            "autonomy readiness px4 receiver desktop action",
        )

        missing_feature_direct = evaluate_autonomy_readiness(
            research_doc_path=research_doc,
            implementation_plan_path=implementation_plan,
            support_bundle_path=direct_report_support_manifest,
            px4_sitl_report_path=px4_receiver_report,
            field_evidence_report_path=field_report,
            field_collection_plan_path=field_collection_plan,
            threshold_tuning_report_path=threshold_report,
        )
        missing_feature_checks = {check["name"]: check["status"] for check in missing_feature_direct["checks"]}
        assert_equal(missing_feature_direct["status"], "failed", "autonomy readiness missing direct feature benchmark")
        assert_equal(
            missing_feature_checks["feature_method_benchmark"],
            "failed",
            "autonomy readiness feature benchmark fail closed",
        )
        feature_actions = [
            action
            for action in missing_feature_direct["next_actions"]
            if action.get("check") == "feature_method_benchmark"
        ]
        assert_equal(len(feature_actions), 1, "autonomy readiness feature benchmark next action")
        assert_equal(
            feature_actions[0]["desktop_action"],
            "Module Setup > Feature Benchmark",
            "autonomy readiness feature benchmark desktop action",
        )
        feature_blockers = [
            blocker
            for blocker in missing_feature_direct["evidence_manifest"]["external_blockers"]
            if blocker.get("name") == "feature_method_benchmark"
        ]
        assert_equal(len(feature_blockers), 1, "autonomy readiness feature benchmark evidence blocker")
        missing_feature_phases = {phase["id"]: phase for phase in missing_feature_direct["proof_runbook"]["phases"]}
        assert_equal(
            missing_feature_phases["method_thresholds"]["status"],
            "action_required",
            "autonomy readiness feature runbook method phase action required",
        )
        assert_equal(
            missing_feature_phases["final_audit"]["status"],
            "blocked",
            "autonomy readiness feature runbook final blocked",
        )

        missing_field_direct = evaluate_autonomy_readiness(
            research_doc_path=research_doc,
            implementation_plan_path=implementation_plan,
            support_bundle_path=direct_report_support_manifest,
            px4_sitl_report_path=px4_receiver_report,
            field_collection_plan_path=field_collection_plan,
            feature_method_benchmark_report_path=feature_report,
            threshold_tuning_report_path=threshold_report,
        )
        missing_field_actions = [
            action
            for action in missing_field_direct["next_actions"]
            if action.get("check") == "field_evidence_proof"
        ]
        assert_equal(len(missing_field_actions), 1, "autonomy readiness field evidence next action")
        assert_equal(
            missing_field_actions[0]["desktop_action"],
            "Module Setup > Evidence Workflow",
            "autonomy readiness field evidence desktop action",
        )
        assert_equal(
            missing_field_actions[0]["command"],
            "./scripts/pi/run_autonomy_evidence_workflow.sh",
            "autonomy readiness field evidence guided workflow command",
        )
        support_field_actions = [
            action
            for action in missing_field_direct["next_actions"]
            if action.get("check") == "support_bundle_bench_readiness.field_evidence"
        ]
        assert_equal(len(support_field_actions), 1, "autonomy readiness support field evidence subcheck action")
        assert_equal(
            support_field_actions[0]["desktop_action"],
            "Module Setup > Evidence Workflow",
            "autonomy readiness support field evidence desktop action",
        )
        missing_field_phases = {phase["id"]: phase for phase in missing_field_direct["proof_runbook"]["phases"]}
        assert_equal(
            missing_field_phases["field_dataset"]["status"],
            "action_required",
            "autonomy readiness field runbook field phase action required",
        )
        assert_equal(
            missing_field_phases["method_thresholds"]["status"],
            "blocked",
            "autonomy readiness field runbook method phase blocked",
        )
        assert_equal(
            missing_field_phases["ros2_replay"]["status"],
            "blocked",
            "autonomy readiness field runbook ros2 phase blocked",
        )
        assert_equal(
            missing_field_phases["ros2_replay"]["dependency_status"]["field_dataset"],
            "action_required",
            "autonomy readiness ros2 replay waits on field dataset",
        )
        incomplete_field_collection_plan = root / "field_collection_plan_incomplete.json"
        incomplete_field_collection_plan.write_text(
            json.dumps(
                {
                    "schema_version": "vision_nav_field_collection_plan_v1",
                    "status": "degraded",
                    "manifest_path": str(root / "field_manifest.json"),
                    "site_name": "unit-field",
                    "summary": {
                        "required_count": len(REQUIRED_FIELD_CONDITIONS),
                        "registered_count": 1,
                        "registered_missing_log_count": 0,
                        "placeholder_count": len(REQUIRED_FIELD_CONDITIONS) - 1,
                        "missing_count": 0,
                    },
                    "conditions": [
                        {
                            "condition": "good_texture",
                            "status": "registered",
                            "expected": "good_map",
                            "case_name": "unit-good-texture",
                            "source_log": str(root / "field-captures/good_texture/terrain_matches.jsonl"),
                            "capture_output_dir": str(root / "field-captures/good_texture"),
                            "runtime_status_path": str(root / "field-captures/good_texture/runtime_status.json"),
                        },
                        {
                            "condition": "blur",
                            "status": "placeholder",
                            "expected": "degraded",
                            "case_name": "unit-blur",
                            "bundle": str(root / "map_bundles/unit_bundle"),
                            "preflight_command": "./scripts/pi/preflight_field_capture.sh --condition blur",
                            "capture_command": "./scripts/pi/run_terrain_nav_loop.sh --condition blur",
                            "metadata_update_command": "./scripts/pi/update_field_capture_metadata.sh --condition blur",
                            "register_command": "./scripts/pi/register_field_replay_case.sh --condition blur",
                        },
                    ],
                }
            )
        )
        incomplete_field_plan_ready = evaluate_autonomy_readiness(
            research_doc_path=research_doc,
            implementation_plan_path=implementation_plan,
            support_bundle_path=direct_report_support_manifest,
            px4_sitl_report_path=px4_receiver_report,
            field_evidence_report_path=field_report,
            field_collection_plan_path=incomplete_field_collection_plan,
            feature_method_benchmark_report_path=feature_report,
            threshold_tuning_report_path=threshold_report,
        )
        incomplete_field_plan_checks = {check["name"]: check["status"] for check in incomplete_field_plan_ready["checks"]}
        assert_equal(
            incomplete_field_plan_checks["field_collection_plan"],
            "failed",
            "autonomy readiness incomplete field collection plan fail closed",
        )
        field_plan_check = next(
            check
            for check in incomplete_field_plan_ready["checks"]
            if check.get("name") == "field_collection_plan"
        )
        next_condition = field_plan_check["details"]["next_condition"]
        assert_equal(
            next_condition["condition"],
            "blur",
            "autonomy readiness incomplete field collection next condition",
        )
        assert_equal(
            next_condition["capture_command"],
            "./scripts/pi/run_terrain_nav_loop.sh --condition blur && ./scripts/pi/read_runtime_status.sh",
            "autonomy readiness incomplete field collection next capture command",
        )
        if "VISION_NAV_FIELD_OPERATOR=TODO_operator" not in next_condition["metadata_update_command"]:
            raise AssertionError("autonomy readiness incomplete field collection next metadata command should prompt operator")
        if "./scripts/pi/update_field_capture_metadata.sh" not in next_condition["metadata_update_command"]:
            raise AssertionError("autonomy readiness incomplete field collection next metadata command should run helper")
        assert_equal(
            next_condition["register_command"],
            "./scripts/pi/register_field_replay_case.sh --condition blur",
            "autonomy readiness incomplete field collection next register command",
        )
        field_plan_actions = [
            action
            for action in incomplete_field_plan_ready["next_actions"]
            if action.get("check") == "field_collection_plan"
        ]
        assert_equal(len(field_plan_actions), 1, "autonomy readiness field collection plan next action")
        assert_equal(
            field_plan_actions[0]["desktop_action"],
            "Module Setup > Create Plan",
            "autonomy readiness field collection plan desktop action",
        )
        assert_equal(
            field_plan_actions[0]["command"],
            "./scripts/pi/create_field_evidence_template.sh && ./scripts/pi/create_field_collection_plan.sh",
            "autonomy readiness field collection plan bootstrap command",
        )
        field_plan_bundle = incomplete_field_plan_ready["command_bundle"]
        if (
            "./scripts/pi/create_field_evidence_template.sh && ./scripts/pi/create_field_collection_plan.sh"
            not in field_plan_bundle["next_action_commands"]
        ):
            raise AssertionError("autonomy readiness JSON missing field collection bootstrap command")
        if (
            "./scripts/pi/preflight_field_capture.sh --condition blur"
            not in field_plan_bundle["field_collection_preflight_commands"]
        ):
            raise AssertionError("autonomy readiness JSON missing field preflight command bundle")
        if (
            "./scripts/pi/run_terrain_nav_loop.sh --condition blur && ./scripts/pi/read_runtime_status.sh"
            not in field_plan_bundle["field_collection_capture_commands"]
        ):
            raise AssertionError("autonomy readiness JSON missing field capture command bundle")
        if not any(
            "VISION_NAV_FIELD_OPERATOR=TODO_operator" in command
            and "./scripts/pi/update_field_capture_metadata.sh" in command
            for command in field_plan_bundle["field_collection_metadata_update_commands"]
        ):
            raise AssertionError("autonomy readiness JSON missing field metadata update command bundle")
        if (
            "./scripts/pi/register_field_replay_case.sh --condition blur"
            not in field_plan_bundle["field_collection_registration_commands"]
        ):
            raise AssertionError("autonomy readiness JSON missing field registration command bundle")
        expected_bundle_validate_command = (
            f"VISION_NAV_BUNDLE={shlex.quote(str(root / 'map_bundles/unit_bundle'))} \\\n"
            "  ./scripts/pi/validate_terrain_bundle.sh"
        )
        missing_support_with_next_field = evaluate_autonomy_readiness(
            research_doc_path=research_doc,
            implementation_plan_path=implementation_plan,
            field_collection_plan_path=incomplete_field_collection_plan,
        )
        missing_support_check = next(
            check
            for check in missing_support_with_next_field["checks"]
            if check.get("name") == "support_bundle_bench_readiness"
        )
        missing_support_bench_actions = missing_support_check["details"]["bench_evidence_actions"]
        if not any(
            action.get("command") == expected_bundle_validate_command
            and action.get("field_bundle") == str(root / "map_bundles/unit_bundle")
            for action in missing_support_bench_actions
            if isinstance(action, dict)
        ):
            raise AssertionError("autonomy readiness missing support preview should use selected field bundle")
        field_plan_command_items = field_plan_bundle.get("command_items")
        if not isinstance(field_plan_command_items, list):
            raise AssertionError("autonomy readiness JSON missing structured field command items")
        if not any(
            item.get("group") == "field_collection_preflight"
            and item.get("command") == "./scripts/pi/preflight_field_capture.sh --condition blur"
            and item.get("desktop_action") == "Module Setup > Field Capture Preflight"
            for item in field_plan_command_items
            if isinstance(item, dict)
        ):
            raise AssertionError("autonomy readiness JSON missing structured field preflight command item")
        if not any(
            item.get("group") == "field_collection_metadata_update"
            and "VISION_NAV_FIELD_OPERATOR=TODO_operator" in item.get("command", "")
            and item.get("desktop_action") == "Module Setup > Field Evidence Case > Update Metadata"
            for item in field_plan_command_items
            if isinstance(item, dict)
        ):
            raise AssertionError("autonomy readiness JSON missing structured field metadata command item")
        incomplete_handoff = render_handoff_markdown(incomplete_field_plan_ready)
        if "Field collection preflight commands:" not in incomplete_handoff:
            raise AssertionError("autonomy handoff missing field preflight command section")
        if "Field collection metadata update commands:" not in incomplete_handoff:
            raise AssertionError("autonomy handoff missing field metadata update command section")
        if "./scripts/pi/preflight_field_capture.sh --condition blur" not in incomplete_handoff:
            raise AssertionError("autonomy handoff missing field preflight command")
        if "VISION_NAV_FIELD_OPERATOR=TODO_operator" not in incomplete_handoff:
            raise AssertionError("autonomy handoff missing field metadata update command")
        incomplete_report = root / "autonomy_readiness_incomplete_field_plan.json"
        incomplete_handoff_path = root / "autonomy_readiness_incomplete_field_plan.md"
        incomplete_report.write_text(json.dumps(incomplete_field_plan_ready))
        incomplete_handoff_path.write_text(incomplete_handoff)
        incomplete_package = create_evidence_package(
            incomplete_report,
            handoff_path=incomplete_handoff_path,
            output_path=root / "autonomy_evidence_package_incomplete_field_plan.zip",
        )
        with zipfile.ZipFile(Path(incomplete_package["zip_path"])) as archive:
            incomplete_package_manifest = json.loads(archive.read("manifest.json"))
            incomplete_package_bundle = incomplete_package_manifest.get("command_bundle")
            if not isinstance(incomplete_package_bundle, dict):
                raise AssertionError("autonomy evidence package missing incomplete field plan command bundle")
            if not any(
                "./scripts/pi/preflight_field_capture.sh --condition blur" in command
                for command in incomplete_package_bundle.get("field_collection_preflight_commands", [])
            ):
                raise AssertionError("autonomy evidence package missing field preflight command group")
            if not any(
                "VISION_NAV_FIELD_OPERATOR=TODO_operator" in command
                for command in incomplete_package_bundle.get("field_collection_metadata_update_commands", [])
            ):
                raise AssertionError("autonomy evidence package missing field metadata update command group")
            incomplete_command_items = incomplete_package_bundle.get("command_items")
            if not isinstance(incomplete_command_items, list):
                raise AssertionError("autonomy evidence package missing structured command items")
            if not any(
                item.get("group") == "field_collection_preflight"
                and item.get("command") == "./scripts/pi/preflight_field_capture.sh --condition blur"
                and item.get("desktop_action") == "Module Setup > Field Capture Preflight"
                for item in incomplete_command_items
                if isinstance(item, dict)
            ):
                raise AssertionError("autonomy evidence package missing structured field preflight command item")
            if not any(
                item.get("group") == "field_collection_metadata_update"
                and "VISION_NAV_FIELD_OPERATOR=TODO_operator" in item.get("command", "")
                and item.get("desktop_action") == "Module Setup > Field Evidence Case > Update Metadata"
                for item in incomplete_command_items
                if isinstance(item, dict)
            ):
                raise AssertionError("autonomy evidence package missing structured field metadata command item")

        missing_rosbag_manifest = root / "support_manifest_without_rosbag_validation.json"
        missing_rosbag_data = json.loads(direct_report_support_manifest.read_text())
        missing_rosbag_data.pop("rosbag_export_validations", None)
        missing_rosbag_manifest.write_text(json.dumps(missing_rosbag_data))
        missing_rosbag_ready = evaluate_autonomy_readiness(
            research_doc_path=research_doc,
            implementation_plan_path=implementation_plan,
            support_bundle_path=missing_rosbag_manifest,
            px4_sitl_report_path=px4_receiver_report,
            field_evidence_report_path=field_report,
            field_collection_plan_path=field_collection_plan,
            feature_method_benchmark_report_path=feature_report,
            threshold_tuning_report_path=threshold_report,
        )
        assert_equal(missing_rosbag_ready["status"], "failed", "autonomy readiness missing rosbag validation")
        missing_rosbag_checks = {check["name"]: check["status"] for check in missing_rosbag_ready["checks"]}
        assert_equal(
            missing_rosbag_checks["rosbag_export_validation"],
            "failed",
            "autonomy readiness rosbag validation fail closed",
        )
        rosbag_actions = [
            action
            for action in missing_rosbag_ready["next_actions"]
            if action.get("check") == "rosbag_export_validation"
        ]
        assert_equal(len(rosbag_actions), 1, "autonomy readiness rosbag validation next action")
        assert_equal(
            rosbag_actions[0]["desktop_action"],
            "Module Setup > ROS Bag Validation",
            "autonomy readiness rosbag validation desktop action",
        )
        if "run_rosbag_export_validation.sh" not in rosbag_actions[0]["command"]:
            raise AssertionError("autonomy readiness rosbag action should use the Pi ROS bag validation wrapper")

        direct_rosbag_ready = evaluate_autonomy_readiness(
            research_doc_path=research_doc,
            implementation_plan_path=implementation_plan,
            support_bundle_path=missing_rosbag_manifest,
            px4_sitl_report_path=px4_receiver_report,
            field_evidence_report_path=field_report,
            field_collection_plan_path=field_collection_plan,
            feature_method_benchmark_report_path=feature_report,
            threshold_tuning_report_path=threshold_report,
            rosbag_export_validation_path=rosbag_validation_report,
            evidence_workflow_report_path=workflow_report,
            evidence_workflow_validation_report_path=workflow_validation_ready_report,
            evidence_workflow_log_archive_path=workflow_log_archive,
        )
        direct_rosbag_checks = {check["name"]: check["status"] for check in direct_rosbag_ready["checks"]}
        assert_equal(direct_rosbag_ready["status"], "passed", "autonomy readiness direct rosbag validation")
        assert_equal(
            direct_rosbag_checks["rosbag_export_validation"],
            "passed",
            "autonomy readiness direct rosbag validation check",
        )

        missing_rosbag2_manifest = root / "support_manifest_without_rosbag2_cli_review.json"
        missing_rosbag2_data = json.loads(direct_report_support_manifest.read_text())
        missing_rosbag2_data.pop("rosbag2_cli_reviews", None)
        missing_rosbag2_manifest.write_text(json.dumps(missing_rosbag2_data))
        missing_rosbag2_ready = evaluate_autonomy_readiness(
            research_doc_path=research_doc,
            implementation_plan_path=implementation_plan,
            support_bundle_path=missing_rosbag2_manifest,
            px4_sitl_report_path=px4_receiver_report,
            field_evidence_report_path=field_report,
            field_collection_plan_path=field_collection_plan,
            feature_method_benchmark_report_path=feature_report,
            threshold_tuning_report_path=threshold_report,
        )
        missing_rosbag2_checks = {check["name"]: check["status"] for check in missing_rosbag2_ready["checks"]}
        assert_equal(missing_rosbag2_ready["status"], "failed", "autonomy readiness missing rosbag2 cli review")
        assert_equal(
            missing_rosbag2_checks["rosbag2_cli_review"],
            "failed",
            "autonomy readiness rosbag2 cli review fail closed",
        )
        rosbag2_actions = [
            action
            for action in missing_rosbag2_ready["next_actions"]
            if action.get("check") == "rosbag2_cli_review"
        ]
        assert_equal(len(rosbag2_actions), 1, "autonomy readiness rosbag2 cli review next action")
        assert_equal(
            rosbag2_actions[0]["desktop_action"],
            "Module Setup > Native rosbag2 Review, then Local Readiness Re-Audit",
            "autonomy readiness rosbag2 desktop action",
        )
        if "scripts/dev/run_rosbag2_cli_review.sh" not in rosbag2_actions[0]["command"]:
            raise AssertionError("autonomy readiness rosbag2 action should use the sourced-workstation wrapper")

        direct_rosbag2_ready = evaluate_autonomy_readiness(
            research_doc_path=research_doc,
            implementation_plan_path=implementation_plan,
            support_bundle_path=missing_rosbag2_manifest,
            px4_sitl_report_path=px4_receiver_report,
            field_evidence_report_path=field_report,
            field_collection_plan_path=field_collection_plan,
            feature_method_benchmark_report_path=feature_report,
            threshold_tuning_report_path=threshold_report,
            rosbag2_cli_review_path=rosbag2_cli_review,
            evidence_workflow_report_path=workflow_report,
            evidence_workflow_validation_report_path=workflow_validation_ready_report,
            evidence_workflow_log_archive_path=workflow_log_archive,
        )
        direct_rosbag2_checks = {check["name"]: check["status"] for check in direct_rosbag2_ready["checks"]}
        assert_equal(direct_rosbag2_ready["status"], "passed", "autonomy readiness direct rosbag2 cli review")
        assert_equal(
            direct_rosbag2_checks["rosbag2_cli_review"],
            "passed",
            "autonomy readiness direct rosbag2 cli review check",
        )

        missing_runtime_status_manifest = root / "support_manifest_without_runtime_status.json"
        missing_runtime_status_data = json.loads(direct_report_support_manifest.read_text())
        missing_runtime_status_data["logs"]["runtime_statuses"] = []
        missing_runtime_status_manifest.write_text(json.dumps(missing_runtime_status_data))
        missing_runtime_status_ready = evaluate_autonomy_readiness(
            research_doc_path=research_doc,
            implementation_plan_path=implementation_plan,
            support_bundle_path=missing_runtime_status_manifest,
            px4_sitl_report_path=px4_receiver_report,
            field_evidence_report_path=field_report,
            field_collection_plan_path=field_collection_plan,
            feature_method_benchmark_report_path=feature_report,
            threshold_tuning_report_path=threshold_report,
            evidence_workflow_report_path=workflow_report,
            evidence_workflow_validation_report_path=workflow_validation_ready_report,
            evidence_workflow_log_archive_path=workflow_log_archive,
        )
        assert_equal(missing_runtime_status_ready["status"], "degraded", "autonomy readiness missing runtime status")
        runtime_actions = [
            action
            for action in missing_runtime_status_ready["next_actions"]
            if action.get("check") == "support_bundle_bench_readiness.runtime_status"
        ]
        assert_equal(len(runtime_actions), 1, "autonomy readiness runtime status next action")
        assert_equal(
            runtime_actions[0]["desktop_action"],
            "Module Setup > Field Log Capture, then Runtime Status and Bench Report",
            "autonomy readiness runtime status desktop action",
        )
        missing_bundle_manifest = root / "support_manifest_without_bundle.json"
        missing_bundle_data = json.loads(direct_report_support_manifest.read_text())
        missing_bundle_data.pop("bundle", None)
        missing_bundle_manifest.write_text(json.dumps(missing_bundle_data))
        missing_bundle_with_next_field = evaluate_autonomy_readiness(
            research_doc_path=research_doc,
            implementation_plan_path=implementation_plan,
            support_bundle_path=missing_bundle_manifest,
            px4_sitl_report_path=px4_receiver_report,
            field_evidence_report_path=field_report,
            field_collection_plan_path=incomplete_field_collection_plan,
            feature_method_benchmark_report_path=feature_report,
            threshold_tuning_report_path=threshold_report,
            evidence_workflow_report_path=workflow_report,
            evidence_workflow_validation_report_path=workflow_validation_ready_report,
            evidence_workflow_log_archive_path=workflow_log_archive,
        )
        field_bundle_health_actions = [
            action
            for action in missing_bundle_with_next_field["next_actions"]
            if action.get("check") == "support_bundle_bench_readiness.bundle_health"
        ]
        assert_equal(len(field_bundle_health_actions), 1, "autonomy readiness field bundle next action")
        assert_equal(
            field_bundle_health_actions[0]["command"],
            expected_bundle_validate_command,
            "autonomy readiness bundle action should validate selected field bundle",
        )
        missing_runtime_status_with_next_field = evaluate_autonomy_readiness(
            research_doc_path=research_doc,
            implementation_plan_path=implementation_plan,
            support_bundle_path=missing_runtime_status_manifest,
            px4_sitl_report_path=px4_receiver_report,
            field_evidence_report_path=field_report,
            field_collection_plan_path=incomplete_field_collection_plan,
            feature_method_benchmark_report_path=feature_report,
            threshold_tuning_report_path=threshold_report,
            evidence_workflow_report_path=workflow_report,
            evidence_workflow_validation_report_path=workflow_validation_ready_report,
            evidence_workflow_log_archive_path=workflow_log_archive,
        )
        field_runtime_status_actions = [
            action
            for action in missing_runtime_status_with_next_field["next_actions"]
            if action.get("check") == "support_bundle_bench_readiness.runtime_status"
        ]
        assert_equal(len(field_runtime_status_actions), 1, "autonomy readiness field runtime-status next action")
        assert_equal(
            field_runtime_status_actions[0]["command"],
            "./scripts/pi/run_terrain_nav_loop.sh --condition blur && ./scripts/pi/read_runtime_status.sh",
            "autonomy readiness runtime-status action should capture then read status",
        )
        assert_equal(
            field_runtime_status_actions[0]["field_condition"],
            "blur",
            "autonomy readiness runtime-status action should name next field condition",
        )
        field_support_check = next(
            check
            for check in missing_runtime_status_with_next_field["checks"]
            if check.get("name") == "support_bundle_bench_readiness"
        )
        field_bench_actions = field_support_check["details"]["bench_evidence_actions"]
        if not any(
            action.get("command") == "./scripts/pi/run_terrain_nav_loop.sh --condition blur && ./scripts/pi/read_runtime_status.sh"
            and action.get("field_condition") == "blur"
            for action in field_bench_actions
            if isinstance(action, dict)
        ):
            raise AssertionError("autonomy readiness bench preview should use next field capture command")
        field_support_blockers = [
            blocker
            for blocker in missing_runtime_status_with_next_field["evidence_manifest"]["external_blockers"]
            if blocker.get("name") == "support_bundle_bench_readiness"
        ]
        assert_equal(len(field_support_blockers), 1, "autonomy readiness field support blocker")
        if not any(
            action.get("command") == "./scripts/pi/run_terrain_nav_loop.sh --condition blur && ./scripts/pi/read_runtime_status.sh"
            and action.get("field_condition") == "blur"
            for action in field_support_blockers[0].get("bench_evidence_actions", [])
            if isinstance(action, dict)
        ):
            raise AssertionError("autonomy readiness support blocker should preserve field capture hint")
        generic_support_actions = [
            action
            for action in missing_runtime_status_ready["next_actions"]
            if action.get("check") == "support_bundle_bench_readiness"
        ]
        assert_equal(len(generic_support_actions), 1, "autonomy readiness support bench next action")
        assert_equal(
            generic_support_actions[0]["bench_subchecks"][0]["name"],
            "runtime_status",
            "autonomy readiness support subcheck details",
        )
        runtime_blockers = [
            blocker
            for blocker in missing_runtime_status_ready["evidence_manifest"]["external_blockers"]
            if blocker.get("name") == "support_bundle_bench_readiness"
        ]
        assert_equal(len(runtime_blockers), 1, "autonomy readiness support evidence blocker")
        assert_equal(
            runtime_blockers[0]["bench_subchecks"][0]["name"],
            "runtime_status",
            "autonomy readiness support evidence subcheck",
        )
        if "runtime terrain log and runtime_status.json snapshot" not in runtime_blockers[0].get("expected_bench_inputs", []):
            raise AssertionError("stale support bundle blocker should preserve strict expected bench inputs")
        if "ROS replay export validation report" not in runtime_blockers[0].get("expected_bench_inputs", []):
            raise AssertionError("stale support bundle blocker should preserve final ROS evidence input")
        if not any(
            action.get("command") == "VISION_NAV_COUNT=30 ./scripts/pi/run_terrain_nav_loop.sh"
            for action in runtime_blockers[0].get("bench_evidence_actions", [])
            if isinstance(action, dict)
        ):
            raise AssertionError("stale support bundle blocker should preserve bench refresh action hints")
        if not any(
            action.get("command") == "./scripts/pi/run_rosbag_export_validation.sh"
            for action in runtime_blockers[0].get("bench_evidence_actions", [])
            if isinstance(action, dict)
        ):
            raise AssertionError("stale support bundle blocker should preserve ROS validation refresh hint")
        assert_equal(
            runtime_blockers[0].get("support_bundle_command"),
            "./scripts/pi/create_support_bundle.sh",
            "stale support bundle blocker refresh command",
        )

        incomplete_gnss_manifest = root / "support_manifest_incomplete_gnss.json"
        incomplete_gnss_data = json.loads(direct_report_support_manifest.read_text())
        incomplete_gnss_data["bundle"]["mission_plan"]["gnss_denied"]["status"] = "incomplete"
        incomplete_gnss_data["bundle"]["mission_plan"]["gnss_denied"]["checks"][1]["status"] = "failed"
        incomplete_gnss_manifest.write_text(json.dumps(incomplete_gnss_data))
        incomplete_gnss_ready = evaluate_autonomy_readiness(
            research_doc_path=research_doc,
            implementation_plan_path=implementation_plan,
            support_bundle_path=incomplete_gnss_manifest,
            px4_sitl_report_path=px4_receiver_report,
            field_evidence_report_path=field_report,
            field_collection_plan_path=field_collection_plan,
            feature_method_benchmark_report_path=feature_report,
            threshold_tuning_report_path=threshold_report,
        )
        assert_equal(incomplete_gnss_ready["status"], "failed", "autonomy readiness incomplete gnss denied plan")
        gnss_actions = [
            action
            for action in incomplete_gnss_ready["next_actions"]
            if action.get("check") == "support_bundle_bench_readiness.gnss_denied_plan"
        ]
        assert_equal(len(gnss_actions), 1, "autonomy readiness gnss denied plan next action")
        assert_equal(
            gnss_actions[0]["desktop_action"],
            "Mission Planner > GNSS-Denied Prep, then Build/Upload Bundle and Bench Report",
            "autonomy readiness gnss denied desktop action",
        )
        gnss_blockers = [
            blocker
            for blocker in incomplete_gnss_ready["evidence_manifest"]["external_blockers"]
            if blocker.get("name") == "support_bundle_bench_readiness"
        ]
        assert_equal(len(gnss_blockers), 1, "autonomy readiness gnss support evidence blocker")
        assert_equal(
            gnss_blockers[0]["bench_subchecks"][0]["name"],
            "gnss_denied_plan",
            "autonomy readiness gnss support evidence subcheck",
        )

        bundled_threshold_manifest = root / "support_manifest_with_threshold.json"
        bundled_threshold_data = json.loads(support_manifest.read_text())
        bundled_threshold_data["threshold_tuning"] = {
            "status": "passed",
            "report_count": 1,
            "field_case_count": 8,
            "capture_metadata_issue_count": 0,
            "covered_conditions": REQUIRED_FIELD_CONDITIONS,
            "reports": [
                {
                    "status": "passed",
                    "covered_conditions": REQUIRED_FIELD_CONDITIONS,
                }
            ],
        }
        bundled_threshold_manifest.write_text(json.dumps(bundled_threshold_data))
        bundled_threshold_ready = evaluate_autonomy_readiness(
            research_doc_path=research_doc,
            implementation_plan_path=implementation_plan,
            support_bundle_path=bundled_threshold_manifest,
            field_evidence_report_path=field_report,
            field_collection_plan_path=field_collection_plan,
            evidence_workflow_report_path=workflow_report,
            evidence_workflow_validation_report_path=workflow_validation_ready_report,
            evidence_workflow_log_archive_path=workflow_log_archive,
        )
        assert_equal(bundled_threshold_ready["status"], "passed", "autonomy readiness bundled threshold status")

        missing_threshold = evaluate_autonomy_readiness(
            research_doc_path=research_doc,
            implementation_plan_path=implementation_plan,
            support_bundle_path=support_manifest,
            px4_sitl_prereq_path=px4_prereq_report,
            field_evidence_report_path=field_report,
            field_collection_plan_path=field_collection_plan,
            evidence_workflow_report_path=workflow_report,
            evidence_workflow_validation_report_path=workflow_validation_report,
            evidence_workflow_log_archive_path=workflow_log_archive,
        )
        assert_equal(missing_threshold["status"], "failed", "autonomy readiness missing threshold report")
        missing_checks = {check["name"]: check["status"] for check in missing_threshold["checks"]}
        assert_equal(missing_checks["threshold_tuning"], "failed", "autonomy readiness threshold fail closed")
        threshold_actions = [
            action for action in missing_threshold["next_actions"] if action.get("check") == "threshold_tuning"
        ]
        assert_equal(len(threshold_actions), 1, "autonomy readiness threshold next action")
        assert_equal(
            threshold_actions[0]["missing_conditions"],
            REQUIRED_FIELD_CONDITIONS,
            "autonomy readiness threshold missing conditions",
        )
        threshold_blockers = [
            blocker
            for blocker in missing_threshold["evidence_manifest"]["external_blockers"]
            if blocker.get("name") == "threshold_tuning"
        ]
        assert_equal(len(threshold_blockers), 1, "autonomy readiness threshold evidence blocker")
        assert_equal(
            threshold_blockers[0]["missing_conditions"],
            REQUIRED_FIELD_CONDITIONS,
            "autonomy readiness threshold evidence missing conditions",
        )
        missing_threshold_phases = {phase["id"]: phase for phase in missing_threshold["proof_runbook"]["phases"]}
        assert_equal(
            missing_threshold_phases["method_thresholds"]["status"],
            "action_required",
            "autonomy readiness threshold runbook method phase action required",
        )
        if "./scripts/pi/run_threshold_tuning_report.sh" not in missing_threshold_phases["method_thresholds"]["commands"]:
            raise AssertionError("autonomy readiness proof runbook missing threshold command")
        command_bundle = missing_threshold["command_bundle"]
        if "./scripts/pi/run_autonomy_evidence_workflow.sh" not in command_bundle["guided_workflow_commands"]:
            raise AssertionError("autonomy readiness JSON missing guided workflow command bundle")
        if "./scripts/pi/run_threshold_tuning_report.sh" not in command_bundle["next_action_commands"]:
            raise AssertionError("autonomy readiness JSON missing next-action command bundle")
        if "./scripts/pi/run_threshold_tuning_report.sh" not in command_bundle["immediate_next_action_commands"]:
            raise AssertionError("autonomy readiness JSON missing immediate threshold command bundle")
        if "./scripts/pi/run_threshold_tuning_report.sh" in command_bundle["blocked_follow_up_commands"]:
            raise AssertionError("autonomy readiness should not mark threshold command blocked once field proof exists")
        handoff = render_handoff_markdown(missing_threshold)
        if "Guided workflow command:" not in handoff:
            raise AssertionError("autonomy handoff missing guided workflow command section")
        if "Immediate next-action commands:" not in handoff:
            raise AssertionError("autonomy handoff missing immediate command section")
        if "./scripts/pi/run_autonomy_evidence_workflow.sh" not in handoff:
            raise AssertionError("autonomy handoff missing guided workflow command")
        if "# app: Module Setup > Evidence Workflow" not in handoff:
            raise AssertionError("autonomy handoff missing guided workflow app hint")
        if "Goal completion: waiting on proof" not in handoff:
            raise AssertionError("autonomy handoff waiting state")
        if "Proof items:" not in handoff:
            raise AssertionError("autonomy handoff proof item summary")
        if "## Audit Metadata" not in handoff:
            raise AssertionError("autonomy handoff audit metadata section")
        if "vision_nav_autonomy_readiness_audit_metadata_v1" not in handoff:
            raise AssertionError("autonomy handoff audit metadata schema")
        if "## Goal Proof Items" not in handoff:
            raise AssertionError("autonomy handoff proof item section")
        if "## Completion Blockers" not in handoff:
            raise AssertionError("autonomy handoff completion blocker section")
        if "threshold_tuning" not in handoff:
            raise AssertionError("autonomy handoff threshold blocker")
        if "Module Setup > Threshold Tuning" not in handoff:
            raise AssertionError("autonomy handoff next action")
        if "# app: Module Setup > Threshold Tuning" not in handoff:
            raise AssertionError("autonomy handoff command bundle missing app hint")
        if "## Field Evidence Collection Checklist" not in handoff:
            raise AssertionError("autonomy handoff field checklist")
        if "- [ ] Good texture (`good_texture`)" not in handoff:
            raise AssertionError("autonomy handoff missing condition checklist")
        if "## Artifact Availability" not in handoff:
            raise AssertionError("autonomy handoff artifact availability")
        if "## PX4 Capture Prerequisites" not in handoff:
            raise AssertionError("autonomy handoff missing PX4 prereq diagnostics")
        if "px4_autopilot_dir" not in handoff:
            raise AssertionError("autonomy handoff missing PX4 prereq failed check")
        if "## Field Collection Plan" not in handoff:
            raise AssertionError("autonomy handoff field collection plan")
        if "## Plan Source Snapshot" not in handoff:
            raise AssertionError("autonomy handoff plan snapshot")
        if "implementation_plan" not in handoff:
            raise AssertionError("autonomy handoff missing implementation plan snapshot")
        if "SHA256" not in handoff:
            raise AssertionError("autonomy handoff missing plan source hash column")
        if "- Registered: 8/8" not in handoff:
            raise AssertionError("autonomy handoff field collection plan summary")
        if "field_collection_plan.json" not in handoff:
            raise AssertionError("autonomy handoff field collection plan path")
        if "## Command Bundle" not in handoff:
            raise AssertionError("autonomy handoff command bundle")
        if "## Proof Runbook" not in handoff:
            raise AssertionError("autonomy handoff proof runbook")
        if "Benchmark methods and tune replay thresholds" not in handoff:
            raise AssertionError("autonomy handoff proof runbook phase")
        if "./scripts/pi/run_threshold_tuning_report.sh" not in handoff:
            raise AssertionError("autonomy handoff next-action command bundle")
        missing_threshold_report = root / "autonomy_readiness_missing_threshold.json"
        missing_threshold_handoff = root / "autonomy_readiness_missing_threshold.md"
        missing_threshold_report.write_text(json.dumps(missing_threshold))
        missing_threshold_handoff.write_text(handoff)
        package_result = create_evidence_package(
            missing_threshold_report,
            handoff_path=missing_threshold_handoff,
            output_path=root / "autonomy_evidence_package.zip",
        )
        package_path = Path(package_result["zip_path"])
        if not package_path.exists():
            raise AssertionError("autonomy evidence package zip was not written")
        with zipfile.ZipFile(package_path) as archive:
            names = set(archive.namelist())
            for expected in {
                "manifest.json",
                "reports/autonomy_readiness_report.json",
                "reports/autonomy_readiness_report.md",
            }:
                if expected not in names:
                    raise AssertionError(f"autonomy evidence package missing {expected}")
            package_manifest = json.loads(archive.read("manifest.json"))
            if not package_manifest.get("proof_runbook_summary"):
                raise AssertionError("autonomy evidence package missing proof runbook summary")
            if not package_manifest.get("diagnostic_summary"):
                raise AssertionError("autonomy evidence package missing diagnostic summary")
            if (
                package_manifest["diagnostic_summary"]["px4_sitl_prereqs"]["failed_checks"][0]["name"]
                != "px4_autopilot_dir"
            ):
                raise AssertionError("autonomy evidence package missing PX4 prereq diagnostic detail")
            if (
                package_manifest["diagnostic_summary"]["px4_sitl_prereqs"]["fix_commands"][0]["condition"]
                != "px4_autopilot_dir"
            ):
                raise AssertionError("autonomy evidence package missing PX4 prereq fix command")
            if package_manifest["proof_runbook_summary"]["summary"]["action_required"] < 1:
                raise AssertionError("autonomy evidence package proof runbook summary should show pending action")
            package_command_bundle = package_manifest.get("command_bundle")
            if not isinstance(package_command_bundle, dict):
                raise AssertionError("autonomy evidence package missing command bundle")
            if "./scripts/pi/run_autonomy_evidence_workflow.sh" not in package_command_bundle["guided_workflow_commands"]:
                raise AssertionError("autonomy evidence package missing guided workflow command")
            if (
                "export VISION_NAV_PX4_AUTOPILOT_DIR=/path/to/PX4-Autopilot"
                not in package_command_bundle["prerequisite_fix_commands"]
            ):
                raise AssertionError("autonomy evidence package missing prereq fix command group")
            if "./scripts/pi/run_threshold_tuning_report.sh" not in package_command_bundle["immediate_next_action_commands"]:
                raise AssertionError("autonomy evidence package missing immediate threshold command")
            if "./scripts/pi/run_threshold_tuning_report.sh" in package_command_bundle["blocked_follow_up_commands"]:
                raise AssertionError("autonomy evidence package should not block immediate threshold command")
            package_command_items = package_command_bundle.get("command_items")
            if not isinstance(package_command_items, list):
                raise AssertionError("autonomy evidence package missing structured command items")
            if not any(
                item.get("group") == "immediate_next_action"
                and item.get("command") == "./scripts/pi/run_threshold_tuning_report.sh"
                and item.get("desktop_action") == "Module Setup > Threshold Tuning"
                for item in package_command_items
                if isinstance(item, dict)
            ):
                raise AssertionError("autonomy evidence package missing structured app-routed command item")
            workflow_validation_summary = package_manifest.get("workflow_validation_summary")
            if not isinstance(workflow_validation_summary, dict):
                raise AssertionError("autonomy evidence package missing workflow validation summary")
            assert_equal(
                workflow_validation_summary["status"],
                "degraded",
                "autonomy evidence package workflow validation status",
            )
            assert_equal(
                workflow_validation_summary["workflow_status"],
                "failed",
                "autonomy evidence package workflow validation workflow status",
            )
            assert_equal(
                workflow_validation_summary["issue_count"],
                1,
                "autonomy evidence package workflow validation issue count",
            )
            assert_equal(
                workflow_validation_summary["next_required_step"]["command"],
                "./scripts/pi/register_field_replay_case.sh",
                "autonomy evidence package workflow validation next command",
            )
            assert_equal(
                workflow_validation_summary["next_required_step"]["capture_command_after_bundle"],
                "VISION_NAV_COUNT=30 ./scripts/pi/run_terrain_nav_loop.sh && ./scripts/pi/read_runtime_status.sh",
                "autonomy evidence package workflow validation capture-after-bundle command",
            )
            assert_equal(
                workflow_validation_summary["next_required_step"]["bundle_path"],
                str(root / "mission_bundle"),
                "autonomy evidence package workflow validation bundle path",
            )
            assert_equal(
                workflow_validation_summary["next_required_step"]["runtime_status_path"],
                str(root / "field-captures/good_texture/runtime_status.json"),
                "autonomy evidence package workflow validation runtime status path",
            )
            required_step_check = next(
                item
                for item in workflow_validation_summary["checks"]
                if item.get("name") == "required_step_results"
            )
            assert_equal(
                required_step_check["non_passed_count"],
                2,
                "autonomy evidence package workflow validation non-passed count",
            )
            assert_equal(
                required_step_check["non_passed_steps"][0]["name"],
                "register_field_replay_case",
                "autonomy evidence package workflow validation non-passed step",
            )
            assert_equal(
                required_step_check["non_passed_steps"][0]["status"],
                "skipped",
                "autonomy evidence package workflow validation non-passed status",
            )
            assert_equal(
                required_step_check["non_passed_steps"][0]["exit_code"],
                0,
                "autonomy evidence package workflow validation non-passed exit code",
            )
            manifest = json.loads(archive.read("manifest.json"))
            assert_equal(manifest["readiness_status"], "failed", "autonomy evidence package status")
            assert_equal(
                manifest["readiness_report_metadata"]["schema_version"],
                "vision_nav_autonomy_readiness_audit_metadata_v1",
                "autonomy evidence package report metadata schema",
            )
            assert_equal(
                manifest["plan_snapshot"]["schema_version"],
                "vision_nav_autonomy_plan_snapshot_v1",
                "autonomy evidence package plan snapshot schema",
            )
            assert_equal(
                len(manifest["plan_snapshot"]["research_doc"]["source_sha256"]),
                64,
                "autonomy evidence package research source hash",
            )
            assert_equal(
                len(manifest["plan_snapshot"]["implementation_plan"]["source_sha256"]),
                64,
                "autonomy evidence package implementation source hash",
            )
            proof_summary = manifest["proof_summary"]
            expected_proof_items = missing_threshold["evidence_manifest"]["proof_items"]
            expected_completion_blockers = missing_threshold["evidence_manifest"]["completion_blockers"]
            expected_external_blockers = missing_threshold["evidence_manifest"]["external_blockers"]
            assert_equal(
                proof_summary["proof_item_count"],
                len(expected_proof_items),
                "autonomy evidence package proof item count",
            )
            assert_equal(
                proof_summary["proof_item_passed_count"],
                len([item for item in expected_proof_items if item.get("status") == "passed"]),
                "autonomy evidence package proof item passed count",
            )
            assert_equal(
                proof_summary["completion_blocker_count"],
                len(expected_completion_blockers),
                "autonomy evidence package completion blocker count",
            )
            assert_equal(
                proof_summary["external_blocker_count"],
                len(expected_external_blockers),
                "autonomy evidence package external blocker count",
            )
            if not any(item.get("name") == "threshold_tuning" for item in proof_summary["proof_items"]):
                raise AssertionError("autonomy evidence package proof summary missing threshold item")
            missing_proof_entries = [
                item for item in manifest["missing"] if item.get("reason") == "proof_gate_not_passed"
            ]
            if not any(item.get("label") == "proof:threshold_tuning" for item in missing_proof_entries):
                raise AssertionError("autonomy evidence package missing proof-gate placeholder")
            threshold_missing = next(
                item for item in missing_proof_entries if item.get("label") == "proof:threshold_tuning"
            )
            assert_equal(
                threshold_missing["missing_conditions"],
                REQUIRED_FIELD_CONDITIONS,
                "autonomy evidence package proof-gate missing conditions",
            )
            missing_lines = missing_artifact_lines(manifest, limit=20)
            threshold_lines = [line for line in missing_lines if "proof:threshold_tuning" in line]
            if not threshold_lines:
                raise AssertionError("autonomy evidence package missing CLI proof-gate summary")
            if "missing=good_texture, low_texture, blur +5" not in threshold_lines[0]:
                raise AssertionError("autonomy evidence package CLI summary missing condition detail")
            if not any(item["label"] == "input:support_bundle" for item in manifest["included"]):
                raise AssertionError("autonomy evidence package did not include support manifest artifact")
            if not any(item["label"] == "input:field_collection_plan" for item in manifest["included"]):
                raise AssertionError("autonomy evidence package did not include field collection plan artifact")
            if not any(item["label"] == "input:field_collection_plan_markdown" for item in manifest["included"]):
                raise AssertionError("autonomy evidence package did not include field collection checklist artifact")
            if not any(item["label"] == "input:evidence_workflow_report" for item in manifest["included"]):
                raise AssertionError("autonomy evidence package did not include workflow report artifact")
            if not any(item["label"] == "input:evidence_workflow_validation_report" for item in manifest["included"]):
                raise AssertionError("autonomy evidence package did not include workflow validation artifact")
            if not any(item["label"] == "input:evidence_workflow_log_archive" for item in manifest["included"]):
                raise AssertionError("autonomy evidence package did not include workflow log archive artifact")
            if not any(item["label"] == "input:px4_sitl_prereqs" for item in manifest["included"]):
                raise AssertionError("autonomy evidence package did not include PX4 prereq report artifact")

        downloaded_report_data = json.loads(json.dumps(missing_threshold))
        downloaded_report_data["inputs"]["field_collection_plan"] = (
            "/home/user/DroneTransfer/outgoing/replay-cases/field_collection_plan.json"
        )
        downloaded_report_data["inputs"]["field_collection_plan_markdown"] = (
            "/home/user/DroneTransfer/outgoing/replay-cases/field_collection_plan.md"
        )
        downloaded_report = root / "downloaded_autonomy_readiness_report.json"
        downloaded_handoff = root / "downloaded_autonomy_readiness_report.md"
        downloaded_report.write_text(json.dumps(downloaded_report_data))
        downloaded_handoff_text = render_handoff_markdown(
            downloaded_report_data,
            report_path=downloaded_report,
        )
        downloaded_handoff.write_text(downloaded_handoff_text)
        if "## Field Collection Plan" not in downloaded_handoff_text:
            raise AssertionError("downloaded autonomy handoff missing sibling field collection plan")
        downloaded_package_result = create_evidence_package(
            downloaded_report,
            handoff_path=downloaded_handoff,
            output_path=root / "downloaded_autonomy_evidence_package.zip",
        )
        with zipfile.ZipFile(Path(downloaded_package_result["zip_path"])) as archive:
            manifest = json.loads(archive.read("manifest.json"))
            if not any(item["label"] == "input:field_collection_plan" for item in manifest["included"]):
                raise AssertionError("downloaded evidence package did not include sibling field collection plan")
            if not any(item["label"] == "input:field_collection_plan_markdown" for item in manifest["included"]):
                raise AssertionError("downloaded evidence package did not include sibling field collection checklist")


def test_replay_gates_pass_good_map_and_fail_wrong_map_acceptance() -> None:
    good_report = evaluate_replay_records(
        [
            {
                "result": {
                    "status": "accepted",
                    "confidence": 0.82,
                    "inliers": 32,
                    "reprojection_error_px": 1.7,
                    "scale_confidence": 0.8,
                    "covariance": {"x_m2": 4.0, "y_m2": 4.0, "z_m2": None, "yaw_rad2": None},
                }
            },
            {"result": {"status": "rejected", "reason": "low_inliers"}},
        ],
        case_name="unit-good",
        expected="good_map",
    )
    assert_equal(good_report["status"], "passed", "good-map replay gate status")

    wrong_map_report = evaluate_replay_records(
        [
            {"result": {"status": "rejected", "reason": "no_candidate_tile_accepted"}},
            {"result": {"status": "rejected", "reason": "no_candidate_tile_accepted"}},
        ],
        case_name="unit-wrong-map",
        expected="wrong_map",
    )
    assert_equal(wrong_map_report["status"], "passed", "wrong-map rejected gate status")

    bad_wrong_map_report = evaluate_replay_records(
        [
            {
                "result": {
                    "status": "accepted",
                    "confidence": 0.9,
                    "inliers": 40,
                    "reprojection_error_px": 1.0,
                    "scale_confidence": 0.9,
                    "covariance": {"x_m2": 1.0, "y_m2": 1.0, "z_m2": None, "yaw_rad2": None},
                }
            }
        ],
        case_name="unit-wrong-map-bad",
        expected="wrong_map",
    )
    assert_equal(bad_wrong_map_report["status"], "failed", "wrong-map accepted gate status")


def test_replay_gates_fail_missing_metrics_motion_jumps_and_weak_covariance() -> None:
    missing_metric_report = evaluate_replay_records(
        [
            {
                "result": {
                    "status": "accepted",
                    "confidence": 0.9,
                    "inliers": 40,
                }
            }
        ],
        case_name="unit-good-missing-metrics",
        expected="good_map",
    )
    assert_equal(missing_metric_report["status"], "failed", "good-map missing metrics gate status")
    missing_messages = " ".join(issue["message"] for issue in missing_metric_report["issues"])
    if "missing reprojection error" not in missing_messages or "missing XY covariance" not in missing_messages:
        raise AssertionError(f"Expected missing metric issues, got {missing_messages}")

    motion_jump_report = evaluate_replay_records(
        [
            {
                "timestamp_us": 1_000_000,
                "result": {
                    "status": "accepted",
                    "confidence": 0.82,
                    "inliers": 32,
                    "reprojection_error_px": 1.7,
                    "scale_confidence": 0.8,
                    "local_enu_m": {"x": 0.0, "y": 0.0, "z": None},
                    "covariance": {"x_m2": 4.0, "y_m2": 4.0, "z_m2": None, "yaw_rad2": None},
                },
            },
            {
                "timestamp_us": 2_000_000,
                "result": {
                    "status": "accepted",
                    "confidence": 0.84,
                    "inliers": 34,
                    "reprojection_error_px": 1.6,
                    "scale_confidence": 0.8,
                    "local_enu_m": {"x": 300.0, "y": 0.0, "z": None},
                    "covariance": {"x_m2": 4.0, "y_m2": 4.0, "z_m2": None, "yaw_rad2": None},
                },
            },
        ],
        case_name="unit-good-motion-jump",
        expected="good_map",
    )
    assert_equal(motion_jump_report["status"], "failed", "good-map motion jump gate status")
    assert_equal(motion_jump_report["metrics"]["motion_jump_max_m"], 300.0, "motion jump metric")

    weak_degraded_report = evaluate_replay_records(
        [
            {
                "result": {
                    "status": "accepted",
                    "confidence": 0.4,
                    "inliers": 22,
                    "reprojection_error_px": 4.0,
                    "scale_confidence": 0.2,
                    "covariance": {"x_m2": 4.0, "y_m2": 4.0, "z_m2": None, "yaw_rad2": None},
                }
            },
            {"result": {"status": "rejected", "reason": "blurred"}},
            {"result": {"status": "rejected", "reason": "blurred"}},
        ],
        case_name="unit-degraded-weak-covariance",
        expected="degraded",
    )
    assert_equal(weak_degraded_report["status"], "failed", "degraded weak covariance gate status")
    weak_messages = " ".join(issue["message"] for issue in weak_degraded_report["issues"])
    if "not inflated" not in weak_messages:
        raise AssertionError(f"Expected covariance inflation issue, got {weak_messages}")


def test_synthetic_replay_case_manifest_passes_all_cases() -> None:
    root = Path(__file__).resolve().parents[1]
    manifest = root / "data" / "replay_cases" / "synthetic_smoke" / "manifest.json"
    with tempfile.TemporaryDirectory() as tmp:
        summary = evaluate_replay_case_manifest(manifest, output_dir=Path(tmp))
        assert_equal(summary["status"], "passed", "synthetic replay manifest status")
        assert_equal(summary["schema"]["status"], "passed", "synthetic replay manifest schema")
        assert_equal(summary["case_count"], 3, "synthetic replay manifest case count")
        statuses = {report["case_name"]: report["status"] for report in summary["reports"]}
        assert_equal(
            statuses,
            {
                "synthetic-good-map": "passed",
                "synthetic-degraded-low-texture": "passed",
                "synthetic-wrong-map": "passed",
            },
            "synthetic replay case statuses",
        )
        if not Path(summary["summary_path"]).exists():
            raise AssertionError("Expected synthetic replay manifest summary to be written")


def test_replay_case_manifest_schema_flags_malformed_cases() -> None:
    root = Path(__file__).resolve().parents[1]
    schema_file = root / "data" / "replay_cases" / "replay_case_manifest.schema.json"
    assert_equal(json.loads(schema_file.read_text()), REPLAY_CASE_MANIFEST_SCHEMA, "checked-in replay schema")

    malformed = {
        "version": "0.1.0",
        "description": 123,
        "cases": [
            {
                "case_name": "field-good-texture",
                "expected": "good_map",
                "dataset_type": "field",
                "conditions": ["good_texture"],
                "log": "missing.jsonl",
            },
            {
                "case_name": "field-good-texture",
                "expected": "gps_like",
                "dataset_type": "lab",
                "conditions": [],
                "condition_tags": "good_texture",
                "tags": [""],
                "bundle": 123,
                "log": "",
                "notes": [],
                "registered_at": {},
            },
        ],
    }
    schema = evaluate_replay_case_schema(malformed)
    assert_equal(schema["status"], "failed", "malformed replay schema status")
    messages = " ".join(issue["message"] for issue in schema["issues"])
    for expected in [
        "Duplicate case_name",
        "expected must be one of",
        "dataset_type must be one of",
        "conditions must be",
        "description must be",
        "condition_tags must be an array",
        "entries must be non-empty strings",
        "bundle must be a string",
        "notes must be a string",
        "registered_at must be a string",
    ]:
        if expected not in messages:
            raise AssertionError(f"Expected schema issue containing {expected!r}, got {messages}")

    with tempfile.TemporaryDirectory() as tmp:
        manifest = Path(tmp) / "malformed_manifest.json"
        manifest.write_text(json.dumps(malformed))
        summary = evaluate_replay_case_manifest(manifest)
        assert_equal(summary["status"], "failed", "malformed replay manifest status")
        assert_equal(summary["schema"]["status"], "failed", "malformed manifest schema status")
        coverage = audit_replay_dataset_coverage(manifest, require_log_exists=False)
        assert_equal(coverage["status"], "failed", "malformed replay coverage audit status")
        assert_equal(coverage["schema"]["status"], "failed", "malformed replay coverage schema")

        non_object = Path(tmp) / "non_object_manifest.json"
        non_object.write_text("[]")
        non_object_summary = evaluate_replay_case_manifest(non_object, schema_only=True)
        assert_equal(non_object_summary["status"], "failed", "non-object replay manifest status")
        assert_equal(non_object_summary["case_count"], 0, "non-object replay manifest case count")
        non_object_coverage = audit_replay_dataset_coverage(non_object, require_log_exists=False)
        assert_equal(non_object_coverage["status"], "failed", "non-object replay coverage status")
        non_object_support = load_replay_cases(replay_case_manifest=str(non_object))
        assert_equal(non_object_support["schema"]["status"], "failed", "non-object support replay schema")
        assert_equal(non_object_support["cases"], [], "non-object support replay cases")


def test_replay_case_manifest_schema_only_skips_log_evaluation() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        manifest = Path(tmp) / "field_manifest.json"
        manifest.write_text(
            json.dumps(
                {
                    "version": "0.1.0",
                    "cases": [
                        {
                            "case_name": "field-good-texture",
                            "expected": "good_map",
                            "dataset_type": "field",
                            "conditions": ["good_texture"],
                            "bundle": "field-bundles/site-a/mission_bundle",
                            "log": "field/site-a/good_texture/terrain_matches.jsonl",
                            "notes": "shape is valid, log is not copied yet",
                        }
                    ],
                }
            )
        )
        schema_only = evaluate_replay_case_manifest(manifest, schema_only=True)
        assert_equal(schema_only["status"], "passed", "schema-only manifest status")
        assert_equal(schema_only["schema_only"], True, "schema-only flag")
        assert_equal(schema_only["schema"]["status"], "passed", "schema-only schema status")
        assert_equal(schema_only["reports"], [], "schema-only reports")

        full = evaluate_replay_case_manifest(manifest)
        assert_equal(full["status"], "failed", "full replay manifest missing log status")
        if "Replay case log is missing" not in json.dumps(full["reports"]):
            raise AssertionError(f"Expected missing log issue, got {full['reports']}")


def test_field_evidence_template_matches_required_conditions() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        output = Path(tmp) / "field_manifest.template.json"
        result = create_field_evidence_template(
            output_path=output,
            site_name="Site A",
            bundle="field-bundles/site-a/mission_bundle",
        )
        assert_equal(result["status"], "written", "field evidence template status")
        assert_equal(result["case_count"], len(REQUIRED_FIELD_CONDITIONS), "field evidence template case count")
        manifest = json.loads(output.read_text())
        cases = manifest["cases"]
        conditions = [case["conditions"][0] for case in cases]
        assert_equal(conditions, REQUIRED_FIELD_CONDITIONS, "field evidence template required conditions")
        expected_by_condition = {case["conditions"][0]: case["expected"] for case in cases}
        assert_equal(expected_by_condition["good_texture"], "good_map", "field template good texture expected")
        assert_equal(expected_by_condition["wrong_map"], "wrong_map", "field template wrong map expected")
        assert_equal(expected_by_condition["low_texture"], "degraded", "field template low texture expected")
        assert_equal({case["dataset_type"] for case in cases}, {"field"}, "field template dataset type")
        assert_equal({case["bundle"] for case in cases}, {"field-bundles/site-a/mission_bundle"}, "field template bundle")
        for case in cases:
            condition = case["conditions"][0]
            capture_metadata = case.get("capture_metadata") or {}
            assert_equal(
                capture_metadata.get("schema_version"),
                CAPTURE_METADATA_SCHEMA_VERSION,
                "field template capture metadata schema",
            )
            assert_equal(capture_metadata.get("site_name"), "Site A", "field template capture metadata site")
            assert_equal(capture_metadata.get("condition"), condition, "field template capture metadata condition")
            capture_checklist = case.get("capture_checklist") or {}
            assert_equal(
                capture_checklist.get("schema_version"),
                CAPTURE_CHECKLIST_SCHEMA_VERSION,
                "field template capture checklist schema",
            )
            assert_equal(capture_checklist.get("condition"), condition, "field template capture checklist condition")
            if not capture_checklist.get("items"):
                raise AssertionError("Expected field template capture checklist items")
        schema_only = evaluate_replay_case_manifest(output, schema_only=True)
        assert_equal(schema_only["status"], "passed", "field template schema-only status")
        full = evaluate_replay_case_manifest(output)
        assert_equal(full["status"], "failed", "field template full gate waits for logs")

        try:
            create_field_evidence_template(output_path=output)
        except FileExistsError:
            pass
        else:
            raise AssertionError("field evidence template should refuse to overwrite without force")

        seeded_template = Path(tmp) / "field_manifest_seeded.template.json"
        active_manifest = Path(tmp) / "field_manifest.json"
        seeded = create_field_evidence_template(
            output_path=seeded_template,
            site_name="Site A",
            bundle="field-bundles/site-a/mission_bundle",
            seed_manifest_path=active_manifest,
        )
        assert_equal(seeded["seed_manifest"]["written"], True, "field template active manifest seeded")
        assert_equal(seeded["seed_manifest"]["path"], str(active_manifest), "field template seed path")
        active = json.loads(active_manifest.read_text())
        assert_equal(len(active["cases"]), len(REQUIRED_FIELD_CONDITIONS), "seeded active manifest case count")
        schema_seeded = evaluate_replay_case_manifest(active_manifest, schema_only=True)
        assert_equal(schema_seeded["status"], "passed", "seeded active manifest schema-only status")

        active_manifest.write_text(json.dumps({"version": "0.1.0", "cases": []}))
        not_seeded = create_field_evidence_template(
            output_path=Path(tmp) / "field_manifest_second.template.json",
            seed_manifest_path=active_manifest,
        )
        assert_equal(not_seeded["seed_manifest"]["written"], False, "field template does not overwrite active manifest")
        assert_equal(not_seeded["seed_manifest"]["reason"], "already_exists", "field template seed skip reason")


def test_field_collection_plan_tracks_placeholders_and_registered_logs() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        active_manifest = base / "field_manifest.json"
        create_field_evidence_template(
            output_path=base / "field_manifest.template.json",
            site_name="Site A",
            bundle="field-bundles/site-a/mission_bundle",
            seed_manifest_path=active_manifest,
        )
        inferred_plan = create_field_collection_plan(
            manifest_path=active_manifest,
            output_path=base / "field_collection_plan_inferred.json",
            capture_root="$HOME/DroneTransfer/outgoing/field-captures",
        )
        assert_equal(
            inferred_plan["next_condition"]["capture_output_dir"],
            "$HOME/DroneTransfer/outgoing/field-captures/Site-A-good_texture",
            "field collection infers site name from manifest template",
        )
        assert_equal(
            inferred_plan["next_condition"]["bundle"],
            "field-bundles/site-a/mission_bundle",
            "field collection preserves case bundle from manifest",
        )
        plan = create_field_collection_plan(
            manifest_path=active_manifest,
            output_path=base / "field_collection_plan.json",
            markdown_output_path=base / "field_collection_plan.md",
            site_name="Site A",
            bundle="field-bundles/site-a/mission_bundle",
            source_log="$HOME/DroneTransfer/outgoing/terrain-match/terrain_matches.jsonl",
            capture_root="$HOME/DroneTransfer/outgoing/field-captures",
        )
        assert_equal(plan["schema_version"], "vision_nav_field_collection_plan_v1", "field collection plan schema")
        assert_equal(plan["status"], "degraded", "field collection plan waits for field logs")
        assert_equal(plan["summary"]["required_count"], len(REQUIRED_FIELD_CONDITIONS), "field collection required count")
        assert_equal(plan["summary"]["placeholder_count"], len(REQUIRED_FIELD_CONDITIONS), "field collection placeholder count")
        assert_equal(plan["summary"]["registered_count"], 0, "field collection registered count")
        assert_equal(
            plan["pending_capture_command_count"],
            len(REQUIRED_FIELD_CONDITIONS),
            "field collection pending capture command count",
        )
        assert_equal(
            plan["pending_preflight_command_count"],
            len(REQUIRED_FIELD_CONDITIONS),
            "field collection pending preflight command count",
        )
        assert_equal(
            plan["pending_metadata_update_command_count"],
            len(REQUIRED_FIELD_CONDITIONS),
            "field collection pending metadata update command count",
        )
        assert_equal(
            plan["next_condition"]["condition"],
            "good_texture",
            "field collection next condition starts with first required condition",
        )
        if "Site-A-good_texture" not in plan["next_condition"]["capture_command"]:
            raise AssertionError("Expected next condition to preserve capture command")
        if "preflight_field_capture.sh" not in plan["next_condition"]["preflight_command"]:
            raise AssertionError("Expected next condition to include field capture preflight command")
        assert_equal(
            plan["runtime_status_path_count"],
            len(REQUIRED_FIELD_CONDITIONS),
            "field collection runtime status path count",
        )
        good_texture = next(item for item in plan["conditions"] if item["condition"] == "good_texture")
        assert_equal(good_texture["status"], "placeholder", "field collection placeholder status")
        assert_equal(
            good_texture["capture_output_dir"],
            "$HOME/DroneTransfer/outgoing/field-captures/Site-A-good_texture",
            "field collection condition-specific capture output",
        )
        assert_equal(
            good_texture["source_log"],
            "$HOME/DroneTransfer/outgoing/field-captures/Site-A-good_texture/terrain_matches.jsonl",
            "field collection condition-specific source log",
        )
        assert_equal(
            good_texture["runtime_status_path"],
            "$HOME/DroneTransfer/outgoing/field-captures/Site-A-good_texture/runtime_status.json",
            "field collection condition-specific runtime status",
        )
        if "VISION_NAV_OUTPUT_DIR" not in good_texture["capture_command"]:
            raise AssertionError("Expected generated capture command to include output directory")
        if "VISION_NAV_FIELD_COLLECTION_PLAN" not in good_texture["preflight_command"]:
            raise AssertionError("Expected generated preflight command to include collection plan path")
        if "VISION_NAV_FIELD_CONDITION=good_texture" not in good_texture["preflight_command"]:
            raise AssertionError("Expected generated preflight command to target the condition")
        if "VISION_NAV_COUNT=30" not in good_texture["capture_command"]:
            raise AssertionError("Expected generated capture command to be bounded")
        if "read_runtime_status.sh" not in good_texture["capture_command"]:
            raise AssertionError("Expected generated capture command to read runtime status after capture")
        if "update_field_capture_metadata.sh" not in good_texture["metadata_update_command"]:
            raise AssertionError("Expected generated metadata update command")
        if "VISION_NAV_FIELD_CONDITION=good_texture" not in good_texture["metadata_update_command"]:
            raise AssertionError("Expected metadata update command to target the condition")
        if "VISION_NAV_FIELD_OPERATOR=TODO_operator" not in good_texture["metadata_update_command"]:
            raise AssertionError("Expected metadata update command to show the missing operator field")
        if "VISION_NAV_FIELD_ALTITUDE_AGL_M=TODO_altitude_agl_m" not in good_texture["metadata_update_command"]:
            raise AssertionError("Expected metadata update command to show the missing altitude field")
        if "VISION_NAV_FIELD_CAMERA_FOCUS_EXPOSURE_NOTES=TODO_camera_focus_exposure_notes" not in good_texture["metadata_update_command"]:
            raise AssertionError("Expected metadata update command to show camera focus/exposure notes")
        human_output = io.StringIO()
        with contextlib.redirect_stdout(human_output):
            print_field_collection_human(plan)
        human_text = human_output.getvalue()
        if "Next preflight command:" not in human_text:
            raise AssertionError("Expected human field collection output to include preflight command")
        if "Next metadata update command:" not in human_text:
            raise AssertionError("Expected human field collection output to include metadata update command")
        if "Next runtime status path:" not in human_text:
            raise AssertionError("Expected human field collection output to include runtime status path")
        if "update_field_capture_metadata.sh" not in human_text:
            raise AssertionError("Expected human field collection output to include the metadata helper")
        if human_text.index("Next capture command:") > human_text.index("Next metadata update command:"):
            raise AssertionError("Expected capture command before metadata update command")
        if human_text.index("Next capture command:") > human_text.index("Next runtime status path:"):
            raise AssertionError("Expected capture command before runtime status path")
        if human_text.index("Next runtime status path:") > human_text.index("Next metadata update command:"):
            raise AssertionError("Expected runtime status path before metadata update command")
        if human_text.index("Next metadata update command:") > human_text.index("Next register command:"):
            raise AssertionError("Expected metadata update command before register command")
        capture_metadata = good_texture.get("capture_metadata") or {}
        assert_equal(
            capture_metadata.get("schema_version"),
            CAPTURE_METADATA_SCHEMA_VERSION,
            "field collection placeholder capture metadata schema",
        )
        assert_equal(capture_metadata.get("site_name"), "Site A", "field collection placeholder capture metadata site")
        assert_equal(
            capture_metadata.get("condition"),
            "good_texture",
            "field collection placeholder capture metadata condition",
        )
        if "VISION_NAV_FIELD_CASE_NAME" not in good_texture["register_command"]:
            raise AssertionError("Expected generated registration command to include case name")
        if "VISION_NAV_FIELD_CAPTURE_METADATA" not in good_texture["register_command"]:
            raise AssertionError("Expected generated registration command to include capture metadata")
        if "field-captures/Site-A-good_texture/terrain_matches.jsonl" not in good_texture["register_command"]:
            raise AssertionError("Expected generated registration command to use the condition capture log")
        if not (base / "field_collection_plan.md").exists():
            raise AssertionError("Expected Markdown field collection plan")
        markdown_text = (base / "field_collection_plan.md").read_text()
        if "Update capture metadata:" not in markdown_text:
            raise AssertionError("Expected Markdown plan to include the next metadata update command")
        if "Preflight:" not in markdown_text:
            raise AssertionError("Expected Markdown plan to include the next preflight command")
        if "preflight_field_capture.sh" not in markdown_text:
            raise AssertionError("Expected Markdown plan to include the preflight helper")
        if "Runtime status:" not in markdown_text:
            raise AssertionError("Expected Markdown plan to include the next runtime status path")
        if "read_runtime_status.sh" not in markdown_text:
            raise AssertionError("Expected Markdown plan capture command to collect runtime status")
        if "update_field_capture_metadata.sh" not in markdown_text:
            raise AssertionError("Expected Markdown plan to include the metadata helper")
        selection = select_next_field_condition(base / "field_collection_plan.json")
        assert_equal(selection["status"], "selected", "field workflow selection status")
        assert_equal(selection["condition"], "good_texture", "field workflow selection condition")
        assert_equal(
            selection["environment"]["VISION_NAV_FIELD_CAPTURE_OUTPUT_DIR"],
            "$HOME/DroneTransfer/outgoing/field-captures/Site-A-good_texture",
            "field workflow selection capture output env",
        )
        assert_equal(
            selection["environment"]["VISION_NAV_FIELD_LOG"],
            "$HOME/DroneTransfer/outgoing/field-captures/Site-A-good_texture/terrain_matches.jsonl",
            "field workflow selection log env",
        )
        assert_equal(
            selection["capture_metadata_status"],
            "failed",
            "field workflow selection detects incomplete placeholder metadata",
        )
        if "update_field_capture_metadata.sh" not in selection["metadata_update_command"]:
            raise AssertionError("Expected selected field condition to include metadata update command")
        if "preflight_field_capture.sh" not in selection["preflight_command"]:
            raise AssertionError("Expected selected field condition to include preflight command")
        if "VISION_NAV_FIELD_CONDITION=good_texture" not in selection["metadata_update_command"]:
            raise AssertionError("Expected metadata update command to target selected condition")
        if "VISION_NAV_FIELD_OPERATOR=TODO_operator" not in selection["metadata_update_command"]:
            raise AssertionError("Expected selected metadata update command to preserve required field prompts")
        selection_shell = shell_assignments(selection)
        if "VISION_NAV_FIELD_AUTO_SELECTED=1" not in selection_shell:
            raise AssertionError("Expected shell assignments to mark the selected field condition")
        if "VISION_NAV_FIELD_METADATA_UPDATE_COMMAND" not in selection_shell:
            raise AssertionError("Expected shell assignments to export metadata update command")
        if "VISION_NAV_FIELD_PREFLIGHT_COMMAND" not in selection_shell:
            raise AssertionError("Expected shell assignments to export preflight command")

        ready_bundle = create_minimal_terrain_bundle(base)
        ready_capture_root = base / "field-captures"
        ready_capture_root.mkdir()
        ready_manifest = base / "field_manifest_ready.json"
        create_field_evidence_template(
            output_path=base / "field_manifest_ready.template.json",
            site_name="Site A",
            bundle=str(ready_bundle),
            seed_manifest_path=ready_manifest,
        )
        ready_plan = create_field_collection_plan(
            manifest_path=ready_manifest,
            output_path=base / "field_collection_plan_ready_capture.json",
            site_name="Site A",
            bundle=str(ready_bundle),
            capture_root=str(ready_capture_root),
        )
        ready_preflight = evaluate_field_capture_preflight(
            plan_path=base / "field_collection_plan_ready_capture.json",
            repo_root=Path.cwd(),
        )
        assert_equal(ready_preflight["status"], "degraded", "field preflight waits for log and metadata")
        assert_equal(ready_preflight["ready_for_capture"], True, "field preflight capture readiness")
        assert_equal(ready_preflight["ready_for_registration"], False, "field preflight registration readiness")
        assert_equal(
            ready_preflight["bundle_path"],
            str(ready_bundle),
            "field preflight reports selected bundle path",
        )
        if "validate_terrain_bundle.sh" not in ready_preflight["bundle_validation_command"]:
            raise AssertionError("Expected preflight to include bundle validation command")
        ready_bundle_check = next(check for check in ready_preflight["checks"] if check["name"] == "bundle_path")
        assert_equal(
            ready_bundle_check["status"],
            "passed",
            "field preflight validates the selected terrain bundle",
        )
        assert_equal(
            (ready_bundle_check.get("details") or {}).get("validation", {}).get("terrain_bundle_status"),
            "passed",
            "field preflight reports terrain validation status",
        )
        ready_actions = {item["id"]: item for item in ready_preflight["next_actions"]}
        assert_equal(
            ready_actions["capture_field_terrain_log"]["status"],
            "ready",
            "field preflight marks capture action ready",
        )
        assert_equal(
            ready_actions["complete_capture_metadata"]["status"],
            "action_required",
            "field preflight marks metadata action required",
        )
        assert_equal(
            ready_actions["register_field_replay_case"]["status"],
            "blocked",
            "field preflight keeps registration blocked until proof files exist",
        )
        if ready_preflight["condition"] != "good_texture":
            raise AssertionError("Expected preflight to select next field condition")
        if not any(check["name"] == "registration_inputs" and check["status"] == "degraded" for check in ready_preflight["checks"]):
            raise AssertionError("Expected preflight to flag missing registration inputs")
        nested_output_plan = json.loads(json.dumps(ready_plan))
        nested_output = base / "field-captures-missing-parent" / "nested" / "Site-A-good_texture"
        nested_output_plan["next_condition"]["capture_output_dir"] = str(nested_output)
        nested_output_plan["next_condition"]["source_log"] = str(nested_output / "terrain_matches.jsonl")
        nested_output_plan["next_condition"]["runtime_status_path"] = str(nested_output / "runtime_status.json")
        for item in nested_output_plan["conditions"]:
            if item["condition"] == "good_texture":
                item["capture_output_dir"] = str(nested_output)
                item["source_log"] = str(nested_output / "terrain_matches.jsonl")
                item["runtime_status_path"] = str(nested_output / "runtime_status.json")
        nested_output_plan_path = base / "field_collection_plan_nested_output.json"
        nested_output_plan_path.write_text(json.dumps(nested_output_plan))
        nested_output_preflight = evaluate_field_capture_preflight(
            plan_path=nested_output_plan_path,
            repo_root=Path.cwd(),
        )
        nested_output_checks = {item["name"]: item["status"] for item in nested_output_preflight["checks"]}
        assert_equal(
            nested_output_checks["capture_output_parent"],
            "passed",
            "field preflight allows runtime-created nested output dirs",
        )
        assert_equal(
            nested_output_preflight["ready_for_capture"],
            True,
            "field preflight does not block capture when output ancestor is writable",
        )
        missing_bundle_plan = json.loads(json.dumps(ready_plan))
        missing_bundle_plan["next_condition"]["bundle"] = str(base / "missing_bundle")
        for item in missing_bundle_plan["conditions"]:
            if item["condition"] == "good_texture":
                item["bundle"] = str(base / "missing_bundle")
        missing_bundle_plan_path = base / "field_collection_plan_missing_bundle.json"
        missing_bundle_plan_path.write_text(json.dumps(missing_bundle_plan))
        missing_bundle_preflight = evaluate_field_capture_preflight(
            plan_path=missing_bundle_plan_path,
            repo_root=Path.cwd(),
        )
        assert_equal(missing_bundle_preflight["status"], "failed", "field preflight fails missing bundle")
        assert_equal(missing_bundle_preflight["ready_for_capture"], False, "field preflight blocks missing bundle capture")
        missing_bundle_check = next(
            check for check in missing_bundle_preflight["checks"] if check["name"] == "bundle_path"
        )
        if missing_bundle_check["status"] != "failed":
            raise AssertionError("Expected preflight to identify missing bundle")
        if "validate_terrain_bundle.sh" not in (missing_bundle_check.get("details") or {}).get("validation_command", ""):
            raise AssertionError("Expected missing bundle check to include validation command")
        if (missing_bundle_check.get("details") or {}).get("desktop_action") != "Mission Planner > Build Bundle, Upload Bundle":
            raise AssertionError("Expected missing bundle check to include desktop action hint")
        missing_bundle_diagnostic = (missing_bundle_check.get("details") or {}).get("diagnostic") or {}
        if "manifest.json" not in missing_bundle_diagnostic.get("missing_required_files", []):
            raise AssertionError("Expected missing bundle diagnostic to list missing manifest")
        detected_bundle_paths = {
            item.get("path")
            for item in missing_bundle_diagnostic.get("bundle_candidates", [])
            if isinstance(item, dict)
        }
        if str(ready_bundle) not in detected_bundle_paths:
            raise AssertionError("Expected missing bundle diagnostic to find the ready bundle candidate")
        diagnostic_actions = {
            item.get("id")
            for item in missing_bundle_diagnostic.get("recommended_actions", [])
            if isinstance(item, dict)
        }
        if "build_or_upload_selected_bundle" not in diagnostic_actions:
            raise AssertionError("Expected missing bundle diagnostic to recommend build/upload")
        missing_bundle_actions = missing_bundle_preflight["next_actions"]
        assert_equal(
            missing_bundle_actions[0]["id"],
            "prepare_bundle",
            "field preflight orders bundle prep first",
        )
        assert_equal(
            missing_bundle_actions[0]["status"],
            "action_required",
            "field preflight marks missing bundle prep action required",
        )
        missing_capture_action = next(item for item in missing_bundle_actions if item["id"] == "capture_field_terrain_log")
        assert_equal(
            missing_capture_action["status"],
            "blocked",
            "field preflight blocks capture while bundle is missing",
        )
        if "bundle_path" not in missing_capture_action.get("waits_on", []):
            raise AssertionError("Expected capture action to wait on bundle_path")

        invalid_bundle = base / "invalid_bundle"
        invalid_bundle.mkdir()
        invalid_bundle_plan = json.loads(json.dumps(ready_plan))
        invalid_bundle_plan["next_condition"]["bundle"] = str(invalid_bundle)
        for item in invalid_bundle_plan["conditions"]:
            if item["condition"] == "good_texture":
                item["bundle"] = str(invalid_bundle)
        invalid_bundle_plan_path = base / "field_collection_plan_invalid_bundle.json"
        invalid_bundle_plan_path.write_text(json.dumps(invalid_bundle_plan))
        invalid_bundle_preflight = evaluate_field_capture_preflight(
            plan_path=invalid_bundle_plan_path,
            repo_root=Path.cwd(),
        )
        assert_equal(invalid_bundle_preflight["status"], "failed", "field preflight fails invalid bundle")
        assert_equal(
            invalid_bundle_preflight["ready_for_capture"],
            False,
            "field preflight blocks capture while bundle validation fails",
        )
        invalid_bundle_check = next(
            check for check in invalid_bundle_preflight["checks"] if check["name"] == "bundle_path"
        )
        if invalid_bundle_check["status"] != "failed":
            raise AssertionError("Expected preflight to identify invalid bundle")
        invalid_validation = (invalid_bundle_check.get("details") or {}).get("validation") or {}
        if invalid_validation.get("status") != "failed":
            raise AssertionError(f"Expected invalid bundle validation failure, got {invalid_validation}")
        invalid_bundle_actions = {item["id"]: item for item in invalid_bundle_preflight["next_actions"]}
        assert_equal(
            invalid_bundle_actions["prepare_bundle"]["status"],
            "action_required",
            "field preflight orders bundle prep when validation fails",
        )

        legacy_plan = json.loads(json.dumps(plan))
        legacy_command = (
            f"VISION_NAV_FIELD_MANIFEST={active_manifest} "
            "VISION_NAV_FIELD_CONDITION=good_texture "
            "./scripts/pi/update_field_capture_metadata.sh"
        )
        legacy_capture_command = str(legacy_plan["next_condition"]["capture_command"]).replace(
            " && ./scripts/pi/read_runtime_status.sh",
            "",
        )
        legacy_plan["next_condition"]["metadata_update_command"] = legacy_command
        legacy_plan["next_condition"]["capture_command"] = legacy_capture_command
        legacy_plan["next_condition"].pop("preflight_command", None)
        for item in legacy_plan["conditions"]:
            if item["condition"] == "good_texture":
                item["metadata_update_command"] = legacy_command
                item["capture_command"] = legacy_capture_command
                item.pop("preflight_command", None)
        legacy_plan_path = base / "field_collection_plan_legacy_metadata.json"
        legacy_plan_path.write_text(json.dumps(legacy_plan))
        legacy_selection = select_next_field_condition(legacy_plan_path)
        if "VISION_NAV_FIELD_OPERATOR=TODO_operator" not in legacy_selection["metadata_update_command"]:
            raise AssertionError("Expected workflow selection to enrich stale metadata update commands")
        if "VISION_NAV_FIELD_COLLECTION_PLAN" not in legacy_selection["preflight_command"]:
            raise AssertionError("Expected workflow selection to backfill stale preflight commands")
        readiness_next = readiness_field_collection_next_condition(legacy_plan, plan_path=legacy_plan_path)
        if not readiness_next or "VISION_NAV_FIELD_OPERATOR=TODO_operator" not in readiness_next["metadata_update_command"]:
            raise AssertionError("Expected readiness next condition to enrich stale metadata update commands")
        if not readiness_next or "VISION_NAV_FIELD_COLLECTION_PLAN" not in readiness_next["preflight_command"]:
            raise AssertionError("Expected readiness next condition to backfill stale preflight commands")
        legacy_plan["_field_collection_plan_path"] = str(legacy_plan_path)
        handoff_next = handoff_field_collection_next_condition(legacy_plan)
        if not handoff_next or "VISION_NAV_FIELD_OPERATOR=TODO_operator" not in handoff_next["metadata_update_command"]:
            raise AssertionError("Expected handoff next condition to enrich stale metadata update commands")
        if not handoff_next or "VISION_NAV_FIELD_COLLECTION_PLAN" not in handoff_next["preflight_command"]:
            raise AssertionError("Expected handoff next condition to backfill stale preflight commands")
        legacy_preflight = evaluate_field_capture_preflight(
            plan_path=legacy_plan_path,
            repo_root=Path.cwd(),
        )
        legacy_preflight_checks = {item["name"]: item["status"] for item in legacy_preflight["checks"]}
        assert_equal(
            legacy_preflight_checks["capture_command"],
            "passed",
            "field preflight normalizes stale capture commands",
        )
        assert_equal(
            legacy_preflight_checks["metadata_update_command"],
            "passed",
            "field preflight normalizes stale metadata commands",
        )
        if "read_runtime_status.sh" not in legacy_preflight["capture_command"]:
            raise AssertionError("Expected field preflight to append runtime status read to stale capture command")
        if "VISION_NAV_FIELD_OPERATOR=TODO_operator" not in legacy_preflight["metadata_update_command"]:
            raise AssertionError("Expected field preflight to enrich stale metadata command")

        log_dir = base / "captures"
        log_dir.mkdir()
        log = log_dir / "terrain_matches.jsonl"
        log.write_text(
            json.dumps(
                {
                    "result": {
                        "status": "accepted",
                        "confidence": 0.82,
                        "inliers": 34,
                        "reprojection_error_px": 1.6,
                        "scale_confidence": 0.74,
                        "local_enu_m": {"x": 0.0, "y": 0.0, "z": None},
                        "covariance": {"x_m2": 4.0, "y_m2": 4.0, "z_m2": None, "yaw_rad2": None},
                    }
                }
            )
            + "\n"
        )
        register_replay_case(
            manifest_path=active_manifest,
            case_name="site-a-good-texture",
            expected="good_map",
            dataset_type="field",
            conditions=["good_texture"],
            log_path=log,
            bundle="field-bundles/site-a/mission_bundle",
            notes="Clear matching-map field log.",
            capture_metadata={
                "schema_version": CAPTURE_METADATA_SCHEMA_VERSION,
                "operator": "unit-operator",
                "lighting": "nominal",
            },
            copy_log=True,
            replace=True,
        )
        updated = create_field_collection_plan(
            manifest_path=active_manifest,
            output_path=base / "field_collection_plan_after.json",
            markdown_output_path=base / "field_collection_plan_after.md",
            site_name="Site A",
            bundle="field-bundles/site-a/mission_bundle",
        )
        assert_equal(updated["summary"]["registered_count"], 1, "field collection updated registered count")
        assert_equal(
            updated["summary"]["placeholder_count"],
            len(REQUIRED_FIELD_CONDITIONS) - 1,
            "field collection updated placeholder count",
        )
        assert_equal(
            updated["next_condition"]["condition"],
            "low_texture",
            "field collection next condition advances after registration",
        )
        assert_equal(
            updated["pending_metadata_update_command_count"],
            len(REQUIRED_FIELD_CONDITIONS) - 1,
            "field collection updated pending metadata update command count",
        )
        updated["next_condition"]["capture_metadata"] = field_capture_metadata_fixture(
            "low_texture",
            updated["next_condition"]["expected"],
            bundle=updated["next_condition"]["bundle"],
            site_name="Site A",
        )
        filled_plan = base / "field_collection_plan_filled.json"
        filled_plan.write_text(json.dumps(updated))
        filled_selection = select_next_field_condition(filled_plan)
        assert_equal(
            filled_selection["capture_metadata_status"],
            "passed",
            "field workflow selection accepts complete capture metadata",
        )
        assert_equal(
            filled_selection["environment"]["VISION_NAV_FIELD_CONDITION"],
            "low_texture",
            "field workflow selection advances condition env",
        )
        updated_good_texture = next(item for item in updated["conditions"] if item["condition"] == "good_texture")
        assert_equal(updated_good_texture["status"], "registered", "field collection registered status")
        assert_equal(
            updated_good_texture["capture_metadata"]["operator"],
            "unit-operator",
            "field collection registered capture metadata operator",
        )
        assert_equal(
            updated_good_texture["capture_metadata"]["lighting"],
            "nominal",
            "field collection registered capture metadata lighting",
        )
        markdown = render_field_collection_markdown(updated)
        if "## Next Pending Condition" not in markdown or "`low_texture`" not in markdown:
            raise AssertionError("Expected Markdown plan to highlight the next pending condition")
        if "- [x] Good texture" not in markdown:
            raise AssertionError("Expected registered condition to be checked in Markdown plan")
        if "Capture output:" not in markdown or "run_terrain_nav_loop.sh" not in markdown:
            raise AssertionError("Expected Markdown plan to include condition-specific capture instructions")
        if "Capture metadata to fill before registration" not in markdown:
            raise AssertionError("Expected Markdown plan to include capture metadata scaffold")

        metadata_update = update_field_capture_metadata(
            manifest_path=active_manifest,
            condition="low_texture",
            updates=field_capture_metadata_fixture(
                "low_texture",
                updated["next_condition"]["expected"],
                bundle=updated["next_condition"]["bundle"],
                site_name="Site A",
            ),
        )
        assert_equal(
            metadata_update["capture_metadata_status"],
            "passed",
            "field capture metadata update status",
        )
        refreshed = create_field_collection_plan(
            manifest_path=active_manifest,
            output_path=base / "field_collection_plan_metadata_updated.json",
            site_name="Site A",
            bundle="field-bundles/site-a/mission_bundle",
        )
        refreshed_low_texture = next(item for item in refreshed["conditions"] if item["condition"] == "low_texture")
        if "VISION_NAV_FIELD_OPERATOR=unit-operator" not in refreshed_low_texture["metadata_update_command"]:
            raise AssertionError("Expected refreshed metadata update command to preserve filled operator")
        if "VISION_NAV_FIELD_ALTITUDE_AGL_M=35" not in refreshed_low_texture["metadata_update_command"]:
            raise AssertionError("Expected refreshed metadata update command to preserve filled altitude")
        refreshed_selection = select_next_field_condition(base / "field_collection_plan_metadata_updated.json")
        assert_equal(
            refreshed_selection["condition"],
            "low_texture",
            "field metadata update keeps low texture as next pending condition",
        )
        assert_equal(
            refreshed_selection["capture_metadata_status"],
            "passed",
            "field metadata update persists through regenerated plan",
        )
        low_texture_case = next(
            case for case in json.loads(active_manifest.read_text())["cases"] if "low_texture" in case["conditions"]
        )
        assert_equal(
            low_texture_case["capture_metadata"]["operator"],
            "unit-operator",
            "field metadata update persisted operator",
        )


def test_replay_dataset_coverage_audit_requires_real_field_cases() -> None:
    root = Path(__file__).resolve().parents[1]
    synthetic_manifest = root / "data" / "replay_cases" / "synthetic_smoke" / "manifest.json"
    synthetic_report = audit_replay_dataset_coverage(synthetic_manifest, require_field_logs=True)
    assert_equal(synthetic_report["status"], "failed", "synthetic replay coverage status")
    requirement_statuses = {item["key"]: item["status"] for item in synthetic_report["requirements"]}
    assert_equal(requirement_statuses["good_texture"], "synthetic_only", "synthetic good texture coverage")
    assert_equal(requirement_statuses["wrong_map"], "synthetic_only", "synthetic wrong map coverage")
    assert_equal(requirement_statuses["blur"], "missing", "synthetic blur coverage")

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        cases = [
            ("field-good-texture", "good_map", "good_texture"),
            ("field-low-texture", "degraded", "low_texture"),
            ("field-blur", "degraded", "blur"),
            ("field-seasonal-change", "degraded", "seasonal_change"),
            ("field-lighting-change", "degraded", "lighting_change"),
            ("field-altitude-scale-change", "good_map", "altitude_scale_change"),
            ("field-repeated-patterns", "degraded", "repeated_patterns"),
            ("field-wrong-map", "wrong_map", "wrong_map"),
        ]
        manifest_cases = []
        for case_name, expected, condition in cases:
            log = base / f"{case_name}.jsonl"
            log.write_text(json.dumps({"result": {"status": "rejected", "reason": "audit_fixture"}}) + "\n")
            manifest_cases.append(
                {
                    "case_name": case_name,
                    "expected": expected,
                    "dataset_type": "field",
                    "conditions": [condition],
                    "bundle": "field-bundle",
                    "log": log.name,
                    "notes": f"Field replay coverage fixture for {condition}.",
                }
            )
        manifest = base / "field_manifest.json"
        manifest.write_text(json.dumps({"version": "0.1.0", "cases": manifest_cases}))
        field_report = audit_replay_dataset_coverage(manifest, require_field_logs=True)
        assert_equal(field_report["status"], "passed", "field replay coverage status")
        assert_equal(field_report["field_case_count"], len(cases), "field replay coverage case count")
        if any(requirement["status"] != "covered" for requirement in field_report["requirements"]):
            raise AssertionError(f"Expected all coverage requirements to pass, got {field_report['requirements']}")
        strict_without_metadata = audit_replay_dataset_coverage(
            manifest,
            require_field_logs=True,
            require_capture_metadata=True,
        )
        assert_equal(strict_without_metadata["status"], "failed", "strict field replay coverage requires metadata")
        if not any("capture metadata" in issue["message"].lower() for issue in strict_without_metadata["case_issues"]):
            raise AssertionError("Expected missing capture metadata to create coverage errors")

        for case in manifest_cases:
            condition = case["conditions"][0]
            case["capture_metadata"] = field_capture_metadata_fixture(condition, case["expected"])
        manifest.write_text(json.dumps({"version": "0.1.0", "cases": manifest_cases}))
        strict_with_metadata = audit_replay_dataset_coverage(
            manifest,
            require_field_logs=True,
            require_capture_metadata=True,
        )
        assert_equal(strict_with_metadata["status"], "passed", "strict field replay coverage with metadata")


def test_field_evidence_gate_combines_coverage_and_replay_gates() -> None:
    def good_record(x_m: float, y_m: float) -> dict:
        return {
            "result": {
                "status": "accepted",
                "confidence": 0.82,
                "inliers": 32,
                "reprojection_error_px": 1.8,
                "scale_confidence": 0.75,
                "local_enu_m": {"x": x_m, "y": y_m},
                "covariance": {"x_m2": 4.0, "y_m2": 4.0, "z_m2": None, "yaw_rad2": None},
            }
        }

    def rejected_record(reason: str) -> dict:
        return {"result": {"status": "rejected", "reason": reason}}

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        case_specs = [
            ("field-good-texture", "good_map", "good_texture", [good_record(0.0, 0.0), good_record(1.0, 0.2)]),
            ("field-low-texture", "degraded", "low_texture", [rejected_record("low_texture")]),
            ("field-blur", "degraded", "blur", [rejected_record("blur")]),
            ("field-seasonal-change", "degraded", "seasonal_change", [rejected_record("seasonal_change")]),
            ("field-lighting-change", "degraded", "lighting_change", [rejected_record("lighting_change")]),
            ("field-altitude-scale-change", "good_map", "altitude_scale_change", [good_record(2.0, 0.3), good_record(3.0, 0.8)]),
            ("field-repeated-patterns", "degraded", "repeated_patterns", [rejected_record("ambiguous")]),
            ("field-wrong-map", "wrong_map", "wrong_map", [rejected_record("wrong_map")]),
        ]
        manifest_cases = []
        for case_name, expected, condition, records in case_specs:
            log = base / "logs" / condition / "terrain_matches.jsonl"
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text("\n".join(json.dumps(record) for record in records) + "\n")
            manifest_cases.append(
                {
                    "case_name": case_name,
                    "expected": expected,
                    "dataset_type": "field",
                    "conditions": [condition],
                    "bundle": "field-bundle",
                    "log": str(log.relative_to(base)),
                    "capture_metadata": field_capture_metadata_fixture(condition, expected),
                }
            )
        manifest = base / "manifest.json"
        manifest.write_text(json.dumps({"version": "0.1.0", "cases": manifest_cases}))

        output = base / "reports" / "field_evidence.json"
        report = evaluate_field_evidence_gate(manifest, output_path=output)
        assert_equal(report["status"], "passed", "field evidence gate status")
        assert_equal(report["summary"]["field_case_count"], 8, "field evidence gate field case count")
        if not output.exists():
            raise AssertionError("Expected field evidence report to be written")

        missing_manifest = base / "missing_manifest.json"
        missing_cases = [dict(case) for case in manifest_cases]
        missing_cases[0]["log"] = "logs/good_texture/missing.jsonl"
        missing_manifest.write_text(json.dumps({"version": "0.1.0", "cases": missing_cases}))
        failed = evaluate_field_evidence_gate(missing_manifest)
        assert_equal(failed["status"], "failed", "field evidence gate missing log status")
        if not any(issue["severity"] == "error" for issue in failed["coverage"]["case_issues"]):
            raise AssertionError("Expected missing field log to create coverage error")

        missing_metadata_manifest = base / "missing_metadata_manifest.json"
        missing_metadata_cases = [dict(case) for case in manifest_cases]
        missing_metadata_cases[0].pop("capture_metadata", None)
        missing_metadata_manifest.write_text(json.dumps({"version": "0.1.0", "cases": missing_metadata_cases}))
        missing_metadata = evaluate_field_evidence_gate(missing_metadata_manifest)
        assert_equal(missing_metadata["status"], "failed", "field evidence gate missing metadata status")
        if not any("capture metadata" in issue["message"].lower() for issue in missing_metadata["coverage"]["case_issues"]):
            raise AssertionError("Expected missing capture metadata to fail field evidence gate")


def test_threshold_tuning_report_requires_full_field_coverage() -> None:
    def good_record(x_m: float, y_m: float) -> dict:
        return {
            "result": {
                "status": "accepted",
                "confidence": 0.84,
                "inliers": 34,
                "reprojection_error_px": 1.6,
                "scale_confidence": 0.76,
                "local_enu_m": {"x": x_m, "y": y_m},
                "covariance": {"x_m2": 4.0, "y_m2": 4.0, "z_m2": None, "yaw_rad2": None},
            }
        }

    def rejected_record(reason: str) -> dict:
        return {"result": {"status": "rejected", "reason": reason}}

    with tempfile.TemporaryDirectory() as tmp:
        base = Path(tmp)
        case_specs = [
            ("field-good-texture", "good_map", "good_texture", [good_record(0.0, 0.0), good_record(1.0, 0.2)]),
            ("field-low-texture", "degraded", "low_texture", [rejected_record("low_texture")]),
            ("field-blur", "degraded", "blur", [rejected_record("blur")]),
            ("field-seasonal-change", "degraded", "seasonal_change", [rejected_record("seasonal_change")]),
            ("field-lighting-change", "degraded", "lighting_change", [rejected_record("lighting_change")]),
            ("field-altitude-scale-change", "good_map", "altitude_scale_change", [good_record(2.0, 0.3), good_record(3.0, 0.8)]),
            ("field-repeated-patterns", "wrong_map", "repeated_patterns", [rejected_record("ambiguous")]),
            ("field-wrong-map", "wrong_map", "wrong_map", [rejected_record("wrong_map")]),
        ]
        manifest_cases = []
        for case_name, expected, condition, records in case_specs:
            log = base / "logs" / condition / "terrain_matches.jsonl"
            log.parent.mkdir(parents=True, exist_ok=True)
            log.write_text("\n".join(json.dumps(record) for record in records) + "\n")
            manifest_cases.append(
                {
                    "case_name": case_name,
                    "expected": expected,
                    "dataset_type": "field",
                    "conditions": [condition],
                    "bundle": "field-bundle",
                    "log": str(log.relative_to(base)),
                    "capture_metadata": field_capture_metadata_fixture(condition, expected),
                }
            )
        manifest = base / "manifest.json"
        manifest.write_text(json.dumps({"version": "0.1.0", "cases": manifest_cases}))

        output = base / "threshold_tuning_report.json"
        report = evaluate_threshold_tuning(manifest, output_path=output)
        assert_equal(report["status"], "passed", "threshold tuning status")
        assert_equal(report["method"], "field-replay-gate-threshold-audit", "threshold tuning method")
        assert_equal(set(report["conditions"]), set(REQUIRED_FIELD_CONDITIONS), "threshold tuning covered conditions")
        assert_equal(report["summary"]["field_case_count"], 8, "threshold tuning field case count")
        assert_equal(report["metrics"]["by_expected"]["good_map"]["case_count"], 2, "threshold tuning good-map cases")
        if report["metrics"]["margins"]["wrong_map_accepted_rate"] is None:
            raise AssertionError("Expected wrong-map margin to be reported")
        if not output.exists():
            raise AssertionError("Expected threshold tuning report to be written")

        missing_manifest = base / "missing_threshold_manifest.json"
        missing_manifest.write_text(json.dumps({"version": "0.1.0", "cases": manifest_cases[:-1]}))
        failed = evaluate_threshold_tuning(missing_manifest)
        assert_equal(failed["status"], "failed", "threshold tuning missing coverage status")
        if "wrong_map" not in {item["key"] for item in failed["coverage"]["requirements"] if item["status"] == "missing"}:
            raise AssertionError("Expected missing wrong-map coverage in threshold tuning report")


def test_replay_case_registry_registers_and_replaces_cases() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        tmp_root = Path(tmp)
        base = tmp_root / "dataset"
        base.mkdir()
        source_dir = tmp_root / "source_logs"
        source_dir.mkdir()
        log = source_dir / "terrain_matches.jsonl"
        log.write_text(json.dumps({"result": {"status": "accepted", "confidence": 0.91}}) + "\n")
        manifest = base / "manifest.json"

        result = register_replay_case(
            manifest_path=manifest,
            case_name="field-good-texture",
            expected="good_map",
            dataset_type="field",
            conditions=["good_texture", "clear-texture"],
            log_path=log,
            bundle="field-bundle",
            notes="field capture",
            capture_metadata={
                "schema_version": CAPTURE_METADATA_SCHEMA_VERSION,
                "operator": "registry-test",
                "flight_altitude_agl_m": 35,
            },
            copy_log=True,
        )
        assert_equal(result["status"], "registered", "replay case registry status")
        copied_log = base / "field" / "field-good-texture" / "terrain_matches.jsonl"
        if not copied_log.exists():
            raise AssertionError(f"Expected copied replay log at {copied_log}")
        manifest_data = json.loads(manifest.read_text())
        assert_equal(len(manifest_data["cases"]), 1, "registered replay case count")
        case = manifest_data["cases"][0]
        assert_equal(case["log"], "field/field-good-texture/terrain_matches.jsonl", "stored replay log path")
        assert_equal(case["conditions"], ["good_texture", "clear_texture"], "normalized replay case conditions")
        assert_equal(
            case["capture_metadata"]["operator"],
            "registry-test",
            "registered replay case capture metadata operator",
        )
        assert_equal(
            case["capture_metadata"]["flight_altitude_agl_m"],
            35,
            "registered replay case capture metadata altitude",
        )

        try:
            register_replay_case(
                manifest_path=manifest,
                case_name="field-good-texture",
                expected="good_map",
                dataset_type="field",
                conditions=["good_texture"],
                log_path=log,
            )
        except ValueError:
            pass
        else:
            raise AssertionError("Expected duplicate replay case registration to require --replace")

        replaced = register_replay_case(
            manifest_path=manifest,
            case_name="field-good-texture",
            expected="degraded",
            dataset_type="field",
            conditions=["low_texture"],
            log_path=log,
            bundle="field-bundle-v2",
            replace=True,
        )
        assert_equal(replaced["case_count"], 1, "replaced replay case count")
        replaced_data = json.loads(manifest.read_text())
        replaced_case = replaced_data["cases"][0]
        assert_equal(replaced_case["expected"], "degraded", "replaced expected behavior")
        assert_equal(replaced_case["conditions"], ["low_texture"], "replaced replay conditions")
        if not Path(replaced_case["log"]).is_absolute():
            raise AssertionError("Expected external non-copied log path to be stored as absolute path")

        template_base = tmp_root / "template_dataset"
        template_base.mkdir()
        template_manifest = template_base / "field_manifest.template.json"
        active_manifest = template_base / "field_manifest.json"
        create_field_evidence_template(
            output_path=template_manifest,
            site_name="field-site",
            bundle="field-bundle",
            seed_manifest_path=active_manifest,
        )
        template_data = json.loads(active_manifest.read_text())
        assert_equal(len(template_data["cases"]), len(REQUIRED_FIELD_CONDITIONS), "template seed case count")
        log2 = template_base / "captured_good_texture.jsonl"
        log2.write_text(json.dumps({"result": {"status": "accepted", "confidence": 0.92}}) + "\n")
        registered_from_template = register_replay_case(
            manifest_path=active_manifest,
            case_name="field-good-texture",
            expected="good_map",
            dataset_type="field",
            conditions=["good_texture"],
            log_path=log2,
            bundle="field-bundle",
            notes="real good texture log",
        )
        assert_equal(
            registered_from_template["replaced_template_placeholders"],
            1,
            "template placeholder replacement count",
        )
        assert_equal(
            registered_from_template["case_count"],
            len(REQUIRED_FIELD_CONDITIONS),
            "template placeholder replacement preserves case count",
        )
        registered_data = json.loads(active_manifest.read_text())
        good_texture_cases = [
            case for case in registered_data["cases"] if case.get("conditions") == ["good_texture"]
        ]
        assert_equal(len(good_texture_cases), 1, "single good texture case after placeholder replacement")
        assert_equal(good_texture_cases[0]["case_name"], "field-good-texture", "registered case replaced placeholder name")
        if "template_status" in good_texture_cases[0]:
            raise AssertionError("Registered real case should not retain template_status")


def test_geospatial_health_blocks_missing_georef() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        bundle = Path(tmp) / "bundle"
        (bundle / "ortho").mkdir(parents=True)
        write_minimal_png(bundle / "ortho" / "map.png", 20, 10)
        (bundle / "manifest.json").write_text(
            json.dumps(
                {
                    "bundle_id": "missing-georef",
                    "orthophoto": {"path": "ortho/map.png"},
                    "features": {"path": "features/map_features.npz", "method": "orb", "max_features": 100},
                    "terrain_bundle": {"tile_index_path": "index/tiles.sqlite"},
                }
            )
        )
        report = geospatial_health_report(bundle)
        assert_equal(report["status"], "failed", "missing georef health status")
        if not any("Missing georeference" in issue["message"] for issue in report["issues"]):
            raise AssertionError("Expected missing georeference issue")


def test_terrain_tile_origins_cover_edges() -> None:
    assert_equal(tile_origins(100, 128, 16), [0], "short tile origins")
    origins = tile_origins(300, 128, 16)
    assert_equal(origins[-1], 172, "tile origins final edge")
    assert_equal(len(set(origins)), len(origins), "tile origins unique")


def test_global_image_descriptor_separates_simple_textures() -> None:
    dark = image_global_descriptor(np.zeros((16, 16), dtype=np.uint8))
    bright = image_global_descriptor(np.full((16, 16), 255, dtype=np.uint8))
    dark_again = image_global_descriptor(np.zeros((16, 16), dtype=np.uint8))
    same_distance = global_descriptor_distance(dark, dark_again)
    different_distance = global_descriptor_distance(dark, bright)
    if same_distance is None or same_distance > 1e-6:
        raise AssertionError("Expected identical global descriptors to have near-zero distance")
    if different_distance is None or different_distance <= 0.5:
        raise AssertionError("Expected dark and bright global descriptors to separate")


def test_precomputed_neural_tile_descriptor_loads_from_npz() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        descriptor_path = root / "tile_000000.npz"
        save_tile_descriptor(
            descriptor_path,
            tile_id="tile_000000",
            image_path="imagery/tiles/tile_000000.png",
            image_shape=(8, 8),
            method="orb",
            keypoints_xy=np.zeros((0, 2), dtype=np.float32),
            descriptors=np.zeros((0, 32), dtype=np.uint8),
            offset_xy_px=(0, 0),
            retrieval_descriptors={"neural_global_descriptor": np.array([0.0, 3.0, 4.0], dtype=np.float32)},
        )
        tile = TerrainTile(
            tile_id="tile_000000",
            row=0,
            col=0,
            x0_px=0,
            y0_px=0,
            x1_px=8,
            y1_px=8,
            min_east_m=None,
            max_east_m=None,
            min_north_m=None,
            max_north_m=None,
            image_path=root / "tile_000000.png",
            descriptor_path=descriptor_path,
            keypoint_count=0,
            method="orb",
        )
        descriptor, source = load_tile_retrieval_descriptor(tile)
        assert_equal(source, "neural_global_descriptor", "embedded neural descriptor source")
        if descriptor is None or abs(float(np.linalg.norm(descriptor)) - 1.0) > 1e-6:
            raise AssertionError("Expected embedded neural descriptor to load as a normalized vector")


def test_retrieval_benchmark_ranks_expected_tile() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bundle = root / "bundle"
        (bundle / "ortho").mkdir(parents=True)
        (bundle / "index" / "descriptors").mkdir(parents=True)
        (bundle / "imagery" / "tiles").mkdir(parents=True)
        write_minimal_png(bundle / "ortho" / "map.png", 32, 16)
        dark = np.zeros((16, 16), dtype=np.uint8)
        bright = np.full((16, 16), 255, dtype=np.uint8)
        for tile_id, image, offset in (
            ("tile_000000", dark, (0, 0)),
            ("tile_000001", bright, (16, 0)),
        ):
            save_tile_descriptor(
                bundle / "index" / "descriptors" / f"{tile_id}.npz",
                tile_id=tile_id,
                image_path=f"imagery/tiles/{tile_id}.png",
                image_shape=image.shape,
                method="orb",
                keypoints_xy=np.zeros((0, 2), dtype=np.float32),
                descriptors=np.zeros((0, 32), dtype=np.uint8),
                offset_xy_px=offset,
                global_descriptor=image_global_descriptor(image),
            )
            write_minimal_png(bundle / "imagery" / "tiles" / f"{tile_id}.png", 16, 16)
        index_path = bundle / "index" / "tiles.sqlite"
        with sqlite3.connect(index_path) as conn:
            create_tile_schema(conn)
            for tile_id, col, x0, x1 in (
                ("tile_000000", 0, 0, 16),
                ("tile_000001", 1, 16, 32),
            ):
                conn.execute(
                    """
                    INSERT INTO tiles (
                        tile_id, row, col, x0_px, y0_px, x1_px, y1_px,
                        min_east_m, max_east_m, min_north_m, max_north_m,
                        image_path, descriptor_path, keypoint_count, method
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        tile_id,
                        0,
                        col,
                        x0,
                        0,
                        x1,
                        16,
                        float(x0),
                        float(x1),
                        -16.0,
                        0.0,
                        f"imagery/tiles/{tile_id}.png",
                        f"index/descriptors/{tile_id}.npz",
                        0,
                        "orb",
                    ),
                )
            conn.commit()
        (bundle / "manifest.json").write_text(
            json.dumps(
                {
                    "bundle_id": "retrieval-test",
                    "orthophoto": {
                        "path": "ortho/map.png",
                        "origin_lat": 40.0,
                        "origin_lon": -75.0,
                        "gsd_m": 1.0,
                    },
                    "features": {"path": "features/map_features.npz", "method": "orb", "max_features": 100},
                    "terrain_bundle": {
                        "version": "0.1.0",
                        "tile_index_path": "index/tiles.sqlite",
                        "tile_size_px": 16,
                        "overlap_px": 0,
                        "gsd_m": 1.0,
                    },
                }
            )
        )
        frame = root / "bright_frame.npy"
        np.save(frame, bright)
        replay_log = root / "replay.jsonl"
        replay_log.write_text(json.dumps({"frame_path": frame.name, "expected_tile_id": "tile_000001"}) + "\n")

        report = benchmark_retrieval(bundle, replay_log, top_k=[1, 2], backend="all")
        global_backend = report["backends"]["global_histogram_v1"]
        assert_equal(global_backend["status"], "passed", "retrieval benchmark global status")
        assert_equal(global_backend["recall_at_k"]["1"], 1.0, "retrieval benchmark recall@1")
        assert_equal(global_backend["records"][0]["rank"], 1, "retrieval benchmark expected rank")
        assert_equal(global_backend["records"][0]["top_tile_id"], "tile_000001", "retrieval benchmark top tile")
        assert_equal(report["backends"]["neural"]["status"], "not_available", "retrieval benchmark neural status")

        np.save(bundle / "index" / "descriptors" / "tile_000000.neural.npy", np.array([1.0, 0.0, 0.0], dtype=np.float32))
        np.save(bundle / "index" / "descriptors" / "tile_000001.neural.npy", np.array([0.0, 1.0, 0.0], dtype=np.float32))
        neural_log = root / "neural_replay.jsonl"
        neural_log.write_text(
            json.dumps(
                {
                    "frame_path": frame.name,
                    "expected_tile_id": "tile_000001",
                    "neural_global_descriptor": [0.0, 1.0, 0.0],
                }
            )
            + "\n"
        )

        neural_report = benchmark_retrieval(bundle, neural_log, top_k=[1, 2], backend="neural")
        neural_backend = neural_report["backends"]["neural"]
        assert_equal(neural_backend["status"], "passed", "retrieval benchmark neural status with sidecars")
        assert_equal(neural_backend["tile_descriptor_count"], 2, "retrieval benchmark neural descriptor count")
        assert_equal(neural_backend["recall_at_k"]["1"], 1.0, "retrieval benchmark neural recall@1")
        assert_equal(neural_backend["records"][0]["rank"], 1, "retrieval benchmark neural expected rank")
        assert_equal(neural_backend["records"][0]["top_tile_id"], "tile_000001", "retrieval benchmark neural top tile")

        missing_query_log = root / "missing_neural_query.jsonl"
        missing_query_log.write_text(json.dumps({"frame_path": frame.name, "expected_tile_id": "tile_000001"}) + "\n")
        missing_query_report = benchmark_retrieval(bundle, missing_query_log, top_k=[1, 2], backend="neural")
        missing_query_backend = missing_query_report["backends"]["neural"]
        assert_equal(missing_query_backend["status"], "degraded", "retrieval benchmark missing neural query status")
        assert_equal(
            missing_query_backend["skipped"][0]["reason"],
            "missing_expected_tile_or_query_descriptor",
            "retrieval benchmark missing neural query reason",
        )


def test_feature_method_benchmark_compares_gate_results() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        orb_log = root / "orb.jsonl"
        akaze_log = root / "akaze.jsonl"
        accepted = {
            "status": "accepted",
            "confidence": 0.82,
            "inliers": 32,
            "reprojection_error_px": 1.6,
            "scale_confidence": 0.78,
            "covariance": {"x_m2": 4.0, "y_m2": 4.0, "z_m2": None, "yaw_rad2": None},
        }
        orb_log.write_text(
            "\n".join(
                [
                    json.dumps({"sequence": 1, "result": {**accepted, "local_enu_m": {"x": 0.0, "y": 0.0}}}),
                    json.dumps({"sequence": 2, "result": {**accepted, "local_enu_m": {"x": 1.0, "y": 0.5}}}),
                ]
            )
            + "\n"
        )
        akaze_log.write_text(
            "\n".join(
                [
                    json.dumps({"sequence": 1, "result": {"status": "rejected", "reason": "not_enough_inliers"}}),
                    json.dumps({"sequence": 2, "result": {"status": "rejected", "reason": "not_enough_inliers"}}),
                ]
            )
            + "\n"
        )

        report = benchmark_feature_methods(
            expected="good_map",
            methods=["orb", "akaze", "neural"],
            case_name="unit-method-benchmark",
            method_logs={"orb": orb_log, "akaze": akaze_log},
        )

        assert_equal(report["status"], "passed", "feature method benchmark overall status")
        assert_equal(report["recommended_method"], "orb", "feature method benchmark recommendation")
        statuses = {method["method"]: method["status"] for method in report["methods"]}
        assert_equal(statuses["orb"], "passed", "orb gate status")
        assert_equal(statuses["akaze"], "failed", "akaze gate status")
        assert_equal(statuses["neural"], "not_available", "neural placeholder status")
        orb_report = next(method for method in report["methods"] if method["method"] == "orb")
        assert_equal(orb_report["gate"]["metrics"]["accepted_rate"], 1.0, "orb accepted rate")


def test_hierarchical_tile_query_uses_prior_or_spatial_coverage() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        bundle = create_minimal_terrain_bundle(Path(tmp))
        index_path = bundle / "index" / "tiles.sqlite"
        with sqlite3.connect(index_path) as conn:
            for idx, (row, col, keypoints) in enumerate(
                [
                    (0, 1, 40),
                    (1, 0, 80),
                    (1, 1, 10),
                    (1, 2, 70),
                    (2, 1, 30),
                ],
                start=1,
            ):
                conn.execute(
                    """
                    INSERT INTO tiles (
                        tile_id, row, col, x0_px, y0_px, x1_px, y1_px,
                        min_east_m, max_east_m, min_north_m, max_north_m,
                        image_path, descriptor_path, keypoint_count, method
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        f"tile_{idx:06d}",
                        row,
                        col,
                        col * 64,
                        row * 64,
                        col * 64 + 64,
                        row * 64 + 64,
                        float(col * 32),
                        float(col * 32 + 32),
                        float(-row * 32 - 32),
                        float(-row * 32),
                        "imagery/tiles/tile_000000.png",
                        "index/descriptors/tile_000000.npz",
                        keypoints,
                        "orb",
                    ),
                )
            conn.commit()

        coarse = query_tiles_with_metadata(index_path, bundle, max_candidates=3)
        assert_equal(coarse.metadata["strategy"], "coarse_spatial_coverage", "coarse query strategy")
        if len({tile.row for tile in coarse.tiles}) < 2 or len({tile.col for tile in coarse.tiles}) < 2:
            raise AssertionError("Expected coarse startup query to cover multiple rows and columns")

        local = query_tiles_with_metadata(
            index_path,
            bundle,
            prior_east_m=75.0,
            prior_north_m=-50.0,
            search_radius_m=10.0,
            max_candidates=4,
        )
        assert_equal(local.metadata["strategy"], "prior_local_radius", "prior query strategy")
        assert_equal(local.tiles[0].tile_id, "tile_000004", "nearest prior tile")
        assert_equal(local.metadata["within_radius_tiles"], 1, "prior query radius count")


def test_terrain_estimator_updates_and_inflates_covariance() -> None:
    estimator = TerrainEstimator(process_noise_m2_per_s=2.0)
    result = estimator.update_from_match(
        {
            "timestamp_us": 1_000_000,
            "status": "accepted",
            "local_enu_m": {"x": 3.0, "y": 4.0, "z": None},
            "confidence": 0.8,
            "scale_confidence": 0.7,
            "covariance": {"x_m2": 1.0, "y_m2": 2.0, "z_m2": None, "yaw_rad2": None},
        }
    )
    assert_equal(result["estimator"]["initialized"], True, "terrain estimator initialized")
    estimator.propagate_time(2_000_000)
    if estimator.state.covariance_x_m2 is None or estimator.state.covariance_x_m2 <= 1.0:
        raise AssertionError("Expected terrain estimator covariance to grow after propagation")


def test_barometer_tracker_and_estimator_fields() -> None:
    if abs(pressure_to_altitude_m(1013.25)) > 0.01:
        raise AssertionError("Expected standard pressure altitude to be near zero")
    tracker = BarometerTracker()
    tracker.update(BarometerSample(altitude_m=100.0, source="unit"))
    baro_state = tracker.update(BarometerSample(altitude_m=103.0, source="unit"))
    assert_equal(baro_state.relative_altitude_m, 3.0, "barometer relative altitude")

    estimator = TerrainEstimator()
    result = estimator.update_from_match(
        {
            "timestamp_us": 1,
            "status": "accepted",
            "local_enu_m": {"x": 1.0, "y": 2.0, "z": None},
            "confidence": 0.8,
            "scale_confidence": 0.5,
            "covariance": {"x_m2": 1.0, "y_m2": 1.0, "z_m2": None, "yaw_rad2": None},
            "measurement": {
                "frame": "local_enu",
                "x_m": 1.0,
                "y_m": 2.0,
                "z_m": None,
                "covariance": {"x_m2": 1.0, "y_m2": 1.0, "z_m2": None, "yaw_rad2": None},
            },
        },
        barometer_sample={"relative_altitude_m": 4.0, "source": "unit"},
    )
    assert_equal(result["altitude_source"], "barometer", "barometer altitude source")
    assert_equal(result["local_enu_m"]["z"], 4.0, "barometer local z")


def main() -> None:
    tests = [
        test_pixel_to_local_default_north_up_axes,
        test_georef_json_round_trip,
        test_build_georef_requires_core_fields_together,
        test_load_manifest_and_resolve_paths,
        test_load_camera_calibration_and_validate_size,
        test_summarize_match_records,
        test_quality_metrics_and_covariance,
        test_mavlink_endpoint_parsing_and_axis_mapping,
        test_mavlink_log_sender_uses_selected_message_type,
        test_mavlink_odometry_requires_mavlink2_dialect,
        test_mavlink_odometry_reports_unsupported_connection,
        test_px4_sitl_receiver_evidence_gate,
        test_px4_sitl_session_evaluator_writes_report_and_flags_missing_captures,
        test_px4_param_checker_flags_external_vision_readiness,
        test_ardupilot_param_checker_flags_external_nav_readiness,
        test_external_position_payloads,
        test_external_position_stream_health,
        test_ros2_odometry_and_diagnostics_adapters,
        test_ros2_bag_jsonl_export_writes_topic_records,
        test_ros2_launch_profiles_static,
        test_ros2_package_wrapper_static,
        test_camera_health_report_on_synthetic_image,
        test_bundle_checksums_detect_changed_file,
        test_validate_bundle_passes_complete_bundle,
        test_bundle_diagnostics_finds_bundle_and_map_source_candidates,
        test_geospatial_health_report_validates_stac_tiles_and_bounds,
        test_gdal_metadata_degrades_gracefully_when_unavailable,
        test_terrain_profile_reports_agl_and_gsd_warnings,
        test_runtime_status_snapshot_reports_active_map_and_last_match,
        test_support_bundle_collects_manifest_health_logs_and_summary,
        test_autonomy_evidence_workflow_validation_checks_log_archive,
        test_autonomy_readiness_requires_external_proof_artifacts,
        test_replay_gates_pass_good_map_and_fail_wrong_map_acceptance,
        test_replay_gates_fail_missing_metrics_motion_jumps_and_weak_covariance,
        test_synthetic_replay_case_manifest_passes_all_cases,
        test_replay_case_manifest_schema_flags_malformed_cases,
        test_replay_case_manifest_schema_only_skips_log_evaluation,
        test_field_evidence_template_matches_required_conditions,
        test_field_collection_plan_tracks_placeholders_and_registered_logs,
        test_replay_dataset_coverage_audit_requires_real_field_cases,
        test_field_evidence_gate_combines_coverage_and_replay_gates,
        test_threshold_tuning_report_requires_full_field_coverage,
        test_replay_case_registry_registers_and_replaces_cases,
        test_geospatial_health_blocks_missing_georef,
        test_terrain_tile_origins_cover_edges,
        test_global_image_descriptor_separates_simple_textures,
        test_precomputed_neural_tile_descriptor_loads_from_npz,
        test_retrieval_benchmark_ranks_expected_tile,
        test_feature_method_benchmark_compares_gate_results,
        test_hierarchical_tile_query_uses_prior_or_spatial_coverage,
        test_terrain_estimator_updates_and_inflates_covariance,
        test_barometer_tracker_and_estimator_fields,
    ]
    for test in tests:
        try:
            test()
        except SkipTest as exc:
            print(f"[SKIP] {test.__name__}: {exc}")
        else:
            print(f"[OK] {test.__name__}")


if __name__ == "__main__":
    main()
