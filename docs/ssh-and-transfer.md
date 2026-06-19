# SSH And File Transfer

The goal is to move map bundles, logs, camera frames, and scripts between the
MacBook Pro and Raspberry Pi without manual USB juggling.

## Folder Layout

On the Mac:

```text
~/DroneTransfer/
  to-pi/
  from-pi/
```

On the Raspberry Pi:

```text
~/DroneTransfer/
  incoming/
  outgoing/
  logs/
  map-bundles/
```

This repo also has a lightweight staging folder:

```text
transfer/
  mac_to_pi/
  pi_to_mac/
```

Git keeps the folders but ignores transferred payload files.

## Enable SSH On Raspberry Pi

Run on the Pi:

```bash
cd Drone
./scripts/pi/enable_ssh_transfer.sh
```

Check the Pi address:

```bash
hostname -I
hostname
```

Raspberry Pi OS often works over mDNS as:

```text
raspberrypi.local
```

## Enable SSH On Mac

Run on the Mac:

```bash
cd /Users/izzyfisi/Documents/DRONE
chmod +x scripts/mac/*.sh
./scripts/mac/setup_mac_ssh_and_transfer.sh
```

The script creates `~/DroneTransfer` and shows the command needed to enable
macOS Remote Login. Enabling Remote Login requires administrator permission.

## Copy Files From Mac To Pi

First install the Mac SSH public key on the Pi:

```bash
PI_USER=pi PI_HOST=raspberrypi.local ./scripts/mac/install_pi_ssh_key.sh
```

Use your actual Pi username/hostname if different:

```bash
PI_USER=dronebox PI_HOST=dronebox.local ./scripts/mac/install_pi_ssh_key.sh
```

Create a short SSH alias:

```bash
PI_USER=pi PI_HOST=raspberrypi.local ./scripts/mac/setup_pi_ssh_config.sh
```

This adds a `drone-pi` entry to `~/.ssh/config`, so future commands can use:

```bash
ssh drone-pi
```

Test non-interactive SSH:

```bash
./scripts/mac/test_pi_ssh.sh
```

To sync this whole repository to the Pi and run the Pi bootstrap remotely:

```bash
PI_USER=pi PI_HOST=raspberrypi.local ./scripts/mac/bootstrap_pi_over_ssh.sh
```

Example:

```bash
PI_USER=pi PI_HOST=raspberrypi.local ./scripts/mac/sync_to_pi.sh
```

If your Pi username is different:

```bash
PI_USER=dronebox PI_HOST=dronebox.local ./scripts/mac/sync_to_pi.sh
```

## Copy Files From Pi To Mac

From the Mac:

```bash
PI_USER=pi PI_HOST=raspberrypi.local ./scripts/mac/sync_from_pi.sh
```

## SSH Login Test

```bash
ssh pi@raspberrypi.local
```

or:

```bash
ssh dronebox@dronebox.local
```

## Run Pi Commands From The Mac

Use `pi_exec.sh` for one-off commands:

```bash
PI_USER=pi PI_HOST=raspberrypi.local ./scripts/mac/pi_exec.sh 'hostname && uptime'
```

After setting up the `drone-pi` alias, plain SSH is also easy:

```bash
ssh drone-pi 'hostname && uptime'
```

Use `pi_status.sh` for a quick status report:

```bash
PI_USER=pi PI_HOST=raspberrypi.local ./scripts/mac/pi_status.sh
```

Run the full first-run verification remotely:

```bash
PI_USER=pi PI_HOST=raspberrypi.local ./scripts/mac/run_pi_first_checks.sh
```

For a faster check that skips the Docker smoke test:

```bash
VISION_NAV_SKIP_DOCKER_SMOKE=1 PI_USER=pi PI_HOST=raspberrypi.local ./scripts/mac/run_pi_first_checks.sh
```

Use `VISION_NAV_SKIP_CAMERA_HEALTH=1` only when intentionally running the remote
check without the Raspberry Pi camera attached.

## Goal Status Check

From the Mac, run:

```bash
./scripts/mac/goal_status.sh
```

If the Pi does not resolve as `raspberrypi.local`, pass the real host or IP:

```bash
PI_USER=pi PI_HOST=192.168.1.123 ./scripts/mac/goal_status.sh
```

This checks the local transfer folders, Mac SSH key, macOS Remote Login status,
current GitHub PR branch, Pi hostname reachability, and non-interactive SSH.

On the Pi, collect a full diagnostic report:

```bash
cd Drone
./scripts/pi/collect_pi_info.sh
```

Then pull it back to the Mac:

```bash
PI_USER=pi PI_HOST=raspberrypi.local ./scripts/mac/sync_from_pi.sh
```
