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
2. Open Devices and use `Local Wi-Fi Discovery` to scan saved hostnames,
   Raspberry Pi mDNS names, and local SSH neighbors.
3. Add or select the discovered module, then expand it and choose
   `Module setup`.
4. Add or confirm the module hostname or IP, username, and SSH authentication.
5. Run `Test Wi-Fi SSH` and save the module as the active device.
6. Open the expanded device menu to adjust runtime paths, MAVLink, and flight
   controller settings only after connection exists.
7. Run `Install Module` to sync runtime files and execute the module bootstrap.
8. Run setup and vision checks from the app:
   - Wi-Fi SSH identity
   - project file check
   - module dependency bootstrap
   - system verification
   - camera view test
   - camera health
   - time sync
   - MAVLink endpoint access
   - optional Micro XRCE-DDS Agent readiness for PX4 ROS 2 paths
   - calibration image capture
   - synthetic vision smoke test
   - deployed runtime bundle validation
   - field replay-case registration for terrain evidence gates
   - bench-report support bundle creation and desktop download
9. Save a local setup report from the collected checks when you need an audit
   trail for a bench run or customer install.

The project sync command intentionally excludes desktop-only and generated
folders such as `.git`, `desktop-app`, `node_modules`, `target`, `data`, `logs`,
and `map_bundles`. Bootstrap uses the existing `scripts/pi/bootstrap_pi5.sh`
script on the module and may require a reboot afterward.

The setup report is exported as JSON and excludes SSH passwords, key
passphrases, and sudo passwords. It includes device connection metadata,
runtime paths, step status, command output, camera-preview path, and the most
recent downloaded support-bundle summaries. Discovery results are saved in the
desktop app so recent local-network candidates remain visible even after a Pi
reboots or temporarily drops offline. Discovery also shows active desktop
IPv4 interface/subnet hints, lets the operator select the adapter that should
be on the Pi network, and provides a copyable mDNS/SSH/firewall checklist. The
selected adapter and checklist are included in the setup report, which helps
diagnose whether the Pi and desktop are on the same local network after a
failed bench install.

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
devices show a bundle-ready state instead of upload status. Build/upload
fingerprints and timestamps are saved locally, so the Mission Planner can show
the previous bundle state after the app restarts.

Mission plans can be imported from the app's JSON format or QGroundControl-style
`.plan` files. Export writes a `.plan` file with QGC mission, geofence, rally,
and `visionNavigation` metadata for this project. The Mission state panel also
shows whether the active imported/exported plan file has unsaved local changes.

Mission Planner also includes a GNSS-denied readiness block. The operator can
record that satellite-source assumptions are disabled, set map-position and
home resets from the selected mission item, set or derive heading, and mark
estimator health. Those values are exported in the app mission JSON and in the
QGroundControl `.plan` file under `visionNavigation.gnss_denied`.

Mission Planner also records terrain planning constraints before bundle build.
The operator can confirm the offline map-cache path, set minimum AGL, maximum
terrain relief, minimum AGL-to-GSD ratio, and maximum route-segment length. The
same metadata is exported in the app mission JSON and in the QGroundControl
`.plan` file under `visionNavigation.terrain_planning`. The planner also
generates deterministic route-segment records with split coordinates,
cumulative distance, longest segment length, and split reason so long
terrain-aware routes can be reviewed in the bundle without changing the
underlying flight-controller mission items. After a bundle build, the app
compares terrain limits with `bundle_health.json` terrain-profile values.

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
the configured MAVLink endpoint, optional replay-gate reports, optional PX4
SITL receiver evidence and parameter checks, optional ArduPilot parameter
checks, optional feature-method benchmarks, optional field-evidence gates, and
optional threshold-tuning reports, and an automatic bench-readiness summary. The
panel lists recent downloaded support bundle ZIPs with parsed bench-readiness
status, bundle health, checksum status, map source provenance, georeference
confidence, replay-gate status, PX4 evidence status, PX4 parameter status,
ArduPilot parameter status, feature-method benchmark status, field-evidence
status, and threshold-tuning status so the operator can confirm what was
captured without manually opening the archive. Feature-method benchmark reports
from `$HOME/DroneTransfer/outgoing/feature-method-bench`, field-evidence reports
from `$HOME/DroneTransfer/outgoing/replay-cases/field_evidence_report.json`, and
threshold-tuning reports from
`$HOME/DroneTransfer/outgoing/replay-cases/threshold_tuning_report.json` are also
packaged automatically when present. The list can
reveal a ZIP in the local file manager, copy the full path for support notes,
show a compact detail view, or delete stale ZIP files after a bench session. The
detail view reads the ZIP archive directly and shows support metadata,
git/app state, log status counts, accepted-rate summaries, bench-readiness
checks, replay-gate case results, PX4 receiver sample counts, MAVLink
version/link hints, PX4 external-vision parameter readiness, ArduPilot
ExternalNav parameter readiness, feature-method benchmark recommendations,
field-evidence case coverage, per-condition coverage status, threshold-tuning
margins, and compact per-record previews from bundled runtime/replay JSONL logs. It also
previews a bounded set of small image artifacts from camera, debug, replay,
smoke, or extra-file paths while skipping full map, orthophoto, and tile
assets.

Desktop-created support bundles automatically pass conventional Pi evidence
locations into `scripts/pi/create_support_bundle.sh`:
`$HOME/px4-sitl-evidence`, `$HOME/px4.params`, `$HOME/ardupilot.params`,
`$HOME/DroneTransfer/outgoing/feature-method-bench`, and
`$HOME/DroneTransfer/outgoing/replay-cases/field_evidence_report.json`.
Missing files are ignored by the Pi wrapper; present files are packaged and
counted in the bench-readiness report.

Module Setup uses the same support-bundle path for its `Bench Report` action,
after validating the deployed terrain bundle at the configured runtime bundle
path.

Module Setup can also register the latest Pi terrain runtime log as a field
evidence case. The operator selects expected behavior, condition tags, notes,
and whether to replace an existing case. The app runs
`scripts/pi/register_field_replay_case.sh` over SSH, which updates the Pi-side
field replay manifest and writes the field-evidence report that the next
support bundle will include automatically.

Module Setup can run `Threshold Tuning` after enough field cases are registered.
The action runs `scripts/pi/run_threshold_tuning_report.sh` over SSH, writes the
threshold report under the Pi replay-cases folder, and downloads it to
`~/DroneTransfer/from-pi/replay-cases/` on the desktop.

After Mission Planner builds and uploads a bundle to a Raspberry Pi device, the
`Open Bench Report In Module Setup` action opens that device's setup tab with
the uploaded bundle path already handed off. From there, `Create Bench Report`
validates the deployed terrain bundle, creates the support bundle on the Pi, and
downloads it to the desktop. The following `Autonomy Readiness` setup action
runs `scripts/pi/run_autonomy_readiness_audit.sh` over SSH against the latest
Pi-side support bundle, then downloads the strict final audit report to
`~/DroneTransfer/from-pi/replay-cases/` on the desktop.

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

ArduPilot device selection is kept as an adapter-readiness path, not the
default runtime output. PX4 remains the bench target until receiver evidence is
repeatable. The ArduPilot design and parameter audit workflow live in
[ArduPilot ExternalNav Adapter Design](ardupilot-externalnav-adapter.md).

The Devices Control tab mirrors the same runtime actions for a selected Pi:
status, short terrain loop, stop loop, view logs, create support bundle, and
service status. The support-bundle action also downloads the generated zip to
the desktop transfer folder and shows recent downloaded bundles with the same
parsed health summary.
