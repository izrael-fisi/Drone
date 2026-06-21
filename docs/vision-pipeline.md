# Vision Pipeline

The first pipeline is visual map-relative localization with classical geometry.
It is built around the Raspberry Pi Global Shutter Camera looking downward.

## Offline Map Preparation

Offline work should happen on the desktop workstation, not the Pi.

Inputs:

- Orthophoto or satellite imagery
- DEM/DSM when available
- Camera calibration
- Mission area bounds

Before trusting any map match, calibrate the downward camera with the mounted
lens and resolution. See [Camera Calibration](camera-calibration.md).

First map bundle shape:

```text
mission_bundle/
  manifest.json
  checksums.sha256
  ortho/
    map.png
  features/
    map_features.npz
  calibration/
    down_camera.yaml
    camera_to_body.yaml
```

Terrain map bundles keep this legacy shape and add `imagery/tiles/`,
`index/tiles.sqlite`, per-tile descriptors, `manifest.stac.json`, and
`config/terrain_nav.yaml`. See
[Terrain Vision Navigation](terrain-vision-navigation.md).

## First Georeferenced Feature Index

For early tests, use a single map image with a simple local georeference:

- `origin_lat` / `origin_lon`: latitude and longitude at a known map pixel
- `gsd_m`: ground sample distance in meters per pixel
- `origin_pixel_x` / `origin_pixel_y`: pixel coordinate for the origin
- `rotation_deg`: optional map-axis rotation in local ENU
- `georef_source`: `geotiff_embedded`, `manual`, or another source label
- `georef_confidence`: map georeference trust score from 0 to 1
- `georef_crs`: optional CRS label such as `EPSG:4326` or `EPSG:32618`

Default axis convention:

- increasing map image `x` is local east
- increasing map image `y` is local south
- `rotation_deg=0` means the image is north-up

Example:

```bash
vision-nav-validate-bundle --bundle mission_bundle --require-calibration
vision-nav-build-bundle --bundle mission_bundle --write-checksums
vision-nav-build-terrain-bundle --bundle mission_bundle --write-checksums
vision-nav-bundle-checksums --bundle mission_bundle --verify
```

Then match a frame:

```bash
vision-nav-match-bundle-frame \
  --bundle mission_bundle \
  --frame downward_frame.jpg \
  --camera-calibration config/camera/down_camera.yaml \
  --viz match_debug.jpg
```

Before copying a mission bundle to the Pi, write checksums after the feature
index and calibration files are in place:

```bash
vision-nav-bundle-checksums --bundle mission_bundle --write
```

After transfer, verify them on the Pi:

```bash
vision-nav-bundle-checksums --bundle ~/drone-data/map_bundles/mission_bundle --verify
```

Use `vision-nav-validate-bundle --require-checksums` or
`VISION_NAV_REQUIRE_CHECKSUMS=1 ./scripts/pi/validate_vision_nav_bundle.sh` when
you want the runtime gate to fail on missing or invalid bundle checksums.

When the homography is valid and georeference metadata is present, the JSON
output includes:

- `estimated_map_pixel`
- `estimated_position.latitude`
- `estimated_position.longitude`
- local `east_m` / `north_m`
- `frame_quality` sharpness, contrast, entropy, and feature density
- `map_georef` source, CRS, GSD, and georeference confidence
- `position_confidence`, which combines visual match confidence with georeference confidence
- `measurement` local ENU candidate with position confidence and estimated covariance

This is still a measurement candidate, not a direct state reset.

For one-off experiments without a bundle manifest, the lower-level commands are:

```bash
vision-nav-build-map \
  --map-image ortho_map.png \
  --output map_features.npz \
  --origin-lat 40.000000 \
  --origin-lon -75.000000 \
  --gsd-m 0.20 \
  --metadata-json map_features.json

vision-nav-match-frame \
  --map-image ortho_map.png \
  --features map_features.npz \
  --frame downward_frame.jpg \
  --camera-calibration config/camera/down_camera.yaml \
  --viz match_debug.jpg
```

## Runtime Loop

```text
PX4/IMU attitude and motion context when available
  + downward camera frame
    -> undistort
    -> normalize contrast
    -> extract ORB/AKAZE features
    -> query nearby map candidates
    -> match descriptors
    -> RANSAC homography
    -> reject bad geometry
    -> convert accepted map pixel to local/global coordinates when georeferenced
    -> publish/log measurement with confidence
```

Critical rule:

Do not snap the drone state directly to a vision match. Publish the match as a
measurement with confidence/covariance so the estimator can accept, weight, or
reject it.

The current Pi bench loop is a logging-only version of this path:

```bash
vision-nav-run-bundle-loop \
  --bundle ~/drone-data/map_bundles/mission_bundle \
  --output-dir ~/DroneTransfer/outgoing/runtime-match \
  --count 30 \
  --interval-s 1.0 \
  --viz-every 5 \
  --camera-calibration config/camera/down_camera.yaml \
  --build-if-missing
```

Or use the Pi wrapper:

```bash
./scripts/pi/run_vision_nav_loop.sh
```

The tiled terrain runtime uses the selected mission bundle's tile index:

```bash
vision-nav-run-terrain-loop \
  --bundle ~/drone-data/map_bundles/mission_bundle \
  --output-dir ~/DroneTransfer/outgoing/terrain-match \
  --count 30 \
  --interval-s 1.0 \
  --camera-calibration config/camera/down_camera.yaml
```

Or use the Pi wrapper:

```bash
./scripts/pi/run_terrain_nav_loop.sh
```

It writes one JSON record per frame in `terrain_matches.jsonl`. Vertical fields
remain unset unless optional barometer telemetry or a future visual vertical
estimate is available.

It writes:

- captured frames under `frames/`
- optional match debug images under `viz/`
- one JSON record per frame in `matches.jsonl`

Each JSONL record includes capture timing, match timing, status, confidence,
inlier count, homography, geometry sanity metrics, frame quality, covariance,
and georeferenced position when available.

The `geometry` block reports:

- `scale_mean`
- `scale_anisotropy`
- `rotation_deg`
- `perspective_norm`

Default rejection thresholds are intentionally conservative for bench testing:

```text
min_scale=0.2
max_scale=5.0
max_rotation_deg=90.0
max_scale_anisotropy=3.0
max_perspective_norm=0.01
```

The Pi wrappers expose these as `VISION_NAV_MIN_SCALE`,
`VISION_NAV_MAX_SCALE`, `VISION_NAV_MAX_ROTATION_DEG`,
`VISION_NAV_MAX_SCALE_ANISOTROPY`, and `VISION_NAV_MAX_PERSPECTIVE_NORM`.

Saved frames can be replayed without camera access:

```bash
vision-nav-replay-bundle-frames \
  --bundle ~/drone-data/map_bundles/mission_bundle \
  --frames "$HOME/DroneTransfer/outgoing/runtime-match/frames/*.jpg" \
  --output-dir ~/DroneTransfer/outgoing/replay-match \
  --viz-every 5 \
  --camera-calibration config/camera/down_camera.yaml \
  --build-if-missing
```

Replay writes `replay_matches.jsonl`, which is the first post-processing format
for comparing maps, thresholds, and camera conditions.

Summarize either runtime or replay logs:

```bash
vision-nav-summarize-match-log \
  ~/DroneTransfer/outgoing/runtime-match/matches.jsonl \
  ~/DroneTransfer/outgoing/replay-match/replay_matches.jsonl
```

Use the accepted rate, confidence range, inlier statistics, reprojection error,
geometry summaries, rejection reasons, quality metrics, covariance estimate,
and timing summary to decide whether to adjust thresholds, lighting, focus, map
imagery, or calibration before trusting the measurements.

Create a transfer-ready support bundle after a bench run:

```bash
vision-nav-support-bundle \
  --bundle ~/drone-data/map_bundles/mission_bundle \
  --log ~/DroneTransfer/outgoing/terrain-match/terrain_matches.jsonl
```

Evaluate replay acceptance gates before trusting a bundle:

```bash
vision-nav-evaluate-replay-gates \
  --case-name good-texture-bench \
  --expected good_map \
  --log ~/DroneTransfer/outgoing/terrain-match/terrain_matches.jsonl
```

For wrong-map checks, the gate fails if any record is accepted by default:

```bash
vision-nav-evaluate-replay-gates \
  --case-name wrong-map-bench \
  --expected wrong_map \
  --log ~/DroneTransfer/outgoing/terrain-replay/wrong_map_matches.jsonl
```

Support bundles can include these gate reports when given a replay-case
manifest:

```bash
vision-nav-support-bundle \
  --bundle ~/drone-data/map_bundles/mission_bundle \
  --log ~/DroneTransfer/outgoing/terrain-match/terrain_matches.jsonl \
  --replay-case-manifest data/replay_cases/manifest.example.json
```

## Match Acceptance Checks

Require:

- Minimum inlier count
- Minimum inlier ratio
- Bounded reprojection error
- Reasonable scale and rotation
- Bounded perspective and scale anisotropy
- Translation consistent with prior motion estimate
- Sufficient texture / sharpness
- Recent camera timestamp

Reject:

- Ambiguous repeated patterns
- Low feature count
- Sudden impossible jumps
- High reprojection residual
- Excessive homography scale, rotation, perspective, or anisotropy
- Bad altitude/ground-scale consistency
- Low confidence from repeated failed matches

## Target Rates

- Camera capture: 30 Hz
- Optical flow / frame-to-frame tracking: 15-30 Hz
- Map matching: 1-5 Hz
- PX4 correction output: 1-5 Hz after bench validation
- Logging: continuous
