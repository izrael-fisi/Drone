# Operator Handoff

This repo is prepared for the Raspberry Pi 5 vision-navigation setup. The
current setup work is published as draft PR #1:

```text
https://github.com/izrael-fisi/Drone/pull/1
```

## Current Storage Assumption

The Raspberry Pi is currently using its onboard 256GB microSD for everything.
That is the default for the setup scripts:

```text
~/DroneTransfer/
~/drone-data/
```

Use the microSD for the first setup, camera checks, calibration images, small
mission map bundles, and short runtime logs.

If a USB SSD is added later, keep the repo in `~/Drone` and move only bulky
runtime data by editing `config/pi/vision-nav.env` on the Pi:

```bash
VISION_NAV_BUNDLE=/mnt/drone-ssd/map_bundles/mission_bundle
VISION_NAV_OUTPUT_DIR=/mnt/drone-ssd/runtime-match
VISION_NAV_REPLAY_OUTPUT_DIR=/mnt/drone-ssd/replay-match
```

## Path A: GitHub Clone On The Pi

Use this path after the draft PR is merged, or clone the PR branch directly
while it is still under review.

On the Raspberry Pi:

```bash
git clone -b codex/pi-vision-nav-setup-pr https://github.com/izrael-fisi/Drone.git
cd Drone
chmod +x scripts/pi/*.sh
./scripts/pi/bootstrap_pi5.sh
sudo reboot
```

After the PR is merged, clone the default branch instead:

```bash
git clone https://github.com/izrael-fisi/Drone.git
```

After reboot:

```bash
cd Drone
./scripts/pi/first_run_checks.sh
```

Or run the same checks remotely from the Mac and pull the generated reports:

```bash
PI_USER=pi PI_HOST=raspberrypi.local ./scripts/mac/run_pi_first_checks.sh
```

The camera health report will be at:

```text
~/DroneTransfer/outgoing/camera-health/camera_health_report.json
```

## Path B: SSH Sync From The Mac

Use this path before a GitHub push, or when iterating locally.

On the Pi:

```bash
cd Drone
./scripts/pi/enable_ssh_transfer.sh
hostname
hostname -I
```

Give Codex:

- Pi username from `whoami`
- Pi hostname from `hostname`
- Pi IP address from `hostname -I`
- whether `raspberrypi.local` or another `.local` name works from the Mac

On the Mac:

```bash
PI_USER=pi PI_HOST=raspberrypi.local ./scripts/mac/install_pi_ssh_key.sh
PI_USER=pi PI_HOST=raspberrypi.local ./scripts/mac/setup_pi_ssh_config.sh
./scripts/mac/test_pi_ssh.sh
PI_USER=pi PI_HOST=raspberrypi.local ./scripts/mac/bootstrap_pi_over_ssh.sh
```

Replace `pi` and `raspberrypi.local` with the real values.

After the Pi reboots from bootstrap:

```bash
PI_USER=pi PI_HOST=raspberrypi.local ./scripts/mac/run_pi_first_checks.sh
```

## Pi Info Codex May Need

From the Mac, this repo now has a status helper:

```bash
./scripts/mac/goal_status.sh
```

That status helper also runs the autonomy proof summary from
`scripts/dev/autonomy_goal_status.sh`, so it shows the current proof-item
counts, external blockers, and next evidence commands before checking Pi
connectivity. Missing proof does not stop the connectivity checks.

If the Pi has a known address, pass it explicitly:

```bash
PI_USER=pi PI_HOST=192.168.1.123 ./scripts/mac/goal_status.sh
```

The easiest option is to run this on the Pi:

```bash
cd Drone
./scripts/pi/collect_pi_info.sh
```

Then pull the report back to the Mac:

```bash
PI_USER=pi PI_HOST=raspberrypi.local ./scripts/mac/sync_from_pi.sh
```

If the repo is not on the Pi yet, send the output of:

```bash
whoami
hostname
hostname -I
cat /etc/os-release
rpicam-hello --list-cameras
df -h /
lsblk -o NAME,SIZE,TYPE,MOUNTPOINTS,FSTYPE
```

## Mac SSH Note

Mac Remote Login requires admin permission. Run this on the Mac if remote login
is needed:

```bash
./scripts/mac/enable_remote_login.sh
```

## Autonomy Readiness Handoff

To see a quick local summary of the full autonomy goal without writing a new
handoff package, run:

```bash
./scripts/dev/autonomy_goal_status.sh
```

It runs the strict readiness audit with the repo-local Python path, prints the
current proof-item counts, external blockers, proof-runbook phase counts, and
the next commands needed to collect missing evidence. The command list follows
the proof runbook, so blocked work such as method/threshold tuning waits until
the field dataset phase is ready, ROS replay validation waits for real field
terrain logs, and bench proof starts with PX4 receiver capture before
support-bundle creation. It scans the conventional
`~/DroneTransfer/from-pi/` evidence folders and includes any downloaded support
bundle, PX4 receiver report, field plan, field evidence, feature benchmark,
threshold report, ROS bag validation, or native rosbag2 review it finds. It
exits nonzero until the full final proof package is ready. Set
`VISION_NAV_AUTONOMY_GOAL_STATUS_JSON=/path/to/report.json` to keep the raw
JSON snapshot from that check.

After bench artifacts and field replay evidence have been downloaded, run:

```bash
./scripts/dev/run_local_autonomy_readiness_audit.sh
```

The wrapper writes both:

```text
~/DroneTransfer/from-pi/replay-cases/autonomy_readiness_report.json
~/DroneTransfer/from-pi/replay-cases/autonomy_readiness_report.md
~/DroneTransfer/from-pi/replay-cases/autonomy_readiness_report.evidence.zip
```

The JSON report is the strict machine-readable gate. The Markdown handoff is
for human review and summarizes status, inputs, checks, external proof blockers,
missing field conditions, bench subchecks, and next actions. It also renders
checkbox checklists for missing field-evidence conditions and failed/degraded
bench subchecks so the next evidence-collection pass can be tracked directly
from the handoff. Its command bundle includes pending field replay preflight,
preflight-plus-capture, capture, metadata-update, and registration commands so
the operator can rerun checks immediately before collecting the next terrain
log. The handoff also includes a proof runbook that orders source plan, bench,
field dataset, method/threshold, ROS replay, and final-audit phases so
downstream proof dependencies are visible. When rendered on a machine that can
see the referenced artifacts, it also includes an artifact-availability table
with present/missing state and file sizes.
The evidence ZIP packages the JSON report, Markdown handoff, and small
referenced evidence artifacts that exist locally; large or missing artifacts are
listed in its manifest instead of being silently ignored.
