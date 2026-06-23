# Vision Pipeline

The active pipeline is visual map-relative localization with classical
geometry. It is built for the Raspberry Pi and a downward camera.

## Pipeline Shape

```text
downward camera frame
  -> undistort / normalize
  -> ORB or AKAZE feature extraction
  -> terrain tile retrieval
  -> descriptor matching
  -> RANSAC homography verification
  -> confidence/covariance estimate
  -> runtime log/status
  -> optional MAVLink ODOMETRY output
```

Do not trust any match that fails the confidence, inlier, reprojection, scale,
or geometry sanity gates.

## Desktop Configuration

The Vision Pipeline page is the only editable configuration surface for:

- pipeline mode
- feature method
- max features
- matcher ratio
- minimum matches
- optional neural model/weight paths

Mission Planner and Devices consume these defaults. They should not create
duplicate pipeline selectors.

## Low-Compute Path

Use this on Raspberry Pi first:

- `classical`
- ORB or AKAZE
- conservative RANSAC thresholds
- bounded tile search
- JSONL logs for review

This is the default product path until field data proves another method is
needed.

## Higher-Compute Option

The optional neural path keeps room for SuperPoint/LightGlue-style descriptors
on higher-compute devices. It is not required for the first Holybro X500 V2
prop-off tests.

## Offline Map Preparation

Offline map preparation happens in the desktop app or on the development
machine, not during flight.

Inputs:

- orthophoto, satellite image, GeoTIFF, or uploaded map image
- optional DEM/DSM
- camera calibration
- mission area bounds
- selected Vision Pipeline defaults

Terrain bundle layout:

```text
mission_bundle/
  manifest.json
  manifest.stac.json
  checksums.sha256
  ortho/map.png
  imagery/tiles/
  index/tiles.sqlite
  index/descriptors/
  features/map_features.npz
  calibration/down_camera.yaml
  calibration/camera_to_body.yaml
  config/terrain_nav.yaml
  bundle_health.json
```

See [Terrain Vision Navigation](terrain-vision-navigation.md).

## Build And Validate

From a prepared map source:

```bash
vision-nav-build-bundle-from-map-source \
  --map-source ~/DroneVisionNav/maps/flight-region \
  --bundle mission_bundle \
  --write-checksums

vision-nav-build-terrain-bundle --bundle mission_bundle --write-checksums
vision-nav-validate-terrain-bundle --bundle mission_bundle
vision-nav-map-health --bundle mission_bundle --json
```

On the Pi:

```bash
VISION_NAV_BUNDLE=$HOME/drone-data/map_bundles/mission_bundle \
./scripts/pi/validate_terrain_bundle.sh
```

## Frame Match Check

```bash
vision-nav-match-terrain-frame \
  --bundle mission_bundle \
  --frame downward_frame.jpg \
  --camera-calibration config/camera/down_camera.yaml
```

The result should report accepted/rejected/failed, confidence, inliers,
reprojection error, tile id, and covariance where available.

## Runtime Loop

Logging-only first:

```bash
VISION_NAV_BUNDLE=$HOME/drone-data/map_bundles/mission_bundle \
VISION_NAV_COUNT=30 \
./scripts/pi/run_terrain_nav_loop.sh
```

Read status:

```bash
VISION_NAV_RUNTIME_STATUS_ROOTS=$HOME/DroneTransfer/outgoing/terrain-match \
./scripts/pi/read_runtime_status.sh
```

Runtime output:

```text
~/DroneTransfer/outgoing/terrain-match/terrain_matches.jsonl
~/DroneTransfer/outgoing/terrain-match/runtime_status.json
~/DroneTransfer/outgoing/terrain-match/frames/
```

Each JSONL record includes capture timing, match timing, status, confidence,
inlier count, homography, geometry sanity metrics, frame quality, covariance,
and georeferenced position when available.

## MAVLink Output

Enable only after logging-only runtime is healthy:

```bash
VISION_NAV_BUNDLE=$HOME/drone-data/map_bundles/mission_bundle \
VISION_NAV_MAVLINK_ENDPOINT=/dev/ttyACM0 \
VISION_NAV_MAVLINK_MESSAGE=odometry \
VISION_NAV_COUNT=30 \
./scripts/pi/run_terrain_nav_loop.sh
```

Rejected or failed matches must be logged but not sent as trusted external
vision updates.

## Replay And Gates

Summarize logs:

```bash
vision-nav-summarize-match-log \
  ~/DroneTransfer/outgoing/terrain-match/terrain_matches.jsonl
```

Evaluate a good-map case:

```bash
vision-nav-evaluate-replay-gates \
  --case-name good-texture-bench \
  --expected good_map \
  --log ~/DroneTransfer/outgoing/terrain-match/terrain_matches.jsonl
```

Evaluate a wrong-map case:

```bash
vision-nav-evaluate-replay-gates \
  --case-name wrong-map-bench \
  --expected wrong_map \
  --log ~/DroneTransfer/outgoing/terrain-replay/wrong_map_matches.jsonl
```

## Support Bundle

After a bench run:

```bash
./scripts/pi/create_support_bundle.sh
```

Review the bundle in the desktop app before moving to any prop-on testing.
