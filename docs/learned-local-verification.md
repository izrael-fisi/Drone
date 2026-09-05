# Learned Local Verification And Geometry

The camera replay can now follow retrieval/student evidence with actual learned
XFeat/LighterGlue correspondence and optional surface-backed camera geometry.
Two research backends are available: the pinned **PyTorch reference** and a
parity-checked **ONNX CPU** export of both learned stages. Neither is a promoted
Pi flight release. No MAVLink transport or vehicle commands are introduced.

## Why This Stage Exists

A retrieved tile or spatial probability peak is not sufficient evidence of
location. The local reference extracts learned descriptors from the camera
image and each candidate tile, runs LighterGlue, and geometrically checks the
correspondences. It does not fall back to classical features or a nearest-neighbor
matcher when learned inference fails.

The source of the implementation/configuration is the pinned official
[XFeat repository](https://github.com/verlab/accelerated_features). The adapter
uses its XFeat detector and LighterGlue architecture configuration with Kornia's
implementation. Source revision, both checkpoint hashes, Kornia implementation
hash/version, PyTorch version and thresholds are included in evidence.

Local checkpoints load with `weights_only=True`. Detector loading is strict;
matcher loading excludes the checkpoint's explicitly named extractor branch
and normalizes known parameter prefixes. All learned matcher keys must then
match exactly. The only permitted absent value is Kornia's deterministic
`confidence_thresholds` buffer, which is not a learned parameter. Missing
learned weights never silently retain random initialization.

## Configuration

Start from `research/local-verification.example.json`. Resolve paths relative
to the config file, or supply absolute paths. Required inputs are a clean
checkout matching `research/upstreams.lock.json`, local XFeat and LighterGlue
weights, and `backend: pytorch_reference`. The pinned checkout includes the
official weights under `weights/`. No implicit network download occurs during
inference. Use the existing upstream-materialization script for provisioning.

The research environment needs PyTorch and Kornia. CPU and CUDA are explicit
choices; a requested but unavailable CUDA device is rejected. Flash attention,
point pruning and early stopping are disabled for this reference, and their
configuration is logged. The input image dimensions must be multiples of 32,
with 512 features and 640x384 images as initial engineering defaults. These are
not measured Pi limits or validated flight-quality thresholds.

## Standalone Image Pair

```bash
python -m vision_nav.neural.learned_correspondence \
  --config research/local-verification.example.json \
  --query /data/camera/query.png \
  --map /data/map/candidate.png \
  --output /data/evidence/pair-001.json
```

Installed command: `vision-nav-match-learned-pair`. The output file must not
already exist. A completed command reports evidence even if geometry rejects
the pair; inspect `geometry.status`. It contains model/source/input hashes,
feature and match counts, inlier ratio, reprojection residual and image coverage.
Standalone image-pair checking establishes only planar consistency, never
metric camera position or actual terrain planarity.

## Recorded-Camera Integration

For a PyTorch-free local backend, see [local ONNX export](learned-local-onnx.md).
The same replay option accepts either backend's configuration; export-bound
ONNX policies cannot be overridden by runtime settings.

Add the following option to the existing automatic or explicit-candidate
map-conditioned replay command:

```bash
--local-verification-config /data/local-verification.json
```

Query features are extracted once per complete accepted temporal window. Up to
four candidates are checked independently; map-feature caching retains at most
four tiles. Matches and their pixel coordinates are retained in the decision
trace, along with per-candidate geometry and failure reasons. These arrays are
bounded by the feature budget but can make research traces large.

If a candidate cannot be checked, the result is incomplete and temporal context
is reset. More than one geometrically consistent candidate remains ambiguous;
overlap merging and a motion-conditioned hypothesis bank are not implemented.
Even one consistent candidate remains `position_authority: false` and
`NO_GLOBAL_FIX`. Student probabilities do not bypass geometric rejection.

## Metric Geometry

With `elevation_spec: null`, the reference performs USAC/MAGSAC homography
consistency only. It does not invent camera altitude or use a zero-height plane.

For a surface-backed PnP experiment, point `elevation_spec` at the documented
`vision_nav_elevation_source_v1` DSM record, or the explicit
[masked v2 ground-surface contract](ground-surface-masks.md). The sampler verifies source
COG/elevation/mask hashes, explicit WGS84 ellipsoidal metre heights, georeferencing and map
error priors. Unknown/masked DSM samples remove corresponding learned features;
an insufficient valid set is rejected, not switched silently to planar geometry.
See [map foundations](navigation-foundations-implementation.md).

v2 permits bare-earth DTM only with a reviewed ground-support mask. Masked or
unknown areas remove corresponding features and descriptors before matching/PnP.
Cached tiles cannot continue after a detected local elevation/mask source change.
This is a conservative research map prior, not a semantic correctness guarantee.

The replay undistorts the camera image with its measured calibration before
resizing. Camera intrinsics use the same pixel-centre resize transform. Map
keypoints are converted back through the original raster/tile transform rather
than interpolating an ENU bounding box. The trace retains matched 3D ENU points,
their declared map-error priors and the scaled camera matrix.

PnP rejects nonfinite/collinear support, robustly selects inliers, refits the
pose and requires inlier landmarks to be in front of the camera. Its output
is explicitly **camera position and ENU-to-optical rotation**, not a body pose.
Map priors are not camera-pose covariance; covariance remains unset. Visibility,
planar ambiguity, uncertainty propagation, body lever arms and temporal
consistency still require qualification before navigation use.

## Evidence Obtained

On 2026-09-05 the pinned pretrained XFeat/LighterGlue weights were run on the
upstream `assets/ref.png` and `assets/tgt.png` pair using CPU, PyTorch 2.14.0,
Kornia 0.8.3 and the default policy:

- 512 features per image; 83 learned correspondences.
- 37 homography inliers; approximately 1.99 px inlier RMS residual.
- 13 of 16 query-image coverage cells occupied by inliers.
- Position authority false; no flight output.

This is an upstream sample-pair integration check, not UAV/satellite accuracy,
flight-pose ground truth, model-export parity or a Pi performance benchmark.

## Tests And Remaining Work

```bash
python -m pytest -q tests/test_learned_correspondence.py
```

To also exercise explicitly provisioned pretrained checkpoints, set
`VISION_NAV_LOCAL_REFERENCE_CONFIG` to your resolved local config path when
running the tests. No test downloads models automatically. Tests cover the
official sample pair and blank-image rejection, plus synthetic replay ambiguity,
provider failures, bounded caches, DSM/no-data handling, checkpoint key checks,
resize/calibration geometry and PnP refitting. The combined replay test uses
synthetic retrieval/student fixtures with real local weights; it proves interface
integration, not end-to-end pretrained navigation accuracy.

Remaining: Pi ONNX measurements, official retriever/student evaluation,
real UAV/orthophoto/DSM correspondence accuracy, uncertainty calibration,
continuous VIO, live scheduling, Pi timing/thermals and reviewed PX4 integration.
