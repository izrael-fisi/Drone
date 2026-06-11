# Simulator Development Stack

## Recommended Host Setup

Use the desktop PC as the main robotics development machine.

Recommended setup:

```text
Desktop PC
  Windows 11 for normal use
  Ubuntu 22.04 LTS dual boot for PX4 / ROS 2 / Gazebo / CV
    Docker inside Ubuntu for reproducible services and experiments
```

Avoid using Windows + WSL2/Docker as the primary environment for the full stack. It can work for pieces of the project, but ROS 2 networking, Gazebo GUI simulation, USB Pixhawk access, GPU acceleration, and DDS discovery are usually smoother on native Ubuntu.

## Core Software To Install

On Ubuntu 22.04:

- Git
- Git LFS
- VS Code or Cursor
- Python 3.10
- `python3-venv`
- `pipx`
- `uv`
- QGroundControl
- PX4-Autopilot
- Gazebo Harmonic through PX4 setup scripts
- ROS 2 Humble Desktop
- Micro XRCE-DDS Agent
- `px4_msgs`
- Docker
- NVIDIA driver
- NVIDIA Container Toolkit, optional but useful

## Python Packages

Initial Python project dependencies:

```bash
pip install mavsdk mcp fastapi uvicorn pydantic python-dotenv pytest pytest-asyncio rich
pip install numpy opencv-python ultralytics torch torchvision
```

Use pinned versions later once the repo has a working baseline.

## ROS 2 Packages

Useful ROS packages:

```bash
ros-humble-desktop
ros-dev-tools
ros-humble-cv-bridge
ros-humble-image-transport
ros-humble-vision-opencv
ros-humble-tf2-ros
ros-humble-rviz2
ros-humble-rqt
ros-humble-rosbag2
```

## PX4 Simulation Targets

Start with:

```bash
make px4_sitl gz_x500
```

Then test sensor-rich variants:

```bash
make px4_sitl gz_x500_depth
make px4_sitl gz_x500_vision
```

The `gz_x500_depth` and `gz_x500_vision` targets are especially relevant for computer vision, depth perception, and GNSS-denied localization experiments.

## ROS 2 Integration

PX4 ROS 2 integration path:

```text
PX4 SITL
  -> uXRCE-DDS client in PX4
    -> Micro XRCE-DDS Agent
      -> ROS 2 topics using px4_msgs
```

ROS 2 should be used for:

- Perception nodes
- Visual localization
- Map matching
- Offboard setpoint generation
- Autonomy behavior trees or mission state machines
- Logging and replay with rosbag2

MAVSDK-Python should be used for:

- Arm
- Disarm
- Takeoff
- Land
- Return to launch
- Simple position/mission commands
- Basic telemetry

## MacBook Role

The M1 MacBook Pro should be used for:

- Code editing
- Git work
- QGroundControl
- Python service development
- MCP server experiments
- Light PX4 simulation if desired

It should not be the primary machine for heavy ROS 2/Gazebo/CV work unless necessary.

## Raspberry Pi Role

The Raspberry Pi 5 should eventually run:

- Companion-computer services
- MAVLink Router
- MAVSDK-Python components
- ROS 2 nodes that need to run onboard
- MCP bridge or local mission manager, if appropriate
- Vision inference only if benchmarks show it can meet latency requirements

Keep separate Pi images early:

1. Ubuntu Server 22.04 arm64 for ROS 2 Humble and flight integration
2. Raspberry Pi OS 64-bit for Raspberry Pi AI HAT+ 2 / Hailo testing

## Docker Guidance

Use Docker inside Ubuntu for:

- Repeatable Python/MCP services
- ROS 2 node experiments
- Model training/inference environments
- CI-style test runs

Do not make Windows Docker/WSL2 the main flight-simulation environment unless there is a strong reason.
