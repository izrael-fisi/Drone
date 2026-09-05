# Learned Vision Pipeline

`pi5_crossview_nav_v1` is the single active vehicle profile. It is a learned,
offline, GNSS-denied cross-view localization pipeline for a calibrated downward
camera and PX4 attitude context.

```text
calibrated UAV frame + PX4 attitude
  -> temporal BEV and bearing student
  -> MegaLoc global candidate retrieval
  -> XFeat + LighterGlue local correspondence
  -> robust 3D PnP or restricted planar geometry
  -> persistent multi-hypothesis fusion
  -> ENU pose, heading, covariance, or explicit NO_GLOBAL_FIX
  -> shadow telemetry and decision trace
```

ORB, AKAZE, and SIFT are not vehicle-selectable, are not used to build a map,
and are never used as an automatic fallback. They remain only as frozen replay
baselines for model evaluation.

## Vehicle Runtime

The Pi runs ONNX Runtime CPU by default. A Hailo AI HAT 2 provider is optional
only after export parity and thermal testing. The Pi holds a bounded active tile
window, runs global retrieval at recovery cadence, and uses learned local
correspondence at keyframe cadence.

The initial release is shadow-only. It records `TRACKING`, `RECOVERING`, or
`NO_GLOBAL_FIX` but does not send `ODOMETRY` or external-vision data to PX4.

## Required Releases

Every field run requires these compatible immutable records:

- `ModelRelease`: checkpoint and ONNX digests, training lineage, licenses,
  supported calibration IDs, and artifact locations.
- `MapRelease v2`: COG/STAC source, tile geometry, MegaLoc index, XFeat cache,
  optional 3D-landmark/DEM provenance, checksums, and compatible calibration IDs.
- camera calibration ID: must be present in both the model and map release.

An incomplete candidate map is deliberately rejected at runtime. The desktop
bundle builder creates candidate packaging only; research map production adds
the learned indexes before activation.

## Pi Commands

After copying an approved model release and MapRelease v2 to the Pi:

```bash
cp config/pi/vision-nav.env.example config/pi/vision-nav.env
# Set VISION_NAV_MAP_RELEASE, VISION_NAV_MODEL_RELEASE, and VISION_NAV_CALIBRATION_ID.
./scripts/pi/validate_neural_runtime.sh
./scripts/pi/run_vision_nav_shadow.sh
```

The runtime writes `runtime_status.json` and `decision_trace.jsonl` under
`VISION_NAV_OUTPUT_DIR`. `runtime_status_v3` is broadcast to the configured
ground-station UDP port and normalized by the desktop app for live monitoring.

To test a real model export against one captured frame without giving it pose
authority, set `VISION_NAV_SHADOW_FRAME` and run once. The model release must
declare `shadow_source: frame_rgb` and an exact preprocessing contract for
each stage that should execute. The trace records only tensor summaries and
latency; it never saves model outputs or publishes PX4 odometry.

```bash
VISION_NAV_SHADOW_FRAME=/home/user/test-frame.jpg \
VISION_NAV_CALIBRATION_FILE=/home/user/down_camera.yaml \
VISION_NAV_STATUS_UDP_TARGET=192.168.1.157:17660 \
./scripts/pi/run_vision_nav_shadow.sh --once
```

For a Pi-attached camera, set `VISION_NAV_CAMERA_BACKEND=auto`. The runtime
captures one frame per shadow tick under `VISION_NAV_OUTPUT_DIR/frames`.
Any frame probe requires a measured calibration YAML whose ID and native frame
resolution match the active model and camera. Placeholder calibration files are
rejected before inference.
Stages that need a map tile or temporal/attitude window are deliberately
reported as skipped until their release-defined input adapters exist.

See [First Learned Shadow Run](first-learned-shadow-run.md) for the exact
release layout, validation steps, and evidence expected from the first real Pi
model/map test.

## Research And Map Production

The cloud-only teacher program is under [`research/`](../research/README.md).
It reproduces Video2BEV, Bearing-UAV, AirLock+, and TopicGeo-oriented training,
with RoMa v2, VGGT, and MASt3R-SLAM used as teacher/map-production tools.

After creating learned map artifacts, promote them with:

```bash
vision-nav-compile-neural-map \
  --model-release model_release.json \
  --cog map.tif --stac-item item.json --tile-index tiles.sqlite \
  --megaloc-index megaloc.faiss --megaloc-metadata megaloc_tiles.json \
  --xfeat-index xfeat_tiles.sqlite --xfeat-metadata xfeat_tiles.json \
  --map-id field-area --version 1.0.0 --crs EPSG:32610 \
  --origin-enu 0 0 0 --gsd-m-per-px 0.15 --calibration-id down-camera-v1 \
  --output map_release.json
```

Promotion writes a SHA-256 entry for the COG, STAC item, tile index, MegaLoc
index, and XFeat cache. `vision-nav-validate-neural-map-release` recomputes all
local digests before a Pi runtime accepts the release. A missing or changed
learned map artifact is therefore a hard compatibility failure, never a silent
fallback to another map index.

`megaloc_tiles.json` uses `vision_nav_retrieval_index_v1` and binds every
FAISS ID to exactly one stable tile ID, its ENU center, and its scale. The Pi
will not use an index whose map release, model release, vector count, or
descriptor dimension disagrees with that metadata.
Start from [`research/retrieval-index-template.json`](../research/retrieval-index-template.json)
when exporting a learned map release.

`xfeat_tiles.json` uses `vision_nav_local_feature_cache_v1` to bind each
retrievable tile to checksum-protected XFeat keypoints and descriptors. In
shadow mode the Pi can pass the live XFeat output plus the top retrieved map
tile into LighterGlue, while still treating its correspondence result as
diagnostic evidence only. The model release must explicitly describe the
match-index and score encoding before the runtime reports match counts. Start from
[`research/local-feature-cache-template.json`](../research/local-feature-cache-template.json).

When the local-matcher contract also declares `homography_restricted` geometry,
the Pi verifies the scored pairs with USAC/MAGSAC and records inliers, residual,
and image coverage. This only proves candidate-tile consistency; it is not a
metric pose and cannot change the shadow runtime out of `NO_GLOBAL_FIX`.

For a `pnp_3d` geometry contract, each XFeat cache asset additionally carries
an index-aligned `map_points_enu_m` array built from versioned 3D map
landmarks. The Pi then records a calibrated **camera-pose candidate** in the
shadow trace after PnP/RANSAC. It is still not a body pose, not temporally
fused, and not available to PX4.

The learned model itself must also pass cloud-reference ONNX parity before the
map can enter Pi shadow testing. The evaluator consumes logical NPZ input and
reference-output cases, checks release-defined tensor bindings, and reports
per-output numerical error. See [Research](../research/README.md#onnx-export-contract).

See [Research](research.md) and [Architecture](architecture.md) for promotion
gates, model provenance, and the PX4 boundary.
