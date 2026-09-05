# Camera Calibration

The Raspberry Pi Global Shutter Camera must be calibrated with the actual lens,
focus, resolution, and mount used on the drone. The default calibration file is
only a placeholder.

Before capturing chessboard images, point the mounted camera at a high-texture
target and run the Pi camera bench. For a manual-focus lens, adjust and lock
focus/aperture first, then keep them unchanged through calibration and training
capture:

```bash
cd ~/Drone
./scripts/pi/capture_camera_bench.sh
```

The resulting `camera_bench_report.json` is a multi-frame quality record. It
does not select a feature matcher; it checks focus sharpness, image texture,
edge content, and exposure so a soft or covered lens cannot quietly enter the
calibration workflow.

## Capture A Chessboard Dataset

Use a printed chessboard with known square size. The `--cols` and `--rows`
values are the number of **interior** chessboard corners, not printed squares.

On the Pi:

```bash
cd Drone
./scripts/pi/capture_calibration_set.sh
```

Defaults:

- 20 images
- 1456x1088
- 2 seconds between captures
- output folder: `~/DroneTransfer/outgoing/calibration/down_camera`

Override if needed:

```bash
CALIBRATION_COUNT=35 \
CALIBRATION_DELAY_S=3 \
CALIBRATION_WIDTH=1456 \
CALIBRATION_HEIGHT=1088 \
CALIBRATION_BOARD_COLS=9 \
CALIBRATION_BOARD_ROWS=6 \
CALIBRATION_SQUARE_SIZE_M=0.024 \
./scripts/pi/capture_calibration_set.sh
```

Move and tilt the board between captures. Include the board in the center,
edges, corners, and at several angles.

## Build Calibration YAML

Example for a 9x6 interior-corner board with 24 mm squares:

```bash
source ~/drone_vision_nav_venv/bin/activate
vision-nav-calibrate-camera \
  --images "$HOME/DroneTransfer/outgoing/calibration/down_camera/*.jpg" \
  --output config/camera/down_camera.yaml \
  --calibration-id down-camera-v1 \
  --camera-name down_global_shutter \
  --cols 9 \
  --rows 6 \
  --square-size-m 0.024 \
  --show-rejections
```

Inspect the reported reprojection error. Lower is better; if it is high, capture
more images with better board coverage, focus, and lighting. The output records
the accepted image hashes, per-view error, board geometry, and calibration
capture-manifest hash. It is marked `calibration_status: measured` and its
`calibration_id` must be the same ID used by every future training capture.
The solver requires at least 12 accepted images and a reprojection error no
higher than `0.8 px` by default. Those are initial safety gates, not a claim of
field-localization accuracy; tighten them only after reviewing real captures.

## Transfer Calibration Back To Mac

From the Mac:

```bash
PI_USER=pi PI_HOST=raspberrypi.local ./scripts/mac/sync_from_pi.sh
```

or copy directly:

```bash
scp pi@raspberrypi.local:~/Drone/config/camera/down_camera.yaml config/camera/down_camera.yaml
```

## Camera-To-Body Extrinsics

After the camera mount is final, measure the camera optical-frame origin from
the Pixhawk body-frame origin and its orientation using a jig or another
documented fixture. This is separate from intrinsic lens calibration. Do not
replace the values below with guesses: the training and Pi capture gates reject
the repository placeholder by design.

```bash
vision-nav-create-camera-extrinsics \
  --output config/camera/releases/x500-down-camera-mount-v1.yaml \
  --extrinsic-id x500-down-camera-mount-v1 \
  --camera-calibration-id down-camera-v1 \
  --translation-m MEASURED_X MEASURED_Y MEASURED_Z \
  --rotation-quat-xyzw MEASURED_QX MEASURED_QY MEASURED_QZ MEASURED_QW \
  --measurement-method measured_mount_jig_v1 \
  --translation-sigma-m MEASURED_TRANSLATION_SIGMA_M \
  --rotation-sigma-deg MEASURED_ROTATION_SIGMA_DEG \
  --mount-id x500-downward-camera-mount
```

The result is `extrinsic_status: measured` and records the intrinsic
calibration ID, mount ID, method, and uncertainty. Recreate it whenever the
camera mount, camera orientation, or flight-controller reference point changes.
The generated release is intentionally strict: `body_frd_T_camera_optical`
means the translation is the camera optical origin expressed in PX4 FRD
(`forward`, `right`, `down`), while the `xyzw` quaternion rotates
camera-optical vectors (`right`, `down`, `forward`) into that body frame. Do
not substitute an inverse transform or an ENU quaternion; capture rejects a
measured file with a different convention.

The command refuses to overwrite an existing calibration release. Choose a new
release ID/path after a mount or calibration change. Identifiers and measurement
method must be nonempty; uncertainty must be finite and non-negative, and the
translation/quaternion must have the correct dimensions and finite numeric
values. A `measured` label is a recorded operator assertion, not an automatic
measurement or proof of accuracy. The placeholder remains unqualified.

Select the new file explicitly for the existing capture wrapper, from the repo
root on the Pi:

```bash
export VISION_NAV_CAMERA_TO_BODY_FILE="$PWD/config/camera/releases/x500-down-camera-mount-v1.yaml"
```

This selects a file only; it does not start capture, change PX4 parameters, or
authorize a flight. Keep the intrinsic calibration ID consistent with the lens,
focus, resolution and crop used for recording.

## Practical Notes

- Calibrate at the same resolution used for navigation.
- Lock focus before calibrating.
- Avoid motion blur and glare.
- Recalibrate after changing lens, focus, camera mount, or resolution.
- Measure `camera_to_body.yaml` after the physical downward mount is final.
- For learned training captures, use `picamera2` when available. The capture
  records `SensorTimestamp` as camera-clock provenance. The current training
  preparation path requires its exposure/native-attitude alignment audit;
  legacy host-receipt matching needs explicit research opt-in. Software time
  pairing alone does not qualify hardware sensor delay or rolling-shutter timing.
