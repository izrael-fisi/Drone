# References And Inspiration

## Purpose

This document records references that are relevant to the GNSS-denied vision navigation goal. It should only include claims that have been verified from accessible sources.

## evansfsu/Macula

Requested reference: https://github.com/evansfsu/Macula

Status: not publicly verifiable at the time of review.

The GitHub connector and direct public GitHub access returned `404 Not Found` for the repository URL. Do not make architecture or implementation decisions based on this repository until the correct public URL or repo access is available.

## Theseus Cyclops

Docs: https://docs.theseus.us/cyclops/getting-started

Relevant verified points:

- Cyclops is described as a software-only visual positioning system for fixed-wing UAVs in GPS-denied environments.
- It uses onboard camera imagery and reference maps to estimate position.
- It runs on commercial ARM64 edge hardware, with Raspberry Pi 5 listed as tested/recommended in the docs.
- It requires camera calibration, camera-to-flight-controller pose, map generation, bench tests, and flight tests.
- The docs currently state Cyclops supports ArduPilot and fixed-wing platforms, not PX4 quadcopters.

Useful inspiration:

- Treat camera calibration and camera-to-FC extrinsics as first-class configuration.
- Require bench validation before flight.
- Use reference maps as the basis for global position correction.
- Enforce time synchronization between flight controller and companion computer.
- Prefer fixed-focus/global-shutter cameras when practical.

Direct limitations for this project:

- The planned vehicle is a PX4 quadcopter, while Cyclops docs currently describe ArduPilot fixed-wing support.
- Cyclops is therefore an architecture reference, not a drop-in component.

## Theseus Micro VPS

Docs: https://docs.theseus.us/micro-vps/theseus-micro-vps

Relevant verified points:

- Micro VPS is described as a camera-based daytime visual positioning system for GPS-denied drone navigation.
- Its docs describe a sensor module, compute module, UART serial cables, and flight-controller integration.
- Its autopilot docs currently state ArduPilot support.
- The docs say Micro VPS sends GPS data to ArduPilot over MAVLink and reads compass/barometer data over MAVLink.

Useful inspiration:

- A GPS-like MAVLink output can be useful as an integration strategy.
- The navigation system should pull attitude/altitude-related data from the flight controller.
- A modular sensor + compute package is a useful product shape for low-cost repeatable drones.

Direct limitations for this project:

- The project should not assume ArduPilot-specific EKF parameters or Lua switch scripts apply to PX4.
- PX4 integration should use PX4-supported external vision and estimator interfaces.

## PX4 External Vision

Docs: https://docs.px4.io/main/en/ros/external_position_estimation

Relevant verified points:

- PX4 accepts external position information through MAVLink messages such as `VISION_POSITION_ESTIMATE` and `ODOMETRY`.
- PX4 maps those messages to `vehicle_visual_odometry` for EKF2 use.
- `ODOMETRY` is the richer path because it can include linear velocity.
- PX4 can use `SET_GPS_GLOBAL_ORIGIN` to give a local pose estimate a global origin for mission-like behavior.

## PX4 EKF2 External Vision Fusion

Docs: https://docs.px4.io/main/en/advanced_config/tuning_the_ecl_ekf.html#external-vision-system

Relevant verified points:

- PX4 EKF2 can fuse external vision position, velocity, and orientation measurements.
- Fusion behavior is configured through `EKF2_EV_CTRL` bits.
- Covariance/uncertainty matters and can be supplied through MAVLink `ODOMETRY` covariance fields or PX4 parameters.

## MAVLink Message References

Docs: https://mavlink.io/en/messages/common.html

Relevant messages:

- `ODOMETRY`
- `VISION_POSITION_ESTIMATE`
- `GLOBAL_VISION_POSITION_ESTIMATE`
- `GPS_INPUT`

## Current Architecture Implication

The best project path is:

```text
ROS 2 estimator output
  -> nav_msgs/Odometry with covariance and diagnostics
    -> PX4 external vision via MAVLink ODOMETRY or VISION_POSITION_ESTIMATE
      -> optional GPS_INPUT or NMEA adapters only when a specific consumer requires them
```
