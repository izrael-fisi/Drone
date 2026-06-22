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

When a runtime wrapper sends external vision to PX4, it now defaults to
`VISION_NAV_MAVLINK_MESSAGE=odometry`. Override it with
`VISION_NAV_MAVLINK_MESSAGE=vision_position_estimate` only for compatibility
debugging; bench-readiness and final autonomy-readiness require receiver proof
from the `ODOMETRY` path.

After exporting PX4 parameters from QGroundControl or the PX4 shell, check the
external-vision readiness settings without changing the flight controller:

```bash
VISION_NAV_PX4_PARAMS="$HOME/px4.params" \
VISION_NAV_GNSS_DENIED_CHECK=1 \
./scripts/pi/check_px4_params.sh
```

ArduPilot remains a later adapter path after PX4 bench validation. If you are
checking an ArduPilot/Mission Planner parameter export for ExternalNav
readiness, use:

```bash
VISION_NAV_ARDUPILOT_PARAMS="$HOME/ardupilot.params" \
VISION_NAV_GNSS_DENIED_CHECK=1 \
VISION_NAV_EXTRINSICS_MEASURED=1 \
./scripts/pi/check_ardupilot_params.sh
```

This only audits the exported file. It does not modify ArduPilot parameters.
See [ArduPilot ExternalNav Adapter Design](ardupilot-externalnav-adapter.md).

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
- writes frames, `terrain_matches.jsonl`, and `runtime_status.json` to
  `~/DroneTransfer/outgoing/terrain-match/`
- runs until you press `Ctrl+C`

For a bounded app-driven field capture, Module Setup `Field Log Capture` runs
the same wrapper with `VISION_NAV_COUNT=30`, uses the configured MAVLink
endpoint when present, and downloads `terrain_matches.jsonl` plus
`runtime_status.json` into the desktop transfer folders.

`runtime_status.json` is the quick operator snapshot. It names the active map
bundle, output path, latest frame, estimator health, last match status/reason,
external-position health, and accepted/rejected counts without opening the full
JSONL log. Support bundles copy this file beside the runtime log, and
`vision-nav-bench-readiness` treats it as evidence: missing runtime status
degrades the bench report, while missing active-map or last-match state fails
the runtime-status check.

To print and mark the latest runtime snapshot for the desktop app:

```bash
cd Drone
./scripts/pi/read_runtime_status.sh
```

The wrapper searches the normal outgoing runtime folders, prints
`__VISION_NAV_RUNTIME_STATUS__=...` and
`__VISION_NAV_RUNTIME_STATUS_JSON__=...` markers, and keeps the preview bounded
so Module Setup can fetch the current operator status without building a full
support bundle.

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

To compare low-compute methods on the same replay case:

```bash
VISION_NAV_FEATURE_BENCH_EXPECTED=good_map \
./scripts/pi/run_feature_method_benchmark.sh
```

Use `VISION_NAV_FEATURE_BENCH_EXPECTED=degraded` or
`VISION_NAV_FEATURE_BENCH_EXPECTED=wrong_map` for those field cases. The wrapper
defaults to the deployed mission bundle and latest terrain runtime log, writes
the summary under `~/DroneTransfer/outgoing/feature-method-bench/`, and prints
`__VISION_NAV_FEATURE_METHOD_REPORT__=...` for desktop download. Neural/
SuperPoint-LightGlue is reported as unavailable until those descriptors are
generated for the bundle.

Register each field run as a replay case from the Pi after a terrain runtime or
replay log exists:

```bash
./scripts/pi/create_field_evidence_template.sh

VISION_NAV_FIELD_CASE_NAME=field-good-texture \
VISION_NAV_FIELD_EXPECTED=good_map \
VISION_NAV_FIELD_CONDITION=good_texture \
VISION_NAV_FIELD_NOTES="clear texture, matching map, nominal lighting" \
VISION_NAV_FIELD_CAPTURE_METADATA='{"operator":"Izrael","flight_altitude_agl_m":35,"lighting":"nominal"}' \
./scripts/pi/register_field_replay_case.sh
```

The template wrapper writes
`~/DroneTransfer/outgoing/replay-cases/field_manifest.template.json`, includes
one placeholder field case for each required autonomy-readiness condition, and
prints `__VISION_NAV_FIELD_TEMPLATE__=...` so Module Setup can download the
starter manifest. It also prints `__VISION_NAV_FIELD_MANIFEST__=...` and seeds
`~/DroneTransfer/outgoing/replay-cases/field_manifest.json` if the active
manifest does not already exist. Use it before the first field run, then
register captured logs with `register_field_replay_case.sh`; matching template
placeholders are replaced by condition tag as real logs are registered.
Starter cases include a `capture_metadata` scaffold and `capture_checklist`
object so the operator can record who captured the log, date/time, altitude,
lighting, weather, map-season notes, camera focus/exposure notes, IMU/PX4 state,
and safety notes before the case becomes field evidence.
For the field-evidence gate and threshold-tuning report, capture metadata is
proof-grade: field cases fail if required metadata fields are missing, still
set to `TODO`, or omit numeric altitude/speed context.

Generate a field-collection checklist from the active manifest before going
outside:

```bash
./scripts/pi/create_field_collection_plan.sh
```

This writes
`~/DroneTransfer/outgoing/replay-cases/field_collection_plan.json` plus a
Markdown checklist at
`~/DroneTransfer/outgoing/replay-cases/field_collection_plan.md`. The plan marks
each required condition as placeholder, missing, registered-missing-log, or
registered, and includes the exact `VISION_NAV_FIELD_*` registration command to
run after each captured terrain log. The generated plan also prints a metadata
JSON block and checklist for each condition; when copied into
`VISION_NAV_FIELD_CAPTURE_METADATA`, that JSON is stored with the replay case
for later support and readiness review. It also emits
`__VISION_NAV_FIELD_COLLECTION_PLAN__=...` and
`__VISION_NAV_FIELD_COLLECTION_PLAN_MD__=...` markers.
From the desktop app, Module Setup exposes the same step as `Create Plan`,
downloads both files, and lists downloaded plans for restart-safe field review.
The Pi support-bundle wrapper auto-includes
`field_collection_plan.json` and the sibling Markdown checklist when they exist
at the default replay-cases path, so support can review intended field coverage
beside the captured evidence.

Use `VISION_NAV_FIELD_CONDITIONS="low_texture blur"` for runs that cover
multiple tags. The wrapper updates
`~/DroneTransfer/outgoing/replay-cases/field_manifest.json`, copies the log by
default, writes
`~/DroneTransfer/outgoing/replay-cases/field_evidence_report.json`, and leaves
the report where `./scripts/pi/create_support_bundle.sh` automatically includes
it. The wrapper also prints `__VISION_NAV_FIELD_EVIDENCE_REPORT__=...` so
Module Setup can download the current coverage report after each registration
and show which required field conditions are still missing. Set
`VISION_NAV_FIELD_REPLACE=1` to retest a case, or
`VISION_NAV_FIELD_GATE_STRICT=1` once the full eight-condition field dataset is
expected to pass.

After registering a full field replay manifest, run the combined evidence gate:

```bash
vision-nav-field-evidence-gate \
  --manifest "$HOME/DroneTransfer/outgoing/replay-cases/field_manifest.json" \
  --output "$HOME/DroneTransfer/outgoing/replay-cases/field_evidence_report.json"
```

This is stricter than a coverage audit: it requires real field log files and
also runs replay gates for every case in the manifest. It also requires filled
capture metadata for every field case so support can audit where, when, and how
the evidence was recorded.

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

To review the same terrain log as ROS-style topics without requiring ROS 2 on
the Pi, export a dependency-free bag-like JSONL directory:

```bash
./scripts/pi/run_rosbag_export_validation.sh
```

That wrapper uses
`~/DroneTransfer/outgoing/terrain-match/terrain_matches.jsonl`, writes
`~/DroneTransfer/outgoing/terrain-match/rosbag-jsonl/`, validates the result,
and emits `__VISION_NAV_ROSBAG_EXPORT_VALIDATION__=...` for desktop download,
support bundles, and final readiness audits. For custom paths, set
`VISION_NAV_ROSBAG_SOURCE_LOG`, `VISION_NAV_ROSBAG_EXPORT_DIR`, or
`VISION_NAV_ROSBAG_EXPORT_VALIDATION`.

The equivalent low-level commands are:

```bash
vision-nav-ros2-replay-log \
  --log ~/DroneTransfer/outgoing/terrain-match/terrain_matches.jsonl \
  --export-rosbag-jsonl ~/DroneTransfer/outgoing/terrain-match/rosbag-jsonl \
  --include-frame-topic

vision-nav-validate-rosbag-export \
  --artifact ~/DroneTransfer/outgoing/terrain-match/rosbag-jsonl \
  --output ~/DroneTransfer/outgoing/terrain-match/rosbag-jsonl-validation.json
```

On a workstation that has the optional MCAP package installed, the same command
can write a JSON-encoded MCAP archive:

```bash
python -m pip install ".[rosbag]"
vision-nav-ros2-replay-log \
  --log ~/DroneTransfer/outgoing/terrain-match/terrain_matches.jsonl \
  --export-mcap ~/DroneTransfer/outgoing/terrain-match/vision-nav.mcap \
  --include-frame-topic

vision-nav-validate-rosbag-export \
  --artifact ~/DroneTransfer/outgoing/terrain-match/vision-nav.mcap \
  --output ~/DroneTransfer/outgoing/terrain-match/vision-nav-mcap-validation.json
```

The validator checks metadata, topic counts, JSONL payload shape, MCAP sidecars,
and native rosbag2 storage files without requiring ROS 2 to be installed. It
also fails closed unless the replay contains non-empty `/vision_nav/odometry`
and `/diagnostics` topics. The default support-bundle wrapper auto-includes
`~/DroneTransfer/outgoing/terrain-match/rosbag-jsonl-validation.json` when it
exists; set `VISION_NAV_ROSBAG_EXPORT_VALIDATION` or
`VISION_NAV_MCAP_EXPORT_VALIDATION` to package another validation report.

On a sourced ROS 2 workstation, export native rosbag2 and save the CLI review
artifact with the dev wrapper:

```bash
source /opt/ros/humble/setup.bash
./scripts/dev/run_rosbag2_cli_review.sh
```

Set `VISION_NAV_ROSBAG_SOURCE_LOG`, `VISION_NAV_ROSBAG2_EXPORT_DIR`, or
`VISION_NAV_ROSBAG2_CLI_REVIEW` when the synced field log or desired output
paths are different. The wrapper writes the native rosbag2 directory, runs the
strict validator, captures `ros2 bag info`, and emits
`__VISION_NAV_ROSBAG2_CLI_REVIEW__=...`.
When Module Setup runs `ROS Bag Validation`, it also downloads the source
`terrain_matches.jsonl` to `~/DroneTransfer/from-pi/terrain-match/`; the
desktop `Native rosbag2 Review` action uses that downloaded log and writes the
CLI review artifact beside it.

The equivalent low-level review command for an already exported native rosbag2
directory is:

```bash
vision-nav-review-rosbag2-cli \
  --artifact ~/DroneTransfer/outgoing/terrain-match/rosbag2-native \
  --output ~/DroneTransfer/outgoing/terrain-match/rosbag2-cli-review.json \
  --require-ros2
```

This command records both the strict validation result and `ros2 bag info`
output. The wrapper fails closed by default when the `ros2` CLI is not sourced;
set `VISION_NAV_ROSBAG2_REQUIRE_ROS2=0` only for non-gating diagnostics.
The support-bundle wrapper auto-includes
`~/DroneTransfer/outgoing/terrain-match/rosbag2-cli-review.json` when it
exists, or set `VISION_NAV_ROSBAG2_CLI_REVIEW` to package a custom review path.
The Pi and desktop autonomy-readiness wrappers also pass this report into the
final audit when it exists and emit `__VISION_NAV_ROSBAG2_CLI_REVIEW__=...` so
Module Setup can download the workstation review artifact with the rest of the
readiness package.

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

When the default
`~/DroneTransfer/outgoing/replay-cases/field_collection_plan.json` exists,
`create_support_bundle.sh` also copies it and the sibling Markdown checklist
under `extras/field_collection_plans/`, then publishes parsed JSON under
`summaries/field_collection_plans/`.

To include PX4 SITL receiver evidence, save the PX4 console outputs from
`listener vehicle_visual_odometry 5` and `mavlink status`, then pass those files
to the wrapper:

```bash
VISION_NAV_PX4_LISTENER_CAPTURE="$HOME/px4-evidence/vehicle_visual_odometry.txt" \
VISION_NAV_PX4_MAVLINK_STATUS_CAPTURE="$HOME/px4-evidence/mavlink_status.txt" \
VISION_NAV_SITL_MAVLINK_MESSAGE=odometry \
./scripts/pi/create_support_bundle.sh
```

The raw captures are copied under `extras/px4_sitl_evidence/`, and the parsed
pass/fail report is written under `summaries/px4_sitl_evidence/`.

If the captures were collected through a PX4 SITL evidence-session folder,
include the whole session instead:

```bash
VISION_NAV_PX4_SITL_SESSION="$HOME/px4-sitl-evidence" \
./scripts/pi/create_support_bundle.sh
```

The desktop SITL smoke and capture scripts print
`__VISION_NAV_PX4_SITL_SESSION__=...` and
`__VISION_NAV_PX4_SITL_REPORT__=...` markers after preparing or evaluating a
session. Module Setup `PX4 SITL Receiver Capture` runs that desktop capture
wrapper and saves the session under `~/DroneTransfer/from-pi/px4-sitl-evidence/`
so local readiness re-audits can find it. Use the session marker as
`VISION_NAV_PX4_SITL_SESSION`. The session folder is copied under
`extras/px4_sitl_session/`, and the parsed pass/fail report is still written
under `summaries/px4_sitl_evidence/`. Session-based receiver reports also
compare the observed `vehicle_visual_odometry` listener rate against the smoke
manifest `rate_hz`, so support bundles show whether PX4 received the
external-vision stream at a plausible rate. The same timing detail is carried
into bench-readiness and final autonomy-readiness check details.
The final autonomy-readiness wrapper can also consume the report marker
directly through `VISION_NAV_PX4_SITL_REPORT`, which is useful when receiver
proof has already been evaluated and does not need to be repackaged into a new
support bundle. When that direct report is present, the wrapper prints
`__VISION_NAV_PX4_SITL_REPORT__=...` so the desktop app can download and list
the standalone receiver proof beside the final readiness report. Receiver
reports must show `expected_message: odometry` to satisfy bench or final
readiness; compatibility-path reports are treated as debug evidence only.

To include the PX4 parameter readiness report in the same support bundle:

```bash
VISION_NAV_PX4_PARAMS="$HOME/px4.params" \
./scripts/pi/create_support_bundle.sh
```

The raw parameter export is copied under `extras/px4_params/`, and the parsed
report is written under `summaries/px4_params/`.

To include the optional ArduPilot ExternalNav parameter readiness report in the
same support bundle:

```bash
VISION_NAV_ARDUPILOT_PARAMS="$HOME/ardupilot.params" \
./scripts/pi/create_support_bundle.sh
```

The raw parameter export is copied under `extras/ardupilot_params/`, and the
parsed report is written under `summaries/ardupilot_params/`.

If feature-method benchmark reports exist under
`~/DroneTransfer/outgoing/feature-method-bench`, the Pi wrapper includes them
automatically. Override the location with:

```bash
VISION_NAV_FEATURE_METHOD_BENCHMARK="$HOME/DroneTransfer/outgoing/feature-method-bench" \
./scripts/pi/create_support_bundle.sh
```

The benchmark directory is copied under `extras/feature_method_benchmarks/`, and
parsed report JSON files are written under `summaries/feature_method_benchmarks/`.
The final autonomy-readiness wrapper can also consume the newest benchmark JSON
directly, so you do not need to rebuild a support bundle just to re-run the
goal-level audit after downloading a benchmark report.

If a field evidence report exists at
`~/DroneTransfer/outgoing/replay-cases/field_evidence_report.json`, the Pi
wrapper includes it automatically. Override the location with:

```bash
VISION_NAV_FIELD_EVIDENCE_REPORT="$HOME/DroneTransfer/outgoing/replay-cases/field_evidence_report.json" \
./scripts/pi/create_support_bundle.sh
```

The raw report is copied under `extras/field_evidence/`, parsed reports are
written under `summaries/field_evidence/`, and the status is counted in bench
readiness when present.

If a threshold-tuning report exists at
`~/DroneTransfer/outgoing/replay-cases/threshold_tuning_report.json`, the Pi
wrapper includes it automatically. Override the location with:

```bash
VISION_NAV_THRESHOLD_TUNING_REPORT="$HOME/DroneTransfer/outgoing/replay-cases/threshold_tuning_report.json" \
./scripts/pi/create_support_bundle.sh
```

The raw report is copied under `extras/threshold_tuning/`, parsed reports are
written under `summaries/threshold_tuning/`, and the final
`vision-nav-autonomy-readiness` audit can use it from the support bundle.

Every support bundle now includes a combined bench-readiness report under
`summaries/bench_readiness.json`. Re-run the same gate against an existing ZIP
with:

```bash
vision-nav-bench-readiness \
  --support-bundle "$HOME/DroneTransfer/outgoing/support-bundles/<bundle>.zip"
```

The gate checks terrain bundle health, Mission Planner GNSS-denied prep from
the bundled mission JSON, runtime logs, replay gates, PX4 receiver evidence, and
PX4 parameter readiness in one report. If an ArduPilot ExternalNav parameter
report is bundled, it is counted in the same readiness report; add
`--require-ardupilot-params` only for ArduPilot-specific adapter bench runs. Use
`--allow-missing-px4-evidence`, `--allow-missing-px4-params`, or
`--allow-missing-replay-gates` only for local software smoke checks before the
real bench evidence exists.

For the full autonomy/ground-control implementation goal, use the stricter
audit after the downloaded support bundle, field-evidence report, feature-method
benchmark evidence, threshold-tuning report, and ROS bag export validation
report exist. Generate the threshold report from the real field manifest with:

```bash
./scripts/pi/run_threshold_tuning_report.sh
```

The wrapper writes `threshold_tuning_report.json` and per-case tuning reports
under `~/DroneTransfer/outgoing/replay-cases/`. Set
`VISION_NAV_THRESHOLD_ALLOW_FAILED=1` only when you want to download a failing
intermediate report without treating the command as a passing validation step.
Downloaded threshold reports are listed in Module Setup beside the PX4, field,
feature, and final readiness evidence artifacts.
If `~/DroneTransfer/outgoing/terrain-match/rosbag-jsonl-validation.json` exists,
the final Pi-side readiness wrapper includes it automatically. Override the path
with `VISION_NAV_ROSBAG_EXPORT_VALIDATION=/path/to/validation.json` when
reviewing a different JSONL, MCAP, or native rosbag2 validation report. Module
Setup downloads emitted validation reports into
`~/DroneTransfer/from-pi/terrain-match/`, lists them under ROS Bag Validation,
offers a standalone `ROS Bag Validation` action for the wrapper, and shows the
ROS bag gate in the Autonomy Readiness Reports card.

If you want one Pi-side command that attempts the ordered evidence workflow and
preserves a step-by-step report even when prerequisites are still missing, run:

```bash
./scripts/pi/run_autonomy_evidence_workflow.sh
```

The workflow creates or reuses the field evidence template, optionally
registers a field case when `VISION_NAV_FIELD_CASE_NAME`,
`VISION_NAV_FIELD_EXPECTED`, and `VISION_NAV_FIELD_CONDITION(S)` are set, then
attempts feature benchmarking, threshold tuning, dependency-free ROS bag JSONL
export validation, support-bundle creation, and the final autonomy-readiness
audit. It writes
`~/DroneTransfer/outgoing/replay-cases/autonomy-evidence-workflow/autonomy_evidence_workflow.json`
with per-step status, log paths, tail output, a compressed workflow-log archive,
and any emitted `__VISION_NAV_*__` markers. The archive preserves the full step
outputs under `logs/*.log`. The wrapper also writes
`autonomy_evidence_workflow.validation.json` and emits
`__VISION_NAV_EVIDENCE_WORKFLOW_VALIDATION__=...` after checking that the
workflow report and log archive are internally consistent. By default it exits
successfully after writing the report even when evidence is still incomplete; set
`VISION_NAV_EVIDENCE_WORKFLOW_ALLOW_FAILED=0` when you want a CI-style nonzero
exit on missing proof.
From the desktop app, Module Setup exposes the same sequence as `Evidence
Workflow`. It uses the current Field Evidence Case form values for the optional
registration step, downloads the workflow JSON to the replay-cases transfer
folder, downloads the workflow-log archive and validation JSON, and downloads
any support bundle, field-evidence report, feature-method benchmark,
threshold-tuning report, readiness report, handoff, evidence package,
field-collection plan/checklist, or PX4 receiver marker emitted by the wrapper.
Downloaded field collection plans expose a `Load` action per pending condition,
which pre-fills the Field Evidence Case form before registration so the plan
condition, expected behavior, and capture metadata stay in sync.
Support-bundle details also preserve field collection capture root, per-condition
terrain log paths, runtime-status paths, and pending capture/register command
counts for offline review.
The downloaded workflow JSON remains visible after app restart in Module Setup's
Evidence Workflow Reports list, including per-step status and emitted artifact
markers. Each artifact marker chip copies the emitted Pi-side path for support
notes or manual transfer checks, including the workflow validation report, and
the `all` chip copies the full emitted artifact path bundle. After the desktop
app downloads matching artifacts into the standard transfer folders, those
chips prefer the local desktop path. When the validation JSON exists beside the
workflow report, the Evidence Workflow Reports list also shows validation
status, workflow status, issue count, and the first validation issue.
To validate a copied workflow report and its full log archive offline, run:

```bash
vision-nav-validate-evidence-workflow \
  --report ~/DroneTransfer/from-pi/replay-cases/autonomy_evidence_workflow.json \
  --output ~/DroneTransfer/from-pi/replay-cases/autonomy_evidence_workflow.validation.json
```

The validator exits nonzero only when the workflow report/archive pair is
structurally failed. A `degraded` validation can still be a usable support
artifact when final readiness is waiting on real field logs or PX4 receiver
proof.

Then run:

```bash
./scripts/pi/run_autonomy_readiness_audit.sh
```

If the evidence workflow report, validation JSON, and workflow-log archive are
present in the default workflow folder, the readiness audit records them as
artifact inputs. They do not change the final pass/fail gates, but the generated
handoff shows their availability and the evidence ZIP includes them for support
review when each file is under the package artifact size limit. After download,
the Module Setup Autonomy Readiness Reports card shows workflow, validation, and
logs chips for those referenced inputs so support can copy or reveal the local
artifacts directly.

The wrapper uses the latest Pi-side support bundle under
`~/DroneTransfer/outgoing/support-bundles/` by default and writes
`~/DroneTransfer/outgoing/replay-cases/autonomy_readiness_report.json` plus a
human-readable
`~/DroneTransfer/outgoing/replay-cases/autonomy_readiness_report.md` handoff
and `~/DroneTransfer/outgoing/replay-cases/autonomy_readiness_report.evidence.zip`
support-review package.
If `field_collection_plan.json` and `field_collection_plan.md` are present next
to the replay-case artifacts, the audit records them as inputs and the evidence
ZIP includes both files.
Override the support bundle with
`VISION_NAV_AUTONOMY_SUPPORT_BUNDLE=/path/to/bundle.zip` when reviewing an older
artifact. The audit intentionally fails until the external PX4 receiver proof
and real field-log evidence are present. Use it as the final proof artifact, not
as a synthetic preflight substitute.

After artifacts have been downloaded to the desktop, run the local wrapper to
scan the conventional `~/DroneTransfer/from-pi/` folders and write the same
strict audit report locally:

```bash
./scripts/dev/run_local_autonomy_readiness_audit.sh
```

The desktop app exposes the same offline check as Module Setup >
`Local Readiness Re-Audit`. That action runs from the desktop repo path, scans
the downloaded `~/DroneTransfer/from-pi/` evidence folders, and refreshes the
final readiness, workflow, field, feature, threshold, ROS bag, PX4, and support
bundle lists without opening a new SSH session.

It uses the latest downloaded support bundle, downloaded field-evidence and
feature-method benchmark reports, downloaded threshold-tuning reports,
downloaded ROS bag export validation reports, and a downloaded field collection
plan/checklist, plus a local PX4 SITL evidence session or receiver report when
present. The local wrapper looks for the default validation report at
`~/DroneTransfer/from-pi/terrain-match/rosbag-jsonl-validation.json`.
It prints `__VISION_NAV_AUTONOMY_REPORT__=...` and
`__VISION_NAV_AUTONOMY_HANDOFF__=...` plus
`__VISION_NAV_AUTONOMY_EVIDENCE_PACKAGE__=...`, then writes
`~/DroneTransfer/from-pi/replay-cases/autonomy_readiness_report.json` and
`~/DroneTransfer/from-pi/replay-cases/autonomy_readiness_report.md`, plus
`~/DroneTransfer/from-pi/replay-cases/autonomy_readiness_report.evidence.zip`,
even when the audit fails, so the missing proof artifacts are visible in both a
machine-readable report, a support handoff, and a package manifest with the
plan source snapshot, compact goal-proof counts, and a bounded proof-runbook
summary.
Failed or degraded gates include `next_actions` entries with the matching
Module Setup action or shell command to run next. Field-evidence and
threshold-tuning next actions include the missing real-world condition keys.
The Module Setup readiness card can copy all next-action shell commands at once
or copy a single command from its row, and the JSON report includes the same
machine-readable command bundle for support tooling.
The Markdown handoff turns those missing condition keys and failed/degraded
bench subchecks into checkbox lists for field collection and support review. It
also lists all goal proof items and separates completion blockers from external
proof blockers. The handoff includes a plan source snapshot so support can see
which research and implementation-plan markers were present during the audit.
It also includes a proof runbook that marks each source-plan, bench, field
dataset, method/threshold, ROS replay, and final-audit phase as passed,
action-required, or blocked by upstream proof.
When a field collection plan is present, the handoff also summarizes registered
vs required field conditions and the pending placeholder/missing cases. It
also includes an artifact-availability table when the referenced evidence
paths are visible from the machine rendering the handoff. The handoff also
includes a copy-friendly command bundle for next-action commands and pending
field replay capture/registration commands.

The desktop Module Setup `Bench Report` action runs the terrain bundle validator
against the configured deployed bundle, creates this same support bundle, and
downloads the latest zip back to `~/DroneTransfer/from-pi/support-bundles/` on
the desktop. The `Autonomy Readiness` action runs the same strict final audit on
the Pi against the latest support bundle and downloads the readiness report to
`~/DroneTransfer/from-pi/replay-cases/` on the desktop, including the sibling
Markdown handoff and evidence ZIP package when those markers are emitted. It
also downloads referenced evidence-workflow reports, workflow-log archives,
workflow-validation JSON, field-evidence reports, feature benchmarks,
threshold-tuning reports, and field-collection plans when the readiness wrapper
emits those markers. It also
lets you save a local JSON setup report containing the check results,
selected discovery adapter, copyable discovery checklist, downloaded
support-bundle summaries, downloaded feature-benchmark summaries, downloaded
field-evidence coverage summaries, downloaded autonomy-readiness report
summaries, downloaded autonomy-workflow reports, and a compact latest-readiness
snapshot with handoff path, evidence package path, goal-completion flag,
plan source snapshot, external blockers, next actions, the readiness
`command_bundle`, and the referenced field collection plan summary when it is
available locally. The same Module
Setup panel lists the latest downloaded feature-method benchmark JSON reports
with recommended method and accepted rates, lists field-evidence JSON reports
with per-condition coverage, then lists autonomy-readiness JSON reports with
pass, degraded, and fail counts plus the support-bundle, PX4 receiver,
field-evidence, feature-benchmark, and threshold-tuning gate statuses. When the
referenced field collection plan is available locally, the same report card
shows plan status, registered-vs-required counts, and pending
placeholder/missing condition keys. If a Pi-generated readiness report still
references the Pi-side absolute plan path, the desktop falls back to a
downloaded sibling `field_collection_plan.json` beside the report. The local
Markdown handoff renderer and evidence ZIP packager use the same fallback, so
support packages can still include the downloaded JSON/Markdown checklist. The
pending field-collection condition pills and command buttons in Module Setup
copy individual or batched generated replay-case capture and registration
commands when the plan includes them. The
autonomy-readiness list detects the sibling Markdown handoff and evidence ZIP
package beside each JSON report and exposes copy/reveal controls for support
review. When the evidence ZIP contains the expected package manifest, the list
also shows included, missing, and skipped artifact counts with the first
included/missing/skipped artifact labels plus packaged proof pass counts and
external-blocker counts. When the downloaded JSON or evidence package includes a
plan snapshot, the same card shows research marker/reference coverage and
implementation track/task/done counts.
The desktop support-bundle list can reveal
downloaded ZIPs in the local file manager, copy their path, show compact
manifest details, inspect log/replay-gate summaries and per-record JSONL
previews from inside the ZIP, inspect PX4 receiver evidence reports, preview
bounded camera/debug/replay image artifacts, or delete stale ZIPs.
