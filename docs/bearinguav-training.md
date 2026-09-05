# Bearing-UAV Research Fine-Tuning

The existing capture-to-map converter now connects to a real optimizer loop for
the pinned official `PARCASGM_v5a` network. This is domain adaptation of that
network, not a reproduction of its paper training protocol, a temporal student,
or a deployed navigation system.

## What Is Implemented

- Verify the complete converted dataset before constructing the model, including
  training images and the actual training population behind the holdout audit.
- Initialize from the digest-pinned official checkpoint; no implicit downloads.
- Train positive contexts with normalized block-position Smooth L1 loss and
  AdamW, respecting the upstream configuration's frozen/trainable parameters.
- Shuffle deterministically with an explicit seed, clip gradients, reject
  nonfinite outputs/losses/gradients/parameters, and keep images in bounded batches.
- Evaluate every validation case, including wrong maps, after each epoch.
- Select the best trained epoch by mean validation normalized-position error.
  Initial checkpoint validation is retained separately; the report explicitly
  says whether the trained model improved over it. No test-set selection.
- Save best and last checkpoints, per-epoch metrics, initial/best validation
  predictions, checksums, dataset/model/code identities and environment versions.
- Reload the selected checkpoint through the existing audited batch evaluator.
- Resume model, optimizer and random state from a completed epoch, without
  overwriting the original run or silently changing its experiment settings.

Direction supervision is disabled. The source labels do not yet establish a
camera/map heading convention; body yaw must not substitute for it. Shared model
features may change direction predictions even without a direction loss. Treat
them as unqualified. Wrong-map samples have no valid regression target and are
excluded from optimizer updates, but remain in validation. No no-match head,
negative contrastive objective or learned confidence is added by this trainer.

## Before Running

Use the isolated Python 3.11 environment and source/weight pins in
[reference setup](bearinguav-reference-evaluation.md). Do not install the legacy
Torch stack into the Pi runtime. Use only trusted, verified artifacts, without
exposing that environment as a service. A maintained training-stack migration
and GPU parity check are still required.

Build inputs using the [dataset converter](bearinguav-dataset-converter.md).
Real captures need calibrated/stabilized camera imagery, synchronized telemetry,
reviewed offline reference labels and session-disjoint validation. Resolve map,
dataset and pretrained-weight permissions before proprietary/commercial training
or distribution; this code does not establish those rights.

## Train

From the repository root, with a new output directory:

```bash
PYTHONPATH=src .venv-bearing-reference/bin/python -m vision_nav.neural.bearinguav_training \
  --dataset /absolute/path/to/converted-bearing-dataset \
  --source research/vendor/bearinguav \
  --checkpoint research/outputs/bearinguav-source-31098d2/Bearing_UAV/cross_view/best_model.pth \
  --configuration research/outputs/bearinguav-source-31098d2/Bearing_UAV/cross_view/training_configure.json \
  --output /absolute/path/to/new-bearing-training-run \
  --epochs 1 --batch-size 4 --learning-rate 0.0001 --seed 7 \
  --device cpu --threads 2
```

Installed entrypoint: `vision-nav-train-bearinguav`. On an explicitly provisioned
RunPod GPU environment, the same command accepts `--device cuda`. CUDA failure
does not silently fall back to CPU. No provisioning, billing, file transfer or
remote job is performed by this command. The implementation has been tested on
Mac CPU only; CUDA memory, determinism and throughput are not yet qualified.
Store outputs on the intended persistent research volume when running remotely.

The [RunPod handoff](runpod-bearinguav-training.md) provides a hash-locked Linux
setup and executable CUDA preflight. It is prepared but has not run on a GPU;
do not use the general research container as proof of this reference environment.

Epochs produce JSON progress lines. Completed runs are published from a staging
directory only after final dataset revalidation. Failure leaves a hidden sibling
`.bearing-train-*` directory with `FAILED.json` and any completed-epoch artifacts,
but no valid completed run. The last committed recovery snapshot may be used
by the explicit resume command below. Never rename partial artifacts into a
completed release.

## Resume An Interrupted Run

Every completed epoch now writes a self-contained recovery snapshot containing
current/best weights, AdamW moments and step counters, Python/NumPy/Torch/CUDA
random states, shuffle-generator state, metrics and experiment context. A small
`resume.json` pointer is replaced only after its hash-bound snapshot is complete.
The previous snapshot is retired after the new pointer is committed. Temporary
or orphaned files are never chosen by filename guessing.

Use `--resume-from` with a completed run directory or the printed hidden staging
directory from an interrupted run. Add it to the original training command,
choose a **new** output directory, and set `--epochs` to the desired total:

```text
--resume-from /workspace/experiments/.bearing-train-PREVIOUS \
--output /workspace/experiments/bearing-resumed-v2 \
--epochs 10
```

If epoch 6 was the last committed snapshot, this executes epochs 7 through 10.
The interrupted portion of epoch 7 is repeated. Recovery is at epoch boundaries,
not individual minibatches. An interruption before the first committed epoch
still requires a fresh run. Older weights-only runs without `resume.json` cannot
recover optimizer state, though their checkpoints remain evaluable.

The original dataset digest, initial weights/configuration/source, trainer and
adapter code hashes, batch size, learning rate, seed and recorded environment
must match. Context mismatches, corrupt snapshots, incompatible best weights or
optimizer settings fail closed. The total epoch target may increase; other
settings must not silently change. Use trusted local artifacts only: hashes are
integrity checks, not signatures establishing who produced a checkpoint.

The source run is unchanged. Resumed reports preserve the whole history and
record parent pointer/checkpoint hashes, completed epoch and epochs executed in
the new run. CPU tests reproduce uninterrupted weights and history exactly after
a forced mid-run failure. CUDA and cross-machine reproducibility remain
unqualified; environment equality is not a claim of hardware-independent bits.
Filesystem durability depends on the host/storage: atomic, flushed writes protect
against partial snapshot selection but are not a replacement for volume backups.

Separate-process CLI evidence is saved under
`research/outputs/bearinguav-resume-smoke-20260905-r1-{start,resumed,full}`.
The resumed and uninterrupted two-epoch synthetic CPU runs have identical current
weights, best weights, optimizer state, random state and validation history.

## Re-Evaluate

```bash
PYTHONPATH=src .venv-bearing-reference/bin/python -m vision_nav.neural.bearinguav_evaluation \
  --manifest /absolute/path/to/converted-bearing-dataset/evaluation.json \
  --source research/vendor/bearinguav \
  --configuration research/outputs/bearinguav-source-31098d2/Bearing_UAV/cross_view/training_configure.json \
  --training-run /absolute/path/to/new-bearing-training-run \
  --output /absolute/path/to/new-evaluation-report.json --device cpu
```

Choose `--training-run` or the original `--checkpoint`, never both. The evaluator
verifies the completed run's source/config/initial-checkpoint lineage, trained
checkpoint digest and training-membership identity. Evaluation against a new
held-out set must retain that actual training-membership document. Hashes prove
content identity, not independent attestation or permissions. Reported errors
remain block-normalized, not metres, unless separately qualified metric labels
are supplied to the evaluator.

## Evidence And Remaining Work

Tests cover actual-checkpoint optimizer updates, identical repeated CPU training,
save/reload validation parity, changed parameters, membership/split/policy checks,
corruption, failure artifacts, atomic recovery, optimizer/RNG resume and immutable
completed outputs. The saved CLI
exercise at `research/outputs/bearinguav-finetune-synthetic-20260905-r2` uses one
synthetic training query and one separate validation query, each paired with a
positive and a wrong map. It proves execution only, not generalization or paper
performance. Generated data and checkpoints remain ignored by Git.

Remaining: actual recorded-data evaluation/training; qualified direction labels;
official-dataset protocol reproduction; negative/recovery learning; calibrated
uncertainty; teacher-target export/distillation; mid-epoch resume; MLflow upload;
maintained Torch/CUDA qualification; ONNX and sustained Pi benchmarks.

All training and evaluation reports stay `NO_GLOBAL_FIX`, with
`position_authority=false`, `field_ready=false`, and PX4 transmission disabled.
