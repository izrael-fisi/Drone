# PX4 External Vision Hardware Bench Guide

This guide is for real Pixhawk hardware bench testing. It does not use PX4
SITL, Gazebo, or ROS 2.

Use it after the basic Holybro X500 V2 prop-off wiring and QGroundControl checks
are complete.

## Safety Scope

- Propellers removed.
- Vehicle restrained.
- QGroundControl connected.
- No autonomous flight mode testing.
- No raw motor commands from this repo.
- No PX4 parameter auto-writes from this repo.

## Preferred Message

Use MAVLink `ODOMETRY` for PX4 external-vision readiness:

```bash
VISION_NAV_MAVLINK_MESSAGE=odometry
```

Use `vision_position_estimate` only for compatibility/debug:

```bash
VISION_NAV_MAVLINK_MESSAGE=vision_position_estimate
```

## Hardware Inputs

Required before external-vision output:

- validated terrain bundle
- camera health report
- camera calibration
- camera-to-body metadata
- Pixhawk telemetry endpoint
- PX4 parameter export
- logging-only runtime capture that produces `terrain_matches.jsonl`

## Endpoint Check

On the Pi:

```bash
cd ~/Drone
export VISION_NAV_MAVLINK_ENDPOINT=/dev/ttyACM0
./scripts/pi/check_mavlink_endpoint.sh
```

Use the actual endpoint for your wiring. Examples may include USB serial,
telemetry UART, or another MAVLink route configured for the bench.

## Parameter Check

Export PX4 parameters from QGroundControl to:

```text
~/px4.params
```

Then run:

```bash
cd ~/Drone
VISION_NAV_PX4_PARAMS=$HOME/px4.params ./scripts/pi/check_px4_params.sh
```

The checker reports readiness issues but does not modify the controller.

## Logging-Only Runtime

Run this before enabling MAVLink send:

```bash
cd ~/Drone
VISION_NAV_BUNDLE=$HOME/drone-data/map_bundles/mission_bundle \
VISION_NAV_COUNT=30 \
./scripts/pi/run_terrain_nav_loop.sh
```

Then:

```bash
VISION_NAV_RUNTIME_STATUS_ROOTS=$HOME/DroneTransfer/outgoing/terrain-match \
./scripts/pi/read_runtime_status.sh
```

Proceed only if the log/status output is understandable and no safety issue is
present.

## MAVLink-Enabled Prop-Off Run

```bash
cd ~/Drone
VISION_NAV_BUNDLE=$HOME/drone-data/map_bundles/mission_bundle \
VISION_NAV_MAVLINK_ENDPOINT=/dev/ttyACM0 \
VISION_NAV_MAVLINK_MESSAGE=odometry \
VISION_NAV_COUNT=30 \
./scripts/pi/run_terrain_nav_loop.sh
```

Pass criteria:

- accepted measurements are sent only when estimator confidence passes gates
- rejected/failed measurements are logged but not sent as trusted updates
- runtime log includes external-position health
- QGroundControl/PX4 shows no unexpected arming, mode, or motor behavior

## Evidence To Save

Create a support bundle:

```bash
cd ~/Drone
./scripts/pi/create_support_bundle.sh
```

The bundle should include:

- mission bundle health
- runtime terrain log
- runtime status
- camera health
- PX4 parameter report
- MAVLink endpoint check output if available
- external-position health from runtime logs

## Do Not Proceed If

- propellers are installed
- Pixhawk wiring is uncertain
- QGroundControl cannot connect
- RC calibration is incomplete
- GPS/compass/power module status is unknown
- Pi cannot read MAVLink heartbeat/telemetry
- camera focus or mount is not stable
- map bundle validation fails
- runtime output is low confidence but still being treated as valid
