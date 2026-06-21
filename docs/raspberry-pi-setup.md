# Raspberry Pi 5 Setup

Target hardware:

- Raspberry Pi 5 16GB
- Raspberry Pi Global Shutter Camera
- Active cooling
- 256GB microSD for the current prototype
- Optional USB 3 SSD later for larger maps, frame logs, and long test runs

Recommended OS for the first prototype:

- Raspberry Pi OS 64-bit Bookworm

Ubuntu Server can work later, but Raspberry Pi OS has the smoother first path
for `libcamera`, `rpicam-apps`, and `picamera2`.

## Bootstrap

On the Pi:

```bash
git clone https://github.com/izrael-fisi/Drone.git
cd Drone
chmod +x scripts/pi/*.sh
./scripts/pi/bootstrap_pi5.sh
sudo reboot
```

Run the bootstrap script as your normal Pi user, not with `sudo`. The script
uses `sudo` internally only for the package/service steps that require it.

After the Pi is on the same Wi-Fi network as the desktop app, the app's Devices
page can scan common Raspberry Pi mDNS hostnames and local SSH neighbors. The
scan stores recent discoveries locally so the module can be selected again even
if it later resolves through a different hostname or IP. The scan also shows
the desktop's active private/link-local IPv4 interfaces and subnet hints to help
diagnose wrong-Wi-Fi, guest-network, or mDNS failures. Choose the adapter that
should reach the Pi, then use the checklist button to copy the exact
mDNS/SSH/firewall checks for terminal or support-bundle notes.

The bootstrap script installs:

- camera tools when available
- Python/OpenCV build/runtime packages
- Docker Engine from the official Docker apt repository
- SSH server
- transfer folders under `~/DroneTransfer`
- a Python virtual environment under `~/drone_vision_nav_venv`

The virtual environment is created with system site packages enabled so it can
reuse Raspberry Pi OS camera/OpenCV packages installed by apt.

## Storage Layout

The current setup uses the Raspberry Pi's onboard microSD storage by default.
Bootstrap creates:

```text
~/DroneTransfer/
~/drone-data/
```

Use these microSD-backed paths for the first bench tests. A 256GB card is enough
for setup, calibration images, small mission bundles, and short runtime logs.

If you add a USB SSD later, mount it wherever you prefer and override the data
paths in `config/pi/vision-nav.env`, for example:

```bash
VISION_NAV_BUNDLE=/mnt/drone-ssd/map_bundles/mission_bundle
VISION_NAV_OUTPUT_DIR=/mnt/drone-ssd/runtime-match
VISION_NAV_REPLAY_OUTPUT_DIR=/mnt/drone-ssd/replay-match
```

Keep the repo itself in `~/Drone`; only bulky map bundles, captures, and logs
need to move to external storage.

Dependency files are split by runtime:

- `requirements/pi-host.txt` is used by `bootstrap_pi5.sh` on Raspberry Pi OS
  and avoids reinstalling heavy apt-provided camera/OpenCV packages with pip.
- `requirements/pi.txt` is used by the Docker image, where pip-installed
  OpenCV is expected.

## Docker Runtime

After reboot:

```bash
cd Drone
./scripts/pi/build_docker.sh
./scripts/pi/run_docker.sh
```

The Docker runtime is meant for map-feature processing, matching experiments,
logging, and replay. Direct Raspberry Pi CSI camera access can be more reliable
on the host with `picamera2`; use Docker first for processing and replay, then
move camera capture inside Docker only after the host pipeline is stable.

Runtime config templates:

- `config/camera/down_camera.yaml`
- `config/camera/camera_to_body.yaml`
- `config/pi/vision-nav.env.example`
- `map_bundles/example/manifest.json`

Copy `config/pi/vision-nav.env.example` to `config/pi/vision-nav.env` on the Pi
if you need local overrides. The override file is intentionally ignored by Git.

## Pi Smoke Test

Run:

```bash
cd Drone
./scripts/pi/first_run_checks.sh
```

This runs:

- setup verification
- Pi diagnostic report collection
- Raspberry Pi Global Shutter camera health check
- host camera/synthetic vision smoke test
- Docker build and Docker synthetic vision smoke test

To skip Docker on a quick pass:

```bash
VISION_NAV_SKIP_DOCKER_SMOKE=1 ./scripts/pi/first_run_checks.sh
```

To intentionally skip live camera validation, for example when testing this repo
on a non-Pi machine:

```bash
VISION_NAV_SKIP_CAMERA_HEALTH=1 ./scripts/pi/first_run_checks.sh
```

The camera health check captures one frame and writes:

```text
~/DroneTransfer/outgoing/camera-health/
  global_shutter_health_capture.jpg
  camera_health_report.json
  list_cameras.txt
```

The report includes resolution, exposure, blur/texture metrics, feature count,
and warnings for low sharpness, low entropy, low feature density, underexposure,
or overexposure. Set `VISION_NAV_CAMERA_FAIL_ON_WARNING=1` if you want those
warnings to fail the check instead of just being reported.

The host smoke test:

1. Captures a Raspberry Pi Global Shutter frame when camera tools are available.
2. Generates a synthetic orthophoto/query image pair.
3. Builds a georeferenced feature index.
4. Runs the frame-to-map matcher.
5. Writes outputs to `~/DroneTransfer/outgoing/vision-smoke/`.

The Docker smoke test runs the synthetic map pipeline inside the container and
writes outputs to `Drone/data/docker-smoke/`.

`collect_pi_info.sh` writes a diagnostic report to:

```text
~/DroneTransfer/outgoing/pi-info/
```

Send or sync that report when you want help debugging Pi setup, camera detection,
Docker, storage, or SSH.

## Camera Calibration

Capture a chessboard dataset:

```bash
cd Drone
./scripts/pi/capture_calibration_set.sh
```

For a short setup-wizard style capture:

```bash
CALIBRATION_COUNT=8 CALIBRATION_DELAY_S=1 ./scripts/pi/capture_calibration_set.sh
```

Then calibrate:

```bash
source ~/drone_vision_nav_venv/bin/activate
vision-nav-calibrate-camera \
  --images "$HOME/DroneTransfer/outgoing/calibration/down_camera/*.jpg" \
  --output config/camera/down_camera.yaml \
  --camera-name down_global_shutter \
  --cols 9 \
  --rows 6 \
  --square-size-m 0.024 \
  --show-rejections
```

Adjust `--cols`, `--rows`, and `--square-size-m` to your actual board. See
[Camera Calibration](camera-calibration.md).

## Optional systemd Service

After the bundle validates and the manual runtime loop works, install the user
service:

```bash
cd Drone
./scripts/pi/install_vision_nav_service.sh
systemctl --user start drone-vision-nav.service
systemctl --user status drone-vision-nav.service
```

The service runs the host-side camera/matching loop:

```text
validate_vision_nav_bundle.sh
run_vision_nav_loop.sh
```

It reads optional overrides from:

```text
config/pi/vision-nav.env
```

Follow logs:

```bash
journalctl --user -u drone-vision-nav.service -f
```

To let the service continue after logout/reboot:

```bash
sudo loginctl enable-linger "$USER"
```

## Camera Smoke Test

On Raspberry Pi OS Bookworm:

```bash
rpicam-hello --list-cameras
rpicam-still -o ~/DroneTransfer/outgoing/global_shutter_test.jpg
```

If `rpicam-*` is not available, try:

```bash
libcamera-hello --list-cameras
libcamera-still -o ~/DroneTransfer/outgoing/global_shutter_test.jpg
```

## Time And MAVLink Checks

Before sending external-vision measurements to PX4, verify the Pi clock and
MAVLink endpoint:

```bash
cd Drone
./scripts/pi/check_time_sync.sh
VISION_NAV_MAVLINK_ENDPOINT=serial:/dev/ttyAMA0:921600 ./scripts/pi/check_mavlink_endpoint.sh
```

`check_time_sync.sh` verifies clock plausibility and NTP synchronization.
`check_mavlink_endpoint.sh` validates endpoint syntax and serial-device access
without requiring live traffic. To probe for live MAVLink telemetry:

```bash
VISION_NAV_MAVLINK_PROBE=1 \
VISION_NAV_MAVLINK_ENDPOINT=serial:/dev/ttyAMA0:921600 \
./scripts/pi/check_mavlink_endpoint.sh
```

For PX4 uXRCE-DDS and ROS 2 bench paths, also check the optional Micro XRCE-DDS
Agent:

```bash
./scripts/pi/check_micro_xrce_dds_agent.sh
```

The check passes with a warning when the agent is not installed so direct
MAVLink deployments are not blocked. To require the ROS 2 bridge dependency in
a setup run:

```bash
VISION_NAV_REQUIRE_XRCE=1 ./scripts/pi/check_micro_xrce_dds_agent.sh
```

The default UDP launch hint is `MicroXRCEAgent udp4 -p 8888`. For serial XRCE
links, set `VISION_NAV_XRCE_TRANSPORT=serial` and
`VISION_NAV_XRCE_SERIAL_DEVICE=/dev/<device>`.

## First Feature-Matching Test

Prepare a map image and a query image, then run:

```bash
source ~/drone_vision_nav_venv/bin/activate
vision-nav-build-bundle --bundle mission_bundle
vision-nav-build-terrain-bundle --bundle mission_bundle
vision-nav-match-bundle-frame --bundle mission_bundle --frame query.jpg --viz match_debug.jpg
vision-nav-match-terrain-frame --bundle mission_bundle --frame query.jpg
```

The output JSON reports match count, inlier count, inlier ratio, reprojection
error, homography, and a confidence score.

With simple georeferencing:

```bash
cp -R map_bundles/example mission_bundle
# Put your map at mission_bundle/ortho/map.png and edit the manifest georef.
vision-nav-build-bundle --bundle mission_bundle
```

If the match is accepted, `vision-nav-match-frame` will also report an estimated
map pixel and approximate latitude/longitude.

## Bench Runtime Loop

After you have a bundle with an orthophoto and manifest, copy it to the Pi at:

```text
~/drone-data/map_bundles/mission_bundle
```

Validate the bundle before using the camera:

```bash
cd Drone
./scripts/pi/validate_vision_nav_bundle.sh
./scripts/pi/validate_terrain_bundle.sh
```

The validator checks:

- manifest schema and paths
- orthophoto file existence
- georef completeness and ranges
- feature method and feature index shape when present
- terrain bundle geospatial health, including CRS/GSD, STAC assets, tile index,
  and lightweight COG/GeoTIFF readiness when applicable
- camera calibration and camera-to-body files
- optional `checksums.sha256` integrity when required

By default, calibration files are required and the feature index may be missing
because the runtime loop can build it. To require a prebuilt feature index:

```bash
VISION_NAV_REQUIRE_FEATURES=1 ./scripts/pi/validate_vision_nav_bundle.sh
```

To require transfer/integrity checksums:

```bash
VISION_NAV_REQUIRE_CHECKSUMS=1 ./scripts/pi/validate_vision_nav_bundle.sh
```

Generate bundle checksums after feature-map build and before transfer:

```bash
vision-nav-bundle-checksums --bundle mission_bundle --write
```

Or build features and write checksums in one step:

```bash
vision-nav-build-bundle --bundle mission_bundle --write-checksums
vision-nav-build-terrain-bundle --bundle mission_bundle --write-checksums
vision-nav-map-health --bundle mission_bundle
```

Then run continuous capture and map matching:

```bash
cd Drone
./scripts/pi/run_terrain_nav_loop.sh
```

Defaults:

- captures from the Raspberry Pi Global Shutter Camera
- undistorts frames with `config/camera/down_camera.yaml`
- matches once per second
- writes frames and `terrain_matches.jsonl` to
  `~/DroneTransfer/outgoing/terrain-match/`
- runs until you press `Ctrl+C`

Useful overrides:

```bash
VISION_NAV_BUNDLE="$HOME/drone-data/map_bundles/test_bundle" \
VISION_NAV_OUTPUT_DIR="$HOME/DroneTransfer/outgoing/runtime-match-test" \
VISION_NAV_COUNT=30 \
VISION_NAV_INTERVAL_S=0.5 \
VISION_NAV_MAX_ROTATION_DEG=60 \
VISION_NAV_MAX_SCALE_ANISOTROPY=2.0 \
VISION_NAV_CAMERA_CALIBRATION="$PWD/config/camera/down_camera.yaml" \
./scripts/pi/run_terrain_nav_loop.sh
```

Use `./scripts/pi/run_vision_nav_loop.sh` only when you specifically want the
legacy single-image matcher.

Each accepted or rejected match logs homography geometry metrics:

- `scale_mean`
- `scale_anisotropy`
- `rotation_deg`
- `perspective_norm`

The defaults are `VISION_NAV_MIN_SCALE=0.2`, `VISION_NAV_MAX_SCALE=5.0`,
`VISION_NAV_MAX_ROTATION_DEG=90.0`, `VISION_NAV_MAX_SCALE_ANISOTROPY=3.0`,
and `VISION_NAV_MAX_PERSPECTIVE_NORM=0.01`. Tighten these after you collect
real camera/map logs.

Pull the results back to the Mac with:

```bash
PI_USER=pi PI_HOST=raspberrypi.local ./scripts/mac/sync_from_pi.sh
```

This loop only logs measurement candidates. It does not publish corrections to
PX4 or alter the vehicle state.

## Replay Captured Frames

Replay is useful when you want to tune matching thresholds or compare bundle
versions without touching the camera:

```bash
cd Drone
./scripts/pi/replay_terrain_nav_log.sh
```

Defaults:

- reads frame records from `~/DroneTransfer/outgoing/terrain-match/terrain_matches.jsonl`
- undistorts frames with `config/camera/down_camera.yaml`
- writes `terrain_replay_matches.jsonl` to
  `~/DroneTransfer/outgoing/terrain-replay/`

Useful overrides:

```bash
VISION_NAV_BUNDLE="$HOME/drone-data/map_bundles/test_bundle" \
VISION_NAV_TERRAIN_REPLAY_LOG="$HOME/DroneTransfer/outgoing/terrain-match/terrain_matches.jsonl" \
VISION_NAV_REPLAY_OUTPUT_DIR="$HOME/DroneTransfer/outgoing/terrain-replay-test" \
VISION_NAV_MAX_ROTATION_DEG=60 \
VISION_NAV_CAMERA_CALIBRATION="$PWD/config/camera/down_camera.yaml" \
./scripts/pi/replay_terrain_nav_log.sh
```

Set `VISION_NAV_CAMERA_CALIBRATION=` to disable undistortion for synthetic or
wrong-resolution test frames.

## Summarize Match Logs

After a runtime or replay run:

```bash
cd Drone
./scripts/pi/summarize_vision_nav_logs.sh
```

The summary reports:

- accepted/rejected/failed counts
- accepted rate
- confidence, inlier, and reprojection-error ranges
- frame sharpness, entropy, and covariance sigma ranges
- capture and match timing
- estimated lat/lon spread when matches include georeferenced positions

## Create A Support Bundle

After every bench run or failed field check, package the active map metadata,
bundle health report, runtime logs, generated summaries, git/app version, Pi OS
metadata, and MAVLink endpoint into a zip file:

```bash
cd Drone
./scripts/pi/create_support_bundle.sh
```

The zip is written under:

```text
~/DroneTransfer/outgoing/support-bundles/
```

When created from the desktop app, the latest zip is copied back to:

```text
~/DroneTransfer/from-pi/support-bundles/
```

By default this keeps the package small by excluding full orthophoto/tile
assets. Include map assets for heavier offline reproduction with:

```bash
VISION_NAV_SUPPORT_INCLUDE_MAP_ASSETS=1 ./scripts/pi/create_support_bundle.sh
```

To include replay-gate pass/fail reports, point the support-bundle wrapper at a
replay-case manifest:

```bash
VISION_NAV_REPLAY_CASE_MANIFEST="$HOME/Drone/replay_cases.json" \
./scripts/pi/create_support_bundle.sh
```

Replay-gate reports are written under `summaries/replay_gates/` inside the
support bundle.

The desktop Module Setup `Bench Report` action runs the terrain bundle validator
against the configured deployed bundle, creates this same support bundle, and
downloads the latest zip back to `~/DroneTransfer/from-pi/support-bundles/` on
the desktop. It also lets you save a local JSON setup report containing the
check results, selected discovery adapter, copyable discovery checklist, and
downloaded support-bundle summaries. The desktop support-bundle list can reveal
downloaded ZIPs in the local file manager, copy their path, show compact
manifest details, inspect log/replay-gate summaries and per-record JSONL
previews from inside the ZIP, or delete stale ZIPs.
