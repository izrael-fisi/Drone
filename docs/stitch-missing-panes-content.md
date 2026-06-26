# Stitch Missing Panes And Content

This list reflects the current authoritative Stitch panes supplied for the live
desktop app redesign.

## Panes Now Implemented In The Live App

- Mission Planner
  - Use the multi-dimensional mission planner frame as visual authority.
  - Keep the live Leaflet map, click-to-place mission items, GPS/vision marker,
    mission import/export, bundle build/upload, MAVLink controls, and support
    bundle actions connected.

- Camera Config / Algorithm Intelligence
  - Use the Algorithm Intelligence pane as visual authority.
  - It must remain the only editable vision pipeline surface.
  - Keep ORB/AKAZE/SIFT, classical/neural mode, matcher ratio, min matches, max
    features, and model paths wired to the existing pipeline defaults.

- System Status / Diagnostics
  - Use the terminal diagnostics and right-side resource monitor pane as visual
    authority.
  - Keep UDP position telemetry, GPS-vs-vision source display, active device,
    map readiness, pipeline status, and MAVLink status connected.

- Ops Console / Dashboard
  - Implemented as a mission-readiness command surface with active device,
    map cache, pipeline, bundle gate, readiness checks, quick actions, and
    saved map source management.

- Map Library
  - Existing draw/download/import/georef workflow now sits in the same fixed
    glass-panel command frame.

- Mission Bundle Builder
  - Implemented as a map-to-runtime package builder with map source, repo path,
    output path, pipeline summary, feature/tile metadata, bundle health, STAC,
    and tile-index status.

- Terrain Planning
  - Implemented as terrain profile/risk planning with map source, min AGL,
    terrain relief, AGL/GSD, max segment length, route-risk checks, and shared
    terrain defaults.

- Vehicle Manager
  - Existing Wi-Fi discovery, SSH, runtime device, MAVLink, and bench-flow
    content now sits in the same fixed command frame.

- Flight Review / History
  - Implemented as support-bundle evidence review with counts, storage,
    latest-bundle health, replay/GNSS-denied summaries, and the support bundle
    manager.

- Settings
  - Implemented as configuration/data control with imagery API keys, YAML
    parameter editing, config status, and retention/security placeholders.

## Remaining Missing Authoritative Panes

These areas still need Stitch panes if you want visual authority before further
UI changes:

- Onboarding / First Run
  - Needs profile creation, organization/accent setup, initial runtime device
    selection, project path selection, first map source prompt, and first bench
    test guidance.

- Dedicated Raspberry Pi Setup Wizard
  - Vehicle Manager already contains much of this workflow, but a dedicated
    pane could still improve first-time Pi discovery, dependency install,
    camera health, and project-sync sequencing.

- Dedicated Security / Data Retention Pane
  - Settings now exposes the placeholder status, but encrypted logs, clear-log
    flows, sensitive file audit, and retention policy need a full authority
    pane if they become product requirements.

## Missing Content Inside Covered Panes

- Mission Planner
  - A future camera/video mini-feed box can be added if the Pi exposes live
    frames over the app.
  - A true 3D terrain visualization is not implemented; the live pane currently
    prioritizes the 2D georeferenced Leaflet map used by the mission data
    pipeline.
  - Direct flight-controller parameter editing is intentionally not exposed.

- Camera Config / Algorithm Intelligence
  - Runtime benchmark values are estimates, not measured profiling results.
  - TensorRT is represented as the neural high-compute option; it is not wired
    to a backend TensorRT build step.
  - Live feature/inlier metrics should be connected to runtime status logs when
    the Pi emits that stream.

- System Status / Diagnostics
  - CPU and memory bars are inferred UI indicators until a Pi system metrics
    packet is added.
  - Terminal commands are app-navigation/status shortcuts, not a raw MAVLink
    shell.
  - Battery/current/remaining-time values need a MAVLink telemetry backend
    packet before becoming authoritative.

- Map Library
  - Map quality heatmap is visible after bundle build, not directly in the
    draw/download surface yet.
  - Provider credential troubleshooting could use a dedicated dialog.

- Vehicle Manager
  - The prop-off Holybro X500 V2 flow exists in the broader setup stack, but a
    tighter authoritative pane could make it more sequential.

- Settings
  - Data retention and security controls are represented as status/placeholders
    until encrypted logs, clear logs, and audit actions are implemented.
