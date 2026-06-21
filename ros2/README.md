# ROS 2 Launch Profiles

This folder contains lightweight ROS 2 launch profiles and a thin
`ament_python` wrapper that execute the existing Python runtime modules. The
goal is to make ROS 2 bench usage repeatable without moving the main runtime
code out of the repo package.

Run from the repository root with ROS 2 sourced:

```bash
source /opt/ros/humble/setup.bash
ros2 launch ros2/launch/terrain_nav_replay.launch.py \
  repo_root:=$(pwd) \
  pythonpath:=$(pwd)/src \
  log:=terrain-run/terrain_matches.jsonl
```

Without ROS 2 installed, export the same replay stream as a topic-oriented JSONL
bag directory:

```bash
vision-nav-ros2-replay-log \
  --log terrain-run/terrain_matches.jsonl \
  --export-rosbag-jsonl terrain-run/rosbag-jsonl
```

Add captured camera frames as bounded compressed-image topic records:

```bash
vision-nav-ros2-replay-log \
  --log terrain-run/terrain_matches.jsonl \
  --export-rosbag-jsonl terrain-run/rosbag-jsonl \
  --include-frame-topic \
  --frame-topic /vision_nav/camera/image/compressed
```

The frame export resolves relative `frame_path` values from the log directory
and writes `sensor_msgs/msg/CompressedImage` JSONL messages with base64
compressed bytes.

For tools that can read MCAP archives, install the optional package and export
the same ROS-shaped topic stream as JSON-encoded MCAP:

```bash
python -m pip install ".[rosbag]"
vision-nav-ros2-replay-log \
  --log terrain-run/terrain_matches.jsonl \
  --export-mcap terrain-run/vision-nav.mcap \
  --include-frame-topic
```

The MCAP path is optional. The JSONL export remains the no-extra-dependency
fallback for Pi and desktop smoke tests.

On a sourced ROS 2 workstation, write native serialized rosbag2 output:

```bash
source /opt/ros/humble/setup.bash
vision-nav-ros2-replay-log \
  --log terrain-run/terrain_matches.jsonl \
  --export-rosbag2 terrain-run/rosbag2-native \
  --include-frame-topic
```

The native export uses `rosbag2_py` and ROS message packages only when that flag
is selected.

For live Pi or desktop camera runtime:

```bash
source /opt/ros/humble/setup.bash
ros2 launch ros2/launch/terrain_nav_live.launch.py \
  repo_root:=$(pwd) \
  pythonpath:=$(pwd)/src \
  bundle:=mission_bundle \
  output_dir:=terrain-run
```

These launch files publish:

- `/vision_nav/odometry`
- `/diagnostics`

Direct MAVLink output remains a separate runtime option.

## Colcon Package Wrapper

`ros2/drone_vision_nav/` is a thin `ament_python` package for ROS 2
workstations that should use `ros2 run` or installed launch profiles. It
installs the existing repo Python runtime package and reuses the launch files in
`ros2/launch/`.

Example workspace setup:

```bash
mkdir -p ~/drone_ros2_ws/src
ln -s /path/to/Drone/ros2/drone_vision_nav ~/drone_ros2_ws/src/drone_vision_nav
cd ~/drone_ros2_ws
source /opt/ros/humble/setup.bash
colcon build --packages-select drone_vision_nav
source install/setup.bash
```

Then run:

```bash
ros2 run drone_vision_nav terrain_nav_replay \
  --log terrain-run/terrain_matches.jsonl \
  --publish

ros2 launch drone_vision_nav terrain_nav_live.launch.py \
  bundle:=mission_bundle \
  output_dir:=terrain-run
```
