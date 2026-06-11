# Project Plan

## Phase 1: Environment Setup

- Install Ubuntu 22.04 LTS dual boot on the desktop PC.
- Install QGroundControl, PX4-Autopilot, Gazebo, ROS 2 Humble, Micro XRCE-DDS Agent, and `px4_msgs`.
- Create Python environment with computer vision, geospatial, MAVLink, and test dependencies.
- Run PX4 SITL smoke tests with X500 models.

Acceptance criteria:

- `gz_x500` launches successfully.
- QGroundControl connects to PX4 SITL.
- ROS 2 bridge can observe PX4 SITL state.

## Phase 2: Repository Scaffold

- Create ROS 2 workspace structure.
- Create Python package structure for estimator utilities and data tools.
- Add simulation launch scripts.
- Add dataset/log directory conventions.
- Add basic pytest tests.

Acceptance criteria:

- Local tests run.
- Project can launch simulator and record synchronized data.

## Phase 3: Sensor And Data Pipeline

- Publish simulated camera frames into ROS 2.
- Record camera, IMU, PX4 state, and simulator ground truth with rosbag2.
- Add camera calibration file format.
- Add timestamp validation tools.

Acceptance criteria:

- Sensor data can be recorded and replayed.
- Timestamp gaps and synchronization errors are detectable.

## Phase 4: Local Visual Motion

- Evaluate visual odometry or VIO candidate stack.
- Publish local pose/velocity as `nav_msgs/Odometry`.
- Compare local estimate to simulator ground truth.
- Track drift over distance/time.

Acceptance criteria:

- Local motion estimate is available with measurable error.
- Drift metrics are logged automatically.

## Phase 5: Map And Relocalization Prototype

- Define a small georeferenced map format.
- Create or simulate map imagery/features.
- Match visual features, landmarks, or image tiles to the map.
- Estimate global pose correction.

Acceptance criteria:

- System can relocalize against a known map in simulation.
- Confidence and failure cases are reported.

## Phase 6: Estimator Fusion

- Fuse local visual motion with map-derived corrections.
- Include altitude/height source where available.
- Publish covariance/confidence and estimator mode.
- Handle relocalization jumps and resets.

Acceptance criteria:

- Estimator publishes pose, velocity, covariance/confidence, and health state.
- Failure and degraded modes are explicit.

## Phase 7: PX4 Integration

- Bridge estimator output to PX4 external-vision input.
- Test MAVLink `ODOMETRY` and `VISION_POSITION_ESTIMATE` paths.
- Evaluate `GPS_INPUT` only if a GPS-like global source is needed.
- Validate PX4 EKF behavior in simulation and bench tests.

Acceptance criteria:

- PX4 can consume the estimator output without unsafe estimator behavior.
- Logs show correct frame, timing, and covariance handling.

## Phase 8: Raspberry Pi + Pixhawk Bench Integration

- Install companion-computer image on Raspberry Pi 5.
- Connect Raspberry Pi to Pixhawk 6X.
- Verify MAVLink and/or Ethernet communication.
- Run camera capture and estimator components on the Pi.
- Feed external-vision estimates to PX4 on the bench.

Acceptance criteria:

- Pi can read Pixhawk telemetry.
- Pi can publish localization output to PX4 without flight hardware attached.

## Phase 9: Hardware Vision Benchmarks

- Test low-cost camera options.
- Benchmark feature extraction, VIO, and map matching on Raspberry Pi 5.
- Benchmark Raspberry Pi AI HAT+ 2 if needed.
- Decide final low-cost vision module.

Acceptance criteria:

- Selected vision hardware meets latency and accuracy requirements for the target mission.

## Phase 10: Physical Flight Later

Physical flight comes only after the simulator and bench systems are stable.

- Assemble drone.
- Configure PX4.
- Run manual flight tests.
- Validate failsafes.
- Test estimator output with GNSS available as ground truth.
- Test GNSS-denied/degraded behavior incrementally.

Acceptance criteria:

- Manual flight is stable.
- Vision navigation is validated against ground truth before being trusted for navigation.
