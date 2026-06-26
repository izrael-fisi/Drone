# Stitch Template Backlog: Optional Design References

The app no longer treats Stitch panes as authoritative implementation blockers.
The native React/Tauri pages are the usable product surface. Stitch can still be
used later to create visual references or refinements for these areas:

- Navigation Panel
- Mission Planner
- Mission Bundle Builder
- Vehicle Manager
- Camera & Vision Configuration
- System Status & Diagnostics
- Flight Review & History

Some features below already exist in the native app. This backlog describes
where additional Stitch design guidance may still be useful, not whether the
feature is active.

## Onboarding / First Run Setup

Purpose: guide a new operator through initial identity, organization, device,
and project setup before the app enters the operational console.

Needed template coverage:

- User profile creation: operator name, role, contact label, and local-only
  profile state.
- Organization/name/accent setup: project name, organization name, aircraft
  callsign prefix, and accent color selection.
- Initial device selection: choose Raspberry Pi runtime, Pixhawk/autopilot
  target, camera profile, and local repo path.
- First-time workflow guidance: short checklist for map source, vision pipeline,
  mission bundle, Pi setup, bench test, and field readiness.
- Completion state: clear first-run complete action and a way to reopen setup.

## Dashboard / Home Page

Purpose: provide a start surface that summarizes project readiness without
duplicating operational pages.

Needed template coverage:

- Quick action cards for mission planning, vehicle connection, camera check,
  diagnostics, flight review, and bundle validation.
- Project summary widgets for active map, active vehicle, camera status,
  runtime position source, latest flight, and latest support bundle.
- "Start here" launcher surface that changes based on whether the operator is
  in setup, bench testing, or field operations.
- Empty, degraded, and ready states for each widget.

## Maps / Map Library

Purpose: manage map sources before they become mission-ready terrain bundles.

Needed template coverage:

- Map drawing/download workflow for selecting an area of interest.
- Map import/upload workflow for local image and geospatial files.
- Uploaded image/GeoTIFF handling, including georeference confidence and manual
  metadata correction when needed.
- Map source list with status, size, date, georeference, and active/inactive
  indicators.
- Map provider selection for offline/cached source strategy.
- Bing/Mapbox/ESRI source controls, including API-key-required states.
- DEM/DSM attachment workflow with elevation source health and compatibility.

## Map Generation / Processing

Purpose: turn map sources into runtime-ready terrain localization assets.

Needed template coverage:

- Bundle map preparation controls and progress.
- Terrain tile indexing status, tile count, tile size, and coverage bounds.
- Feature-map generation status for ORB/AKAZE and optional neural paths.
- Map quality/heatmap preview for feature density and weak localization areas.
- Lifecycle states: local, built, uploaded, active, stale, failed.
- Failure detail panel with missing files, bad georeference, invalid CRS, and
  insufficient features.

## Raspberry Pi Setup Wizard

Purpose: make runtime-compute setup approachable from the desktop app.

Needed template coverage:

- Wi-Fi discovery for nearby Raspberry Pi devices and manual host entry.
- SSH setup, key installation, auth status, and retry/failure flows.
- Project sync/install with repo path, Python environment, and dependency
  status.
- Pi dependency checks for Python, OpenCV, camera libraries, MAVLink packages,
  storage, and permissions.
- Camera health check with preview status, frame rate, resolution, exposure, and
  sample capture evidence.
- Hardware setup checks for Pixhawk/MAVLink serial endpoint, camera mount,
  storage, and runtime service readiness.

## Device Profile Management

Purpose: create and manage reusable runtime-device definitions.

Needed template coverage:

- Add/edit/delete runtime device.
- SSH auth configuration, username, host, port, key status, and last verified
  time.
- Remote project path and remote bundle path validation.
- MAVLink endpoint setup for UDP/TCP/serial, baud rate, and health status.
- Pixhawk/autopilot profile fields for board type, firmware family, vehicle
  frame, airframe notes, telemetry radio, and safety constraints.
- Backward-compatible display for older saved device fields.

## Vision Pipeline Algorithm Settings

Purpose: own all editable localization algorithm settings in one dedicated
configuration page.

Needed template coverage:

- ORB / AKAZE selection with Pi-friendly defaults.
- SuperPoint / LightGlue option for higher-compute devices.
- Max features, matcher ratio, minimum matches, RANSAC threshold, and confidence
  tuning.
- Neural model weight paths and availability checks.
- Runtime-cost estimate for Raspberry Pi, desktop GPU, and future compute
  modules.
- Read-only summary states for other pages to consume without editing.

## GNSS-Denied Readiness

Purpose: verify the aircraft and runtime are ready to operate without relying
on GNSS.

Needed template coverage:

- Satellite source disabled/ignored state for validation mode.
- Map reset point and home reset point selection.
- Heading setup and alignment instructions.
- Estimator health display for vision fix, IMU propagation, covariance,
  confidence, stale-match detection, and fallback state.
- GNSS-denied checklist for map coverage, camera health, MAVLink link, position
  output, and bench safety.

## Terrain Planning

Purpose: plan route constraints that affect visual localization quality and
camera-ground geometry.

Needed template coverage:

- Minimum AGL control and warnings.
- Terrain relief constraints and route risk flags.
- AGL/GSD ratio display.
- Max route segment length and route split settings.
- Terrain profile preview with elevation uncertainty.
- Route split records and status for each segment.

## Support Bundle Manager

Purpose: review downloaded evidence from bench and field runs.

Needed template coverage:

- Downloaded support bundle list with date, device, mission, and status.
- Support bundle detail viewer for logs, images, manifests, telemetry snippets,
  and runtime config.
- Evidence extraction for camera frames, MAVLink samples, position telemetry,
  and diagnostics.
- Bench/field report summaries with pass/fail, warnings, and next actions.

## Hardware Bench / Prop-Off Test Flow

Purpose: guide safe first hardware testing for the Holybro X500 V2 without
propellers.

Needed template coverage:

- Holybro X500 V2 bench steps, including Pixhawk, receiver, camera, Pi, radio,
  and battery checks.
- Prop-off safety checks with explicit confirmation state.
- PX4 receiver evidence capture for stick movement, arm prevention, flight mode,
  failsafe, and telemetry link.
- Field capture workflow for camera/MAVLink/runtime logs while motors remain
  safe.
- Hardware readiness report with evidence, unresolved blockers, and export.

## Runtime Position Telemetry Settings

Purpose: configure and diagnose live position packets from the runtime compute
module to the ground station.

Needed template coverage:

- UDP listener port and bind-address settings.
- GPS vs vision source priority display.
- Live source fallback configuration for GPS healthy, GPS weak/jammed, vision
  accepted, and degraded states.
- Position packet diagnostics: packet rate, last packet age, source, lat/lon,
  local ENU, covariance, confidence, and rejected packet reason.
- Integration points for Mission Planner live aircraft marker and Flight Review
  replay.

## Settings Page

Purpose: hold general application preferences that do not belong to operational
surfaces.

Needed template coverage:

- Imagery API keys and provider availability.
- App preferences such as theme, units, coordinate format, and startup route.
- Repo path defaults for local project, Pi project path, and transfer folders.
- Storage/download locations for maps, bundles, support bundles, logs, and
  exports.
- General configuration import/export.

## Import / Export Plan Tools

Purpose: support mission-plan file exchange while preserving compatibility
details.

Needed template coverage:

- `.plan` import action and parse result.
- `.plan` export action and validation result.
- Plan file dirty/saved state.
- QGroundControl compatibility details for mission items, unsupported fields,
  geofence/rally data, and vision-specific metadata.
- Conflict and overwrite dialogs for imported plans.

## Security / Lockdown / Data Retention

Purpose: protect sensitive operational data and provide controlled cleanup.

Needed template coverage:

- Encrypted logs state and key/storage status.
- Clear logs and clear cached maps actions with confirmation states.
- Audit sensitive files view for API keys, SSH keys, support bundles, and
  telemetry logs.
- Recording retention policy for automatic cleanup and manual preservation.
- Lockdown/field mode that hides secrets and restricts risky edits.
