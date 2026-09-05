"""Versioned learned cross-view navigation contracts and Pi shadow runtime."""

import os

# The vehicle pipeline is offline. Disable ORT's uploader before any lazy model
# import initializes it; the late telemetry API cannot suppress initial events.
os.environ["ORT_DISABLE_TELEMETRY"] = "1"

from .profiles import PI5_CROSSVIEW_NAV_V1, InferenceProfile
from .releases import DatasetRelease, ModelRelease

__all__ = ["DatasetRelease", "InferenceProfile", "ModelRelease", "PI5_CROSSVIEW_NAV_V1"]
