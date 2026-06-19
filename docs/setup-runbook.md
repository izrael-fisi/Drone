# Setup Runbook

This is the practical order for getting the MacBook Pro and Raspberry Pi ready.
For the short decision tree between GitHub clone and SSH sync, see
`docs/operator-handoff.md`.

## 1. MacBook Pro

From this repository on the Mac:

```bash
chmod +x scripts/mac/*.sh
./scripts/mac/setup_mac_ssh_and_transfer.sh
./scripts/mac/enable_remote_login.sh
```

`enable_remote_login.sh` requires your macOS admin password.

Verify the Mac transfer folders:

```bash
ls -la ~/DroneTransfer
```

Expected folders:

```text
from-pi
to-pi
```

## 2. Raspberry Pi 5

Copy or clone this repository onto the Pi, then run:

```bash
cd Drone
chmod +x scripts/pi/*.sh
./scripts/pi/bootstrap_pi5.sh
sudo reboot
```

Do not run `bootstrap_pi5.sh` with `sudo`. It asks for sudo internally when
needed.

The current storage target is your onboard 256GB microSD. The scripts default
to `~/DroneTransfer` and `~/drone-data`; add a USB SSD later only when map
bundles or logs outgrow the card.

After reboot:

```bash
cd Drone
./scripts/pi/first_run_checks.sh
```

If SSH is already working, run the same check from the Mac and pull the Pi
reports back into `transfer/pi_to_mac/`:

```bash
PI_USER=pi PI_HOST=raspberrypi.local ./scripts/mac/run_pi_first_checks.sh
```

For a remote check that skips Docker but still validates the live camera:

```bash
VISION_NAV_SKIP_DOCKER_SMOKE=1 PI_USER=pi PI_HOST=raspberrypi.local ./scripts/mac/run_pi_first_checks.sh
```

The Pi info report is written to:

```text
~/DroneTransfer/outgoing/pi-info/
```

The camera health report is written to:

```text
~/DroneTransfer/outgoing/camera-health/camera_health_report.json
```

For a faster first pass without Docker:

```bash
VISION_NAV_SKIP_DOCKER_SMOKE=1 ./scripts/pi/first_run_checks.sh
```

Use `VISION_NAV_SKIP_CAMERA_HEALTH=1` only when intentionally testing without
the Raspberry Pi camera attached.

If SSH to the Pi is already available, the Mac can sync this repo and start the
Pi bootstrap for you:

```bash
PI_USER=pi PI_HOST=raspberrypi.local ./scripts/mac/bootstrap_pi_over_ssh.sh
```

Use the real Pi username/hostname if different.

## 3. SSH Key From Mac To Pi

From the Mac:

```bash
PI_USER=pi PI_HOST=raspberrypi.local ./scripts/mac/install_pi_ssh_key.sh
PI_USER=pi PI_HOST=raspberrypi.local ./scripts/mac/setup_pi_ssh_config.sh
./scripts/mac/test_pi_ssh.sh
```

Use the real Pi username/hostname if different:

```bash
PI_USER=dronebox PI_HOST=dronebox.local ./scripts/mac/install_pi_ssh_key.sh
```

## 4. Transfer Test

From the Mac:

```bash
echo "hello pi" > transfer/mac_to_pi/hello.txt
PI_USER=pi PI_HOST=raspberrypi.local ./scripts/mac/sync_to_pi.sh
```

On the Pi:

```bash
cat ~/DroneTransfer/incoming/hello.txt
```

From the Pi:

```bash
echo "hello mac" > ~/DroneTransfer/outgoing/hello-from-pi.txt
```

From the Mac:

```bash
PI_USER=pi PI_HOST=raspberrypi.local ./scripts/mac/sync_from_pi.sh
cat transfer/pi_to_mac/hello-from-pi.txt
```

## 5. Camera Smoke Test

On the Pi:

```bash
rpicam-hello --list-cameras
python -m vision_nav.capture_frame --output ~/DroneTransfer/outgoing/global_shutter_test.jpg
```

If `rpicam-*` is unavailable, the capture helper will try `libcamera-still`.

Run the full camera/Python synthetic-map smoke test:

```bash
cd Drone
./scripts/pi/smoke_test_vision.sh
```

Outputs are written to:

```text
~/DroneTransfer/outgoing/vision-smoke/
```

## 5a. Camera Calibration

On the Pi:

```bash
cd Drone
./scripts/pi/capture_calibration_set.sh
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

Adjust the chessboard dimensions and square size to your actual calibration
board.

Run the Docker synthetic-map smoke test:

```bash
cd Drone
./scripts/pi/smoke_test_docker.sh
```

Outputs are written to:

```text
Drone/data/docker-smoke/
```

## 6. First Vision Match Test

Use any map-like image and query image:

```bash
source ~/drone_vision_nav_venv/bin/activate
vision-nav-build-bundle --bundle mission_bundle
vision-nav-match-bundle-frame \
  --bundle mission_bundle \
  --frame query.jpg \
  --viz match_debug.jpg
```

The result should report `accepted`, `rejected`, or `failed`, along with
confidence, inlier count, and reprojection error.

For a georeferenced first pass:

```bash
cp -R map_bundles/example mission_bundle
# Put your map at mission_bundle/ortho/map.png and edit mission_bundle/manifest.json.
vision-nav-build-bundle --bundle mission_bundle --write-checksums
vision-nav-bundle-checksums --bundle mission_bundle --verify
```

The matcher will include an approximate lat/lon estimate when an accepted
homography is available. Runtime and replay logs also include homography
geometry metrics such as scale, rotation, anisotropy, and perspective so bad
matches can be rejected before they become navigation candidates.

## 7. Bench Runtime Loop

Copy a mission bundle to the Pi:

```text
~/drone-data/map_bundles/mission_bundle
```

Validate the bundle:

```bash
cd Drone
./scripts/pi/validate_vision_nav_bundle.sh
```

When you want the Pi to fail fast on a partial or stale map-bundle transfer:

```bash
VISION_NAV_REQUIRE_CHECKSUMS=1 ./scripts/pi/validate_vision_nav_bundle.sh
```

Then run:

```bash
./scripts/pi/run_vision_nav_loop.sh
```

The loop captures Global Shutter frames, matches them to the bundle, and writes
review artifacts to:

```text
~/DroneTransfer/outgoing/runtime-match/
```

Tune geometry rejection thresholds with environment variables such as
`VISION_NAV_MAX_ROTATION_DEG`, `VISION_NAV_MAX_SCALE_ANISOTROPY`, and
`VISION_NAV_MAX_PERSPECTIVE_NORM` after you have real logs.

Pull the results back to the Mac:

```bash
PI_USER=pi PI_HOST=raspberrypi.local ./scripts/mac/sync_from_pi.sh
```

Replay the captured frames without touching the camera:

```bash
cd Drone
./scripts/pi/replay_vision_nav_frames.sh
```

Summarize runtime/replay logs:

```bash
./scripts/pi/summarize_vision_nav_logs.sh
```

After the manual loop works, optionally install the user service:

```bash
./scripts/pi/install_vision_nav_service.sh
systemctl --user start drone-vision-nav.service
systemctl --user status drone-vision-nav.service
journalctl --user -u drone-vision-nav.service -f
```

## 7a. Local Handoff Audit

On the Mac, before committing, pushing, or syncing to the Pi:

```bash
cd /Users/izzyfisi/Documents/DRONE
./scripts/dev/handoff_audit.sh
```

## 8. GitHub Push Status

Do not push until explicitly requested.

Important: this local repo currently has no commits. The GitHub repository may
already contain earlier planning docs, so the first push should be done after
checking the remote state and deciding whether to merge, replace, or branch.

See [GitHub Push Plan](github-push-plan.md) for the safe upload path.

Before any commit:

```bash
./scripts/dev/local_preflight.sh
```
