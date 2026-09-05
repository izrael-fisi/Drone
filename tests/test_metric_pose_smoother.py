"""Synthetic factor tests, not evidence of camera/VIO or flight accuracy."""

from dataclasses import replace

import numpy as np
import pytest

from vision_nav.estimation.pose_smoother import MetricPoseSmoother, SmootherConfig

T0 = 1_000_000_000
STEP = 100_000_000


def pose(x=0., y=0., z=0., yaw=0.):
    c, s = np.cos(yaw), np.sin(yaw)
    return np.array([[c, -s, 0, x], [s, c, 0, y], [0, 0, 1, z], [0, 0, 0, 1.]])


def config(**kwargs):
    return replace(SmootherConfig(
        "map@1", "model@1", "camera@1", "vio@1", "camera-clock@1", "noise@1",
        np.eye(4), np.diag([.001] * 3 + [.25] * 3)), **kwargs)


def identity(c, fields):
    return {**{field: getattr(c, field) for field in fields},
            "covariance_convention": "right_tangent_rx_ry_rz_tx_ty_tz"}


def odometry(c, start=T0, end=T0+STEP, delta=None):
    return {**identity(c, ("vio_release_id", "calibration_id", "clock_id", "covariance_model_id")),
            "from_timestamp_ns": start, "timestamp_ns": end,
            "previous_body_T_body": (pose(x=1) if delta is None else delta).tolist(),
            "covariance": (np.eye(6) * .01).tolist(), "metric_scale_verified": True}


def fix(c, ns=T0, body=None):
    return {**identity(c, ("map_release_id", "model_release_id", "calibration_id", "clock_id", "covariance_model_id")),
            "timestamp_ns": ns, "fix_id": f"fix-{ns}", "hypothesis_state": "unique_temporal_support",
            "geometry_mode": "pnp_3d", "temporal_support": 2,
            "map_T_camera": ((pose() if body is None else body) @ c.body_T_camera).tolist(),
            "covariance": (np.eye(6) * .02).tolist()}


def ready(c=None):
    c = c or config()
    s = MetricPoseSmoother(c)
    s.start(T0)
    return s


def assert_shadow(result):
    assert result["navigation_mode"] == "NO_GLOBAL_FIX"
    assert result["position_authority"] is False
    assert result["px4_external_vision"] == "disabled"
    assert result["fault"] is None


def test_local_motion_composes_se3_without_inventing_global_pose():
    s = MetricPoseSmoother(config())
    assert s.snapshot(T0)["estimator_state"] == "NOT_STARTED"
    s.start(T0)
    first = pose(x=1, yaw=np.pi / 2)
    second = pose(x=2, yaw=-np.pi / 4)
    s.add_odometry(odometry(s.config, delta=first))
    s.add_odometry(odometry(s.config, T0+STEP, T0+2*STEP, second))
    out = s.snapshot(T0+2*STEP)
    np.testing.assert_allclose(out["raw_local_T_body"], first @ second, atol=1e-10)
    assert out["estimator_state"] == "LOCAL_ONLY"
    assert out["map_T_body"] is None and out["covariance"] is None
    assert_shadow(out)


def test_delayed_map_fix_uses_capture_keyframe_and_camera_lever_arm():
    s = ready(config(body_T_camera=pose(.3, -.2, .1, np.pi/2)))
    first, second = pose(1, yaw=.4), pose(.6, .1, yaw=-.2)
    s.add_odometry(odometry(s.config, delta=first))
    s.add_odometry(odometry(s.config, T0+STEP, T0+2*STEP, second))
    map_body_at_capture = pose(30, -20, 50, .8)
    s.add_map_fix(fix(s.config, T0+STEP, map_body_at_capture))
    out = s.snapshot(T0+2*STEP)
    assert out["estimator_state"] == "SMOOTHED"
    np.testing.assert_allclose(out["map_T_body"], map_body_at_capture @ second, atol=1e-7)
    np.testing.assert_allclose(out["raw_local_T_body"], first @ second, atol=1e-10)
    assert out["map_fix_age_s"] == .1
    assert np.linalg.eigvalsh(out["covariance"]).min() > 0
    assert_shadow(out)


def test_camera_covariance_lever_arm_and_axis_conversion():
    s = ready(config(body_T_camera=pose(.3, -.2, .1, .5)))
    body = pose(4, 5, 20, .8)
    event = fix(s.config, body=body)
    s.add_map_fix(event)
    out = s.snapshot(T0)
    a = s.body_T_camera.AdjointMap()
    p = s.g.Pose3(body)
    common = p.inverse().AdjointMap()
    right = a @ np.array(event["covariance"]) @ a.T + common @ s.config.map_systematic_covariance @ common.T
    axes = np.zeros((6, 6))
    axes[:3, 3:] = axes[3:, :3] = body[:3, :3]
    np.testing.assert_allclose(out["covariance"], axes @ right @ axes.T, atol=1e-8)


def test_duplicate_map_evidence_never_reduces_uncertainty():
    s = ready()
    event = fix(s.config)
    s.add_map_fix(event)
    before = s.snapshot(T0)
    for repeated in (event, {**event, "fix_id": "changed-id"}):
        with pytest.raises(ValueError, match="Duplicate"):
            s.add_map_fix(repeated)
    assert s.snapshot(T0) == before
    s.add_odometry(odometry(s.config))
    with pytest.raises(ValueError, match="Duplicate"):
        s.add_map_fix({**fix(s.config, T0+STEP, pose(1)), "fix_id": event["fix_id"]})


@pytest.mark.parametrize("field,value", [
    ("map_release_id", "wrong"), ("model_release_id", "wrong"), ("calibration_id", "wrong"),
    ("clock_id", "wrong"), ("covariance_model_id", "wrong"), ("covariance_convention", "unknown"),
    ("temporal_support", 1), ("temporal_support", True), ("hypothesis_state", "ambiguous"),
    ("geometry_mode", "homography"), ("timestamp_ns", T0+1), ("timestamp_ns", True),
    ("fix_id", ""), ("covariance", np.zeros((6, 6)).tolist()),
    ("covariance", (np.eye(6) * -1).tolist()), ("covariance", np.full((6, 6), np.nan).tolist()),
    ("map_T_camera", np.zeros((4, 4)).tolist()),
])
def test_bad_map_factors_leave_state_unchanged(field, value):
    s = ready()
    before = s.snapshot(T0)
    with pytest.raises(ValueError):
        s.add_map_fix({**fix(s.config), field: value})
    assert s.snapshot(T0) == before


@pytest.mark.parametrize("field,value", [
    ("vio_release_id", "wrong"), ("calibration_id", "wrong"), ("clock_id", "wrong"),
    ("covariance_model_id", "wrong"), ("metric_scale_verified", False),
    ("from_timestamp_ns", T0-1), ("timestamp_ns", T0), ("timestamp_ns", T0+2_000_000_000),
    ("covariance", np.zeros((6, 6)).tolist()), ("previous_body_T_body", np.zeros((4, 4)).tolist()),
])
def test_bad_odometry_leaves_state_unchanged(field, value):
    s = ready()
    before = s.snapshot(T0)
    with pytest.raises(ValueError):
        s.add_odometry({**odometry(s.config), field: value})
    assert s.snapshot(T0) == before


def test_outlier_gate_retains_prior_and_can_accept_corrected_evidence():
    s = ready()
    s.add_map_fix(fix(s.config))
    s.add_odometry(odometry(s.config))
    before = s.snapshot(T0+STEP)
    with pytest.raises(ValueError, match="innovation rejected"):
        s.add_map_fix(fix(s.config, T0+STEP, pose(200, 100, 50, 2.)))
    after = s.snapshot(T0+STEP)
    assert after["accepted_fix_count"] == 1
    assert after["last_map_fix_timestamp_ns"] == T0
    np.testing.assert_allclose(after["covariance"], before["covariance"])
    s.add_map_fix(fix(s.config, T0+STEP, pose(1)))
    assert s.snapshot(T0+STEP)["accepted_fix_count"] == 2


def test_fixed_lag_bounds_state_and_preserves_map_systematic_floor():
    s = ready(config(lag_s=.5, max_odometry_gap_s=.2, max_keyframes=8))
    s.add_map_fix(fix(s.config))
    for i in range(1, 60):
        ns = T0+i*STEP
        s.add_odometry(odometry(s.config, ns-STEP, ns, pose()))
        s.add_map_fix(fix(s.config, ns))
        out = s.snapshot(ns)
        assert_shadow(out)
        assert out["active_keyframes"] <= 6
        assert len(s.used_fixes) <= 6
        assert np.linalg.eigvalsh(np.array(out["covariance"])[:3, :3]).min() >= .25
    with pytest.raises(ValueError, match="retained keyframe"):
        s.add_map_fix(fix(s.config))
    assert s.snapshot(ns)["accepted_fix_count"] == 60


def test_map_stale_and_local_stale_have_independent_clocks():
    s = ready(config(max_map_age_s=.15, max_local_age_s=.05))
    s.add_map_fix(fix(s.config))
    s.add_odometry(odometry(s.config))
    s.add_odometry(odometry(s.config, T0+STEP, T0+2*STEP))
    assert s.snapshot(T0+2*STEP)["estimator_state"] == "MAP_STALE"
    assert s.snapshot(T0+3*STEP)["estimator_state"] == "LOCAL_STALE"
    with pytest.raises(ValueError, match="query clock"):
        s.snapshot(T0)


def test_covariance_grows_through_marginalization_without_new_fixes():
    s = ready(config(lag_s=.5, max_odometry_gap_s=.2, max_keyframes=8))
    s.add_map_fix(fix(s.config))
    for i in range(1, 100):
        ns = T0+i*STEP
        s.add_odometry(odometry(s.config, ns-STEP, ns, pose()))
        out = s.snapshot(ns)
        assert_shadow(out)
        expected = np.diag([.25+.02+i*.01] * 3 + [.001+.02+i*.01] * 3)
        np.testing.assert_allclose(out["covariance"], expected, atol=1e-7)


def test_replay_is_deterministic_records_rejections_and_never_overwrites(tmp_path):
    import hashlib
    import json
    from dataclasses import asdict
    from vision_nav.estimation.replay_smoother import replay, main

    c = config()
    config_data = asdict(c)
    for field in ("body_T_camera", "map_systematic_covariance"):
        config_data[field] = config_data[field].tolist()
    config_path, log = tmp_path / "config.json", tmp_path / "input.jsonl"
    config_path.write_text(json.dumps(config_data))
    events = [{"type": "start", "timestamp_ns": T0, "received_timestamp_ns": T0},
              {**odometry(c), "type": "odometry", "received_timestamp_ns": T0+STEP},
              {**fix(c), "type": "map_fix", "received_timestamp_ns": T0+STEP},
              {**fix(c), "type": "map_fix", "received_timestamp_ns": T0+STEP},
              {"type": "tick", "timestamp_ns": T0+10*STEP, "received_timestamp_ns": T0+10*STEP}]
    log.write_text("".join(json.dumps(e) + "\n" for e in events))
    a, b = tmp_path / "a", tmp_path / "b"
    report = replay(config_path, log, a)
    assert report == replay(config_path, log, b)
    assert (a / "decisions.jsonl").read_bytes() == (b / "decisions.jsonl").read_bytes()
    assert report["decisions"] == {"accepted": 4, "rejected": 1}
    assert report["input_sha256"] == hashlib.sha256(log.read_bytes()).hexdigest()
    assert report["final_snapshot"]["estimator_state"] == "LOCAL_STALE"
    assert_shadow(report["final_snapshot"])
    with pytest.raises(FileExistsError):
        replay(config_path, log, a)
    assert main(["--config", str(config_path), "--log", str(log), "--output", str(tmp_path / "cli")]) == 2


def test_replay_bad_events_and_large_lines_fail_closed(tmp_path):
    import json
    from dataclasses import asdict
    from vision_nav.estimation.replay_smoother import replay, MAX_EVENT_BYTES

    data = asdict(config())
    data["body_T_camera"] = data["body_T_camera"].tolist()
    data["map_systematic_covariance"] = data["map_systematic_covariance"].tolist()
    cfg, log = tmp_path / "config.json", tmp_path / "log.jsonl"
    cfg.write_text(json.dumps(data))
    log.write_text('null\n{"bad":NaN}\n{"type":"tick","type":"start"}\n'
                   '{"type":"start","received_timestamp_ns":1,"timestamp_ns":2}\n')
    report = replay(cfg, log, tmp_path / "bad")
    assert report["decisions"] == {"accepted": 0, "rejected": 4}
    assert report["final_snapshot"]["estimator_state"] == "NOT_STARTED"
    log.write_text("x" * (MAX_EVENT_BYTES+1))
    report = replay(cfg, log, tmp_path / "oversize")
    assert not report["complete"]
    assert report["input_sha256"] is None


def test_late_older_fix_does_not_refresh_last_fix_time():
    s = ready()
    s.add_odometry(odometry(s.config))
    s.add_map_fix(fix(s.config, T0+STEP, pose(1)))
    s.add_map_fix(fix(s.config, T0, pose()))
    assert s.snapshot(T0+STEP)["last_map_fix_timestamp_ns"] == T0+STEP


def test_capacity_rejection_does_not_drop_relative_motion():
    s = ready(config(max_keyframes=3))
    s.add_odometry(odometry(s.config))
    s.add_odometry(odometry(s.config, T0+STEP, T0+2*STEP))
    with pytest.raises(ValueError, match="capacity"):
        s.add_odometry(odometry(s.config, T0+2*STEP, T0+3*STEP))
    assert s.snapshot(T0+3*STEP)["estimate_timestamp_ns"] == T0+2*STEP
    with pytest.raises(ValueError, match="already started"):
        s.start(T0+3*STEP)


def test_optimizer_failure_is_latched():
    s = ready()

    class BrokenOptimizer:
        def calculateEstimate(self):
            return previous.calculateEstimate()

        def update(self, *args):
            raise RuntimeError("synthetic failure")

    previous = s.smoother
    s.smoother = BrokenOptimizer()
    with pytest.raises(RuntimeError, match="optimizer_failure"):
        s.add_odometry(odometry(s.config))
    out = s.snapshot(T0+STEP)
    assert out["estimator_state"] == "FAULT"
    assert out["map_T_body"] is None
    with pytest.raises(RuntimeError, match="latched"):
        s.add_map_fix(fix(s.config))


@pytest.mark.parametrize("updates", [{"lag_s": "5"}, {"lag_s": float("nan")}, {"max_keyframes": True},
                                      {"map_release_id": ""}, {"lag_s": .5},
                                      {"map_systematic_covariance": np.zeros((6, 6))}])
def test_config_rejects_invalid_limits_and_unknown_covariance(updates):
    with pytest.raises(ValueError):
        config(**updates)
