# Localization Output Interfaces

## Purpose

This project should output the navigation estimate through the interface that
best fits the current hardware test. For the active Holybro/Pixhawk path, the
default integration target is MAVLink to PX4.

NMEA is not the default architecture. It remains only a compatibility adapter
for downstream systems that explicitly need it.

## Internal Runtime Output

The Python runtime writes JSONL records and runtime status snapshots.

Expected artifacts:

```text
terrain_matches.jsonl
runtime_status.json
field_log_capture_report.json
support_bundle.zip
```

Each accepted/rejected/failed result should preserve:

- timestamp
- status
- local position where valid
- optional lat/lon where map georeference supports it
- covariance/confidence
- tile id
- inlier count
- reprojection error
- scale confidence
- estimator/external-position health

## PX4 Output Options

### MAVLink ODOMETRY

Preferred and used by default for PX4 bench/product readiness when the
estimator can provide local pose and covariance. It is the target path for the
Holybro X500 V2 prop-off bench work.

### MAVLink VISION_POSITION_ESTIMATE

Useful for simple pose-only compatibility/debug tests. It should not be treated
as the primary readiness path.

### MAVLink GPS_INPUT

Use only when deliberately presenting the estimate as a GPS-like global sensor.
This can be useful for compatibility, but it can also hide the fact that the
source is visual/map-derived. Quality fields and estimator health must be
handled carefully.

### SET_GPS_GLOBAL_ORIGIN

For local-only systems, PX4 can use a global origin so a local estimate can
support mission-like global behavior. Treat this as an integration tool, not as
proof that the estimate is ordinary GNSS.

## Optional Legacy Output

### NMEA

NMEA can be useful for legacy consumers that expect `$GPGGA`, `$GNRMC`, or
similar strings. If implemented, it must:

- clearly encode quality/degraded state where possible
- avoid pretending vision/map estimates are ordinary satellite GNSS
- be generated from estimator output, not drive estimator design

## Recommended First Hardware Implementation

1. Run terrain runtime in logging-only mode.
2. Verify `terrain_matches.jsonl` and `runtime_status.json`.
3. Verify Pixhawk heartbeat/telemetry from the Raspberry Pi.
4. Export and check PX4 parameters.
5. Enable MAVLink `ODOMETRY` output for a short prop-off bench test.
6. Package logs, status, PX4 parameter report, and support bundle for review.

## References

- PX4 external position estimation:
  https://docs.px4.io/main/en/ros/external_position_estimation
- PX4 EKF2 external vision fusion:
  https://docs.px4.io/main/en/advanced_config/tuning_the_ecl_ekf.html#external-vision-system
- MAVLink common messages:
  https://mavlink.io/en/messages/common.html
