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
~/drone-data/captures/
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
```

If `rpicam-*` is unavailable, the capture helper tries compatible
`libcamera-*` tools where available.

## Training Data Capture

Record training data only after calibrating the mounted downward camera. A
capture session binds raw frames to nearby PX4 MAVLink telemetry, map-release
and calibration identifiers, and SHA-256 checksums. GPS samples, if present in
PX4 telemetry, are explicitly restricted to offline training/evaluation labels;
they are not a runtime dependency.

The first **supervised** field dataset requires a healthy offline GNSS reference
for every captured frame: `GPS_RAW_INT` fix type at least 3, horizontal
accuracy at most 10 m, and a reference timestamp within 500 ms of the camera
frame. This does not weaken the GNSS-denied product goal. GNSS exists only to
produce and evaluate initial training labels. A capture made while GPS is
unhealthy can still be useful for camera diagnostics, but it must not enter a
supervised DatasetRelease.

It also requires short MAVLink `TIMESYNC` audits before and after capture: at
least three low-latency request/response samples out of eight in each snapshot,
each with a round trip at or below 50 ms. This records the PX4-to-Pi
clock-offset evidence and any session drift needed for later camera/PX4 timing
calibration. It does not alter PX4, arm the vehicle, or claim that the camera
sensor clock is already synchronized.

Before calibration or a training session, run the strict multi-frame camera
bench while aimed at a printed map, textured ground, or chessboard. This is
especially important for the manual-focus/manual-aperture lens:

```bash
cd ~/Drone
./scripts/pi/capture_camera_bench.sh
```

It writes a timestamped `camera_bench_report.json` under
`~/DroneTransfer/outgoing/camera-bench/`. Correct focus, aperture, exposure,
or lens-cap problems before proceeding if any frame fails.

For the first prop-off capture, keep the vehicle disarmed and confirm that the
camera and PX4 attitude stream are healthy:

```bash
cd ~/Drone
export VISION_NAV_CALIBRATION_ID=down-camera-v1
export VISION_NAV_CALIBRATION_FILE=$HOME/Drone/config/camera/down_camera.yaml
export VISION_NAV_MAP_RELEASE_ID=your-map-release-id
export VISION_NAV_MAVLINK_ENDPOINT=serial:/dev/ttyACM0:921600
export VISION_NAV_CAMERA_BACKEND=picamera2
export VISION_NAV_CAPTURE_FRAMES=30
export VISION_NAV_MAX_ATTITUDE_FRAME_DELTA_MS=50
export VISION_NAV_REQUIRE_OFFLINE_GNSS_REFERENCE=1
./scripts/pi/record_training_capture.sh
```

With `VISION_NAV_MAVLINK_ENDPOINT` set, this recorder continuously reads the
single selected Pixhawk MAVLink link during camera capture. The session keeps
ATTITUDE cadence, all received telemetry, and each frame's nearest
host-monotonic ATTITUDE sample. It is read-only: it never sends a PX4 command
or a vision pose. A serial-reader failure recorded by that collector makes the
session ineligible for a training DatasetRelease, even when a few early
ATTITUDE samples were captured.

Validate the resulting session before copying it to the training computer:

```bash
./scripts/pi/validate_training_capture.sh ~/drone-data/captures/capture-YYYYMMDDTHHMMSSZ
```

On the CUDA training computer, turn one or more valid sessions into an
immutable dataset release:

```bash
cd /path/to/Drone
VISION_NAV_DATASET_RELEASE_ID=holybro-capture-set \
VISION_NAV_DATASET_RELEASE_VERSION=0.1.0 \
./scripts/research/create_dataset_release.sh /path/to/capture-session
```

The release tool requires PX4 `ATTITUDE` evidence by default. Camera-only
bench sessions remain useful for focus and exposure checks, but are not
eligible for temporal vision-model training.

The Pi wrapper rejects the repository's placeholder calibration. This is
intentional: only a calibration generated for the mounted lens at the capture
resolution may enter a training dataset. `VISION_NAV_ALLOW_PROVISIONAL_CALIBRATION=1`
is a bench-only override and must not be used for model-training captures.

`VISION_NAV_CAMERA_BACKEND=auto` prefers Picamera2 when available and falls
back to `rpicam-still`. Each accepted session records sensor timestamp,
exposure, analogue gain, crop, blur, luminance, and saturation metrics for
every frame. These are later enforced by the training-readiness gate.

For field-training data, Picamera2 is the wrapper default. Keep
`VISION_NAV_CAMERA_BACKEND=picamera2` explicit in saved run notes; using `auto`
or `rpicam_still` is a bench-only choice unless the resulting session proves
strict SensorTimestamp coverage.

Before recording the first model-training session, bind the passed prop-off
evidence to the model-independent ReferenceMapRelease and confirm Pi storage
headroom. A ReferenceMapRelease pins the COG/STAC imagery and WGS84 origin used
for capture and offline labels; it does not need learned model assets yet.

For the first session, use the single preparation command instead of running
these gates separately. It captures camera quality and read-only MAVLink
evidence, enforces the prop-off decision with measured camera records, and
checks the selected reference map plus storage. It never records a training
session, changes PX4 parameters, arms the vehicle, or sends a vision pose:

```bash
cd ~/Drone
VISION_NAV_PROP_OFF_CONFIRMED=1 \
VISION_NAV_REFERENCE_MAP=$HOME/drone-data/reference-maps/first-site/reference_map.json \
VISION_NAV_CALIBRATION_ID=down-camera-v1 \
VISION_NAV_MAVLINK_ENDPOINT=serial:/dev/ttyACM0:921600 \
VISION_NAV_TRAINING_SESSION_ID=first-field-capture \
./scripts/pi/prepare_first_training_capture.sh
```

Only use the printed `record_training_capture.sh` command after all four stages
pass. The MAVLink bench includes a bounded `TIMESYNC` evidence exchange but
never sends a flight command, parameter change, or external-vision pose. Keep
the vehicle prop-off and disarmed for the first capture.

After validating a capture, include its compact provenance in the bench support
bundle without copying its raw frames or telemetry:

```bash
VISION_NAV_TRAINING_CAPTURE_DIR=$HOME/drone-data/captures/first-field-capture \
./scripts/pi/create_support_bundle.sh
```

The support bundle re-runs strict offline-GNSS-label, attitude,
sensor-timestamp, and checksum validation and stores the resulting summary
alongside the capture manifest. The raw session remains reserved for the
Windows research transfer workflow.

```bash
VISION_NAV_PROP_OFF_PREFLIGHT_REPORT=$HOME/DroneTransfer/outgoing/prop-off-preflight/<timestamp>/prop_off_preflight_report.json \
VISION_NAV_REFERENCE_MAP=$HOME/drone-data/reference-maps/first-site/reference_map.json \
VISION_NAV_CALIBRATION_ID=down-camera-v1 \
VISION_NAV_TRAINING_SESSION_ID=first-field-capture \
./scripts/pi/preflight_training_capture.sh
```

This check is read-only and does not open MAVLink or capture frames. It requires
a passing prop-off report at `ready_for_training_capture`, validates the map
reference and calibration binding, refuses a non-empty capture directory, and
checks the configured free-space reserve. Only then run
`./scripts/pi/record_training_capture.sh` with the same session ID.
The normal wrapper requires `SensorTimestamp` on every frame. `rpicam-still`,
imported images, or a missing timestamp can still be used for a bench diagnosis
with `VISION_NAV_REQUIRE_SENSOR_TIMESTAMPS=0`, but that session must not be
used to build a field-training DatasetRelease.

Each frame is paired to its nearest PX4 `ATTITUDE` message using the Pi's
monotonic capture midpoint. PX4-native time and the camera sensor timestamp
are preserved separately for audit and replay, but the recorder does not claim
these clocks are synchronized. The recorded `TIMESYNC` audit supplies a
measured PX4/Pi offset and dispersion for later calibration; a camera/PX4 clock
model still needs field validation. The default 50 ms maximum is strict on purpose:
do not relax it to hide low MAVLink cadence or camera stalls. Fix the telemetry
stream or capture load first.

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
  --calibration-id down-camera-v1 \
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

The training-capture wrapper requires both the measured intrinsic calibration
and a measured `camera_to_body.yaml` whose intrinsic calibration ID matches
`VISION_NAV_CALIBRATION_ID`. See [Camera Calibration](camera-calibration.md)
for the mount-transform creation command.

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
export VISION_NAV_MAVLINK_ENDPOINT=serial:/dev/ttyACM0:921600
./scripts/pi/check_mavlink_endpoint.sh
```

Then capture a bounded, read-only telemetry evidence session before calibration
or training capture. It verifies heartbeat presence, attitude cadence, and long
message gaps without sending an arm, mode, parameter, or external-position
command to PX4:

```bash
cd ~/Drone
VISION_NAV_MAVLINK_ENDPOINT=serial:/dev/ttyACM0:921600 \
./scripts/pi/capture_mavlink_bench.sh
```

Review `$HOME/DroneTransfer/outgoing/mavlink-bench/<timestamp>/mavlink_bench_report.json`.
The default gate is 20 Hz PX4 attitude with no gap over 150 ms. If it fails,
fix the companion link, PX4 stream configuration, or Pi serial permissions
before collecting camera/PX4 training sessions.

Once both the camera and MAVLink bench reports pass, attest that the physical
propellers are removed and produce the combined decision report:

```bash
VISION_NAV_PROP_OFF_CONFIRMED=1 ./scripts/pi/run_prop_off_preflight.sh
```

It reports whether the setup is ready for calibration or, when measured camera
intrinsics and extrinsics are already installed, ready for synchronized
training capture.

Adjust the endpoint to match the real Pixhawk link. Do not use this project to
arm or command motors.

Export PX4 parameters from QGroundControl to `~/px4.params`, then:

```bash
VISION_NAV_PX4_PARAMS=$HOME/px4.params ./scripts/pi/check_px4_params.sh
```

## Learned Shadow Runtime

The active Pi runtime consumes a versioned `MapRelease`, `ModelRelease`, and
measured camera calibration. It is always shadow-only: it writes decision
traces and may broadcast status to the ground station, but it never sends PX4
external-vision or odometry messages.

Run one saved-frame smoke check first:

```bash
cd ~/Drone
VISION_NAV_MAP_RELEASE=$HOME/drone-data/map_releases/active/map_release.json \
VISION_NAV_MODEL_RELEASE=$HOME/drone-data/model_releases/pi5_crossview_nav_v1/model_release.json \
VISION_NAV_MODEL_ARTIFACT_ROOT=$HOME/drone-data/model_releases/pi5_crossview_nav_v1 \
VISION_NAV_CALIBRATION_ID=down-camera-v1 \
VISION_NAV_CALIBRATION_FILE=$HOME/drone-data/calibration/down_camera.yaml \
VISION_NAV_SHADOW_FRAME=$HOME/drone-data/test-frame.jpg \
./scripts/pi/run_vision_nav_shadow.sh --once
```

Then sample the mounted camera, still prop-off:

```bash
VISION_NAV_CAMERA_BACKEND=picamera2 \
VISION_NAV_STATUS_UDP_TARGET=255.255.255.255:17660 \
./scripts/pi/run_vision_nav_shadow.sh --once
```

Add `VISION_NAV_MAVLINK_ENDPOINT=serial:/dev/ttyACM0:921600` only for a
release whose temporal contract needs read-only PX4 `ATTITUDE` context. The
runtime records the selected model/map, candidate tiles, inference timing,
camera health, and its `NO_GLOBAL_FIX` decision in
`$HOME/DroneTransfer/outgoing/neural-shadow/`. Read the newest status with:

```bash
VISION_NAV_RUNTIME_STATUS_ROOTS=$HOME/DroneTransfer/outgoing/neural-shadow \
./scripts/pi/read_runtime_status.sh
```

For a long-running shadow service, copy and edit
`config/pi/vision-nav.env.example`, then install the user service:

```bash
./scripts/pi/install_vision_nav_service.sh
systemctl --user start drone-vision-nav.service
journalctl --user -u drone-vision-nav.service -f
```

Run `sudo loginctl enable-linger $USER` once if the service must survive Pi
reboots while the user is not logged in. The service runs
`validate_neural_runtime.sh` before every start and honors
`VISION_NAV_MODEL_ARTIFACT_ROOT` when artifacts live outside the model release
manifest directory.

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
