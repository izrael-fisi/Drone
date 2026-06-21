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

Status:

- In progress: `vision_nav.geospatial_health` reports map georeference,
  CRS/GSD, raster metadata, lightweight COG/GeoTIFF readiness, STAC asset
  validity, tile-index readiness, feature counts, feature-density quality,
  estimated Pi runtime cost, local bounds, and blocking issues.
- In progress: `vision-nav-build-terrain-bundle` writes `bundle_health.json`
  and returns the same health summary to the desktop app after bundle build.
- In progress: `vision-nav-validate-bundle` and
  `vision-nav-validate-terrain-bundle` include geospatial health checks for
  terrain bundles.
- In progress: Mission Planner shows bundle map health, tile count, feature
  count, and GSD after a bundle build.
- In progress: `bundle_health.json` now includes checksum status and source
  provenance while excluding generated health reports from checksum coverage to
  avoid self-referential mismatches.
- In progress: Mission Planner bundle results display checksum status, covered
  file count, map source, source filename/name, georeference source, CRS, and
  georeference confidence.
- In progress: terrain bundles now discover optional `elevation/dem.tif` and
  `elevation/dsm.tif` assets, declare them in manifest/STAC/runtime config, and
  report elevation-readiness in bundle health and desktop/support summaries.
- In progress: Maps can attach optional DEM/DSM GeoTIFFs to saved map sources
  so Mission Planner bundle builds carry elevation assets into runtime bundles.
- In progress: terrain matching now reports hierarchical tile retrieval
  metadata and uses prior-local radius search when a pose prior exists, or
  spatially distributed coarse candidates at startup with no prior.
- In progress: terrain tile descriptors now include a compact grayscale global
  descriptor, and runtime matching reranks coarse/prior candidates by visual
  descriptor distance before local ORB/AKAZE homography.

Tasks:

1. Add stricter GDAL-backed COG/GeoTIFF validation when GDAL is available.
2. Add checksum/source-provenance status to downloaded support-bundle browsing.
3. Add terrain profile preview and AGL/GSD checks for imported DEM/DSM assets.
4. Turn the current feature-density summary into operator-facing map-quality
   heatmaps.
5. Benchmark the current lightweight global descriptor against optional neural
   retrieval descriptors on replay logs.

Acceptance checks:

- Invalid georeference blocks terrain bundle validation.
- The desktop app shows map health before the Pi uses the bundle.
- A wrong-map replay produces rejected matches, not low-covariance outputs.

### Track 4: Desktop Setup And Mission UX

Goal: make the customer workflow guided, diagnosable, and hard to misuse.

Status:

- In progress: Mission Planner now tracks a session-local plan fingerprint and
  shows invalid, not built, stale bundle, not uploaded, uploaded, or
  bundle-ready state after build/upload actions.
- In progress: plan-state checks include mission/map readiness, selected map
  source, output bundle path, remote bundle path, QGC plan content, and desktop
  mission JSON content.

Tasks:

1. Build a setup wizard that chains Pi discovery, runtime verification, camera
   test, MAVLink test, time sync, calibration, map upload, and bench test.
2. Persist plan-state history across app restarts and add an explicit unsaved
   file indicator for imported/exported `.plan` files.
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

Status:

- In progress: `vision_nav.support_bundle` creates a zip package containing
  runtime metadata, git/app version state, bundle manifest/config/health,
  selected logs, generated log summaries, optional autopilot metadata, and
  optional full map assets.
- In progress: `scripts/pi/create_support_bundle.sh` packages the default Pi
  terrain/runtime/replay logs into `~/DroneTransfer/outgoing/support-bundles/`.
- In progress: Devices and Mission Planner runtime controls expose one-click
  support-bundle creation and desktop download for connected Raspberry Pi
  modules.
- In progress: Devices and Mission Planner list the most recent downloaded
  support-bundle ZIPs under `~/DroneTransfer/from-pi/support-bundles/` with
  parsed bundle health, checksum status, map provenance, georeference
  confidence, and replay-gate status.
- In progress: `vision_nav.replay_gates` evaluates replay/runtime logs for
  `good_map`, `degraded`, and `wrong_map` expected behavior. Wrong-map cases
  fail if any map match is accepted by default.
- In progress: support bundles include replay-gate reports when a replay-case
  manifest is provided.
- In progress: `data/replay_cases/` defines the replay case registry shape for
  good texture, degraded, and wrong-map datasets.

Tasks:

1. Fill `data/replay_cases/` with real logs for good texture, low texture,
   blur, seasonal change, altitude/scale change, repeated patterns, and wrong
   map.
2. Compare ORB/AKAZE against optional higher-compute features on the same logs.
3. Add acceptance gates for inliers, reprojection error, scale confidence,
   geometry sanity, motion consistency, covariance inflation, and wrong-map
   rejection.
4. Add a dedicated support-bundle browser with open/delete/share actions and
   deeper per-log/replay drilldown.

Acceptance checks:

- CI or local smoke tests cover accepted, degraded, and rejected localization
  cases.
- Support bundles are enough to reproduce a failed bench run offline.

## Execution Order

1. External-position conversion and MAVLink payloads.
2. PX4 external-vision guidance and SITL smoke path.
3. Desktop setup wizard and runtime health display.
4. COG/STAC/GeoTIFF bundle validation and health report.
5. ROS 2 package wrapper and replay.
6. Hierarchical tile retrieval and map-quality heatmap.
7. ArduPilot adapter after PX4 bench validation.

The first execution item is now represented in code by
`src/vision_nav/external_position.py` and the updated MAVLink bridge tests.
