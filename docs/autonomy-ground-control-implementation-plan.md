# Autonomy And Ground Control Implementation Plan

This plan executes the recommendations from
[Autonomy And Ground Control Research](autonomy-ground-control-research.md)
without changing the project scope. The target remains a low-cost
GNSS-denied terrain-navigation module plus desktop setup software.

## Delivery Tracks

### Track 1: External Position Output

Goal: make every autopilot output path share one tested conversion layer.

Status:

- In progress: `vision_nav.external_position` defines local ENU input,
  local NED/FRD output, MAVLink covariance ordering, yaw conversion, and
  payload shapes for `VISION_POSITION_ESTIMATE` and `ODOMETRY`.
- In progress: `mavlink_bridge.py` now uses the shared conversion layer for
  existing `VISION_POSITION_ESTIMATE` sends.
- In progress: `send_odometry_match_result()` and
  `VISION_NAV_MAVLINK_MESSAGE=odometry` are available for bench testing the
  richer PX4 external-vision path.
- Done: PX4 external-vision bench guidance is documented in
  [PX4 External Vision Bench Guide](px4-external-vision-bench.md).
- Done: runtime logs include `external_position_health` snapshots with message
  type, send rate, latency, covariance warnings, and skip reasons.
- Done: `ODOMETRY` output includes reset-counter tracking for estimator reset
  epochs, map changes, and backward timestamps.

Next tasks:

1. Add PX4 SITL tests that confirm EKF2 receives the selected message path.
2. Add live ROS 2 wrapper launch profiles for camera, matcher, estimator,
   health, and external-position output.

Acceptance checks:

- Unit tests prove ENU to NED axis mapping, yaw conversion, and covariance
  placement.
- Existing MAVLink pose output continues to pass.
- Rejected or incomplete terrain matches are not sent.

### Track 2: ROS 2 Companion Runtime

Goal: make ROS 2 the modular runtime spine while keeping the direct Python CLI
and MAVLink path for simple Pi deployments.

Status:

- In progress: `vision-nav-ros2-replay-log` converts accepted runtime log
  records into ROS-compatible `nav_msgs/Odometry` dictionaries and
  `diagnostic_msgs`-style health records.
- In progress: the same command can publish with `rclpy` when ROS 2 packages are
  installed and sourced.
- In progress: `vision-nav-run-terrain-loop --ros2-publish` can publish live
  odometry and diagnostics during camera/matcher runtime.
- In progress: repo-local launch files under `ros2/launch/` start live terrain
  runtime publishing or replay publishing with repeatable arguments.

Tasks:

1. Add PX4 SITL launch profile arguments once SITL receiver verification is
   available.
2. Add a package-style ROS 2 entrypoint if the project later needs colcon-native
   packaging.
3. Add Micro XRCE-DDS Agent setup checks to Pi diagnostics.
4. Add rosbag replay for terrain logs and camera frames.

Acceptance checks:

- ROS 2 topics can replay a saved frame log on the desktop.
- PX4 SITL can receive external-position output through ROS 2 or direct MAVLink.
- The direct Python `vision-nav-run-terrain-loop` command remains usable.

### Track 3: Terrain Map Bundle Pipeline

Goal: make map preparation reliable enough for customer field use.

Tasks:

1. Add COG/GeoTIFF/STAC validation to the bundle builder.
2. Record CRS, GSD, bounds, source, checksums, overviews, and tile count in the
   bundle health report.
3. Add optional DEM/DSM assets for planning and vertical sanity checks.
4. Add feature-density and map-quality heatmaps.
5. Add hierarchical tile retrieval before local ORB/AKAZE matching.

Acceptance checks:

- Invalid georeference or missing map metadata blocks bundle upload.
- The desktop app shows map health before the Pi uses the bundle.
- A wrong-map replay produces rejected matches, not low-covariance outputs.

### Track 4: Desktop Setup And Mission UX

Goal: make the customer workflow guided, diagnosable, and hard to misuse.

Tasks:

1. Build a setup wizard that chains Pi discovery, runtime verification, camera
   test, MAVLink test, time sync, calibration, map upload, and bench test.
2. Add QGroundControl-style plan state: unsaved, not uploaded, uploaded,
   stale bundle, and invalid map/mission mismatch.
3. Add UgCS-style terrain planning: DEM/DSM import, terrain profile, GSD/AGL
   checks, offline cache state, and route segmentation.
4. Add GNSS-denied readiness actions: set/reset map position, heading, home,
   and estimator health.

Acceptance checks:

- A new operator can get from fresh Pi to bench report without shell commands
  except the initial OS flash.
- Runtime output clearly shows active map, active output path, estimator health,
  and last accepted/rejected match reason.

### Track 5: Validation And Product Risk Controls

Goal: prove the estimator rejects bad information before field use.

Tasks:

1. Maintain replay cases for good texture, low texture, blur, seasonal change,
   altitude/scale change, repeated patterns, and wrong map.
2. Compare ORB/AKAZE against optional higher-compute features on the same logs.
3. Add acceptance gates for inliers, reprojection error, scale confidence,
   geometry sanity, motion consistency, covariance inflation, and wrong-map
   rejection.
4. Generate support bundles with app version, Pi version, autopilot metadata,
   map manifest, config, logs, and summaries.

Acceptance checks:

- CI or local smoke tests cover accepted, degraded, and rejected localization
  cases.
- Support bundles are enough to reproduce a failed bench run offline.

## Execution Order

1. External-position conversion and MAVLink payloads.
2. PX4 external-vision guidance and SITL smoke path.
3. Desktop setup wizard and runtime health display.
4. COG/STAC/GeoTIFF bundle validation.
5. ROS 2 package wrapper and replay.
6. Hierarchical tile retrieval and map-quality heatmap.
7. ArduPilot adapter after PX4 bench validation.

The first execution item is now represented in code by
`src/vision_nav/external_position.py` and the updated MAVLink bridge tests.
