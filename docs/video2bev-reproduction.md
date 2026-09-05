# Video2BEV Reproduction Readiness

## Architectural Meaning

Video2BEV uses 3D Gaussian-splat scene reconstruction to render overhead views,
then learns cross-view retrieval/matching. Its second training stage freezes
the encoder and trains matching components with hard negatives. It is not a
released, small online network mapping a few consecutive camera frames directly
to a calibrated BEV pose. [Paper](https://arxiv.org/html/2411.13610v3),
[official repository](https://github.com/HaoDot/Video2BEV-Open).

For this project, treat it as an **offline reconstructed-BEV teacher experiment**.
Transferring its representations to a short-window onboard model is separate
research. Do not rename the existing temporal pooling model as a Video2BEV
reproduction or substitute resized camera frames for reconstructed BEV data.
The published retrieval task also does not by itself establish metric position,
heading covariance or a PX4 measurement interface.

The upstream links UniV, supplemental BEVs/synthetic negatives, and stage-specific
weights. Those artifacts are not present locally. Download availability does not
establish commercial-use permission. Review source, weights, rendered imagery,
underlying imagery and dataset terms separately before contract-related use.

### Artifact Access Check

Checked September 5, 2026 against the [official download listing](https://github.com/HaoDot/Video2BEV-Open#weights):

- Weights are linked through Baidu. The browser safety policy blocks that
  destination in this environment; no workaround or download was attempted.
- The linked UniV Google Drive folder opens a Google sign-in page in this browser.
  No login credentials were entered or account permissions changed.
- The README also links a separate Google Drive folder for supplemental BEVs
  and generated negatives; its contents were not verified in this access check.

To continue pretrained execution, provide locally downloaded, official
first-stage checkpoint and matching `opts.yaml`, preserving their origin URL
and archive layout. Provide the organized UniV and supplemental paths separately.
Do not substitute a generic ViT initialization checkpoint for the trained
first-stage artifact. Their hashes, loading compatibility and usage permissions
still need verification after acquisition. No dataset or weights were acquired
during this check, and no paid compute was provisioned.

## Pinned Source Audit

The following immutable checkouts were materialized on September 5, 2026:

| Stage | Commit |
| --- | --- |
| First stage | `0b5a7aabbaf451d041b0d2089eaad1b11b8fba57` |
| Second-stage training | `d6737eb6ca3ead1bdfdc6f38b20347597ed5abce` |

No source files were changed. Inspection found:

- First-stage `train.sh` has author-specific working-directory/interpreter paths.
  Its ViT branch loads `pre_trained/vit_small_p16_224-15ec54c9.pth`; that file is
  not in the checkout. A directory containing arbitrary frames is insufficient.
- Second-stage `train_bev_paired_fsra.py` imports root-level `folder.py` and
  `random_erasing.py`, which are absent from the pinned second-stage checkout.
  Related files exist in the first-stage tree or under `tool`, but do not silently
  copy them and call the result an unchanged upstream reproduction.
- Second-stage `tool/Dataloader_University.py` assigns the synthetic-negative
  root to an author-specific absolute path. `--data_dir` does not override it.
  The pinned standard training command enables synthetic negatives.
- The shell commands use batch size 140 and FP16/Apex. These are upstream
  experiment settings, not a measured budget or a portable setup for our GPU.
- The second stage requires a compatible first-stage checkpoint; file existence
  alone cannot establish compatible architecture or successful weight loading.

An [isolated portable workspace adapter](video2bev-portable-workspace.md) now
resolves the two unused imports and explicit negative-root plumbing with recorded
file hashes. Its real data loaders have synthetic CPU smoke evidence. Parameter
loading, training launch configuration and a bounded forward/backward pass on
the intended Linux/CUDA environment remain required. Do not mutate the pinned
source checkout or treat loader execution as a full reproduction.

## Required Training Layout

`--dataset-root` must point directly at the organized training folder, for
example `UniV/45/2fps/train`, not the archive root or our cross-view JSON package.
The pinned loader enumerates all five view directories even in its two-view
training configuration:

```text
train/
  satellite/<class-id>/<image files>
  street/<class-id>/<image files>
  drone/<class-id>/<image files>
  google/<class-id>/<image files, possibly empty>
  bev/<class-id>/<rendered image files>
```

BEV class IDs define the sampler. Every such class must exist in the other
modalities. Google can be empty because the loader falls back to street;
street, satellite, drone and BEV must have decodable images. Class directories
must not contain nested folders or non-image files: upstream samples directory
entries without filtering them. Second-stage synthetic negatives additionally
need `satellite/<class-id>/*.jpeg` and `bev/<class-id>/*.jpeg` under their own
explicitly configured root after the portability adapter is implemented.

This layout is a reproduction dependency, not a requirement to add street or
Google-image inputs to our drone runtime. A proprietary-data adaptation needs
its own explicit modality/loss design and must not claim the unchanged protocol.

## Run The Preflight

```bash
PYTHONPATH=src python -m vision_nav.neural.upstream_training \
  --lock research/upstreams.lock.json \
  --workflow video2bev_first_stage_v1 \
  --checkout research/vendor/workflows/video2bev_first_stage_v1 \
  --dataset-root /workspace/UniV/45/2fps/train \
  --output /workspace/reports/video2bev-first-stage.json
```

For the second stage, use its workflow/checkout and supply `--checkpoint` for
the first-stage artifact. The preflight does **not** run shell scripts, load a
pickle/checkpoint, import upstream model code, download data or rent compute.

Checks include pinned Git contents, unexpected Python modules, required local
source files, missing backbone assets, the nonportable negative-root setting,
class coverage, full image decoding and deterministic image-inventory hashes.
The audit caps each image at 32 MiB/8192 pixels per side and 100,000 images total;
an incomplete audit does not pass. These are audit bounds, not paper settings.

`readiness_scope=source_and_input_preflight_not_training_execution` prevents a
successful input check being mistaken for working CUDA or a trained model.
Dataset split separation, reconstruction quality, calibration, checkpoint
compatibility, GPU capacity and rights remain independent qualification gates.

## Current Evidence

Both actual pinned checkouts produce **not_ready**, as expected. Ignored reports:

```text
research/outputs/video2bev-first-stage-preflight-20260905-r1.json
research/outputs/video2bev-second-stage-preflight-20260905-r1.json
```

No UniV corpus, supplemental BEVs, stage weights or training jobs were downloaded
or started in this pass. Eleven focused tests cover class-layout failures,
Google fallback, image corruption, escaping paths, image hashes, missing imports,
the hardcoded negative root and modified/shadow Python sources. This is source
and input-preparation evidence, not reproduced Video2BEV performance.
