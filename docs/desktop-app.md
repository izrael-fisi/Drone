# Desktop App

The desktop app in `desktop-app/` is a Tauri + React operator tool adapted from
the Macula desktop workflow for this Drone GNSS-denied navigation repo.

It is used for:

- guided module setup over local Wi-Fi/SSH from the Devices page
- selecting or importing satellite map regions
- importing your own map/image files
- building and uploading a Drone `mission_bundle`
- configuring the low-compute classical feature path or high-compute neural path
- running module validation, camera view, and short runtime checks over SSH
- enabling MAVLink output for accepted vision measurements when ready

## Module Setup

The Devices page contains the customer-facing `Module setup` flow for a
Raspberry Pi runtime computer on the same Wi-Fi network as the desktop app.

The flow is:

1. Connect the desktop computer and Raspberry Pi to the same Wi-Fi network.
2. Open Devices, expand the module, then choose `Module setup`.
3. Add the module hostname or IP, username, and SSH authentication.
4. Run `Test Wi-Fi SSH` and save the module as the active device.
5. Open the expanded device menu to adjust runtime paths, MAVLink, and flight
   controller settings only after connection exists.
6. Run `Install Module` to sync runtime files and execute the module bootstrap.
7. Run setup and vision checks from the app:
   - Wi-Fi SSH identity
   - project file check
   - module dependency bootstrap
   - system verification
   - camera view test
   - camera health
   - time sync
   - MAVLink endpoint access
   - calibration image capture
   - synthetic vision smoke test
   - deployed runtime bundle validation

The project sync command intentionally excludes desktop-only and generated
folders such as `.git`, `desktop-app`, `node_modules`, `target`, `data`, `logs`,
and `map_bundles`. Bootstrap uses the existing `scripts/pi/bootstrap_pi5.sh`
script on the module and may require a reboot afterward.

## Vision Pipeline

The default mode is `classical`.

```text
satellite region
  -> mission_bundle/ortho/map.png
  -> ORB or AKAZE feature index
  -> Raspberry Pi 5 runtime
```

The optional `neural` mode keeps SuperPoint + LightGlue metadata and region files
inside the bundle for higher-compute devices. The Raspberry Pi-safe classical
feature index is still built as a fallback.

The Vision Pipeline page stores the default pipeline, feature method, feature
count, match thresholds, and neural weight paths used by new mission bundle
builds. Devices and Mission Planner show or consume these values, but the Vision
Pipeline page is the only editable configuration surface for matching defaults.

## Map Sources

The Maps page can create runtime map sources in three ways:

- draw an area and download satellite tiles
- import an existing folder containing `satellite.png` and `metadata.json`
- upload a map/image file and convert it into the same normalized folder shape

Uploaded map files are converted to:

```text
uploaded_map_source/
  satellite.png
  metadata.json
```

Supported upload formats include PNG, JPEG/JPG, TIFF/GeoTIFF image files, BMP,
WebP, and GIF. GeoTIFF uploads automatically read standard embedded
georeferencing when the source CRS is one of:

- EPSG:4326 or another geographic lon/lat CRS stored in GeoTIFF keys
- EPSG:3857 Web Mercator
- WGS84 UTM, EPSG:32601 through EPSG:32660 or EPSG:32701 through EPSG:32760

For those GeoTIFFs, the app derives the runtime origin latitude/longitude,
origin pixel, GSD, rotation, CRS label, and georeference confidence. Manual
origin/GSD fields remain available and override embedded metadata. Non-GeoTIFF
images still need manual origin latitude, longitude, and GSD.

The normalized `metadata.json` includes `georef_source`,
`georef_confidence`, and `georef_crs`. These fields are copied into the mission
bundle so the Pi runtime can combine map georeference quality with visual match
quality when it estimates measurement covariance.

Terrain bundles also declare optional barometer support. The app does not
require that telemetry, but the runtime can use PX4 MAVLink altitude/pressure
messages to fill relative vertical fields and vertical covariance.

## Local Setup

```bash
cd desktop-app
npm ci
npm run build
```

To run or package the Tauri shell, install Rust/Cargo first:

```bash
cd desktop-app
npm run tauri dev
```

The bundle builder command uses the local Drone repo path selected in the app.
If needed, set `DRONE_DESKTOP_PYTHON` to the Python interpreter that has this
repo's dependencies installed.

## Mission Planner

The Mission Planner tab is the ground-control style workspace. The user selects
a flight area/map source and the interactive planner map displays that saved
source's local `satellite.png` mosaic.

The planner is organized into four operator layers:

- `Mission`: takeoff, waypoint, and land items.
- `GeoFence`: an optional polygon safety boundary.
- `Rally`: optional emergency rally points.
- `Vision Map`: localization checkpoints used to reason about GNSS-denied
  feature-map coverage.

Mission Planner opens without auto-selecting a saved map source, so large local
mosaics do not block the first tab render. The saved `satellite.png` mosaic is
loaded only after the user selects a map source. The stats panel reports mission
item count, distance, estimated time, map area, and readiness checks for map
quality, mission path, fence shape, and MAVLink endpoint.

The plan editor also tracks mission state during the app session. It marks the
plan as invalid when required inputs are missing, not built before a bundle has
been created, stale when the map/mission/output settings change after a build,
not uploaded when the current bundle exists only locally, and uploaded when the
current plan fingerprint has been sent to the active Raspberry Pi. Local-only
devices show a bundle-ready state instead of upload status.

Mission plans can be imported from the app's JSON format or QGroundControl-style
`.plan` files. Export writes a `.plan` file with QGC mission, geofence, rally,
and `visionNavigation` metadata for this project.

The mission bundle action builds the selected map source, writes the desktop
mission JSON to `mission/mission_plan.json`, writes the QGC-style file to
`mission/qgc.plan`, records both in `manifest.json`, and uploads the bundle to
the runtime compute module. Feature extraction settings are read from the saved
Vision Pipeline defaults. It also builds the terrain tile index, STAC-style
manifest, `bundle_health.json`, and terrain runtime config. The Mission Planner
bundle result shows map health, tile count, feature count, and GSD before the
operator validates or runs the bundle on the Pi. It also shows a coarse Pi
runtime-cost estimate from tile count and feature density, plus checksum status,
covered file count, map source provenance, georeference source, CRS, and
georeference confidence. A compact map-quality heatmap previews feature density
per tile so low-texture areas are visible before the Pi uses the bundle. If
optional DEM/DSM elevation rasters are present in the selected bundle, the
result also shows whether elevation sanity checks are ready. When the bundle
contains a mission plan and sampleable DEM/DSM raster, it shows terrain-profile
status, estimated minimum AGL, terrain relief, and a compact terrain/flight
profile preview. By default this overwrites the active bundle at:

```text
/home/<pi-user>/drone-data/map_bundles/mission_bundle
```

That path is what `./scripts/pi/run_terrain_nav_loop.sh` loads through
`VISION_NAV_BUNDLE`, so the map selected in the desktop app becomes the active
map used for feature comparison on the Raspberry Pi.

The Maps page can attach optional DEM and DSM GeoTIFFs to a saved map source.
Those files are copied into the map folder under `elevation/`, referenced from
`metadata.json`, and carried into the next terrain mission bundle.
When GDAL Python bindings are available on the machine building the bundle, the
same health report also includes stricter TIFF/GeoTIFF driver, projection,
geotransform, overview, block-layout, and COG-readiness checks.

It then runs the existing Pi scripts:

```bash
./scripts/pi/validate_terrain_bundle.sh
./scripts/pi/run_terrain_nav_loop.sh
```

The Runtime And MAVLink panel can also create a support bundle on the connected
Raspberry Pi. Support bundles are written under
`~/DroneTransfer/outgoing/support-bundles/` on the Pi, then downloaded to
`~/DroneTransfer/from-pi/support-bundles/` on the desktop. They include active
map metadata, bundle health, runtime logs, generated summaries, app/git state,
and the configured MAVLink endpoint. The panel lists recent downloaded support
bundle ZIPs with parsed bundle health, checksum status, map source provenance,
georeference confidence, and replay-gate status so the operator can confirm what
was captured without manually opening the archive.

## MAVLink

MAVLink output is opt-in. When enabled in Mission Planner runtime controls, the
app sets:

```bash
VISION_NAV_MAVLINK_ENDPOINT=serial:/dev/ttyAMA0:921600
VISION_NAV_MAVLINK_MESSAGE=vision_position_estimate
VISION_NAV_EXTERNAL_POSITION_MIN_RATE_HZ=1.0
VISION_NAV_EXTERNAL_POSITION_MAX_LATENCY_MS=500.0
```

Accepted map matches are sent as MAVLink `VISION_POSITION_ESTIMATE` by default,
with local NED position derived from the repo's local ENU measurement. Set
`VISION_NAV_MAVLINK_MESSAGE=odometry` to bench the richer PX4 external-vision
`ODOMETRY` path. Rejected matches are logged but not sent. Runtime logs include
`external_position_health` snapshots with output status, send rate, latency,
skip reasons, and covariance warnings.

The Devices Control tab mirrors the same runtime actions for a selected Pi:
status, short terrain loop, stop loop, view logs, create support bundle, and
service status. The support-bundle action also downloads the generated zip to
the desktop transfer folder and shows recent downloaded bundles with the same
parsed health summary.
