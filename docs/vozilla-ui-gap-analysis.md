# Vozilla UI Gap Analysis

This note compares the Drone Vision desktop app with the Theseus Vozilla GCS documentation at:

- https://docs.theseus.us/gcs-software/vozilla
- https://docs.theseus.us/gcs-software/vozilla/quick-start.md
- https://docs.theseus.us/gcs-software/vozilla/navigation-panel.md
- https://docs.theseus.us/gcs-software/vozilla/maps.md
- https://docs.theseus.us/gcs-software/vozilla/maps/creating-a-new-map.md
- https://docs.theseus.us/gcs-software/vozilla/maps/selecting-a-map.md
- https://docs.theseus.us/gcs-software/vozilla/maps/setting-a-home-position.md
- https://docs.theseus.us/gcs-software/vozilla/maps/feature-map.md
- https://docs.theseus.us/gcs-software/vozilla/vehicles.md
- https://docs.theseus.us/gcs-software/vozilla/vehicles/creating-a-new-vehicle.md
- https://docs.theseus.us/gcs-software/vozilla/cameras.md
- https://docs.theseus.us/gcs-software/vozilla/cameras/creating-a-new-camera-calibration.md
- https://docs.theseus.us/gcs-software/vozilla/cameras/calibrating-your-camera.md
- https://docs.theseus.us/gcs-software/vozilla/cameras/manual-camera-calibration.md
- https://docs.theseus.us/gcs-software/vozilla/flights.md
- https://docs.theseus.us/gcs-software/vozilla/flights/reviewing-a-flight.md
- https://docs.theseus.us/gcs-software/vozilla/settings.md
- https://docs.theseus.us/gcs-software/vozilla/settings/recording-control.md
- https://docs.theseus.us/gcs-software/vozilla/settings/organizations.md
- https://docs.theseus.us/gcs-software/vozilla/lockdown-mode.md
- https://docs.theseus.us/gcs-software/vozilla/system-status.md

Stitch MCP was used for the design brief after auth was restored:

- Stitch project: `projects/2037543811164044557`
- Stitch design system asset: `3f458e6f5fa2449eb0a7969807a8f3f4`
- Design system name: `Tactical Horizon`

The docs include UI media/captions for the Vozilla homepage, maps page, flights page, metrics tab, recording controls, lockdown/audit flow, and related panels. Those references were used as product and interaction inspiration only. Theseus images, animations, screenshots, logos, and exact visual assets are not copied into this repository.

The `Tactical Horizon` Stitch output has been integrated into the local desktop app through Tailwind tokens and component styling: dark cockpit surfaces, cyan active state, green/amber/red status semantics, monospaced telemetry labels, rectangular controls, square status LEDs, map overlays, and an always-visible diagnostics console in Mission Planner.

Mission Planner now uses the Stitch overlay structure rather than a scrolling page:

- full-bleed map as the base surface
- floating top telemetry/status rail
- fixed right-side plan editor dock
- left-side map and live-position overlays
- fixed bottom operations/diagnostics dock for maps, readiness, bundle build, runtime commands, bundle health, and console output

## UI Media Reviewed

The Vozilla docs expose UI media through GitBook image blocks and one connection-animation placeholder. The following media references shaped this pass:

| Docs area | UI media / animation reference | Product signal used |
| --- | --- | --- |
| Vozilla landing page | Vozilla 2.0+ homepage screenshot | Main app should open as an operations console, not a marketing or setup page. |
| Quick Start login/homepage | Login and homepage screenshots | User/session context belongs in the shell, but operational content should remain dominant. |
| Quick Start connection flow | Connection image/GIF placeholder | Connection status should be near the top-right and visibly tied to the flight controller/runtime. |
| Quick Start overview | Homepage breakdown screenshots | Persistent title context, map controls, global search, connection info, dock navigation, and diagnostics are core shell elements. |
| Navigation Panel | Right dock navigation screenshot | Active map, vehicle, camera, and flights should be summarized in an operations dock. |
| Maps | Maps page screenshot | Map lifecycle needs an operator-facing page, not just file-path inputs. |
| Map creation/selection | Boundary, generation, download, upload screenshots | Map build/upload should expose progress, size, generated state, local state, and active runtime state. |
| Home position | Home/takeoff map control screenshot | Takeoff/home should be editable on the map and persisted with the selected map. |
| Feature map | Quality map screenshot | Terrain matchability should be visible as a planning overlay. |
| Vehicles | Vehicle configuration screenshots | Vehicle transform setup should become a guided calibration/configuration flow. |
| Cameras | Camera calibration and distortion-preview screenshots | Intrinsics capture, compute, save, and verify need a desktop UI. |
| Flights | Flights subpage screenshot | Synced logs should be first-class desktop records. |
| Flight review | Metrics tab screenshot | GPS-vs-vision tracks, accuracy, coordinates, MCAP export, and notes are expected review tools. |
| Settings / recording | Recording control screenshots | Record state should have live controls and service-backed state, not just local UI state. |
| Organizations | Organization/map sharing screenshots | Team map sharing is visible in Vozilla but remains out of scope for this single-operator v1. |
| Lockdown mode | Audit/delete-log screenshots | Security audit and encrypted logging are missing future operational controls. |
| System status | Bottom system status screenshot | Diagnostics should be persistent, customizable, and sourced from runtime API plus MAVLink. |

## Design Takeaways Applied

- Vozilla is structured like a live ground control station, not a settings app.
- The homepage centers on a map with persistent map controls, connection state, a search bar, and dockable navigation.
- Operator state is always near the top: app version/page context, flight controller connection, edge-device health, recording state, and navigation/search.
- Diagnostics are first-class, with a bottom panel for MAVLink, heartbeat, parameters, console, and system status.
- Map lifecycle is explicit: generated or imported, downloaded locally, uploaded to the edge device, selected as active, and assigned a home/takeoff position.
- Flights/logs are reviewable after sync, with GPS and estimated-position tracks, metrics, and notes.

## Feature Gap Matrix

| Vozilla capability | Vozilla docs area | Our current app status | Gap / action |
| --- | --- | --- | --- |
| Page-aware title bar with app version | Quick Start, Vozilla Directory and Version | Added in this pass | Keep wired to route context; later source version from Tauri/package metadata instead of a constant. |
| Global search / spotlight navigation | Quick Start, Search Bars; Selecting a map | Added in this pass | Expand beyond route navigation into settings cards, devices, maps, and command actions. |
| Top-right connection info for flight controller, edge device, and recording | Quick Start, Connection Info; Recording Control | Partially added | Title bar now shows active device and a local record marker. Need real Pi service state, MAVLink state, and recording start/stop commands. |
| Map cursor lat/lon readout | Quick Start, Map Controls | Added in this pass | Mission Planner map now shows cursor coordinates. Consider also adding coordinate copy and format switching. |
| Map recenter control | Quick Start, Map Controls | Added in this pass | Recenter returns to selected region bounds or default center. |
| Right dockable navigation panel summarizing active map, vehicle, camera, flights | Navigation Panel | Partial | We have a left nav and right mission editor. Need a reusable operations dock showing active map, vehicle config, camera calibration, and recent flights/logs. |
| Maps page can generate mission maps from search and polygon boundary | Maps; Creating a new map | Partial | We support downloaded/uploaded maps and selected regions. Need richer generation jobs, polygon boundaries, queue/progress, area estimates, and failure states. |
| Large/non-rectangular map handling and size/time estimates | Creating a new map; Maps Information | Partial | Terrain bundle health exists. Need operator-facing size/time estimates before build/upload. |
| Map library with cloud/local/device lifecycle | Selecting a map | Partial | App has local regions and Pi upload. Need explicit lifecycle states: local bundle, uploaded to Pi, active on runtime, stale, failed, deleting. |
| Active map automatically selected after upload | Selecting a map | Partial | Mission upload validates bundle but does not persist active-runtime map selection state from the Pi. |
| Home/takeoff position per map, draggable or coordinate entry | Setting a home position | Partial | GNSS-denied readiness has home/map reset fields. Need map-level home point editor and runtime restart/apply action. |
| Feature/quality map for terrain matchability | Feature map | Partial | Bundle health includes map quality heatmap after build. Need preflight quality map layer in the map viewport. |
| Vehicle configuration for flight-controller-to-camera/body transform | Vehicles; Creating a new vehicle | Partial | Device settings exist, but no guided transform wizard for Pixhawk, camera, and compute-module mounting offsets. |
| Automatic upload and activation of vehicle config | Creating a new vehicle | Missing | Need config packager and Pi apply/test command. |
| Camera calibration utility with capture/compute/save/verify | Cameras; Creating a new camera calibration; Calibrating your camera | Partial | Docs and scripts exist, but desktop app lacks a guided calibration capture and distortion preview flow. |
| Manual camera intrinsics editor | Manual camera calibration | Missing | Need safe advanced editor with validation and bundle export. |
| Flight log sync from device | Flights | Missing | Support bundles can download artifacts, but there is no automatic post-flight log sync/index. |
| Flight review map with GPS track and estimated vision track | Flights; Reviewing a flight | Missing | Need log parser/viewer, track overlays, accuracy coloring, and replay controls. |
| Metrics tab with median GPS-vs-estimate accuracy and coordinates | Reviewing a flight | Missing | Need metric extraction from runtime logs and visualization. |
| Notes tab for flight review | Reviewing a flight | Missing | Need local notes attached to flight log records. |
| MCAP export for visualization | Reviewing a flight | Missing | Need MCAP writer/export command for synced logs. |
| Settings search and grouped settings content | Settings | Partial | Basic settings page exists. Need search across settings cards and device/config fields. |
| Recording controls: start/stop, auto-start on boot, delete logs | Recording Control | Partial | Titlebar has a local record marker only. Need SSH/API commands for actual recorder service state. |
| Organization/team map sharing | Organizations | Out of scope for v1 | Could become shared map registry later, but not needed for single-operator hardware test. |
| Lockdown mode, encrypted logs, security audit, delete offending logs | Lockdown Mode | Missing | Need a security mode only after runtime logging/storage format stabilizes. |
| Bottom system status panel with Edge API and MAVLink sources | System Status | Partial | Mission Planner now has an operations strip and command output. Need a persistent diagnostics drawer with MAVLink heartbeat, runtime service health, parameters, and warnings. |
| MAVLink status indicators and heartbeat visibility | System Status | Partial | MAVLink endpoint is configured and position telemetry exists. Need live heartbeat/parameter/status view. |

## Highest-Value Next Features

1. Persistent diagnostics drawer:
   A bottom panel should consolidate MAVLink heartbeat, Pi service health, runtime status JSON, command output, support bundle evidence, and current warnings.

2. Active runtime lifecycle:
   Maps and bundles should show `local`, `built`, `uploaded`, `validated`, `active on Pi`, `stale`, and `failed` states.

3. Flight review page:
   Add synced flight logs, map replay, GPS vs vision tracks, accuracy coloring, metrics, and operator notes.

4. Camera and vehicle setup wizards:
   Add guided camera calibration and vehicle transform setup because those directly affect GNSS-denied accuracy.

5. Feature quality overlay:
   Render bundle quality/feature density on the Mission Planner map before flight so the operator can avoid weak localization areas.
