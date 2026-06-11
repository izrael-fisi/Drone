# Drone

Simulator-first autonomy project for a low-cost quadcopter platform using PX4, Pixhawk, Raspberry Pi, ROS 2, computer vision, and MCP/LLM-assisted high-level control.

## Current Goal

Design a drone navigation stack that can use computer vision plus pre-installed maps to produce NMEA-style geolocation data in GNSS-denied environments. The project should favor inexpensive, repeatable modules so the cost per drone stays low.

## Planned Hardware Direction

- Flight controller: Holybro Pixhawk 6X Standard V2A running PX4
- Companion computer: Raspberry Pi 5 16GB
- AI acceleration: Raspberry Pi AI HAT+ 2 only if benchmarks show the Pi CPU/GPU path is insufficient
- Development machines:
  - M1 MacBook Pro, 16GB RAM, for editing, QGroundControl, Python/MCP work, and light simulation
  - Desktop PC, 24GB RAM, RTX 3060, recommended as the main Ubuntu ROS 2/PX4/Gazebo/CV workstation

## Architecture Summary

```text
LLM / UI / CLI
  -> MCP command server
    -> Safety and mission-policy layer
      -> Mission manager
        -> MAVSDK-Python for simple vehicle actions
        -> ROS 2 for perception, localization, planning, and offboard autonomy
          -> PX4 SITL / Gazebo during simulation
          -> Pixhawk 6X + Raspberry Pi 5 on hardware
```

PX4 remains responsible for flight control, stabilization, arming, failsafes, and low-level safety. ROS 2 handles perception, localization, and autonomy. MCP/LLM control is limited to high-level mission intents, never raw motor or attitude commands.

## Development Strategy

1. Build and test everything in PX4 SITL and Gazebo before prioritizing physical flight.
2. Use ROS 2 early because computer vision recognition is part of the end product.
3. Use MAVSDK-Python for simple command/control workflows.
4. Use Micro XRCE-DDS and `px4_msgs` when ROS 2 needs direct PX4 topic integration.
5. Add physical Pixhawk/Raspberry Pi integration after the simulator stack is stable.

## Documentation

- [Project Summary](docs/project-summary.md)
- [Simulator Development Stack](docs/simulator-development-stack.md)
- [GNSS-Denied Vision Navigation Goal](docs/gnss-denied-vision-navigation.md)
- [Reference Repo Assessment](docs/reference-repo-assessment.md)

## Key Safety Rule

The LLM should never directly control motors, publish raw low-level setpoints, or bypass PX4 failsafes. It should request mission-level actions that are validated by a deterministic safety gate.
