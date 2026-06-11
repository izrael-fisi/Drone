# Project Plan

## Phase 1: Environment Setup

- Install Ubuntu 22.04 LTS dual boot on the desktop PC.
- Install QGroundControl, PX4-Autopilot, Gazebo, ROS 2 Humble, Micro XRCE-DDS Agent, and `px4_msgs`.
- Create Python environment with MAVSDK and MCP dependencies.
- Run PX4 SITL smoke tests with X500 models.

Acceptance criteria:

- `gz_x500` launches successfully.
- QGroundControl connects to PX4 SITL.
- A Python MAVSDK script can read telemetry from SITL.

## Phase 2: Repository Scaffold

- Create Python package structure.
- Add ROS 2 workspace structure.
- Add MCP server skeleton.
- Add mission manager skeleton.
- Add simulation launch scripts.
- Add basic pytest tests.

Acceptance criteria:

- Local tests run.
- Project can start simulator and connect the Python service.

## Phase 3: Basic Vehicle Command Layer

- Implement MAVSDK adapter.
- Implement vehicle state reader.
- Implement safe high-level tools: arm, takeoff, land, RTL, hold.
- Implement command validation and logging.

Acceptance criteria:

- MCP tools can command PX4 SITL through safety checks.
- Invalid commands are rejected.

## Phase 4: ROS 2 Bridge

- Add PX4 uXRCE-DDS integration.
- Subscribe to vehicle status and position topics.
- Publish internal vehicle state topics.
- Record telemetry with rosbag2.

Acceptance criteria:

- ROS 2 can observe PX4 SITL state.
- Logs can be replayed for debugging.

## Phase 5: Vision Pipeline

- Use Gazebo camera/depth/vision model.
- Publish camera frames into ROS 2.
- Add basic object recognition node.
- Add recording/replay workflow.

Acceptance criteria:

- Vision node detects known targets in simulation or recorded data.
- Detection output is timestamped and published as ROS 2 messages.

## Phase 6: GNSS-Denied Localization Prototype

- Define map format.
- Build small georeferenced test map.
- Match visual features or recognized landmarks to map features.
- Estimate pose and confidence.
- Compare pose estimate to simulator ground truth.

Acceptance criteria:

- System produces an estimated position with confidence.
- Error can be measured against ground truth.

## Phase 7: NMEA Output

- Convert estimated position into NMEA-style sentences.
- Include quality/confidence handling.
- Add logs and replay tests.

Acceptance criteria:

- System emits parseable NMEA-style output.
- Output distinguishes estimated vision/map position from true GNSS.

## Phase 8: Raspberry Pi + Pixhawk Bench Integration

- Install companion-computer image on Raspberry Pi 5.
- Connect Raspberry Pi to Pixhawk 6X.
- Verify MAVLink and/or Ethernet communication.
- Run MAVSDK telemetry scripts.
- Run ROS 2 bridge.

Acceptance criteria:

- Pi can read Pixhawk telemetry.
- Pi can run the mission manager without flight hardware attached.

## Phase 9: Hardware Vision Benchmarks

- Test low-cost camera options.
- Benchmark inference on Raspberry Pi 5.
- Benchmark Raspberry Pi AI HAT+ 2 if needed.
- Decide final low-cost vision module.

Acceptance criteria:

- Selected vision hardware meets latency and accuracy requirements for the target mission.

## Phase 10: Physical Flight Later

Physical flight comes only after the simulator and bench systems are stable.

- Assemble drone.
- Configure PX4.
- Run manual flight tests.
- Validate failsafes.
- Test autonomy incrementally.

Acceptance criteria:

- Manual flight is stable.
- Autonomous features pass simulation and bench tests before real flight.
