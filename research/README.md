# Learned Navigation Research Workspace

**Current transfer status:** read the
[technical handoff](../docs/technical-handoff-2026-09-05.md) and
[verification/artifact inventory](../docs/handoff-verification-2026-09-05.md).
Sections below include intended teacher workloads, not claims that every paper
has been reproduced or a qualified Pi student trained. RunPod is prepared but
not deployed by this research work.

This directory is separate from the flight runtime. It trains cloud teachers,
distills `pi5_crossview_nav_v1`, validates exported ONNX models, and produces
the learned artifacts that a `MapRelease v2` requires.

For the current standalone retrieval experiment, see
[learned retrieval into camera replay](../docs/learned-retrieval-replay.md).
It exports an explicitly supplied MegaLoc checkpoint, builds a model-bound
tile index, and connects automatic candidates to the map-conditioned student.
This is shadow-only research; official-checkpoint accuracy and Pi benchmarks
remain required before deployment.

For the released Bearing-UAV model, the
[environment upgrade comparison](../docs/bearinguav-environment-parity.md)
records newer Torch CPU compatibility separately from the old reference stack
and the still-unqualified RunPod/CUDA deployment.
The [teacher-to-student workflow](../docs/bearinguav-student-distillation.md)
adds actual released-model point targets to the compact student's supervised
training, with explicit opt-in and no teacher loss on validation or negatives.

## What Belongs Here

- Video2BEV temporal cross-view teacher reproduction
- Bearing-UAV architecture reproduction and trajectory/heading losses
- AirLock+ registration evaluation
- TopicGeo auxiliary retrieval and hard-negative training
- RoMa v2, VGGT, and MASt3R-SLAM pseudo-label or map-production adapters
- MegaLoc retrieval and XFeat/LighterGlue Pi-student export

None of these workloads run on the Pi or during flight. Run them on a CUDA
machine or an ephemeral RunPod Pod. The Pi gets only audited ONNX artifacts and
a manifest with hashes, licenses, calibration compatibility, and lineage.

## Pinned Upstream Sources

The exact public source revisions and model-specific training stages are recorded in
[`upstreams.lock.json`](upstreams.lock.json). Validate the lock before a
research build, then explicitly materialize it into the ignored
`research/vendor/` directory:

```bash
PYTHONPATH=src python -m vision_nav.neural.upstreams \
  --lock research/upstreams.lock.json
./scripts/research/materialize_upstreams.sh
```

When an upstream project uses different branches for its official training
stages, materialize a separate immutable checkout rather than repurposing the
generic source checkout:

```bash
PYTHONPATH=src python -m vision_nav.neural.upstreams \
  --lock research/upstreams.lock.json \
  --materialize-workflow video2bev_first_stage_v1 \
  --workflow-dir research/vendor/workflows

VISION_NAV_UPSTREAM_WORKFLOW=video2bev_first_stage_v1 \
VISION_NAV_UPSTREAM_CHECKOUT=research/vendor/workflows/video2bev_first_stage_v1 \
VISION_NAV_UPSTREAM_DATASET_ROOT=/data/univ \
./scripts/research/preflight_upstream_training.sh
```

The preflight checks pinned Git contents and rejects modified or shadow Python
sources. Video2BEV adds [stage-specific source and rendered-image checks](../docs/video2bev-reproduction.md);
a non-empty folder is not sufficient. It does not download a dataset or run a
trainer. `video2bev_second_stage_v1` additionally requires
`VISION_NAV_UPSTREAM_CHECKPOINT` to name the first-stage checkpoint.

The [portable second-stage workspace](../docs/video2bev-portable-workspace.md)
resolves known import/path issues in a separate hash-recorded copy and provides
a real upstream data-loader smoke test. Its separate
`vision-nav-smoke-video2bev-model` command runs the unchanged ViT/fusion/head
with random initialization through one upstream matching-loss optimizer step,
including both anchor directions and the original negative-selection code.
Neither check reproduces pretrained training or qualifies geographic accuracy;
no cloud job is launched.

The lock separates Pi-export sources from cloud-only teachers. It records
license review status, but a source marked `verify_before_distribution` must be
reviewed again before its weights or derivative artifact leaves research.

## Provider Boundaries

- **MegaLoc** is a pinned pretrained global-retrieval baseline. It supplies
  coarse candidate embeddings and map indexing; this project does not present
  it as a public fine-tuning workflow.
- **Video2BEV** uses its own staged public dataset/protocol. Its upstream
  checkout must receive that layout, not the project’s JSONL map-pair folder.
  Its BEVs come from offline 3DGS reconstruction; short-window onboard temporal
  distillation is our separate research task, not a released upstream component.
- **XFeat** can be trained through its own upstream workflow or a project-owned
  adapter that consumes the audited `vision_nav_map_pairs` contract.
- **XFeat LighterGlue** consumes the fixed XFeat 64-dimensional learned
  keypoints and descriptors. Its official XFeat checkpoint, source revision,
  and fixed-cardinality ONNX candidate must share explicit provenance;
  interface wiring alone is not a training claim.
- **RoMa, VGGT, and MASt3R-SLAM** remain cloud-only teachers/map-production
  tools. They are never Pi runtime dependencies.

## XFeat Export Boundary

The project exports the exact pinned XFeat convolutional backbone to ONNX
before attempting vehicle integration. The dense export contains raw stride-8
descriptors, keypoint logits, and reliability maps. The sparse export applies
the pinned XFeat heatmap/NMS, reliability scoring, descriptor interpolation,
and L2 normalization in a fixed-cardinality wrapper, yielding the `keypoints`,
`descriptors`, and `scores` tensors required by the future local-feature role.
Both include immutable PyTorch parity fixtures.

On the Windows RTX workstation, first materialize the locked upstreams and
obtain a weight file whose source and license are recorded in the future model
release. Then run:

```powershell
.\scripts\research\export_xfeat_dense_onnx.ps1 `
  -Checkout D:\DroneData\vendor\xfeat `
  -Weights D:\DroneData\weights\xfeat.pt `
  -OutputDir D:\DroneData\research-runs\xfeat-dense-v1
```

The script verifies the detached source revision against
`research/upstreams.lock.json`, records the exact weight digest, exports one
fixed-resolution ONNX graph, validates the raw tensor contract, and runs CPU
ONNX parity against recorded PyTorch tensors. Its output is research-only.
Do not copy it to the Pi or bind it to a `ModelRelease` until the downstream
sparse post-processing and correspondence path has equivalent parity evidence.

Once the dense graph passes, export the bounded local-feature candidate with:

```bash
./scripts/research/export_xfeat_sparse_onnx.sh \
  research/vendor/xfeat /data/weights/xfeat.pt /data/research-runs/xfeat-sparse-v1
```

On Windows, use the matching PowerShell wrapper:

```powershell
.\scripts\research\export_xfeat_sparse_onnx.ps1 `
  -Checkout D:\DroneData\vendor\xfeat `
  -Weights D:\DroneData\weights\xfeat.pt `
  -OutputDir D:\DroneData\research-runs\xfeat-sparse-v1
```

This fixed-cardinality wrapper marks invalid slots with score `-1`; every map
compiler or local matcher must discard them. The sparse artifact still requires
ONNX parity and comparison against pinned upstream `detectAndCompute` results
on real map and camera imagery before it can join a `ModelRelease`.

## LighterGlue Compatibility Boundary

The active local matcher is **XFeat LighterGlue**, the smaller matcher supplied
by the official `verlab/accelerated_features` XFeat source. It is not the
generic `cvg/LightGlue` checkpoint: XFeat descriptors have dimension 64 and
must be evaluated against the matching XFeat LighterGlue checkpoint.

The original compatibility probe remains available for an immutable reference
record from the exact upstream Python implementation:

```bash
./scripts/research/probe_lighterglue.sh \
  research/vendor/xfeat /data/weights/xfeat-lighterglue.pt \
  /data/research-runs/lighterglue-probe-v1
```

```powershell
.\scripts\research\probe_lighterglue.ps1 `
  -Checkout D:\DroneData\vendor\xfeat `
  -Weights D:\DroneData\weights\xfeat-lighterglue.pt `
  -OutputDir D:\DroneData\research-runs\lighterglue-probe-v1
```

The probe records fixed-count XFeat keypoints/descriptors, accepted
correspondence pairs, confidence scores, the checkpoint hash, and the pinned
source revision. It is research evidence only; it does not produce ONNX or a
vehicle `ModelRelease`.

For the current export/replay path, use [learned local ONNX](../docs/learned-local-onnx.md).
It exports fixed-slot XFeat plus dynamically sized, unpadded LighterGlue,
compares against original reference inference on real images and requires
startup/cascade parity before loading. `pip install -e '.[local-export]'` in a
separate development environment installs the modern exporter dependencies.
Do not assume the older CUDA training container includes that toolchain.
The ONNX replay itself needs no PyTorch/Kornia. Pi measurements and real
UAV-to-map accuracy remain separate acceptance requirements.

## Low-Cost Cloud Workflow

1. Build `docker/research/Dockerfile` locally or in a RunPod template.
2. Mount a persistent volume only for model/dataset cache. Keep source and
   experiment metadata in Git, DVC, and MLflow-backed object storage.
3. Use `scripts/research/train_teacher.sh` for one paper reproduction at a time.
4. Run `scripts/research/distill_pi_student.sh` only after the teacher's public
   benchmark is recorded.
5. Export and verify with `scripts/research/export_pi_onnx.sh` before promoting
   a release with `vision-nav-compile-neural-map`.

The container installs the CUDA 12.4 PyTorch build, project research extras,
ONNX Runtime GPU, and Kornia for the official XFeat LighterGlue probe. Build it
on the RTX workstation or a RunPod machine; it is never a Pi runtime image:

```bash
docker build -f docker/research/Dockerfile -t drone-vision-nav-research:cuda124 .
docker run --rm --gpus all -it \
  -v /path/to/drone-data:/data \
  -v "$PWD":/workspace/Drone \
  drone-vision-nav-research:cuda124
```

Inside the container, run `./scripts/research/check_research_environment.sh`.
It must report CUDA, map-production, ONNX, and LighterGlue readiness before a
teacher or export command is started.

Before an RTX desktop or RunPod Pod receives a real CUDA job, verify the actual
environment. This check is read-only and writes a report with PyTorch/CUDA,
`nvidia-smi`, GeoTIFF, and ONNX availability. It fails rather than silently
falling back to a CPU-only or incomplete map-production environment:

```bash
./scripts/research/check_research_environment.sh
```

The report defaults to `research/environment_report.json`, which is ignored
because it describes one local machine. Set `VISION_NAV_RESEARCH_PYTHON` when
the CUDA virtual environment is not the shell default.

For the RTX Windows workstation, use
[`scripts/research/setup_windows_cuda_research.ps1`](../scripts/research/setup_windows_cuda_research.ps1).
It creates `.venv-research`, installs the project research extras, and invokes
this same gate. The CUDA wheel index is configurable so it can follow the
current official PyTorch selector without changing project source.

Once a verified temporal-window package exists, Windows users can exercise the
whole project-owned teacher-to-student chain with one bounded CUDA smoke run:

```powershell
.\scripts\research\run_first_temporal_smoke.ps1 `
  -TemporalWindowsDir D:\DroneData\temporal-windows\first-site-v1 `
  -OutputRoot D:\DroneData\research-runs\first-site-smoke-v1
```

It creates a teacher checkpoint/evaluation, immutable teacher targets, a Pi
student checkpoint, a standalone ONNX export, structural export report, and
ONNX parity report. It rejects a nonempty output root and cannot transfer to a
Pi, build a ModelRelease, open MAVLink, or enable PX4 publication.
Its teacher/student optimization pass is bounded by default, while target
materialization covers every source window so the resulting package remains
complete and hash-verifiable.

For the first model, a Windows workstation can also prepare that temporal-window
package directly from copied, validated Pi capture sessions and the measured
model-independent `ReferenceMapRelease`:

```powershell
.\scripts\research\prepare_first_temporal_windows.ps1 `
  -CaptureSessionDir D:\DroneData\captures\route-a,D:\DroneData\captures\route-b `
  -ReferenceMap D:\DroneData\reference-maps\first-site\reference_map.json `
  -OutputRoot D:\DroneData\prepared\first-site-v1 `
  -DatasetReleaseId holybro-first-site `
  -DatasetReleaseVersion 0.1.0
```

It creates the immutable `DatasetRelease`, synchronized training index,
offline-only ENU/heading labels, session-disjoint split, teacher experiment,
reference-map tile layout, verified UAV/map pairs, and validated temporal
windows. It is a preparation path only: it does not touch a Pi, create a
ModelRelease, or enable PX4 publication.

## Dataset Rules

Record `DatasetRelease` metadata before training. Public adapters target UniV
and AirLock+. Proprietary Holybro captures must preserve raw image timestamps,
camera calibration, PX4 attitude, map-release ID, and GNSS truth for offline
training/evaluation only. GNSS is never required during flight.

## Capturing Proprietary Holybro Data

On the Pi, use `scripts/pi/record_training_capture.sh`. Each session includes
frames, continuously collected PX4 MAVLink telemetry when an endpoint is
configured, timebase records, camera calibration ID,
map-release ID, and SHA-256 checksums. The session validator verifies that the
files have not changed and that the declared GNSS policy is
`offline_training_only`.

The Pi wrapper also requires PX4 `ATTITUDE`, a camera sensor timestamp, and a
bounded MAVLink `TIMESYNC` audits before and after capture by default. It
records the host capture start/end/midpoint for each frame and pairs attitude using that midpoint;
`SensorTimestamp` stays camera-clock provenance until a separately measured
camera-to-PX4 clock model exists. Do not disable
`VISION_NAV_REQUIRE_SENSOR_TIMESTAMPS=1` for data intended to train or promote
a field model. A temporary `=0` override is only for bench diagnosis or legacy
camera investigation, and those sessions should not enter a DatasetRelease.

Likewise, `VISION_NAV_REQUIRE_OFFLINE_GNSS_REFERENCE=0` is bench-only. The
normal Pi wrapper and Windows import path require a healthy PX4 GNSS reference
per frame for offline labels. This does not make GNSS available to or required
by the Pi flight runtime.

Before a field session, run `scripts/pi/preflight_training_capture.sh` with a
passing prop-off preflight report and the exact `ReferenceMapRelease`. It
verifies that the geospatial reference includes the selected calibration, the
target capture directory is empty, and the Pi has enough storage for raw
evidence. The preflight is read-only and never opens a MAVLink connection.

On the Windows research workstation, use
`scripts/research/import_pi_training_captures.ps1` to copy explicit session
folders over SSH/SCP and revalidate every checksum, frame-to-attitude pairing,
MAVLink time-sync audit, sensor timestamp, and offline GNSS-label audit before
the sessions are accepted as training input. The transfer manifest is
research-only and does not touch PX4, the Pi camera, or a model release.

For field review, set `VISION_NAV_TRAINING_CAPTURE_DIR` when running
`scripts/pi/create_support_bundle.sh`. The resulting support archive carries a
fresh strict offline-GNSS-label, attitude, sensor-timestamp, and checksum
validation report plus capture manifest/checksums, but never raw
training frames or telemetry. Move the full session only through the validated
Windows import workflow above.

## First Geospatial Reference

Create the model-independent `ReferenceMapRelease` before the first capture.
It has no MegaLoc/XFeat/model dependency, so it breaks the bootstrap loop
between first capture and first learned deployment map. For a projected,
georeferenced aerial GeoTIFF, use the importer below. It copies the imagery to
a COG, emits STAC metadata, derives metre-per-pixel GSD, and creates the
reference release in one immutable folder. Install the research computer's map
production dependencies first: `python3 -m pip install -e '.[map-production]'`.

```bash
VISION_NAV_REFERENCE_MAP_ID=first-site \
VISION_NAV_REFERENCE_MAP_VERSION=0.1.0 \
VISION_NAV_CALIBRATION_ID=down-camera-v1 \
./scripts/research/import_reference_geotiff.sh \
  /data/source-imagery/first-site.tif \
  /data/reference-maps/first-site
```

The source must already be georeferenced in a projected metre-based CRS. This
command never guesses a location from an ordinary image. Use the manual route
below only when COG and STAC assets already exist and their metadata has been
independently checked; its asset paths are relative to `reference_map.json`:

```bash
VISION_NAV_REFERENCE_MAP_ID=first-site \
VISION_NAV_REFERENCE_MAP_VERSION=0.1.0 \
VISION_NAV_REFERENCE_MAP_CRS=EPSG:32610 \
VISION_NAV_REFERENCE_ORIGIN_LAT_DEG=37.000000 \
VISION_NAV_REFERENCE_ORIGIN_LON_DEG=-122.000000 \
VISION_NAV_REFERENCE_ORIGIN_ALT_M=0 \
VISION_NAV_REFERENCE_GSD_M_PER_PX=0.15 \
VISION_NAV_REFERENCE_IMAGERY_URI=imagery/map.tif \
VISION_NAV_REFERENCE_STAC_URI=stac/item.json \
VISION_NAV_CALIBRATION_ID=down-camera-v1 \
./scripts/research/create_reference_map.sh /data/reference-maps/first-site/reference_map.json
```

Set `VISION_NAV_REFERENCE_MAP_ID=first-site@0.1.0` and
`VISION_NAV_MAP_REFERENCE_KIND=reference_map_release` for the capture session.
After the capture has become a verified training index, materialize labels
against that same reference before any deployment MapRelease exists:

```bash
VISION_NAV_REFERENCE_MAP=/data/reference-maps/first-site/reference_map.json \
./scripts/research/materialize_training_labels.sh \
  /data/training-index/first-site /data/training-labels/first-site-v1
```

Later, build the model-bound MapRelease v2 from the same source imagery for Pi
retrieval, learned cache compilation, and shadow runtime deployment. The two
release types are intentionally separate: one is a data-collection geospatial
anchor; the other is a model-compatible runtime artifact.

On the research computer, build a release only from validated sessions:

```bash
VISION_NAV_DATASET_RELEASE_ID=holybro-capture-set \
VISION_NAV_DATASET_RELEASE_VERSION=0.1.0 \
./scripts/research/create_dataset_release.sh /data/captures/capture-001
```

The release builder requires PX4 `ATTITUDE` telemetry by default. Do not train
from camera-only focus tests or from sessions with an unknown calibration or
map release.

Before a PyTorch job reads those captures, materialize the auditable frame index
that associates every retained image with its nearest PX4 attitude sample and
an optional offline-only GNSS reference label:

```bash
./scripts/research/prepare_training_dataset.sh \
  data/datasets/holybro-captures-release.json \
  /data/training-index/holybro-capture-set
```

The resulting `samples.jsonl` is the training job input. It records frame
hashes, calibration and map-release IDs, timestamped attitude, and any GPS
label with an explicit `offline_training_only` policy. A session changed after
release creation is rejected rather than silently reused.

By default, offline GNSS labels must have a PX4 `GPS_RAW_INT` fix type of at
least 3, be no more than 500 ms from the camera frame, and report horizontal
accuracy of 10 m or better. `GPS_RAW_INT` is preferred over a nearby
`GLOBAL_POSITION_INT` because it retains the fix and accuracy evidence. Tune
the label threshold only as a documented experiment variable:

```bash
VISION_NAV_REFERENCE_MAP=/data/reference-maps/first-site/reference_map.json \
VISION_NAV_MAX_GPS_HORIZONTAL_ACCURACY_M=5 \
./scripts/research/materialize_training_labels.sh \
  /data/training-index/holybro-capture-set \
  /data/training-labels/holybro-capture-set-v1
```

GPS stays offline-only training and evaluation truth in this workflow; it is
not read by the Pi learned-localization runtime.

Gate the index before spending GPU time:

```bash
./scripts/research/check_training_readiness.sh /data/training-index/holybro-capture-set
```

The default gate requires at least 100 samples, two separate capture sessions,
attitude for every frame, one calibration ID, offline geo labels, and measured
frame-quality metrics. Its initial quality thresholds are a Laplacian variance
of at least `20` and saturation no higher than `0.65`; tune them using recorded
field evidence rather than lowering them blindly. Relax a threshold only for a
narrowly scoped smoke experiment, never for a field model promotion.

Materialize offline-only position and heading targets next. This converts
healthy recorded GNSS references into a WGS84-derived local ENU frame and pairs
PX4 attitude yaw with each accepted frame. The label files are cloud-training
and evaluation data only; no Pi runtime command reads them.

```bash
./scripts/research/materialize_training_labels.sh \
  /data/training-index/holybro-capture-set \
  /data/training-labels/holybro-capture-set-v1
```

The label manifest records the source-index checksum, ENU origin, GPS-age/fix
policy, and every rejected-frame reason. Defaults require GPS fix type `>= 3`
and a nearest reference no older than `500 ms`; use a deliberate
experiment-specific override only when the recorded data makes that policy
inappropriate.

Create a deterministic, capture-session-disjoint split before starting an
experiment. This prevents adjacent frames from one route appearing in both
training and validation, which would overstate localization performance:

```bash
./scripts/research/create_training_splits.sh \
  /data/training-index/holybro-capture-set \
  /data/training-splits/holybro-capture-set-v1
```

The split manifest records the source-index checksum, seed, session assignment,
and hashes for `train.jsonl` and `validation.jsonl`. Treat it as immutable
experiment input alongside the `DatasetRelease`.

Join accepted labels to the session-disjoint split before invoking a teacher
trainer. This fails closed when the labels and split do not originate from the
same indexed captures, or when either side lacks usable train/validation data:

```bash
./scripts/research/prepare_teacher_experiment.sh \
  /data/training-splits/holybro-capture-set-v1 \
  /data/training-labels/holybro-capture-set-v1 \
  /data/teacher-experiments/holybro-capture-set-v1
```

The resulting package has joined `train.jsonl` and `validation.jsonl` files for
the CUDA teacher implementation, alongside an immutable manifest. Point
`train_teacher.sh` at that directory with `VISION_NAV_TEACHER_EXPERIMENT_DIR`.

For the **first teacher**, create frame/map pairs from the same
`ReferenceMapRelease` used for capture and labels. This avoids requiring a
model-bound Pi `MapRelease v2` before a first learned model exists. The tile
layout hashes back to the immutable reference COG, and every teacher pair
records its reference-map and layout manifest digests. It is training-only and
cannot be deployed to a Pi.

```bash
./scripts/research/build_reference_map_tile_layout.sh \
  /data/reference-maps/first-site/reference_map.json \
  /data/reference-map-layouts/first-site

VISION_NAV_REFERENCE_MAP=/data/reference-maps/first-site/reference_map.json \
VISION_NAV_REFERENCE_TILE_LAYOUT=/data/reference-map-layouts/first-site/tile_layout.json \
./scripts/research/build_training_map_pairs.sh \
  /data/teacher-experiments/holybro-capture-set-v1 \
  /data/reference-maps/first-site/reference_map.json \
  /data/training-map-pairs/holybro-capture-set-v1
```

The positive tile must contain the offline ENU label; nearby-but-separated
tiles become deterministic spatial hard negatives. The builder rejects a tile
layout whose source COG checksum or ENU origin does not match the selected
reference map.

After teacher/student training has produced an audited model release, build
the model-bound deployment path from the source COG. The generic tile-layout
builder exports PNG crops and ENU bounds with no feature extraction or model
fallback:

```bash
VISION_NAV_MAP_ORIGIN_LAT_DEG=37.000000 \
VISION_NAV_MAP_ORIGIN_LON_DEG=-122.000000 \
VISION_NAV_MAP_ORIGIN_ALTITUDE_M=0 \
./scripts/research/build_map_tile_layout.sh \
  /data/orthophoto.tif /data/map-tile-layout
```

Use the resulting `tile_layout.json` when producing MegaLoc embeddings and the
XFeat cache. The learned map-asset compiler runs the real, release-declared
global-retrieval and local-feature ONNX stages over every tile. It writes the
bounded retrieval index, retrieval metadata, feature NPZ files, XFeat SQLite
manifest, and tile SQLite manifest with consistent immutable IDs:

```bash
VISION_NAV_MAP_RELEASE_ID=field-area@1.0.0 \
VISION_NAV_MODEL_RELEASE=/data/model_release/model_release.json \
VISION_NAV_MODEL_ARTIFACT_ROOT=/data/model_release \
./scripts/research/compile_learned_map_assets.sh \
  /data/map-release-staging/tile_layout.json
```

`numpy_flat` is the default exact cosine index for a small first Pi map because
it has no FAISS runtime dependency. Set
`VISION_NAV_RETRIEVAL_INDEX_FORMAT=faiss` only after the target Pi has passed
FAISS installation and memory benchmarks. In either format, the metadata binds
every vector ID to a tile ID, ENU bounds, scale, and image URI. The compiler
never creates ORB/AKAZE/SIFT descriptors.

The staging directory must contain `tile_layout.json` at its root, with its
`imagery/tiles/` directory beside it. Keep the source COG and a valid STAC item
in the same staging area, then promote the full release after the compiler:

```bash
vision-nav-compile-neural-map \
  --output /data/map-release-staging/map_release.json \
  --map-id field-area --version 1.0.0 \
  --model-release /data/model_release/model_release.json \
  --cog /data/map-release-staging/imagery/map.tif \
  --stac-item /data/map-release-staging/stac/item.json \
  --tile-index /data/map-release-staging/index/tiles.sqlite \
  --megaloc-index /data/map-release-staging/index/megaloc.npz \
  --megaloc-metadata /data/map-release-staging/index/megaloc_tiles.json \
  --xfeat-index /data/map-release-staging/index/xfeat_tiles.sqlite \
  --xfeat-metadata /data/map-release-staging/index/xfeat_tiles.json \
  --crs EPSG:XXXXX --origin-enu 0 0 0 \
  --geodetic-origin-wgs84 LAT LON ALT \
  --gsd-m-per-px GSD --calibration-id down-camera-v1
```

Replace the placeholders with the COG's real CRS, WGS84 origin, GSD, and
measured camera calibration ID. This COG operation requires the
`map-production` dependencies (`rasterio` and `pyproj`) and runs only on the
research machine.

Before transferring the immutable release to the Pi, run the deep asset audit:

```bash
vision-nav-validate-learned-map-assets \
  --map-release /data/map-release-staging/map_release.json \
  --output /data/map-release-staging/learned_map_asset_validation.json
```

This checks that the retrieval index, retrieval metadata, map-tile SQLite
catalog, XFeat SQLite catalog, every cached learned feature file, and every
tile image agree. It is a field-preflight operation rather than a per-frame
runtime cost; a modified asset fails the release.

```bash
For later retraining against an existing deployment map, pass
`--map-release` instead. That route additionally binds pairs to its learned
retrieval metadata. Both routes deliberately fail if a label is not
map-aligned, no tile covers the frame’s ENU target, a tile crop is missing, or
the split/label/map hashes do not agree. They are cloud-training inputs, not Pi
runtime assets.

Validate every camera frame, positive map crop, and spatial hard negative before
allocating a GPU worker:

```bash
./scripts/research/validate_teacher_dataset.sh \
  /data/training-map-pairs/holybro-capture-set-v1
```

The optional `CrossViewTeacherDataset` Python class then exposes raw `[C,H,W]`
RGB tensors for the UAV frame, positive crop, and each negative crop, plus ENU
and PX4-heading targets. For a Holybro IMU-aware run it also exposes the
captured PX4 roll/pitch/yaw and measured `body_frd_T_camera_optical` transform.
The teacher launch preflight requires that pose context by default for project
map pairs; public image-only upstream datasets remain a separate adapter path.
Normalization and augmentation remain model-adapter decisions, rather than
hidden preprocessing that would make model parity hard to audit.

For temporal Video2BEV-style or bearing-aware training, derive windows only
after this audit. Each window is monotonic in both host capture time and camera
sensor time, stays within one capture-session split, retains per-frame PX4
roll/pitch/yaw, and uses its final frame as the map-supervised anchor:

```bash
./scripts/research/build_temporal_training_windows.sh \
  /data/training-map-pairs/holybro-capture-set-v1 \
  /data/temporal-windows/holybro-capture-set-v1
```

The default is four frames, one-frame stride, and a maximum 1-second host-time
gap. Change those values only in the experiment record through
`VISION_NAV_TEMPORAL_WINDOW_SIZE`, `VISION_NAV_TEMPORAL_WINDOW_STRIDE`, and
`VISION_NAV_TEMPORAL_MAX_FRAME_GAP_MS`; the window manifest records the exact
choice and source map-pair digest.

Validate the portable window package before assigning a CUDA worker. This
checks hashes, final-frame supervision anchors, same-session ordering, declared
time gaps, map/calibration identity, and every measured PX4/camera pose field:

```bash
./scripts/research/validate_temporal_training_windows.sh \
  /data/temporal-windows/holybro-capture-set-v1
```

`TemporalCrossViewTeacherDataset` exposes a single `[T,C,H,W]` RGB frame tensor
and aligned `[T,3]` attitude / camera translation arrays to a project-owned
teacher adapter, plus the final frame's positive map crop and ENU target. It
does not choose a model architecture, normalization, or augmentation policy.
Point `train_teacher.sh` at a validated package through
`VISION_NAV_TEMPORAL_WINDOWS_DIR`; the script performs this validation before
printing the CUDA-launch instructions.

For an end-to-end integration smoke run, the repository also has a compact
project-owned learned temporal baseline. It uses the window sequence, PX4
attitude, a positive map crop, and spatial hard negatives to train contrastive
map retrieval plus ENU/heading auxiliary heads. It writes a PyTorch checkpoint
and hash-bound `training_run.json`. This is a research-only baseline, **not** a
claim to reproduce Video2BEV or Bearing-UAV, and it may not publish external
vision to PX4:

```bash
VISION_NAV_TEACHER_DEVICE=cuda \
VISION_NAV_TEACHER_EPOCHS=1 \
VISION_NAV_TEACHER_MAX_TRAIN_BATCHES=10 \
VISION_NAV_TEACHER_MAX_VALIDATION_BATCHES=10 \
./scripts/research/train_temporal_teacher_baseline.sh \
  /data/temporal-windows/holybro-capture-set-v1 \
  /data/teacher-runs/temporal-baseline-smoke-v1
```

The baseline deliberately uses batch size one because the verified hard-negative
set can vary per temporal anchor. Use it to prove data/gradient/checkpoint
integrity before a long CUDA job or a pinned upstream reproduction.

Each validation epoch records contrastive, position, and heading losses plus
`retrieval_top1`, Euclidean `position_error_m`, and wrapped
`heading_error_rad`. These are smoke-run indicators only; promotion still
requires held-out route evaluation, false-fix analysis, covariance checks, and
Pi latency/thermal evidence.

Re-run held-out validation later without retraining. The evaluator checks that
the checkpoint is bound to the exact temporal-window manifest before it opens
the model, then writes a standalone report:

```bash
./scripts/research/evaluate_temporal_teacher_baseline.sh \
  /data/teacher-runs/temporal-baseline-smoke-v1/temporal_teacher_baseline.pt \
  /data/temporal-windows/holybro-capture-set-v1 \
  /data/teacher-runs/temporal-baseline-smoke-v1/heldout_evaluation.json
```

The first distillation stage is now concrete as well: it materializes one
hash-bound teacher target NPZ per temporal window. Targets contain the teacher
temporal feature, ENU regression output, and heading output, preserving the
window/checkpoint relationship without treating teacher output as flight truth:

```bash
VISION_NAV_TEMPORAL_TEACHER_CHECKPOINT=/data/teacher-runs/temporal-baseline-smoke-v1/temporal_teacher_baseline.pt \
VISION_NAV_TEMPORAL_WINDOWS_DIR=/data/temporal-windows/holybro-capture-set-v1 \
VISION_NAV_TEACHER_TARGETS_DIR=/data/teacher-targets/temporal-baseline-smoke-v1 \
./scripts/research/distill_pi_student.sh
```

This produces distillation inputs only. The compact Pi student trainer, ONNX
export, export validation, parity fixtures, and the normal MapRelease/Pi
shadow gates remain separate required steps.

Train the compact student only after target export validates. The checkpoint is
bound to one map reference and one measured camera calibration; it cannot blend
sites or mount configurations:

```bash
VISION_NAV_TEMPORAL_WINDOWS_DIR=/data/temporal-windows/holybro-capture-set-v1 \
VISION_NAV_TEACHER_TARGETS_DIR=/data/teacher-targets/temporal-baseline-smoke-v1 \
VISION_NAV_PI_STUDENT_OUTPUT_DIR=/data/pi-student-runs/pi5-student-smoke-v1 \
VISION_NAV_STUDENT_DEVICE=cuda \
./scripts/research/train_pi_temporal_student.sh
```

Export and immediately compare the ONNX artifact with its stored PyTorch
reference tensors. The export uses the Pi runtime’s exact two temporal inputs:
`frame_window` and `attitude_window`.

```bash
VISION_NAV_PI_STUDENT_CHECKPOINT=/data/pi-student-runs/pi5-student-smoke-v1/pi_temporal_student.pt \
VISION_NAV_TEMPORAL_WINDOWS_DIR=/data/temporal-windows/holybro-capture-set-v1 \
VISION_NAV_TEACHER_TARGETS_DIR=/data/teacher-targets/temporal-baseline-smoke-v1 \
VISION_NAV_PI_STUDENT_EXPORT_DIR=/data/pi-student-exports/pi5-student-smoke-v1 \
./scripts/research/export_pi_temporal_student.sh
```

The wrapper first runs an environment-independent structural export check. It
verifies artifact and tensor checksums, exact input/output names and shapes,
map/calibration provenance, and that the export remains research-only. ONNX
Runtime then executes the graph for the separate numerical parity report.
You can rerun the first check after copying an export without opening the graph:

```bash
vision-nav-validate-pi-temporal-student-export \
  --export-dir /data/pi-student-exports/pi5-student-smoke-v1
```

The standalone `temporal_student.onnx` is not a complete vehicle
`ModelRelease`: MegaLoc, XFeat, and LighterGlue artifacts must be present with
their own release-bound interfaces and parity cases. Even after that, the first
Pi use is shadow-only.

Before copying the student into a complete release directory, bind the exact
student artifact to its intended `ModelRelease` and `MapRelease`. This rejects
a changed ONNX file, a different map/calibration, or a temporal tensor contract
that cannot provide the shadow runtime's required evidence fields:

```bash
vision-nav-validate-pi-temporal-student-binding \
  --export-dir /data/pi-student-exports/pi5-student-smoke-v1 \
  --model-release /data/model_release/model_release.json \
  --map-release /data/map_release/map_release.json \
  --artifact-root /data/model_release
```

The binding report is still research-only: it does not validate the other three
model roles, run ONNX numerically, transfer to a Pi, or enable PX4 output.

When the research environment has MLflow configured, opt in explicitly. The
baseline records the temporal-window manifest hash, model settings, per-epoch
losses, the immutable run record, and checkpoint artifact under one MLflow run:

```bash
MLFLOW_TRACKING_URI=http://YOUR-MLFLOW-HOST:5000 \
VISION_NAV_MLFLOW_RUN_NAME=temporal-baseline-smoke-v1 \
./scripts/research/train_temporal_teacher_baseline.sh \
  /data/temporal-windows/holybro-capture-set-v1 \
  /data/teacher-runs/temporal-baseline-smoke-v1
```

Without `MLFLOW_TRACKING_URI`, the run remains fully local and writes the same
hash-bound `training_run.json`; no network connection is attempted.

## ONNX Export Contract

Use [`model-release-interface-template.json`](model-release-interface-template.json)
to declare the actual tensor names and ranks of every exported model stage.
The template is deliberately logical rather than prescriptive: export tools
must replace its names with the graph's inspected names. Validate the release
on the training machine and again on the Pi:

```bash
vision-nav-validate-neural-model \
  --model-release /data/model_release.json \
  --artifact-root /data \
  --require-interfaces \
  --inspect-onnx
```

This checks all four artifacts, the aggregate digest, and ONNX Runtime graph
interfaces. It does not publish position to PX4.

After export, create deterministic NPZ reference cases from the cloud PyTorch
model for each required role and run the parity evaluator. Use
[`onnx-parity-cases-template.json`](onnx-parity-cases-template.json) as the
logical-tensor manifest, then run:

```bash
VISION_NAV_MODEL_RELEASE=/data/model_release.json \
VISION_NAV_MODEL_ARTIFACT_ROOT=/data \
VISION_NAV_ONNX_PARITY_CASES=/data/parity/cases.json \
VISION_NAV_ONNX_PARITY_REPORT=/data/parity/onnx_parity_report.json \
./scripts/research/export_pi_onnx.sh
```

The report records each model role, tensor shape, maximum/mean absolute error,
and maximum relative error. A failed case blocks promotion. Repeat this on the
desktop provider and on the Pi provider before any shadow-runtime release.

## Promotion Gates

A model cannot enter a field release until all of these are stored with the
release:

- source commit and checkpoint/ONNX SHA-256
- training dataset release IDs and licensing status
- map and calibration compatibility
- PyTorch versus desktop ONNX versus Pi ONNX parity report
- Pi P50/P95/P99 latency, memory, CPU, thermal, and dropped-frame report
- deterministic replay comparison against the frozen classical baseline

The runtime remains shadow-only in this program. It must never publish PX4
external vision before a separate promotion decision.

The deployment bench is a release-bound shadow probe, not a synthetic timing
loop. Run [`scripts/pi/benchmark_learned_runtime.sh`](../scripts/pi/benchmark_learned_runtime.sh)
with a measured calibration and at least three camera frames after the deep map
asset audit passes. Its `vision_nav_learned_runtime_benchmark_v1` report records
per-stage and end-to-end P50/P95/P99 latency, process RSS/CPU, Pi thermal
evidence when the platform exposes it, and whether the required learned stages
actually executed. It remains explicitly PX4-disabled.

## Bearing-UAV Capture Adaptation

The [reference adapter](../docs/bearinguav-reference-evaluation.md),
[portable dataset converter](../docs/bearinguav-dataset-converter.md), and
[position-only fine-tuning workflow](../docs/bearinguav-training.md) connect our
camera/map captures to the pinned official model. Training saves audited
checkpoints that the batch evaluator can reload using `--training-run`.
Synthetic CPU optimizer/save/reload tests pass; recorded-data accuracy, heading
supervision, CUDA qualification and paper-protocol reproduction remain pending.
Use its isolated environment, not the general CUDA container or the Pi runtime.

For a future approved cloud run, use the
[RunPod Bearing-UAV handoff](../docs/runpod-bearinguav-training.md). It includes
the generated Linux wheel lock, isolated setup script and real CUDA tensor
preflight. No cloud resources have been provisioned by that preparation.
