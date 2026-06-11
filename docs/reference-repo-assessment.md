# Reference Repo Assessment: PeterJBurke/droneserver

Reference: https://github.com/PeterJBurke/droneserver

## Verdict

`PeterJBurke/droneserver` is a useful reference, but should not be used directly as this project's safety-critical foundation.

It is relevant because it combines:

- Python
- MAVSDK
- MAVLink
- MCP
- LLM-oriented drone commands
- Telemetry tools
- Mission monitoring concepts

However, the current project should remain PX4-native and simulator-first, with ROS 2 integrated from the beginning for computer vision and GNSS-denied autonomy.

## Useful Ideas To Borrow

- MCP tool naming and structure
- High-level commands such as `arm`, `takeoff`, `land`, `get_position`, and `monitor_flight`
- The idea of a mission lifecycle manager
- Landing gates and command validation
- User-visible progress monitoring
- Logging all LLM/MCP tool calls and vehicle commands

## Reasons Not To Use Directly As The Base

- It is monolithic.
- Its docs describe manual testing rather than automated tests.
- It appears more ArduPilot-oriented than PX4-oriented.
- It does not cover ROS 2 perception, visual localization, offboard autonomy, or GNSS-denied navigation.
- Its own documentation includes a crash report around unsafe mission pause behavior that later had to be deprecated.

## How This Project Should Differ

This project should be structured around clear modules:

```text
mcp_server
  -> exposes safe high-level tools

mission_manager
  -> owns state machine and safety policy

vehicle_adapters
  -> MAVSDK and PX4 ROS 2 interfaces

ros2_nodes
  -> perception, localization, planning, NMEA output

sim_tests
  -> PX4 SITL and Gazebo test cases
```

## Takeaway

Use `droneserver` as a learning reference for MCP/MAVSDK patterns. Build a new, testable, PX4/ROS 2-centered architecture for this project.
