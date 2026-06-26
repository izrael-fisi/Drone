# Codex Project Context

This file is the first-stop context for Codex or another coding agent after
cloning the repo, especially on a Windows 11 development machine.

## Project Goal

Build a GNSS-denied drone navigation stack with two active sections:

1. **Drone runtime code** for Raspberry Pi 5 + Pixhawk/PX4 hardware.
2. **Ground-control / mission-planner desktop app** for map selection, mission
   planning, runtime setup, telemetry monitoring, and evidence review.

The active navigation goal is vision + IMU/PX4-state localization against
preloaded georeferenced maps. GPS is primary when healthy; terrain vision and
dead reckoning are the fallback path when GPS is weak, jammed, or unavailable.

## What Is Out Of Scope

- Do not reintroduce ROS 2, Gazebo, or PX4 SITL as active project scaffolding.
- Do not add LLM/MCP flight-control features.
- Do not make neural matching the default runtime path. ORB/AKAZE classical CV
  remains the Raspberry Pi-safe baseline; SuperPoint/LightGlue remains optional
  higher-compute scaffolding.
- Do not auto-change PX4 parameters from the desktop app. Document and verify
  parameter guidance instead.

## Key Directories

- `src/vision_nav/`: Python terrain vision runtime, bundle tools, matching,
  estimator, MAVLink/status bridge, support/evidence tooling.
- `scripts/pi/`: Raspberry Pi setup, validation, runtime, field capture, status
  bridge, and support-bundle wrappers.
- `desktop-app/`: Tauri + React operator app.
- `desktop-app/export-preview/`: source files for the Windows browser-preview
  zip package.
- `docs/`: operator, hardware, PX4, desktop app, and Windows handoff docs.
- `config/`: camera, Pi runtime, and PX4 hardware snapshots.

Generated folders such as `desktop-app/dist/`, `desktop-app/export/`,
`desktop-app/node_modules/`, and `desktop-app/src-tauri/target/` should not be
committed.

## Desktop App Direction

The desktop app should be map-first and operator-focused:

- Start/Dashboard: live operations map, active map, active device, position
  source, GNSS-denied bundle state, quick actions.
- Map: create/download/import map sources and attach DEM/DSM metadata.
- Mission: plan takeoff, waypoint, land, geofence, rally, and vision map
  checkpoints.
- Drone: manage runtime devices, Raspberry Pi SSH setup, camera/MAVLink checks,
  and prop-off bench workflows.
- Fly: system status, MAVLink diagnostics, GPS/vision/dead-reckoning source
  state, readiness.
- Review: support bundles, field evidence, GPS-vs-vision track replay.

Keep Vision Pipeline / Camera & Vision as the only editable algorithm settings
surface. Other panes may summarize those settings.

## Verification Commands

From repo root:

```bash
python tests/run_unit_tests.py
```

Desktop frontend:

```bash
cd desktop-app
npm ci
npm run build
```

Tauri/Rust backend:

```bash
cd desktop-app/src-tauri
cargo check
cargo test
```

Windows UI preview export:

```bash
./scripts/dev/export_desktop_ui_preview.sh
```

On Windows PowerShell, use the equivalent:

```powershell
cd desktop-app
npm ci
npm run build
```

See `docs/windows-11-codex-handoff.md` for the Windows setup path.

## Hardware Safety Notes

- Treat Holybro X500 / Pixhawk tests as prop-off until explicitly cleared.
- Bench workflows should collect evidence before enabling in-flight external
  vision control.
- MAVLink external vision/odometry is the PX4-first path; ArduPilot adapters are
  future optional work.
- PX4 parameter snapshots in `config/px4/` are evidence, not blind restore files.

## Commit Hygiene

- Keep source, docs, scripts, and test fixtures in Git.
- Keep generated zips, build outputs, downloaded maps, logs, support bundles,
  node modules, and Rust targets out of Git.
- Before pushing, run at least `npm run build` for the desktop app and
  `cargo check` in `desktop-app/src-tauri`.
