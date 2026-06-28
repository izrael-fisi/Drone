# Drone GNSS-Denied Vision Navigation

This repository contains the two active parts of the GNSS-denied navigation
project:

1. Drone runtime code for the Raspberry Pi companion computer.
2. A ground-control / mission-planner desktop app.

ROS 2, Gazebo, and PX4 SITL are no longer part of the active project scaffold.
Hardware bench testing with the real Pixhawk/Holybro stack is the path forward.

The project goal is narrow:

- Downward camera visual map-relative localization
- Precomputed map features from georeferenced imagery, including GeoTIFF-derived map metadata
- Runtime feature matching with confidence scoring
- Approximate map-pixel to lat/lon conversion for simple orthophoto bundles
- Pixhawk/PX4 MAVLink integration as a navigation source after bench validation

The first implementation is intentionally classical computer vision:

```text
downward global-shutter frame
  -> undistort / normalize
    -> ORB or AKAZE feature extraction
      -> match against precomputed map-tile descriptors
        -> RANSAC homography verification
          -> confidence/covariance estimate
            -> log result
            -> later publish to PX4 external vision
```

Do not start with AI accelerators or model training. Add neural features only
after the classical pipeline has been measured and its failure modes are known.

## Current Hardware Target

- Raspberry Pi 5 16GB
- Raspberry Pi Global Shutter Camera, downward-facing
- 256GB microSD for current logs and map bundles
- Optional USB 3 SSD later for larger maps, image logs, and long test runs
- Holybro X500 V2 kit with Pixhawk 6C-class flight controller
- IMU/attitude telemetry from PX4 for vision-estimator context
- Optional PX4 barometer telemetry for relative vertical confidence

## Active Project Sections

### 1. Drone Code Operation

- `src/vision_nav/`: terrain bundle, map matching, estimator, MAVLink, camera,
  field-evidence, and support-bundle tools
- `scripts/pi/`: Raspberry Pi bootstrap, camera checks, MAVLink checks, bundle
  validation, terrain runtime, field capture, and support-bundle wrappers
- `scripts/mac/`: Mac transfer and SSH helpers for moving bundles/logs between
  the desktop and Raspberry Pi
- `docker/pi/`: optional reproducible Pi runtime container
- `config/`: camera and Pi runtime configuration examples

### 2. Ground Control / Mission Planner Desktop App

- `desktop-app/`: Tauri + React operator app for map selection/import, Vision
  Pipeline configuration, Mission Planner layers, module setup, camera preview,
  MAVLink-enabled runtime checks, and support-bundle review
- `docs/`: hardware-first setup, calibration, mission planning, and test plans
- `transfer/`: local staging folder for Mac-to-Pi and Pi-to-Mac file movement

Run the desktop UI without installing Node on Windows by using Docker Desktop:

```powershell
.\scripts\dev\desktop_docker.ps1 dev
```

Then open `http://localhost:5173`. Use `.\scripts\dev\desktop_docker.ps1 build`
for a production frontend build, or `preview` to serve the built output at
`http://localhost:4173`. The Docker runtime is for the React operator UI and
browser fallback/Edge API workflows; the native Tauri window still requires the
local Windows Tauri/Rust toolchain.

Key docs:

- [Stable Project Scaffold](docs/stable-project-scaffold.md)
- [Raspberry Pi Setup](docs/raspberry-pi-setup.md)
- [Camera Calibration](docs/camera-calibration.md)
- [Vision Pipeline](docs/vision-pipeline.md)
- [Holybro X500 V2 Hardware Data Inputs](docs/holybro-x500v2-hardware-data-inputs.md)
- [Holybro X500 V2 Prop-Off Hardware Test](docs/holybro-x500v2-prop-off-hardware-test.md)
- [PX4 External Vision Bench Guide](docs/px4-external-vision-bench.md)
- [Windows 11 Codex Handoff](docs/windows-11-codex-handoff.md)
- [SSH And File Transfer](docs/ssh-and-transfer.md)
- [Desktop App](docs/desktop-app.md)
- [Operator Handoff](docs/operator-handoff.md)

## Quick Pi Setup

On the Raspberry Pi:

```bash
git clone https://github.com/izrael-fisi/Drone.git
cd Drone
chmod +x scripts/pi/*.sh
./scripts/pi/bootstrap_pi5.sh
sudo reboot
```

After reboot:

```bash
cd Drone
./scripts/pi/first_run_checks.sh
```

Rebooting after bootstrap makes Docker group membership and camera/system
services fully active.

Enable the companion Edge API used by the desktop app:

```bash
cd ~/Drone
./scripts/pi/install_vision_nav_service.sh
sudo loginctl enable-linger $USER
systemctl --user start drone-vision-nav-api.service
systemctl --user start drone-vision-nav-status-bridge.service
curl http://127.0.0.1:5000/health
```

After you copy a mission map bundle to `~/drone-data/map_bundles/mission_bundle`,
validate it, then start the logging-only bench loop:

```bash
./scripts/pi/validate_terrain_bundle.sh
vision-nav-map-health --bundle ~/drone-data/map_bundles/mission_bundle
./scripts/pi/run_terrain_nav_loop.sh
```

For transfer-safe mission bundles, write checksums after building features:

```bash
vision-nav-build-terrain-bundle --bundle mission_bundle --write-checksums
```

It captures camera frames, matches them against the bundle, and writes logs to
`~/DroneTransfer/outgoing/terrain-match/`.
By default, Pi runtime matching undistorts frames with
`config/camera/down_camera.yaml`.
When `VISION_NAV_MAVLINK_ENDPOINT` is set, accepted local map measurements can
be sent over MAVLink. Use `VISION_NAV_MAVLINK_MESSAGE=odometry` for the preferred
PX4 external-vision bench path, or `vision_position_estimate` only as a
compatibility/debug mode.
MAVLink-enabled logs include external-position health with send rate, latency,
skip reasons, and covariance warnings.

Replay saved frames without using the camera:

```bash
./scripts/pi/replay_terrain_nav_log.sh
```

Summarize runtime and replay match logs:

```bash
./scripts/pi/summarize_vision_nav_logs.sh
vision-nav-evaluate-replay-gates --case-name bench --expected good_map --log ~/DroneTransfer/outgoing/terrain-match/terrain_matches.jsonl
```

Package a bench run for debugging or desktop transfer:

```bash
./scripts/pi/create_support_bundle.sh
```

Before committing or handing the repo to the Pi:

```bash
./scripts/dev/handoff_audit.sh
```
