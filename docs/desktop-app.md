# Desktop App

The desktop app in `desktop-app/` is the ground-control / mission-planner
surface for the project. It is a Tauri + React app.

## UI Direction

The Stitch panes are design references, not authoritative implementation
constraints. The active desktop app now prioritizes fully usable native React
pages wired to the real Tauri/Python data pipeline.

Pane-style routes remain as aliases so operator terminology still works:
Navigation Panel maps to the dashboard, Vehicle Manager maps to Devices,
Camera & Vision maps to Vision Pipeline, Mission Bundle Builder maps to the
Mission Planner bundle workflow, System Status maps to Diagnostics, and Flight
Review maps to support-bundle review.

The app is not a ROS 2 or simulator control surface. Its active purpose is to
prepare maps and missions, configure the vision pipeline, connect to the
Raspberry Pi, run hardware checks, and review support bundles from real bench
or field runs.

## Docker Runtime

If the Windows host does not have Node/npm/pnpm on `PATH`, run the React
operator UI through Docker Desktop:

```powershell
winget install Docker.DockerDesktop
cd C:\Users\gmod4\OneDrive\Documents\DRONE\DroneRepo
.\scripts\dev\desktop_docker.ps1 dev
```

Open `http://localhost:5173`.

Useful actions:

```powershell
.\scripts\dev\desktop_docker.ps1 build
.\scripts\dev\desktop_docker.ps1 preview
.\scripts\dev\desktop_docker.ps1 shell
.\scripts\dev\desktop_docker.ps1 down
.\scripts\dev\desktop_docker.ps1 clean
```

This containerized runtime builds and serves the frontend and uses the browser
fallback paths in `src/lib/tauri.ts`, including Edge API calls to the companion
computer. It does not launch or package the native Tauri desktop window. Native
Tauri still needs the Windows Rust/MSVC/WebView2 toolchain on the host.

## Active Pages

- `/dashboard` and `/navigation-panel`: operator home, readiness, quick actions,
  saved regions.
- `/maps`: draw/download map areas, import folders, import uploaded imagery and
  GeoTIFFs, attach DEM/DSM assets.
- `/mission-planner`: map-backed mission planning, live position marker,
  GNSS-denied readiness, terrain constraints, bundle build/upload/validation,
  runtime commands, and support bundles.
- `/mission-bundle-builder`: alias into the Mission Planner bundle workflow.
- `/devices` and `/vehicle-manager`: device profiles, Raspberry Pi SSH,
  runtime module setup, camera checks, MAVLink checks, bench/field workflows.
- `/pi-setup` and `/module-setup`: deep Raspberry Pi setup workflow.
- `/camera-vision` and `/vision-pipeline`: editable vision feature/matcher
  defaults used by mission bundle builds.
- `/system-status`: diagnostics, readiness summary, and live GPS/vision position
  telemetry listener.
- `/flight-review`: downloaded support bundle and field/bench evidence review.
- `/settings`: app-level paths, keys, and preferences.

## Operations UI Direction

The desktop app uses streamlined ground-control patterns: page-aware title
context, global command search, active-device status, recording readiness, map
cursor readout, recentering, live position source, and visible diagnostics.

## Module Setup

The Devices and Module Setup pages contain the customer-facing setup flow for a
Raspberry Pi runtime computer on the same Wi-Fi network as the desktop app.

The hardware-first setup flow is:

1. Connect desktop and Raspberry Pi to the same Wi-Fi network.
2. Discover or add the Pi host.
3. Test Wi-Fi SSH.
4. Install/sync the project onto the Pi.
5. Run Pi setup checks.
6. Run camera health and synthetic vision smoke checks.
7. Validate the deployed terrain bundle.
8. Check MAVLink endpoint access.
9. Run a short prop-off terrain runtime capture.
10. Download runtime status and support bundle.

The setup report excludes passwords and records device metadata, commands,
step status, output snippets, camera preview paths, support-bundle summaries,
and hardware bench evidence.

## Vision Pipeline

The Camera & Vision / Vision Pipeline page is the editable feature/matcher
configuration surface.

The default mode is `classical`.

```text
saved map source
  -> mission_bundle/ortho/map.png
  -> ORB or AKAZE feature/tile index
  -> Raspberry Pi runtime
```

The optional `neural` mode stores SuperPoint/LightGlue-style metadata for later
higher-compute devices. The Raspberry Pi-safe classical feature index remains
the default.

The Vision Pipeline page stores:

- pipeline mode
- feature method
- max features
- matcher ratio
- minimum matches
- optional neural weight paths

Other pages may summarize these settings, but they should not create duplicate
editable pipeline controls outside the Camera & Vision page.

## Maps

The Maps page can create runtime map sources by:

- drawing an area and downloading map tiles
- importing an existing folder containing `satellite.png` and `metadata.json`
- uploading a map/image file

Supported uploads include PNG, JPEG/JPG, TIFF/GeoTIFF, BMP, WebP, and GIF.
GeoTIFF uploads can derive georeference metadata when the CRS and tags are
supported. Non-georeferenced images need manual origin and GSD values.

These map records feed the Mission Planner and bundle build pipeline.

## Mission Planner

Mission Planner is the ground-control style planning workspace.

It has four operator layers:

- Mission: takeoff, waypoint, and land items.
- GeoFence: optional polygon safety boundary.
- Rally: optional emergency rally points.
- Vision Map: localization checkpoints for map-matching coverage review.

The active Mission Planner route renders the native planning workspace.
`/mission-bundle-builder` is an alias for the same workflow because bundle
building depends on the selected mission, map, device, and vision settings.

Mission Planner exports:

- app mission JSON
- QGroundControl-style `.plan`
- `mission/mission_plan.json`
- `mission/qgc.plan`
- GNSS-denied prep metadata
- terrain planning constraints
- selected Vision Pipeline defaults

The bundle action builds the selected map source, terrain tile index,
STAC-style manifest, bundle health report, runtime config, and checksums. When
the active device is a Raspberry Pi profile, the app can upload and validate the
bundle over SSH.

## Hardware Bench App Flow

The Holybro X500 V2 prop-off workflow is supported through Devices / Module
Setup, Mission Planner, and Flight Review:

1. Select/import the map source.
2. Set Vision Pipeline defaults.
3. Build and validate the terrain bundle.
4. Upload the bundle to the Raspberry Pi.
5. Connect to the Pi from Devices.
6. Run camera checks.
7. Run MAVLink endpoint check.
8. Run Field Log Capture with props removed.
9. Run Bench Report.
10. Review/download the support bundle.

## Live Drone Position

The runtime code can emit `vision_nav_position_update_v1` UDP packets from the
Raspberry Pi. System Status listens for those packets, and Mission Planner can
display the current GPS/vision position source on the map.

The runtime source priority is:

1. Healthy MAVLink GPS: fix type at least 3, enough satellites, and acceptable
   reported horizontal accuracy.
2. Terrain vision position from the selected mission bundle when GPS is missing,
   weak, or likely jammed.
3. Dead reckoning between accepted terrain-vision fixes when no current GPS or
   vision fix is available.
4. Degraded GPS only when no valid terrain-vision position is available.

The default ground-station listener port is `17660`. Mission Planner passes
`VISION_NAV_POSITION_UDP_TARGET=255.255.255.255:<port>` into the Pi runtime
when launching the terrain loop from the app. The app accepts both legacy
`vision_nav_position_update_v1` packets and the v2 packets emitted by the
status bridge and terrain loop.

## Local Development

```bash
cd desktop-app
npm ci
npm run build
cd src-tauri
cargo check
cargo test
```

If needed, set `DRONE_DESKTOP_PYTHON` to the Python interpreter that has this
repo's dependencies installed.
