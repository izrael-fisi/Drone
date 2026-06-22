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
    "mavlink_endpoint": "",
    "mavlink_message": "odometry",
    "mavlink_ev_delay_ms": "50",
    "mavlink_system_id": "1",
    "mavlink_component_id": "197",
    "mavlink_source_system": "42",
    "mavlink_source_component": "197",
    "external_position_min_rate_hz": "1.0",
    "external_position_max_latency_ms": "500.0",
    "external_position_max_horizontal_var_m2": "400.0",
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
            "--mavlink-endpoint",
            LaunchConfiguration("mavlink_endpoint"),
            "--mavlink-message",
            LaunchConfiguration("mavlink_message"),
            "--mavlink-ev-delay-ms",
            LaunchConfiguration("mavlink_ev_delay_ms"),
            "--mavlink-system-id",
            LaunchConfiguration("mavlink_system_id"),
            "--mavlink-component-id",
            LaunchConfiguration("mavlink_component_id"),
            "--mavlink-source-system",
            LaunchConfiguration("mavlink_source_system"),
            "--mavlink-source-component",
            LaunchConfiguration("mavlink_source_component"),
            "--external-position-min-rate-hz",
            LaunchConfiguration("external_position_min_rate_hz"),
            "--external-position-max-latency-ms",
            LaunchConfiguration("external_position_max_latency_ms"),
            "--external-position-max-horizontal-var-m2",
            LaunchConfiguration("external_position_max_horizontal_var_m2"),
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
