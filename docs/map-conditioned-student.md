# Map-Conditioned Student Development

The new project-owned student receives both a temporal UAV view and candidate
map imagery. It is trainable/exportable research software, not a validated
navigation model or a reproduction of Bearing-UAV/Video2BEV. Optional
[Bearing-UAV point distillation](bearinguav-student-distillation.md) now adds
released-teacher supervision without claiming a qualified distilled release.
All outputs remain shadow-only.

## Model Contract

Inputs, fixed to one candidate per Pi ONNX invocation:

| Tensor | Shape | Meaning |
| --- | --- | --- |
| frame_window | 1,T,3,H,W | Rectified RGB images in [0,1], oldest to newest |
| candidate_map | 1,3,H,W | Selected georeferenced map crop, RGB in [0,1] |
| roll_pitch | 1,T,2 | PX4 roll/pitch radians; no absolute yaw input |
| time_offsets_s | 1,T | Sensor timestamps relative to the final frame, increasing to zero |
| camera_context | 1,11 | Normalized intrinsics, camera-to-body quaternion, translation |
| map_context | 1,4 | East/north metre vectors per normalized crop u/v step |

Separate spatial camera/map encoders feed cross-attention. Learned temporal
pooling uses image features and camera/attitude/time context. The local map grid
is retained rather than reducing the whole map to an absolute-position regressor.
Map-axis context preserves scale/orientation information for rotated crops.

Outputs are a distribution over map grid cells plus one explicit no-match class,
and a heading-bin distribution. Heading bins run clockwise from north over
[0,2*pi), matching PX4 yaw supervision. These are uncalibrated evidence, not
ENU coordinates or pose covariance. A positive prediction must still pass
learned correspondence, metric geometry and temporal/ambiguity checks.

## Data Preparation

Use the existing capture -> reference map -> labels -> map pairs -> temporal
windows workflow. Rebuild tile layouts/map pairs created before exact
`pixel_geometry` metadata was available. ENU axis-aligned bounding boxes alone
are rejected for location supervision; the builder inverts the actual raster
affine/CRS transform.

New preparation defaults to exposure/native-attitude timing. Rebuild indices
and windows from the latest recorder's evidence following [capture timing](capture-timing.md).
The map-conditioned builder requires checked per-frame timing audits by
default, verifies image identity/timestamps/attitude against them, and packages
them for portable training. Old windows require explicit
`--allow-legacy-timing` for research comparison only; this choice remains in
dataset, checkpoint and export provenance. Existing cross-view packages that
lack timing provenance must be rebuilt. No automatic timing fallback occurs.

Create a calibration registry JSON mapping measured calibration IDs to YAML
paths, relative to the registry file:

```json
{
  "down-camera-v1": "calibration/down-camera.yaml"
}
```

The builder verifies measured intrinsics, image dimensions, constant measured
camera-to-body transforms, positive-map containment and negative-map exclusion.
It copies and hashes camera/map/calibration files into a portable dataset.
Train/validation sessions and camera-image bytes may not overlap. Overlapping
geography is not yet automatically verified: the dataset explicitly records
`geographic_holdout_verified: false`. Do not present its scores as unseen-area
generalization without a separate route/geography audit.

```bash
python -m pip install -e '.[research,map-production]'
python -m pip install onnx onnxruntime pytest

python -m vision_nav.neural.crossview_dataset \
  --window-dir /workspace/data/temporal-windows \
  --calibration-registry /workspace/data/calibrations.json \
  --output-dir /workspace/data/map-conditioned-dataset
```

Repeat `--window-dir` to combine independent packages from different areas.
There is no one-map-only constraint on this student. IDs across packages must
remain globally unique. Existing source location labels retain their reference
point: if GNSS antenna lever arms have not been corrected, the builder does not
claim that they are camera-centre truth. Validate altitude datum, clock alignment,
label uncertainty and lever arms before substantive training.

## Train And Export

The default model uses four frames, 256-pixel inputs, an 8x8 location grid, and
36 heading bins. These are initial development settings, not measured optimal
Pi settings. Match `--window-size` to the recorded temporal package.

```bash
python -m vision_nav.neural.map_conditioned_student \
  --dataset-dir /workspace/data/map-conditioned-dataset \
  --output-dir /workspace/runs/crossview-001 \
  --device cuda --epochs 10 --window-size 4

python -m vision_nav.neural.map_conditioned_export \
  --checkpoint /workspace/runs/crossview-001/map_conditioned_student.pt \
  --dataset-dir /workspace/data/map-conditioned-dataset \
  --output-dir /workspace/exports/crossview-001

vision-nav-probe-map-conditioned-student \
  --export-dir /workspace/exports/crossview-001 \
  --inputs /workspace/exports/crossview-001/sample_inputs.npz \
  --tile-id replay-tile --map-release-id replay-map@1 \
  --calibration-id down-camera-v1
```

Use `--device cpu` for a small local smoke run. The existing research container
can host this workflow on RunPod; nothing here provisions a paid pod or uploads
data automatically. Keep trained weights and generated datasets out of Git.

The trainer records source/dataset/checkpoint hashes, map/calibration identities,
seed/config, losses and classification diagnostics. It does not call classification
accuracy a metric pose error. The exporter checks the ONNX graph, runs CPU parity
for every validation candidate, and checks both numerical error and argmax
decisions. Exported batch size is fixed at one. Pi and GPU performance still
need independent measurements.

Current training also pins exact calibration-file digests and the shared image
preprocessing implementation. Export rejects older checkpoints without those
bindings. Retrain them; do not manually fill in provenance fields.

Exposure-clock alignment is not calibrated physical sensor delay. The artifacts
continue to declare `sensor_delay_calibrated: false` and
`gps_timing_calibrated: false` until those independent measurements are supported.

## Integration Status

- Implemented: exact pixel metadata propagation, portable dataset preparation,
  map-conditioned model, supervised training, ONNX export/parity, recorded-tensor
  shadow inference, integrity checks and synthetic end-to-end tests.
- Implemented: [raw-camera capture replay](map-conditioned-replay.md) using
  the same image preprocessing as training, exposure-aligned windows, and
  explicit selected map tiles without GNSS or position-label inputs.
- Implemented: optional training-only Bearing-UAV point targets and auxiliary
  student loss, preserving supervised negatives and independent validation.
- Not yet qualified: teacher improvement on real data, full temporal/heading
  distribution distillation,
  measured confidence calibration, real-data geographic evaluation, and binding
  this new multi-input student into the live camera/retrieval scheduler.
- The old `pi_temporal_student` module remains a labeled fixed-area comparison
  baseline. Its checkpoint/tensor family cannot load into the new exporter.
- The standalone probe accepts new map IDs but still requires an audited camera
  calibration ID. This tests the intended interface, not demonstrated accuracy
  on unseen maps. Caller-supplied tensors are replay evidence, not authenticated
  live map/calibration measurements.
- No MAVLink vision/odometry publication, desktop edits or PX4 parameter changes
  are part of this work.

## Verification

```bash
python -m pytest -q tests/test_map_conditioned_student.py tests/test_exposure_training.py
python tests/run_unit_tests.py
```

The tests execute an actual small PyTorch training loop and CPU ONNX inference
using synthetic images. They check map sensitivity/gradients, georeferencing,
no-match behavior, portability, provenance failures and export parity. This is
software evidence only, not a model-quality benchmark.
