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

Use the microSD for setup, camera checks, calibration images, small mission map
bundles, and short runtime logs. Add a USB SSD later only for larger maps or
longer logs.

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

## Active Bench Commands

Validate the deployed terrain bundle:

```bash
VISION_NAV_BUNDLE=$HOME/drone-data/map_bundles/mission_bundle \
./scripts/pi/validate_terrain_bundle.sh
```

Run a logging-only terrain capture:

```bash
VISION_NAV_BUNDLE=$HOME/drone-data/map_bundles/mission_bundle \
VISION_NAV_COUNT=30 \
./scripts/pi/run_terrain_nav_loop.sh
```

Read runtime status:

```bash
VISION_NAV_RUNTIME_STATUS_ROOTS=$HOME/DroneTransfer/outgoing/terrain-match \
./scripts/pi/read_runtime_status.sh
```

Check Pixhawk/PX4 parameter export:

```bash
VISION_NAV_PX4_PARAMS=$HOME/px4.params ./scripts/pi/check_px4_params.sh
```

Create a support bundle:

```bash
./scripts/pi/create_support_bundle.sh
```

## Holybro X500 V2 Arrival

Use:

- [Holybro X500 V2 Hardware Data Inputs](holybro-x500v2-hardware-data-inputs.md)
- [Holybro X500 V2 Prop-Off Hardware Test](holybro-x500v2-prop-off-hardware-test.md)

Propellers stay removed for every first hardware command.
