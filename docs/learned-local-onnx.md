# Learned Local ONNX Replay

Export the actual pinned XFeat detector and LighterGlue matcher, then use both
with ONNX Runtime CPU for recorded-camera localization experiments. This is a
two-stage research artifact, **not** a complete vehicle ModelRelease or a
validated Pi flight system. The learned matcher remains mandatory: no classical
or nearest-neighbor fallback is introduced.

## Prepare And Export

Use a separate development environment, not the Pi capture environment:

```bash
python -m pip install -e '.[local-export]'
```

The initial measured environment was Python 3.13, PyTorch 2.14.0, Kornia 0.8.3,
ONNX 1.22.0, ONNX Script 0.7.1 and ONNX Runtime 1.29.0 on macOS CPU. Other
versions/platforms must pass startup parity rather than inherit that result.
The intended Pi host remains Python 3.11 with ONNX Runtime, without PyTorch.

Configure `research/local-verification.example.json` with a clean, pinned
XFeat checkout, both explicit pretrained checkpoint paths, and `device: cpu`.
The checkout's `weights/xfeat.pt` and `weights/xfeat-lighterglue.pt` are loaded
strictly with `weights_only=True`. No export or inference command downloads
weights. Provision the checkout before running it.

```bash
python -m vision_nav.neural.local_onnx_export \
  --config research/local-verification.example.json \
  --images research/vendor/xfeat/assets/ref.png research/vendor/xfeat/assets/tgt.png \
  --output-dir research/outputs/local-onnx-v1
```

Installed entrypoint: `vision-nav-export-local-onnx`. Supply two to twelve
images. The first pair must produce at least six learned correspondences;
use actual query/map pairs for meaningful domain coverage. The upstream sample
pair verifies machinery only. Additional blur/viewpoint/lighting examples test
feature-export parity, not independently measured localization accuracy.

The output directory must not exist. An interrupted/failed export is not
reusable; choose a new directory. Generated outputs are excluded from Git.

## What Is Exported

- `xfeat.onnx`: fixed RGB `[1,3,H,W]` input and a fixed feature-slot budget.
  Upstream heatmap, NMS, reliability sampling and normalized descriptors are
  included. Slots with nonpositive scores are removed before matching.
- `lighterglue.onnx`: batch-one dynamic query/map feature counts, each bounded
  to `[6,max_features]` by the loader. It consumes real, unpadded keypoints and
  descriptors. No flash attention, adaptive pruning or early stopping.
- `parity/*.npz`: exact reference image tensors, sparse outputs, matcher inputs
  and reference match indices/scores. The export adds a zero-image case plus
  unequal-count and minimum-count matcher cases.
- `manifest.json`: checkpoint/source/graph hashes, preprocessing, policy,
  reference environment, parity samples and measured differences.

XFeat uses the explicitly selected TorchScript exporter at opset 17. LighterGlue
uses `torch.export` at opset 18 because the legacy exporter failed on its dynamic
graph with the measured toolchain. The adapter reuses Kornia's learned layers
and matching functions, leaving Python assertions and ragged result lists
outside the graph. It compares against the **original reference forward**,
not merely against the export wrapper. No weights are embedded in external
files separate from the two graph files.

## Run Without PyTorch

Use `research/local-verification-onnx.example.json`, changing `export_dir` to
the generated directory. Paths resolve relative to the config file. The policy
comes only from the export; changing feature count/thresholds requires a new
export and comparison.

```bash
python -m vision_nav.neural.learned_correspondence \
  --config research/local-verification-onnx.example.json \
  --query /data/camera/query.png \
  --map /data/map/candidate.png \
  --output /data/evidence/onnx-pair.json
```

For map-conditioned replay, pass this same config with
`--local-verification-config`. Optional `elevation_spec` uses the existing
DSM-backed geometry path. Feature extraction and matching no longer require
Torch/Kornia; DSM sampling still requires Rasterio/PyProj. Base Python/OpenCV
and ONNX Runtime dependencies must be installed on the replay machine.

Startup validates graph/sample hashes, tensor names/types/shapes and the local
preprocessing/runtime source digests. It reruns feature, matcher and complete
cascade comparisons on CPU. Missing/malformed artifacts or failed parity stop
loading. There is no automatic PyTorch fallback. This startup work has a cost;
instantiate once per replay/runtime session, not once per camera frame.

## Parity Meaning And Limits

Valid feature pixel sets must match exactly after sorting by pixel identity;
descriptor/score differences use `atol=1e-4, rtol=1e-3`. Sorting permits output
ordering differences, not missing or different features. Discrete match indices
must agree exactly, including after ONNX-produced descriptors pass through
LighterGlue. Runtime checks reject duplicate/out-of-range matches, invalid
scores and nonfinite tensors. Fewer than six valid features produces no matches
without calling attention or adding synthetic padding.

The original sample pair produced 83 correspondences in the measured ONNX
cascade, agreeing with the reference. Tests also export blurred and rotated
images, run in a fresh process that prohibits Torch/Kornia imports, corrupt
artifacts/references, and exercise the combined camera replay with real local
weights and synthetic retrieval/student fixtures.

Pixel ordering is canonical in the ONNX consumer. Feeding the same match set
to RANSAC in a different order can change its sampled inliers: the saved ONNX
sample run had 38 inliers at approximately 1.99 px RMSE, while the original
score-ordered PyTorch run had 37. Neural correspondence parity does not imply
bitwise geometric-result parity. Requalify geometry thresholds on field data.

```bash
VISION_NAV_LOCAL_REFERENCE_CONFIG=/absolute/path/local-reference.json \
  python -m pytest -q tests/test_local_onnx.py
```

Without this environment variable, tests requiring explicitly provisioned
official weights skip. No test downloads data or opens MAVLink.

Still needed: UAV-to-orthophoto/DSM accuracy, representative export regression
sets, Pi latency/RSS/thermals, live scheduling, continuous VIO, calibrated pose
uncertainty and separately reviewed PX4 integration. Pixel residuals, matching
scores and passing export parity are not proof of aircraft position accuracy.
Every replay retains `NO_GLOBAL_FIX`, null pose covariance and disabled flight
publication.

Use the [saved-image local benchmark](learned-local-benchmark.md) to collect
warm/cold feature-cache, latency and resource evidence on an idle bench. It
does not replace the full-runtime or live-camera measurements above.

Reference implementations: [XFeat/LighterGlue](https://github.com/verlab/accelerated_features),
[PyTorch ONNX](https://docs.pytorch.org/docs/stable/onnx.html).
