# Project Summary

## Project Vision

Build a low-cost drone navigation stack that can estimate position in
GNSS-denied environments using onboard computer vision, flight-controller
telemetry, optional altitude sensing, and pre-installed georeferenced maps.

The project is not a general autonomy, chatbot, ROS middleware, or simulation
project. The active scope is now hardware-first GNSS-denied vision navigation
and the ground-control tooling needed to configure, test, and review it.

## Active Project Shape

The repo has two active sections:

1. Drone code operation.
2. Ground control / mission planner desktop app.

ROS 2, Gazebo, and PX4 SITL are not active project dependencies. Existing
legacy helper code may remain in the repo until it is safely removed, but new
operator guidance, readiness planning, and hardware prep should not depend on
simulation or ROS 2.

## Primary Objective

Produce a reliable navigation estimate when GNSS is unavailable, degraded, or
intentionally ignored.

The navigation estimate should include:

- local east/north/down or east/north position where validated
- optional global georeferenced position when map matching supports it
- covariance or confidence score
- timestamp and estimator health
- explicit failed/degraded-state reporting

## Preferred Output Strategy

The default output path is MAVLink to PX4/Pixhawk.

Preferred outputs:

1. MAVLink `ODOMETRY` for PX4 external-vision bench/product readiness.
2. MAVLink `VISION_POSITION_ESTIMATE` only as a compatibility/debug path.
3. MAVLink `GPS_INPUT` only when intentionally presenting the output as a
   GPS-like global source, with quality clearly marked.
4. Optional NMEA adapter only for downstream systems that specifically require
   NMEA sentences.

## Planned Hardware

Known hardware target:

- Raspberry Pi 5 16GB as the companion computer
- Holybro X500 V2 kit
- Pixhawk 6C-class flight controller from the Holybro kit
- Holybro M8N GPS module from the kit, used for setup/ground-truth comparison
  before GNSS-denied operation
- SiK telemetry radio from the kit for QGroundControl/PX4 parameter/log access
- Raspberry Pi camera or global-shutter camera for downward map matching
- Optional Pixhawk barometer telemetry for relative vertical confidence
- Optional Raspberry Pi AI HAT+ only after CPU benchmarks prove it is needed

## Software Architecture

```text
Drone code operation
  -> camera capture and calibration
  -> terrain bundle loading and tile retrieval
  -> ORB/AKAZE feature matching
  -> RANSAC geometry checks
  -> estimator confidence/covariance
  -> runtime log and status artifacts
  -> MAVLink external-vision output to Pixhawk when enabled

Ground control / mission planner app
  -> import/select map source
  -> configure vision pipeline defaults
  -> plan mission/fence/rally/vision checkpoints
  -> build/upload terrain bundle
  -> connect to Raspberry Pi over local Wi-Fi/SSH
  -> run camera, bundle, MAVLink, and hardware bench checks
  -> download/review support bundles and field evidence
```

## Development Machines

M1 MacBook Pro:

- code editing
- Git and documentation
- desktop app development/testing
- QGroundControl
- light Python tools and bundle review

Desktop PC:

- desktop app development/testing
- heavier map processing and feature benchmarking
- optional model benchmarking
- no required ROS 2, Gazebo, or SITL workflow

Raspberry Pi 5:

- onboard companion-computer deployment target
- camera capture
- terrain runtime estimator
- MAVLink output bridge
- logging, field capture, and support-bundle creation

## Major Decisions

- Use real hardware bench tests instead of ROS 2/SITL simulation as the active
  validation path.
- Keep the runtime Python-first and Raspberry-Pi-friendly.
- Use PX4 external-vision MAVLink paths before pretending a vision estimate is
  ordinary GNSS.
- Keep NMEA optional, not central.
- Keep optical flow hardware optional until downward-camera tests show a clear
  need.
- Treat Theseus products/docs as product workflow inspiration, not as a direct
  implementation target.
