# GNSS-Denied Navigation Architecture Reevaluation

Follow-up: [updated working-tree review](updated-architecture-review-2026-09-05.md)
accounts for the subsequently implemented map-conditioned replay, metric map
assets and exposure-timing checks. The findings below describe the earlier state.

Date: 2026-09-05. Scope: current working tree, drone runtime, map production,
capture, research/training, and the future PX4 observation boundary. Desktop
implementation is deferred. This is an assessment and proposed revision, not
an implementation or model-promotion record.

## Assessment

Keep the learned vision-first direction. The project has useful release,
capture, inference-interface, and rejection scaffolding. It does not yet have
a validated continuous GNSS-denied navigation estimator. The most consequential
next changes concern map-conditioned learning, metric map geometry, local
motion estimation, and measured uncertainty, rather than adding more named
foundation models.

The reviewed architecture documents were architecture.md, interface-reference.md,
px4-integration.md, change-playbook.md, flagship-modern-vision-pipeline.md, and
the historical August reassessment. Current source was inspected separately;
documentation intent was not counted as implemented capability.

## Findings, In Priority Order

### 1. The Pi student does not yet implement the intended cross-view model

In [pi_temporal_student.py](../src/vision_nav/neural/pi_temporal_student.py),
`build_pi_temporal_student` accepts frame and attitude windows, then directly
regresses ENU position, heading, and a feature vector. It receives no candidate
map imagery/features or tile coordinates. `build_student_run_record` requires
one map reference and one calibration across both splits.

This is a useful experiment for a fixed area, but it is not a general model
that can localize against a newly loaded map. A global average pooling CNN
and GRU also do not themselves implement calibrated BEV reconstruction.

Proposed correction: condition the compact student on candidate map feature
grids, neighbor geometry, calibration, and temporal camera features. Predict
candidate-relative location/heading likelihoods, overlap, and matchability.
Use verified geometry to turn evidence into a metric pose. Keep absolute ENU
regression as a clearly labeled research baseline. Add a held-out geography
test where the same weights must work with a previously unseen MapRelease.

### 2. The runtime lacks continuous local odometry and an actual estimator

[adapters/models.py](../src/vision_nav/adapters/models.py) has unimplemented
VO/model adapter targets and a deterministic stub. The active neural probe
does not bind a local VIO frontend. [factor_graph.py](../src/vision_nav/estimation/factor_graph.py)
is explicitly a fallback: it blends position using reprojection-error-derived
gain, shrinks covariance heuristically, and is not a graph optimizer.

Proposed correction: implement continuous local visual-inertial estimation
alongside asynchronous learned map localization. Maintain local odometry and
map alignment separately, including uncertainty in their transform. Map loss
can leave local motion observable while global position becomes uncertain.
Represent these as separate health states; do not silently promote dead
reckoning into a fresh global fix. Existing shadow publication stays disabled.

Use a proven native estimation backend after a sensor/interface evaluation;
Python can own orchestration, capture and learning. OpenVINS is a useful
reference for initialization, biases, and camera/IMU calibration. Its relevant
requirement is calibrated inertial data, not merely an attitude tuple.
[OpenVINS calibration](https://docs.openvins.com/gs-calibration.html)

### 3. Metric map geometry is not produced by the normal learned map compiler

[map_asset_compiler.py](../src/vision_nav/neural/map_asset_compiler.py) writes
XFeat keypoints and descriptors, but not per-keypoint 3D ENU points. In contrast,
[shadow_geometry.py](../src/vision_nav/neural/shadow_geometry.py) requires 3D
map points for PnP camera-position candidates. A GeoTIFF and descriptor cache
alone do not complete that path.

Proposed correction: add aligned orthophoto plus DSM/DTM ingestion, vertical
datum handling, valid-elevation masks, per-point uncertainty, and image-pixel
to map-point lookup. Sample surface elevations for rooftops/trees instead of
assigning ground elevation to all visible features. Preserve projected-grid
versus true-ENU conventions and map georeferencing error.

An accurately calibrated camera and a known metric plane can support planar
pose recovery; the issue is not that every monocular homography is inherently
nonmetric. It is that current planar verification does not establish the
plane, resolve pose ambiguity, or estimate camera pose uncertainty. Treat
planar operation as a tested restricted geometry mode. Learned depth without
verified scale and georegistration is not a substitute for metric landmarks.

### 4. Top-K retrieval currently becomes Top-1 verification

`_matcher_inputs` in [shadow_inference.py](../src/vision_nav/neural/shadow_inference.py)
uses `candidates[0]`. [shadow_hypotheses.py](../src/vision_nav/neural/shadow_hypotheses.py)
tracks one tile, resets on a tile change, and reports the mean/covariance of
recent positions. It is explicitly not an estimator. This misses alternative
matches and makes legitimate tile crossings look like reinitializations.

Proposed correction: verify a bounded candidate set, merge overlapping tiles
into spatial hypotheses, propagate each hypothesis with measured local motion,
and compare against an explicit no-match hypothesis. Repeated nearly identical
frames must not be treated as independent evidence. Require motion-conditioned
consistency and a calibrated margin against competing locations. Sample
scatter along a moving trajectory must not serve as pose-error covariance.

### 5. Capture is auditable but not yet a calibrated VIO sensor stream

[training_capture.py](../src/vision_nav/training_capture.py) records concurrent
telemetry, camera metadata and pre/post TIMESYNC evidence. Its frame pairing
still uses host capture midpoint and nearest attitude receive time.
[mavlink_bridge.py](../src/vision_nav/mavlink_bridge.py) currently selects
ATTITUDE and position/health messages, not a raw acceleration/gyro stream.

Proposed correction before substantial footage collection: retain exposure
timestamps and derive an actual camera-to-host-to-PX4 clock model; record its
offset, drift and uncertainty. Add calibrated raw IMU/integrated inertial data
with sample timestamps and PX4 ULog retention. Establish whether Pixhawk USB
can deliver the required quality/cadence; otherwise evaluate a synchronized
camera/IMU module. Missing raw IMU should not prevent an explicitly labeled
attitude-aided map-localization experiment.

For stabilization, preserve a known camera-to-body transform. A moving gimbal
requires synchronized gimbal orientation; an unmeasured moving mount invalidates
fixed extrinsics. Electronic stabilization/cropping likewise needs its pixel
transform recorded. Recalibrate after changing focus, lens or mounting geometry.

### 6. PX4 conversion and uncertainty handling need correction before promotion

In [px4/bridge.py](../src/vision_nav/px4/bridge.py), `to_message` converts position
to NED but only reorders quaternion components and leaves covariance unchanged.
Quaternion storage order is not a reference-frame rotation. Orientation needs
the declared world/body transform, and covariance needs the corresponding
Jacobian transformation and MAVLink packing. `publication_decision` also clamps
negative age to zero, allowing future-dated timestamps through the age test.
`publish_mavsdk` remains unimplemented, so these are pre-promotion defects,
not evidence of current malformed flight output.

PX4's current external-vision guide documents LOCAL_FRD ODOMETRY and a
30-50 Hz stream. Pin the actual aircraft firmware and verify accepted parent/
child frames, sample timing, reset counter and covariance layout against it;
do not infer support from a frame's name. Global map matching can be slower
than continuous estimator output, but repeating an old fix with a new timestamp
is not propagation. [PX4 external vision](https://docs.px4.io/main/en/ros/external_position_estimation),
[MAVLink ODOMETRY](https://mavlink.io/en/messages/common.html#ODOMETRY)

The separate shared geometry scaffold also uses a fixed PnP residual and
identity orientation in [estimation/geometry.py](../src/vision_nav/estimation/geometry.py).
Keep it out of publication until consolidated with real measured geometry.
Do not fuse PX4-derived attitude/state back into PX4 as independent visual
information without an explicit treatment of correlated evidence.

### 7. The full Pi compute budget is still a hypothesis

[profiles.py](../src/vision_nav/neural/profiles.py) declares different recovery
and keyframe cadences. The frame probe executes its available model stages
serially per invocation. Declared cadence and tile limits are not proof of a
deadline-aware scheduler, bounded recovery cost or sustained thermal behavior.

MegaLoc's current model interface produces 8,448-dimensional descriptors and
its Hugging Face weights are approximately 915 MB. Ten thousand float32
descriptors alone occupy about 338 MB decimal before indexes and feature
caches. This arithmetic is not a Pi benchmark. Keep frozen MegaLoc as the
reference retrieval model; evaluate a compact distilled retriever and index
compression if measured latency/memory requires them. [MegaLoc](https://github.com/gmberton/MegaLoc),
[weights](https://huggingface.co/gberton/MegaLoc/tree/main)

XFeat/LighterGlue remains a credible learned edge candidate, but upstream
MegaDepth matching results are not UAV/satellite or Pi end-to-end results.
Finish exact-checkpoint ONNX parity and target-device measurements.
[Official XFeat/LighterGlue](https://github.com/verlab/accelerated_features)

AI HAT+ 2 needs a separately compiled and validated Hailo deployment path;
the CPU ONNX profile does not establish accelerator compatibility. Raspberry
Pi documents a Hailo-10H-specific software stack. Evaluate supported operators,
quantization and host/NPU transfer time before a hardware purchase decision.
[AI software](https://www.raspberrypi.com/documentation/computers/ai.html),
[compiler guidance](https://www.raspberrypi.com/news/introducing-the-raspberry-pi-ai-hat-plus-2-generative-ai-on-raspberry-pi-5/)

## Research Updates That Change The Plan

Sources were checked against public author repositories, papers and official
documentation on the assessment date. Available artifacts were inspected at
the listing/documentation level, not downloaded and independently reproduced.

| Work | Verified update or limitation | Decision for this project |
| --- | --- | --- |
| Bearing-UAV, CVPR 2026 | Official code, training/test scripts, model archive and Bearing-UAV-90K archive are now public. The paper abstract's future-release wording is stale. The navigation scheme assumes a known starting satellite neighborhood. | Replace the paper-only reproduction assumption with an official baseline evaluation. Use neighbor-conditioned location/heading evidence; separately test lost-position recovery. |
| OrthoLoC, NeurIPS 2025 | Provides aligned orthophoto/DSM and 6-DoF localization/calibration evaluation, with public code/data links. Dataset terms are CC BY-NC-SA 4.0. | Add a geometry benchmark and study AdHoP refinement. Keep benchmark/training permissions explicit before commercial model use. |
| AnyVisLoc | Public UAV localization benchmark uses retrieval, matching and PnP with 2.5D reference maps. | Add low-altitude/viewpoint stress tests; report aerial-map and historical-satellite results separately. |
| Video2BEV, ICCV 2025 | Uses Gaussian-splat reconstruction to obtain BEVs; UniV is a video cross-view benchmark. Official training/evaluation stages and weights are linked. | Retain as an offline teacher experiment. A GRU over frames is not a reproduction of its BEV construction. |
| AirLock+, WACV 2026 | Public sequences and map/telemetry downloads support UAV-to-map registration and target geolocation. | Use registration/temporal evaluation. Target-location error is not drone body-pose error or a PX4 closed-loop result. |
| RoMa v2 | Separate public implementation exists; our upstream lock still names Parskatt/RoMa. | Pin and evaluate RoMaV2 explicitly for offline correspondence supervision; do not label RoMa v1 runs as v2. |

Sources for the table:
[Bearing-UAV code](https://github.com/liukejia121/bearinguav),
[model archive](https://huggingface.co/HaoyZhou/bearinguav/tree/main),
[dataset archive](https://huggingface.co/datasets/HaoyZhou/bearinguav/tree/main),
[OrthoLoC](https://deepscenario.github.io/OrthoLoC/),
[AnyVisLoc](https://github.com/UAV-AVL/Benchmark),
[Video2BEV paper](https://arxiv.org/abs/2411.13610),
[Video2BEV code](https://github.com/HaoDot/Video2BEV-Open),
[AirLock+](https://airlock-wacv2026.github.io/),
[RoMaV2](https://github.com/Parskatt/RoMaV2).

A broader field-system comparison is the SPRIN-D heightmap-gradient work,
which combines local visual-inertial motion and terrain registration for
low-altitude navigation. Its geometry/sensor assumptions differ from ours;
its results cannot be transferred to a Pi with one camera. It supports the
architectural separation of local motion and global correction, not a mandate
to adopt its hardware. [Paper](https://arxiv.org/abs/2510.01348)

## Proposed Architecture

```text
Calibrated camera + synchronized inertial samples
    |
    +--> local feature tracking + VIO --> continuous local odometry
    |
    +--> keyframes --> learned retrieval --> candidate neighborhoods
                         |                         |
                         +---- map-conditioned temporal student
                                                   |
                              XFeat/LighterGlue + 2.5D/3D geometry
                                                   |
                         multi-location hypotheses + no-match state
                                                   |
                   bounded estimator + uncertain map/local alignment
                                                   |
                  global pose, local motion, integrity, decision trace
                                                   |
                       shadow evidence now; reviewed PX4 bridge later
```

This keeps learned cross-view localization as the product's core. VIO supplies
continuity; it does not replace the learned map system. GPS stays independently
observable and supplies offline reference labels where valid. Satellite count
or a nominal fix flag alone cannot establish absence of spoofing. A future
GPS/vision selection policy needs consistency checks, hysteresis and recorded
source transitions; the current shadow runtime is not an anti-spoofing product.

Optional barometer measurements can constrain relative vertical motion with a
bias state. They do not establish terrain height or map-frame absolute altitude.
This assessment proposes no new required sensor beyond a usable camera/IMU
stream; stereo, ranging and alternative terrain sensors remain separate choices.

## Next Work, In Execution Order

| Stage | Concrete work | Evidence required to move on |
| --- | --- | --- |
| 1. Capture readiness | Finish mount/focus/exposure work; validate timing; retain camera metadata, raw inertial samples, ULog and reference GNSS; freeze calibration and map versions. | A short replayable session with measured timing, blur, exposure and telemetry coverage. |
| 2. Geometry baseline | Compile orthophoto plus elevation into learned feature/3D caches. Run XFeat/LighterGlue against known map pairs, then bounded retrieval candidates. | Real-image 6-DoF/position residuals, bad-map rejection and transform checks. |
| 3. RunPod research baseline | Evaluate official Bearing-UAV and frozen MegaLoc; compare local correspondences against an offline RoMaV2 reference. | Reproducible revisions, weights, dataset splits, peak memory and actual task metrics. |
| 4. Learn the deployment model | Train candidate-conditioned spatial/heading heads; add temporal support after the single-frame reference is measurable; distill only useful teacher signals. | Improvement on held-out routes/areas and no increase in false global fixes. |
| 5. Continuous shadow navigation | Add local VIO, bounded hypotheses, covariance calibration, scheduling and map/local alignment. | Loss/recovery, tile-crossing and ambiguity evidence plus Pi latency/power/thermal results. |
| 6. Vehicle integration | Correct and consolidate frame/time/covariance serialization under Px4VisionBridge; validate against pinned firmware. | Recorded bench evidence and later controlled validation before any publication promotion. |

Do not make reproducing every teacher a prerequisite for the first useful
experiment. Each RunPod experiment should answer one question before a larger
training run. Start with existing checkpoints and a small permitted dataset
subset, profile VRAM, then choose the GPU. Keep proprietary datasets and
research datasets with different terms separately versioned. Record licenses
for code, weights and data independently; distillation does not by itself
resolve upstream use restrictions.

## Evaluation Requirements

- Split by geography, route, capture session and time/season. Session-only
  separation is insufficient when adjacent views of the same landmarks leak
  across splits. Retain an untouched final test set.
- Evaluate cold start, known-start tracking and kidnapped/lost-position recovery
  separately. Test wrong maps and out-of-map frames, not only successful pairs.
- Report retrieval recall, metric position/heading error, false-global-fix rate,
  availability, time to first fix, recovery time and longest uncorrected drift.
- Match published metrics to their actual task. Do not compare target error,
  tile recall and full camera-pose error as equivalent accuracy numbers.
- Use covariance calibration/coverage and uncertainty from map, camera, timing,
  geometry and local propagation. Neural scores are not calibrated covariance.
- Audit heading supervision: current labels use PX4 yaw while attitude is also
  a model input. Add yaw-withheld/bias tests and independent heading reference
  where available, so copying the input is not scored as learned visual heading.
- Retain reference GNSS uncertainty and antenna-to-camera/body lever arms;
  ordinary GNSS agreement cannot substantiate submeter accuracy.
- Measure capture-to-estimate P50/P95/P99, sustained RSS, thermals, dropped
  frames and queue age on the actual Pi. Keep recovery load separate from
  steady tracking. Do not infer full-pipeline performance from feature FPS.
- Validate PyTorch/ONNX/Pi numerical parity and resulting match/pose decisions,
  including quantized models and any later Hailo artifacts.

## Verification And Limits Of This Assessment

`PYTHONPATH=src python tests/run_unit_tests.py` completed successfully during
this review. Two Rasterio-dependent map import/tile tests skipped because that
dependency is absent. The suite contains contract, synthetic, legacy and
scaffolding tests; its success is not a field-navigation benchmark.

No real model inference, public-paper reproduction, RunPod job, Pi timing test,
flight log evaluation, or PX4 publication test was performed in this review.
No navigation code, model locks, aircraft configuration or desktop behavior
was changed. The working tree already contains extensive uncommitted migration
work; this report describes that local state, not a released Git revision.
