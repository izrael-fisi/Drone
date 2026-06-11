# Drone

GNSS-denied vision navigation project for low-cost UAVs.

## Single Project Goal

Build a drone navigation system that uses onboard vision, inertial data, altitude sensing, and pre-installed maps to estimate vehicle position when GNSS is unavailable, degraded, or untrusted.

The system should prioritize high accuracy, low hardware cost, and repeatable deployment across many drones. Output should use the best interface for the consumer, not default to NMEA. Preferred internal outputs are ROS 2 pose/odometry messages with covariance and PX4-compatible external-vision or GPS-like MAVLink inputs. NMEA can remain an optional adapter only if a downstream system specifically requires it.

## Hardware Direction

Planned core modules:

- Flight controller: Holybro Pixhawk 6X Standard V2A running PX4
- Companion computer: Raspberry Pi 5 16GB
- Optional AI acceleration: Raspberry Pi AI HAT+ 2, only if benchmarks require it
- Primary development workstation: desktop PC with Ubuntu 22.04 LTS, 24GB RAM, RTX 3060
- Secondary development machine: M1 MacBook Pro for editing, QGroundControl, documentation, and light tests

Low-cost sensor bias:

- Prefer fixed-focus global-shutter cameras where possible
- Use Raspberry Pi camera or low-cost UVC camera modules first
- Add a rangefinder if vertical/height-above-ground quality needs improvement
- Add optical flow only if tests show it improves low-altitude hold or velocity estimation enough to justify the extra module

## System Architecture

```text
Camera + IMU + barometer/rangefinder
  -> timestamped sensor capture
    -> visual odometry / visual-inertial odometry
      -> map matching / visual relocalization
        -> estimator fusion and confidence scoring
          -> local pose + global pose estimate
            -> ROS 2 odometry/pose output
            -> PX4 external-vision or GPS-like MAVLink input
            -> optional NMEA adapter if required
```

PX4 owns flight stabilization, arming, failsafes, and low-level control. The companion computer owns vision processing, map matching, estimator fusion, and navigation-source output.

## Documentation

- [Project Summary](docs/project-summary.md)
- [GNSS-Denied Vision Navigation](docs/gnss-denied-vision-navigation.md)
- [Simulator Development Stack](docs/simulator-development-stack.md)
- [Localization Output Interfaces](docs/localization-output-interfaces.md)
- [Flight Control And Compute Boundaries](docs/flight-control-and-compute-boundaries.md)
- [References And Inspiration](docs/references-and-inspiration.md)
- [Software Download Checklist](docs/software-download-checklist.md)
- [Project Plan](docs/project-plan.md)

## Design Rule

Every component in this repository should directly support GNSS-denied vision navigation, estimator fusion, flight-controller integration, simulator validation, or onboard compute benchmarking. Anything outside that scope should stay out of the project.
