# Project Summary

## Project Vision

Build a simulator-first drone autonomy platform that can later run on a Pixhawk + Raspberry Pi physical drone. The end-stage product should use computer vision recognition and pre-installed maps to generate NMEA-style geolocation output in GNSS-denied environments.

The project should prioritize inexpensive, repeatable hardware modules so each drone can be built at a reasonable cost.

## Current Priorities

Physical flight is not the immediate priority. The immediate goal is to build a robust software stack in simulation, then move to Pixhawk/Raspberry Pi hardware once the core autonomy design is proven.

Primary early work:

- PX4 SITL simulation
- Gazebo X500 drone models
- ROS 2 integration
- MAVSDK-Python command/control
- MCP server for high-level LLM commands
- Computer vision recognition pipeline
- GNSS-denied localization concept using maps and vision

## Planned Hardware

Known purchases/planned modules:

- Raspberry Pi 5 16GB as the companion computer
- Holybro Pixhawk 6X Standard V2A as the flight controller
- Raspberry Pi AI HAT+ 2 if local AI inference requires acceleration

Possible later modules:

- Low-cost global-shutter or rolling-shutter camera module
- Depth camera only if needed for visual odometry or obstacle awareness
- Rangefinder for altitude/landing support
- Optical flow sensor only if low-cost indoor velocity hold is needed and vision/VIO is insufficient

## Software Architecture

```text
LLM / UI / CLI
  -> MCP command server
    -> Safety and mission-policy layer
      -> Mission manager
        -> MAVSDK-Python for simple vehicle actions
        -> ROS 2 for perception, localization, planning, and offboard autonomy
          -> PX4 SITL / Gazebo
          -> Pixhawk 6X + Raspberry Pi 5 on hardware
```

## Control Philosophy

PX4 owns hard flight safety:

- Stabilization
- Failsafes
- Arming checks
- Return-to-launch behavior
- Flight modes
- Low-level control loops

ROS 2 owns autonomy:

- Camera input
- Computer vision recognition
- Visual localization
- Map matching
- Local planning
- Offboard setpoint generation

The MCP/LLM layer owns high-level intent only:

- Start mission
- Inspect target
- Return home
- Hold position
- Land
- Report state

The LLM must not directly control motors, raw attitude, or unsafe low-level setpoints.

## Development Machines

M1 MacBook Pro, 16GB RAM:

- Code editing
- QGroundControl
- Python/MCP development
- Light PX4 simulation

Desktop PC, 24GB RAM, RTX 3060:

- Recommended primary development machine
- Ubuntu 22.04 LTS dual boot preferred
- PX4 SITL
- Gazebo
- ROS 2 Humble
- Computer vision training and inference experiments

Cloud:

- Not required at the start
- Useful later only for heavier model training

## Major Decisions So Far

- Use ROS 2 early because computer vision is an end-stage requirement.
- Keep MAVSDK-Python for simple commands and telemetry.
- Prefer Ubuntu dual boot over Windows-only Docker/WSL for ROS 2/PX4/Gazebo work.
- Use Docker inside Ubuntu for reproducible services and tests.
- Do not buy an optical flow sensor yet.
- Treat PeterJBurke/droneserver as a useful reference, not as the safety-critical foundation.
