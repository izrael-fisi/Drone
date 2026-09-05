# RunPod Bearing-UAV Training Handoff

## Current Status

This is a prepared deployment path, not a completed RunPod deployment.

- The actual model's optimizer/save/reload workflow has CPU synthetic evidence.
- The Linux x86_64/Python 3.11 dependency lock resolves to 51 pinned packages with
  SHA-256 hashes, including the CUDA dependencies of the reference Torch wheel.
- Cross-platform installation **dry-run** passed; no Linux packages were installed
  into the Mac environment. This is dependency-resolution evidence, not execution.
- The environment check runs real convolution/backpropagation and matrix probes.
  Requesting CUDA never silently falls back to CPU.
- GPU installation, model parity, memory and throughput are not yet verified.
- A separate [Torch 2.13 CPU compatibility experiment](bearinguav-environment-parity.md)
  passes same-checkpoint synthetic inference comparison. It does not replace
  this reference lock or establish Linux/CUDA compatibility.
- Local RunPod CLI authentication did not verify on September 5. No Pods,
  volumes, templates, endpoints or training jobs were created.

## 1. Reconnect Before Cloud Work

On the machine used to manage RunPod, complete local CLI setup interactively:

```bash
runpodctl doctor
runpodctl user
```

Keep credentials local, outside this repository and chat. See
[RunPod API key guidance](https://docs.runpod.io/get-started/api-keys).
A successful CLI check is required before provisioning; a documentation
connection alone is not an authenticated infrastructure connection.

Before allocating anything, agree on GPU/hourly budget, storage capacity,
region, maximum job duration and cleanup ownership. No script here provisions
or tears down paid resources.

## 2. Choose And Record The Host

Prefer an official NVIDIA PyTorch Pod template as the host, then create the
isolated environment below. Do not replace the template's system Python or Torch.
Inspect current template metadata rather than assuming its Python/CUDA versions:

```bash
runpodctl template list --type official
```

Record the selected template ID, image tag/digest, GPU, driver, datacenter,
storage mount and Pod ID with the experiment. Template configurations include
image, ports, environment and startup behavior; inspect all of these before
deploying. Batch training needs no public model or notebook service.
[RunPod templates](https://docs.runpod.io/pods/templates/overview).

This reference stack uses old Torch 2.1.2. Its presence on a modern GPU is not
proof of support; pass the CUDA execution check and actual bounded model run.
Never expose this legacy environment as a service or load untrusted checkpoints.
It is a reproduction environment, not our final production training stack.

Place datasets, approved weights, source and experiment outputs on explicitly
selected persistent storage. RunPod network volumes typically mount at
`/workspace` and survive compute termination, but have separate storage charges.
Confirm the actual mount and maintain an independent artifact backup; a directory
named `/workspace` alone does not prove persistence.
[Network volume documentation](https://docs.runpod.io/storage/network-volumes).

## 3. Prepare The Isolated Environment

On the already provisioned Linux x86_64 host, make this repo available as
`/workspace/Drone`, with the reviewed changes included. The following is a path
example; substitute the actual persistent mount when it differs.

Install `uv` through the host's approved setup. Then:

```bash
cd /workspace/Drone
bash scripts/research/setup_bearinguav_linux.sh /workspace/envs/bearing-reference-v1
```

The script creates a **new** Python 3.11 virtual environment. `uv` may download
Python when that interpreter is unavailable. It installs only hash-verified
prebuilt wheels from `research/requirements-bearinguav-linux.lock`, checks
dependency consistency, verifies the exact core versions/imports and Linux
architecture, and executes CUDA convolution/backward/matrix operations.
It writes `/workspace/envs/bearing-reference-v1/bearing-environment.json`.

It refuses existing environment paths and will not modify system packages.
Failure leaves the environment for inspection. Do not treat it as ready or
rerun with hash checks removed. Missing native OS libraries, GPU drivers or CUDA
kernel support require fixing the chosen host, not CPU fallback. The reference
OpenCV wheel needs its normal Linux runtime libraries; an import failure appears
in the report.

The dependency lock was generated with `uv 0.12.10` for CPython 3.11 and
`x86_64-manylinux_2_28`. To intentionally refresh it on a development machine:

```bash
uv pip compile research/requirements-bearinguav-reference.txt \
  --python-version 3.11 --python-platform x86_64-manylinux_2_28 \
  --generate-hashes --no-build \
  --output-file research/requirements-bearinguav-linux.lock
```

Review changes and requalify the resulting environment. Hash pinning does not
make old dependencies secure or prove runtime compatibility. The setup reports
core package compatibility; `uv` enforces the complete lock at installation.

## 4. Validate Inputs And Run One Epoch

Bring the digest-pinned official model/configuration, a clean pinned upstream
checkout, and the portable converted dataset described in
[Bearing-UAV training](bearinguav-training.md). Explicitly resolve weights/map/
dataset permissions before proprietary or commercial training. Downloads are
not hidden inside model loading.

```bash
cd /workspace/Drone
PYTHONPATH=src /workspace/envs/bearing-reference-v1/bin/python \
  -m vision_nav.neural.bearinguav_training \
  --dataset /workspace/datasets/bearing-captures-v1 \
  --source research/vendor/bearinguav \
  --checkpoint /workspace/models/bearinguav/cross_view/best_model.pth \
  --configuration /workspace/models/bearinguav/cross_view/training_configure.json \
  --output /workspace/experiments/bearing-cuda-smoke-v1 \
  --device cuda --epochs 1 --batch-size 1 --threads 2
```

Paths above must contain real reviewed artifacts; they are not downloaded or
created automatically. Start with a small reviewed dataset and one epoch to
measure fit and validate output. Use the existing batch evaluator's
`--training-run` path to reload and compare the result before a longer job.
Keep validation footage separate from training and retain wrong-map cases.

For long runs, arrange supervised execution that survives an SSH disconnect and
records process exit status. A running Pod or an old log is not evidence that a
training process is live. The trainer now supports explicit, hash-bound
[epoch-boundary optimizer/RNG recovery](bearinguav-training.md#resume-an-interrupted-run).
Use a new output directory and the original settings/code with `--resume-from`.
It does not transparently restart a Pod, recover a deleted volume or resume an
unfinished minibatch. CPU interruption parity is tested; GPU recovery still needs
qualification before relying on it for a long cloud job.

## 5. Evidence Before Scaling Or Cleanup

Keep the environment report, source and dataset identities, `training_run.json`,
best/last checkpoints, checksums, initial/best validation and reloaded evaluation.
Compare CPU/CUDA predictions and repeated-run behavior; measure model training
memory and latency. The small tensor probe does not establish those properties.
Archive verified artifacts before releasing compute. Confirm resource state in
RunPod; stopping a Python process does not stop billing, and retained storage
still costs money. Delete storage only after explicit approval and verified backup.

No artifact from this path is automatically promoted to the Pi. The learned
runtime remains shadow-only and PX4 external vision stays disabled.
