# Flight Control And Compute Boundaries

## Core Principle

This project is a navigation-source project. It should estimate position well and feed that estimate safely to the flight-control stack. It should not become a general autonomy or unrelated command system.

## PX4 Responsibilities

PX4 owns:

- Stabilization
- Attitude and rate control
- Arming checks
- Failsafes
- Flight modes
- Low-level motor/actuator control
- EKF fusion of accepted navigation sources

## Companion Computer Responsibilities

The Raspberry Pi 5 owns:

- Camera capture
- Camera calibration metadata
- Timestamp synchronization
- Visual odometry / VIO
- Map matching / relocalization
- Confidence and covariance estimation
- Navigation output bridge to PX4
- Logging and diagnostics

## Ground Workstation Responsibilities

The desktop PC owns:

- PX4/Gazebo simulation
- ROS 2 development
- Dataset generation
- Offline benchmarking
- Model training or optimization
- Map preparation experiments

The MacBook owns:

- Code editing
- QGroundControl
- Documentation
- Light test utilities

## Allowed Flight-Control Interactions

The companion computer may:

- Read telemetry from PX4
- Read IMU, attitude, barometer, and estimator status
- Publish external-vision pose/odometry estimates
- Publish GPS-like inputs only when deliberately configured and tested
- Send simple bench-test commands only in controlled development scripts

The companion computer should not:

- Bypass PX4 arming/failsafe logic
- Send raw motor commands
- Disable PX4 safety features
- Hide low-confidence localization from PX4 or the operator
- Treat visual/map estimates as trustworthy when confidence is low

## Navigation Health Checks

The estimator should track:

- Time since last camera frame
- Time since last successful map match
- Feature count or landmark count
- Reprojection or matching error
- Covariance/confidence
- Drift estimate where possible
- Sensor timestamp offset
- Estimator reset count

## Failure Behavior

When localization quality is poor, the system should explicitly switch to a degraded or failed state. The correct behavior is to stop publishing trusted navigation updates or mark them as low quality, not to continue producing plausible-looking but untrustworthy positions.

## Practical Rule

Accuracy and failure honesty are more important than continuous output.
