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
Terrain `bundle_health.json` reports checksum state and map source provenance,
but the health report itself is treated as generated metadata and is excluded
from `checksums.sha256`.

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
- one JSON record per frame in `terrain_matches.jsonl`
- the latest operator-facing runtime snapshot in `runtime_status.json`

`runtime_status.json` is rewritten after every processed frame. It shows the
active map bundle, tile index, output/log path, latest frame, estimator health,
last accepted/rejected match reason, external-position health, MAVLink/ROS 2
state, telemetry sample count, timing, and status counts. Support bundles carry
this snapshot into the bench-readiness gate, where missing runtime status
degrades the report and missing active-map or last-match state fails the
runtime-status check.

On the Pi, `./scripts/pi/read_runtime_status.sh` finds the newest
`runtime_status.json`, prints stable desktop-app markers, and summarizes the
active map, latest match, estimator health, and external-position state without
loading the full runtime log.

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

Export a runtime or replay log into ROS-shaped offline review artifacts when
you need to inspect odometry, diagnostics, and bounded camera frames outside the
runtime loop:

```bash
./scripts/pi/run_rosbag_export_validation.sh
```

The wrapper exports the default terrain log into
`~/DroneTransfer/outgoing/terrain-match/rosbag-jsonl/`, validates it, and emits
`__VISION_NAV_ROSBAG_EXPORT_VALIDATION__=...` for support-bundle and final
readiness collection. The equivalent low-level commands are:

```bash
vision-nav-ros2-replay-log \
  --log ~/DroneTransfer/outgoing/terrain-match/terrain_matches.jsonl \
  --export-rosbag-jsonl ~/DroneTransfer/outgoing/terrain-match/rosbag-jsonl \
  --include-frame-topic

vision-nav-validate-rosbag-export \
  --artifact ~/DroneTransfer/outgoing/terrain-match/rosbag-jsonl \
  --output ~/DroneTransfer/outgoing/terrain-match/rosbag-jsonl-validation.json
```

For MCAP-capable desktop tooling, install the optional extra and write a
JSON-encoded MCAP archive:

```bash
python -m pip install ".[rosbag]"
vision-nav-ros2-replay-log \
  --log ~/DroneTransfer/outgoing/terrain-match/terrain_matches.jsonl \
  --export-mcap ~/DroneTransfer/outgoing/terrain-match/vision-nav.mcap \
  --include-frame-topic

vision-nav-validate-rosbag-export \
  --artifact ~/DroneTransfer/outgoing/terrain-match/vision-nav.mcap \
  --output ~/DroneTransfer/outgoing/terrain-match/vision-nav-mcap-validation.json
```

The JSONL export is still the dependency-free fallback and is preferred for
basic Pi setup checks.
On a sourced ROS 2 workstation, use `--export-rosbag2` instead when you need a
native serialized rosbag2 directory for `ros2 bag info/play`. The validator
checks metadata, topic/message counts, MCAP sidecars, and native rosbag2 storage
files without requiring ROS 2 to be installed, and fails closed unless the
export includes non-empty `/vision_nav/odometry` and `/diagnostics` topics.
After creating a native rosbag2 directory on a sourced workstation, save a
CLI review artifact with:

```bash
vision-nav-review-rosbag2-cli \
  --artifact ~/DroneTransfer/outgoing/terrain-match/rosbag2-native \
  --output ~/DroneTransfer/outgoing/terrain-match/rosbag2-cli-review.json
```

That review wraps the strict validator and captures `ros2 bag info` output so
support can confirm the bag is readable by standard ROS 2 tooling.
When a validation report exists at the normal Pi transfer path,
`scripts/pi/create_support_bundle.sh` packages it so desktop support-bundle
diagnostics show the ROS replay artifact health beside PX4, replay-gate,
field-evidence, and threshold evidence.

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

Support bundles write `summaries/bench_readiness.json` automatically. Re-run
the whole-artifact gate before treating a bench run as reviewable:

```bash
vision-nav-bench-readiness \
  --support-bundle ~/DroneTransfer/outgoing/support-bundles/<bundle>.zip
```

The readiness gate combines terrain bundle health, runtime logs, replay gates,
PX4 receiver evidence, and PX4 parameter checks into one pass/degraded/fail
report.

On the Pi, register field logs before treating them as dataset coverage:

```bash
VISION_NAV_FIELD_CASE_NAME=field-good-texture \
VISION_NAV_FIELD_EXPECTED=good_map \
VISION_NAV_FIELD_CONDITION=good_texture \
./scripts/pi/register_field_replay_case.sh
```

The wrapper writes `field_manifest.json`, per-case gate reports, and
`field_evidence_report.json` under `~/DroneTransfer/outgoing/replay-cases/`.
Support bundles include that field-evidence report automatically when it exists.
The wrapper also emits a stable `__VISION_NAV_FIELD_EVIDENCE_REPORT__=...`
marker so Module Setup can download the report and show the required-condition
coverage checklist.
Use `vision-nav-register-replay-case` directly on a desktop dataset folder when
you need custom manifest paths.

Audit whether the manifest covers the required real field cases:

```bash
vision-nav-audit-replay-coverage \
  --manifest data/replay_cases/manifest.example.json
```

Replay manifests are schema-backed by
`data/replay_cases/replay_case_manifest.schema.json`. The standalone evaluator,
coverage audit, and support-bundle packager include schema status in their JSON
reports and fail on schema errors such as duplicate case names, unsupported
`expected` values, unsupported `dataset_type` values, empty conditions, or
missing log paths. Use the evaluator as a quick schema and gate smoke check:

```bash
vision-nav-evaluate-replay-manifest \
  --manifest data/replay_cases/field_manifest.json \
  --json
```

Use `--schema-only` during dataset assembly when you want to validate manifest
shape before all referenced logs have been copied into place.

This audit fails when coverage is synthetic-only or when field replay logs are
missing. It expects field cases for good texture, low texture, blur, seasonal
change, lighting change, altitude/scale change, repeated patterns, and wrong-map
rejection.

Run the combined field evidence gate before treating a replay dataset as
pilot-ready:

```bash
vision-nav-field-evidence-gate \
  --manifest data/replay_cases/field_manifest.json \
  --output data/replay_cases/field_evidence_report.json
```

This gate requires real field log files, checks that all required field
conditions are covered, and evaluates every replay case with the same
accepted/degraded/wrong-map gates used in support bundles.

Compare feature methods on the same real field replay log before promoting a
runtime default:

```bash
VISION_NAV_FEATURE_BENCH_EXPECTED=good_map \
./scripts/pi/run_feature_method_benchmark.sh
```

Use `VISION_NAV_FEATURE_BENCH_EXPECTED=degraded` or
`VISION_NAV_FEATURE_BENCH_EXPECTED=wrong_map` for those field cases. The wrapper
writes reports under `~/DroneTransfer/outgoing/feature-method-bench/`, emits a
stable download marker for Module Setup, and lets support bundles include the
benchmark evidence automatically.

Generate the threshold-tuning report from the same real field manifest:

```bash
vision-nav-tune-replay-thresholds \
  --manifest data/replay_cases/field_manifest.json \
  --output data/replay_cases/threshold_tuning_report.json
```

Use the CLI threshold flags, such as `--min-confidence`,
`--min-good-accepted-rate`, and `--max-reprojection-error-px`, when promoting a
new set of gate thresholds after reviewing the field cases. Support bundles
include `threshold_tuning_report.json` automatically from the same
`~/DroneTransfer/outgoing/replay-cases/` folder when it exists.

After a support bundle, field-evidence report, feature-method benchmark report,
threshold-tuning report, and ROS bag export validation report exist, run the
goal-level readiness audit:

```bash
./scripts/dev/run_local_autonomy_readiness_audit.sh
```

The wrapper scans conventional downloaded artifact folders under
`~/DroneTransfer/from-pi/`, writes
`~/DroneTransfer/from-pi/replay-cases/autonomy_readiness_report.json`, renders
`~/DroneTransfer/from-pi/replay-cases/autonomy_readiness_report.md`, and prints
`__VISION_NAV_AUTONOMY_REPORT__=...` plus
`__VISION_NAV_AUTONOMY_HANDOFF__=...`. It passes standalone field, PX4 receiver,
feature-method benchmark, threshold-tuning, and ROS bag export validation
reports directly when they were downloaded outside the support bundle. Use
`vision-nav-autonomy-readiness` directly when custom artifact paths are needed,
or
`vision-nav-autonomy-handoff --report <report.json> --output <handoff.md>` to
render a handoff from an existing report. The handoff and evidence ZIP package
include bounded goal-proof summaries so support review can see both passing
proof items and remaining completion blockers.

This is intentionally stricter than the synthetic smoke tests. It fails until
PX4 receiver proof, real field coverage, feature-method benchmark evidence,
field-tuned acceptance thresholds, and ROS replay export validation are all
present.

For a dependency-free local registry smoke test:

```bash
./scripts/dev/evaluate_synthetic_replay_cases.sh
```

This evaluates `data/replay_cases/synthetic_smoke/manifest.json`, covering a
good-map case, a degraded low-texture case, and a wrong-map rejection case. It
is synthetic coverage for the gate machinery; real field logs are still needed
before tuning operational thresholds.

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
