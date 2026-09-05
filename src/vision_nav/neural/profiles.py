"""The one vehicle-selectable learned navigation profile."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class InferenceProfile:
    profile_id: str
    display_name: str
    global_retriever: str
    local_correspondence: str
    temporal_student: str
    geometry_modes: tuple[str, ...]
    supported_providers: tuple[str, ...]
    default_provider: str
    recovery_cadence_s: float
    keyframe_cadence_s: float
    active_tile_limit: int
    shadow_only: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


PI5_CROSSVIEW_NAV_V1 = InferenceProfile(
    profile_id="pi5_crossview_nav_v1",
    display_name="Pi 5 Cross-View Navigation v1",
    global_retriever="MegaLoc",
    local_correspondence="XFeat + LighterGlue",
    temporal_student="distilled_temporal_bev_bearing_v1",
    geometry_modes=("pnp_3d", "homography_restricted", "stereo_ready"),
    supported_providers=("onnxruntime_cpu", "hailo_ai_hat_optional"),
    default_provider="onnxruntime_cpu",
    recovery_cadence_s=2.0,
    keyframe_cadence_s=0.5,
    active_tile_limit=24,
)


def load_profile(profile_id: str) -> InferenceProfile:
    if profile_id != PI5_CROSSVIEW_NAV_V1.profile_id:
        raise ValueError(
            f"Unsupported vehicle profile '{profile_id}'. "
            f"The active profile is {PI5_CROSSVIEW_NAV_V1.profile_id}."
        )
    return PI5_CROSSVIEW_NAV_V1
