from pathlib import Path

from vision_nav.bundle import (
    load_manifest,
    manifest_feature_options,
    manifest_features_path,
    manifest_georef,
    manifest_orthophoto_path,
)


def test_load_manifest_and_resolve_paths(tmp_path: Path):
    bundle = tmp_path / "bundle"
    bundle.mkdir()
    manifest = bundle / "manifest.json"
    manifest.write_text(
        """
{
  "bundle_id": "unit-test",
  "orthophoto": {
    "path": "ortho/map.png",
    "origin_lat": 40.0,
    "origin_lon": -75.0,
    "gsd_m": 0.2
  },
  "features": {
    "path": "features/map_features.npz",
    "method": "orb",
    "max_features": 123
  }
}
""".strip()
    )

    bundle_dir, loaded = load_manifest(bundle)

    assert bundle_dir == bundle
    assert manifest_orthophoto_path(bundle_dir, loaded) == bundle / "ortho/map.png"
    assert manifest_features_path(bundle_dir, loaded) == bundle / "features/map_features.npz"
    assert manifest_feature_options(loaded) == {"method": "orb", "max_features": 123}
    assert manifest_georef(loaded)["origin_lat"] == 40.0

