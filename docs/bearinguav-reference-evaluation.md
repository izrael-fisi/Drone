# Bearing-UAV Reference Evaluation

Source audit: September 5, 2026. This is a local research adapter for the released
checkpoint, not a replacement for qualified VIO, geometric verification or PX4
publication gates. It does not change the Pi runtime or desktop configuration.

## Architecture findings

The official `PARCASGM_v5a` network consumes four ordered neighboring satellite
patches and one UAV image. It returns normalized block-relative XY position and
a two-component direction. It has 17,134,600 parameters. We load the source model,
not a locally invented reproduction.

- This forward pass is single-frame and neighbor-conditioned, not Video2BEV,
  a video transformer, or a raw-IMU estimator.
- Its position/direction heads are regressors, not calibrated probability
  distributions. Direction magnitude is not heading confidence.
- The published navigation scheme starts at a known location in a four-tile
  block. It does not establish arbitrary map-wide recovery.
- No explicit no-match head exists. Wrong maps can still produce finite
  coordinates. Retrieval, geometric verification and temporal rejection remain
  separate project requirements.

See the [official source](https://github.com/liukejia121/bearinguav/tree/d16e558a14bd0a9142c6901793fe6a6813e4e954)
and [model card](https://huggingface.co/HaoyZhou/bearinguav).

## Immutable artifacts

| Artifact | Identity |
| --- | --- |
| Source commit | `d16e558a14bd0a9142c6901793fe6a6813e4e954` |
| HF weights revision | `31098d2e44749817b624b6a9f6a8b5165e5d4357` |
| Archive SHA-256 | `f3b161273cd063aeeea84d9bf8fce2faea40d3ca615cc6b6424dbc75d53a4f35` |
| Cross-view checkpoint SHA-256 | `31dc15f9fa48cfcd5e58c22387ea5509ba932bb6f70be0d2dfc3c9bac7c45644` |
| Configuration SHA-256 | `1d8028236cf7abeafc65b60ff867689dc9e41b9f73474231131fa2d643c766a5` |
| Dataset revision, not downloaded | `6573175e5b888df73a8eaa7076710b95c90c52ed` |

The source license is Apache-2.0. The inspected checkpoint and
[dataset](https://huggingface.co/datasets/HaoyZhou/bearinguav) cards do not declare
explicit license terms. Source licensing does not establish weights/data rights.
Commercial training, distillation, redistribution and field-release permission
remain unverified; resolve those questions before product promotion.

The weight archive is 131,126,692 bytes. The approximately 13 GB dataset has not
been downloaded. Reports inside the weight archive are the authors' results,
not independently reproduced evidence.

## Isolated setup

Use Python 3.11 in a dedicated environment. Requirements pin the official legacy
core stack, not a complete transitive lock or a hardened production environment.
Torch 2.1.2 is old: do not expose this environment as a service or load arbitrary
checkpoints. The reference CLI permits only the audited digest, using restricted
`weights_only=True` loading. The batch evaluator separately accepts completed
local training runs with verified checkpoint hashes and training membership.
Maintained-stack parity remains a separate task.

From the repository root, skipping the clone if the pinned checkout exists:

```bash
uv venv --python 3.11 .venv-bearing-reference
uv pip install --python .venv-bearing-reference/bin/python -r research/requirements-bearinguav-reference.txt
git clone https://github.com/liukejia121/bearinguav.git research/vendor/bearinguav
git -C research/vendor/bearinguav checkout --detach d16e558a14bd0a9142c6901793fe6a6813e4e954
```

Download `Bearing_UAV.zip` explicitly from the pinned weights revision. Verify
its digest above, then extract only `Bearing_UAV/cross_view/best_model.pth` and
`Bearing_UAV/cross_view/training_configure.json` into an ignored output directory.
The adapter independently verifies both extracted files.

The upstream constructor tries to fetch ImageNet weights before loading its
checkpoint. Our adapter disables that download and strictly loads all checkpoint
parameters including the backbone. It avoids the upstream test script's
CUDA-forced checkpoint loader so CPU research is possible.

Do not install the repository's broad research extras here: they select a
different Torch version. Do not mix `opencv-python` and `opencv-python-headless`.
Use `PYTHONPATH=src` below. Linux CUDA and GPU parity remain untested. No paid
RunPod resources were started by this work.

## Sample contract

Place a JSON manifest beside its images. Replace the illustrative hash strings
with the actual encoded-file SHA-256 digests:

```json
{
  "schema_version": "vision_nav_bearinguav_sample_v1",
  "sample_id": "capture-001-frame-000123",
  "dataset_release_id": "audited-capture-release-id",
  "split": "held-out-route-id",
  "input_kind": "recorded",
  "patches": {
    "p1": {"path": "p1.png", "sha256": "REPLACE_WITH_ACTUAL_SHA256"},
    "p2": {"path": "p2.png", "sha256": "REPLACE_WITH_ACTUAL_SHA256"},
    "p3": {"path": "p3.png", "sha256": "REPLACE_WITH_ACTUAL_SHA256"},
    "p4": {"path": "p4.png", "sha256": "REPLACE_WITH_ACTUAL_SHA256"},
    "uav": {"path": "uav.png", "sha256": "REPLACE_WITH_ACTUAL_SHA256"}
  }
}
```

The audited dataset uses `p1..p4` in row-major image order: top-left, top-right,
bottom-left, bottom-right (`base_00`, `base_10`, `base_01`, `base_11`). Position
labels have right/down axes and a quarter-block normalization unit. The network's
hardcoded coordinate embeddings do **not** describe this patch order correctly;
do not reorder dataset inputs to match them. See the [coordinate audit and
converter](bearinguav-dataset-converter.md). These are not ENU axes.
All five images must share dimensions, 32-512 pixels per axis. No implicit
resize/crop or EXIF georeferencing occurs. This bound is a resource limit, not
a validated operating envelope. The producer owns patch selection and geometry;
sample provenance labels are declarations, not proof of disjoint holdout.

```bash
PYTHONPATH=src .venv-bearing-reference/bin/python -m vision_nav.neural.bearinguav_reference \
  --sample research/outputs/my-bearing-sample/sample.json \
  --source research/vendor/bearinguav \
  --checkpoint research/outputs/bearinguav-source-31098d2/Bearing_UAV/cross_view/best_model.pth \
  --configuration research/outputs/bearinguav-source-31098d2/Bearing_UAV/cross_view/training_configure.json \
  --device cpu --threads 2 \
  --output research/outputs/my-bearing-sample/result.json
```

Run in a fresh process. Results record input/model/config/adapter hashes,
versions, normalized predictions and two timings with exact-repeat checking.
Two timings are not sustained-load P95/P99 benchmarks. Existing evidence cannot
be overwritten. Every result remains `NO_GLOBAL_FIX`, with no position authority,
null metric pose/covariance and PX4 disabled. That status is enforced by our
research wrapper, not inferred by a learned Bearing-UAV rejection head.

## Validation and next steps

Local actual-checkpoint tests cover strict loading, finite synthetic outputs,
repeatability, RGB/ImageNet normalization parity, patch order, integrity gates
and immutable evidence. Synthetic images have no ground-truth geography and
establish neither metric accuracy nor paper reproduction.

The saved local CPU run is in
`research/outputs/bearinguav-cpu-20260905-r1/`: five generated 256x256 images,
`sample.json`, and `result.json`. Tests also use smaller generated images to keep
routine verification inexpensive. These artifacts are intentionally ignored by
Git, as are downloaded weights and upstream checkouts.

Before training or integration:

1. Resolve weights/data terms and freeze an audited dataset release.
2. Audit metadata and block geometry. The upstream test loader uses an 85/5/10
   random row split with seed 42, not guaranteed city/flight-disjoint holdout.
3. Reproduce its official protocol as a separately labeled benchmark, then test
   held-out geography and proprietary flights. `UNI_PIXEL=128` and per-RSI pixel
   scale enter its location metric; raw predictions are not metres.
4. Preserve definitions: `RECALL_AT_K_PHR` measures coordinate-sign quadrant
   agreement, not retrieval Recall@K. The final location aggregate averages
   RSI-group means. Report sample-weighted error separately.
5. Test wrong maps, repeated structures, blur, illumination and stale imagery;
   qualify external rejection and uncertainty rather than inventing confidence.
6. Establish maintained Torch/CUDA/export parity and Pi feasibility, then test
   teacher/student distillation against the existing simpler baseline.

The [research fine-tuning loop](bearinguav-training.md) now runs actual optimizer
updates on converted datasets, with synthetic CPU save/reload evidence. It is
position-only domain adaptation, not the paper's training reproduction.
Recorded-data training, official-dataset accuracy evaluation, qualified ENU/heading
conversion, teacher-target export, ONNX export, CUDA and Pi tests are pending.

The [batch evaluation path](bearinguav-batch-evaluation.md) now adds declared
train/test membership auditing, separate labels and known-context error reports.
It can export training membership from our existing map-pair pipeline but does
not automatically turn positive/negative pairs into Bearing-UAV neighborhoods.
