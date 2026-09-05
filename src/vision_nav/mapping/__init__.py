from .correlation import FeatureMapPatch, NumpyFeatureMapCorrelator
from .schema import TileRecord

__all__ = [
    "FeatureMapPatch",
    "MapRelease",
    "NumpyFeatureMapCorrelator",
    "TileRecord",
    "check_map_release_compatibility",
    "load_map_release",
]


def __getattr__(name: str):
    if name in {"MapRelease", "check_map_release_compatibility", "load_map_release"}:
        from .release import MapRelease, check_map_release_compatibility, load_map_release

        return {
            "MapRelease": MapRelease,
            "check_map_release_compatibility": check_map_release_compatibility,
            "load_map_release": load_map_release,
        }[name]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
