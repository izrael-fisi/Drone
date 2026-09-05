# Learned Retrieval Into Camera Replay

The recorded-camera path can now choose map candidates automatically:

```text
recorded camera exposure -> learned ONNX global embedding
    -> cosine search over selected-map tile embeddings
    -> at most four candidate tiles
    -> map-conditioned temporal ONNX student
    -> uncalibrated location/heading evidence, NO_GLOBAL_FIX
```

By default there is no GPS prior, manual-candidate fallback, metric pose, local
matcher, VIO or MAVLink publication in this command. An optional
[local-verification config](learned-local-verification.md) adds PyTorch
XFeat/LighterGlue and map-backed geometric evidence, still without navigation
authority. The existing complete vehicle
ModelRelease checks are unchanged. These single-stage research artifacts do
not masquerade as complete deployment releases.

## Prepare The Retriever

Use the pinned MegaLoc checkout and local safetensors checkpoint in a dedicated
research environment. Do not run unreviewed remote code or download weights
implicitly inside an inference call. The exporter checks the checkout against
`research/upstreams.lock.json`, requires a clean checkout, loads the local
checkpoint strictly and records source/checkpoint/license hashes.

The exporter needs PyTorch, a compatible Torchvision, Safetensors, Pillow,
ONNX and ONNX Runtime (`python -m pip install -e '.[retrieval-export]'`).
The existing research image installs PyTorch/Torchvision;
`research/requirements-research.txt` includes the other research dependencies.
Indexing and replay require ONNX Runtime, Pillow and the project's map-production
dependencies, but not PyTorch. No cloud connection is needed during replay.

Supply 2-32 representative parity images, ideally a mix of UAV images and map
crops. All examples are executed through PyTorch and CPU ONNX Runtime. The
export is written as usable only after finite outputs, numerical tolerances
and non-tied descriptor ranking checks pass. Startup reruns saved parity tensors
on the current CPU provider instead of trusting a status string.

```bash
python -m vision_nav.neural.retrieval_export \
  --checkout /workspace/upstreams/megaloc \
  --upstream-lock research/upstreams.lock.json \
  --weights /workspace/weights/megaloc/model.safetensors \
  --checkpoint-record research/megaloc-checkpoint-source.json \
  --model-id megaloc-shadow@1 \
  --weights-license MIT \
  --example-image /data/captures/query-001.png \
  --example-image /data/maps/candidate-001.png \
  --example-image /data/maps/candidate-002.png \
  --output-dir /data/models/megaloc-shadow-001
```

Installed command: `vision-nav-export-megaloc`. Replace the example paths and
license text with audited local inputs. Output must be empty. A failed export
can leave staging files but has no valid final manifest; inspect the failure
and use a fresh output directory. It never downloads or activates a model.

### Preprocessing Qualification

New MegaLoc exports use RGB conversion, float32 ImageNet normalization, then
antialiased bilinear resize to 322x322. The CPU implementation resizes normalized
float channels with Pillow, avoiding quantization back to uint8 and avoiding a
Torch dependency on Pi. The exact implementation digest is part of the contract.
Both map indexing and camera queries use that same implementation. Camera
retrieval uses raw recorded pixels; rectification remains part of the separate
map-conditioned student's input contract.

Export compares the resulting tensors against upstream Torchvision's tensor
Normalize/Resize pipeline, then compares **official-preprocessed PyTorch model
outputs against CPU-preprocessed ONNX outputs**. Float resampling is not bitwise
identical: preprocessing limits are atol 2e-4 / rtol 1e-5; full embedding limits
remain atol 1e-4 / rtol 1e-3. A failure at either stage prevents a final manifest.
Raw parity images and official reference tensors are stored in a checksummed
`preprocessing_parity.npz`; startup reruns preprocessing and model parity on the
actual CPU host. Export records Torch, Torchvision, Pillow and ORT versions.

Old OpenCV-linear exports remain readable under their original explicit ID and
retain their preprocessing limitation. They are not silently migrated. Rebuild
the export and map index to use the new input contract. Numerical parity is
still not published retrieval accuracy or held-out UAV-to-map validation.

[Official MegaLoc interface](https://github.com/gmberton/MegaLoc) and
[pinned source](https://github.com/gmberton/MegaLoc/tree/5fe0dd697c4a70ba3e23607f6716ab3c606b16db).
Code terms and checkpoint/data terms are separate records; neither a free-text
license field nor an export status constitutes field-use approval.

### Pinned Public Checkpoint

`research/megaloc-checkpoint-source.json` records the public checkpoint from
[gberton/MegaLoc](https://huggingface.co/gberton/MegaLoc/tree/37bb43d65dd6388d1578052de5eb0bcdceb497e7),
revision `37bb43d65dd6388d1578052de5eb0bcdceb497e7`. The exporter optionally verifies
this record against the local weights, license and model-card hashes and embeds
the source record in provenance. The supplied record applies only to this exact
download, not arbitrary weights bearing the MegaLoc name.

```bash
hf download gberton/MegaLoc model.safetensors LICENSE README.md config.json \
  --revision 37bb43d65dd6388d1578052de5eb0bcdceb497e7 \
  --local-dir /workspace/weights/megaloc
```

Weights are 914,577,436 bytes and SHA256
`d4f9f2bcb60018f91eb6a8e061ed054fd55654e10c2569cf13841ea986ffb4f8`.
The model card and accompanying license state MIT; original training data
provenance still requires a separate field-release review. Downloading is a
separate explicit step; export and runtime never download models. Source loading
does not create bytecode files in the clean pinned checkout.

## Build The Selected Map Index

Use the reference map and tile layout already prepared for camera replay:

```bash
python -m vision_nav.neural.replay_retrieval \
  --retrieval-export /data/models/megaloc-shadow-001 \
  --reference-map /data/reference/reference_map.json \
  --tile-layout /data/map-tiles/tile_layout.json \
  --output-dir /data/retrieval/area-001
```

Installed command: `vision-nav-build-replay-retrieval`. This streams map images
through the retriever and produces `embeddings.npz`, `tiles.json` and a manifest.
Tile imagery, source layout, reference map and retriever export are bound by
checksums. Rebuilding either map or model requires a new index.

The exact cosine index reuses the project's NumPy retrieval metadata/reader.
Embeddings have a 256 MiB raw-vector limit; this is not a total RSS guarantee.
Loader normalization, inference weights and temporary arrays consume additional
memory. Smaller mission areas are required when this limit is exceeded; FAISS
compression and larger-map scheduling remain separate evaluation work.

## Run Automatic Replay

```bash
python -m vision_nav.neural.map_conditioned_replay \
  --session /data/captures/prop-off-session \
  --export-dir /data/models/crossview-001 \
  --reference-map /data/reference/reference_map.json \
  --tile-layout /data/map-tiles/tile_layout.json \
  --calibration /data/calibration/down-camera.yaml \
  --retrieval-export /data/models/megaloc-shadow-001 \
  --retrieval-index /data/retrieval/area-001 \
  --retrieval-top-k 4 \
  --output-dir /data/replays/automatic-001
```

Retrieval runs once for each complete accepted temporal window, from its newest
camera frame. Warmup, bad imagery, clock failures and frame gaps do not trigger
searches. A retrieval/provider failure rejects the frame and clears temporal
context. No previous result is silently substituted. Candidate image tensors
use a four-entry cache; all saved mosaics are not loaded at startup.

The trace records query-frame hash, candidate rank/cosine score, selected tile
image hashes and the student evidence. These scores are not calibrated position
probabilities. Summary provenance binds both model exports and the map/index,
and counts retrieval calls separately. The deterministic decision hash excludes
processing latency. No high-rate live scheduler or Pi thermal claim is implied.

## Verification And Remaining Work

```bash
python -m pytest -q tests/test_replay_retrieval.py tests/test_map_conditioned_replay.py
python tests/run_unit_tests.py
```

The default tests execute actual small synthetic ONNX models. They cover
selection, shared indexing/query behavior, no-PyTorch
replay, GPS independence, lazy tile loading, bad-provider rejection, export
parity reruns and map/model/checksum mismatches. New preprocessing tests cover
up/downsampling, high-frequency imagery and tampered input/reference evidence.

The official checkpoint was exported and reloaded on the arm64 Mac on September
5, 2026. The local final artifact is
`research/outputs/megaloc-onnx-cpu-20260905-r2` (ignored by Git). Three parity
inputs were the pinned XFeat upstream sample pair plus a generated checkerboard,
not a flight dataset. Maximum normalized-input error was about 3.95e-5 and
maximum embedding error about 9.75e-7; descriptor dimension is 8448. Source and
checkpoint digests, examples, versions and graph checksums are in the manifest.
These measurements demonstrate export compatibility, not localization accuracy.

Run the opt-in actual checkpoint tests after explicitly preparing the artifact:

```bash
VISION_NAV_MEGALOC_EXPORT=/data/models/megaloc-shadow-001 \
  python -m pytest -q tests/test_megaloc_checkpoint.py
```

These rerun raw-image-to-embedding parity without Torch imports and connect the
real retriever to synthetic map indexing and deterministic student replay.
The student and geography in that integration test remain fixtures. UAV
retrieval recall, downstream learned geometry accuracy, live scheduling and
sustained Pi measurements remain unverified. All paths remain `NO_GLOBAL_FIX`
with PX4 output disabled.

The broader 14-module regression run on this host passed **231 tests**, with
both `VISION_NAV_MEGALOC_EXPORT` and `VISION_NAV_LOCAL_REFERENCE_CONFIG` set to
the actual prepared artifacts. No checkpoint tests were skipped in that run.
The separate unit runner completed 104 checks (overlapping coverage, not an
additional unique-test count). Existing Torch exporter and Rasterio warnings
remain. No Pi deployment, paid cloud job or flight output was enabled.
