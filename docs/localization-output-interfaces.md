# Localization Output Interfaces

## Purpose

This project should output the navigation estimate through the interface that best fits the consumer. NMEA is not the default architecture. It is only a compatibility adapter.

## Preferred Internal Output

Use ROS 2 as the internal navigation interface.

Primary topics:

- `nav_msgs/Odometry` for local pose, velocity, and covariance
- `geometry_msgs/PoseWithCovarianceStamped` for pose-only consumers
- `sensor_msgs/NavSatFix` only when publishing a global georeferenced estimate and clearly marking status/quality
- `diagnostic_msgs/DiagnosticArray` or custom diagnostics for estimator health

Recommended metadata:

- Estimator mode
- Confidence score
- Covariance
- Time since last successful map match
- Number of matched features/landmarks
- Drift estimate if available
- Reset counter for relocalization jumps

## PX4 Output Options

### MAVLink ODOMETRY

Preferred when the estimator can provide local pose, velocity, and covariance.

PX4 maps MAVLink `ODOMETRY` with `MAV_FRAME_LOCAL_FRD` into `vehicle_visual_odometry`. PX4 documentation notes that `ODOMETRY` is the only listed external-position message that can also send linear velocities to PX4.

### MAVLink VISION_POSITION_ESTIMATE

Useful for simpler local pose-only external vision input.

PX4 maps `VISION_POSITION_ESTIMATE` into `vehicle_visual_odometry`.

### MAVLink GPS_INPUT

Use only when deliberately presenting the estimate as a GPS-like global sensor. This can be useful for compatibility, but it can also hide the fact that the source is visual/map-derived. The quality fields and estimator health must be handled carefully.

### SET_GPS_GLOBAL_ORIGIN

For local-only systems, PX4 can use a global origin so a local estimate can support mission-like global behavior. This is useful for indoor or site-specific maps with a known origin.

## Optional Legacy Output

### NMEA

NMEA can be useful for legacy consumers that expect `$GPGGA`, `$GNRMC`, or similar strings. It should not be the primary internal interface.

If implemented, NMEA output must:

- Clearly encode quality/degraded state where possible
- Avoid pretending vision/map estimates are ordinary satellite GNSS
- Be generated from the estimator output, not drive the estimator design

## Recommended First Implementation

1. Publish `nav_msgs/Odometry` from the estimator.
2. Publish estimator diagnostics.
3. Bridge odometry to PX4 through MAVLink `ODOMETRY` or PX4 ROS 2 external-vision topics.
4. Add `GPS_INPUT` only if PX4 integration tests show it is the right abstraction for global map-derived estimates.
5. Add NMEA last, and only if a downstream system needs it.

## References

- PX4 external position estimation: https://docs.px4.io/main/en/ros/external_position_estimation
- PX4 EKF2 external vision fusion: https://docs.px4.io/main/en/advanced_config/tuning_the_ecl_ekf.html#external-vision-system
- MAVLink common messages: https://mavlink.io/en/messages/common.html
