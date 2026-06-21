from pathlib import Path

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from vision_nav.build_terrain_bundle import build_terrain_bundle
from vision_nav.terrain_bundle import load_terrain_bundle, summarize_terrain_bundle
from vision_nav.terrain_estimator import TerrainEstimator
from vision_nav.terrain_matcher import TerrainMatchOptions, match_terrain_frame
from vision_nav.terrain_tiles import query_tiles, tile_origins


def write_synthetic_bundle(root: Path) -> tuple[Path, Path]:
    bundle = root / "bundle"
    (bundle / "ortho").mkdir(parents=True)
    image = np.zeros((420, 420), dtype=np.uint8)
    cv2.rectangle(image, (40, 40), (140, 160), 255, 3)
    cv2.circle(image, (300, 100), 45, 220, 4)
    cv2.line(image, (30, 320), (390, 280), 180, 5)
    cv2.putText(image, "TERRAIN", (70, 380), cv2.FONT_HERSHEY_SIMPLEX, 1.2, 210, 3)
    map_path = bundle / "ortho" / "map.png"
    cv2.imwrite(str(map_path), image)
    (bundle / "manifest.json").write_text(
        """
{
  "bundle_id": "terrain-test",
  "orthophoto": {
    "path": "ortho/map.png",
    "origin_lat": 40.0,
    "origin_lon": -75.0,
    "origin_pixel_x": 0.0,
    "origin_pixel_y": 0.0,
    "gsd_m": 0.5,
    "rotation_deg": 0.0,
    "georef_source": "unit-test",
    "georef_confidence": 1.0,
    "georef_crs": "EPSG:4326"
  },
  "features": {
    "path": "features/map_features.npz",
    "method": "orb",
    "max_features": 800
  }
}
""".strip()
    )
    frame_path = root / "frame.png"
    cv2.imwrite(str(frame_path), image[0:220, 0:220])
    return bundle, frame_path


def test_tile_origins_cover_end_without_duplicates():
    assert tile_origins(100, 128, 16) == [0]
    assert tile_origins(300, 128, 16)[-1] == 172
    assert len(set(tile_origins(300, 128, 16))) == len(tile_origins(300, 128, 16))


def test_build_terrain_bundle_and_query_tiles(tmp_path: Path):
    bundle, _ = write_synthetic_bundle(tmp_path)
    result = build_terrain_bundle(str(bundle), tile_size_px=220, overlap_px=40)

    loaded = load_terrain_bundle(bundle)
    summary = summarize_terrain_bundle(bundle)
    tiles = query_tiles(loaded.tile_index_path, loaded.bundle_dir, max_candidates=4)

    assert result["terrain_bundle"]["tile_count"] >= 4
    assert loaded.has_tile_index
    assert summary["status"] == "passed"
    assert tiles
    assert tiles[0].descriptor_path.exists()


def test_match_terrain_frame_accepts_synthetic_crop(tmp_path: Path):
    bundle, frame_path = write_synthetic_bundle(tmp_path)
    build_terrain_bundle(str(bundle), tile_size_px=240, overlap_px=80)

    result = match_terrain_frame(
        load_terrain_bundle(bundle),
        str(frame_path),
        TerrainMatchOptions(max_features=1000, ratio=0.9, min_inliers=6, max_candidates=8),
    )

    assert result["status"] == "accepted"
    assert result["tile_id"].startswith("tile_")
    assert result["local_enu_m"]["x"] is not None
    assert result["lat_lon"]["lat"] is not None
    assert result["covariance"]["z_m2"] is None


def test_terrain_estimator_updates_and_inflates_covariance():
    estimator = TerrainEstimator(process_noise_m2_per_s=2.0)
    result = estimator.update_from_match(
        {
            "timestamp_us": 1_000_000,
            "status": "accepted",
            "local_enu_m": {"x": 3.0, "y": 4.0, "z": None},
            "confidence": 0.8,
            "scale_confidence": 0.7,
            "covariance": {"x_m2": 1.0, "y_m2": 2.0, "z_m2": None, "yaw_rad2": None},
        }
    )
    assert result["estimator"]["initialized"] is True
    estimator.propagate_time(2_000_000)
    assert estimator.state.covariance_x_m2 is not None
    assert estimator.state.covariance_x_m2 > 1.0
