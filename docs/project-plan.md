# Project Plan

This plan reflects the current hardware-first direction. The project is split
into two active sections only:

1. Drone code operation.
2. Ground control / mission planner desktop app.

ROS 2, Gazebo, and PX4 SITL are out of scope for the active plan.

## Section 1: Drone Code Operation

Goal: run a stable Raspberry Pi companion-computer stack that can capture
downward imagery, evaluate it against a preloaded learned map release, and
produce shadow-only evidence through the versioned vision-first runtime. The
active runtime uses explicit frame/time/calibration, map/model-release,
geometry, ambiguity, and temporal contracts. PX4 publication is deliberately
withheld until a later validation program passes.

### Stable Scaffold

- Keep Python package code under `src/vision_nav/`.
- Put new localization behavior under `types.py`, `contracts.py`, `runtime/`,
  `estimation/`, `mapping/`, and `px4/`.
- Keep root-level terrain matching loops as map/replay evidence only; do not
  add direct PX4 publication to them. The active vehicle runtime is
  `src/vision_nav/neural/shadow_runtime.py`.
- Keep Pi operator wrappers under `scripts/pi/`.
- Keep camera and Pi runtime config under `config/`.
- Keep local smoke/preflight scripts under `scripts/dev/`.
- Keep generated logs, map bundles, and transfer artifacts out of committed
  source except for small examples.

Acceptance criteria:

- `python tests/run_unit_tests.py` passes.
- `./scripts/dev/local_preflight.sh` passes.
- Pi wrappers still run from the repo root without requiring ROS 2/SITL.
- Runtime commands write deterministic logs/status files under
  `~/DroneTransfer/outgoing/`.

### Pi Runtime Setup

- Bootstrap Raspberry Pi 5.
- Verify camera tools and Python environment.
- Run synthetic vision smoke test.
- Validate the compatible learned ModelRelease, MapRelease, and measured
  camera calibration.
- Run a bounded learned shadow probe from a saved frame, then live camera plus
  read-only Pixhawk attitude.
- Read and download runtime status.
- Create support bundle.

Acceptance criteria:

- `./scripts/pi/first_run_checks.sh` completes.
- `./scripts/pi/validate_neural_model.sh` and
  `./scripts/pi/validate_neural_runtime.sh` pass for the selected releases.
- `./scripts/pi/run_vision_nav_shadow.sh --once` writes `runtime_status.json`
  and `decision_trace.jsonl`.
- `./scripts/pi/read_runtime_status.sh` finds `runtime_status.json`.

### Pixhawk/MAVLink Bench Setup

- Connect Raspberry Pi to Pixhawk using a documented telemetry link.
- Verify QGroundControl can connect through USB or SiK telemetry.
- Export PX4 parameters from the real flight controller.
- Run `./scripts/pi/check_px4_params.sh` against the export.
- Run a prop-off MAVLink telemetry check from the Pi.
- Collect MAVLink telemetry for bench evidence; bind external-vision ODOMETRY
  only after the versioned runtime passes its replay, HIL, and PX4 transport
  validation gates.

Acceptance criteria:

- The Pi can read Pixhawk heartbeat/attitude/local-state telemetry.
- PX4 parameter report is saved.
- Runtime logs show telemetry health and explicit publication-withheld reasons.
- No motor spin is commanded by this project code.

### Camera And Map Data

- Calibrate the downward camera.
- Measure or document camera-to-body mounting direction and offsets.
- Prepare a learned MapRelease from the selected georeferenced map source.
- Validate georeference, GSD, tile count, learned retrieval index, learned
  local-feature cache, checksums, and calibration compatibility.

Acceptance criteria:

- `config/camera/down_camera.yaml` exists for the installed camera/lens.
- `config/camera/camera_to_body.yaml` reflects the mounted camera.
- MapRelease validation passes, including artifact checksums and compatible
  calibration ID.
- Training capture validation can find the deployed camera calibration and
  camera-to-body transform.

## Section 2: Ground Control / Mission Planner Desktop App

Goal: provide a stable customer-facing app for local map preparation, mission
planning, Raspberry Pi setup, hardware checks, and support-bundle review.

### Stable Scaffold

- Keep React/Tauri UI code under `desktop-app/src/`.
- Keep Rust commands under `desktop-app/src-tauri/src/`.
- Keep the Vision Pipeline page as the only editable pipeline configuration
  surface.
- Keep Mission Planner focused on maps, mission/fence/rally/vision checkpoints,
  terrain constraints, bundle build/upload, and validation.
- Keep Devices/Module Setup focused on Wi-Fi/SSH setup, Pi checks, camera checks,
  MAVLink checks, field capture, and support bundles.

Acceptance criteria:

- `npm run build --prefix desktop-app` passes.
- `cd desktop-app/src-tauri && cargo check && cargo test` passes.
- First click into Mission Planner does not auto-load a large mosaic.
- Bundle build reads the selected Vision Pipeline defaults.

### Hardware-Ready Operator Flow

- Select or import map source.
- Build and validate terrain bundle.
- Upload bundle to Raspberry Pi.
- Connect to Pi over Wi-Fi/SSH.
- Verify camera.
- Verify MAVLink endpoint.
- Run prop-off runtime and support-bundle checks.
- Download support bundle and review failures before any prop-on work.

Acceptance criteria:

- App can build a bundle without ROS 2/SITL assumptions.
- App can run camera, bundle, MAVLink, runtime-status, and support-bundle checks.
- App report includes enough information to reproduce the hardware bench test.

## Immediate Hardware Test Milestone

The next milestone is a prop-off Holybro X500 V2 bench test with the real
Pixhawk flight controller, radio/GCS link, GPS module, Raspberry Pi, and camera.

Use:

- [Holybro X500 V2 Hardware Data Inputs](holybro-x500v2-hardware-data-inputs.md)
- [Holybro X500 V2 Prop-Off Hardware Test](holybro-x500v2-prop-off-hardware-test.md)

Propellers must stay removed for every command in this milestone.
