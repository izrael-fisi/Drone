# Project Summary

## Project Vision

Build a low-cost drone navigation stack that can estimate position in GNSS-denied environments using onboard computer vision, inertial data, altitude sensing, and pre-installed maps.

The project is not a general autonomy, chatbot, or mission-command project. The only goal is GNSS-denied vision navigation and the flight-control/compute dependencies needed to validate it.

## Primary Objective

Produce a reliable navigation estimate when GNSS is unavailable or untrusted.

The navigation estimate should include:

- Local pose and velocity
- Global georeferenced position when map matching supports it
- Covariance or confidence score
- Timestamp and estimator health
- Failure/degraded-state reporting

## Preferred Output Strategy

Do not force everything into NMEA.

Preferred outputs:

1. ROS 2 `nav_msgs/Odometry` or pose-with-covariance topics for internal robotics use.
2. PX4-compatible external vision via MAVLink `ODOMETRY` or `VISION_POSITION_ESTIMATE` when feeding local pose into the PX4 estimator.
3. MAVLink `GPS_INPUT` only when intentionally presenting the output as a GPS-like global sensor input.
4. Optional NMEA adapter only for downstream systems that specifically require NMEA sentences.

## Planned Hardware

Known purchases/planned modules:

- Raspberry Pi 5 16GB as the companion computer
- Holybro Pixhawk 6X Standard V2A as the flight controller
- Raspberry Pi AI HAT+ 2 only if local inference benchmarks require acceleration

Likely low-cost sensor path:

- Fixed-focus global-shutter camera if budget allows
- Raspberry Pi camera or low-cost UVC camera for early tests
- Optional Pixhawk barometer telemetry for relative vertical confidence
- Optional optical flow only after simulation/bench tests show clear value

## Software Architecture

```text
Sensor acquisition
  -> camera calibration and time synchronization
    -> VIO / visual odometry
      -> map matching / visual relocalization
        -> estimator fusion
          -> navigation output bridge
            -> ROS 2 topics
            -> PX4/MAVLink estimator input
            -> optional legacy output adapters
```

## Development Machines

M1 MacBook Pro, 16GB RAM:

- Code editing
- Git and documentation
- QGroundControl
- Light Python tools and smaller tests

Desktop PC, 24GB RAM, RTX 3060:

- Main development machine
- Ubuntu 22.04 LTS dual boot preferred
- PX4 SITL
- Gazebo
- ROS 2 Humble
- Computer vision and VIO experiments
- Model benchmarking and training experiments

Raspberry Pi 5:

- Onboard companion-computer deployment target
- Camera capture
- Runtime estimator and output bridge
- AI HAT+ 2 benchmarks if needed

## Major Decisions

- Use ROS 2 for perception, localization, sensor fusion, logging, and replay.
- Use PX4 SITL and Gazebo before relying on physical flight tests.
- Use PX4 external-vision paths before pretending a vision estimate is ordinary GNSS.
- Keep NMEA as optional, not central.
- Treat Theseus products/docs as architecture inspiration, not a direct implementation target, because Cyclops and Micro VPS currently document ArduPilot support rather than PX4 support.
- The referenced `evansfsu/Macula` GitHub URL could not be publicly verified; do not base technical assumptions on it until the correct public repository is available.
