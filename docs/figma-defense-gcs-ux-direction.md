# Drone Vision GCS UX Direction

Figma board: https://www.figma.com/design/UYXxM70JixKVJOz5gOhd7N

This critique and design direction is for the desktop ground station app. The goal is a minimal technical interface for GNSS-denied vision navigation that feels closer to an operations console than a consumer dashboard. The visual direction should keep the Stitch panel language, then tighten it around a map-first GCS workflow, defense-style decision support, and QRadar/SOAR-style issue handling.

## Reference Intent

- Anduril-style influence: command and control, autonomy oversight, sensor/effect integration, and high-confidence operator decisions.
- Palantir-style influence: dense operational intelligence, cross-domain data fusion, and decision support rather than decorative UI.
- QRadar/SOAR-style influence: case queues, severity, playbooks, evidence, audit trail, and workflow state.
- GCS influence: live map, vehicle state, mission commands, telemetry confidence, failsafe status, and post-flight replay.

Reference links:

- Anduril Lattice Command and Control: https://www.anduril.com/lattice/command-and-control
- Anduril Lattice Mission Autonomy: https://www.anduril.com/lattice/mission-autonomy
- Palantir Defense: https://www.palantir.com/offerings/defense/
- IBM SOAR overview: https://www.ibm.com/think/topics/security-orchestration-automation-response
- IBM QRadar SOAR SLA article: https://developer.ibm.com/articles/awb-tracking-slas-security-incidents-using-qradar-soar/

## Current UX Critique

The app already has the right major product shape: persistent navigation, mission planning, map preparation, bundle building, vehicle management, camera/vision configuration, diagnostics, and flight review. That is a strong base.

The main issue is information hierarchy. Several panes recreate a full command surface inside the page body, so the app can feel like many consoles embedded inside one console. Keep the global left navigation and top command bar as the single shell. Inside each pane, use one primary work surface, one decision rail, and one evidence or console lane.

The second issue is that operational warnings are currently treated as status text. They should behave like cases. A weak map georef, stale bundle, Pi offline state, MAVLink gap, camera health failure, GPS degradation, or low vision confidence should become a severity-ranked operational case with state, evidence, and a playbook action.

The third issue is that data preparation is spread across separate feature areas. Map Library, Terrain Planning, and Mission Bundle Builder should feel like one pipeline with visible lifecycle states: local, validated, built, uploaded, active, stale, and failed.

## Design Principles

1. One command shell.

   The left navigation and top command bar are persistent. Do not duplicate app identity, primary nav, or global status inside each pane.

2. Map and mission first.

   Mission Planner and Ops Console should give the first visual priority to vehicle position, map bundle, route, source confidence, and mission state.

3. Decision density, not decoration.

   Panels should exist only when they bind to real app data: runtime state, bundle metadata, telemetry, hardware readiness, flight logs, or operator actions.

4. Color is state.

   Use cyan for selected/action, green for ready/healthy, amber for degraded/review, red for blocked/critical, and gray for unknown/inactive. Avoid using color as general decoration.

5. Progressive disclosure.

   Default screens show readiness, next action, and current blockers. Deep configuration belongs in drawers, modals, or lower-priority sections.

6. Evidence is always nearby.

   Operators should never need to hunt for why something is red. Every warning should expose the source signal, timestamp, confidence, related logs, and recommended next action.

## Proposed App Hierarchy

Operate:

- Ops Console
- Mission Planner
- Flight Review

Prepare Data:

- Map Library
- Terrain Planning
- Mission Bundle Builder

Systems:

- Vehicle Manager
- Camera and Vision Configuration
- System Status and Diagnostics

Admin:

- Settings
- Security and Data Retention

This keeps the app from becoming one long feature list while still preserving every required capability.

## Pane-Level Direction

### Ops Console

Purpose: overall mission command surface.

Primary canvas: live asset and mission state.

Decision rail: GPS versus vision source, active bundle, estimator health, open cases, arm/failsafe readiness.

Evidence lane: latest telemetry packets, source switches, command log, alerts.

### Mission Planner

Purpose: map-first route planning and in-flight mission awareness.

Primary canvas: Leaflet map, route, takeoff/waypoint/land placement, active vehicle position.

Decision rail: selected map bundle, source priority, mission validity, export state, geofence/rally status.

Evidence lane: warnings, waypoint list, QGC compatibility, plan dirty/saved state.

### Map Library

Purpose: manage source maps and imported georeferenced assets.

Primary canvas: map/source list and selected region preview.

Decision rail: georef confidence, coverage, format, source provider, DEM/DSM attachment state.

Evidence lane: import logs, validation errors, checksums, processing history.

### Terrain Planning

Purpose: constrain route and navigation bundle assumptions against terrain and imaging requirements.

Primary canvas: terrain profile and route constraints.

Decision rail: min AGL, GSD ratio, max segment, relief risk, split route records.

Evidence lane: constraint violations and recommended fixes.

### Mission Bundle Builder

Purpose: convert selected mission/map/pipeline defaults into a deployable runtime bundle.

Primary canvas: bundle build pipeline.

Decision rail: tile count, feature count, GSD, CRS, checksum, estimated Pi runtime cost.

Evidence lane: manifest preview, STAC details, upload state, validation output.

### Vehicle Manager

Purpose: configure and validate the runtime compute and Pixhawk connection.

Primary canvas: device list and selected device readiness.

Decision rail: SSH, remote paths, camera, MAVLink endpoint, Pixhawk profile, radio/GPS inputs.

Evidence lane: prop-off bench checklist, discovered interfaces, support bundle actions.

### Camera and Vision Configuration

Purpose: configure camera calibration and vision pipeline defaults.

Primary canvas: camera preview, calibration state, algorithm mode.

Decision rail: ORB/AKAZE low-compute path, optional SuperPoint/LightGlue high-compute path, match thresholds, model weights.

Evidence lane: feature count, match confidence, replay frame diagnostics, failed frame examples.

### System Status and Diagnostics

Purpose: live health, telemetry, logs, and operational cases.

Primary canvas: diagnostic terminal and health timeline.

Decision rail: CPU, memory, storage, temperature, MAVLink, camera, runtime process, packet rate.

Evidence lane: case queue with severity, owner, timer, playbook, and audit history.

### Flight Review

Purpose: post-flight replay, issue review, and report generation.

Primary canvas: route replay and source-confidence timeline.

Decision rail: GPS/vision fallback events, estimator confidence, case resolution, mission summary.

Evidence lane: logs, screenshots, support bundles, exported field report.

## QRadar-Style Operational Model

Add an `operations_cases` concept in the app model. These are not cybersecurity incidents, but they follow the same workflow pattern:

- `case_id`
- `severity`: P1, P2, P3, P4
- `state`: open, acknowledged, in_progress, resolved, suppressed
- `source`: map, bundle, camera, mavlink, px4, gps, vision_nav, storage, operator
- `summary`
- `evidence_refs`
- `first_seen`
- `last_seen`
- `playbook`
- `recommended_action`

Example cases:

- P1: MAVLink heartbeat gap detected.
- P1: GPS unreliable and vision fallback unavailable.
- P2: Vision confidence below mission threshold.
- P2: Active map bundle checksum mismatch.
- P3: Map bundle age exceeds field threshold.
- P3: Camera calibration missing or stale.
- P4: Support bundle ready for upload.

## Visual System Direction

Keep the current dark technical shell, but simplify it:

- Background: near-black blue.
- Panels: low-contrast blue-black with one-pixel borders.
- Typography: compact labels, technical numerals, no oversized marketing headings inside tools.
- Radius: small, typically 6 to 10 px.
- Motion: subtle transitions only for pane changes, status updates, and map overlays.
- Density: high enough for operators, but never equal-weight everywhere.

Avoid:

- Marketing hero layouts.
- Decorative gradients or ornamental blobs.
- Repeating the full shell inside panes.
- Making every metric a card.
- Using red/amber/green for anything other than state.

## Figma Deliverable Plan

The created Figma board contains:

- UX critique board.
- Operator Console 1440x900 blueprint.
- Design principles.
- Navigation hierarchy.
- QRadar/GCS feature retention notes.
- Figma build targets.

Next Figma screens to build:

- Ops Console
- Mission Planner
- Map Library
- Terrain Planning
- Mission Bundle Builder
- Vehicle Manager
- Camera and Vision Configuration
- System Status and Diagnostics
- Flight Review
- Settings

Reusable components to extract:

- Shell top bar
- Left navigation item
- Status chip
- Metric tile
- Decision rail section
- Case row
- Playbook action button
- Evidence lane row
- Terminal/log row
- Map overlay badge
- Segmented control
- Dense form field
- Modal/drawer shell

## Implementation Notes For The Desktop App

- Preserve the Stitch-derived fonts, palette, and box language where it already matches the new direction.
- Refactor panes toward a shared `CommandShell`, `DecisionRail`, and `EvidenceLane` structure.
- Introduce a typed case model before adding more warning banners.
- Make Mission Planner and Ops Console consume the same live position source priority model: GPS primary when healthy, GNSS-denied vision position as fallback when GPS is unreliable or jammed.
- Keep Vision Pipeline as the only editable algorithm configuration surface.
- Keep settings low-traffic and non-command-like unless the app enters a field lockdown or security review mode.
