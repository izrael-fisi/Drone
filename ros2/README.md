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
