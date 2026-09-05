# Updated Navigation Architecture Review

Date: 2026-09-05. Scope: the current local working tree, not a released
commit. Refreshed after the retrieval/local-ONNX integration and desktop
benchmark work. This review supersedes the implementation-status claims in
`architecture-reevaluation-2026-09-05.md`; that document describes the earlier
state. No runtime or flight configuration changes are part of this review.

## Verdict

Subsequent implementation update: [learned retrieval](learned-retrieval-replay.md)
now records official MegaLoc checkpoint export, antialiased preprocessing and
CPU parity, plus real-retriever integration with fixture map/student replay.
The review findings below describe the earlier assessment point; official UAV
retrieval accuracy, continuous VIO and Pi validation are still open.

Further implementation update: the [metric pose smoother](metric-pose-smoother.md)
now implements GTSAM fixed-lag optimization for qualified metric factor replay,
camera/body conversion, delayed consume-once fixes, separate local/map ages and
a declared common map-error floor. The old heuristic demo now withholds all
position authority. Findings about that demo below are historical; a qualified
live VIO frontend, shared uncertainty calibration and PX4 boundary corrections
are still open. The target architecture now names camera plus synchronized IMU
as nominal, retaining camera-only as an explicitly constrained experiment.

The next landed step is [ROS-free OpenVINS research replay](openvins-research-replay.md).
It runs real native image/IMU estimation on a synthetic sensor fixture, without
ground-truth initialization. It has not been qualified on actual flight data or
the Pi, and its correlated local-state covariance is not yet connected to the
map smoother. This updates the earlier "no VIO implementation" finding without
claiming a validated continuous navigation system.

The latest measured step extends that adapter to **real TUM VI room1 replay**:
2,821 frames, 2,689 local updates, repeatable state output, and independent
ground-truth evaluation without scale fitting. The associated portion has
0.1565 m initial-pose-aligned position RMSE and 0.0806 m full-trajectory
rigid-aligned RMSE. These are local handheld-dataset results, not aerial global
position accuracy. [Full protocol and exclusions](openvins-research-replay.md).

The [joint pose-window contract](vio-joint-pose-contract.md) now preserves and
audits within-snapshot VIO correlations on the real room1 replay. This advances
the input contract for item 2 below; it does not implement the joint map-fusion
backend or qualify the covariance on drone hardware.

Current implementation priority remains:

1. Qualify the actual camera/IMU clock, delay, axes, noise and rigid extrinsics.
2. Connect learned metric map evidence and local VIO with an explicit correlated
   uncertainty contract; native posterior poses are not independent factors.
3. Evaluate held-out UAV routes, map ambiguity, wrong maps and sensor dropouts.
4. Measure concurrent learned inference and VIO on the Pi before deployment.

The table and findings below preserve the earlier assessment point. Refer to
the implementation updates above and their linked evidence for work landed since
that review; none of these milestones authorizes PX4 publication.

Subsequent candidate-path update: [shared metric grouping](learned-candidate-grouping.md)
now recognizes pose-consistent overlapping landmark-support boxes without
summing correlated tile evidence. Both learned shadow paths use it and the
same pixel-center intrinsics resize. Distinct locations, inconsistent rotation,
chain overlaps and incomplete searches still withhold selection. A calibrated
motion-conditioned hypothesis bank and joint VIO/map uncertainty remain open.

Keep learned map-conditioned localization as the core product. The updated
repository now supports a meaningful training/export/recorded-camera experiment,
with stronger sensor and map contracts. It is not yet a continuous GNSS-denied
navigation system or a validated Pi deployment model.

The next investment should connect and measure the existing pieces, not expand
the list of teacher models. The target is asynchronous learned global correction
alongside continuous local visual-inertial odometry (VIO), joined by an estimator
that explicitly represents uncertainty in the map-to-local alignment.

## Current Implementation Versus Intent

| Area | Observed implementation | Remaining limitation |
| --- | --- | --- |
| Map-conditioned learning | `neural/map_conditioned_student.py` consumes camera windows, candidate map imagery, roll/pitch, relative time and calibration/map context; predicts spatial and heading distributions with a no-match class. | Project-owned supervised baseline, not a Bearing-UAV/Video2BEV reproduction or teacher-distilled field model. |
| Recorded-camera replay | `neural/map_conditioned_replay.py` connects optional learned retrieval, map-conditioned student and optional XFeat/LighterGlue geometric verification, with bounded exposure-aligned windows and provenance. | Automatic retrieval is fixture-tested; official MegaLoc checkpoint accuracy/export is not yet measured. No continuous VIO, fused metric body pose or navigation covariance. Outputs deliberately remain `NO_GLOBAL_FIX`. |
| Learned local matching | `neural/local_onnx.py` runs exported XFeat/LighterGlue without PyTorch, verifies saved parity and rejects invalid features/matches. Prior local artifacts record actual pretrained checkpoint execution. | Upstream sample-pair success and ONNX parity are not held-out UAV-to-map accuracy. Pi thermal and concurrent-runtime performance remain unmeasured. |
| Metric map assets | `neural/map_landmarks.py` lifts learned keypoints onto audited DSM geometry and retains map-error priors. | Real-map registration, vertical datum conversion, camera-pose uncertainty and real-image accuracy still require validation. |
| Candidate verification | `neural/shadow_inference.py` and `neural/replay_local_geometry.py` withhold ambiguous/incomplete searches; replay now supports surface-backed camera-pose candidates. | Multiple implementations still exist; neither provides a validated motion-conditioned multi-location estimator or overlap merging. |
| Sensor data | Raw inertial capture, periodic TIMESYNC, exposure/native-attitude audits and training provenance exist. | Clock consistency is not physical sensor-delay calibration; inertial bandwidth, noise, axes and GNSS reference timing remain hardware questions. |
| Continuous estimator | `estimation/factor_graph.py` is explicitly a deterministic fallback; model/VO adapters still contain unimplemented methods. | No validated VIO frontend, proper pose composition, optimizer or calibrated covariance. |
| Vehicle boundary | `px4/bridge.py` exposes gates and a transport hook. | Orientation/covariance conversion and future-time rejection need correction; the transport is unimplemented. Do not promote it. |

## Findings, In Priority Order

1. **Continuous motion estimation is the principal missing subsystem.** Attitude
   samples alone do not provide local translation. Evaluate a proven native VIO
   backend with the actual camera/IMU stream; keep Python orchestration and do
   not reintroduce ROS into the deployed runtime. OpenVINS documents why bias,
   extrinsic and timing calibration matter, but its tutorial is not evidence
   that our Pixhawk USB stream satisfies those requirements.
   [OpenVINS calibration](https://docs.openvins.com/gs-calibration.html)

2. **The modern model is connected in replay, not validated as navigation.**
   Measure the integrated retrieval/student/correspondence/geometry path on
   actual calibrated flight captures and wrong-map examples. Preserve all
   attempted hypotheses and the no-match outcome. A
   high probability in an 8x8 map grid is coarse learned evidence, not a metric
   position or a calibrated covariance. The current temporal pooling also does
   not reconstruct a BEV or estimate camera motion.

3. **Do not promote the fallback estimator or bridge.** The fallback directly
   adds translations, replaces orientation and reduces covariance heuristically.
   Its TRACKING status is not integrity evidence. The bridge currently changes
   position axes but only reorders quaternion storage and leaves covariance
   unchanged; it clamps future timestamps to age zero. Correct these separately,
   including camera/body transforms and correlated PX4 attitude inputs, before
   binding any flight transport. Pin the aircraft firmware and accepted external
   vision frames/timing against its documentation.
   [PX4 external vision](https://docs.px4.io/main/en/ros/external_position_estimation)

   There are additional scaffold defects that tests currently do not rule out:
   `runtime/pipeline.py:94` can apply the same accepted global observation on
   subsequent frames, while `factor_graph.py:40` reduces covariance each time.
   `runtime/pipeline.py:140` reports measurement age as zero. Hypotheses expire
   during updates rather than every estimate query, and a single retained
   hypothesis receives normalized probability one even when absolute evidence
   is weak (`estimation/hypotheses.py:52`). These are not properties of a
   calibrated posterior. Require measurement IDs, consume-once factors,
   real observation age, an explicit no-match hypothesis, motion consistency
   and expiration at every decision. The newer shadow replay correctly withholds
   authority; these findings concern the future canonical runtime scaffold.

4. **Pi deployment remains an unmeasured requirement.** MegaLoc's documented
   descriptor has 8,448 dimensions: 10,000 float32 descriptors require about
   338 MB before indexes, images and local features. This is arithmetic, not a
   latency benchmark. Keep it as a frozen retrieval reference; benchmark before
   deciding whether the onboard retriever needs distillation/compression.
   MegaLoc publishes inference/evaluation, not its original training pipeline.
   [MegaLoc](https://github.com/gmberton/MegaLoc)

   The existing retriever uses OpenCV linear resize whereas MegaLoc documents
   antialiased Torchvision resize. Identical-input ONNX parity does not establish
   upstream preprocessing or benchmark parity. Resolve or explicitly evaluate
   that difference before descriptor/index promotion. The current replay uses
   a bounded exact NumPy cosine index, not the target hierarchical FAISS search.

5. **Training success is not generalization evidence.** Split by geography as
   well as session, lighting and route. Measure GNSS reference timestamp error
   and antenna/camera lever arms before treating coordinates as truth. Include
   unseen geography, out-of-map imagery, repeated terrain, stale maps and
   retrieval failures. Test fixed-mount calibration after focus or mount changes;
   moving gimbals require synchronized orientation, not constant extrinsics.

## Research Decisions

| Research | Verified relevance | Project decision |
| --- | --- | --- |
| [Bearing-UAV](https://github.com/liukejia121/bearinguav) | Official code now links weights, dataset, training and navigation evaluation scripts. | Evaluate the official baseline first; test known-start navigation and lost-position recovery separately. Do not label our small cross-attention network as its reproduction. |
| [OrthoLoC](https://deepscenario.github.io/OrthoLoC/) | Orthophoto plus DSM, camera calibration and ground-truth 6-DoF pose directly address our geometry problem. Dataset is CC BY-NC-SA 4.0. | Prioritize permitted geometry evaluation; establish commercial-use permissions separately before training product models on the dataset. |
| [AnyVisLoc](https://github.com/UAV-AVL/Benchmark) | Low-altitude, multi-view UAV localization benchmark. | Add viewpoint/altitude stress evaluation rather than relying only on image-retrieval metrics. |
| [Video2BEV](https://github.com/HaoDot/Video2BEV-Open) | Video-based cross-view localization with a real BEV construction pipeline. | Retain as an offline teacher experiment; require measured benefit before adding its training cost to student development. |
| [3D geometric perception, April 2026](https://arxiv.org/abs/2604.01747) | Uses VGGT reconstruction and virtual BEV imagery, followed by candidate-aware processing and 3-DoF localization. | Relevant offline geometry/representation experiment; not proof of Pi feasibility or full 6-DoF navigation. |
| [XFeat/LighterGlue](https://github.com/verlab/accelerated_features) | Released learned local features/correspondence implementation. | Retain as the edge candidate; validate UAV-to-map matches and exact-checkpoint ONNX parity on our data. |
| [AirLock+](https://airlock-wacv2026.github.io/) | UAV/map registration and geolocation evaluation. | Evaluate the stated registration task; do not equate target geolocation error with aircraft pose or closed-loop performance. |
| [UAV Visual Localization in GNSS-Denied Environments, July 2026](https://isprs-archives.copernicus.org/articles/XLIX-B1-2026/55/2026/) | Combines learned SuperPoint/LightGlue matches with true orthophotos, DSM and robust spatial resection; reports strong terrain/altitude dependence over 100-250 m captures. | Supports our learned correspondence plus surface geometry direction. It does not establish performance for our lower-altitude X500 captures or justify switching matchers without comparison. |
| [A2RL state estimation, February 2026](https://arxiv.org/abs/2602.01860) | Combines monocular RGB/IMU VIO, landmark measurements and drift correction of multiple state components. | Supports separate continuous motion and correction paths. Racing landmarks are not satellite imagery; this is architectural evidence, not transferable cross-view accuracy. |

These are recommendations inferred from task fit, not independently reproduced
accuracy claims. Source availability, code license, weight license and dataset
permissions are separate release checks. Newer publication dates alone do not
justify replacing measured baselines.

The official Bearing-Naver description assumes a known initial position within
a four-tile satellite block and urban waypoint navigation. Test this task
separately from cold-start recovery anywhere in a large mission map. The older
paper abstract still says artifacts will be released; the current official
repository links code, model weights and the dataset. Prefer executing that
baseline over building a second purported reproduction from a paper summary.

## Architecture Corrections Recommended

These are proposed contract changes, not implemented runtime behavior:

- Make calibrated camera plus synchronized inertial measurements the nominal
  navigation path. Camera-only remains an explicitly constrained experiment.
  Update the contradictory camera-only/optional-IMU language in the target
  architecture when this decision is adopted. PX4 attitude is useful context
  but cannot substitute for a translation estimator or qualified raw IMU.
- Keep local odometry continuous in its own frame and estimate an uncertain
  map-to-local alignment. Apply delayed map corrections at their measurement
  time in a bounded estimator history. Do not overwrite the current trajectory
  with a delayed camera pose or refusion of the same fix.
- Keep separate local-tracking health and global-map-lock health. Local motion
  can remain useful after map lock is lost, but must not be advertised as a
  fresh global fix. Any future local-only PX4 output needs a separate reviewed
  policy; the current shadow release continues to publish nothing.
- Model shared uncertainty: observations from the same map, adjacent frames,
  student/matcher stages and PX4-derived attitude are not independent just
  because they have separate scores. Repeated updates must not erase systematic
  map, calibration or timing error. Account for the feedback correlation if
  PX4 attitude is later fed back to that same PX4 estimator as vision attitude.
- Represent camera-to-body extrinsics, map datum, vertical observability and
  camera mount state explicitly. A moving gimbal needs synchronized orientation;
  an unsensed moving mount invalidates fixed extrinsics. Stabilization by rigid
  mounting, vibration control and exposure tuning should precede bulk capture.
- Separate telemetry source preference from estimator source fusion. Healthy
  GPS can remain the displayed primary source and a qualified offline reference;
  flags/satellite count alone are not spoofing detection. Do not feed GNSS-derived
  PX4 local position into the GNSS-denied model as undisclosed dead reckoning.
- Replace synchronous research calls with bounded workers and queues for live
  use. Retrieval must not block local motion. Record capture-to-result latency,
  deadlines and dropped frames, not only model inference time.

The current PX4 documentation identifies external-vision ODOMETRY using
`MAV_FRAME_LOCAL_FRD`, with separate parent/body frame conventions, and recommends
a 30-50 Hz stream. Our blanket `LOCAL_NED` message contract therefore needs a
firmware-pinned compatibility decision, not merely a quaternion storage reorder.
Preserve ENU internally; validate orientation, velocity, covariance, timestamps,
reset counters and origin semantics against the actual aircraft release. A
low-rate map matcher must not fake this stream by repeating old fixes as fresh.
No ROS runtime is required for MAVLink integration.
[PX4 integration documentation](https://docs.px4.io/main/en/ros/external_position_estimation)

## Commercial-System Comparison

Theseus currently documents Cyclops on commodity ARM64 hardware, camera-to-flight-
controller pose calibration, time synchronization, preloaded maps and bench
validation. Its documented autopilot path is ArduPilot; messages depend on
Cyclops version, including GPS_INPUT and earlier external-position commands.
These are vendor-documented requirements, not independently reproduced results.
Do not transplant its parameter block into our PX4 setup or infer its internal
neural model from marketing claims.
[Cyclops setup](https://docs.theseus.us/cyclops/getting-started),
[autopilot integration](https://docs.theseus.us/cyclops/autopilot-integration)

The relevant competitive gap is a measured operating envelope and reliable
sensor/estimator integration, not a longer list of model names. Keep thermal,
night-operation and anti-spoofing claims out of our supported envelope until
our hardware/data demonstrate them.

## Existing Performance Evidence

The saved local benchmark reports show approximately 65.54 ms warm-cache and
94.17 ms cold-feature-cache P95 on the Mac for one upstream sample candidate,
640x384 inputs and a 512-feature limit. They omit the global retriever, temporal
student, continuous VIO and live capture workload. Both have an incomplete
limits assessment because temperature was unavailable; the benchmark source
also changed between runs. They cannot establish Pi throughput or sustained
flight performance. See [local benchmark](learned-local-benchmark.md).

Keep MegaLoc as the measured research retrieval reference, XFeat/LighterGlue as
the current edge correspondence candidate, and the map-conditioned student as
an experimental component. Onboard promotion requires full concurrent Pi
measurements; compression/distillation should follow those measurements.
RoMa/VGGT/MASt3R/Video2BEV remain offline experiments whose individual value must
be demonstrated before adding their targets to student training.

## Recommended Runtime

```text
Calibrated camera + timestamped inertial measurements
  |-- continuous local VIO --> local pose / velocity / uncertainty
  |-- selected keyframes --> learned retrieval of candidate neighborhoods
                              --> map-conditioned location/heading evidence
                              --> learned correspondence + DSM-backed geometry
                              --> competing global hypotheses + no-match
  +--> estimator: local odometry and uncertain map-to-local transform
       --> position / velocity / integrity / explicit stale-global state
       --> shadow recordings and telemetry
       --> separately reviewed PX4 publication, later
```

Optional barometer data belongs in the estimator as a biased relative-height
aid, not as absolute terrain height. GPS remains independent telemetry and a
qualified evaluation reference; a nominal good fix flag does not prove absence
of spoofing. The project should not claim anti-spoofing performance yet.

## Execution And Acceptance

1. **Capture qualification:** rigid mount, focus/exposure, frozen intrinsics and
   extrinsics, timing audit, inertial cadence and retained ULog. Exit with a short
   real replayable sequence, not only a passing synthetic fixture.
2. **Validate the connected offline path:** automatic retrieval -> student ->
   correspondence -> metric geometry on that sequence. Exit with coordinate
   checks, per-stage failures, position/heading residuals and wrong-map rejection.
3. **RunPod baseline evaluation:** official Bearing-UAV, frozen retrieval and a
   permitted geometry dataset subset. Record revisions, hashes, permissions,
   splits, runtime/memory and task-specific metrics before training teachers.
4. **Student improvement:** train/distill only teacher signals that improve the
   baseline. Require held-out geography, export parity, false-fix and calibration
   results. Keep absolute-ENU regression as a labeled fixed-area baseline only.
5. **Continuous Pi shadow test:** VIO, hypothesis persistence, map/local alignment,
   bounded queues/caches and recovery scheduling. Measure capture-to-result
   P50/P95/P99, dropped frames, RSS, CPU load and sustained thermals.
6. **Vehicle promotion:** repaired frame/time/covariance boundary, pinned PX4
   configuration and separately approved validation. Keep flight output disabled
   throughout the preceding research stages.

Acceptance metrics must include false global fixes, availability, first-fix and
recovery time, longest uncorrected drift, position/heading error and uncertainty
coverage. Report known-start, cold-start and recovery separately. Neural scores,
test pass counts and retrieval recall cannot substitute for these measurements.

## Verification Performed In The Earlier Review

The eight focused neural, map geometry, candidate, inertial, timing, training and
replay test modules passed: **115 tests**. This included tiny synthetic
PyTorch-to-ONNX training/export/replay checks. Rasterio and Torch exporter
deprecation/tracing warnings remain. This review did not run official model
checkpoints, reproduce paper accuracy, provision RunPod, benchmark a Pi or test
flight publication. No desktop or navigation code was changed.

## Verification In This Refresh

The 12 focused architecture, candidate, sensor, replay, local ONNX, benchmark,
student, geometry and retrieval modules returned **195 passed, 12 skipped**.
All skips require the explicit `VISION_NAV_LOCAL_REFERENCE_CONFIG` for the
pretrained-checkpoint tests; the variable was not selected for this review.
The separate unit runner completed **104 checks**. These suites overlap and
must not be added into a unique test count. Existing Torch export and Rasterio
warnings remain. Earlier real-checkpoint artifacts were reviewed, not recreated.

Only this review document was edited. No model training, downloads, deployment,
Pi load test, aircraft commands, desktop changes or PX4 publication were performed
as part of this reassessment. Passing synthetic tests does not close the
estimation defects or establish flight readiness.
