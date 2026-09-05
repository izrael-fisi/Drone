# Flagship Modern Vision Pipeline Program

## Product Contract

The field product is a calibrated, offline learned UAV-to-map localization
system. Its Pi profile is `pi5_crossview_nav_v1` and it never replaces a
withheld learned result with a classical descriptor match.

| Layer | Field implementation | Research-only source |
| --- | --- | --- |
| Global recovery | MegaLoc candidate retrieval | map-specific hard-negative evaluation; MegaLoc stays frozen unless a separately licensed training path is proven |
| Local evidence | XFeat + LighterGlue | XFeat/LighterGlue training and export |
| Temporal support | Target compact temporal/bearing ONNX student | Video2BEV offline 3DGS-rendered-BEV retrieval/matching; separate temporal distillation research |
| Geometry | USAC/MAGSAC PnP, then restricted planar homography | RoMa v2, VGGT, MASt3R-SLAM labels/refinement |
| Semantic signal | Auxiliary candidate score only | TopicGeo training |

## Release Chain

`DatasetRelease -> ModelRelease -> MapRelease v2 -> Pi shadow runtime`

Each arrow is hash-checked and calibration-compatible. A release mismatch,
missing model artifact, stale camera calibration, unsupported provider, or poor
geometry produces an observable withheld decision rather than a position.

The first link is now concrete: `scripts/pi/record_training_capture.sh` writes
immutable frame/PX4 sessions, `scripts/research/create_dataset_release.sh`
registers only validated sessions, and
`scripts/research/prepare_training_dataset.sh` materializes frame-to-attitude
training samples. The Pi also validates real ONNX model artifacts and aggregate
digests with `scripts/pi/validate_neural_model.sh`; manifest placeholders do
not pass deployment readiness.

Every training frame carries nearest-PX4-`ATTITUDE` timing evidence. Pairing
uses the Pi monotonic receive clock; PX4-native timestamps and their clock
domains are retained as provenance rather than treated as synchronized wall
time. The default 50 ms maximum frame-to-attitude delay is a dataset-release
gate, not a display-only metric.

The pinned XFeat source is Apache-2.0 and has dedicated research-only dense
and fixed-cardinality sparse ONNX export paths. The sparse artifact emits
keypoints, descriptors, and explicit invalid-slot scores after the upstream
heatmap/NMS and interpolation procedure. It still requires ONNX parity and
direct comparison against upstream `detectAndCompute` on held-out real images.
LighterGlue correspondence also needs its own release-bound parity and
held-out evaluation before a `ModelRelease` can use either stage on the Pi.

## Deployment Stages

1. Register public and Holybro datasets in DVC/MLflow.
2. Reproduce every source paper on its published protocol where artifacts are
   available, recording deviations.
3. Train the combined teacher and distill the Pi student.
4. Prove PyTorch, desktop ONNX, and Pi ONNX parity.
5. Compile a COG/STAC map into MegaLoc/XFeat artifacts and promote MapRelease v2.
6. Run Pi thermal and deterministic replay gates.
7. Run hardware shadow captures. PX4 ODOMETRY remains disabled in this stage.

The cloud path is intentionally separate and expendable. RunPod Pods can be
used for CUDA work; no cloud service is contacted during map localization or
flight.
