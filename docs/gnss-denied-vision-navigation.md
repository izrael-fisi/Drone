# GNSS-Denied Vision Navigation

## Goal

Develop a low-cost onboard navigation system that estimates UAV position when
GNSS is unavailable, degraded, spoofed, or intentionally ignored.

The active implementation uses:

- onboard downward camera imagery
- IMU/attitude/local-state telemetry from the flight controller when available
- optional Pixhawk barometer telemetry for relative vertical confidence
- pre-installed georeferenced maps or visual feature databases
- estimator confidence/covariance with explicit degraded/failure states

## Core Navigation Concept

```text
camera frames
  -> ORB/AKAZE feature extraction
  -> terrain tile retrieval
  -> geometric matching against preloaded map tiles
  -> RANSAC verification
  -> estimator update with confidence/covariance
  -> runtime log/status
  -> optional MAVLink external-vision output to Pixhawk
```

## Key Subsystems

### Sensor Capture

- camera driver
- camera calibration
- camera-to-body metadata
- timestamp checks
- frame quality checks
- rolling/global-shutter characterization

### Map Matching

- georeferenced orthomosaic or satellite image tiles
- precomputed ORB/AKAZE feature descriptors
- hierarchical tile search
- local feature matching
- homography/geometric verification
- wrong-map and low-texture rejection

### Estimator And Health

The estimator should report:

- local position where valid
- optional global position where georeference supports it
- covariance or confidence
- time since last map match
- visual scale confidence
- estimator mode: initializing, map-matched, degraded, failed
- external-position send/skip reason when MAVLink is enabled

## Map Inputs

Preferred map sources:

- orthomosaic imagery
- satellite or aerial imagery tiles
- GeoTIFF/COG where available
- prebuilt feature maps
- site-specific maps generated before deployment

The immediate hardware path is a real map bundle built in the desktop Mission
Planner and uploaded to the Raspberry Pi. Simulator maps are not part of the
active workflow.

## Output Strategy

Use modern autopilot outputs first:

- MAVLink `ODOMETRY` for PX4 external-vision bench/product readiness
- MAVLink `VISION_POSITION_ESTIMATE` for compatibility/debug only
- MAVLink `GPS_INPUT` only if the estimate is intentionally being used as a
  GPS-like global sensor
- NMEA only as a compatibility adapter for legacy consumers

## Low-Cost Sensor Bias

Prefer inexpensive modules first:

- Raspberry Pi 5 onboard compute
- Raspberry Pi camera or low-cost UVC camera modules
- fixed-focus global-shutter camera if affordable
- Pixhawk IMU and attitude telemetry through PX4
- optional Pixhawk barometer telemetry through PX4
- Raspberry Pi AI HAT+ only after benchmarks show it is needed

Avoid expensive modules unless tests prove they are necessary:

- premium stereo depth cameras
- heavy onboard GPU modules
- expensive LiDAR
- high-end INS/RTK systems

## Optical Flow Decision

Do not buy an optical flow sensor yet.

Optical flow can help short-term image-motion propagation, but it should be
tested first as a software signal from the downward camera. Add a dedicated
sensor only if hardware tests show a real accuracy, robustness, or cost
advantage.

## Hardware Milestones

1. Bench-test camera capture on Raspberry Pi 5.
2. Calibrate camera intrinsics and document camera-to-body geometry.
3. Build and validate a real terrain map bundle.
4. Connect Raspberry Pi 5 to Pixhawk.
5. Verify MAVLink heartbeat/telemetry and time assumptions.
6. Export and check PX4 parameters.
7. Run terrain runtime in logging-only mode with props removed.
8. Run short MAVLink `ODOMETRY` external-vision bench test with props removed.
9. Package support bundle and review failures.
10. Validate outdoors with GNSS available only as ground-truth comparison.
11. Test GNSS-denied/degraded behavior only after estimator failure handling is
    proven.

## Design Risks

- Map matching can fail in repetitive, low-texture, dark, seasonal, or changed
  environments.
- Rolling-shutter cameras may degrade accuracy during fast motion.
- Raspberry Pi latency may be too high without acceleration.
- Poor time synchronization can break estimator fusion.
- Feeding low-confidence vision estimates into the flight controller can be
  worse than declaring failure.

## Safety Rule

A low-confidence map/vision estimate must degrade or fail explicitly. It should
not silently replace GNSS or be presented as a high-quality position source.
