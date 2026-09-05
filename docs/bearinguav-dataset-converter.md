# Capture-to-Bearing-UAV Converter

Connects our portable cross-view training package to the
[reference model](bearinguav-reference-evaluation.md) and
[batch evaluator](bearinguav-batch-evaluation.md). It creates inputs and offline
labels; it does not train, promote a checkpoint, or enable PX4 output.

## Coordinate audit

Published metadata contradicts some navigation-code comments and the apparent
meaning of the model's coordinate embeddings. We checked 225 rows from the pinned
single-city debug metadata and one independent query/map georeference pair using
HTTP range access, without downloading the full archive.

Verified for that subset:

- `p1..p4` use suffixes `00,10,01,11`: row-major image quadrants.
- Position X increases right and Y down.
- A 512-pixel block uses `(pixel - 256) / 128`, not `/256`.
- Stored Cartesian auxiliary Y is negative normalized image Y.
- Direction components equal `cos(theta),sin(theta)`. This does not establish
  their relationship to a mounted camera or PX4 body yaw.

The independent georeference reproduces the selected label within `2e-11`.
This is coordinate consistency evidence, not model accuracy or validation of
every training sample. The network itself is unchanged; inputs follow actual
dataset order rather than attempting to repair its coordinate embeddings.

Evidence: `research/outputs/bearinguav-coordinate-audit-20260905-r1/report.json`.
Metadata SHA-256: `bbfcf4f6aa9bd0b80a4deb86522c9cd6c222c77411647c740dfcc1ab1a673230`.
Dataset revision: `6573175e5b888df73a8eaa7076710b95c90c52ed`.
Range-read members pass ZIP CRC checks and have recorded hashes; the full archive
digest has not been verified. To repeat the explicit network audit, install
`remotezip==0.12.3` in a research environment and run:

```bash
python scripts/research/audit_bearinguav_coordinates.py --output /new/path/audit.json
```

The archive directory is transferred to locate members. The three extracted
members total approximately 111 KB; no training images are downloaded.

## Convert captures

Input is a validated `vision_nav_map_conditioned_dataset_v1` package from our
existing `build_crossview_dataset` flow. Images, split files, calibration and
timing evidence are checked. A scene registry maps capture session IDs to
reviewed geographic scene labels.

```bash
PYTHONPATH=src .venv-bearing-reference/bin/python -m vision_nav.neural.bearinguav_dataset \
  --dataset /absolute/path/to/crossview-dataset \
  --scenes /absolute/path/to/scenes.json \
  --release-id proprietary-bearing-inputs-v1 \
  --input-kind recorded --image-size 256 \
  --output /new/path/to/bearing-dataset
```

The last camera frame of each window is lens-rectified using measured calibration
and linearly resized to a square, matching our current preprocessing. This is
not viewpoint rectification or temporal BEV. Resizing and quantization are
explicit domain-adaptation preprocessing, not public-dataset image generation.
Camera attitude and evaluation labels do not enter the model image tensor.

Each candidate map crop is independently split into four quadrants and resized
to the same model input size. Positive and wrong-map cases each use neighbors
from one block, never a positive mixed with unrelated hard negatives. Source map
dimensions must be even and at least 64 pixels per axis.

Crop labels `(u,v)` become `(4(u-0.5),4(v-0.5))`. They preserve the source's
offline reference-point semantics, not a camera-ray ground intersection. Full
block labels can extend to approximately `[-2,2]`; the published training
distribution and flight operating envelope still need separate evaluation.

The producer sets `context_policy=oracle_context`: offline ground truth selected
the source positive tile. This does not measure cold-start retrieval. It retains
positive and wrong-map cases. Raw query bytes/hashes accompany processed inputs,
so resizing cannot hide exact-image train/test leakage. Calibration and original
map crops are preserved as content-addressed assets.

Output includes train cases, training membership, a validation evaluation
manifest and checksums. It is portable and published from staging only after
validation succeeds. Existing directories cannot be overwritten. Exposure-native
timing is required unless `--allow-legacy-timing` explicitly enables a baseline.

Run the batch evaluator with
`--manifest /new/path/to/bearing-dataset/evaluation.json` and its pinned model
arguments. Generated policy checks session separation and reports scene overlap;
it does not claim scene-disjoint testing or disjointness from public pretraining.

## Remaining qualification

No heading label is fabricated from PX4 yaw. Positive labels use
`direction_xy=null` with a reason until camera/map/airframe direction conversion
is qualified. The evaluator reports zero available direction labels and null
direction-error statistics. Metric-error conversion is also omitted rather than
turning a broad crop's approximate axes into claimed global-pose accuracy.

Tests exercise synthetic source windows -> portable dataset -> converted cases
-> actual checkpoint. They check ordering, normalization, portable assets,
raw-byte leakage/corruption rejection and timing opt-in. First real-data
evaluation, heading conversion, recorded-data training, data/weight permissions and
field release remain pending. Every output stays without position authority and
with PX4 disabled.

The [research fine-tuning loop](bearinguav-training.md) now consumes this package
directly and trains the actual checkpoint using positive position labels only.
It retains wrong-map validation and does not fabricate heading supervision.
