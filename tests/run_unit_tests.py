from __future__ import annotations

import contextlib
import io
import json
import math
from pathlib import Path
import shutil
import sqlite3
import struct
import tarfile
import tempfile
import time
import zipfile

import numpy as np

from vision_nav.barometer import BarometerSample, BarometerTracker, pressure_to_altitude_m
from vision_nav.ardupilot_params import check_ardupilot_external_nav_params, params_from_text as ardupilot_params_from_text
from vision_nav.autonomy_evidence_package import create_evidence_package
from vision_nav.autonomy_evidence_workflow import REQUIRED_WORKFLOW_STEPS, validate_workflow_report, validation_exit_code
from vision_nav.autonomy_handoff import render_handoff_markdown
from vision_nav.autonomy_readiness import REQUIRED_FIELD_CONDITIONS, evaluate_autonomy_readiness
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
from vision_nav.field_collection_plan import create_field_collection_plan, render_field_collection_markdown
from vision_nav.field_evidence_template import create_field_evidence_template
from vision_nav.field_evidence_gate import evaluate_field_evidence_gate
from vision_nav.geospatial_health import gdal_raster_metadata, geospatial_health_report
from vision_nav.georef import SimpleGeoReference, build_georef_from_cli, georef_from_json, georef_to_json
from vision_nav.mavlink_bridge import MavlinkSendResult, MavlinkVisionBridge, parse_mavlink_endpoint, send_records_once
from vision_nav.px4_sitl_evidence import Px4SitlEvidenceConfig, evaluate_px4_sitl_evidence
from vision_nav.px4_sitl_session import evaluate_px4_sitl_session
from vision_nav.px4_params import check_px4_external_vision_params, evaluate_px4_param_file, params_from_text
from vision_nav.ros2_bridge import DIAG_ERROR, DIAG_OK, diagnostic_status_from_health, odometry_dict_from_match_result
from vision_nav.ros2_bridge import export_rosbag_jsonl, export_rosbag_mcap, export_rosbag2, ros_records_from_log
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
        }
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
                    "version": "0.1.0",
                    "message_type": "odometry",
                    "rate_hz": 5.0,
                    "expected_captures": {
                        "vehicle_visual_odometry": "receiver_capture/vehicle_visual_odometry.txt",
                        "mavlink_status": "receiver_capture/mavlink_status.txt",
                    },
                    "receiver_report": "receiver_evidence.json",
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
                "covariance": {"x_m2": 9.0, "y_m2": 16.0, "z_m2": 25.0, "yaw_rad2": 0.2},
            },
        }
    )
    assert_equal(reason, None, "external position parse reason")
    ned = estimate.to_local_ned()
    assert_equal(ned.north_m, 7.0, "external position north")
    assert_equal(ned.east_m, 4.0, "external position east")
    assert_equal(ned.down_m, -3.0, "external position down")
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
    assert_equal(odometry_payload.quality, 73, "odometry quality")

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


def test_ros2_launch_profiles_static() -> None:
    root = Path(__file__).resolve().parents[1]
    live = (root / "ros2" / "launch" / "terrain_nav_live.launch.py").read_text()
    replay = (root / "ros2" / "launch" / "terrain_nav_replay.launch.py").read_text()
    for expected in ("vision_nav.run_terrain_loop", "--ros2-publish", "/vision_nav/odometry", "/diagnostics"):
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
                    "version": "0.1.0",
                    "message_type": "odometry",
                    "rate_hz": 5.0,
                    "expected_captures": {
                        "vehicle_visual_odometry": "receiver_capture/vehicle_visual_odometry.txt",
                        "mavlink_status": "receiver_capture/mavlink_status.txt",
                    },
                    "receiver_report": "receiver_evidence.json",
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
                        }
                        for condition in REQUIRED_FIELD_CONDITIONS
                    ],
                }
            )
        )
        field_collection_plan.with_suffix(".md").write_text("# Field Evidence Collection Plan\n")
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
            threshold_tuning_report_paths=[str(threshold_tuning_report)],
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
            "summaries/terrain_matches.summary.json",
            "summaries/replay_gates/unit-good.gate.json",
            "summaries/px4_sitl_evidence/receiver_evidence.json",
            "summaries/px4_params/param_check.json",
            "summaries/ardupilot_params/param_check.json",
            "summaries/feature_method_benchmarks/unit-method-benchmark-01.json",
            "summaries/field_evidence/field_manifest-01.json",
            "summaries/field_collection_plans/field_manifest-01.json",
            "summaries/threshold_tuning/field_manifest-01.json",
            "summaries/bench_readiness.json",
            "extras/field_collection_plans/field_collection_plan.json",
            "extras/field_collection_plans/field_collection_plan.md",
            "extras/px4_sitl_session/px4_sitl_evidence_session.json",
            "extras/px4_sitl_session/receiver_capture/vehicle_visual_odometry.txt",
            "extras/px4_sitl_session/receiver_capture/mavlink_status.txt",
            "extras/px4_params/px4.params",
            "extras/ardupilot_params/ardupilot.params",
            "extras/feature_method_benchmarks/feature-method-bench/unit-method-benchmark.json",
            "extras/field_evidence/field_evidence_report.json",
            "extras/threshold_tuning/threshold_tuning_report.json",
        }:
            if expected not in names:
                raise AssertionError(f"Missing {expected} from support bundle zip")
        manifest = json.loads(Path(result["manifest_path"]).read_text())
        assert_equal(manifest["bundle"]["mission_plan"]["status"], "loaded", "support mission plan loaded")
        assert_equal(manifest["bundle"]["mission_plan"]["gnss_denied"]["status"], "ready", "support gnss denied status")
        assert_equal(manifest["logs"]["summaries"][0]["accepted_rate"], 1.0, "support log accepted rate")
        assert_equal(manifest["field_collection_plans"]["status"], "passed", "support field collection plan status")
        assert_equal(manifest["field_collection_plans"]["report_count"], 1, "support field collection plan count")
        assert_equal(
            manifest["field_collection_plans"]["registered_count"],
            len(REQUIRED_FIELD_CONDITIONS),
            "support field collection plan registered count",
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
        assert_equal(manifest["bench_readiness"]["status"], "degraded", "support bench readiness status")
        assert_equal(manifest["bench_readiness"]["summary"]["degraded"], 1, "support bench readiness degraded count")
        readiness = evaluate_bench_readiness_file(zip_path)
        assert_equal(readiness["status"], "degraded", "bench readiness degraded on px4 param warning")
        readiness_checks = {check["name"]: check["status"] for check in readiness["checks"]}
        readiness_check_details = {check["name"]: check.get("details") or {} for check in readiness["checks"]}
        assert_equal(readiness_checks["bundle_health"], "passed", "bench readiness bundle health")
        assert_equal(readiness_checks["gnss_denied_plan"], "passed", "bench readiness gnss denied plan")
        assert_equal(readiness_checks["runtime_status"], "passed", "bench readiness runtime status")
        assert_equal(readiness_checks["px4_sitl_evidence"], "passed", "bench readiness px4 evidence")
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
        assert_equal(readiness_checks["px4_params"], "degraded", "bench readiness px4 params")
        assert_equal(readiness_checks["ardupilot_params"], "passed", "bench readiness ardupilot params")
        assert_equal(readiness_checks["feature_method_benchmarks"], "passed", "bench readiness feature benchmarks")
        assert_equal(readiness_checks["field_evidence"], "passed", "bench readiness field evidence")

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

        missing_runtime_status = json.loads(json.dumps(manifest))
        missing_runtime_status["logs"]["runtime_statuses"] = []
        runtime_degraded = evaluate_bench_readiness(missing_runtime_status)
        runtime_checks = {check["name"]: check["status"] for check in runtime_degraded["checks"]}
        assert_equal(runtime_degraded["status"], "degraded", "bench readiness missing runtime status degrades")
        assert_equal(runtime_checks["runtime_status"], "degraded", "bench readiness runtime status degrade")

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
        report_path = root / "autonomy_evidence_workflow.json"
        report_path.write_text(
            json.dumps(
                {
                    "schema_version": "vision_nav_autonomy_evidence_workflow_v1",
                    "generated_at": "2026-06-21T12:00:00Z",
                    "status": "failed",
                    "summary": {"passed": 4, "failed": 1, "skipped": 2},
                    "workflow_dir": str(root),
                    "steps": [
                        {
                            "name": step_name,
                            "status": "passed" if step_name != "run_autonomy_readiness_audit" else "failed",
                            "exit_code": 0 if step_name != "run_autonomy_readiness_audit" else 1,
                            "log_path": str(log_dir / f"{step_name}.log"),
                            "markers": {},
                        }
                        for step_name in REQUIRED_WORKFLOW_STEPS
                    ],
                    "markers": {
                        "__VISION_NAV_EVIDENCE_WORKFLOW_LOGS__": str(archive_path),
                        "__VISION_NAV_SUPPORT_ZIP__": str(root / "support.zip"),
                        "__VISION_NAV_FIELD_COLLECTION_PLAN__": str(root / "field_collection_plan.json"),
                        "__VISION_NAV_FIELD_COLLECTION_PLAN_MD__": str(root / "field_collection_plan.md"),
                        "__VISION_NAV_AUTONOMY_REPORT__": str(root / "autonomy_readiness_report.json"),
                    },
                }
            )
        )
        validation = validate_workflow_report(report_path)
        assert_equal(validation["status"], "degraded", "workflow validation preserves failed workflow status")
        assert_equal(validation_exit_code(validation), 0, "workflow validation degraded exit code")
        checks = {check["name"]: check["status"] for check in validation["checks"]}
        assert_equal(checks["log_archive"], "passed", "workflow validation log archive")
        assert_equal(validation["step_count"], len(REQUIRED_WORKFLOW_STEPS), "workflow validation step count")

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
                    "## Highest-Value References",
                    "## Recommended Product Architecture Changes",
                    "## Near-Term Repo Integration Plan",
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
                        "covered_conditions": REQUIRED_FIELD_CONDITIONS,
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
                        },
                        {
                            "condition": "blur",
                            "status": "placeholder",
                            "expected": "degraded",
                            "case_name": "unit-blur",
                            "register_command": "./scripts/pi/register_field_replay_case.sh --condition blur",
                        },
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
        threshold_report = root / "threshold_tuning.json"
        threshold_report.write_text(
            json.dumps(
                {
                    "status": "passed",
                    "method": "unit-field-grid-search",
                    "conditions": REQUIRED_FIELD_CONDITIONS,
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
                    "report_path": str(workflow_report),
                    "issue_count": 1,
                    "issues": ["unit validation issue"],
                }
            )
        )
        workflow_log_archive = root / "autonomy_evidence_workflow.logs.tar.gz"
        workflow_logs_dir = root / "workflow-logs"
        workflow_logs_dir.mkdir()
        (workflow_logs_dir / "create_field_evidence_template.log").write_text("unit workflow log\n")
        with tarfile.open(workflow_log_archive, "w:gz") as archive:
            archive.add(workflow_logs_dir / "create_field_evidence_template.log", arcname="logs/create_field_evidence_template.log")

        direct_report_support_manifest = root / "support_manifest_without_embedded_field_feature.json"
        direct_report_support_data = json.loads(support_manifest.read_text())
        direct_report_support_data.pop("px4_sitl_evidence", None)
        direct_report_support_data.pop("feature_method_benchmarks", None)
        direct_report_support_data.pop("field_evidence", None)
        direct_report_support_manifest.write_text(json.dumps(direct_report_support_data))

        ready = evaluate_autonomy_readiness(
            research_doc_path=research_doc,
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
        assert_equal(ready["status"], "passed", "autonomy readiness full proof status")
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
            str(workflow_validation_report),
            "autonomy readiness workflow validation input",
        )
        assert_equal(
            ready["inputs"]["evidence_workflow_log_archive"],
            str(workflow_log_archive),
            "autonomy readiness workflow log archive input",
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
            ready["plan_snapshot"]["implementation_plan"]["track_count"],
            5,
            "autonomy readiness implementation track count",
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
        assert_equal(ready_checks["field_evidence_proof"], "passed", "autonomy readiness field evidence")
        assert_equal(ready_checks["feature_method_benchmark"], "passed", "autonomy readiness feature benchmark")
        assert_equal(ready_checks["threshold_tuning"], "passed", "autonomy readiness threshold tuning")
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

        missing_feature_direct = evaluate_autonomy_readiness(
            research_doc_path=research_doc,
            implementation_plan_path=implementation_plan,
            support_bundle_path=direct_report_support_manifest,
            px4_sitl_report_path=px4_receiver_report,
            field_evidence_report_path=field_report,
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

        missing_field_direct = evaluate_autonomy_readiness(
            research_doc_path=research_doc,
            implementation_plan_path=implementation_plan,
            support_bundle_path=direct_report_support_manifest,
            px4_sitl_report_path=px4_receiver_report,
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
            "Module Setup > Field Evidence Case > Create Template, then Register",
            "autonomy readiness field evidence desktop action",
        )
        if "create_field_evidence_template.sh" not in missing_field_actions[0]["command"]:
            raise AssertionError("autonomy readiness field evidence action should start with template creation")
        support_field_actions = [
            action
            for action in missing_field_direct["next_actions"]
            if action.get("check") == "support_bundle_bench_readiness.field_evidence"
        ]
        assert_equal(len(support_field_actions), 1, "autonomy readiness support field evidence subcheck action")
        assert_equal(
            support_field_actions[0]["desktop_action"],
            "Module Setup > Field Evidence Case > Create Template, then Register",
            "autonomy readiness support field evidence desktop action",
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
            feature_method_benchmark_report_path=feature_report,
            threshold_tuning_report_path=threshold_report,
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
            "Module Setup > Runtime Status, then Bench Report",
            "autonomy readiness runtime status desktop action",
        )
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
        )
        assert_equal(bundled_threshold_ready["status"], "passed", "autonomy readiness bundled threshold status")

        missing_threshold = evaluate_autonomy_readiness(
            research_doc_path=research_doc,
            implementation_plan_path=implementation_plan,
            support_bundle_path=support_manifest,
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
        command_bundle = missing_threshold["command_bundle"]
        if "./scripts/pi/run_threshold_tuning_report.sh" not in command_bundle["next_action_commands"]:
            raise AssertionError("autonomy readiness JSON missing next-action command bundle")
        if (
            "./scripts/pi/register_field_replay_case.sh --condition blur"
            not in command_bundle["field_collection_registration_commands"]
        ):
            raise AssertionError("autonomy readiness JSON missing field registration command bundle")
        handoff = render_handoff_markdown(missing_threshold)
        if "Goal completion: waiting on proof" not in handoff:
            raise AssertionError("autonomy handoff waiting state")
        if "Proof items:" not in handoff:
            raise AssertionError("autonomy handoff proof item summary")
        if "## Goal Proof Items" not in handoff:
            raise AssertionError("autonomy handoff proof item section")
        if "## Completion Blockers" not in handoff:
            raise AssertionError("autonomy handoff completion blocker section")
        if "threshold_tuning" not in handoff:
            raise AssertionError("autonomy handoff threshold blocker")
        if "Module Setup > Threshold Tuning" not in handoff:
            raise AssertionError("autonomy handoff next action")
        if "## Field Evidence Collection Checklist" not in handoff:
            raise AssertionError("autonomy handoff field checklist")
        if "- [ ] Good texture (`good_texture`)" not in handoff:
            raise AssertionError("autonomy handoff missing condition checklist")
        if "## Artifact Availability" not in handoff:
            raise AssertionError("autonomy handoff artifact availability")
        if "## Field Collection Plan" not in handoff:
            raise AssertionError("autonomy handoff field collection plan")
        if "## Plan Source Snapshot" not in handoff:
            raise AssertionError("autonomy handoff plan snapshot")
        if "implementation_plan" not in handoff:
            raise AssertionError("autonomy handoff missing implementation plan snapshot")
        if "- Registered: 1/8" not in handoff:
            raise AssertionError("autonomy handoff field collection plan summary")
        if "field_collection_plan.json" not in handoff:
            raise AssertionError("autonomy handoff field collection plan path")
        if "## Command Bundle" not in handoff:
            raise AssertionError("autonomy handoff command bundle")
        if "./scripts/pi/run_threshold_tuning_report.sh" not in handoff:
            raise AssertionError("autonomy handoff next-action command bundle")
        if "./scripts/pi/register_field_replay_case.sh --condition blur" not in handoff:
            raise AssertionError("autonomy handoff field registration command bundle")
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
            manifest = json.loads(archive.read("manifest.json"))
            assert_equal(manifest["readiness_status"], "failed", "autonomy evidence package status")
            assert_equal(
                manifest["plan_snapshot"]["schema_version"],
                "vision_nav_autonomy_plan_snapshot_v1",
                "autonomy evidence package plan snapshot schema",
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
        plan = create_field_collection_plan(
            manifest_path=active_manifest,
            output_path=base / "field_collection_plan.json",
            markdown_output_path=base / "field_collection_plan.md",
            site_name="Site A",
            bundle="field-bundles/site-a/mission_bundle",
            source_log="$HOME/DroneTransfer/outgoing/terrain-match/terrain_matches.jsonl",
        )
        assert_equal(plan["schema_version"], "vision_nav_field_collection_plan_v1", "field collection plan schema")
        assert_equal(plan["status"], "degraded", "field collection plan waits for field logs")
        assert_equal(plan["summary"]["required_count"], len(REQUIRED_FIELD_CONDITIONS), "field collection required count")
        assert_equal(plan["summary"]["placeholder_count"], len(REQUIRED_FIELD_CONDITIONS), "field collection placeholder count")
        assert_equal(plan["summary"]["registered_count"], 0, "field collection registered count")
        good_texture = next(item for item in plan["conditions"] if item["condition"] == "good_texture")
        assert_equal(good_texture["status"], "placeholder", "field collection placeholder status")
        if "VISION_NAV_FIELD_CASE_NAME" not in good_texture["register_command"]:
            raise AssertionError("Expected generated registration command to include case name")
        if not (base / "field_collection_plan.md").exists():
            raise AssertionError("Expected Markdown field collection plan")

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
        assert_equal(updated["summary"]["placeholder_count"], len(REQUIRED_FIELD_CONDITIONS) - 1, "field collection updated placeholder count")
        updated_good_texture = next(item for item in updated["conditions"] if item["condition"] == "good_texture")
        assert_equal(updated_good_texture["status"], "registered", "field collection registered status")
        markdown = render_field_collection_markdown(updated)
        if "- [x] Good texture" not in markdown:
            raise AssertionError("Expected registered condition to be checked in Markdown plan")


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
