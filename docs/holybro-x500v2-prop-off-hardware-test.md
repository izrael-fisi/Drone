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

Prepare or select a test map bundle from the desktop Mission Planner and upload
it to:

```text
~/drone-data/map_bundles/mission_bundle
```

Validate on the Pi:

```bash
cd ~/Drone
VISION_NAV_BUNDLE=$HOME/drone-data/map_bundles/mission_bundle ./scripts/pi/validate_terrain_bundle.sh
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
./scripts/pi/smoke_test_vision.sh
```

Expected outputs:

```text
~/DroneTransfer/outgoing/pi-info/
~/DroneTransfer/outgoing/camera-health/camera_health_report.json
~/DroneTransfer/outgoing/vision-smoke/
```

If the lens is manual focus/manual aperture:

- Point the camera at high-texture ground or a printed map.
- Adjust focus until the camera-health image looks sharp.
- Avoid very small aperture settings that force blur from low light.
- Re-run camera health after adjustment.

## Step 4: Camera Calibration

If calibration has not been done with the installed camera/lens/mount:

```bash
cd ~/Drone
./scripts/pi/capture_calibration_set.sh
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
export VISION_NAV_MAVLINK_ENDPOINT=/dev/ttyACM0
```

Then run:

```bash
cd ~/Drone
./scripts/pi/check_mavlink_endpoint.sh
```

Pass criteria:

- heartbeat is detected, or the script clearly reports the endpoint problem
- no arming or motor command is sent
- endpoint path and baud assumptions are recorded

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

## Step 7: Terrain Runtime Logging-Only Check

Keep propellers removed.

Run without MAVLink output first:

```bash
cd ~/Drone
VISION_NAV_BUNDLE=$HOME/drone-data/map_bundles/mission_bundle \
VISION_NAV_COUNT=30 \
./scripts/pi/run_terrain_nav_loop.sh
```

Then:

```bash
VISION_NAV_RUNTIME_STATUS_ROOTS=$HOME/DroneTransfer/outgoing/terrain-match \
./scripts/pi/read_runtime_status.sh
```

Pass criteria:

- `terrain_matches.jsonl` exists
- `runtime_status.json` exists
- status is accepted, degraded, rejected, or failed with clear reason
- no MAVLink send is attempted unless explicitly configured

## Step 8: MAVLink Output Dry Bench

Only after Step 7 works, run a short MAVLink-enabled test:

```bash
cd ~/Drone
VISION_NAV_BUNDLE=$HOME/drone-data/map_bundles/mission_bundle \
VISION_NAV_MAVLINK_ENDPOINT=/dev/ttyACM0 \
VISION_NAV_MAVLINK_MESSAGE=odometry \
VISION_NAV_COUNT=30 \
./scripts/pi/run_terrain_nav_loop.sh
```

Pass criteria:

- logs include external-position health
- accepted measurements are sent only when the estimator result is valid
- rejected/failed measurements are not sent as trusted external vision
- QGroundControl/PX4 shows no unexpected arming or mode behavior

## Step 9: Support Bundle

Create the bench evidence bundle:

```bash
cd ~/Drone
./scripts/pi/create_support_bundle.sh
```

Pull it to the desktop and review it in the app.

The bundle should contain:

- Pi info
- camera health
- terrain bundle health
- runtime log
- runtime status
- MAVLink check output if available
- PX4 parameter check output

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
