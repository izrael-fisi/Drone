# Adopting the scaffold in an existing drone repository

This guide is intentionally repository-agnostic. It gives the smallest safe path for moving the scaffold into a flight project without forcing an immediate rewrite of camera capture, PX4 transport, or mission code.

## Desired end state

```text
existing camera driver ──────> FramePacket ──> vision_nav runtime ──> NavEstimate
                                                               │               │
existing telemetry/logging <──────────────── diagnostics      │               └─> PX4 ODOMETRY adapter
                                                               │
existing map/data pipeline ──> versioned visual map ──────────┘

existing mission/control code <──── PX4 only; never directly driven by vision_nav
```

## 1. Pick an ownership model

Keep these boundaries even if your repository uses MAVSDK, MAVLink, C++, Python,
or containers. This project does not use ROS 2, Gazebo, or SITL as an
integration path.

| Existing repository concern | Integrate with | Must remain owned by |
| --- | --- | --- |
| camera driver, gimbal metadata, hardware timestamps | `FramePacket` adapter | camera/perception package |
| neural inference runtime/TensorRT engines | `vision_nav.adapters` | perception/model package |
| local VIO/VO and keyframes | `VisualOdometryFrontend` | localization package |
| map imagery, DEM, feature storage, map release | `vision_nav.mapping` schema | mapping/data platform |
| pose graph and covariance | `NavigationBackend` | state-estimation package |
| MAVLink bridge to flight controller | `Px4VisionBridge` | vehicle integration package |
| arming, offboard state, missions, actuators, emergency action | no direct integration | PX4 + mission/safety package |

If your project is C++-first, retain the same messages in protobuf/IDL and implement the core runtime in C++ or wrap the Python reference implementation behind a process boundary. Do not translate data field-by-field ad hoc; define one versioned schema and generate bindings.

## 2. Copy strategy

Copy these files first; they are the stable contract layer:

```text
src/vision_nav/types.py
src/vision_nav/contracts.py
src/vision_nav/estimation/geometry.py
src/vision_nav/estimation/hypotheses.py
src/vision_nav/runtime/pipeline.py
src/vision_nav/px4/bridge.py
configs/default.yaml
AGENTS.md
```

Then copy `mapping/` if your project does not already have a map registry, and `adapters/` only when your model-serving approach is settled. Avoid copying `DeterministicStub` into production wiring; it is for CI and pipeline probing only.

### Suggested target layouts

| Target repository layout | Recommended placement |
| --- | --- |
| MAVSDK companion app | `lib/vision_nav/`, `apps/companion/vision_bridge/`, `config/vision_nav/` |
| PX4 module tree | keep vision inference on companion computer; PX4 receives ODOMETRY only |
| monorepo | `packages/navigation/vision_nav/`, `packages/maps/visual_map/`, `packages/vehicle/px4_bridge/` |

## 3. Implement the four first adapters

### A. Camera ingress → `FramePacket`

Populate `sequence`, monotonic `timestamp_s`, undistorted image, and calibrated `CameraModel`. Preserve raw timestamp and frame ID separately in logging. Do **not** use wall clock. Reject frames when calibration is unknown, exposure/blur quality fails, image dimensions disagree with calibration, or timestamp is non-monotonic.

```python
packet = FramePacket(
    sequence=camera.sequence,
    timestamp_s=clock.to_monotonic_seconds(camera.hardware_timestamp),
    image=rectify(camera.image, calibration),
    camera=CameraModel(fx, fy, cx, cy, width, height),
)
estimate = runtime.ingest_frame(packet)
```

### B. Local visual odometry → `VisualOdometry`

Convert the existing local tracker output to a relative *body pose* factor in the internal ENU convention. Populate a 6×6 covariance and a track quality score. Camera-only monocular VO needs a metric-scale source before it can make a metric global pose claim: stereo, known ground constraint, a mapped 3D landmark/PnP update, or an explicitly modelled scale state.

### C. Global map access → retrieval/matching ports

Back the `GlobalRetriever` with a version-pinned FAISS index of SALAD or MegaLoc tile descriptors. Bind the top candidates to an immutable tile record, then run sparse LightGlue and dense RoMa against feature-cache URIs, not the entire map image store. For 3D PnP, load map landmarks in the map's ENU coordinate system.

### D. PX4 transport → `Px4VisionBridge`

Send only valid tracking estimates to the MAVLink `ODOMETRY` path. The bridge converts ENU to LOCAL_NED. Validate position frame, orientation frame, covariance ordering, timestamp epoch/units, reset counter, and estimator configuration against the exact PX4 and MAVSDK releases in your drone repository.

## 4. Configuration integration

Split configuration ownership; never bury vehicle-specific values in model code.

| Config group | Source of truth | Examples |
| --- | --- | --- |
| airframe/sensors | vehicle calibration registry | intrinsics, body-camera transform, camera serial, time offset |
| flight operating envelope | safety/mission config | height, attitude, speed, lighting, maximum vision age |
| map release | map registry | map version, CRS origin, epochs, index checksum, GSD |
| models | ML release registry | checkpoint digest, TensorRT precision, input transform, feature dimension |
| estimator gates | navigation config | inlier gate, covariance gate, hypothesis persistence, stale timeout |
| PX4 | vehicle integration config | MAVLink endpoint, estimator settings, publish rate, reset policy |

Start from `configs/default.yaml`, but replace every placeholder with explicit provenance. Load configuration once at startup, log its immutable digest, and reject incompatible camera/map/model combinations.

## 5. Data and logging contract

Log enough to reproduce both successes and withheld estimates. At a minimum record:

- source image/keyframe IDs and timestamps; image-quality and dynamic-mask scores;
- calibration, map, descriptor-index, model/checkpoint, and config hashes;
- retrieval Top-K IDs/scores; sparse and dense match counts/coverage; geometry model, inlier mask, RMSE and covariance;
- all hypothesis weights/margins, runtime state transitions, factor additions, output covariance and publication decision;
- PX4 send status/acknowledgement and estimator-health telemetry.

Store raw imagery according to your privacy/retention policy. Store derived replay data where it can regenerate decisions without requiring access to live camera hardware.

## 6. Migration sequence

| Phase | Change | Exit criterion |
| --- | --- | --- |
| 0 — inventory | document current frames, clocks, calibration, PX4 estimator and map assets | owners agree on the coordinate/time contracts |
| 1 — passive replay | run `FramePacket → runtime → NavEstimate` on bags/logs, publish nowhere | deterministic replay and complete diagnostics |
| 2 — shadow mode | feed camera live, compare against trusted reference, never send PX4 vision | latency, drift and false accepts meet signed thresholds |
| 3 — prop-off PX4 transport bench | real companion, flight controller, and camera; do not arm or publish vision | clocks, telemetry, frames, covariance, and withholding are verified under load |
| 4 — constrained hardware validation | approved, controlled vehicle operations after an explicit promotion decision | transport, estimator innovation, and emergency behaviour are independently observed |
| 5 — expanded envelope | staged flights across environmental changes | safety case and flight review approve each expansion |

Do not skip directly from model demo to autonomous flight. An accurate median error does not validate false-positive behaviour, timing, or PX4 estimator interaction.

## 7. Questions to resolve when the target repository is available

1. What camera model(s), lens calibration, gimbal relationship, and exposure controls exist?
2. Is the local tracker mono, stereo, or VIO—and what supplies metric scale in a camera-only loss-of-GNSS case?
3. Where will map tiles/features live and how are map epochs approved/rolled back?
4. Which PX4 release, estimator mode, MAVLink transport, and companion hardware are pinned?
5. What is the safety action for stale/ambiguous global vision, and where is it enforced?
6. What reference data provides ground truth for replay and acceptance testing?

Answer these in an architecture decision record before binding a real model or flight controller.
