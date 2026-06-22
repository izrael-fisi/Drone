# ROS 2 Runtime Adapter

ROS 2 is the preferred internal interface for the navigation estimate. The
runtime still works without ROS 2, but the repo now includes a bridge layer that
turns terrain-match logs into ROS-compatible odometry and diagnostics.

## What The Adapter Publishes

The adapter maps accepted local ENU measurements into `nav_msgs/Odometry`:

- ROS `x`: local east
- ROS `y`: local north
- ROS `z`: local up
- covariance index `0`: east variance
- covariance index `7`: north variance
- covariance index `14`: up variance
- covariance index `35`: yaw variance

It also maps `external_position_health` snapshots into diagnostic status data:

- `healthy` -> OK
- `warming_up` or non-fatal degraded output -> WARN
- degraded with no sent measurements -> ERROR
- `inactive` -> STALE

Rejected terrain matches are not published as odometry. They still produce a
diagnostic entry so replay logs show why the output stopped.

## JSON Replay Without ROS Installed

This command works in the regular Python environment:

```bash
vision-nav-ros2-replay-log \
  --log ~/DroneTransfer/outgoing/terrain-match/terrain_matches.jsonl
```

It prints the odometry and diagnostic records that would be published. This is
the safest first check on a Mac or a Pi that does not have ROS 2 sourced.

To create an offline topic-oriented replay artifact without ROS 2 installed:

```bash
vision-nav-ros2-replay-log \
  --log ~/DroneTransfer/outgoing/terrain-match/terrain_matches.jsonl \
  --export-rosbag-jsonl ~/DroneTransfer/outgoing/terrain-match/rosbag-jsonl
```

This writes `metadata.json` and `messages.jsonl` with ROS message type names,
topics, timestamps, and payloads. It is intentionally dependency-free; convert
it to native rosbag2 or MCAP on a workstation when that workflow is needed.

To include the captured camera frames referenced by each `frame_path` in the
runtime log, add the compressed frame topic export:

```bash
vision-nav-ros2-replay-log \
  --log ~/DroneTransfer/outgoing/terrain-match/terrain_matches.jsonl \
  --export-rosbag-jsonl ~/DroneTransfer/outgoing/terrain-match/rosbag-jsonl \
  --include-frame-topic \
  --frame-topic /vision_nav/camera/image/compressed \
  --camera-frame-id down_camera
```

Frame paths are resolved relative to the log directory unless `--frame-root` is
provided. Frames larger than `--max-frame-bytes` are skipped so support exports
do not accidentally embed full map assets or huge captures. The JSONL message
uses ROS type `sensor_msgs/msg/CompressedImage` and stores the compressed bytes
as base64 so the artifact remains plain JSON.

## Native Bag Exports

On a sourced ROS 2 workstation, export the same replay stream as native rosbag2
serialized messages and create the final CLI review artifact:

```bash
source /opt/ros/humble/setup.bash
./scripts/dev/run_rosbag2_cli_review.sh
```

The wrapper imports `rosbag2_py`, `rclpy.serialization`, `nav_msgs`,
`diagnostic_msgs`, and `sensor_msgs` only during the native export. It writes
serialized `nav_msgs/msg/Odometry`,
`diagnostic_msgs/msg/DiagnosticArray`, and optional
`sensor_msgs/msg/CompressedImage` topics, plus
`vision_nav_rosbag2_metadata.json` for the support tooling, then captures
`ros2 bag info` into `rosbag2-cli-review.json`.

Use the dependency-free JSONL export on the Pi or on machines without ROS 2.
For MCAP-capable tools that do not require native ROS serialization, install the
optional Python extra and export a JSON-encoded MCAP archive:

```bash
python -m pip install ".[rosbag]"
vision-nav-ros2-replay-log \
  --log ~/DroneTransfer/outgoing/terrain-match/terrain_matches.jsonl \
  --export-mcap ~/DroneTransfer/outgoing/terrain-match/vision-nav.mcap \
  --include-frame-topic
```

## Publish With ROS 2

On a ROS 2 machine, source the ROS environment first:

```bash
source /opt/ros/humble/setup.bash
```

Then replay a log into ROS 2 topics:

```bash
vision-nav-ros2-replay-log \
  --log ~/DroneTransfer/outgoing/terrain-match/terrain_matches.jsonl \
  --publish \
  --odometry-topic /vision_nav/odometry \
  --diagnostics-topic /diagnostics \
  --rate-hz 2.0
```

The publish mode imports `rclpy`, `nav_msgs`, and `diagnostic_msgs` only when
`--publish` is used. The normal Python smoke tests do not require ROS 2 packages.

## Live Terrain Runtime Publishing

The terrain runtime can publish live odometry and diagnostics while it captures
and matches frames:

```bash
VISION_NAV_ROS2_PUBLISH=1 \
VISION_NAV_ROS2_ODOMETRY_TOPIC=/vision_nav/odometry \
VISION_NAV_ROS2_DIAGNOSTICS_TOPIC=/diagnostics \
./scripts/pi/run_terrain_nav_loop.sh
```

Equivalent direct Python arguments:

```bash
vision-nav-run-terrain-loop \
  --bundle mission_bundle \
  --output-dir terrain-run \
  --ros2-publish \
  --ros2-odometry-topic /vision_nav/odometry \
  --ros2-diagnostics-topic /diagnostics
```

Live publishing does not require MAVLink output to be enabled. When MAVLink
stream health exists, diagnostics include that health. Otherwise diagnostics are
derived directly from the terrain match status.

## Launch Profiles

The repo includes lightweight ROS 2 launch files under `ros2/launch/`. ROS 2
launch files are meant to automate starting multiple nodes or processes with one
command; these profiles start the existing Python runtime processes.

Replay a saved log:

```bash
source /opt/ros/humble/setup.bash
ros2 launch ros2/launch/terrain_nav_replay.launch.py \
  repo_root:=$(pwd) \
  pythonpath:=$(pwd)/src \
  log:=terrain-run/terrain_matches.jsonl
```

Run the live terrain runtime with ROS 2 publishing:

```bash
source /opt/ros/humble/setup.bash
ros2 launch ros2/launch/terrain_nav_live.launch.py \
  repo_root:=$(pwd) \
  pythonpath:=$(pwd)/src \
  bundle:=mission_bundle \
  output_dir:=terrain-run
```

For PX4 SITL receiver checks, the same live launch profile can also enable the
direct MAVLink `ODOMETRY` output path:

```bash
source /opt/ros/humble/setup.bash
ros2 launch ros2/launch/terrain_nav_live.launch.py \
  repo_root:=$(pwd) \
  pythonpath:=$(pwd)/src \
  bundle:=mission_bundle \
  output_dir:=terrain-run \
  mavlink_endpoint:=udp:14550 \
  mavlink_message:=odometry \
  external_position_min_rate_hz:=1.0
```

Leave `mavlink_endpoint` empty for ROS-only publishing. When set, the runtime
records MAVLink stream health in diagnostics and in `terrain_matches.jsonl`.

These are repo-local launch profiles. If this project later needs colcon-native
packaging, the repo now includes a thin `ament_python` package wrapper under
`ros2/drone_vision_nav/`. Build it from a ROS 2 workspace that has this repo
checked out:

```bash
mkdir -p ~/drone_ros2_ws/src
ln -s /path/to/Drone/ros2/drone_vision_nav ~/drone_ros2_ws/src/drone_vision_nav
cd ~/drone_ros2_ws
source /opt/ros/humble/setup.bash
colcon build --packages-select drone_vision_nav
source install/setup.bash
```

The wrapper installs the existing Python `vision_nav` runtime package, the
repo launch files, and two console scripts:

```bash
ros2 run drone_vision_nav terrain_nav_replay \
  --log /path/to/terrain_matches.jsonl \
  --publish

ros2 run drone_vision_nav terrain_nav_live \
  --bundle /path/to/mission_bundle \
  --output-dir terrain-run \
  --ros2-publish
```

You can also launch the installed profiles:

```bash
ros2 launch drone_vision_nav terrain_nav_replay.launch.py log:=terrain-run/terrain_matches.jsonl
ros2 launch drone_vision_nav terrain_nav_live.launch.py bundle:=mission_bundle output_dir:=terrain-run
```

## PX4 Bridge Direction

For PX4 SITL and hardware tests, the ROS path should eventually be:

```text
terrain matcher result
  -> nav_msgs/Odometry
    -> PX4 VehicleOdometry or MAVLink ODOMETRY bridge
      -> PX4 EKF2 external-vision fusion
```

Direct MAVLink output remains available for simple Pi deployments and early
bench tests.

On Raspberry Pi modules that should use the PX4 uXRCE-DDS ROS 2 path, run:

```bash
./scripts/pi/check_micro_xrce_dds_agent.sh
```

Set `VISION_NAV_REQUIRE_XRCE=1` when the setup should fail if
`MicroXRCEAgent` is missing. The script reports the detected agent path,
validates the selected UDP or serial transport settings, and prints the launch
command for the expected PX4 bridge path.
