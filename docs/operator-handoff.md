# Operator Handoff

This is the current handoff for operating the repo with a Raspberry Pi 5,
desktop app, and upcoming Holybro X500 V2 hardware.

## Active Scope

The repo has two active sections:

1. Drone code operation on the Raspberry Pi.
2. Ground control / mission planner desktop app.

Use real hardware bench tests, not simulator or middleware-first validation.

## Current Storage Assumption

The Raspberry Pi uses its onboard 256GB microSD for the first setup:

```text
~/Drone
~/DroneTransfer/
~/drone-data/
```

Use the microSD for setup, camera checks, calibration images, reference maps,
short training captures, and short runtime logs. Add a USB SSD later only for
larger map releases, longer captures, or retained research datasets.

## Pi Identity Needed By Codex Or The Desktop App

On the Pi:

```bash
whoami
hostname
hostname -I
cat /etc/os-release
```

Also useful:

```bash
rpicam-hello --list-cameras
df -h /
lsblk -o NAME,SIZE,TYPE,MOUNTPOINTS,FSTYPE
```

## Mac To Pi SSH

On the Pi:

```bash
cd ~/Drone
./scripts/pi/enable_ssh_transfer.sh
```

On the Mac:

```bash
PI_USER=pi PI_HOST=raspberrypi.local ./scripts/mac/install_pi_ssh_key.sh
PI_USER=pi PI_HOST=raspberrypi.local ./scripts/mac/setup_pi_ssh_config.sh
./scripts/mac/test_pi_ssh.sh
```

Replace `pi` and `raspberrypi.local` with the real values.

## First Pi Checks

On the Pi:

```bash
cd ~/Drone
./scripts/pi/first_run_checks.sh
```

From the Mac:

```bash
PI_USER=pi PI_HOST=raspberrypi.local ./scripts/mac/run_pi_first_checks.sh
```

## Active Prop-Off Path

The first real-data path is camera/PX4 evidence, not legacy map matching. Keep
the vehicle disarmed with propellers removed:

```bash
cd ~/Drone
./scripts/pi/capture_camera_bench.sh
VISION_NAV_MAVLINK_ENDPOINT=serial:/dev/ttyACM0:921600 \
./scripts/pi/capture_mavlink_bench.sh
```

After measured intrinsics, camera-to-body transform, and a
`ReferenceMapRelease` are available, use the single preparation gate:

```bash
VISION_NAV_PROP_OFF_CONFIRMED=1 \
VISION_NAV_REFERENCE_MAP=$HOME/drone-data/reference-maps/first-site/reference_map.json \
VISION_NAV_CALIBRATION_ID=down-camera-v1 \
VISION_NAV_MAVLINK_ENDPOINT=serial:/dev/ttyACM0:921600 \
VISION_NAV_TRAINING_SESSION_ID=first-field-capture \
./scripts/pi/prepare_first_training_capture.sh
```

The printed recorder command creates the synchronized camera/PX4 evidence
session. It does not arm the aircraft, modify PX4, or publish vision position.

To run an approved learned model/map combination later, use
`./scripts/pi/run_vision_nav_shadow.sh`; it remains shadow-only and reports
`TRACKING`, `RECOVERING`, or `NO_GLOBAL_FIX`.

Check Pixhawk/PX4 parameter export:

```bash
VISION_NAV_PX4_PARAMS=$HOME/px4.params ./scripts/pi/check_px4_params.sh
```

Create a support bundle:

```bash
VISION_NAV_TRAINING_CAPTURE_DIR=$HOME/drone-data/captures/first-field-capture \
./scripts/pi/create_support_bundle.sh
```

## Holybro X500 V2 Arrival

Use:

- [Holybro X500 V2 Hardware Data Inputs](holybro-x500v2-hardware-data-inputs.md)
- [Holybro X500 V2 Prop-Off Hardware Test](holybro-x500v2-prop-off-hardware-test.md)

Propellers stay removed for every first hardware command.
