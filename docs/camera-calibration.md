# Camera Calibration

The Raspberry Pi Global Shutter Camera must be calibrated with the actual lens,
focus, resolution, and mount used on the drone. The default calibration file is
only a placeholder.

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
  --camera-name down_global_shutter \
  --cols 9 \
  --rows 6 \
  --square-size-m 0.024 \
  --show-rejections
```

Inspect the reported reprojection error. Lower is better; if it is high, capture
more images with better board coverage, focus, and lighting.

## Transfer Calibration Back To Mac

From the Mac:

```bash
PI_USER=pi PI_HOST=raspberrypi.local ./scripts/mac/sync_from_pi.sh
```

or copy directly:

```bash
scp pi@raspberrypi.local:~/Drone/config/camera/down_camera.yaml config/camera/down_camera.yaml
```

## Practical Notes

- Calibrate at the same resolution used for navigation.
- Lock focus before calibrating.
- Avoid motion blur and glare.
- Recalibrate after changing lens, focus, camera mount, or resolution.
- Measure `camera_to_body.yaml` after the physical downward mount is final.

