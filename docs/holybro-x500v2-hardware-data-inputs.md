# Holybro X500 V2 Hardware Data Inputs

This document lists the data to collect before and during the first hardware
bench tests with the Holybro X500 V2 kit and Pixhawk 6C-class controller.

Source references checked on 2026-06-23:

- PX4 Holybro X500 V2 + Pixhawk 6C guide:
  https://docs.px4.io/main/en/frames_multicopter/holybro_x500v2_pixhawk6c
- Holybro PX4 Development Kit - X500 v2:
  https://docs.holybro.com/drone-development-kit/px4-development-kit-x500v2
- Holybro Pixhawk 6C ports:
  https://docs.holybro.com/autopilot/pixhawk-6c/pixhawk-6c-ports

Holybro documents the X500 v2 development kit as including a Pixhawk 6C flight
controller, PM02 V3-12S power module, M8N GPS, SiK telemetry radio, X500 V2
frame kit, preinstalled motors/ESCs, and a companion-computer mount. The PX4
guide covers the X500 V2 + Pixhawk 6C build with props removed during setup.

## Hardware Identity Data

Record this in the bench log:

```text
airframe: Holybro X500 V2
flight_controller_model:
flight_controller_serial:
firmware: PX4
firmware_version:
gps_model: Holybro M8N
telemetry_radio_model: Holybro SiK Telemetry Radio V3
telemetry_radio_frequency:
rc_receiver_model:
raspberry_pi_model: Raspberry Pi 5 16GB
camera_model:
camera_lens:
camera_mount_location:
battery_model:
battery_cell_count:
battery_capacity_mah:
props_removed: yes
```

## Pixhawk Port Mapping To Confirm

Use the labels printed on the Pixhawk case and the Holybro port documentation.
Do not guess pin orientation from cable color alone.

```text
Power module:
  Pixhawk port:
  power module model:
  battery connector:
  voltage seen in QGroundControl:

GPS / compass:
  Pixhawk port: GPS1
  GPS model:
  safety switch/buzzer present:
  compass detected:
  GPS lock indoors/outdoors:

Telemetry radio:
  Pixhawk port: TELEM1 or TELEM2
  radio frequency:
  ground radio connected to:
  QGroundControl link detected:

RC receiver:
  receiver type: SBUS / CRSF / ELRS / DSM / other
  Pixhawk port:
  QGroundControl radio calibration complete:

Raspberry Pi companion link:
  Pixhawk port:
  physical link: USB / TELEM UART / Ethernet adapter
  MAVLink endpoint used by this repo:
  Pi can read heartbeat:
```

Important Pixhawk 6C port notes from Holybro:

- `I/O PWM OUT` is `MAIN OUT`.
- `FMU PWM OUT` is `AUX OUT`.
- `TELEM1` and `TELEM2` are six-pin telemetry UART ports.
- `GPS1` includes UART, I2C, safety switch, buzzer, and ground pins.
- Some Pixhawk 6C `TELEM3` and `I2C` revisions have serial-number-specific pin
  behavior. Use the Holybro port page for your exact board before connecting
  non-I2C peripherals to those pins.

## Camera Data Inputs

Record:

```text
camera_model:
sensor_type: rolling_shutter / global_shutter / unknown
lens_focus: fixed / manual / autofocus
manual_focus_locked: yes/no
manual_aperture:
resolution:
frame_rate:
mount_orientation:
mount_offset_x_m:
mount_offset_y_m:
mount_offset_z_m:
down_camera_yaml:
camera_to_body_yaml:
```

Required files before using vision output:

```text
config/camera/down_camera.yaml
config/camera/camera_to_body.yaml
```

Capture and calibrate:

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

Adjust the chessboard values to match the real board.

Then, after the mounted camera and Pixhawk reference point are fixed, create a
measured camera-to-body transform. Use the measured translation and unit
quaternion from the mount procedure; do not use the placeholder transform:

```bash
vision-nav-create-camera-extrinsics \
  --output config/camera/camera_to_body.yaml \
  --extrinsic-id x500-down-camera-mount-v1 \
  --camera-calibration-id down-camera-v1 \
  --translation-m MEASURED_X MEASURED_Y MEASURED_Z \
  --rotation-quat-xyzw MEASURED_QX MEASURED_QY MEASURED_QZ MEASURED_QW \
  --measurement-method measured_mount_jig_v1 \
  --translation-sigma-m 0.003 \
  --rotation-sigma-deg 1.0
```

## GPS Data Inputs

GPS is not the product navigation source, but it is useful for setup and
ground-truth comparison while developing.

Record:

```text
gps_model:
pixhawk_port:
gps_fix_type:
satellite_count:
hdop:
home_position_set:
gps_used_as_ground_truth_only: yes
```

Do not call a vision estimate "GNSS-denied ready" just because GPS is present.
GNSS-denied testing must keep the vision estimate and its health separate from
ordinary satellite navigation.

## Radio Data Inputs

Record both the control radio and telemetry radio.

```text
control_radio_type:
receiver_model:
receiver_pixhawk_port:
qgc_radio_calibration_complete:
flight_mode_switch_verified:
kill/disarm switch_verified:

telemetry_radio_model:
air_radio_port:
ground_radio_connected_to:
qgc_mavlink_connected:
parameter_download_complete:
log_download_test_complete:
```

## Pixhawk Parameter Artifacts

Export a PX4 parameter file after the real controller is configured:

```text
~/px4.params
```

Then run:

```bash
cd ~/Drone
VISION_NAV_PX4_PARAMS=$HOME/px4.params ./scripts/pi/check_px4_params.sh
```

The output belongs in the support bundle.

## Learned Shadow Data Inputs

Before hardware runtime tests, prepare:

```text
~/drone-data/model_releases/pi5_crossview_nav_v1/model_release.json
~/drone-data/model_releases/pi5_crossview_nav_v1/models/*.onnx
~/drone-data/map_releases/active/map_release.json
~/drone-data/map_releases/active/index/megaloc.npz  # default portable index
# or ~/drone-data/map_releases/active/index/megaloc.faiss after Pi FAISS validation
~/drone-data/map_releases/active/index/megaloc_tiles.json
~/drone-data/map_releases/active/index/tiles.sqlite
~/drone-data/map_releases/active/index/xfeat_tiles.json
~/drone-data/map_releases/active/index/xfeat_tiles.sqlite
~/drone-data/map_releases/active/features/xfeat/*.npz
~/drone-data/calibration/down_camera.yaml
~/drone-data/calibration/camera_to_body.yaml
```

Validate:

```bash
cd ~/Drone
VISION_NAV_MAP_RELEASE=$HOME/drone-data/map_releases/active/map_release.json \
./scripts/pi/validate_learned_map_assets.sh

VISION_NAV_MODEL_RELEASE=$HOME/drone-data/model_releases/pi5_crossview_nav_v1/model_release.json \
VISION_NAV_MODEL_ARTIFACT_ROOT=$HOME/drone-data/model_releases/pi5_crossview_nav_v1 \
./scripts/pi/validate_neural_model.sh

VISION_NAV_MAP_RELEASE=$HOME/drone-data/map_releases/active/map_release.json \
VISION_NAV_MODEL_RELEASE=$HOME/drone-data/model_releases/pi5_crossview_nav_v1/model_release.json \
VISION_NAV_CALIBRATION_ID=down-camera-v1 \
./scripts/pi/validate_neural_runtime.sh
```

For a training capture, keep props removed and use the direct Pi camera rather
than imported image files. The default recorder requires a measured
calibration/extrinsic pair, PX4 ATTITUDE evidence, and a per-frame camera
sensor timestamp:

```bash
cd ~/Drone
VISION_NAV_PROP_OFF_PREFLIGHT_REPORT=$HOME/DroneTransfer/outgoing/prop-off-preflight/<timestamp>/prop_off_preflight_report.json \
VISION_NAV_REFERENCE_MAP=$HOME/drone-data/reference-maps/first-site/reference_map.json \
VISION_NAV_CALIBRATION_ID=down-camera-v1 \
VISION_NAV_TRAINING_SESSION_ID=first-field-capture \
./scripts/pi/preflight_training_capture.sh

# Run only after the preflight passes, using the same session ID.
VISION_NAV_MAVLINK_ENDPOINT=serial:/dev/ttyACM0:921600 \
VISION_NAV_CALIBRATION_ID=down-camera-v1 \
VISION_NAV_REFERENCE_MAP_ID=field-area@0.1.0 \
VISION_NAV_MAP_REFERENCE_KIND=reference_map_release \
VISION_NAV_CAMERA_BACKEND=picamera2 \
./scripts/pi/record_training_capture.sh
```

The saved session records the camera capture midpoint used to pair PX4
attitude, the unmodified sensor timestamp, and a bounded MAVLink `TIMESYNC`
audit. The audit measures PX4-to-Pi timing offset and transport dispersion; it
does not command PX4 or establish a camera/PX4 clock model by itself. A capture
with missing sensor timestamps or failed time-sync evidence is a bench artifact,
not training data.

## Support Bundle Data Inputs

For the first prop-off bench test, the support bundle should include:

- Pi info
- camera health report
- learned MapRelease and ModelRelease manifests
- PX4 parameter report
- MAVLink endpoint check
- MAVLink bench report with heartbeat, attitude cadence, and raw telemetry
- runtime `decision_trace.jsonl`
- runtime `runtime_status.json`
- field log capture report if the app/Pi flow creates one

Create:

```bash
cd ~/Drone
./scripts/pi/create_support_bundle.sh
```
