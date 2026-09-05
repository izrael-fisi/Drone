# Drone GNSS-Denied Vision Navigation

**Transferring to another LLM or development machine?** Start with
[LLM_HANDOFF.md](LLM_HANDOFF.md), the
[technical requirements and status](docs/technical-handoff-2026-09-05.md), and
[verification/artifact inventory](docs/handoff-verification-2026-09-05.md).
This is shadow research, not a flight-qualified GNSS-denied navigation release.

This repository contains the two active parts of the GNSS-denied navigation
project:

1. Drone runtime code for the Raspberry Pi companion computer.
2. A ground-control / mission-planner desktop app.

ROS 2, Gazebo, and PX4 SITL are no longer part of the active project scaffold.
Hardware bench testing with the real Pixhawk/Holybro stack is the path forward.

The target is a vision-first, fail-closed runtime. Current research combines:

```text
calibrated camera + synchronized raw IMU -> native local VIO
camera windows + preloaded map -> MegaLoc -> cross-attention student
  -> XFeat + LighterGlue -> surface-backed geometry
  -> conditional delayed alignment research -> shadow evidence

Still unqualified: continuous current global estimates, persistent global
hypotheses, measured field uncertainty, Pi performance and aircraft output.
```

The project goal is narrow:

- Downward camera visual map-relative localization
- Learned map releases from georeferenced imagery, including COG/STAC metadata,
  MegaLoc indexes, and XFeat tile caches
- Versioned map/model/calibration compatibility checks, persistent ambiguity
  management, and robust geometry before an external-vision observation exists
- Approximate map-pixel to lat/lon conversion for simple orthophoto bundles
- Pixhawk/PX4 MAVLink integration as a status source after bench validation

The active vehicle profile is `pi5_crossview_nav_v1`: MegaLoc, XFeat +
LighterGlue, and a project-owned map-conditioned temporal student. Distillation
and field performance are not established by the profile name. ORB, AKAZE, and SIFT
are frozen replay baselines only. They are not an active map builder, Pi runtime,
desktop setting, mission-bundle format, or automatic fallback.

Read [Target Vision-First Architecture](docs/architecture.md),
[Integration Interface Reference](docs/interface-reference.md),
[PX4 Integration Boundary](docs/px4-integration.md), and
[Change Playbook](docs/change-playbook.md) before binding a model or vehicle.
The [Architecture Reassessment](docs/architecture-reassessment-2026-08.md)
records what is implemented today and the modern stack selected for migration.
The active learned product program is documented in
[Flagship Modern Vision Pipeline](docs/flagship-modern-vision-pipeline.md).

## Current Hardware Target

- Raspberry Pi 5 16GB
- Raspberry Pi Global Shutter Camera, downward-facing
- 256GB microSD for current logs and map bundles
- Optional USB 3 SSD later for larger maps, image logs, and long test runs
- Holybro X500 V2 kit with Pixhawk 6C-class flight controller
- IMU/attitude telemetry from PX4 for vision-estimator context
- Optional barometer telemetry in legacy tools; not qualified learned fusion

## Active Project Sections

### 1. Drone Code Operation

- `src/vision_nav/neural/`: learned model/map releases, ONNX execution,
  MegaLoc retrieval, XFeat/LighterGlue evidence, temporal windows, robust
  geometry, and shadow candidate gating
- `src/vision_nav/`: camera calibration, MAVLink/PX4 read-only telemetry,
  training capture, field-evidence, and support-bundle tools
- `scripts/pi/`: Raspberry Pi bootstrap, camera/MAVLink bench checks, learned
  release validation, shadow runtime, training capture, and support-bundle
  wrappers
- `scripts/mac/`: Mac transfer and SSH helpers for moving bundles/logs between
  the desktop and Raspberry Pi
- `docker/pi/`: optional reproducible offline map-build and replay container;
  the hardware runtime owns camera/MAVLink devices through host systemd
- `config/`: camera and Pi runtime configuration examples

### 2. Ground Control / Mission Planner Desktop App

- `desktop-app/`: Tauri + React operator app for map selection/import, Vision
  Pipeline configuration, Mission Planner layers, module setup, camera preview,
  MAVLink-enabled runtime checks, and support-bundle review
- `docs/`: hardware-first setup, calibration, mission planning, and test plans
- `transfer/`: local staging folder for Mac-to-Pi and Pi-to-Mac file movement

Run the desktop UI without installing Node on Windows by using Docker Desktop:

```powershell
.\scripts\dev\desktop_docker.ps1 dev
```

Then open `http://localhost:5173`. Use `.\scripts\dev\desktop_docker.ps1 build`
for a production frontend build, or `preview` to serve the built output at
`http://localhost:4173`. The Docker runtime is for the React operator UI and
browser fallback/Edge API workflows; the native Tauri window still requires the
local Windows Tauri/Rust toolchain.

Key docs:

- [Stable Project Scaffold](docs/stable-project-scaffold.md)
- [Raspberry Pi Setup](docs/raspberry-pi-setup.md)
- [Camera Calibration](docs/camera-calibration.md)
- [Vision Pipeline](docs/vision-pipeline.md)
- [Holybro X500 V2 Hardware Data Inputs](docs/holybro-x500v2-hardware-data-inputs.md)
- [Holybro X500 V2 Prop-Off Hardware Test](docs/holybro-x500v2-prop-off-hardware-test.md)
- [PX4 External Vision Bench Guide](docs/px4-external-vision-bench.md)
- [Windows 11 Codex Handoff](docs/windows-11-codex-handoff.md)
- [SSH And File Transfer](docs/ssh-and-transfer.md)
- [Desktop App](docs/desktop-app.md)
- [Operator Handoff](docs/operator-handoff.md)
- [First Learned Shadow Run](docs/first-learned-shadow-run.md)
- [Cloud + GovCloud Readiness](docs/cloud/README.md)

## Quick Pi Setup

On the Raspberry Pi:

```bash
git clone https://github.com/izrael-fisi/Drone.git
cd Drone
chmod +x scripts/pi/*.sh
./scripts/pi/bootstrap_pi5.sh
sudo reboot
```

After reboot:

```bash
cd Drone
./scripts/pi/first_run_checks.sh
```

Rebooting after bootstrap makes Docker group membership and camera/system
services fully active.

Enable the companion Edge API used by the desktop app:

```bash
cd ~/Drone
./scripts/pi/install_vision_nav_service.sh
sudo loginctl enable-linger $USER
systemctl --user start drone-vision-nav-api.service
systemctl --user start drone-vision-nav-status-bridge.service
curl http://127.0.0.1:5000/health
```

After you copy an approved MapRelease v2 and ModelRelease to the Pi, validate
the release chain and start learned shadow telemetry. Stop other serial-device
owners before running a session that needs exclusive camera/MAVLink access:

```bash
./scripts/pi/validate_neural_runtime.sh
./scripts/pi/run_vision_nav_shadow.sh
```

The Pi writes `runtime_status.json` and `decision_trace.jsonl` under
`~/DroneTransfer/outgoing/neural-shadow/`. PX4 external-vision publication is
disabled during this shadow stage.

For the exact camera, model-release, map-release, and PX4-attitude sequence,
follow [First Learned Shadow Run](docs/first-learned-shadow-run.md). The shadow
runtime may record retrieval, correspondence, geometry, and temporal candidate
evidence, but it never sends `ODOMETRY`, external vision, parameter changes, or
motor commands to PX4.

Root-level terrain-bundle and feature-method tools remain as frozen replay and
migration baselines. They are not the active vehicle runtime or a fallback for
`pi5_crossview_nav_v1`.

Package a bench run for debugging or desktop transfer:

```bash
./scripts/pi/create_support_bundle.sh
```

Before committing or handing the repo to the Pi:

```bash
./scripts/dev/handoff_audit.sh
```
