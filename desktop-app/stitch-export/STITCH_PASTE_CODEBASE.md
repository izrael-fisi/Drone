# Paste This Into Stitch: Drone Vision GCS Codebase Context

You are designing UI for an existing Tauri + React + TypeScript desktop app.
Do not make a marketing page. Do not make static decorative panes. Preserve the
working app as a dense, cockpit-style ground control station for GNSS-denied
drone navigation.

## Product

App name: Drone Vision GCS

Purpose: ground control, mission planning, map preparation, Raspberry Pi runtime
setup, and live position monitoring for a drone that primarily uses GPS when it
is healthy and falls back to onboard terrain-vision navigation when GPS is weak,
jammed, unavailable, or untrusted.

Hardware target:

- Raspberry Pi 5 runtime computer
- Pixhawk / PX4 flight controller
- Holybro X500 V2 aircraft target
- Downward camera for terrain feature matching
- Optional telemetry radio / MAVLink endpoint

Core navigation method:

- Preloaded georeferenced maps
- Terrain tile index
- ORB / AKAZE feature matching for Raspberry Pi compute
- Optional SuperPoint / LightGlue for higher compute devices
- IMU / PX4 local state support where available
- MAVLink position output and UDP telemetry back to the desktop app

## Existing Technology

- Desktop shell: Tauri
- Frontend: React + TypeScript
- Styling: Tailwind CSS with custom operational utility classes
- Backend: Rust Tauri commands
- Drone runtime: Python modules under `src/vision_nav`
- App state: local store with Profile, Device, Region, and runtime status data
- Browser preview must work with local fallback data when Tauri APIs are absent

## Visual Direction

Use a tactical operations console style:

- Dark cockpit UI
- Dense operational layouts
- Rectangular controls, not pill-heavy consumer UI
- Cyan active states
- Green / amber / red health semantics
- Monospaced telemetry values
- Status LEDs and compact badges
- Map overlays and right-side control panels
- No hero sections
- No large marketing cards
- No static iframe panes
- Every page should look like an operator can use it repeatedly in the field

Design tokens already used:

```css
--ops-bg-base: #0F172A;
--ops-bg-surface: #1E293B;
--ops-bg-overlay: #334155;
--ops-border-subtle: #334155;
--ops-border-strong: #475569;
--ops-active: #00E5FF;
--ops-ready: #22C55E;
--ops-warning: #F59E0B;
--ops-critical: #EF4444;
--ops-offline: #64748B;
```

Reusable UI classes already exist:

```text
btn-primary
btn-secondary
btn-ghost
input-field
label
section-title
badge-cyan
badge-green
badge-yellow
badge-red
ops-panel
ops-tile
ops-label
ops-value
ops-led
ops-led-ready
ops-led-warning
ops-led-critical
ops-led-offline
ops-map-overlay
ops-console
```

## App Shell

The app has two persistent navigation areas:

1. Top mode bar
   - DRONE VISION GCS brand
   - mode shortcuts: MAP, SENSORS, FLEET, OPERATIONS
   - Wi-Fi / battery / active system status
   - ARM SYSTEM / SYSTEM ARMED button
   - desktop window controls

2. Left operations navigation
   - Ops Console
   - Map Library
   - Mission Planner
   - Bundle Builder
   - Terrain
   - Vehicle Manager
   - Camera Config
   - System Status
   - Flight Review
   - Settings

The shell also includes:

- Command palette/search field
- active device badge
- downloaded map count
- recording marker
- profile footer
- Logs and Sync shortcut buttons

## Routes

```text
/dashboard                 -> Ops Console
/navigation-panel          -> Ops Console alias
/maps                      -> Map Library
/mission-planner           -> Mission Planner
/mission-bundle-builder    -> Bundle Builder
/bundle-builder            -> Bundle Builder alias
/bundle                    -> Bundle Builder alias
/mission-bundle            -> Bundle Builder alias
/terrain                   -> Terrain Planning
/terrain-planning          -> Terrain Planning alias
/devices                   -> Vehicle Manager
/vehicle-manager           -> Vehicle Manager alias
/pi-setup                  -> Raspberry Pi setup area under Vehicle Manager
/module-setup              -> Raspberry Pi setup alias
/camera-vision             -> Camera & Vision Config
/vision-pipeline           -> Camera & Vision Config alias
/system-status             -> System Status & Diagnostics
/diagnostics               -> System Status alias
/flight-review             -> Flight Review & History
/history                   -> Flight Review alias
/settings                  -> Settings
```

Do not remove aliases. Operator language varies.

## Core Data Types

```ts
type DeviceKind = "pi5" | "local";
type VisionPipeline = "classical" | "neural";
type FeatureMethod = "orb" | "akaze" | "sift";

interface Profile {
  name: string;
  email: string;
  org: string;
  accent_color: string;
  onboarding_complete: boolean;
  mapbox_key?: string;
  bing_key?: string;
}

interface Device {
  id: string;
  name: string;
  kind: DeviceKind;
  host?: string;
  port?: number;
  username?: string;
  auth?: { type: "Password"; password: string } | { type: "Key"; key_path: string; passphrase?: string };
  remote_project_path?: string;
  known_fingerprint?: string;
  mavlink_endpoint?: string;
  autopilot?: "px4" | "ardupilot";
}

interface Region {
  id: string;
  name: string;
  lat_min: number;
  lat_max: number;
  lon_min: number;
  lon_max: number;
  zoom: number;
  source?: "esri" | "mapbox" | "bing" | "uploaded" | "folder";
  output_path: string;
  last_downloaded?: string;
  tile_count?: number;
  gsd_m_per_px?: number;
  georef_source?: string;
  georef_confidence?: number;
  georef_crs?: string;
  file_size_mb?: number;
  location_label?: string;
  elevation_dem_path?: string;
  elevation_dsm_path?: string;
}

interface DronePositionUpdate {
  schema_version: "vision_nav_position_update_v1";
  timestamp_utc?: string;
  sequence?: number;
  status?: "accepted" | "degraded" | "unavailable" | string;
  source?: "gps" | "vision" | "gps_degraded" | "none" | string;
  lat_lon?: { lat?: number | null; lon?: number | null };
  altitude_m?: number | null;
  local_enu_m?: { x?: number | null; y?: number | null; z?: number | null };
  confidence?: number | null;
  covariance?: Record<string, number | null>;
  gps_health?: {
    healthy?: boolean;
    reason?: string;
    fix_type?: number | null;
    satellites_visible?: number | null;
    h_acc_m?: number | null;
  };
  vision_health?: {
    available?: boolean;
    status?: string;
    confidence?: number | null;
    tile_id?: string | null;
    inliers?: number | null;
    reprojection_error_px?: number | null;
  };
}

interface BuildDroneBundleRequest {
  region_dir: string;
  output_dir: string;
  repo_path: string;
  pipeline: VisionPipeline;
  feature_method: FeatureMethod;
  max_features: number;
  mission_plan_json?: string;
  qgc_plan_json?: string;
}
```

## Data Pipeline

Preserve this pipeline in the UI:

```text
Map Library
  -> selected map source
  -> Mission Planner
  -> mission plan with takeoff, waypoint, land, fence, rally, and vision checkpoints
  -> Mission Bundle Builder / Mission Planner bundle action
  -> terrain tile index + feature map + STAC manifest
  -> Raspberry Pi upload
  -> onboard terrain vision runtime
  -> GPS or vision position telemetry
  -> Mission Planner live aircraft marker
  -> System Status diagnostics
  -> Flight Review support bundle history
```

Source priority for position display:

1. Healthy GPS
2. Terrain vision if GPS is missing, weak, jammed, spoofed, or degraded
3. Degraded GPS only if vision is unavailable

The active source must be visually obvious.

## Page Requirements

### Ops Console

Operator home surface. Show readiness without duplicating every workflow.

Must include:

- active device
- active map / downloaded maps
- latest mission bundle status
- GPS / vision telemetry summary
- quick actions for Map Library, Mission Planner, Vehicle Manager, Camera Config, System Status, Flight Review
- readiness checklist: map, camera, Pi, MAVLink, bundle, telemetry

### Map Library

Map preparation page for GNSS-denied navigation.

Must include:

- map source list
- draw/download area workflow
- local folder import
- uploaded image / GeoTIFF import
- supported formats: PNG, JPG/JPEG, TIFF/GeoTIFF, BMP, WebP, GIF
- georef confidence
- CRS/GSD/zoom/tile count
- DEM/DSM attachment fields
- map lifecycle state: local, built, uploaded, active, stale, failed

### Mission Planner

Primary planning surface.

Must include:

- large interactive map canvas
- no auto-loading of a huge mosaic on first page entry
- user selects map source before loading a mosaic
- layer tabs: Mission, GeoFence, Rally, Vision Map
- Takeoff, Waypoint, Land placement buttons
- clicking Takeoff sets takeoff point, then automatically switches to Waypoint placement
- clicking Waypoint lets user add route waypoints
- clicking Land sets the landing point
- map clicks must not reset map pan/zoom
- mission item list with reorder/delete
- selected item editor
- `.plan` import/export
- GNSS-denied readiness
- terrain constraints summary
- live aircraft marker when telemetry exists
- build bundle and upload bundle actions

Do not duplicate editable vision algorithm controls here. Show only a compact
read-only summary that links to Camera Config.

### Bundle Builder

Dedicated map-to-runtime packaging page.

Must include:

- map source selector
- runtime target summary
- repo path
- output folder
- build bundle button
- selected Vision Pipeline summary
- GSD, tile count, feature count
- STAC manifest status
- tile index status
- checksum / geospatial health
- estimated Raspberry Pi runtime cost
- console/log output

This page calls the same backend bundle build pipeline as Mission Planner.

### Terrain Planning

Operational terrain risk and route constraints page.

Must include:

- selected map source
- min AGL
- max terrain relief
- min AGL/GSD ratio
- max route segment length
- GSD readout
- AGL/GSD readout
- georef confidence
- route segmentation preview
- terrain profile/risk visualization
- saved terrain defaults shared with Mission Planner

### Vehicle Manager

Runtime device and Raspberry Pi management.

Must include:

- device profile list
- add/edit/delete profile
- active device selection
- Raspberry Pi Wi-Fi discovery
- SSH host, port, username, auth status
- project sync/install
- dependency checks
- camera health check
- MAVLink endpoint check
- Pixhawk/PX4 profile fields
- prop-off Holybro X500 V2 bench flow
- bundle upload/validate controls

### Camera Config

Single source of truth for vision algorithm settings.

Must include:

- pipeline mode: classical / neural
- feature method: ORB / AKAZE / SIFT
- max features
- matcher ratio
- minimum matches
- SuperPoint weight path
- LightGlue weight path
- save defaults button

Other pages can summarize this config but must not edit it.

### System Status

Live diagnostics and telemetry page.

Must include:

- active device health
- map readiness
- camera/vision readiness
- MAVLink readiness
- UDP telemetry listener state
- default UDP port 17660
- latest GPS/vision position packet
- active source
- confidence
- covariance
- GPS health reason
- vision tile/inliers/reprojection error
- packet age and update rate

### Flight Review

Evidence and history page.

Must include:

- support bundle list
- latest bench/field reports
- pass/degraded/failed counts
- storage estimate
- support bundle detail panel
- report extraction/reveal actions
- evidence summaries for camera, MAVLink, telemetry, runtime config, and replay gates

### Settings

General configuration.

Must include:

- imagery API keys
- app preferences
- repo path defaults
- storage/download locations
- telemetry port defaults
- data retention/log cleanup
- security/audit controls

## Interaction Rules

- Pages must route normally through React navigation.
- Do not put UI cards inside other UI cards.
- Keep controls rectangular.
- Use icons in buttons where possible.
- Keep map overlays compact and readable.
- Do not use huge hero text.
- Do not create duplicate feature pipeline selectors outside Camera Config.
- Keep Mission Planner fast on first open.
- Load large mosaics only after explicit map selection.
- Keep browser preview safe when Tauri APIs are unavailable.
- Preserve real backend command contracts.

## Backend Commands Used By UI

The frontend talks to Tauri through `cmd` wrappers. Design should preserve these
workflow names:

```ts
cmd.loadProfile()
cmd.saveProfile(profile)
cmd.loadDevices()
cmd.saveDevices(devices)
cmd.loadRegions()
cmd.saveRegions(regions)
cmd.estimateTiles(bbox, zoom)
cmd.downloadTiles(bbox, zoom, outputDir, source, apiKey)
cmd.importMapFile(request)
cmd.importElevationAssets(request)
cmd.buildDroneBundle(request)
cmd.discoverPiDevices(seedHosts, port)
cmd.localNetworkHints()
cmd.testSshConnection(host, port, username, auth)
cmd.sshRunCommand(host, port, username, auth, command)
cmd.sshUploadProject(host, port, username, auth, localDir, remoteDir)
cmd.sshUploadDirectory(host, port, username, auth, localDir, remoteDir)
cmd.sshDownloadFile(host, port, username, auth, remotePath, localDir)
cmd.sshCaptureCameraFrame(host, port, username, auth, remoteProjectPath, width, height, timeoutMs)
cmd.receivePositionUpdate(port)
cmd.listSupportBundles()
cmd.readSupportBundleDetails(path)
```

## Desired Stitch Output

Create or refine UI screens that fit this codebase:

1. App shell with top mode bar and left operations nav
2. Ops Console
3. Map Library
4. Mission Planner
5. Bundle Builder
6. Terrain Planning
7. Vehicle Manager
8. Camera Config
9. System Status
10. Flight Review
11. Settings

For each screen, provide:

- complete visible layout
- realistic empty/ready/degraded states
- controls that correspond to existing workflows
- no placeholder-only panels
- no removal of required actions
- responsive behavior for desktop and smaller laptop widths

The final design must remain implementable in the current React/Tauri app.
