# Raspberry Pi Setup

This is the active Raspberry Pi setup path for the drone runtime section of the
project. It does not require ROS 2, Gazebo, or PX4 SITL.

## Storage Assumption

The current target uses the onboard 256GB microSD.

Default paths:

```text
~/Drone
~/DroneTransfer/
~/drone-data/
~/drone-data/map_bundles/mission_bundle/
```

Add a USB SSD later only when map bundles or logs outgrow the card.

## Install

On the Pi:

```bash
git clone https://github.com/izrael-fisi/Drone.git
cd Drone
chmod +x scripts/pi/*.sh
./scripts/pi/bootstrap_pi5.sh
sudo reboot
```

Do not run `bootstrap_pi5.sh` with `sudo`; it asks for sudo internally.

## First Run

After reboot:

```bash
cd ~/Drone
./scripts/pi/first_run_checks.sh
```

For a faster check without Docker:

```bash
VISION_NAV_SKIP_DOCKER_SMOKE=1 ./scripts/pi/first_run_checks.sh
```

Only skip camera health when intentionally testing without the camera attached:

```bash
VISION_NAV_SKIP_CAMERA_HEALTH=1 ./scripts/pi/first_run_checks.sh
```

Expected outputs:

```text
~/DroneTransfer/outgoing/pi-info/
~/DroneTransfer/outgoing/camera-health/
~/DroneTransfer/outgoing/vision-smoke/
```

## SSH And Transfer

On the Pi:

```bash
cd ~/Drone
./scripts/pi/enable_ssh_transfer.sh
whoami
hostname
hostname -I
```

On the Mac:

```bash
PI_USER=pi PI_HOST=raspberrypi.local ./scripts/mac/install_pi_ssh_key.sh
PI_USER=pi PI_HOST=raspberrypi.local ./scripts/mac/setup_pi_ssh_config.sh
./scripts/mac/test_pi_ssh.sh
```

Replace `pi` and `raspberrypi.local` with the real values.

## Camera Check

On the Pi:

```bash
rpicam-hello --list-cameras
cd ~/Drone
./scripts/pi/check_global_shutter_camera.sh
./scripts/pi/smoke_test_vision.sh
```

If `rpicam-*` is unavailable, the capture helper tries compatible
`libcamera-*` tools where available.

## Camera Calibration

Capture calibration images:

```bash
cd ~/Drone
./scripts/pi/capture_calibration_set.sh
```

Calibrate:

```bash
source ~/drone_vision_nav_venv/bin/activate
vision-nav-calibrate-camera \
  --images "$HOME/DroneTransfer/outgoing/calibration/down_camera/*.jpg" \
  --output config/camera/down_camera.yaml \
  --camera-name down_camera \
  --cols 9 \
  --rows 6 \
  --square-size-m 0.024 \
  --show-rejections
```

Adjust chessboard values to the actual board. After final mounting, update:

```text
config/camera/camera_to_body.yaml
```

## Mission Bundle

Build/upload the mission bundle from the desktop Mission Planner, or place it
on the Pi manually:

```text
~/drone-data/map_bundles/mission_bundle/
```

Validate:

```bash
cd ~/Drone
VISION_NAV_BUNDLE=$HOME/drone-data/map_bundles/mission_bundle ./scripts/pi/validate_terrain_bundle.sh
vision-nav-map-health --bundle $HOME/drone-data/map_bundles/mission_bundle --json
```

## MAVLink / Pixhawk Check

For prop-off hardware testing, first verify the endpoint only:

```bash
cd ~/Drone
export VISION_NAV_MAVLINK_ENDPOINT=/dev/ttyACM0
./scripts/pi/check_mavlink_endpoint.sh
```

Adjust the endpoint to match the real Pixhawk link. Do not use this project to
arm or command motors.

Export PX4 parameters from QGroundControl to `~/px4.params`, then:

```bash
VISION_NAV_PX4_PARAMS=$HOME/px4.params ./scripts/pi/check_px4_params.sh
```

## Companion Edge API

The desktop app expects the Pi to expose a lightweight companion API on port
`5000`. The API reports device identity, service state, latest
`runtime_status.json`, available serial devices, and MAVLink heartbeat probes.

Install and enable the user services:

```bash
cd ~/Drone
./scripts/pi/install_vision_nav_service.sh
sudo loginctl enable-linger $USER
systemctl --user start drone-vision-nav-api.service
systemctl --user start drone-vision-nav-status-bridge.service
```

Check from the Pi:

```bash
curl http://127.0.0.1:5000/health
curl http://127.0.0.1:5000/api/v1/device
curl -X POST http://127.0.0.1:5000/api/v1/mavlink/heartbeat \
  -H 'Content-Type: application/json' \
  -d '{"endpoint":"serial:/dev/ttyACM0:921600","timeout_s":4}'
```

From the desktop, use `http://<pi-ip>:5000`. Keep service control disabled
unless the Pi is on a trusted private network:

```bash
# Optional, trusted LAN only:
VISION_NAV_API_ALLOW_SERVICE_CONTROL=1 systemctl --user restart drone-vision-nav-api.service
```

Docker alternative:

```bash
cd ~/Drone
VISION_NAV_API_MAVLINK_ENDPOINT=serial:/dev/ttyACM0:921600 \
  ./scripts/pi/start_companion_api_docker.sh
```

## QGroundControl On The Pi

Install the ARM64 QGroundControl AppImage on the companion computer when you
want local Pixhawk setup, parameter inspection, and log access from a Pi desktop
session:

```bash
cd ~/Drone
./scripts/pi/install_qgroundcontrol.sh
qgroundcontrol --appimage-help
```

The installer writes:

- `/opt/qgroundcontrol/QGroundControl-aarch64.AppImage`
- `/usr/local/bin/qgroundcontrol`
- `/usr/local/bin/qgroundcontrol-safe`

The desktop app reads `/api/v1/qgroundcontrol` from the companion Edge API to
show whether QGroundControl is installed, whether a graphical Pi session is
visible, and whether QGroundControl is already running. The app launch action
uses `/api/v1/qgroundcontrol/launch`; it refuses cleanly when the API has no
`DISPLAY` or `WAYLAND_DISPLAY`.

QGroundControl and the telemetry status bridge cannot both own a direct serial
Pixhawk endpoint at the same time. When launching QGroundControl through the app,
the launcher requests that `drone-vision-nav-status-bridge.service` stop first
so QGroundControl can use the Pixhawk link.

## ArduPilot Mission Planner Compatibility

Mission Planner is the primary ArduPilot ground-control application. Native
Windows Mission Planner remains the recommended path for full ArduPilot setup.
For a Raspberry Pi desktop session, the repo includes an optional Mono-based
installer:

```bash
cd ~/Drone
./scripts/pi/install_mission_planner.sh
missionplanner-safe
```

The installer writes:

- `/opt/missionplanner/MissionPlanner.exe`
- `/usr/local/bin/missionplanner`
- `/usr/local/bin/missionplanner-safe`

The companion Edge API exposes `/api/v1/mission-planner` and
`/api/v1/mission-planner/launch`. The desktop app groups QGroundControl and
Mission Planner under the left-nav **Ground Control** pane, where operators can
select PX4 or ArduPilot compatibility, check install/display state, and launch a
GCS after stopping the status bridge to release the serial MAVLink endpoint.

## Runtime Capture

Run the always-on status bridge first when the Pixhawk is connected but the
active map/bundle is not ready yet:

```bash
cd ~/Drone
VISION_NAV_MAVLINK_ENDPOINT=/dev/ttyACM0 \
VISION_NAV_POSITION_UDP_TARGET=255.255.255.255:17660 \
VISION_NAV_COUNT=30 \
./scripts/pi/run_status_bridge.sh
```

This writes `runtime_status.json` and broadcasts live status packets with GPS,
MAVLink, camera, active bundle, runtime profile, and source-state information.

Run logging-only first:

```bash
cd ~/Drone
VISION_NAV_BUNDLE=$HOME/drone-data/map_bundles/mission_bundle \
VISION_NAV_COUNT=30 \
./scripts/pi/run_terrain_nav_loop.sh
```

Read runtime status:

```bash
VISION_NAV_RUNTIME_STATUS_ROOTS=$HOME/DroneTransfer/outgoing/terrain-match \
./scripts/pi/read_runtime_status.sh
```

Enable MAVLink output only after logging-only runtime is healthy:

```bash
VISION_NAV_BUNDLE=$HOME/drone-data/map_bundles/mission_bundle \
VISION_NAV_MAVLINK_ENDPOINT=/dev/ttyACM0 \
VISION_NAV_MAVLINK_MESSAGE=odometry \
VISION_NAV_POSITION_UDP_TARGET=255.255.255.255:17660 \
VISION_NAV_COUNT=30 \
./scripts/pi/run_terrain_nav_loop.sh
```

`VISION_NAV_POSITION_UDP_TARGET` sends the ground station a compact live
position packet after every status bridge tick or processed terrain frame. The
packet exposes the source state as `gps_primary`, `vision_correction`,
`dead_reckoning_between_fixes`, `gps_degraded`, or `no_position`.

## Support Bundle

Package a bench run:

```bash
cd ~/Drone
./scripts/pi/create_support_bundle.sh
```

Pull it to the desktop with the app or:

```bash
PI_USER=pi PI_HOST=raspberrypi.local ./scripts/mac/sync_from_pi.sh
```

## Holybro X500 V2 Next Steps

Use these docs for the kit arrival milestone:

- [Holybro X500 V2 Hardware Data Inputs](holybro-x500v2-hardware-data-inputs.md)
- [Holybro X500 V2 Prop-Off Hardware Test](holybro-x500v2-prop-off-hardware-test.md)
