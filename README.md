# Drone GNSS-Denied Vision Navigation

This repository contains the setup scripts and first prototype pipeline for a
Raspberry Pi 5 + Raspberry Pi Global Shutter Camera navigation module.

The project goal is narrow:

- Downward camera visual map-relative localization
- Precomputed map features from georeferenced imagery, including GeoTIFF-derived map metadata
- Runtime feature matching with confidence scoring
- Approximate map-pixel to lat/lon conversion for simple orthophoto bundles
- PX4/Pixhawk integration as a navigation source after bench validation

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
- Pixhawk/PX4 flight controller later
- IMU/attitude telemetry from PX4 for vision-estimator context
- Optional PX4 barometer telemetry for relative vertical confidence

## Main Folders

- `scripts/pi/`: Raspberry Pi bootstrap, Docker, SSH, and transfer setup
- `scripts/mac/`: Mac transfer and SSH helpers
- `docker/pi/`: Docker runtime for the Pi vision environment
- `desktop-app/`: Tauri + React desktop operator app for map selection/import, Vision Pipeline configuration, QGC-style Mission Planner layers, module setup, camera preview, and MAVLink-enabled runtime checks
- `src/vision_nav/`: First feature-map and frame-to-map matching tools
- `transfer/`: Local staging folder for Mac-to-Pi and Pi-to-Mac file movement
- `docs/`: Setup and architecture notes

Key docs:

- [Raspberry Pi Setup](docs/raspberry-pi-setup.md)
- [Camera Calibration](docs/camera-calibration.md)
- [Vision Pipeline](docs/vision-pipeline.md)
- [Autonomy And Ground Control Research](docs/autonomy-ground-control-research.md)
- [Autonomy And Ground Control Implementation Plan](docs/autonomy-ground-control-implementation-plan.md)
- [PX4 External Vision Bench Guide](docs/px4-external-vision-bench.md)
- [ROS 2 Runtime Adapter](docs/ros2-runtime.md)
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
When `VISION_NAV_MAVLINK_ENDPOINT` is set, accepted local map measurements are
also sent as MAVLink `VISION_POSITION_ESTIMATE`. Set
`VISION_NAV_MAVLINK_MESSAGE=odometry` to bench MAVLink `ODOMETRY` output.
MAVLink-enabled logs include external-position health with send rate, latency,
skip reasons, and covariance warnings.

For ROS 2 bench work, set `VISION_NAV_ROS2_PUBLISH=1` before
`./scripts/pi/run_terrain_nav_loop.sh` to publish `/vision_nav/odometry` and
`/diagnostics` while the terrain runtime is running.

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
