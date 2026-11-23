"""Strict portable job configuration; installations never choose algorithms."""

from __future__ import annotations

import json
from pathlib import Path, PurePosixPath
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator


Identifier = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.-]{0,95}$")]


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, frozen=True)


class Installation(StrictModel):
    installation_id: Identifier
    airframe: Annotated[str, StringConstraints(min_length=1, max_length=120)]
    flight_computer_id: Identifier
    camera_module_id: Identifier
    # A declared identity is not a measured transform or calibration approval.
    calibration_id: Identifier | None = None
    mount_notes: Annotated[str, StringConstraints(max_length=1000)] = "Unmeasured installation"


class FlightController(StrictModel):
    protocol: Literal["mavlink2"] = "mavlink2"
    autopilot: Literal["px4", "ardupilot", "unverified"] = "unverified"
    transport: Literal["disabled", "serial", "udp_listen"] = "disabled"
    serial_device: Literal["/dev/flight-controller"] = "/dev/flight-controller"
    baud: Annotated[int, Field(ge=9600, le=3000000)] = 921600
    udp_port: Annotated[int, Field(ge=1024, le=65535)] = 14550

    def endpoint(self) -> str:
        if self.transport == "serial":
            return f"serial:{self.serial_device}:{self.baud}"
        if self.transport == "udp_listen":
            return f"udp:0.0.0.0:{self.udp_port}"
        raise ValueError("Flight-controller transport is disabled")


class Job(StrictModel):
    kind: Literal["diagnostics", "native_smoke", "vio_replay", "learned_replay", "telemetry_bench"] = "diagnostics"
    # All paths are relative to a read-only input mount, never to the host home.
    inputs: dict[str, str] = Field(default_factory=dict)
    duration_s: Annotated[float, Field(gt=0, le=120, allow_inf_nan=False)] = 15.0
    retrieval_top_k: Annotated[int, Field(ge=1, le=4)] = 4

    @field_validator("inputs")
    @classmethod
    def portable_paths(cls, values):
        if len(values) > 10:
            raise ValueError("Too many job inputs")
        for value in values.values():
            path = PurePosixPath(value)
            if (not value or len(value) > 512 or path.is_absolute() or ".." in path.parts
                    or "\\" in value or ":" in value or "\x00" in value or value == "."):
                raise ValueError("Inputs must be portable relative paths without traversal")
        return values


class ModuleConfig(StrictModel):
    schema_version: Literal["vision_nav_module_deployment_v1"]
    module_id: Identifier
    profile: Literal["pi5_crossview_nav_v1"] = "pi5_crossview_nav_v1"
    execution_provider: Literal["onnxruntime_cpu"] = "onnxruntime_cpu"
    shadow_only: Literal[True] = True
    installation: Installation
    flight_controller: FlightController = Field(default_factory=FlightController)
    job: Job = Field(default_factory=Job)

    @field_validator("shadow_only", mode="before")
    @classmethod
    def explicit_shadow(cls, value):
        if value is not True:
            raise ValueError("This deployment only permits explicit shadow-only operation")
        return value


def load_config(path: Path) -> ModuleConfig:
    with path.open("rb") as stream:
        raw = stream.read(32769)
    if len(raw) > 32768:
        raise ValueError("Deployment config exceeds 32 KiB")

    def unique_pairs(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError(f"Duplicate configuration key: {key}")
            result[key] = value
        return result

    return ModuleConfig.model_validate(json.loads(raw, object_pairs_hook=unique_pairs))
