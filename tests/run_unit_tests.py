from __future__ import annotations

import json
import math
from pathlib import Path
import sqlite3
import struct
import tempfile
import time
import zipfile

import numpy as np

from vision_nav.barometer import BarometerSample, BarometerTracker, pressure_to_altitude_m
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
from vision_nav.geospatial_health import geospatial_health_report
from vision_nav.georef import SimpleGeoReference, build_georef_from_cli, georef_from_json, georef_to_json
from vision_nav.mavlink_bridge import MavlinkVisionBridge, parse_mavlink_endpoint
from vision_nav.ros2_bridge import DIAG_ERROR, DIAG_OK, diagnostic_status_from_health, odometry_dict_from_match_result
from vision_nav.replay_gates import evaluate_replay_records
from vision_nav.summarize_match_log import summarize_records
from vision_nav.support_bundle import create_support_bundle
from vision_nav.terrain_estimator import TerrainEstimator
from vision_nav.terrain_tiles import create_tile_schema, tile_origins
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


def create_minimal_terrain_bundle(root: Path, *, include_tile_index: bool = True) -> Path:
    bundle = root / "bundle"
    (bundle / "ortho").mkdir(parents=True)
    (bundle / "imagery" / "tiles").mkdir(parents=True)
    (bundle / "index" / "descriptors").mkdir(parents=True)
    write_minimal_png(bundle / "ortho" / "map.png", 100, 60)
    write_minimal_png(bundle / "imagery" / "tiles" / "tile_000000.png", 64, 60)
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
                "terrain_bundle": {
                    "version": "0.1.0",
                    "tile_index_path": "index/tiles.sqlite",
                    "tile_size_px": 64,
                    "overlap_px": 8,
                    "local_origin": {"latitude": 40.0, "longitude": -75.0, "east_m": 0.0, "north_m": 0.0},
                    "crs": "EPSG:4326",
                    "gsd_m": 0.5,
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
                "assets": {
                    "orthophoto": {"href": "ortho/map.png"},
                    "tile_index": {"href": "index/tiles.sqlite"},
                },
            }
        )
    )
    return bundle


class SkipTest(Exception):
    pass


def test_pixel_to_local_default_north_up_axes() -> None:
    georef = SimpleGeoReference(origin_lat=40.0, origin_lon=-75.0, gsd_m=0.5)
    east_m, north_m = georef.pixel_to_local_m(10, 8)
    assert_equal(east_m, 5.0, "east_m")
    assert_equal(north_m, -4.0, "north_m")


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

        written = write_checksum_file(bundle)
        assert_equal(written["entry_count"], 3, "checksum entry count")
        verified = verify_checksum_file(bundle)
        assert_equal(verified["status"], "passed", "checksum verified status")

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
        bundle = create_minimal_terrain_bundle(Path(tmp))

        report = geospatial_health_report(bundle)
        assert_equal(report["status"], "passed", "geospatial health status")
        assert_equal(report["raster"]["format"], "PNG", "geospatial raster format")
        assert_equal(report["tile_index"]["tile_count"], 1, "geospatial tile count")
        assert_equal(report["map_quality"]["estimated_pi_runtime_cost"], "low", "geospatial runtime cost")
        if report["georef"]["bounds"]["width_m"] <= 0:
            raise AssertionError("Expected geospatial bounds width to be positive")


def test_support_bundle_collects_manifest_health_logs_and_summary() -> None:
    with tempfile.TemporaryDirectory() as tmp:
        root = Path(tmp)
        bundle = create_minimal_terrain_bundle(root)
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
        replay_manifest = root / "replay_cases.json"
        replay_manifest.write_text(
            json.dumps(
                {
                    "version": "0.1.0",
                    "cases": [
                        {
                            "case_name": "unit-good",
                            "expected": "good_map",
                            "log": str(log),
                            "notes": "Synthetic accepted support bundle case.",
                        }
                    ],
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
            replay_case_manifest_path=str(replay_manifest),
        )
        assert_equal(result["status"], "passed", "support bundle status")
        zip_path = Path(result["zip_path"])
        if not zip_path.exists():
            raise AssertionError("Expected support bundle zip to exist")
        with zipfile.ZipFile(zip_path) as archive:
            names = set(archive.namelist())
        for expected in {
            "support_manifest.json",
            "bundle/manifest.json",
            "bundle/bundle_health.generated.json",
            "logs/terrain_matches.jsonl",
            "summaries/terrain_matches.summary.json",
            "summaries/replay_gates/unit-good.gate.json",
        }:
            if expected not in names:
                raise AssertionError(f"Missing {expected} from support bundle zip")
        manifest = json.loads(Path(result["manifest_path"]).read_text())
        assert_equal(manifest["logs"]["summaries"][0]["accepted_rate"], 1.0, "support log accepted rate")
        assert_equal(manifest["replay_gates"]["status"], "passed", "support replay gate status")
        assert_equal(manifest["replay_gates"]["reports"][0]["status"], "passed", "support replay gate report")


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
        test_external_position_payloads,
        test_external_position_stream_health,
        test_ros2_odometry_and_diagnostics_adapters,
        test_ros2_launch_profiles_static,
        test_camera_health_report_on_synthetic_image,
        test_bundle_checksums_detect_changed_file,
        test_validate_bundle_passes_complete_bundle,
        test_geospatial_health_report_validates_stac_tiles_and_bounds,
        test_support_bundle_collects_manifest_health_logs_and_summary,
        test_replay_gates_pass_good_map_and_fail_wrong_map_acceptance,
        test_geospatial_health_blocks_missing_georef,
        test_terrain_tile_origins_cover_edges,
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
