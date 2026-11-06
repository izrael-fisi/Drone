# Codex Project Context

This file is the first-stop context for Codex or another coding agent after
cloning the repo, especially on a Windows 11 development machine.

For a conversation-independent transfer, first read `LLM_HANDOFF.md`,
`docs/technical-handoff-2026-09-05.md`, and
`docs/handoff-verification-2026-09-05.md`. They distinguish implementation from
targets, document test limitations and list artifacts excluded from Git.
Immediate priority is camera/capture, current-state estimation and training;
desktop expansion can wait. No learned aircraft transport is enabled.

## Project Goal

Build a GNSS-denied drone navigation stack with two active sections:

1. **Drone runtime code** for Raspberry Pi 5 + Pixhawk/PX4 hardware.
2. **Ground-control / mission-planner desktop app** for map selection, mission
   planning, runtime setup, telemetry monitoring, and evidence review.

The active navigation goal is vision-first localization against versioned,
preloaded georeferenced maps. GPS is an independent primary telemetry source
when healthy; the visual system must never silently become a metric global pose
without map geometry, calibration, timestamp, covariance, and ambiguity gates.

Read `docs/architecture.md`, `docs/interface-reference.md`,
`docs/px4-integration.md`, and `docs/change-playbook.md` before changing the
navigation path. Those documents supersede the older prototype architecture.

## What Is Out Of Scope

- Do not reintroduce ROS 2 or Gazebo as active project scaffolding. The staged
  validation ladder may call for PX4 SITL evidence in a future vehicle-integration
  milestone, but no SITL framework belongs in routine setup or runtime paths.
- Do not add LLM/MCP flight-control features.
- `pi5_crossview_nav_v1` is the active vehicle profile: learned retrieval,
  learned correspondence, and a distilled temporal student. ORB/AKAZE/SIFT are
  frozen replay baselines only; do not add them to active map building, runtime,
  mission bundles, or operator settings.
- Do not auto-change PX4 parameters from the desktop app. Document and verify
  parameter guidance instead.

## Key Directories

- `src/vision_nav/vio/` and `research/native/openvins/`: opt-in ROS-free native
  local VIO research replay and finite live experiments. Pinned OpenVINS GPL code is not an approved product
  dependency. KLT tracks local motion only; learned map matching is unchanged.
  Native posterior covariance must not be relabeled as independent smoother
  factor noise. No Pi service or PX4 publication is enabled by this adapter.

- `src/vision_nav/neural/`: active learned runtime. New model-release,
  retrieval, local-matching, temporal, geometry, and shadow-evidence behavior
  belongs here.
- `src/vision_nav/`: camera calibration/capture, MAVLink read-only telemetry,
  PX4 boundary, training data, and support/evidence tooling.
- `src/vision_nav/types.py`, `contracts.py`, `runtime/`, `estimation/`,
  `mapping/`, and `px4/`: shared contracts and future promotion boundary.
- Legacy root-level terrain matching modules remain replay/migration tools.
  They must not gain learned-runtime or PX4 publication paths.
- `scripts/pi/`: Raspberry Pi setup, validation, runtime, field capture, status
  bridge, and support-bundle wrappers.
- `desktop-app/`: Tauri + React operator app.
- The currently routed map-first app is mainly `src/App.tsx`,
  `src/platform/` (Proxigo shell: product registry, entitlements, dock,
  command palette, account/settings), `src/products/macula-vps/` (VPS panels
  and bottom dock), `src/products/airspace-weather/` (second product), `src/platform/api/`
  (backend connection, discovery, Rust-routed HTTP) and
  `src/pages/Dashboard.tsx` (shared map; VPS tools show only in the VPS view)
  inside desktop-app. Many
  older page routes now open shared docks. Trace imports/routes before
  editing an old page; do not recreate duplicate controls or classical defaults.
  New products register a `ProductModule`; see `docs/desktop-platform.md`.
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
- Only `vision_nav.px4.Px4VisionBridge` may cross the new runtime boundary.
  It publishes nothing unless mode is `TRACKING`, the estimate is fresh and
  finite, covariance is within policy, map/model/calibration compatibility is
  confirmed, and transport health is known.
- PX4 parameter snapshots in `config/px4/` are evidence, not blind restore files.

## Commit Hygiene

- Keep source, docs, scripts, and test fixtures in Git.
- Keep generated zips, build outputs, downloaded maps, logs, support bundles,
  node modules, and Rust targets out of Git.
- Before pushing, run at least `npm run build` for the desktop app and
  `cargo check` in `desktop-app/src-tauri`.
- Freeze source edits while hash-bound native/model worker tests run. Report
  missing artifacts and skipped tests explicitly; green contract tests do not
  prove current global output, Pi performance, model accuracy or flight readiness.
