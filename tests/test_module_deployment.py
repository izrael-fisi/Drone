"""Portable installation boundary, fail-closed dispatch and container contracts."""

import json
import os
from pathlib import Path
import subprocess
import sys

import pytest
import yaml
from pydantic import ValidationError

from vision_nav.deployment.config import ModuleConfig, load_config
from vision_nav.deployment.cli import command_for, main, resolve_inputs, snapshot


ROOT = Path(__file__).resolve().parents[1]


def example(name="generic.json"):
    return json.loads((ROOT / "config/module" / name).read_text())


def test_machine_readable_schema_needs_no_devices_or_config(capsys):
    assert main(["schema", "--config", "/absent/config.json"]) == 0
    schema = json.loads(capsys.readouterr().out)
    assert schema["additionalProperties"] is False
    assert schema["properties"]["profile"]["const"] == "pi5_crossview_nav_v1"


@pytest.mark.parametrize("path", sorted((ROOT / "config/module").glob("*.json")))
def test_examples_are_strict_shadow_configs(path):
    config = load_config(path)
    assert config.shadow_only is True
    assert config.profile == "pi5_crossview_nav_v1"


def test_airframe_is_metadata_not_a_navigation_algorithm():
    configs = [ModuleConfig.model_validate(example(n)) for n in
               ("generic.json", "holybro-x500-v2.json", "holybro-x650-v2.json")]
    for config in configs:
        assert resolve_inputs(config, Path("/unused")) == {}
        assert command_for(config, {}, Path("/outputs"), Path("/unused"), Path("/unused"),
                           propellers_removed=False) == []
    assert len({c.installation.airframe for c in configs}) == 3
    data = example()
    data["installation"]["airframe"] = "Independent research aircraft"
    assert ModuleConfig.model_validate(data).installation.airframe == "Independent research aircraft"


@pytest.mark.parametrize("field,value", [
    ("shadow_only", False), ("shadow_only", 1), ("shadow_only", "true"),
    ("profile", "orb"), ("execution_provider", "cuda"),
    ("schema_version", "v99"), ("module_id", "name;command"), ("publish", True),
])
def test_invalid_or_flight_configuration_rejected(field, value):
    data = example()
    data[field] = value
    with pytest.raises(ValidationError):
        ModuleConfig.model_validate(data)


@pytest.mark.parametrize("value", ["../escape", "/root/capture", "C:\\capture", "a/../../b", "a\x00b", ".", ""])
def test_nonportable_paths_rejected(value):
    data = example("vio-replay.json")
    data["job"]["inputs"]["capture"] = value
    with pytest.raises(ValidationError):
        ModuleConfig.model_validate(data)


def test_json_duplicate_keys_and_oversize_rejected(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"shadow_only":true,"shadow_only":false}')
    with pytest.raises(ValueError, match="Duplicate"):
        load_config(path)
    path.write_bytes(b" " * 32769)
    with pytest.raises(ValueError, match="32 KiB"):
        load_config(path)


def test_unknown_job_options_rejected():
    data = example()
    data["job"]["command"] = "sh"
    with pytest.raises(ValidationError):
        ModuleConfig.model_validate(data)


def test_input_inventory_missing_wrong_type_and_symlink_escape(tmp_path):
    config = ModuleConfig.model_validate(example("vio-replay.json"))
    inputs = tmp_path / "inputs"
    inputs.mkdir()
    with pytest.raises(FileNotFoundError):
        resolve_inputs(config, inputs)
    path = inputs / "sealed-vio-capture"
    path.write_text("not a directory")
    with pytest.raises(ValueError, match="Wrong input type"):
        resolve_inputs(config, inputs)
    path.unlink()
    path.symlink_to(tmp_path, target_is_directory=True)
    with pytest.raises(ValueError, match="escapes"):
        resolve_inputs(config, inputs)
    path.unlink()
    path.mkdir()
    assert resolve_inputs(config, inputs)["capture"] == path
    bad = example("vio-replay.json")
    bad["job"]["inputs"]["extra"] = "other"
    with pytest.raises(ValueError, match="requires exactly"):
        resolve_inputs(ModuleConfig.model_validate(bad), inputs)


def test_telemetry_requires_invocation_attestation_and_implemented_controller():
    data = example("serial-bench.json")
    config = ModuleConfig.model_validate(data)
    def command(c, removed):
        return command_for(c, {}, Path("/out"), Path("/unused"), Path("/unused"), propellers_removed=removed)
    with pytest.raises(ValueError, match="propellers"):
        command(config, False)
    actual = command(config, True)
    assert actual[2] == "vision_nav.mavlink_bench"
    assert "serial:/dev/flight-controller:921600" in actual
    assert snapshot(config, actual)["telemetry_writes"] == "TIMESYNC_probes_only"
    for family in ("ardupilot", "unverified"):
        data["flight_controller"]["autopilot"] = family
        with pytest.raises(ValueError, match="Only PX4"):
            command(ModuleConfig.model_validate(data), True)
    data["flight_controller"].update(autopilot="px4", transport="disabled")
    with pytest.raises(ValueError, match="disabled"):
        command(ModuleConfig.model_validate(data), True)
    data["flight_controller"].update(transport="udp_listen")
    assert "udp:0.0.0.0:14550" in command(ModuleConfig.model_validate(data), True)


def test_diagnostic_launch_is_auditable_not_readiness(tmp_path):
    config = ROOT / "config/module/generic.json"
    args = ["run", "--config", str(config), "--inputs", str(tmp_path / "inputs"),
            "--outputs", str(tmp_path / "outputs"), "--run-id", "first"]
    assert main(args) == 0
    path = tmp_path / "outputs/first/launch.json"
    report = json.loads(path.read_text())
    assert report["position_authority"] is False
    assert report["command"] == []
    assert report["state"] == "validated_launch_not_runtime_readiness"
    assert report["module_config"]["installation"]["calibration_id"] is None
    before = path.read_bytes()
    assert main(args) == 2
    assert path.read_bytes() == before
    args[0] = "inspect"
    assert main(args) == 0


def test_output_cannot_overlap_input_or_escape_run_root(tmp_path):
    args = ["run", "--config", str(ROOT / "config/module/generic.json"),
            "--inputs", str(tmp_path), "--outputs", str(tmp_path)]
    assert main(args) == 2
    assert main(args + ["--run-id", "../escape"]) == 2


def test_dispatch_to_existing_learned_path_without_model_execution(monkeypatch):
    import vision_nav.vio.openvins_replay as native
    calls = []
    monkeypatch.setattr(native, "verify_binary", lambda *a, **k: calls.append((a, k)))
    config = ModuleConfig.model_validate(example("learned-replay.json"))
    inputs = {k: Path("/inputs") / v for k, v in config.job.inputs.items()}
    command = command_for(config, inputs, Path("/output/result"), Path("/bin/native"),
                          Path("/checkout"), propellers_removed=False)
    assert command[2] == "vision_nav.neural.map_conditioned_replay"
    assert "--native-vio-capture" in command and "--local-verification-config" in command
    assert "--candidate-tile" not in command
    assert calls == [((Path("/bin/native"),), {"pose_window": True, "stream_input": True})]


def test_compose_is_offline_least_privilege_and_preserves_desktop_dev():
    compose = yaml.safe_load((ROOT / "docker/module/compose.yaml").read_text())
    for name in ("module", "research"):
        service = compose["services"][name]
        assert service["network_mode"] == "none"
        assert service["read_only"] and service["init"]
        assert service["cap_drop"] == ["ALL"]
        assert service["tmpfs"] == ["/tmp:rw,nosuid,nodev,size=512m"]
        assert "devices" not in service and "privileged" not in service
        assert service["volumes"][0]["read_only"] and service["volumes"][1]["read_only"]
        assert service["build"]["target"] in {"runtime", "research-native"}
    serial = yaml.safe_load((ROOT / "docker/module/serial-bench.yaml").read_text())
    assert compose["services"]["gcs"]["tmpfs"] == ["/tmp:rw,nosuid,nodev,size=64m"]
    assert len(serial["services"]["module"]["devices"]) == 1
    assert not serial["services"]["module"].get("privileged")
    desktop = yaml.safe_load((ROOT / "docker/desktop/docker-compose.yml").read_text())
    assert desktop["services"]["desktop-web"]["build"]["target"] == "development"


def test_actual_module_native_smoke_subprocess(tmp_path):
    binary = os.environ.get("VISION_NAV_OPENVINS_BINARY")
    checkout = ROOT / "research/vendor/open_vins"
    if not binary or not checkout.exists():
        pytest.skip("Actual pinned native executable and source required")
    result = subprocess.run([sys.executable, "-m", "vision_nav.deployment.cli", "run",
        "--config", str(ROOT / "config/module/native-smoke.json"), "--inputs", str(tmp_path / "inputs"),
        "--outputs", str(tmp_path / "outputs"), "--run-id", "smoke",
        "--binary", str(Path(binary).resolve()), "--checkout", str(checkout)],
        capture_output=True, text=True, timeout=90)
    assert result.returncode == 0, result.stderr + result.stdout[-4000:]
    report = json.loads((tmp_path / "outputs/smoke/result/vio/report.json").read_text())
    assert report["complete"] and report["position_authority"] is False
    assert report["worker"]["completed_batch_count"] == 160
    assert (tmp_path / "outputs/smoke/launch.json").is_file()
