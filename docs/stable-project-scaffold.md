# Stable Project Scaffold

The active repository scaffold has two sections:

1. Drone code operation.
2. Ground control / mission planner desktop app.

ROS 2, Gazebo, and PX4 SITL are not part of the active scaffold. Legacy helpers
may remain until removed safely, but they should not appear in new setup
instructions or hardware-readiness criteria.

## Drone Code Operation

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

Expected runtime data:

```text
~/drone-data/map_bundles/mission_bundle/
~/DroneTransfer/outgoing/terrain-match/
~/DroneTransfer/outgoing/support-bundles/
~/DroneTransfer/outgoing/camera-health/
~/DroneTransfer/outgoing/replay-cases/
```

Stable commands:

```bash
./scripts/pi/first_run_checks.sh
./scripts/pi/validate_terrain_bundle.sh
./scripts/pi/run_terrain_nav_loop.sh
./scripts/pi/read_runtime_status.sh
./scripts/pi/check_mavlink_endpoint.sh
./scripts/pi/check_px4_params.sh
./scripts/pi/create_support_bundle.sh
```

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
