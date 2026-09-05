# Bearing-UAV Environment Upgrade Evidence

## Scope

The old reference environment is retained for reproduction. A separate
PyTorch 2.13.0 / torchvision 0.28.0 experiment tests whether the pinned released
model works with a newer framework. This version pair is listed in the
[official PyTorch installation archive](https://pytorch.org/get-started/previous-versions/).
It is not presented as the newest version or a fully maintained dependency stack.

The experiment keeps Python 3.11 and the reference NumPy 1.26.4, OpenCV and
imgaug versions. Upstream imports its legacy augmentation module even during
inference. This isolates the Torch change; it does not solve NumPy 2 support or
complete the dependency security review. No upstream source, architecture,
checkpoint or preprocessing is replaced.

`research/requirements-bearinguav-torch213-experiment.txt` records the core pins.
It is not a complete transitive lock and is **not** used by the Linux setup script.
Use a new isolated environment to test it; do not upgrade the Pi, system Python,
the reference environment, or an in-progress training run in place.

## Measured Local Result

On September 5, 2026, the same digest-pinned official checkpoint and five 256x256
synthetic patches ran in separate Python 3.11.16 processes on macOS arm64 CPU,
with two Torch threads:

| Measurement | Torch 2.1.2 vs 2.13.0 |
| --- | --- |
| Maximum normalized XY difference | 2.905726432800293e-7 |
| Maximum direction-vector difference | 1.1920928955078125e-7 |
| Repeated inference within each process | Exact |
| Comparison tolerance | abs 1e-6 + rel 1e-5 times reference magnitude |
| Result | Passed for this input/environment pair |

These are **normalized model output differences**, not meters or heading errors.
This fixture carries no geographic accuracy evidence. Tiny differences are
expected numerical evidence, not bitwise equality across framework versions.

Local ignored evidence:

- `research/outputs/bearinguav-torch212-cpu-20260905-parity-r1.json`
- `research/outputs/bearinguav-torch213-cpu-20260905-r1.json`
- `research/outputs/bearinguav-torch213-parity-20260905-r1.json`

Each reference report records source, checkpoint, configuration, adapter and
image digests. The comparison also records hashes of both full reports.
The newer environment's dependency consistency check passed; that does not
constitute a vulnerability audit.

The focused reference/evaluation/conversion/training/recovery/environment suite
passed **97 tests** under Torch 2.13.0, with the real source and checkpoint
explicitly provided. This includes exact uninterrupted-versus-resumed optimizer
tests **within** that environment on synthetic data; it does not claim identical
training trajectories across Torch versions. The comparison-only suite also
passed **29 tests** in the Torch 2.1.2 reference environment.

## Repeat The Comparison

Run `vision_nav.neural.bearinguav_reference` once under each environment with
the exact same `--sample`, `--source`, `--checkpoint` and `--configuration`,
writing different new `--output` files. See the
[reference evaluation guide](bearinguav-reference-evaluation.md) for those inputs.
Then compare the reports in either project environment:

```bash
PYTHONPATH=src python -m vision_nav.neural.bearinguav_parity \
  --reference /path/to/reference-result.json \
  --candidate /path/to/candidate-result.json \
  --output /path/to/new-parity-result.json
```

The installed entrypoint is `vision-nav-compare-bearinguav-environments`.
The command rejects different experiments, missing provenance, nondeterministic
reports, nonfinite/wrong-shaped outputs and reports claiming pose authority.
Numerical drift outside the fixed tolerance writes a failed report and exits
nonzero. Existing output evidence is never overwritten. This compares recorded
reports, not their authenticity; retain the underlying inputs and environment.

## Still Required Before Adoption

- Run official and held-out flight inputs, including wrong-map conditions, at
  the intended image sizes. One synthetic input is not dataset-wide parity.
- Repeat on the selected Linux/CUDA host, with its actual driver and GPU.
- Verify bounded training, optimizer recovery, throughput and peak memory there.
- Review the full dependency lock, model/weights/data permissions and security.
- Test export and Pi inference separately. CPU Torch compatibility is not ONNX,
  Pi thermal, AI HAT or flight qualification.

Training recovery intentionally binds framework/environment identity. Do not
resume an old Torch optimizer snapshot under a new environment. Start a new
experiment from the explicitly approved checkpoint and keep the original run.

No result here enables PX4 publication. The RunPod reference handoff remains
unchanged until a newer Linux/CUDA environment has its own evidence.
