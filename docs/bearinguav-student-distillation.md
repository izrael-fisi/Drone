# Bearing-UAV To Map-Conditioned Student

## What Is Implemented

The actual pinned Bearing-UAV checkpoint can now produce training-only point
targets for our compact map-conditioned student. A completed local Bearing-UAV
fine-tuning run can also supply the teacher, provided its dataset and training
membership match the target-export dataset.

This is an optional auxiliary loss, not a reproduced Bearing-UAV architecture,
Video2BEV temporal distillation, or a qualified `pi5_crossview_nav_v1` release.
The student architecture and inference inputs remain unchanged. There is no
teacher dependency on the Pi and no change to flight-control output.

## Target And Loss Contract

- Only **training** windows receive teacher targets. Validation images are
  audited for separation but are not passed to the teacher target exporter.
- Each converted case is bound to the original window, session, candidate
  index, map bytes, query bytes, calibration and pixel geometry. Both dataset
  manifests are recorded, along with source/config/checkpoint digests.
- The teacher is run only on positive contexts selected by the training labels.
  This is explicitly oracle-context training, not demonstrated map retrieval.
- Published normalized block XY becomes candidate UV by `uv = xy / 4 + 0.5`.
  Out-of-map points are withheld, never clipped to an edge. A package with no
  usable teacher points cannot enable distillation.
- Student spatial logits are normalized **without** the no-match class. Their
  expected grid-cell-center UV receives Smooth L1 point loss with beta 0.1.
  An explicit weight in `(0,1]` scales this auxiliary loss.
- Ground-truth cell/no-match cross-entropy remains in place. Negative candidates
  and the no-match logit get no gradient from the auxiliary point loss.
- Teacher direction output is not used as heading truth. There is no fabricated
  teacher probability, distribution, uncertainty or pose covariance.
- Validation scores remain based on independent supervised labels with no
  teacher loss. Student/teacher agreement is not an accuracy metric.

Matching a distribution's expected point does **not** teach its full spatial
shape or ambiguity. A diffuse/multimodal student can share that expectation.
The normal supervised objective and subsequent rejection/calibration evaluation
remain essential. This small point-loss experiment does not replace the planned
temporal, bearing-distribution and geometry teacher signals.

## Generate Targets

First prepare a portable cross-view dataset, then convert that exact dataset
using the [Bearing-UAV converter](bearinguav-dataset-converter.md). Use the
isolated teacher environment described in the reference/training guides.

```bash
PYTHONPATH=src python -m vision_nav.neural.bearinguav_targets \
  --dataset /workspace/datasets/crossview-v1 \
  --converted /workspace/datasets/bearing-v1 \
  --source research/vendor/bearinguav \
  --configuration /workspace/models/bearinguav/cross_view/training_configure.json \
  --checkpoint /workspace/models/bearinguav/cross_view/best_model.pth \
  --output /workspace/targets/bearing-v1.json \
  --device cuda --threads 2
```

Use `--device cpu` for a bounded local check. The installed entrypoint is
`vision-nav-build-bearinguav-targets`. To use a verified completed fine-tune,
replace `--checkpoint ...` with `--training-run /workspace/runs/bearing-v1`.
Failed/incomplete runs or mismatched dataset membership are rejected. Neither
choice downloads weights or provisions compute.

## Train And Compare

In the student training environment:

```bash
PYTHONPATH=src python -m vision_nav.neural.map_conditioned_student \
  --dataset-dir /workspace/datasets/crossview-v1 \
  --output-dir /workspace/runs/student-point-distillation-v1 \
  --teacher-targets /workspace/targets/bearing-v1.json \
  --teacher-weight 0.25 --device cuda --epochs 10 --window-size 4
```

Run the same seed/settings/dataset without the two teacher arguments into a
separate new directory for the supervised control. Choose the weight using
validation only; keep independent test routes for final evaluation. The example
weight and epoch count are starting experiment settings, not tuned values.

Reports record per-epoch teacher point count/loss separately, target-file digest,
teacher lineage and the loss implementation digest. The normal ONNX exporter
preserves those fields and runs numerical/decision parity. Teacher targets are
offline training files, not inputs to the exported network.

## Local Evidence And Remaining Gates

September 5 synthetic CPU smoke evidence:

- Official and locally fine-tuned teachers each generated one usable positive
  target; the negative candidate was explicitly excluded.
- Two epochs trained a 23,067-parameter debug student with two 64px frames,
  4x4 map grid and 16 feature channels. These are test settings, not the default
  deployable architecture or a measured optimum for Pi.
- The distilled model exported to ONNX and passed both validation candidates;
  maximum location-logit difference was approximately `6.71e-8` and heading-logit
  difference `2.98e-8`. Decisions agreed.
- Synthetic validation positive-cell accuracy was **0**. This evidence proves
  execution and artifact lineage, not improved localization or useful accuracy.
- Tests verify real teacher inference, changed student weights versus the
  supervised control, isolated negative/no-match gradients, zero validation
  teacher loss, mismatched identities and ONNX shadow inference.

Ignored local artifacts use these paths:

```text
research/outputs/bearinguav-student-targets-20260905-r1.json
research/outputs/bearinguav-student-finetuned-targets-20260905-r1.json
research/outputs/bearinguav-distilled-student-20260905-r1/
research/outputs/bearinguav-distilled-student-onnx-20260905-r1/
```

Real geographic holdout data, teacher improvement over supervised controls,
calibrated heading/ambiguity, Linux/CUDA execution, Pi resource tests, and
weights/data/map permissions remain required. All artifacts stay research-only
with PX4 external vision disabled. The experimental Torch stack still includes
legacy dependencies; see [environment limits](bearinguav-environment-parity.md).
