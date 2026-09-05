# Architecture Reassessment: Modern Vision-First Navigation

**Date:** 2026-08-30  
**Scope:** Raspberry Pi 5 companion runtime, PX4/Pixhawk integration, offline
map production, and the Tauri ground-control app.

> **Historical reassessment.** This document records the transition state that
> led to the learned pipeline. The active implementation decision is
> [`pi5_crossview_nav_v1`](flagship-modern-vision-pipeline.md): MegaLoc
> retrieval, XFeat/LighterGlue correspondence, a distilled temporal student,
> and shadow-only vehicle telemetry. Any classical-first recommendation below
> is retained only as migration/replay context.

## Decision

Adopt the supplied vision-first design as the target architecture, with a
strict division of work:

```text
Desktop workstation (RTX / offline)              Raspberry Pi 5 (on vehicle)
COG + STAC map preparation                       libcamera/Picamera2 capture
feature/index generation                         quality gate + local tracking
map release validation                           bounded coarse retrieval
replay, benchmark, and flight review             sparse geometry verification
                                                   hypothesis + state gate
                                                   PX4 ODOMETRY only while valid
```

The current single-image ORB/AKAZE terrain loop remains useful as a **map and
replay baseline**. It is not a flight-control localization source. It must not
publish a pose to PX4 until it is replaced by the versioned runtime path below.

## Current Maturity

| Area | Current state | Decision |
| --- | --- | --- |
| Coordinate, clock, calibration contracts | Implemented in `types.py`, `contracts.py`, and ingress checks | Keep; make every real adapter conform before use. |
| Hypothesis, geometry, and publication gates | Implemented and unit-tested | Keep; calibrate thresholds using held-out flight data. |
| Canonical runtime orchestration | Implemented as interfaces and deterministic test stub | Keep the shell; bind real adapters incrementally. |
| Map release manifest | Implemented for version, CRS, ENU origin, models, calibrations, and checksums | Keep; add artifact-level validation and signing later. |
| Classical terrain matcher | Functional ORB/AKAZE prototype with homography | Quarantine as replay baseline; do not promote it directly to PX4. |
| Global retrieval, neural matching, dense verification, VO | Interface-only | Implement behind adapters, beginning with a small, profiled subset. |
| Factor graph | Deterministic fallback, not an optimizer | Replace with a bounded estimator after replay data exists; use GTSAM/iSAM2 only behind a native boundary. |
| PX4 bridge | Validity decision and ENU-to-NED transform are implemented; transport is not | Implement one `ODOMETRY` sender with time/reset/ack tests. |
| Pi capture path | Legacy camera scripts and OpenCV reads | Replace with a calibrated `Picamera2`/libcamera ingress adapter. |
| Desktop app | Strong workflow/UI scaffolding; consumes legacy runtime status and bundles | Migrate its data model to map releases, decision traces, and canonical runtime health. |
| Deployment/logging | Systemd and JSON/support bundles exist; Docker is currently too broad for hardware ownership | Run camera/MAVLink on host systemd. Keep containers for offline map build and replay only. |

## Chosen Software Stack

### 1. Maps and offline map production

Use **Cloud Optimized GeoTIFF (COG)** as the raster source of record,
**STAC** as interoperable discovery/asset metadata, and the repository's
`MapRelease` manifest as the navigation-specific, immutable compatibility
contract. GDAL's COG driver and validator make it practical to produce tiled,
overview-backed rasters on the workstation. STAC intentionally does not model
camera calibration, descriptor space, or PX4 frame rules; those remain in the
navigation manifest.

Recommended workstation tools:

| Need | Selected implementation | Notes |
| --- | --- | --- |
| Raster conversion, reprojection, tiling | GDAL, Rasterio, PyProj | Desktop-only; produce COG, tile bounds, GSD, CRS and overlap records. |
| Geometric map build / landmarks | COLMAP through PyCOLMAP | Use for map compilation and evaluation only, never as a Pi runtime dependency. |
| Classical baseline features | OpenCV ORB/AKAZE | Always produce this cache first so a Pi-safe regression path exists. |
| High-compute map features | SuperPoint/ALIKED + LightGlue feature cache | Optional RTX workstation profile; each cache records exact model digest/input transform. |
| Global visual retrieval | Version-pinned SALAD or MegaLoc embeddings plus FAISS | Build and evaluate on desktop. Ship only a bounded, compatible Pi query index. |
| Dense confirmation | RoMa, only for short-listed candidates | Experimental until latency, memory, and false-accept evidence are captured on target hardware. |

Every release contains the imagery assets, derived tile grid, index artifacts,
model/checkpoint digests, calibration compatibility list, DEM/landmark facts,
and checksums. A release is immutable. Any map imagery, descriptor, or model
change creates a new release; it never overwrites a bundle on the Pi.

### 2. Pi companion runtime

Use Raspberry Pi OS's **libcamera/rpicam** stack through `Picamera2` for an
officially supported Python capture path. Capture timestamps must be translated
into the runtime monotonic clock and logged together with the original hardware
timestamp. The runtime runs natively under a systemd service so it can safely
own camera and serial/USB devices. Docker remains an offline/replay convenience,
not the production owner of Pi camera or MAVLink hardware.

The first production-capable runtime is deliberately modest:

1. `Picamera2FrameSource` rectifies a frame, validates calibration and rejects
   blur/exposure/dimension/clock failures.
2. `Px4TelemetryAdapter` supplies attitude, vehicle boot-time mapping, and
   motion context. It must not invent a metric pose from raw IMU alone.
3. `ClassicalLocalTracker` maintains a bounded local keyframe track.
4. `TileRetriever` searches a version-pinned, bounded Top-K index. Start with
   a small classical/embedding index measured on Pi RAM; do not assume a GPU
   FAISS build is available or beneficial on ARM.
5. `SparseGeometryAdapter` uses robust PnP when the map has verified 3D ENU
   landmarks. Homography is permitted only for an explicitly planar,
   altitude/attitude-bounded orthophoto operating envelope.
6. `HypothesisBank` and `NavigationBackend` require persistent support before
   a `TRACKING` estimate exists. Ambiguity, stale data, a model timeout, or
   inconsistent geometry becomes observable `RECOVERING`/`NO_GLOBAL_FIX`.

Do not deploy MASt3R-SLAM, VGGT, Video2BEV, RoMa, or a Hailo pipeline in the
first Pi flight path. They are research/acceleration candidates, not a safety
case. The Hailo AI HAT can later accelerate a separately profiled inference
adapter, but it does not solve camera synchronization, map release integrity,
geometry, covariance, or PX4 integration.

### 3. Estimation and metric-scale policy

The current factor-graph fallback is an API probe, not a state estimator. The
next estimator must first be a deterministic bounded-window implementation with
replayable factors, explicit covariance growth, and no PX4 output until metrics
pass. Once replay needs justify it, move the graph implementation to a native
GTSAM/iSAM2 component or service. Keep the Python types and JSON decision trace
as the stable boundary; do not couple flight behavior to a Python optimizer.

Monocular camera motion is not metric by itself. A metric global observation
requires one of the following, recorded in the map release and in the decision
trace:

- robust PnP against surveyed/map-derived 3D ENU landmarks;
- an approved ground-plane/known-height model inside a constrained envelope;
- stereo or another explicitly calibrated metric geometry source.

PX4 attitude and IMU motion are useful factors and consistency checks. They do
not make an otherwise scale-ambiguous single-image homography a valid global
metric pose. Barometer data may remain contextual/legacy telemetry, but cannot
be the authority for an absolute map-frame terrain height.

### 4. PX4 and MAVLink boundary

Keep PX4 as the only owner of arming, flight modes, missions, failsafes, and
actuators. The companion sends **MAVLink `ODOMETRY`** only after the canonical
runtime returns a fresh `TRACKING` estimate. The bridge converts ENU to
`MAV_FRAME_LOCAL_NED` exactly once, converts internal xyzw quaternions to
MAVLink wxyz, serializes a valid 6x6 covariance, and maintains a reset-counter
policy for relocalization.

Implement this as a focused `pymavlink` transport adapter first. It provides
the direct `ODOMETRY`, telemetry, `TIMESYNC`, and packet-capture control needed
for this project without creating a second vehicle-control layer. MAVSDK may be
added later as a desktop convenience API, but should not replace the strict
transport contract in the companion runtime.

The bridge needs three independent gates before an observation is sent:

```text
canonical runtime says TRACKING
AND map/calibration/model/clock/covariance compatibility is valid
AND PX4 transport + required parameter evidence are healthy
```

No terrain matcher, desktop button, or status script may bypass that predicate.

### 5. Ground-control desktop app

Preserve the map-first operator workflow, but make the app a consumer of real
release/runtime evidence rather than of optimistic legacy status:

- **Map / Bundle:** select an immutable map release; show COG/STAC assets, GSD,
  age, feature model, calibration compatibility, checksum and Pi cost.
- **Mission:** can select an active release and plan a route but cannot alter
  algorithm configuration or force visual publishing.
- **Drone / Fly:** show frame rate, capture quality, clock health, active
  calibration/map/model IDs, VPR candidates, geometry state, hypothesis margin,
  last valid fix age, PX4 link, and the exact publication decision/reason.
- **Review:** load the same decision trace and raw log references used by replay;
  compare known GNSS reference only as validation evidence, not as a hidden
  runtime dependency.

Replace misleading labels such as a single accepted terrain match being a
"vision correction" with the canonical state: `INITIALIZING`, `RECOVERING`,
`TRACKING`, or `NO_GLOBAL_FIX`. The desktop application must display withheld
output as a successful safety outcome, not a silent failure.

### 6. Logging, replay, and support bundles

Use append-only structured JSON decision traces now, with an optional **MCAP**
recording for synchronized camera metadata, telemetry, factors, and published
observations. MCAP is serialization-agnostic and has Python, TypeScript, Rust,
and C++ libraries, which fits this two-section repository without reinstating
ROS 2. A support bundle references raw imagery by retention policy, never hides
map/model/config/calibration digests.

The active replay API must be independent of `rosbag` terminology. Existing ROS
review tools can remain in an explicitly `legacy/` compatibility area until
their callers are migrated or removed.

## Architecture Diagram

```text
             OFFLINE / WORKSTATION
GeoTIFF/COG -> STAC + nav MapRelease -> tile/features/index -> signed checksum set
                               |                         |
                               v                         v
                       desktop map library         Pi release installation

                   ON-VEHICLE COMPANION (Pi 5)
camera -> ingress quality/clock -> local tracker -----+
PX4 attitude/telemetry -------------------------------+--> hypothesis + bounded estimator
map index -> coarse retrieval -> sparse geometry -----+              |
                                                                   NavEstimate
                                                                       |
                    Desktop receives status/trace <---- health -----+----> PX4 ODOMETRY gate
```

## Migration Plan

### Phase A: stabilize the boundary (next)

- Move active desktop/Pi commands off `run_terrain_nav_loop.sh` and onto a
  new `vision-nav-runtime` command that initially runs in passive/shadow mode.
- Add `Picamera2FrameSource`, a time-domain adapter, and an explicit camera
  quality result. Validate a real calibrated camera before matching.
- Replace legacy runtime status fields with a canonical `runtime_status_v3`
  that includes map/calibration/model digests and publication decision.
- Mark all ROS/SITL scripts and UI wording as legacy, or remove callers when
  their output is no longer needed. Do not add ROS/Gazebo back to the scaffold.

**Exit:** passive Pi capture and replay use the same `FramePacket`, map release,
and decision-trace schemas. Nothing sends `ODOMETRY`.

### Phase B: map production and classical geometry

- Build COG/STAC ingest, tile/overlap records, checksums, and a map-release
  validation command.
- Add documented geometry evidence: 3D ENU landmarks/PnP where possible, or a
  narrow homography envelope whose altitude/attitude/planarity gates are tested.
- Create deterministic replay sets: nominal, wrong map, repeated texture,
  blur, dark frame, stale map, bad calibration, time jumps, and tile seams.

**Exit:** a new map release can be built on the RTX desktop, installed on the
Pi, and replayed deterministically with accepted and withheld examples.

### Phase C: local tracking and recovery

- Bind a classical VO/keyframe adapter and a bounded tile retriever.
- Add persistent geometry support and covariance behavior to the real backend.
- Benchmark ORB/AKAZE against optional SuperPoint/LightGlue on the same frozen
maps; promote only if Pi latency, memory, heat, availability, and false-accept
metrics improve.

**Exit:** live Pi shadow mode can recover and lose global localization without
publishing; every decision is explainable from the trace.

### Phase D: PX4 external-vision bench integration

- Implement the `pymavlink` ODOMETRY adapter, `TIMESYNC` handling, reset
  counter, covariance order tests, serial/UDP packet capture, and parameter
  evidence readback.
- Start prop-off, then HIL/constrained testing under the existing safety plan.
- Retain PX4 as the safety authority. A stale or ambiguous estimate means no
  new external vision observation, never a synthesized low-confidence fix.

**Exit:** PX4 estimator health, coordinate alignment, delay, covariance, and
loss/recovery behavior have recorded evidence on the actual aircraft.

### Phase E: optional high-compute enhancements

- Add global SALAD/MegaLoc retrieval and SuperPoint/LightGlue map artifacts
  only after model release pinning and map compatibility checks are enforced.
- Evaluate RoMa/BEV/MASt3R-family adapters in desktop replay first, then Pi
  shadow mode. Hailo integration is optional and must retain a CPU fallback.

**Exit:** a feature upgrade has lower false acceptance and acceptable P95/P99
latency/power on its declared profile. Otherwise it remains a research profile.

## Explicit Non-Goals for This Migration

- No autonomous mission commands from vision code.
- No implicit GPS replacement or GPS-jamming test logic in the map matcher.
- No cloud inference or online map dependency in flight.
- No default neural path on Pi.
- No ROS 2, Gazebo, or SITL dependency in the active runtime. SITL remains an
  optional validation rung only if the project later chooses to restore it.

## Evidence Required Before PX4 Output

1. A pinned Pi image, application build, camera calibration, map release, and
   model/config digest.
2. Deterministic nominal and fault-injection replay results, including false
   accept and false-`TRACKING` duration metrics.
3. Measured Pi capture-to-estimate P50/P95/P99 latency, temperature, memory,
   dropped frames, and storage/cache behavior.
4. PX4 packet capture proving timing, `LOCAL_NED` orientation/position frames,
   covariance ordering, reset behavior, and EKF response.
5. A prop-off/HIL record and explicit vehicle-safety approval before any
   constrained flight test.

## Sources

- [PX4 external position estimation](https://docs.px4.io/main/en/ros/external_position_estimation)
- [MAVLink `ODOMETRY` definition](https://mavlink.io/en/messages/common.html#ODOMETRY)
- [Raspberry Pi camera software and Picamera2](https://www.raspberrypi.com/documentation/computers/camera_software.html)
- [GDAL Cloud Optimized GeoTIFF driver](https://gdal.org/en/stable/drivers/raster/cog.html)
- [STAC specification](https://stacspec.org/en/about/stac-spec/)
- [COLMAP / PyCOLMAP](https://colmap.github.io/pycolmap/index.html)
- [Hierarchical Localization](https://github.com/cvg/Hierarchical-Localization)
- [FAISS](https://github.com/facebookresearch/faiss)
- [GTSAM](https://github.com/borglab/gtsam)
- [MCAP](https://mcap.dev/)
- [Hailo Raspberry Pi 5 examples](https://github.com/hailo-ai/hailo-rpi5-examples)
