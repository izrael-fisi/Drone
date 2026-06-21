from importlib.util import module_from_spec, spec_from_file_location
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def load_launch_module(name: str):
    path = ROOT / "ros2" / "launch" / name
    spec = spec_from_file_location(name.replace(".", "_"), path)
    module = module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_live_launch_profile_is_import_safe_without_ros_installed():
    module = load_launch_module("terrain_nav_live.launch.py")
    defaults = module.LAUNCH_ARGUMENT_DEFAULTS
    assert defaults["odometry_topic"] == "/vision_nav/odometry"
    assert defaults["diagnostics_topic"] == "/diagnostics"
    assert callable(module.generate_launch_description)


def test_replay_launch_profile_is_import_safe_without_ros_installed():
    module = load_launch_module("terrain_nav_replay.launch.py")
    defaults = module.LAUNCH_ARGUMENT_DEFAULTS
    assert defaults["log"] == "terrain-run/terrain_matches.jsonl"
    assert defaults["rate_hz"] == "2.0"
    assert callable(module.generate_launch_description)


def test_launch_profiles_execute_expected_modules():
    live_source = (ROOT / "ros2" / "launch" / "terrain_nav_live.launch.py").read_text()
    replay_source = (ROOT / "ros2" / "launch" / "terrain_nav_replay.launch.py").read_text()
    assert "vision_nav.run_terrain_loop" in live_source
    assert "--ros2-publish" in live_source
    assert "vision_nav.ros2_bridge" in replay_source
    assert "--publish" in replay_source
