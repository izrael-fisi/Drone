# Drone Vision Desktop App Requirements

This README is the design and product handoff for importing the desktop app into
Stitch. Stitch panes may be used as visual references, but the app should remain
fully usable and connected to the existing data pipeline. Do not treat any static
pane template as authoritative if it conflicts with the working app behavior.

## Product Goal

Drone Vision is a ground-control and mission-planning desktop app for a
GNSS-denied terrain-vision navigation drone.

The app helps an operator:

- prepare local georeferenced maps
- configure the camera and feature-matching pipeline
- create a flight mission and terrain bundle
- connect to a Raspberry Pi runtime computer
- launch and validate the onboard vision navigation loop
- monitor GPS versus terrain-vision position telemetry
- review bench and field evidence after tests

The app is hardware-first. ROS 2 and SITL controls should not be first-class
operator surfaces.

## App Architecture

The desktop app is a Tauri + React + TypeScript application.

Primary folders:

- `src/`: React UI, routing, pages, app store, and Tauri command wrappers
- `src-tauri/`: Rust backend commands for map import, SSH, bundle build,
  telemetry, support bundle handling, and local command wrappers
- `public/`: static public assets
- `dist/`: built static preview output

Stitch should preserve the app as a navigable operations console rather than a
set of isolated static screens.

## Navigation Requirements

The left navigation must stay available across the main app:

- Ops Console
- Map Library
- Mission Planner
- Vehicle Manager
- Camera & Vision
- Diagnostics
- Flight Review
- Settings

Route aliases should still work because operator language varies:

- `/navigation-panel` aliases `/dashboard`
- `/vehicle-manager` aliases `/devices`
- `/camera-vision` aliases `/vision-pipeline`
- `/mission-bundle-builder` aliases `/mission-planner`
- `/diagnostics` aliases `/system-status`
- `/history` aliases `/flight-review`

The app must use native pane transitions and real route navigation. Do not embed
the working app as an iframe.

## Core Page Requirements

### Ops Console

Purpose: operator home and launch surface.

Must show:

- active device status
- downloaded map count
- saved region count
- readiness checklist
- quick action cards for maps, vision pipeline, devices, and mission planning
- saved regions with edit/delete/plan actions

### Map Library

Purpose: prepare map data for GNSS-denied localization.

Must support:

- drawing a map area
- estimating tile count and storage
- downloading satellite map sources
- importing local map folders
- importing/uploading common image formats
- handling GeoTIFF metadata when available
- showing georeference quality and map readiness
- preserving map lifecycle states such as local, built, uploaded, active, stale,
  or failed when implemented

Supported map/image inputs should include PNG, JPEG/JPG, TIFF/GeoTIFF, BMP,
WebP, and GIF.

### Mission Planner

Purpose: plan the operational mission and build the runtime bundle.

Must support:

- manual Takeoff, Waypoint, and Land placement
- Mission, GeoFence, Rally, and Vision Map planning layers
- map-backed planning without auto-loading large maps on first entry
- mission item reorder/delete/edit
- QGroundControl-style `.plan` import/export compatibility
- bundle build and validation
- bundle upload to the active Raspberry Pi profile
- GNSS-denied readiness checks
- terrain planning constraints
- live position display when telemetry is available

Mission Planner should consume the selected Vision Pipeline defaults. It should
not duplicate editable feature/matcher controls.

### Vehicle Manager

Purpose: manage runtime devices and Raspberry Pi setup.

Must support:

- add/edit/delete runtime device profiles
- active device selection
- SSH host, port, username, and auth configuration
- Raspberry Pi discovery over local Wi-Fi
- project sync/install commands
- Pi dependency checks
- camera health checks
- MAVLink endpoint checks
- bundle upload and validation
- prop-off hardware bench workflow

### Camera & Vision

Purpose: single source of truth for vision pipeline configuration.

Must support:

- classical CPU pipeline
- ORB / AKAZE / SIFT feature method options
- optional neural SuperPoint / LightGlue mode
- max features
- matcher ratio
- minimum matches
- model weight paths

This page is the only editable vision configuration surface. Other pages may
show summaries but should not create duplicate controls.

### Diagnostics

Purpose: live readiness and position telemetry.

Must support:

- active device readiness
- downloaded map readiness
- selected vision pipeline summary
- UDP position telemetry listener
- GPS versus terrain-vision source display
- confidence and covariance diagnostics
- source fallback status

Default position telemetry port: `17660`.

### Flight Review

Purpose: review evidence after bench or field runs.

Must support:

- downloaded support bundle list
- support bundle health summary
- evidence/report summaries
- local reveal/delete/extract actions where available
- storage and pass/fail counts

### Settings

Purpose: app-level configuration.

Must support:

- imagery API keys
- app preferences
- repo/path defaults
- storage and download locations
- YAML config viewing/editing when available

## Data Pipeline Requirements

The app must stay wired to the current project data pipeline:

```text
Map Library
  -> selected map source
  -> Mission Planner
  -> mission bundle build
  -> terrain tile index and feature map
  -> Raspberry Pi upload
  -> onboard terrain vision runtime
  -> GPS / vision position telemetry
  -> Diagnostics and Mission Planner live position
  -> Flight Review support bundles
```

The desktop app should continue to use the Tauri command wrapper in
`src/lib/tauri.ts`. Browser preview may use safe local fallback data, but the
packaged Tauri app must use real backend commands.

## Runtime Position Requirement

The drone position should be sent from the runtime module to the ground station.

Source priority:

1. Use healthy GPS when available.
2. Use GNSS-denied terrain-vision position when GPS is unavailable, weak,
   spoofed, jammed, or degraded.
3. Use degraded GPS only if terrain vision is unavailable.

The UI must make the active source obvious and show confidence/health state.

## Visual Requirements

The app should feel like a professional ground-control operations console:

- dark cockpit-like UI
- dense but readable operational layout
- rectangular controls
- visible status LEDs/badges
- cyan active navigation
- green/amber/red health semantics
- monospaced telemetry values
- no marketing-style hero pages
- no static placeholder panes where a working workflow exists

The UI should be optimized for repeat use by an operator, not for a landing-page
presentation.

## Nonfunctional Requirements

- First entry into Mission Planner must feel fast.
- Large map mosaics should load only after user selection.
- Browser/dev preview should not crash when Tauri APIs are unavailable.
- Tauri runtime actions should be lazy or guarded.
- The app should build with `npm run build`.
- The Rust backend should pass `cargo check` and `cargo test`.
- The project preflight should pass with `./scripts/dev/local_preflight.sh`.
- Static Stitch design changes must not break route navigation or the data
  pipeline.

## Stitch Import Guidance

Use the included files as follows:

- `desktop-app-source/`: implementation source of truth for panes, routing,
  components, styles, and backend command contracts
- `desktop-app-static-build/`: static built output for quick visual inspection
- `README.md`: this requirements document

When redesigning in Stitch:

- keep the current navigation hierarchy
- preserve all active workflows
- make controls interactive, not just decorative
- keep Camera & Vision as the only editable pipeline configuration page
- keep Mission Planner focused on mission, map, bundle, and live position
- keep Vehicle Manager focused on device and Raspberry Pi setup
- keep Diagnostics focused on system and telemetry health
- keep Flight Review focused on support bundle history

## Current Verification Snapshot

The current app has been checked with:

```bash
npm run build
cd src-tauri && cargo check && cargo test
./scripts/dev/local_preflight.sh
```

The production preview rendered the native shell and the core routes without
iframes.
