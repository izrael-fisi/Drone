# ROS 2 Launch Profiles

This folder contains lightweight ROS 2 launch profiles that execute the existing
Python runtime modules. They are intentionally not a colcon package yet; the
current goal is to make ROS 2 bench usage repeatable without restructuring the
repo.

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
