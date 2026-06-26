from __future__ import annotations

from copy import deepcopy
from typing import Any


RUNTIME_PROFILES: dict[str, dict[str, Any]] = {
    "pi5_full": {
        "id": "pi5_full",
        "label": "Raspberry Pi 5 full",
        "compute_target": "raspberry_pi_5_16gb",
        "feature_method": "orb",
        "max_features": 3000,
        "max_tile_candidates": 64,
        "target_frame_rate_hz": 1.0,
        "notes": "Default field profile for Pi 5 16GB with full terrain tile search.",
    },
    "pi5_low_memory": {
        "id": "pi5_low_memory",
        "label": "Raspberry Pi low memory",
        "compute_target": "raspberry_pi_low_memory",
        "feature_method": "orb",
        "max_features": 1600,
        "max_tile_candidates": 24,
        "target_frame_rate_hz": 0.5,
        "notes": "Reduced tile candidates, features, and frame rate for constrained devices.",
    },
    "desktop_high_compute": {
        "id": "desktop_high_compute",
        "label": "Desktop high compute",
        "compute_target": "desktop_gpu_optional",
        "feature_method": "akaze",
        "max_features": 6000,
        "max_tile_candidates": 128,
        "target_frame_rate_hz": 2.0,
        "notes": "Desktop validation profile with room for SuperPoint/LightGlue later.",
    },
}


CAMERA_PROFILES: dict[str, dict[str, Any]] = {
    "rgb_global_shutter": {
        "id": "rgb_global_shutter",
        "label": "RGB global shutter",
        "modality": "rgb",
        "shutter": "global",
        "status": "operational",
        "metadata_ready": True,
        "notes": "Primary v1 camera path for downward terrain matching.",
    },
    "rgb_rolling_shutter": {
        "id": "rgb_rolling_shutter",
        "label": "RGB rolling shutter",
        "modality": "rgb",
        "shutter": "rolling",
        "status": "experimental",
        "metadata_ready": True,
        "notes": "Usable for metadata and bench checks; verify vibration and motion blur before field use.",
    },
    "thermal_low_res": {
        "id": "thermal_low_res",
        "label": "Thermal low resolution",
        "modality": "thermal",
        "shutter": "unknown",
        "status": "experimental",
        "metadata_ready": True,
        "notes": "Future metadata profile; not a v1 terrain matching path.",
    },
    "eo_generic": {
        "id": "eo_generic",
        "label": "EO generic",
        "modality": "eo",
        "shutter": "unknown",
        "status": "experimental",
        "metadata_ready": True,
        "notes": "Future electro-optical metadata profile.",
    },
}


def runtime_profile(profile_id: str | None = None) -> dict[str, Any]:
    key = profile_id or "pi5_full"
    if key not in RUNTIME_PROFILES:
        raise ValueError(f"Unsupported runtime profile: {key}")
    return deepcopy(RUNTIME_PROFILES[key])


def camera_profile(profile_id: str | None = None) -> dict[str, Any]:
    key = profile_id or "rgb_global_shutter"
    if key not in CAMERA_PROFILES:
        raise ValueError(f"Unsupported camera profile: {key}")
    return deepcopy(CAMERA_PROFILES[key])


def hardware_profile(
    *,
    runtime: dict[str, Any] | None = None,
    camera: dict[str, Any] | None = None,
    module_weight_g: float | None = None,
    estimated_bom_usd: float | None = None,
    camera_cost_usd: float | None = None,
    sensor_compliance_notes: str | None = None,
    mount_vibration_notes: str | None = None,
) -> dict[str, Any]:
    runtime = runtime or runtime_profile()
    camera = camera or camera_profile()
    return {
        "compute_target": runtime.get("compute_target"),
        "runtime_profile": runtime.get("id"),
        "camera_profile": camera.get("id"),
        "module_weight_g": module_weight_g,
        "estimated_bom_usd": estimated_bom_usd,
        "camera_cost_usd": camera_cost_usd,
        "sensor_compliance_notes": sensor_compliance_notes or "",
        "mount_vibration_notes": mount_vibration_notes or "",
    }


def parse_float_or_none(value: Any) -> float | None:
    if value in {None, ""}:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
