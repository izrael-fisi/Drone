# Autonomy And Ground Control Research

This research note ranks drone software, ground control products, and
localization stacks against this repo's product goal:

> A sellable, low-cost GNSS-denied navigation module that uses onboard computer
> vision, IMU/attitude data, optional barometer data, and preloaded
> georeferenced terrain maps.

The practical product shape is a companion-computer module plus a desktop
operator app. The flight controller should remain PX4/Pixhawk first, with an
ArduPilot adapter kept in view because several commercial visual navigation
references use ArduPilot today.

## Fit Criteria

Use these criteria when deciding whether a reference belongs in the product:

- Runs offline after map preparation.
- Works with low-cost compute, starting with Raspberry Pi 5 class hardware.
- Uses camera, IMU/attitude, and preloaded maps as the core navigation source.
- Publishes confidence, covariance, timestamp, and health instead of pretending
  every match is valid.
- Integrates through PX4 external vision, MAVLink, and ROS 2 rather than
  replacing the autopilot.
- Helps the customer install, calibrate, validate, and diagnose the module.
- Has license terms and compute requirements that are compatible with a
  commercial product path.

## Highest-Value References

| Reference | What it proves | What to implement here |
| --- | --- | --- |
| [PX4 External Vision](https://docs.px4.io/main/en/ros/external_position_estimation) | PX4 EKF2 can consume visual odometry through MAVLink `VISION_POSITION_ESTIMATE` and `ODOMETRY`; `ODOMETRY` also carries velocities and PX4 expects stable high-rate streaming. | Add a first-class external-position bridge with rate, timestamp, covariance, and frame checks. Keep `VISION_POSITION_ESTIMATE` as a compatibility path, but design toward `ODOMETRY` and ROS 2 `VehicleOdometry`. |
| [PX4 uXRCE-DDS](https://docs.px4.io/main/en/middleware/uxrce_dds) | PX4 can expose uORB topics to a companion computer as ROS 2 topics through the Micro XRCE-DDS Agent. | Add ROS 2 nodes around the terrain runtime for image/frame input, attitude/local-state subscription, and external-vision output. Keep the direct MAVLink bridge for simpler setups. |
| [QGroundControl Plan View](https://docs.qgroundcontrol.com/master/en/qgc-user-guide/plan_view/plan_view.html) | A mature GCS separates Mission, GeoFence, Rally, upload/download, dirty-state indicators, planned home, terrain stats, and item editing. | Keep improving the desktop Mission Planner with QGC-style layer separation, plan import/export, upload/download state, home position, geofence validation, and clear mission readiness status. |
| [ArduPilot Non-GPS Position Estimation](https://ardupilot.org/dev/docs/mavlink-nongps-position-estimation.html) | ArduPilot can accept external position and velocity estimates for EKF operation without GPS. | Build an ArduPilot adapter after PX4 bench validation so the module can sell into more autopilot ecosystems. |
| [Mission Planner](https://ardupilot.org/planner/) | A mature Windows GCS combines waypoint/fence/rally editing, autopilot configuration, log download, analysis, and SITL entry points. | Add log download/replay/report flows and autopilot configuration checklists to the desktop app without turning the app into a full autopilot configurator. |
| [UgCS](https://www.sphengineering.com/flight-planning/ugcs) | Professional users value desktop planning, custom DEM/DSM import, full offline workflows, terrain following, 3D preview, route segmentation, and mixed fleet support. | Add offline map libraries, DEM/DSM ingestion, AGL/GSD planning checks, terrain profile preview, route segmentation, and field-ready cache validation. |
| [Auterion Mission Control GPS-denied workflow](https://docs.auterion.com/vehicle-operation/auterion-mission-control/useful-resources/operations/gps-denied-workflow) | GNSS-denied operation needs explicit mode selection, position reset, home reset, and clear UI indicators when satellite positioning is disabled. | Add a GNSS-denied readiness workflow: disable satellite-source assumptions, set map/home/heading, verify estimator health, and make reset actions explicit. |
| [Theseus Cyclops docs](https://docs.theseus.us/cyclops/getting-started) and [Theseus YC profile](https://www.ycombinator.com/companies/theseus) | The closest public product analogue is software-only visual positioning on ARM64 edge hardware using onboard imagery, inertial sensors, reference maps, MAVLink, setup wizard, camera calibration, map generation, and bench tests. | Copy the product workflow, not the implementation: guided Pi install, camera/extrinsic calibration, map upload, time-sync check, autopilot-source checklist, bench-test report, and field-test checklist. |
| [OpenDroneMap](https://opendronemap.org/) | Open-source photogrammetry can produce maps, point clouds, meshes, DEMs, and orthophotos from drone imagery. | Support ODM/WebODM output import as a primary low-cost way to build local orthomaps and terrain assets. |
| [GDAL COG driver](https://gdal.org/en/stable/drivers/raster/cog.html) and [STAC](https://github.com/radiantearth/stac-spec) | Modern geospatial packaging should use georeferenced rasters, validation, overviews, tile-friendly layouts, and standard metadata. | Make terrain bundles COG/STAC-aware: validate georeference, CRS, GSD, overviews, checksums, tile index, DEM/DSM optional assets, and source provenance. |
| [hloc](https://github.com/cvg/Hierarchical-Localization) | Hierarchical localization combines coarse image retrieval, local feature matching, geometric verification, and debuggable logs. | Evolve from brute tile search to hierarchical terrain localization: coarse tile retrieval first, then ORB/AKAZE or neural local matching, then RANSAC verification. |
| [COLMAP](https://github.com/colmap/colmap) | Mature SfM/MVS tooling with GUI/CLI and permissive license can build/reconstruct maps from image sets. | Use as an offline map-building/reference-pose tool where useful; do not put it in the Pi runtime path. |
| [OpenVINS](https://github.com/rpng/open_vins) | VIO is a strong comparison baseline for camera plus IMU fusion. | Use for log-based benchmarking and estimator architecture lessons; be careful with GPL-3.0 obligations before product embedding. |
| [ORB-SLAM3](https://github.com/UZ-SLAMLab/ORB_SLAM3) | Strong visual/visual-inertial SLAM benchmark, including multi-map support. | Treat as a benchmark or separately licensed component only. GPLv3 makes it risky as product-core code. |
| [MAVSDK Offboard](https://mavsdk.mavlink.io/main/en/cpp/guide/offboard.html) | Companion computers can send controlled setpoints, but offboard control has strict rate and mode requirements. | Use MAVSDK/MAVLink for telemetry, mission upload, diagnostics, and guarded bench actions. Do not depend on offboard control for the initial navigation module. |
| [Aerostack2](https://aerostack2.github.io/) | ROS 2 aerial autonomy frameworks organize perception, controllers, localization, mapping, planning, and behaviors. | Borrow ROS 2 package boundaries and lifecycle ideas. Avoid making Aerostack2 a required dependency for the Pi MVP. |
| [Nav2](https://nav2.org/) | Production-grade autonomy UX includes behavior trees, visualization, waypoint execution, and health metadata. | Borrow behavior-tree style health gates and operator visualization ideas only; multirotor flight control should remain autopilot-led. |
| [DJI FlightHub 2](https://enterprise.dji.com/flighthub-2) | Enterprise drone software sells operations management: remote operations, route management, scheduling, third-party integration, visual oversight, and optional on-prem deployments. | Add fleet/module management concepts later: device inventory, version state, route library, audit logs, and on-prem sync. This is a business UX reference, not an autopilot or navigation core. |

## Recommended Product Architecture Changes

### 1. External-Position Interface

Current repo direction is correct: keep the terrain matcher as a companion
navigation source and feed validated estimates into the flight-control stack.
The next architecture improvement is to formalize three output levels:

- Internal ROS 2: `nav_msgs/Odometry` or PX4 `VehicleOdometry` equivalent with
  covariance, timestamps, frame IDs, and source health.
- PX4 direct: MAVLink `ODOMETRY` preferred when velocity is available, with
  `VISION_POSITION_ESTIMATE` kept for compatibility.
- ArduPilot direct: ExternalNav/MAVLink adapter after PX4 bench validation.

Implementation tasks:

- Add a `vision_nav.external_position` module that converts terrain-estimator
  output into ENU, NED, and FRD-safe message shapes.
- Add frame-transform tests for camera, body, local ENU, local NED, and PX4 FRD.
- Add stream-health checks: output rate, timestamp skew, covariance bounds,
  stale-match rejection, and estimator reset events.
- Surface PX4 SITL receiver capture from Module Setup so the operator can
  generate the required ODOMETRY receiver proof in the same evidence workflow.
- Add a PX4 parameter guidance doc for EKF external vision. Do not auto-change
  flight-controller parameters from the app.

### 2. ROS 2 Companion Runtime

ROS 2 should be the modular runtime spine, while the existing Python CLIs remain
useful for bench tests and field debugging.

Implementation tasks:

- Add a ROS 2 package or launch profile for:
  - camera frame input,
  - terrain matcher,
  - estimator,
  - PX4 attitude/local-state subscriber,
  - external-position publisher,
  - health/status publisher.
- Support Micro XRCE-DDS Agent as the PX4 path on Linux companion hardware.
- Keep direct MAVLink as a no-ROS fallback for simple Pi deployments.
- Add rosbag/replay compatibility so every field run can be replayed on the
  desktop PC.
- Surface the native rosbag2 CLI review from Module Setup after the Pi JSONL
  validation syncs the source terrain log, so the final proof gate can be
  generated from the same operator workflow.

### 3. Terrain Map And Bundle Pipeline

The sellable advantage is not just matching one image to one map. It is a
repeatable map-prep pipeline that a customer can trust offline.

Implementation tasks:

- Import GeoTIFF/COG, STAC Item/Catalog metadata, ODM/WebODM orthophotos, and
  DEM/DSM rasters.
- Validate CRS, GSD, bounds, checksum, overviews, and tile count before a bundle
  can be sent to the Pi.
- Store multiple feature indexes per bundle:
  - low-compute ORB/AKAZE for Pi 5,
  - optional SuperPoint/LightGlue style descriptors for higher compute,
  - optional global descriptors for coarse retrieval.
- Add a map quality heatmap showing feature density, low-texture zones,
  seasonal-change risk, and expected runtime cost.
- Add a bounded startup relocalization mode: coarse tile search first, then local
  matching, then geometry checks.

### 4. Desktop GCS And Customer Setup

The desktop app should feel closer to QGroundControl/UgCS/Theseus Vozilla than a
developer demo. Its main job is to make setup and field validation hard to get
wrong.

Implementation tasks:

- Add a guided setup flow:
  1. discover Raspberry Pi,
  2. verify OS/runtime,
  3. verify camera,
  4. verify Pixhawk/MAVLink,
  5. verify time sync,
  6. calibrate camera intrinsics,
  7. set camera-to-body extrinsics,
  8. import/build map bundle,
  9. run bench localization,
  10. export readiness report.
- Keep field-evidence case metadata as a local app draft until registration so
  the operator does not lose site, lighting, weather, camera, IMU/PX4, or
  safety context while moving between setup checks.
- Let operators load pending field collection plan conditions into the field
  evidence registration form so required condition tags, expected behavior, and
  capture metadata stay consistent with the checklist.
- Add a bounded Module Setup field-log capture action so real terrain replay
  logs and runtime status snapshots can be collected and synced before field
  evidence registration or threshold tuning.
- Add QGC-style plan state:
  - unsaved changes,
  - not uploaded,
  - uploaded to device,
  - stale bundle,
  - invalid map/mission mismatch.
- Add UgCS-style terrain planning:
  - custom DEM/DSM import,
  - terrain profile preview,
  - GSD/AGL consistency,
  - offline cache status,
  - route segmentation for large areas.
- Add Auterion-style GNSS-denied controls:
  - explicit satellite-source disabled status,
  - reset vehicle/map position,
  - reset heading,
  - reset home position,
  - confidence/covariance display.

### 5. Benchmarking And Product Risk Controls

The biggest technical risk is false confidence: the system must reject bad
terrain matches instead of sending attractive but wrong positions.

Implementation tasks:

- Maintain a replay dataset with:
  - clear ground texture,
  - low texture,
  - seasonal change,
  - lighting change,
  - blur,
  - altitude/scale change,
  - wrong map,
  - repeated patterns.
- Compare ORB/AKAZE against higher-compute feature methods on the same logs.
- Add acceptance gates:
  - minimum inliers,
  - reprojection error,
  - geometric consistency,
  - scale confidence,
  - motion consistency with IMU/attitude,
  - covariance inflation on weak matches,
  - hard rejection on wrong-map behavior.
- Generate a support bundle after every test with app version, Pi version,
  flight-controller metadata, map manifest, pipeline config, logs, and summary.

## Ranked Implementation Backlog

### P0 - Required For A Sellable Bench Prototype

- PX4 external-position bridge with `ODOMETRY` path, covariance, frame checks,
  and stream-health reporting.
- ROS 2 wrapper around the terrain runtime, with direct MAVLink fallback.
- Desktop guided Pi/Pixhawk/camera setup wizard.
- COG/STAC/GeoTIFF bundle validation and map health report.
- Terrain replay dataset and wrong-map rejection tests.
- QGC-style Mission Planner state: save, upload, dirty, invalid, active bundle.

### P1 - Required For Field Pilot Readiness

- ArduPilot ExternalNav adapter.
- DEM/DSM import, terrain profile preview, AGL/GSD checks, and offline cache
  validation.
- Hierarchical tile retrieval before local matching.
- Optional higher-compute feature pipeline for desktop/RTX/AI-HAT/Jetson class
  devices.
- One-click bench report and field support bundle.

### P2 - Product Differentiation

- Map-quality heatmaps and expected localization-cost estimates.
- Multi-bundle map library with versioned provenance and checksums.
- Fleet/device inventory, update status, route library, and on-prem sync.
- Behavior-tree style health gates for autonomy enablement.

### P3 - Research Or Licensed Later

- OpenVINS or ORB-SLAM3 style VIO/SLAM as benchmark baselines.
- COLMAP reconstruction automation for customer-collected map imagery.
- Full autonomy behaviors beyond navigation source output.
- Cloud fleet operations after the offline field workflow is reliable.

## Implementation Choices To Avoid

- Do not make the desktop app a full replacement for QGroundControl or Mission
  Planner. Build only the pieces needed for map prep, module setup, mission
  context, and validation.
- Do not make neural features mandatory for the Pi 5 product path.
- Do not use GPL visual navigation code as product-core code without a licensing
  decision.
- Do not send external-position estimates to the autopilot when covariance,
  timestamp, frame, or health checks are missing.
- Do not treat map-relative terrain matching as satellite positioning. It is an
  external navigation source with explicit confidence and failure modes.

## Near-Term Repo Integration Plan

1. Add `vision_nav.external_position` and tests for ENU/NED/FRD conversion.
2. Add ROS 2 runtime package/launch files around `run_terrain_loop`.
3. Extend the desktop bundle builder to validate COG/STAC/GeoTIFF metadata.
4. Add a map quality report to Mission Planner after bundle build.
5. Add a setup wizard page that chains Pi discovery, camera, MAVLink, time sync,
   calibration, map upload, and bench test.
6. Add a PX4 external-vision guidance doc and an ArduPilot adapter design doc.
7. Build a replay dataset and CI smoke test for accepted, degraded, and rejected
   localization cases.

This sequence improves the current architecture without changing the core
project goal: low-cost GNSS-denied terrain feature navigation first, broader
autonomy only after the navigation source is reliable.
