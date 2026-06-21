# Terrain Vision Navigation

This repo supports an additive terrain navigation path for GNSS-denied testing
with only:

- downward camera frames
- onboard IMU attitude/motion context when available
- optional PX4 barometer telemetry for relative vertical confidence
- preloaded georeferenced map bundles

No GNSS, internet maps, rangefinder, or cloud inference is required for the v1
runtime path. Barometer input is optional; if it is absent, vertical position
is left unset.

## Bundle Layout

The terrain builder keeps the original single-image bundle files and adds a
tiled index:

```text
mission_bundle/
  manifest.json
  manifest.stac.json
  ortho/map.png
  elevation/dem.tif                      # optional
  elevation/dsm.tif                      # optional
  imagery/tiles/
  index/tiles.sqlite
  index/descriptors/
  features/map_features.npz
  calibration/down_camera.yaml
  calibration/camera_to_body.yaml
  config/terrain_nav.yaml
  bundle_health.json
  checksums.sha256
```

`features/map_features.npz` remains for legacy matching. The terrain runtime
uses `index/tiles.sqlite` to select candidate map tiles, then loads per-tile
descriptor files from `index/descriptors/`.

## Build And Validate

```bash
vision-nav-build-terrain-bundle --bundle mission_bundle --write-checksums
vision-nav-validate-terrain-bundle --bundle mission_bundle
vision-nav-map-health --bundle mission_bundle --json
```

The builder updates `manifest.json` with `terrain_bundle` metadata, writes a
small STAC-style manifest, creates `config/terrain_nav.yaml`, and writes
`bundle_health.json`. The health report checks georeference completeness, CRS,
GSD, raster header metadata, lightweight COG/GeoTIFF readiness, STAC asset
paths, tile-index readiness, feature count, first-pass feature-density quality,
estimated Pi runtime cost, local map bounds, source provenance, and checksum
status. Missing checksums are reported without failing the health report; invalid
checksums are reported as errors. The generated `bundle_health.json` file is
excluded from bundle checksums so regenerating the report does not create a
self-referential checksum mismatch.

Optional DEM/DSM rasters can be placed at `elevation/dem.tif` and
`elevation/dsm.tif` before building the terrain bundle. The builder declares
those assets in `manifest.json`, `manifest.stac.json`, and
`config/terrain_nav.yaml`. Bundle health reports whether DEM/DSM assets are
present, whether their raster metadata can be inspected, and whether vertical
terrain sanity checks are ready. Missing DEM/DSM files are fine when no asset is
declared; declared-but-missing files fail bundle health.

From the desktop app, use Maps -> Attach Elevation Assets to copy DEM/DSM
GeoTIFFs into a saved map source. The next Mission Planner bundle build will
carry those assets into `mission_bundle/elevation/`.

## Runtime

```bash
vision-nav-match-terrain-frame --bundle mission_bundle --frame downward_frame.jpg
vision-nav-run-terrain-loop --bundle mission_bundle --output-dir terrain-run --count 30
vision-nav-replay-terrain-log --bundle mission_bundle --log terrain-run/terrain_matches.jsonl
```

The Pi wrappers are:

```bash
./scripts/pi/validate_terrain_bundle.sh
./scripts/pi/run_terrain_nav_loop.sh
./scripts/pi/replay_terrain_nav_log.sh
```

The runtime emits local ENU, optional lat/lon, covariance, confidence, tile id,
inliers, reprojection error, scale confidence, and barometer health fields. When
optional barometer input is unavailable, `z_m` and `z_m2` stay `null`.

Tile retrieval is hierarchical. If the caller has a prior local ENU position and
search radius, the matcher queries tiles that overlap that local radius first.
On startup with no prior, it performs a bounded coarse search using spatially
distributed high-feature tiles instead of only checking the most textured area,
then reranks candidate tiles with a compact grayscale global descriptor before
local ORB/AKAZE matching. Each runtime result includes `tile_query.strategy`,
`tile_query.global_retrieval`, selected tile IDs, and coverage metadata so a
support bundle can explain how candidate tiles were chosen.

## Estimator Policy

The prototype estimator is conservative:

- map matches update local east/north only after RANSAC geometry checks
- IMU yaw can orient short-term image-motion propagation
- optional barometer samples can add relative `z_m` and vertical covariance
- covariance grows when no fresh strong map match exists
- weak or stale visual scale reduces `scale_confidence`
- rejected matches are logged but not sent as MAVLink vision measurements

This produces measurement candidates for PX4 or later ROS2 fusion; it does not
reset flight state directly.
