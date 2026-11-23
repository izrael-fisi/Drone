"""Validated module jobs using existing shadow/replay entrypoints.

No arbitrary command strings, model plugins, flight commands or automatic device
discovery. exec replaces this launcher so container stop reaches the job owner.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
import hashlib
from importlib import metadata
import json
import os
from pathlib import Path
import platform
import subprocess
import sys

from .config import ModuleConfig, load_config


REQUIRED_INPUTS = {
    "diagnostics": set(), "native_smoke": set(), "telemetry_bench": set(),
    "vio_replay": {"capture"},
    "learned_replay": {"session", "export_dir", "reference_map", "tile_layout", "calibration",
                       "retrieval_export", "retrieval_index", "local_verification_config", "native_vio_capture"},
}
DIRECTORIES = {"capture", "session", "export_dir", "retrieval_export", "native_vio_capture"}


def resolve_inputs(config: ModuleConfig, root: Path) -> dict[str, Path]:
    if set(config.job.inputs) != REQUIRED_INPUTS[config.job.kind]:
        raise ValueError(f"{config.job.kind} requires exactly {sorted(REQUIRED_INPUTS[config.job.kind])}")
    root = root.resolve()
    result = {}
    for name, value in config.job.inputs.items():
        path = (root / value).resolve(strict=True)
        if not path.is_relative_to(root):
            raise ValueError(f"Input escapes read-only input root: {name}")
        if not (path.is_dir() if name in DIRECTORIES else path.is_file()):
            raise ValueError(f"Wrong input type: {name}")
        result[name] = path
    return result


def command_for(config: ModuleConfig, inputs: dict[str, Path], output: Path,
                binary: Path, checkout: Path, *, propellers_removed: bool) -> list[str]:
    kind = config.job.kind
    if kind in {"native_smoke", "vio_replay", "learned_replay"}:
        from vision_nav.vio.openvins_replay import verify_binary
        verify_binary(binary, pose_window=True, stream_input=True)
    prefix = [sys.executable, "-m"]
    if kind == "diagnostics":
        return []
    if kind == "native_smoke":
        from vision_nav.vio.openvins_replay import verify_checkout
        verify_checkout(checkout)
        return prefix + ["vision_nav.deployment.smoke", "--binary", str(binary),
                         "--checkout", str(checkout), "--output", str(output)]
    if kind == "vio_replay":
        return prefix + ["vision_nav.vio.stream_replay", "--binary", str(binary),
                         "--capture", str(inputs["capture"]), "--output", str(output)]
    if kind == "learned_replay":
        args = prefix + ["vision_nav.neural.map_conditioned_replay"]
        for key, value in inputs.items():
            args += ["--" + key.replace("_", "-"), str(value)]
        return args + ["--native-vio-binary", str(binary), "--output-dir", str(output),
                       "--retrieval-top-k", str(config.job.retrieval_top_k)]
    if not propellers_removed:
        raise ValueError("Telemetry bench requires explicit --propellers-removed for this invocation")
    if config.flight_controller.autopilot != "px4":
        raise ValueError("Only PX4 bench evidence is implemented; other autopilots are metadata-only")
    return prefix + ["vision_nav.mavlink_bench", "--endpoint", config.flight_controller.endpoint(),
                     "--duration-s", str(config.job.duration_s), "--output-dir", str(output)]


def snapshot(config: ModuleConfig, command: list[str]) -> dict:
    packages = {}
    for package in ("drone-vision-nav", "numpy", "onnxruntime", "pymavlink", "rasterio"):
        try:
            packages[package] = metadata.version(package)
        except metadata.PackageNotFoundError:
            packages[package] = None
    encoded = json.dumps(config.model_dump(), sort_keys=True, separators=(",", ":")).encode()
    return {"schema_version": "vision_nav_module_launch_v1",
            "created_at_utc": datetime.now(timezone.utc).isoformat(),
            "module_config": config.model_dump(), "config_sha256": hashlib.sha256(encoded).hexdigest(),
            "command": command, "platform": {"os": platform.system(), "machine": platform.machine()},
            "packages": packages, "source_revision": os.environ.get("VISION_NAV_SOURCE_REVISION", "unrecorded"),
            "position_authority": False, "px4_external_vision": "disabled",
            "installation_binding": "declared_metadata_not_measured_or_capture_verified",
            "state": "validated_launch_not_runtime_readiness",
            "telemetry_writes": "TIMESYNC_probes_only" if config.job.kind == "telemetry_bench" else "none"}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("schema", "inspect", "run"))
    parser.add_argument("--config", type=Path, default=Path("/etc/vision-nav/module.json"))
    parser.add_argument("--inputs", type=Path, default=Path("/inputs"))
    parser.add_argument("--outputs", type=Path, default=Path("/outputs"))
    parser.add_argument("--run-id", default="run")
    parser.add_argument("--binary", type=Path, default=Path("/opt/vision-nav/bin/vision-nav-openvins"))
    parser.add_argument("--checkout", type=Path, default=Path("/opt/vision-nav/vendor/open_vins"))
    parser.add_argument("--propellers-removed", action="store_true")
    args = parser.parse_args(argv)
    if args.action == "schema":
        print(json.dumps(ModuleConfig.model_json_schema(), indent=2))
        return 0
    try:
        from pydantic import TypeAdapter
        from .config import Identifier
        run_id = TypeAdapter(Identifier).validate_python(args.run_id)
        config = load_config(args.config)
        inputs = resolve_inputs(config, args.inputs)
        root = args.outputs.resolve() / run_id
        if root.is_relative_to(args.inputs.resolve()) or args.inputs.resolve().is_relative_to(root):
            raise ValueError("Output must be separate from read-only inputs")
        if args.config.resolve().is_relative_to(root):
            raise ValueError("Output must be separate from configuration")
        command = command_for(config, inputs, root / "result", args.binary.resolve(), args.checkout.resolve(),
                              propellers_removed=args.propellers_removed)
        report = snapshot(config, command)
        if args.action == "inspect":
            print(json.dumps(report, indent=2))
            return 0
        root.mkdir(parents=True, exist_ok=False)
        (root / "launch.json").write_text(json.dumps(report, indent=2) + "\n")
        print(json.dumps(report), flush=True)
        if command:
            os.execv(sys.executable, command)
        return 0
    except (ValueError, OSError, RuntimeError, subprocess.SubprocessError) as exc:
        print(json.dumps({"state": "rejected", "reason": str(exc), "position_authority": False,
                          "px4_external_vision": "disabled"}), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
