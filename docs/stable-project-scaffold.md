# Stable Project Scaffold

The active repository scaffold has two sections:

1. Drone code operation.
2. Ground control / mission planner desktop app.

ROS 2, Gazebo, and PX4 SITL are not part of the active scaffold. Legacy helpers
may remain until removed safely, but they should not appear in new setup
instructions or hardware-readiness criteria.

## Drone Code Operation

### Canonical navigation boundary

New navigation work belongs in the learned shadow architecture, not in the
legacy single-frame terrain loop:

```text
FramePacket -> VisionNavigationRuntime -> NavEstimate -> Px4VisionBridge
```

`FramePacket` carries a monotonic timestamp, clock domain, calibration ID, and
camera model. The runtime joins each frame with read-only PX4 attitude,
requires a compatible ModelRelease and MapRelease, and records learned
retrieval, correspondence, geometry, and temporal evidence. It returns
`NO_GLOBAL_FIX` or `RECOVERING` when evidence is stale or ambiguous.
`Px4VisionBridge` remains a future output boundary; the current runtime is
shadow-only and cannot publish vehicle position.

The root-level `terrain_matcher.py` / `run_terrain_loop.py` path remains useful
for map build evaluation, recorded replay, and camera bench evidence. It does
not publish vision observations to PX4.

Use `vision-nav-create-map-release` after a map's feature/cache build to write
its immutable release manifest. The release records the ENU origin, GSD, tile
index, model versions, compatible camera calibration IDs, and checksums. The
runtime must reject a partial or incompatible release before it reaches the PX4
boundary.

Runtime source:

```text
src/vision_nav/
```

Operator wrappers:

```text
scripts/pi/
scripts/mac/
scripts/dev/
```

Configuration:

```text
config/camera/down_camera.yaml
config/camera/camera_to_body.yaml
config/pi/vision-nav.env.example
```

Expected active runtime data:

```text
~/drone-data/model_releases/pi5_crossview_nav_v1/
~/drone-data/map_releases/active/
~/drone-data/calibration/
~/DroneTransfer/outgoing/neural-shadow/
~/DroneTransfer/outgoing/support-bundles/
~/DroneTransfer/outgoing/camera-health/
~/DroneTransfer/outgoing/replay-cases/
```

Active Pi commands:

```bash
./scripts/pi/first_run_checks.sh
./scripts/pi/run_prop_off_preflight.sh
./scripts/pi/capture_calibration_set.sh
./scripts/pi/record_training_capture.sh
./scripts/pi/validate_neural_model.sh
./scripts/pi/validate_neural_runtime.sh
./scripts/pi/run_vision_nav_shadow.sh --once
./scripts/pi/read_runtime_status.sh
./scripts/pi/check_mavlink_endpoint.sh
./scripts/pi/check_px4_params.sh
./scripts/pi/create_support_bundle.sh
```

See [First Learned Shadow Run](first-learned-shadow-run.md) for the required
release layout and [Legacy Prototype Components](legacy-prototype-components.md)
for explicitly non-operational tools retained for replay compatibility.

## Ground Control / Mission Planner Desktop App

App source:

```text
desktop-app/src/
desktop-app/src-tauri/src/
```

Stable app surfaces:

- Dashboard
- Maps
- Vision Pipeline
- Devices / Module Setup
- Mission Planner
- Settings

Mission Planner owns map selection, manual mission items, geofence/rally/vision
map layers, GNSS-denied prep, terrain constraints, bundle build/upload, and
bundle validation.

Vision Pipeline owns all editable feature/matcher defaults.

Devices / Module Setup owns Wi-Fi/SSH setup, Raspberry Pi install, camera
checks, MAVLink endpoint checks, field capture, runtime status, and support
bundle review.

## Validation

Run before pushing code:

```bash
python tests/run_unit_tests.py
./scripts/dev/local_preflight.sh
npm run build --prefix desktop-app
cd desktop-app/src-tauri && cargo check && cargo test
```

The project is scaffold-stable when those commands pass and the active docs do
not instruct the operator to use ROS 2, Gazebo, or PX4 SITL for the next
hardware milestone.
