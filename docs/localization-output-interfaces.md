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

## ArduPilot Output Options

ArduPilot support is a later adapter path after PX4 bench validation. The
preferred shape is still MAVLink `ODOMETRY` from the shared external-position
conversion layer, with ArduPilot configured to consume ExternalNav through its
EKF source parameters.

Use `VISION_POSITION_ESTIMATE` only as a compatibility path and `GPS_INPUT` only
for a deliberate GPS-like compatibility mode. ArduPilot's own Non-GPS Position
Estimation docs list `GPS_INPUT` but mark it not recommended for this use.

Before testing an ArduPilot vehicle, audit the exported parameters with:

```bash
vision-nav-check-ardupilot-params \
  --params ardupilot.params \
  --source-set 1 \
  --gnss-denied \
  --extrinsics-measured
```

See [ArduPilot ExternalNav Adapter Design](ardupilot-externalnav-adapter.md).

## Optional Legacy Output

### NMEA

NMEA can be useful for legacy consumers that expect `$GPGGA`, `$GNRMC`, or similar strings. It should not be the primary internal interface.

If implemented, NMEA output must:

- Clearly encode quality/degraded state where possible
- Avoid pretending vision/map estimates are ordinary satellite GNSS
- Be generated from the estimator output, not drive the estimator design

## Recommended First Implementation

1. Publish `nav_msgs/Odometry` from the estimator or from replayed runtime logs.
   The first adapter is `vision-nav-ros2-replay-log`; see
   [ROS 2 Runtime Adapter](ros2-runtime.md).
2. Publish estimator diagnostics.
3. Bridge odometry to PX4 through MAVLink `ODOMETRY` or PX4 ROS 2 external-vision topics.
4. Add the ArduPilot ExternalNav adapter only after PX4 bench evidence is
   repeatable.
5. Add `GPS_INPUT` only if integration tests show it is the right abstraction
   for a specific global map-derived compatibility target.
6. Add NMEA last, and only if a downstream system needs it.

## References

- PX4 external position estimation: https://docs.px4.io/main/en/ros/external_position_estimation
- PX4 EKF2 external vision fusion: https://docs.px4.io/main/en/advanced_config/tuning_the_ecl_ekf.html#external-vision-system
- ArduPilot Non-GPS position estimation: https://ardupilot.org/dev/docs/mavlink-nongps-position-estimation.html
- MAVLink common messages: https://mavlink.io/en/messages/common.html
