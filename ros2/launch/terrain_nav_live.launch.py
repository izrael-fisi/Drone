from __future__ import annotations


LAUNCH_ARGUMENT_DEFAULTS = {
    "python_executable": "python3",
    "repo_root": ".",
    "pythonpath": "src",
    "bundle": "mission_bundle",
    "output_dir": "terrain-run",
    "count": "0",
    "interval_s": "1.0",
    "width": "1456",
    "height": "1088",
    "timeout_ms": "1000",
    "max_candidates": "64",
    "search_radius_m": "80.0",
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
            "vision_nav.run_terrain_loop",
            "--bundle",
            LaunchConfiguration("bundle"),
            "--output-dir",
            LaunchConfiguration("output_dir"),
            "--count",
            LaunchConfiguration("count"),
            "--interval-s",
            LaunchConfiguration("interval_s"),
            "--width",
            LaunchConfiguration("width"),
            "--height",
            LaunchConfiguration("height"),
            "--timeout-ms",
            LaunchConfiguration("timeout_ms"),
            "--max-candidates",
            LaunchConfiguration("max_candidates"),
            "--search-radius-m",
            LaunchConfiguration("search_radius_m"),
            "--ros2-publish",
            "--ros2-odometry-topic",
            LaunchConfiguration("odometry_topic"),
            "--ros2-diagnostics-topic",
            LaunchConfiguration("diagnostics_topic"),
            "--ros2-frame-id",
            LaunchConfiguration("frame_id"),
            "--ros2-child-frame-id",
            LaunchConfiguration("child_frame_id"),
        ],
        cwd=LaunchConfiguration("repo_root"),
        additional_env={"PYTHONPATH": LaunchConfiguration("pythonpath")},
        output="screen",
    )
    return LaunchDescription([*declarations, process])
