# Legacy Prototype Components

This repository contains earlier prototype paths alongside the active learned
vision program. They are retained because historical replay logs, migration
tests, and evidence tooling still read them. They are not operator entry points
and must not be used for new Pi deployments, map production, data capture, or
vehicle tests.

## Active Path

Use only these components for current work:

- [`src/vision_nav/neural/`](../src/vision_nav/neural/): learned model/map
  contracts, ONNX execution, retrieval, correspondence, geometry, temporal
  evidence, and shadow telemetry.
- [`scripts/pi/run_vision_nav_shadow.sh`](../scripts/pi/run_vision_nav_shadow.sh):
  live or saved-frame learned shadow runtime.
- [`scripts/pi/record_training_capture.sh`](../scripts/pi/record_training_capture.sh):
  synchronized camera and read-only Pixhawk-attitude capture.
- [`scripts/pi/run_prop_off_preflight.sh`](../scripts/pi/run_prop_off_preflight.sh):
  safety-first Pi/camera/MAVLink bench check.
- [`docs/first-learned-shadow-run.md`](first-learned-shadow-run.md): first
  approved model/map/calibration runbook.

The active runtime is shadow-only. It writes evidence and status but does not
send PX4 `ODOMETRY`, control motors, or modify PX4 parameters.

## Retained For Replay Or Migration

- `src/vision_nav/features.py`, `matching.py`, `terrain_matcher.py`,
  `run_terrain_loop.py`, and the root-level terrain bundle tools: frozen
  classical-map/replay baseline only.
- `scripts/pi/run_terrain_nav_loop.sh`, `run_vision_nav_loop.sh`, and
  `validate_terrain_bundle.sh`: compatibility wrappers. They print a retirement
  notice and redirect to the learned validation or shadow runtime.
- `feature_method_benchmark.py`, historical `terrain_matches.jsonl`, and
  terrain replay helpers: evaluation evidence only. They do not select or
  supply an active vehicle method.
- `ros2_bridge.py`, `rosbag*`, `px4_sitl_*`, and simulator-derived documents:
  historical utilities. ROS 2, Gazebo, and PX4 SITL are not active scaffolding.
- `barometer.py` and legacy autonomy/evidence helpers: retained compatibility
  code. They are not required for the camera + PX4-attitude learned shadow
  milestone.

## Migration Rule

Never convert a classical descriptor bundle into a learned MapRelease. Build a
new MapRelease with its COG/STAC provenance, MegaLoc index, XFeat cache,
calibration compatibility, model binding, and checksums. Likewise, retain old
logs for model evaluation but collect new synchronized captures with
`record_training_capture.sh`.
