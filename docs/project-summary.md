# Project Summary

## Project Vision

Build a low-cost drone navigation stack that can estimate position in
GNSS-denied environments using onboard learned computer vision,
flight-controller attitude telemetry, and pre-installed georeferenced maps.

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

The active navigation runtime is `FramePacket -> learned shadow inference ->
decision trace/runtime status`. It keeps map coordinates internally in ENU,
uses named camera calibration and monotonic timestamps, and fails closed on
stale, ambiguous, invalid, or incompatible evidence. `Px4VisionBridge` remains
future-gated; the current release never publishes vehicle position.

The navigation estimate should include:

- local east/north/down or east/north position where validated
- optional global georeferenced position when map matching supports it
- covariance or confidence score
- timestamp and estimator health
- explicit failed/degraded-state reporting

## Preferred Output Strategy

The future vehicle output path is MAVLink `ODOMETRY` to PX4/Pixhawk, only after
the new runtime reaches `TRACKING` with finite/fresh covariance, a compatible
map/model/calibration release, and healthy transport. The existing ORB/AKAZE
terrain loops are map/replay evidence paths and do not publish PX4 vision.

Preferred outputs:

1. MAVLink `ODOMETRY` from `Px4VisionBridge` after the architecture validation
   ladder has been completed.
2. GPS stays an independent telemetry source for comparison/fallback policy;
   it is not rewritten by this project.
3. Optional NMEA remains a downstream-format adapter, never a navigation
   authority.

## Planned Hardware

Known hardware target:

- Raspberry Pi 5 16GB as the companion computer
- Holybro X500 V2 kit
- Pixhawk 6C-class flight controller from the Holybro kit
- Holybro M8N GPS module from the kit, used for setup/ground-truth comparison
  before GNSS-denied operation
- SiK telemetry radio from the kit for QGroundControl/PX4 parameter/log access
- Raspberry Pi camera or global-shutter camera for downward map matching
- Optional Raspberry Pi AI HAT+ only after CPU benchmarks prove it is needed

## Software Architecture

```text
Drone code operation
  -> camera capture and calibration
  -> versioned map release and calibrated frame ingress
  -> learned MegaLoc recovery retrieval and XFeat/LighterGlue correspondence
  -> distilled temporal BEV/bearing context and robust geometry
  -> hypothesis bank and graph estimate with covariance
  -> runtime log and status artifacts
  -> PX4 ODOMETRY only through the versioned publication gate

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
