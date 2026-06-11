# Software Download Checklist

## Desktop PC

Recommended: keep Windows installed and add Ubuntu 22.04 LTS as a dual boot.

Install on Ubuntu:

- Ubuntu 22.04 LTS
- NVIDIA proprietary driver
- Git
- Git LFS
- VS Code or Cursor
- Python 3.10
- `python3-venv`
- `pipx`
- `uv`
- Docker
- NVIDIA Container Toolkit, optional
- QGroundControl
- PX4-Autopilot
- ROS 2 Humble Desktop
- Micro XRCE-DDS Agent
- `px4_msgs`

## MacBook Pro

Install:

- Xcode Command Line Tools
- Homebrew
- Git
- Git LFS
- VS Code or Cursor
- Python through Homebrew or `pyenv`
- `uv`
- QGroundControl
- Optional PX4 macOS simulation toolchain

Recommended Mac role:

- Editing
- QGroundControl
- Git and documentation
- Dataset review
- Light Python tools
- Small simulation checks

## Raspberry Pi 5

Keep two boot images early:

1. Ubuntu Server 22.04 or 24.04 arm64
   - ROS 2
   - camera drivers
   - MAVLink Router or direct MAVLink bridge
   - Micro XRCE-DDS Agent if needed onboard
   - visual-navigation runtime services

2. Raspberry Pi OS 64-bit
   - Raspberry Pi AI HAT+ 2 / Hailo testing
   - camera and inference benchmarks

## Python Project Packages

Initial packages:

```bash
pip install numpy scipy opencv-python pydantic pytest pytest-asyncio rich
pip install mavsdk pymavlink geographiclib pyproj
pip install torch torchvision ultralytics
```

Later candidates to evaluate, not install blindly:

- OpenVINS
- ORB-SLAM3 or other VIO/SLAM stack
- RTAB-Map if map-building experiments require it
- ONNX Runtime
- Hailo runtime packages for AI HAT+ 2

## First Simulation Commands

From the PX4-Autopilot repo:

```bash
make px4_sitl gz_x500
make px4_sitl gz_x500_depth
make px4_sitl gz_x500_vision
```

## Docker Guidance

Use Docker inside Ubuntu for reproducible experiments, dataset processing, model training, and isolated ROS/Python services. Do not make Windows Docker/WSL2 the primary full-stack simulation environment unless necessary.
