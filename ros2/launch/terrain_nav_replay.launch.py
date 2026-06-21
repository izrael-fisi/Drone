from __future__ import annotations


LAUNCH_ARGUMENT_DEFAULTS = {
    "python_executable": "python3",
    "repo_root": ".",
    "pythonpath": "src",
    "log": "terrain-run/terrain_matches.jsonl",
    "rate_hz": "2.0",
    "odometry_topic": "/vision_nav/odometry",
    "diagnostics_topic": "/diagnostics",
    "frame_id": "map",
    "child_frame_id": "base_link",
}


def generate_launch_description():
    from launch import LaunchDescription
    from launch.actions import DeclareLaunchArgument, ExecuteProcess
    from launch.substitutions import LaunchConfiguration

    declarations = [
        DeclareLaunchArgument(name, default_value=default)
        for name, default in LAUNCH_ARGUMENT_DEFAULTS.items()
    ]
    process = ExecuteProcess(
        cmd=[
            LaunchConfiguration("python_executable"),
            "-m",
            "vision_nav.ros2_bridge",
            "--log",
            LaunchConfiguration("log"),
            "--publish",
            "--odometry-topic",
            LaunchConfiguration("odometry_topic"),
            "--diagnostics-topic",
            LaunchConfiguration("diagnostics_topic"),
            "--frame-id",
            LaunchConfiguration("frame_id"),
            "--child-frame-id",
            LaunchConfiguration("child_frame_id"),
            "--rate-hz",
            LaunchConfiguration("rate_hz"),
        ],
        cwd=LaunchConfiguration("repo_root"),
        additional_env={"PYTHONPATH": LaunchConfiguration("pythonpath")},
        output="screen",
    )
    return LaunchDescription([*declarations, process])
