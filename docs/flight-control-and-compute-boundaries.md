# Flight Control And Compute Boundaries

## Core Principle

This project is a navigation-source project. It estimates position and reports
health. It does not replace PX4 flight control, arming checks, failsafes, or
pilot authority.

## Pixhawk / PX4 Responsibilities

PX4 owns:

- stabilization
- attitude and rate control
- arming checks
- failsafes
- flight modes
- low-level motor/actuator control
- RC input handling
- battery, GPS, compass, and safety-switch integration
- EKF fusion of accepted navigation sources

## Raspberry Pi Companion Responsibilities

The Raspberry Pi 5 owns:

- downward camera capture
- camera calibration metadata
- frame quality checks
- terrain map-bundle loading
- feature extraction and map matching
- confidence and covariance estimation
- runtime logs and status snapshots
- MAVLink external-vision output to PX4 only when explicitly configured

The Raspberry Pi must not:

- send raw motor commands
- bypass PX4 arming/failsafe logic
- auto-change PX4 parameters
- hide low-confidence localization from PX4 or the operator
- present visual/map estimates as ordinary GPS unless an explicit compatibility
  mode is being tested

## Ground Control / Mission Planner Responsibilities

The desktop app owns:

- map source import/selection
- Vision Pipeline defaults
- mission/fence/rally/vision-map planning
- terrain bundle build/upload/validation
- Raspberry Pi Wi-Fi/SSH setup
- camera and MAVLink checks
- field capture workflow
- support-bundle review

QGroundControl remains the tool for Pixhawk firmware, radio calibration, sensor
calibration, flight modes, parameter export, and PX4 log review.

## Allowed Flight-Control Interactions

The companion computer may:

- read MAVLink heartbeat and telemetry
- read attitude, local state, GPS state, optional barometer, and estimator status
- publish external-vision `ODOMETRY` measurements during controlled bench tests
- publish `VISION_POSITION_ESTIMATE` only as a compatibility/debug path

The companion computer should not:

- arm the drone
- command throttle
- switch to autonomous flight modes
- disable GPS, RC, battery, or failsafe protections

## Prop-Off Hardware Rule

For the Holybro X500 V2 arrival test, propellers stay removed. The only accepted
outputs are logs, reports, parameter checks, support bundles, and optional
MAVLink external-vision messages with the vehicle safely restrained.

## Navigation Health Checks

The estimator should track:

- time since last camera frame
- time since last successful map match
- feature count or landmark count
- reprojection or matching error
- covariance/confidence
- visual scale confidence
- sensor timestamp offset where available
- external-position send/skip reason
- estimator reset count

## Failure Behavior

When localization quality is poor, the system should explicitly switch to a
degraded or failed state. The correct behavior is to stop publishing trusted
navigation updates or mark them as low quality, not to continue producing
plausible-looking but untrustworthy positions.
