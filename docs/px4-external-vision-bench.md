# PX4 External Vision Hardware Bench Guide (Future Milestone)

This guide is for real Pixhawk hardware bench testing. It does not use PX4
SITL, Gazebo, or ROS 2.

Use it only after the learned shadow runtime has passed held-out replay,
ONNX-parity, Pi thermal, and real prop-off capture reviews. It is not part of
the current data-collection or learned-model development milestone.

## Safety Scope

- Propellers removed.
- Vehicle restrained.
- QGroundControl connected.
- No autonomous flight mode testing.
- No raw motor commands from this repo.
- No PX4 parameter auto-writes from this repo.

## Output Boundary

The current learned runtime is shadow-only. It will not publish a match,
candidate, or estimate to PX4. A future controlled output path may use MAVLink
`ODOMETRY` from `VisionNavigationRuntime` through `Px4VisionBridge`, but only
after `TRACKING`, freshness, covariance, compatibility, and transport-health
gates pass. Do not substitute `VISION_POSITION_ESTIMATE` as a shortcut.

## Hardware Inputs

Required before external-vision output:

- compatible learned `MapRelease` and `ModelRelease`
- camera health report
- camera calibration
- camera-to-body metadata
- Pixhawk telemetry endpoint
- PX4 parameter export
- learned shadow trace that records disabled PX4 publication

## Endpoint Check

On the Pi:

```bash
cd ~/Drone
export VISION_NAV_MAVLINK_ENDPOINT=serial:/dev/ttyACM0:921600
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

## Current Shadow Runtime Evidence

Run this before any future external-vision integration:

```bash
cd ~/Drone
VISION_NAV_MAP_RELEASE=$HOME/drone-data/map_releases/active/map_release.json \
VISION_NAV_MODEL_RELEASE=$HOME/drone-data/model_releases/pi5_crossview_nav_v1/model_release.json \
VISION_NAV_MODEL_ARTIFACT_ROOT=$HOME/drone-data/model_releases/pi5_crossview_nav_v1 \
VISION_NAV_CALIBRATION_ID=down-camera-v1 \
VISION_NAV_CALIBRATION_FILE=$HOME/drone-data/calibration/down_camera.yaml \
VISION_NAV_SHADOW_FRAME=$HOME/drone-data/test-frame.jpg \
./scripts/pi/run_vision_nav_shadow.sh --once
```

Then:

```bash
VISION_NAV_RUNTIME_STATUS_ROOTS=$HOME/DroneTransfer/outgoing/neural-shadow \
./scripts/pi/read_runtime_status.sh
```

Proceed only if the log/status output is understandable and no safety issue is
present.

## MAVLink Telemetry Prop-Off Run

```bash
cd ~/Drone
VISION_NAV_MAVLINK_ENDPOINT=serial:/dev/ttyACM0:921600 \
./scripts/pi/capture_mavlink_bench.sh
```

Pass criteria:

- Pixhawk heartbeat, attitude, and GPS telemetry are captured as bench evidence
- no PX4 external-vision message is sent
- every learned runtime decision remains publication-disabled
- QGroundControl/PX4 shows no unexpected arming, mode, or motor behavior

## Evidence To Save

Create a support bundle:

```bash
cd ~/Drone
./scripts/pi/create_support_bundle.sh
```

The bundle should include:

- learned map/model release health
- learned shadow decision trace
- runtime status
- camera health
- PX4 parameter report
- MAVLink endpoint check output if available
- MAVLink telemetry health from runtime logs

## Do Not Proceed If

- propellers are installed
- Pixhawk wiring is uncertain
- QGroundControl cannot connect
- RC calibration is incomplete
- GPS/compass/power module status is unknown
- Pi cannot read MAVLink heartbeat/telemetry
- camera focus or mount is not stable
- map/model/calibration compatibility validation fails
- runtime output is low confidence but still being treated as valid
