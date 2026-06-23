# Desktop App

The desktop app in `desktop-app/` is the ground-control / mission-planner
surface for the project. It is a Tauri + React app.

The app is not a ROS 2 or simulator control surface. Its active purpose is to
prepare maps and missions, configure the vision pipeline, connect to the
Raspberry Pi, run hardware checks, and review support bundles from real bench
or field runs.

## Active Pages

- Dashboard: quick entry points.
- Maps: select, draw, import, or upload map sources.
- Vision Pipeline: the only editable feature/matcher configuration page.
- Devices / Module Setup: connect to Raspberry Pi over local Wi-Fi/SSH and run
  hardware checks.
- Mission Planner: plan mission, geofence, rally, and vision-map checkpoints.
- Settings: app-level preferences.

## Module Setup

The Devices page contains the customer-facing module setup flow for a Raspberry
Pi runtime computer on the same Wi-Fi network as the desktop app.

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

Devices and Mission Planner may summarize these settings, but they do not edit
them.

## Maps

The Maps page can create runtime map sources by:

- drawing an area and downloading map tiles
- importing an existing folder containing `satellite.png` and `metadata.json`
- uploading a map/image file

Supported uploads include PNG, JPEG/JPG, TIFF/GeoTIFF, BMP, WebP, and GIF.
GeoTIFF uploads can derive georeference metadata when the CRS and tags are
supported. Non-georeferenced images need manual origin and GSD values.

Optional DEM/DSM GeoTIFFs can be attached to a saved map source and carried
into the mission bundle for terrain-profile checks.

## Mission Planner

Mission Planner is the ground-control style planning workspace.

It has four operator layers:

- Mission: takeoff, waypoint, and land items.
- GeoFence: optional polygon safety boundary.
- Rally: optional emergency rally points.
- Vision Map: localization checkpoints for map-matching coverage review.

Mission Planner opens without auto-selecting a saved map source, so large local
mosaics do not block the first tab render. A saved `satellite.png` mosaic loads
only after the user selects a map source.

Mission Planner exports:

- app mission JSON
- QGroundControl-style `.plan`
- `mission/mission_plan.json`
- `mission/qgc.plan`
- GNSS-denied prep metadata
- terrain planning constraints
- selected Vision Pipeline defaults

The bundle action builds the selected map source, terrain tile index,
STAC-style manifest, bundle health report, runtime config, and checksums.

## Hardware Bench App Flow

For the Holybro X500 V2 prop-off milestone:

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
