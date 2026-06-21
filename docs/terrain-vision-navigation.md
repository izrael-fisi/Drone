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
  imagery/tiles/
  index/tiles.sqlite
  index/descriptors/
  features/map_features.npz
  calibration/down_camera.yaml
  calibration/camera_to_body.yaml
  config/terrain_nav.yaml
  checksums.sha256
```

`features/map_features.npz` remains for legacy matching. The terrain runtime
uses `index/tiles.sqlite` to select candidate map tiles, then loads per-tile
descriptor files from `index/descriptors/`.

## Build And Validate

```bash
vision-nav-build-terrain-bundle --bundle mission_bundle --write-checksums
vision-nav-validate-terrain-bundle --bundle mission_bundle
```

The builder updates `manifest.json` with `terrain_bundle` metadata, writes a
small STAC-style manifest, and creates `config/terrain_nav.yaml`.

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
