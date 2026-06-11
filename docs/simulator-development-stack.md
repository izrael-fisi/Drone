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

Native Ubuntu is preferred for ROS 2 networking, Gazebo GUI simulation, USB Pixhawk access, GPU acceleration, and DDS discovery.

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
pip install numpy scipy opencv-python pydantic pytest pytest-asyncio rich
pip install mavsdk pymavlink geographiclib pyproj
pip install torch torchvision ultralytics
```

Add specialized VIO/SLAM dependencies only after selecting a specific package to evaluate.

## ROS 2 Packages

Useful ROS packages:

```bash
ros-humble-desktop
ros-dev-tools
ros-humble-cv-bridge
ros-humble-image-transport
ros-humble-vision-opencv
ros-humble-tf2-ros
ros-humble-tf-transformations
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

The `gz_x500_depth` and `gz_x500_vision` targets are relevant for camera, depth, visual odometry, and GNSS-denied localization experiments.

## ROS 2 Integration

PX4 ROS 2 integration path:

```text
PX4 SITL
  -> uXRCE-DDS client in PX4
    -> Micro XRCE-DDS Agent
      -> ROS 2 topics using px4_msgs
```

ROS 2 should be used for:

- Camera transport
- Calibration metadata
- Visual odometry / VIO
- Map matching
- Estimator fusion
- Pose/odometry output
- ROS bag recording and replay
- PX4 external-vision bridge

MAVSDK-Python and pymavlink can be used for:

- Telemetry checks
- PX4/Pixhawk bench integration
- Sending or inspecting MAVLink external-position messages
- Simple flight-control validation scripts

## MacBook Role

The M1 MacBook Pro should be used for:

- Code editing
- Git work
- QGroundControl
- Documentation
- Light Python tools
- Small simulation checks if desired

It should not be the primary machine for heavy ROS 2/Gazebo/CV work unless necessary.

## Raspberry Pi Role

The Raspberry Pi 5 should eventually run:

- Camera capture
- Time synchronization services
- MAVLink Router or direct MAVLink bridge
- ROS 2 nodes that need to run onboard
- Visual odometry or map-matching runtime if performance allows
- Navigation output bridge to PX4

Keep separate Pi images early:

1. Ubuntu Server 22.04 or 24.04 arm64 for ROS 2 and flight integration
2. Raspberry Pi OS 64-bit for Raspberry Pi AI HAT+ 2 / Hailo testing

## Docker Guidance

Use Docker inside Ubuntu for:

- Repeatable Python/ROS experiments
- Dataset-processing tools
- Model training/inference environments
- CI-style test runs

Do not make Windows Docker/WSL2 the primary full-stack simulation environment unless necessary.
