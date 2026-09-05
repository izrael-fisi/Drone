# Portable Video2BEV Research Workspace

## Purpose And Scope

This adapter resolves the specific second-stage source portability problems
found in the [reproduction audit](video2bev-reproduction.md). It creates a new
copy of the pinned upstream checkout and records original/adapted file hashes.
The original Git checkout remains unchanged. The generated workspace is not a
qualified training environment or a Pi runtime component.

Only three files change:

| File | Adaptation |
| --- | --- |
| `train_bev_paired_fsra.py` | Remove two unused imports of absent modules; require `--sd-negative-root` |
| `tool/make_dataloader.py` | Pass that explicit root to both data-loader constructors |
| `tool/Dataloader_University.py` | Read the supplied root instead of the author's absolute path |

The adapter uses parsed Python nodes and fails if the expected source structure
differs. It preserves unrelated source text rather than rewriting whole modules.
The model definition, loss code, augmentation settings and optimizer defaults
are not changed. The copied upstream MIT license and notices are retained;
weights and dataset licensing still require independent review.

## Prepare

Use the pinned second-stage checkout from `research/upstreams.lock.json`:

```bash
PYTHONPATH=src python -m vision_nav.neural.video2bev_workspace prepare \
  --source research/vendor/workflows/video2bev_second_stage_v1 \
  --output /workspace/research/video2bev-second-stage-portable
```

The installed command is `vision-nav-video2bev-workspace`. It refuses an existing
output, a modified checkout, extra Python sources or a different Git revision.
Copying/adaptation finishes in a staging directory and is published only after
all three expected changes have been checked. `workspace.json` contains source
and workspace hashes plus the adapter digest. Keep this generated copy outside
the product source tree or in ignored `research/outputs`.

The upstream `train.sh` still contains author-specific paths. **Do not run it
unchanged.** When a reviewed training launch is eventually prepared, invoke the
adapted Python entrypoint directly with the intended options, including:

- `--data_dir` for the organized training folder.
- `--first_stage_weight_path` for the compatible, verified first-stage artifact.
- `--sd-negative-root` for the supplemental synthetic negatives.

No training process is launched by the preparation command.

## Exercise The Real Data Pipeline

```bash
PYTHONPATH=src python -m vision_nav.neural.video2bev_workspace smoke-data \
  --workspace /workspace/research/video2bev-second-stage-portable \
  --dataset /workspace/UniV/45/2fps/train \
  --negatives /workspace/UniV-supp/negative-samples \
  --input-kind recorded \
  --output /workspace/reports/video2bev-data-smoke.json
```

Use actual paths matching the supplemental data organization, not the illustrative
folder name above. Main-data layout is documented in the reproduction guide;
negative images must be under `satellite/<class-id>/*.jpeg` and
`bev/<class-id>/*.jpeg`. Synthetic fixtures must use `--input-kind synthetic`.

The smoke command verifies workspace hashes, audits primary images and then
imports the adapted upstream `tool.make_dataloader` in an isolated package
namespace. It runs the upstream resizing/augmentation/normalization, sampling
and collation for one batch from each of its two loaders. Settings are 256px,
batch size 2, one repeat, zero worker processes and seed 0. No model or checkpoint
is loaded and no CUDA operation, network access or cloud provisioning is requested.

Before importing the loader, the command now decodes and hashes every supplemental
negative eligible for the selected BEV classes. It mirrors the upstream
`*.jpeg` glob, including hidden-file exclusion and suffix case behavior, rather
than checking only a randomly sampled file. Both negative modalities need every
selected class. Invalid images, escaping paths and audits exceeding 100,000
eligible images fail closed. Images have the same 32 MiB/8192px limits as the
primary-image audit. An incomplete negative audit has no inventory digest.

The report includes both inventory digests, actual output tensor
shapes/dtypes/digests, class count and framework version. The negative audit covers
only eligible images for selected classes, not ignored suffixes or unused classes.
It does not establish licensing or whether generated negatives are semantically
correct. Loader execution still exercises only the first batch of each loader;
this is not complete training, sampling-equivalence or model-accuracy qualification.
Original random file enumeration is retained, so the seed alone does not promise
portability of sample order across filesystem layouts.

## Exercise The Model Without Weights

```bash
PYTHONPATH=src python -m vision_nav.neural.video2bev_model_smoke \
  --workspace /workspace/research/video2bev-second-stage-portable \
  --output /workspace/reports/video2bev-model-smoke.json
```

The installed command is `vision-nav-smoke-video2bev-model`. It checks the
prepared workspace before importing the unchanged upstream model. It selects
the upstream `vit-small` branch, which constructs locally without pretrained
downloads. This is not the trainer's default `swin-base` configuration.

The bounded CPU experiment uses seed 0, batch size 2 and 256px synthetic random
tensors for both anchor directions and their supplemental negatives. It runs
the shared encoder, upstream fusion module, binary matching head and actual
`get_matching_loss_two_view` function, then one AdamW update. Five original
function definitions are compiled without running the trainer's module-level
argument parsing, file setup, checkpoint loading or CUDA launch. Their bodies
are unchanged and their source digest is retained.

The encoder is frozen and in evaluation mode, matching the trainer's
`set_eval` behavior; the matching modules remain in training mode. The actual
similarity-weighted sampler selects one negative per query, producing two
positive and eight negative pairs per anchor direction. Both anchor losses are
averaged, independently checked against their captured matching logits. The
report retains the original negative-selection log. A separate test exercises
the sampler's three-negative top-K branch, query exclusion, supplemental
self-class negatives and ordered tensor repetition.

This is the actual matching-loss code on **synthetic inputs and random weights**,
not an official training reproduction. It does not join the real data loaders
to a pretrained trainer, evaluate geographic truth, use the default three
negatives in the full-model smoke, or exercise optional classification and label
smoothing. Optimizer settings are bounded smoke settings, not paper defaults.

Checks require finite outputs/gradients/parameters, nonzero matching gradients,
unchanged encoder state and changed fusion/head states. The JSON report records
source and runner hashes, tensor hashes, model size and scope limitations. It
does not save or promote the random model. It loads no checkpoint and establishes
neither weight compatibility nor learned localization performance.

Local CPU evidence uses Torch 2.13.0: 56,429,826 total parameters and 8,398,338
trainable matching parameters. Repeated runs on the same environment have equal
tensor/state digests and loss. The elapsed time in this report includes setup
and hashing; it is not an inference latency benchmark or a Pi measurement.

## Verification And Remaining Work

Local tests execute both real upstream loaders using two synthetic class IDs,
including the empty-Google-directory fallback to street images. They also check
source immutability, unchanged model bytes, missing negatives, changed/injected
workspace code, unexpected patch structure and UTF-8-safe edits.
Model tests additionally exercise actual forward/backward execution, repeated
seeded results, frozen-encoder integrity, forbidden checkpoint/download calls,
and cleanup of import namespace, thread count and CPU random state.

Local ignored artifacts:

```text
research/outputs/video2bev-second-stage-workspace-20260905-r1/
research/outputs/video2bev-loader-fixture-20260905-r1/
research/outputs/video2bev-loader-smoke-20260905-r3.json
research/outputs/video2bev-model-smoke-20260905-r2.json
```

Still required: stage-specific weight acquisition and compatibility, a reviewed
Linux/CUDA dependency environment, the default Swin backbone and complete
pretrained trainer execution, full real-data/negative audits, official evaluation
and resource measurements.
The older `video2bev-model-smoke-20260905-r1.json` remains a historical report
of simplified pair loss with a training-mode frozen encoder. Its scope and
runner digest differ; it is not evidence for the current upstream-loss check.
Do not route this prepared copy through the pristine-checkout preflight and
expect its Git pin to pass: it is intentionally an explicitly adapted workspace,
validated through its own manifest. The original checkout remains the upstream
reference and continues to report its unmodified portability issues.
