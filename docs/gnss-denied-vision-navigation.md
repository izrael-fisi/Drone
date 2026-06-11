# GNSS-Denied Vision Navigation

## Goal

Develop a low-cost onboard navigation system that estimates UAV position when GNSS is unavailable, degraded, spoofed, or intentionally ignored.

The system should use:

- Onboard camera imagery
- IMU and attitude estimates from the flight controller
- Barometer and optional rangefinder data
- Pre-installed georeferenced maps or visual feature databases
- Estimator fusion with explicit confidence/covariance

The project should produce the most useful modern navigation output for PX4 and ROS 2. NMEA is only an optional compatibility output.

## Core Navigation Concept

```text
Camera frames
  -> feature tracking / visual odometry / VIO
    -> visual place recognition or map matching
      -> absolute or drift-corrected pose estimate
        -> fusion with inertial, altitude, and heading sources
          -> navigation estimate with covariance/confidence
            -> ROS 2 pose/odometry
            -> PX4 external vision or GPS-like input
```

## Key Subsystems

### Sensor Capture

- Camera driver
- Camera calibration
- Timestamp synchronization
- Frame quality checks
- Rolling/global-shutter characterization

### Local Motion Estimation

Candidate approaches:

- Visual odometry
- Visual-inertial odometry
- Optical flow plus rangefinder for low-altitude velocity, if justified by tests
- Feature tracking with IMU propagation

### Global Relocalization

Candidate approaches:

- Matching against georeferenced orthomosaic tiles
- Matching against satellite/aerial image tiles
- Matching against a prebuilt visual landmark database
- Place recognition followed by geometric verification
- Pose refinement using known camera intrinsics, altitude, and attitude

### Fusion And Health

The estimator should publish:

- Pose
- Velocity if available
- Covariance or confidence
- Time since last map match
- Drift estimate if available
- Estimator mode: initializing, local-only, map-matched, degraded, failed

## Map Inputs

Possible map sources:

- Orthomosaic imagery
- Satellite imagery tiles
- Prebuilt feature maps
- Georeferenced keypoints
- Landmark/object databases
- Site-specific maps generated before deployment

Initial simulator maps can be small and artificial. The point is to validate the full navigation loop before paying the complexity cost of real-world map preparation.

## Output Strategy

Use modern robotics/autopilot outputs first:

- ROS 2 `nav_msgs/Odometry` for local pose/velocity with covariance
- ROS 2 `geometry_msgs/PoseWithCovarianceStamped` for pose-only consumers
- MAVLink `ODOMETRY` for PX4 external-vision input when velocity and covariance are available
- MAVLink `VISION_POSITION_ESTIMATE` for simpler PX4 external-vision pose input
- MAVLink `GPS_INPUT` only if the estimate is intentionally being used as a GPS-like global sensor
- NMEA only as a compatibility adapter for legacy consumers

## Low-Cost Sensor Bias

Prefer inexpensive modules first:

- Raspberry Pi 5 onboard compute
- Raspberry Pi camera or low-cost UVC camera modules
- Fixed-focus global-shutter camera if affordable
- Pixhawk IMU/barometer data through PX4
- Optional rangefinder for height-above-ground correction
- Raspberry Pi AI HAT+ 2 only after benchmarks show it is needed

Avoid expensive modules unless tests prove they are necessary:

- Premium stereo depth cameras
- Heavy onboard GPU modules
- Expensive LiDAR
- High-end INS/RTK systems

## Optical Flow Decision

Do not buy an optical flow sensor yet.

Optical flow can help with low-altitude velocity estimation, especially when paired with a rangefinder. PX4's EKF uses optical flow only when valid rangefinder data is available, optical-flow fusion is enabled, and the flow quality metric is good enough. That makes it useful, but not automatically required.

The first implementation path should be camera-based VIO/map matching. Add optical flow only if tests show a real accuracy, robustness, or cost advantage.

## Simulation Milestones

1. Run PX4 SITL with Gazebo X500.
2. Add simulated camera/depth/vision models.
3. Publish camera frames into ROS 2.
4. Record and replay camera, IMU, pose, and ground-truth data.
5. Build a small georeferenced test map.
6. Estimate local visual motion.
7. Match visual features or landmarks against the map.
8. Publish estimated pose and covariance.
9. Feed pose into PX4 external-vision paths.
10. Compare estimated position to simulator ground truth.

## Hardware Milestones

1. Bench-test camera capture on Raspberry Pi 5.
2. Benchmark local feature extraction and VIO on Raspberry Pi 5.
3. Benchmark map matching on Raspberry Pi 5.
4. Test Raspberry Pi AI HAT+ 2 only if CPU benchmarks fail latency targets.
5. Connect Raspberry Pi 5 to Pixhawk 6X.
6. Verify PX4/MAVLink telemetry and time synchronization.
7. Feed external-vision estimates to PX4 on the bench.
8. Validate outdoors with GNSS available only as ground truth.
9. Test GNSS-denied/degraded behavior only after estimator failure handling is proven.

## Design Risks

- Map matching can fail in repetitive, low-texture, dark, seasonal, or changed environments.
- Rolling-shutter cameras may degrade accuracy during fast motion.
- Raspberry Pi inference latency may be too high without acceleration.
- Poor time synchronization can break estimator fusion.
- Feeding low-confidence vision estimates into the flight controller can be worse than declaring failure.

## Safety Rule

A low-confidence map/vision estimate must degrade or fail explicitly. It should not silently replace GNSS or be presented as a high-quality position source.
