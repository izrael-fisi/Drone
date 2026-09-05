import numpy as np

from vision_nav.adapters import DeterministicStub
from vision_nav.estimation.factor_graph import VisionFactorGraph
from vision_nav.estimation.geometry import GeometryPolicy, RobustGeometry
from vision_nav.estimation.hypotheses import HypothesisBank
from vision_nav.px4 import Px4VisionBridge
from vision_nav.runtime import RuntimePolicy, VisionNavigationRuntime
from vision_nav.types import (CameraModel, FramePacket, GeometricEstimate, GlobalObservation,
                              NavigationMode, Pose3)


def test_geometry_rejects_insufficient_matches():
    from vision_nav.types import CorrespondenceSet
    frame = FramePacket(0, 0., None, CameraModel(1, 1, 0, 0, 2, 2))
    matches = CorrespondenceSet(np.zeros((3, 2)), np.zeros((3, 2)), np.ones(3), "sparse")
    assert not RobustGeometry().estimate(frame, "tile", matches).valid


def test_hypothesis_requires_temporal_support():
    est = GeometricEstimate("a", Pose3(np.zeros(3), np.array([0, 0, 0, 1.])), "pnp",
                            np.ones(80, dtype=bool), .2, np.eye(6), True, 1.0)
    bank = HypothesisBank(accept_confidence=.1)
    observation = GlobalObservation(0., est, .95, .95)
    bank.update([observation])
    assert bank.accepted() is None
    bank.update([observation])
    assert bank.accepted() is not None


def test_px4_publish_only_in_tracking_mode():
    from vision_nav.types import NavEstimate
    nav = NavEstimate(1., Pose3(np.array([2., 3., 4.]), np.array([0., 0., 0., 1.])),
                      np.zeros(3), np.eye(6), .9, NavigationMode.TRACKING, "x",
                      body_frame="FRD", covariance_convention="position_then_left_rotation_world",
                      clock_domain="host_monotonic")
    msg = Px4VisionBridge().to_message(nav)
    assert msg is not None
    assert np.allclose(msg.position_ned_m, [3, 2, -4])
    assert Px4VisionBridge().to_message(nav.__class__(**{**nav.__dict__, "mode": NavigationMode.RECOVERING})) is None


def test_demo_runtime_withholds_unbound_backend():
    stub = DeterministicStub()
    runtime = VisionNavigationRuntime(stub, stub, stub, stub, RobustGeometry(GeometryPolicy(min_inliers=12)),
        VisionFactorGraph(), RuntimePolicy(retrieval_top_k=1, global_recovery_period_s=.01, min_accept_confidence=.1))
    camera = CameraModel(960, 960, 640, 360, 1280, 720)
    for i in range(4):
        nav = runtime.ingest_frame(
            FramePacket(
                i,
                (i + 1) * .1,
                None,
                camera,
                calibration_id="test-camera-v1",
                clock_domain="test-monotonic",
            )
        )
    assert nav.mode == NavigationMode.NO_GLOBAL_FIX
    assert nav.confidence == 0
    assert nav.diagnostics["publication_withheld"] is True
    assert nav.diagnostics["position_authority"] is False
    assert nav.diagnostics["global_updates"] == 0
    assert Px4VisionBridge().to_message(nav) is None


def test_runtime_withholds_anonymous_or_reordered_frames():
    stub = DeterministicStub()
    runtime = VisionNavigationRuntime(
        stub,
        stub,
        stub,
        stub,
        RobustGeometry(GeometryPolicy(min_inliers=12)),
        VisionFactorGraph(),
        RuntimePolicy(retrieval_top_k=1, global_recovery_period_s=.01, min_accept_confidence=.1),
    )
    camera = CameraModel(960, 960, 640, 360, 1280, 720)
    anonymous = runtime.ingest_frame(FramePacket(0, .1, None, camera))
    assert anonymous.mode == NavigationMode.NO_GLOBAL_FIX
    assert anonymous.diagnostics["last_rejection_reason"] == "missing_calibration_id"

    accepted = runtime.ingest_frame(
        FramePacket(1, .2, None, camera, calibration_id="test-camera-v1", clock_domain="test-monotonic")
    )
    assert accepted.mode == NavigationMode.NO_GLOBAL_FIX
    reordered = runtime.ingest_frame(
        FramePacket(2, .2, None, camera, calibration_id="test-camera-v1", clock_domain="test-monotonic")
    )
    assert reordered.mode == NavigationMode.NO_GLOBAL_FIX
    assert reordered.diagnostics["last_rejection_reason"] == "nonmonotonic_frame_timestamp"


def test_orchestrator_cannot_override_backend_publication_veto():
    from dataclasses import replace

    stub = DeterministicStub()
    backend = VisionFactorGraph()
    runtime = VisionNavigationRuntime(stub, stub, stub, stub, RobustGeometry(), backend)
    runtime.hypotheses.accepted = lambda: object()
    misleading = replace(backend.optimize(1.), confidence=1., covariance=np.eye(6),
                         diagnostics={"publication_withheld": True, "covariance_trace_m2": 3.})
    assert runtime._apply_mode(misleading).mode == NavigationMode.NO_GLOBAL_FIX


def test_px4_publication_requires_fresh_compatible_tracking_estimate():
    from vision_nav.contracts import CompatibilityReport
    from vision_nav.px4 import Px4VisionBridge
    from vision_nav.types import NavEstimate

    estimate = NavEstimate(
        10.0,
        Pose3(np.array([2.0, 3.0, 4.0]), np.array([0.0, 0.0, 0.0, 1.0])),
        np.zeros(3),
        np.eye(6),
        .9,
        NavigationMode.TRACKING,
        "tile-a",
        calibration_id="test-camera-v1", map_version="map@1", model_version="model@1",
        body_frame="FRD", covariance_convention="position_then_left_rotation_world",
        clock_domain="host_monotonic",
    )
    bridge = Px4VisionBridge()
    compatible = CompatibilityReport(True, camera_calibration_id="test-camera-v1",
                                     map_version="map@1", model_version="model@1")
    assert bridge.publication_decision(
        estimate, compatibility=compatible, transport_healthy=True, now_s=10.1
    ).publish
    assert bridge.publication_decision(
        estimate, compatibility=compatible, transport_healthy=True, now_s=11.0
    ).reason == "estimate_stale"
    assert bridge.publication_decision(
        estimate,
        compatibility=CompatibilityReport(False, ("model_mismatch",)),
        transport_healthy=True,
        now_s=10.1,
    ).reason == "incompatible_map_camera_or_model"


def test_map_release_rejects_mixed_calibration_or_model_versions():
    from vision_nav.mapping import MapRelease, check_map_release_compatibility

    release = MapRelease.from_dict(
        {
            "map_id": "coastal-test",
            "version": "2026.08.30",
            "epoch_utc": "2026-08-30T00:00:00Z",
            "crs": "ENU:37.7749,-122.4194",
            "local_origin_enu": [0.0, 0.0, 0.0],
            "gsd_m_per_px": 0.12,
            "tile_index_uri": "index/tiles.jsonl",
            "descriptor_dimension": 8448,
            "descriptor_model_version": "salad-v1",
            "sparse_model_version": "superpoint-v1",
            "dense_model_version": "roma-v1",
            "compatible_calibration_ids": ["test-camera-v1"],
            "checksums_sha256": {"index/tiles.jsonl": "abc"},
        }
    )
    compatible = check_map_release_compatibility(
        release,
        calibration_id="test-camera-v1",
        descriptor_dimension=8448,
        descriptor_model_version="salad-v1",
        sparse_model_version="superpoint-v1",
        dense_model_version="roma-v1",
    )
    assert compatible.compatible
    incompatible = check_map_release_compatibility(
        release,
        calibration_id="other-camera",
        descriptor_dimension=8448,
        descriptor_model_version="salad-v2",
        sparse_model_version="superpoint-v1",
        dense_model_version="roma-v1",
    )
    assert not incompatible.compatible
    assert "camera_calibration_not_compatible_with_map" in incompatible.reasons
    assert "global_descriptor_model_version_mismatch" in incompatible.reasons
