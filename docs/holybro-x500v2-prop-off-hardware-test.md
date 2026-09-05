# Holybro X500 V2 Prop-Off Hardware Test

Use this when the Holybro X500 V2 kit arrives. This is a bench-only test plan.
Propellers stay removed for every step.

Source references checked on 2026-06-23:

- PX4 Holybro X500 V2 + Pixhawk 6C guide:
  https://docs.px4.io/main/en/frames_multicopter/holybro_x500v2_pixhawk6c
- Holybro PX4 Development Kit - X500 v2:
  https://docs.holybro.com/drone-development-kit/px4-development-kit-x500v2
- Holybro Pixhawk 6C ports:
  https://docs.holybro.com/autopilot/pixhawk-6c/pixhawk-6c-ports

## Absolute Safety Rules

- Remove all propellers before connecting a battery or running any command.
- Keep the drone restrained on a stable bench.
- Keep hands, loose cables, and tools away from motors.
- Do not use this repo to send raw motor commands.
- Do not enable autonomous flight modes.
- Do not trust vision navigation for control during this milestone.
- Stop immediately if motors spin unexpectedly.

## Test Goals

Confirm that the real hardware stack can provide the data this project needs:

- Pixhawk powers correctly.
- QGroundControl connects over USB or SiK telemetry.
- GPS, radio receiver, safety switch, and battery telemetry are visible.
- Raspberry Pi boots and can reach the repo.
- Camera capture works with the installed lens/mount.
- Raspberry Pi can read MAVLink heartbeat/telemetry from Pixhawk.
- Terrain runtime can run in logging-only mode.
- Support bundle captures enough evidence for the next bench iteration.

## Required Items

- Holybro X500 V2 kit
- Pixhawk 6C-class controller from the kit
- M8N GPS module from the kit
- SiK telemetry radio pair from the kit
- RC transmitter/receiver
- Battery suitable for the kit
- Raspberry Pi 5 with this repo installed
- Downward camera and cable
- Mac or desktop running the desktop app and QGroundControl
- USB-C / telemetry cables
- No propellers installed

## Day-0 Prep Before The Kit Arrives

On the development machine:

```bash
cd /Users/izzyfisi/Documents/DRONE
python tests/run_unit_tests.py
./scripts/dev/local_preflight.sh
npm run build --prefix desktop-app
cd desktop-app/src-tauri && cargo check && cargo test
```

On the Raspberry Pi:

```bash
cd ~/Drone
git pull
./scripts/pi/first_run_checks.sh
```

For the learned-shadow milestone, prepare an approved model release, a learned
MapRelease, and a measured camera calibration. Copy them to:

```text
~/drone-data/model_releases/pi5_crossview_nav_v1/model_release.json
~/drone-data/map_releases/active/map_release.json
~/drone-data/calibration/down_camera.yaml
```

Validate on the Pi:

```bash
cd ~/Drone
VISION_NAV_MAP_RELEASE=$HOME/drone-data/map_releases/active/map_release.json \
VISION_NAV_MODEL_RELEASE=$HOME/drone-data/model_releases/pi5_crossview_nav_v1/model_release.json \
VISION_NAV_CALIBRATION_ID=down-camera-v1 \
./scripts/pi/validate_neural_runtime.sh
```

## Step 1: Mechanical And Wiring Inspection

Record results in the bench log.

Checklist:

- Propellers removed.
- Motors firmly mounted.
- ESC leads seated.
- Power module connected correctly.
- Pixhawk mounted with orientation arrow documented.
- GPS/compass mounted away from power wiring where practical.
- Telemetry radio connected to an appropriate telemetry port.
- RC receiver connected and antenna clear.
- Raspberry Pi mounted but isolated from vibration/shorts.
- Camera mounted downward and lens cap removed.
- Camera manual focus/aperture set and locked if applicable.
- Cable strain relief applied.

Do not connect battery until this inspection passes.

## Step 2: Pixhawk Power And QGroundControl

Use USB first if possible.

Checklist:

- Connect Pixhawk to QGroundControl.
- Confirm firmware is PX4.
- Confirm airframe is not armed.
- Confirm sensors page is readable.
- Confirm battery telemetry appears when battery is connected.
- Confirm GPS module is detected.
- Confirm safety switch and buzzer behavior.
- Confirm radio calibration can see stick movement.
- Confirm flight mode switch movement.
- Export parameters to `px4.params`.

Save:

```text
~/px4.params
```

Then copy or place it where the Pi or desktop app can use it.

## Step 3: Raspberry Pi And Camera Check

On the Pi:

```bash
cd ~/Drone
./scripts/pi/collect_pi_info.sh
./scripts/pi/check_global_shutter_camera.sh
./scripts/pi/capture_camera_bench.sh
```

Expected outputs:

```text
~/DroneTransfer/outgoing/pi-info/
~/DroneTransfer/outgoing/camera-health/camera_health_report.json
~/DroneTransfer/outgoing/camera-bench/<timestamp>/camera_bench_report.json
```

If the lens is manual focus/manual aperture:

- Point the camera at high-texture ground or a printed map.
- Adjust focus until the camera-health image looks sharp.
- Avoid very small aperture settings that force blur from low light.
- Re-run the multi-frame camera bench after adjustment. It requires every frame
  to pass sharpness, texture/edge-content, and exposure checks by default.

## Step 4: Camera Calibration

If calibration has not been done with the installed camera/lens/mount:

```bash
cd ~/Drone
./scripts/pi/capture_calibration_set.sh
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

Adjust `--cols`, `--rows`, and `--square-size-m` to the actual calibration
board.

Also update:

```text
config/camera/camera_to_body.yaml
```

Use measured camera orientation and approximate offsets from the vehicle body
frame.

## Step 5: MAVLink Endpoint Check

Choose one physical link for the first prop-off test:

- Pixhawk USB to Raspberry Pi, or
- Pixhawk telemetry UART to Raspberry Pi, or
- telemetry radio to ground-control computer for QGroundControl only

Do not use multiple companion links at once until one link is proven.

On the Pi, set the endpoint in the environment or config. Example only:

```bash
export VISION_NAV_MAVLINK_ENDPOINT=serial:/dev/ttyACM0:921600
```

Then run:

```bash
cd ~/Drone
./scripts/pi/check_mavlink_endpoint.sh

# Capture 15 seconds of read-only telemetry evidence. This sends no PX4 command.
VISION_NAV_MAVLINK_ENDPOINT=serial:/dev/ttyACM0:921600 \
./scripts/pi/capture_mavlink_bench.sh
```

Pass criteria:

- heartbeat is observed
- PX4 `ATTITUDE` cadence is at least 20 Hz with no gap longer than 150 ms
- `mavlink_bench_report.json` records the endpoint, raw telemetry path, message
  counts, attitude cadence, gaps, and PX4-native timestamp domains
- no arming or motor command is sent
- endpoint path and baud assumptions are recorded

## Step 5.5: Prop-Off Evidence Decision

After the camera and MAVLink bench reports exist, make the explicit bench
decision. This is still read-only, but it requires an operator attestation that
the propellers are physically removed:

```bash
cd ~/Drone
VISION_NAV_PROP_OFF_CONFIRMED=1 \
./scripts/pi/run_prop_off_preflight.sh
```

The report identifies one of three states:

- `not_ready`: resolve camera, Pixhawk telemetry, or safety-attestation failures.
- `ready_for_calibration`: the physical data links are healthy; create measured
  intrinsics and camera-to-body extrinsics next.
- `ready_for_training_capture`: both measured camera records match and the
  system can record a synchronized training session.

## Step 6: PX4 Parameter Check

Copy the exported parameter file to the Pi as:

```text
~/px4.params
```

Run:

```bash
cd ~/Drone
VISION_NAV_PX4_PARAMS=$HOME/px4.params ./scripts/pi/check_px4_params.sh
```

Pass criteria:

- report is written
- GNSS-denied warnings/errors are understood
- no PX4 parameters are auto-modified by this repo

## Step 7: Learned Shadow Runtime Check

Keep propellers removed.

Run one saved, calibrated test frame first. The output remains diagnostic only:

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

Then:

```bash
VISION_NAV_RUNTIME_STATUS_ROOTS=$HOME/DroneTransfer/outgoing/neural-shadow \
./scripts/pi/read_runtime_status.sh
```

Pass criteria:

- `decision_trace.jsonl` exists
- `runtime_status.json` exists
- `shadow_mode` is true and `px4_publication.enabled` is false
- status is `TRACKING`, `RECOVERING`, or `NO_GLOBAL_FIX` with a clear reason
- no external-vision MAVLink send is attempted

## Step 8: MAVLink Telemetry Dry Bench

Only after Step 7 works, run live camera plus read-only attitude collection:

```bash
cd ~/Drone
VISION_NAV_CAMERA_BACKEND=auto \
VISION_NAV_CALIBRATION_FILE=$HOME/drone-data/calibration/down_camera.yaml \
VISION_NAV_MAVLINK_ENDPOINT=serial:/dev/ttyACM0:921600 \
./scripts/pi/run_vision_nav_shadow.sh --once
```

Pass criteria:

- logs include PX4 ATTITUDE telemetry health
- the learned trace records a disabled publication decision
- no learned estimate is sent to PX4
- QGroundControl/PX4 shows no unexpected arming or mode behavior

## Step 8.5: Prepare The First Training Capture

After measured camera intrinsics, camera-to-body transform, and the first
ReferenceMapRelease exist, run the combined preparation workflow. Keep the
props removed and vehicle disarmed:

```bash
cd ~/Drone
VISION_NAV_PROP_OFF_CONFIRMED=1 \
VISION_NAV_REFERENCE_MAP=$HOME/drone-data/reference-maps/first-site/reference_map.json \
VISION_NAV_CALIBRATION_ID=down-camera-v1 \
VISION_NAV_MAVLINK_ENDPOINT=serial:/dev/ttyACM0:921600 \
VISION_NAV_TRAINING_SESSION_ID=first-field-capture \
./scripts/pi/prepare_first_training_capture.sh
```

This is the required preparation path before the first synchronized
camera/PX4 training session. It only gathers evidence and validates readiness.

## Step 9: Support Bundle

Create the bench evidence bundle:

```bash
cd ~/Drone
VISION_NAV_TRAINING_CAPTURE_DIR=$HOME/drone-data/captures/first-field-capture \
./scripts/pi/create_support_bundle.sh
```

Pull it to the desktop and review it in the app.

The bundle should contain:

- Pi info
- camera health
- camera bench report and captured camera-bench frames
- learned map/model release health
- runtime log
- runtime status
- MAVLink endpoint check output if available
- `mavlink_bench_report.json` and `mavlink_telemetry.jsonl` from the prop-off
  telemetry bench capture
- `prop_off_preflight_report.json`
- PX4 parameter check output
- a strict training-capture summary when `VISION_NAV_TRAINING_CAPTURE_DIR` is
  provided. It includes the session manifest, checksums, camera/PX4 timing, and
  validation result, but intentionally excludes raw frames and telemetry. Keep
  those in the training transfer path.

## Stop Criteria

Stop the test if any of these happen:

- propellers are installed
- vehicle arms unexpectedly
- motors spin unexpectedly
- smoke, heat, electrical smell, or brownout occurs
- GPS/compass/power wiring is uncertain
- MAVLink endpoint is ambiguous
- camera mount is loose
- runtime logs produce plausible positions while reporting low confidence

## Result Labels

Use one label at the end:

- `passed`: all prop-off checks completed and support bundle created
- `degraded`: hardware is safe, but one or more data inputs need repair
- `failed`: hardware, power, telemetry, or safety state is not acceptable

Do not proceed to prop-on testing from this repo until the prop-off label is
`passed` and the support bundle has been reviewed.
