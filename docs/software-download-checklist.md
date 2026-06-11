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
- Python/MCP development
- Git and documentation
- Light simulation

## Raspberry Pi 5

Keep two boot images early:

1. Ubuntu Server 22.04 arm64
   - ROS 2 Humble
   - MAVSDK-Python
   - MAVLink Router
   - Micro XRCE-DDS Agent
   - onboard mission services

2. Raspberry Pi OS 64-bit
   - Raspberry Pi AI HAT+ 2 / Hailo testing
   - camera and inference benchmarks

## Python Project Packages

Initial packages:

```bash
pip install mavsdk mcp fastapi uvicorn pydantic python-dotenv pytest pytest-asyncio rich
pip install numpy opencv-python ultralytics torch torchvision
```

## First Simulation Commands

From the PX4-Autopilot repo:

```bash
make px4_sitl gz_x500
make px4_sitl gz_x500_depth
make px4_sitl gz_x500_vision
```

## Docker Guidance

Use Docker inside Ubuntu for reproducible services and experiments. Do not make Windows Docker/WSL2 the primary full-stack simulation environment unless necessary.
