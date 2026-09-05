# Current GNSS-Denied Navigation Assessment

Research checked: September 5, 2026. Scope: the current local working tree,
including uncommitted research work, not a deployed or released aircraft build.
This is a reassessment, not authorization to enable flight-control output.

**Transfer update:** the [technical handoff](technical-handoff-2026-09-05.md)
consolidates current requirements, source ownership and remaining work, including
newer GitHub desktop/companion changes. The [verification ledger](handoff-verification-2026-09-05.md)
records the final handoff checks separately from historical runs below. Numeric
chunk validation, a bounded estimator-child numerical thread budget and reduced
internal copying are implemented; none closes the zero-advancing-current-output
finding. Preserve both failed and passing evidence when continuing this work.

Subsequent [worker-scoped PnP reuse](research-alignment-process.md#exact-pnp-assessment-reuse)
avoids re-assessing unchanged original fixes while recomputing each snapshot's
joint alignment. Exact parity and a modest in-process improvement were measured;
the real native 20 Hz observation still produced zero advancing current updates.
It is not a replacement for the current-state architecture work below.

The follow-up [owned transaction path](research-alignment-process.md#owned-transaction-reuse)
removes repeated native validation and hashing within a single operation. Its
274-test regression passed, but the advancing 20 Hz observation remained at zero
current global updates. Continue the delayed-correction/current-propagation
architecture work; this is not a navigation-readiness improvement claim.

The [compact worker transport](alignment-serialization.md#compact-worker-transport)
now preserves 64-bit values over a private MessagePack link while retaining JSON
evidence/hashes and an explicit JSON comparison mode. One initial native trial
produced a single advancing current update, but subsequent trials returned to
zero. This is intermittent evidence, not reliable current-state availability.

## Latest Review: Live Availability And Training Priorities

Rechecked September 5, 2026 against the local sources, completed native test
artifacts and the primary sources linked below. This section supersedes older
model descriptions and maturity statements in this document.

**Verdict: retain continuous native VIO plus asynchronous learned map fixes.
The architecture is modern, but its research implementation is not yet a
continuous, qualified global navigation system.** The next milestone is a
repeatable X500 shadow capture/replay, not another mandatory foundation model.

Implementation follow-up: [direct single-fix propagation](joint-pnp-vio-alignment.md#direct-single-fix-propagation)
now bypasses the iterative graph for one anchor while preserving joint
correlations, and private snapshots are hashed once on ingestion. The native
single-snapshot benchmark improved, with 230 targeted tests passing. A stronger
streaming observation still counted zero genuinely advancing current poses at
20 Hz. These changes do not close finding 1 below or authorize flight output.
The final expanded regression had **250 passes and one failure**: at the same
offered cadence the bounded native input queue overflowed and the epoch stopped.
Both availability and sustained input processing remain open; the queue and
freshness policies were not relaxed. See the linked propagation evidence for
the failure report and separate successful model/main-check runs.

**Subsequent native-handoff check:** snapshot copying now releases the native
acquisition lock, and receipt timestamps precede evidence-recording delays.
Three short trials of the same 20 Hz offer loop completed without queue
overflow; each still counted **zero advancing current global updates** during
the stream. The native/estimator regression passed 218 tests. This removes one
observed lock-contention path, not the possibility of overload under other
loads. It does not close the current-state availability finding. See
[bounded handoff evidence](native-vio-stream-worker.md#handoff-regression-september-5-2026)
for timings, scope and artifact paths. No input cadence, queue capacity or
freshness limit was relaxed.

The learned-model regression also exposed an orderly-close race during anchor
submission. Atomic unsent refusal now withholds that query; owned-anchor cleanup
still requires confirmed native exit and genuine transport errors remain errors.
After this correction, 87 native lifecycle checks and 130 learned/observer
checks passed, followed by all 104 main project checks. The [query-ownership evidence](learned-query-anchors.md#verification-september-5-2026)
records the initial failure and subsequent verification. Flight qualification
and continuous global updates remain outstanding.

### Highest-Priority Findings

1. **Current-state availability remains the primary software blocker.**
   `DelayedPnPAlignment.refresh_current` requires the solved native snapshot
   still to be latest at the final check. A continually advancing source can
   exhaust every retry. The combined noise/solve worker transaction now exists,
   but the latest finite observation recorded **zero current updates during
   20 frames offered at 20 Hz**, with a 500 ms snapshot receipt-age limit.
   Recorded attempt processing times were approximately 358 ms and 1,230 ms.
   These two attempts are not latency percentiles or Pi measurements. The
   experiment used native OpenVINS with synthetic map/noise inputs on the Mac;
   it demonstrates a remaining availability gap, not an aircraft accuracy result.
   Design bounded delayed correction and correlated forward propagation, or
   an estimator-owned update mechanism. Do not solve this by changing timestamps,
   dropping cross-covariance, or merely raising the freshness limit.
2. **Physical uncertainty is still unqualified.** Explicit joint noise inputs,
   square-root numerical checks and failure tests are valuable, but synthetic
   providers do not measure camera, inertial, map-height or calibration error.
   Shared images/map points and Pixhawk inertial data must not be fused as
   independent repeated evidence. Record real timing, noise and error coverage
   before any aircraft publication milestone.
3. **Tracking and recovery need separate policies.** The research retriever
   searches the selected map with a bounded cosine index and returns at most
   four candidates. Per-frame candidate grouping is not a persistent global
   hypothesis bank. Test wrong-prior escape, cold start, repetitive terrain,
   outside-map input and long gaps; report false locks and recovery time.
4. **Training execution is not trained-product performance.** The live path
   uses the project-owned map-conditioned student: CNN encoders, spatial
   cross-attention and weighted temporal pooling. It is neither the old GRU
   baseline nor a faithful Video2BEV/Bearing-UAV reproduction. Optional
   Bearing-UAV point-target training exists, but point agreement does not
   distill an entire location/heading distribution. The documented synthetic
   distillation smoke had zero positive-cell accuracy; use it only as execution
   evidence. Require a geographic holdout before claiming benefit.
5. **Hardware and publication remain separate release gates.** Pi execution
   with camera, raw IMU, inference, solver and microSD recording must be measured
   together. `Px4VisionBridge.publish_mavsdk` remains intentionally unimplemented.
   Desktop GPS/vision source selection does not prove PX4 estimator failover or
   spoofing detection. Preserve shadow-only operation.

### Research-Informed Decisions

| Current primary source | Decision for this project | Evidence limit |
| --- | --- | --- |
| [OrthoTrack v2](https://arxiv.org/html/2606.25245v2) | Test tracking verified map-backed 2D-3D landmarks between learned keyframes, alongside the existing VIO baseline | Its L40S results report 1.4 s keyframe work and 23.8 FPS aggregate throughput, not a Pi deadline. Its [license](https://raw.githubusercontent.com/cvg/orthotrack/main/LICENSE) is non-commercial; no product import without appropriate permission |
| [AeroMap3D, July 2026](https://arxiv.org/abs/2607.14009) | Evaluate scale/yaw normalization and terrain-consistent match filtering before increasing model size | DEM ground height cannot stand in for roof height. Published results do not qualify our live sensor integration |
| [NGPS, July 2026](https://arxiv.org/abs/2607.18936) | Retain asynchronous map/VIO/IMU scheduling | Reported Orin NX accuracy is not Pi evidence; confidence-weighted covariance is not our calibrated joint error model |
| [UCVL / XIAN-Visloc, August 2026](https://www.sciencedirect.com/science/article/pii/S0924271626002339), [official artifacts](https://github.com/VERYBC/UCVL) | Add a large-area retrieval benchmark before treating MegaLoc as the settled production retriever; evaluate geographic hard negatives and map sampling | Authors describe 21 trajectories over 81.13 km and separate-city testing. This is coarse initialization evidence, not raw-IMU flight fusion or a qualified 6-DoF reference. Audit imagery/data permissions before training or redistribution |
| [Bearing-UAV official code](https://github.com/liukejia121/bearinguav) | Keep as an independently evaluated teacher/control | Documented navigation starts from a known location within four tiles; it does not establish unrestricted recovery |

These are engineering recommendations, not reproduced paper claims. Keep
Video2BEV and dense/3D foundation models as independently justified research
tools rather than prerequisites for the first operational baseline.

### Recommended Delivery Sequence

1. **Capture:** stabilize the installed camera mechanically; lock manual focus
   and record aperture/exposure. Calibrate at the actual resolution and verify
   raw camera/IMU timestamps and extrinsics. A moving camera mount or digital
   stabilization needs measured transforms/warps, not a fixed calibration label.
   [OpenVINS calibration](https://docs.openvins.com/gs-calibration.html).
2. **Replay:** produce one complete checksummed X500 session with imagery,
   inertial samples, calibration, map/surface release, PX4 log and independent
   reference. Keep GPS out of denied-mode inference; report reference quality.
3. **Continuity:** demonstrate current estimates while input keeps advancing,
   then bounded outage/recovery with honest covariance growth. Track observation
   time, estimate time and wall-clock availability separately.
4. **Training:** on RunPod, compare the existing learned baseline, a normalized
   matching variant and one supervised/teacher-assisted student on fixed
   geographically separated train/validation/test sets. Include no-match cases.
   Advance a model only for measured gains, not successful export alone.
5. **Pi qualification:** measure full-system latency tails, memory, thermals,
   dropped frames and microSD load. Keep acceleration optional until parity and
   end-to-end measurements justify it; do not extrapolate Mac/GPU throughput.
6. **PX4:** only after estimator qualification, separately test pinned firmware,
   wire frames, covariance conversion, clock/reset behavior and loss handling
   prop-off. [PX4 guidance](https://raw.githubusercontent.com/PX4/PX4-Autopilot/main/docs/en/ros/external_position_estimation.md)
   calls for 30-50 Hz external-vision streaming; that must come from a valid
   current estimator, not repeated historical map fixes.

### Reviewed Evidence

- The pending targeted native/estimator regression completed with **163 passed
  in 46.85 s**. Passing includes fail-closed behavior, not a positive real-time
  global-update guarantee. No Pi or aircraft was exercised.
- Local, ignored observation:
  `research/outputs/joint-transaction-native-20260905-r1/test_query_owned_before_slow_r0/streaming-observation.json`.
- Source: `estimation/delayed_alignment.py`, `estimation/live_alignment.py`,
  `estimation/alignment_worker.py`, `neural/live_sensor_inference.py`,
  `neural/replay_retrieval.py`, `neural/map_conditioned_student.py`, and
  `px4/bridge.py`, all under `src/vision_nav/`.
- Training limitations: [student distillation](bearinguav-student-distillation.md).
- This reassessment updates documentation only. It does not change runtime
  gates, provision cloud resources, connect hardware or enable PX4 output.

## Latest Decision: After Automatic Query Ownership

This section supersedes the older implementation findings below. Rechecked
against source and primary research on September 5, 2026. **The direction is
sound, but the system is a shadow-research implementation, not a qualified
GNSS-denied flight-navigation product.** Keep the learned map pipeline and
continuous native VIO; prioritize their measured integration over adding models.

Subsequent implementation: [correlated solver reduction](joint-pnp-vio-alignment.md#smaller-correlated-solve)
now removes unused pose residuals with proper Gaussian elimination and fixes
an observed native-window covariance numerical issue using square-root/QR
calculations. Paired full/reduced tests and Mac benchmarks are recorded there.
The exact-latest snapshot availability problem remains open; no freshness
limit was relaxed and no aircraft transport was enabled.

### Source Versus Target

| Capability | Current implementation | Remaining qualification |
| --- | --- | --- |
| Continuous local motion | Native OpenVINS sensor ownership, joint pose windows and bounded passive anchors | Installed X500 camera/IMU timing, extrinsics, noise and sustained Pi execution |
| Learned map evidence | Actual MegaLoc and XFeat/LighterGlue ONNX paths, candidate-conditioned student and surface-backed geometry | Held-out aerial-to-map accuracy, model confidence, Pi end-to-end deadlines |
| Delayed query ownership | Opt-in retention before inference, original frame identity/deadline and explicit retirement | Operational duration, capacity and failure behavior on the actual sensor stream |
| Current global estimate | Conditional correlated alignment against a checked VIO snapshot | Guaranteed progress while snapshots advance; long-gap propagation without invented independence |
| Global retrieval | Bounded NumPy cosine index; up to four candidates in the research path | Large-area recovery coverage and measured indexing strategy; FAISS is a target, not this implementation |
| Retrieval scheduling | Search on every selected complete camera window | Separate tracking and recovery schedules with escape from a wrong local prior |
| Temporal ambiguity | Per-frame duplicate/ambiguous candidate grouping and a research student | Qualified multi-location temporal hypotheses, cold start, wrong-map rejection and recovery |
| Aircraft output | Offline eligibility/frame/covariance checks | Firmware-pinned transport, timing/reset/loss behavior and independent vehicle testing |

Evidence: [live learned runner](../src/vision_nav/neural/live_sensor_inference.py),
[query owner](../src/vision_nav/estimation/query_anchors.py),
[current refresh](../src/vision_nav/estimation/delayed_alignment.py),
[retrieval](../src/vision_nav/neural/replay_retrieval.py),
[candidate grouping](../src/vision_nav/neural/candidate_groups.py), and
[PX4 boundary](../src/vision_nav/px4/bridge.py).
The current student uses project-owned spatial cross-attention and weighted
temporal pooling, not a reproduced online Video2BEV network. Successful ONNX
execution does not establish mission accuracy.

### Recommended Architecture Refinements

1. **Preserve two rates.** Continuous local VIO must not wait for learned map
   inference. Map results retain their original observation time and correct
   the local/global relationship asynchronously. A current pose needs actual
   propagation with its uncertainty, not a newer label on an old pose.
2. **Fix current-state availability.** `refresh_current` deliberately withholds
   when the native snapshot advances during a solve. This can repeatedly
   exhaust retries. Profile geometry, joint-noise construction, serialization
   and optimization separately. Exact, bounded caching of unchanged conditional
   geometry may reduce work, but cannot by itself qualify delayed propagation.
   Do not relax freshness or discard cross-covariance to improve throughput.
3. **Test map-backed tracking as an independent ablation.** Compare learned
   keyframe corrections alone against tracking their valid 2D-3D landmarks
   between keyframes. Retain landmark identities, observation age, occlusion
   checks and shared-error ownership. Keep native VIO as the motion baseline;
   tracked observations are not independent of their source images/map.
4. **Make recovery different from tracking.** During tracking, use bounded
   nearby candidates. On inconsistency or loss, widen search and preserve
   competing locations. Measure false global locks and recovery time separately
   from known-start localization. Four plausible candidates are not proof that
   the true place was retrieved.
5. **Make geometry and map age release criteria.** Require co-registered
   orthophotos and valid surface height or explicitly supported ground points.
   A roof pixel must not inherit bare-earth height. Test lower-altitude,
   vegetation, repeated texture and stale-map routes, not just easy urban pairs.
6. **Keep deployment evidence separate from research results.** Train on RunPod
   with pinned inputs and lineage; fly offline. Retain Pi 5 as the required
   target, but do not claim it meets the complete workload budget yet. Qualify
   camera, raw IMU, inference, solver and microSD recording concurrently before
   deciding whether retrieval distillation or optional acceleration is needed.

### Research Update And Limits

- [OrthoTrack v2](https://arxiv.org/html/2606.25245v2) directly motivates the
  map-backed tracking experiment. Its appendix reports 1.4-second keyframe
  localization and 23.8 FPS aggregate throughput on an L40S. Those numbers do
  not establish a 30 FPS deadline or Pi performance. Its
  [code license](https://raw.githubusercontent.com/cvg/orthotrack/main/LICENSE)
  restricts commercial use; treat it as a research reference pending permission,
  not a product dependency.
- [AeroMap3D](https://arxiv.org/abs/2607.14009) motivates scale/yaw normalization
  and rejecting terrain-inconsistent surface matches. Evaluate those changes
  with our existing learned matcher before making another large model mandatory.
- [NGPS](https://arxiv.org/abs/2607.18936) supports asynchronous inertial/VIO/map
  fusion. Its reported 2.94 m RMSE on Orin NX is an author result, not evidence
  for our Pi, covariance model or safety policy.
- [Wang et al., ISPRS, July 2026](https://isprs-archives.copernicus.org/articles/XLIX-B1-2026/55/2026/)
  evaluates learned correspondence with orthophotos/DSMs at 100-250 m. Reported
  texture and altitude sensitivity reinforces the need to test our actual X500
  operating envelope rather than borrow accuracy from higher-altitude datasets.
- [Bearing-UAV](https://github.com/liukejia121/bearinguav) now has official code,
  weights and dataset instructions. Its documented navigation begins from a
  known location within four tiles. It remains useful for teacher/baseline work,
  but that protocol does not establish unrestricted global recovery.
- [Video2BEV](https://github.com/HaoDot/Video2BEV-Open) remains an offline
  reconstructed-BEV research reference. Distillation into our compact student
  must demonstrate incremental benefit on a fixed holdout.

These recommendations are engineering inferences, not reproduced paper results.
Additional community implementations such as
[GeoAnchor](https://github.com/YusufGUNEL/GeoAnchor) are useful failure-case
references, not a reason to replace the qualified-estimator work with a reported
headline median or to reintroduce classical map descriptors.

### Next Delivery Order

1. Stabilize the installed camera, lock focus/aperture and calibrate the actual
   operating resolution. Measure camera/IMU timing and transform; a moving
   gimbal requires time-varying extrinsics and digital stabilization requires
   its pixel warp. [OpenVINS calibration](https://docs.openvins.com/gs-calibration.html).
2. Produce one complete, checksummed X500 capture before collecting a large
   training set. Include raw imagery/inertial timestamps, calibration, settings,
   PX4 logs, map release and independent reference. Keep GNSS out of denied-mode
   inference and split evaluation by geography and flight, not adjacent frames.
3. Establish local-VIO drift, retrieval recall and geometry errors; close
   current-state starvation and test loss/recovery with realistic wall-clock
   delays. Measure uncertainty coverage as well as accepted-fix precision.
4. Train/adapt one candidate-conditioned student on RunPod and compare each
   normalization or teacher change against the frozen held-out baseline.
5. Measure complete Pi latency tails, memory, thermal throttling and storage
   load. Then qualify the separate PX4 transport on a prop-off bench. Its
   [external-vision guide](https://raw.githubusercontent.com/PX4/PX4-Autopilot/main/docs/en/ros/external_position_estimation.md)
   calls for 30-50 Hz streaming and explicit frame conventions; ground-station
   GPS/vision status switching is not autopilot estimator failover.

### Verification For This Reassessment

105 targeted tests passed in 6.27 seconds: `test_query_anchors.py`,
`test_delayed_alignment.py`, `test_current_alignment_refresh.py` and
`test_px4_boundary.py`, using the local estimator environment. This checks
lifecycle, freshness and boundary contracts, not real-model accuracy, native
rebuilds, Pi timing or flight performance. No runtime behavior was changed.

## Follow-Up Review: Live Integration And Research Priorities

Subsequent implementation: [automatic learned-query ownership](learned-query-anchors.md)
now addresses finding 1 below through an explicit opt-in live path. It retains
before inference, separates accepted-evidence age from query deadlines and
releases retired evidence. The findings below record the reviewed pre-change
state; continuous-current estimation, physical uncertainty and Pi/X500
qualification remain open.

Reviewed against the current working tree and primary sources on September 5,
2026. **Keep continuous native VIO plus asynchronous learned map localization.**
The next milestone is a measured, replayable X500 shadow run, not a larger
collection of teacher models. The following findings supersede older maturity
statements where they differ. No runtime source was changed by this review.

### Concrete Integration Findings

1. **Map queries are still registered too late.**
   `neural/live_sensor_inference.py:258` calls inference before observer binding;
   `estimation/live_alignment.py:193` creates the coordinator query only after
   uncertainty binding. Neither path acquires the native frame anchor before
   inference. The live retain/release API exists, but a slow learned result can
   outlive its normal VIO clone. Acquire the exact issued frame before dispatch,
   preserve the original dispatch deadline, and explicitly release rejected,
   superseded and retired evidence. Optional accepted-evidence age and explicit
   retirement now exist in the coordinator, but automatic ownership does not.
2. **Current-state refresh can starve.**
   `estimation/delayed_alignment.py:294` solves a snapshot, then requires that
   snapshot still be latest. Continuous ingestion can advance during every
   solve. Its rejection is conservative, but bounded retries do not guarantee
   an available current position. Qualify delayed correction plus correlated
   forward propagation, or an estimator update mechanism with bounded latency;
   do not relabel a historical solution or relax freshness to hide the problem.
3. **Uncertainty is a contract, not yet a measured sensor model.**
   `estimation/live_alignment.py` requires explicit research noise providers.
   Shared images, map registration, terrain error, calibration and PX4-derived
   inertial information can correlate estimates. Independent-looking matrices
   in synthetic tests are not flight calibration. Measure error coverage on
   held-out flights, retaining cross-correlations and an explicit unknown state.
4. **Recovery and geometric tracking are separate unfinished capabilities.**
   `neural/candidate_groups.py` groups one frame's redundant metric candidates;
   it is not a qualified temporal global-recovery posterior. The reviewed live
   learned path also does not propagate map-backed feature tracks between
   keyframes. Test cold start, wrong local prior, ambiguous terrain and recovery
   separately from known-start local tracking.
5. **Hardware and output remain qualification boundaries.**
   The single-owner sensor session is a finite prop-off experiment. Physical
   synchronization, camera mount/extrinsics, sustained Pi load and microSD
   recording need X500 evidence. `px4/bridge.py:140` still has no aircraft
   transport. This is appropriate while global validity remains unqualified.

### Research That Changes The Next Experiments

| Research | Useful direction | Limit on adopting its results |
| --- | --- | --- |
| [OrthoTrack, June 2026](https://arxiv.org/html/2606.25245v1) | Reuse map-backed 2D-3D correspondences through inter-frame optical flow; refresh with learned keyframe matching | Reported timing uses an L40S GPU, not Pi. Map quality and tracking continuity matter. Benchmark throughput is not a bound on delayed-update age |
| [AeroMap3D, July 2026](https://arxiv.org/html/2607.14009v1) | Evaluate scale/yaw normalization before matching and remove matches whose image surface is inconsistent with terrain height | Main trajectory evaluation uses reference-derived motion, coarse initialization and offline timestamp ordering; it is not proof of live raw-IMU fusion |
| [NGPS, July 2026](https://arxiv.org/abs/2607.18936) | Supports multi-rate inertial/VIO propagation and asynchronous satellite corrections | Reported Jetson Orin NX performance and heuristic confidence weighting do not qualify our Pi or covariance |
| [Bearing-UAV official release](https://github.com/liukejia121/bearinguav) | Released teacher, weights and dataset support a reproducible map-conditioned baseline | Documented navigation initializes at a known position within four tiles, not unrestricted global recovery |
| [Video2BEV](https://arxiv.org/html/2411.13610v3) | Offline reconstructed-BEV teacher and retrieval experiment | Its reconstructed-view pipeline is not our short-window CNN/GRU student; distillation remains a separate experiment |
| [SASGeo, July 2026](https://arxiv.org/abs/2607.07737) | Spatial semantics, contradictory/unknown observations and ambiguity rejection are useful ablations | Published evidence is a synthetic proof of concept, not validated flight navigation |

**New priority: test map-backed tracking, not another mandatory teacher.**
Retain local VIO as the nominal motion estimator. In a separate experiment,
compare learned keyframe matching plus tracked map landmarks against the current
keyframe-only correction path. Track landmark identity, shared map error,
forward/backward flow checks, spatial coverage, occlusion and original keyframe
age. Tracked observations sharing images or landmarks must not enter as
independent new evidence. This is a proposed experiment, not an implemented
replacement for native passive pose anchors.

The [OrthoTrack repository](https://github.com/cvg/orthotrack) requires CUDA and
lists different VRAM budgets for matcher profiles. Its
[released license](https://raw.githubusercontent.com/cvg/orthotrack/main/LICENSE)
is non-commercial. Do not import it into the contract product or assume that
internal commercial evaluation is automatically permitted; obtain permission
or an appropriate licensing review first. The paper also reports substantial
keyframe latency, so its average frame rate must not become our control budget.

### Recommended Delivery Gates

1. **Capture gate:** lock manual focus/aperture, record the exact settings,
   calibrate at the operating resolution, and verify exposure timing, raw IMU,
   clock drift and camera-to-IMU transform on the installed mount. Moving
   stabilization requires a measured time-varying transform; digital stabilization
   requires its pixel warp. See [OpenVINS calibration](https://docs.openvins.com/gs-calibration.html).
2. **Baseline gate:** one checksummed real X500 session must replay local VIO
   and learned map verification. Keep GNSS reference out of denied-mode inputs;
   ordinary GNSS is not sub-meter ground truth. Split evaluation by flight and
   geography, not adjacent frames.
3. **Runtime gate:** complete query/anchor ownership, accepted-evidence retirement
   and bounded delayed-current estimation. Test long gaps, dropped images,
   queue pressure, release failures and source restarts without aircraft output.
4. **Training gate:** compare the existing learned baseline, scale/yaw adapter
   and map-conditioned student on the same fixed holdout. Use RunPod only for
   reproducible bounded experiments with checkpoint/release lineage. Adding
   Video2BEV, semantic or dense-teacher losses requires measured incremental gain.
5. **Pi gate:** measure end-to-end P50/P95/P99 sensor age, inference and solve
   latency, dropped frames, memory, thermal throttling and microSD writes together.
   ONNX export success is not deployment acceptance. Keep AI HAT acceleration
   optional until provider parity and sustained measurements exist.
6. **PX4 gate:** qualify firmware-specific frames, reset behavior, timestamp and
   covariance conversion, and source-loss behavior on a prop-off bench. The
   [PX4 guide](https://raw.githubusercontent.com/PX4/PX4-Autopilot/main/docs/en/ros/external_position_estimation.md)
   describes a 30-50 Hz external-vision stream; repeated stale map fixes are not
   a substitute for fresh propagated state. GPS health/status switching in the
   ground station is not equivalent to qualified autopilot estimator failover.

### Verification In This Review

104 tests passed in 18.27 seconds using `/tmp/drone-estimator-tests/bin/python`:
`test_delayed_alignment.py`, `test_current_alignment_refresh.py`,
`test_continuous_alignment.py`, and `test_px4_boundary.py`.
These were targeted local estimator/contract tests, not the similarly numbered
main check suite, a complete repository test run, a new native build, a Pi load
test or a flight trial. Existing uncommitted source work was left untouched.

## Current Decision And Next Milestone

Rechecked against current source and primary research on September 5, 2026.
**Keep the architecture; prioritize a measured end-to-end system over adding
teacher models.** The near-term deliverable is a repeatable X500 recording and
shadow replay, not autonomous GNSS-denied flight. Desktop changes remain deferred.

Targeted review verification: 81 tests passed in continuous alignment,
isolated estimator worker and PX4 boundary suites using the local estimator
environment. This review changed documentation only; it did not rerun the full
suite, qualify every pending working-tree edit, test real hardware or enable
publication. `git diff --check` passed.

The intended runtime has two independent rates:

```text
Calibrated exposure-timed camera + synchronized raw IMU
  -> continuous native local VIO -> local pose and uncertainty

Selected immutable camera windows + preloaded map release
  -> learned retrieval -> bounded candidate verification
  -> valid surface-backed 3D correspondence -> robust metric pose
  -> temporal ambiguity and delayed map/local alignment

Local state + qualified map/local alignment
  -> propagated global estimate and explicit validity
  -> shadow evidence now; separately qualified PX4 adapter later
```

PX4 attitude is auxiliary context, not a replacement for raw inertial samples.
GNSS reference must not enter the denied-mode retrieval or estimator inputs.
Optional pressure-derived height can be evaluated separately; it does not
establish absolute terrain height or repair incorrect map geometry.

### Highest-Priority Gaps

| Priority | Current limitation | Required evidence or change |
| --- | --- | --- |
| 1 | Camera/IMU interfaces exist, but physical timing and extrinsics are not qualified | Record exposure times, raw IMU, clock offset/drift, calibration, mount configuration and PX4 ULog from the same X500 session; verify useful images under vibration |
| 2 | Continuous conditional alignment only survives while original map anchors remain retained | Preserve map/local alignment information and its correlations across marginalization, or supply a qualified forward-propagation design; test long gaps and recovery without reusing old fixes as new evidence |
| 3 | Numerical uncertainty plumbing is not measured navigation uncertainty | Calibrate pixel, elevation, map registration, timing and camera errors on independent flights; measure error coverage and false accepts, including shared VIO/map/PX4 information |
| 4 | Native/model execution evidence is not a sustained Pi budget | Profile serialization, copying, solver, inference and recording together; measure P50/P95/P99 measurement age, dropped inputs, RSS and thermals with the normal freshness policy |
| 5 | Candidate grouping and a conditioned student do not establish reliable global recovery | Separate known-start, cold-start, wrong-map and lost-track evaluations; allow recovery to escape a wrong local prior; test false global locks and recovery time |
| 6 | Research pose conventions are not a MAVLink flight payload | Keep publication disabled until firmware-specific frames, clock conversion, covariance packing, reset behavior and loss handling pass an independent integration gate |

Do not raise freshness limits to make a slow live test pass. Do not require
simultaneous reproduction of every teacher before evaluating the learned
baseline. Native local feature tracking does not reintroduce classical
UAV-to-map descriptors into the active learned product.

### Research Implications

- **NGPS, July 2026:** supports the multi-rate VIO/inertial plus asynchronous
  satellite-correction direction. Its authors report 2.94 m position RMSE over
  five sequences on Jetson Orin NX; neither this accuracy nor its throughput is
  established for our Pi or camera. Adopt the scheduling principle, not its
  heuristic confidence-to-covariance mapping without calibration.
  [Primary paper](https://arxiv.org/abs/2607.18936).
- **AeroMap3D, July 2026:** motivates testing scale/yaw normalization and
  surface-validity filtering before terrain lifting. Its evaluations are
  offline and initialized by the user; the main motion prior is generated
  from reference data, and wall-clock delayed fusion is not evaluated. Our
  conservative support masks cover part of the geometry concern, not the
  full method or its reported performance.
  [Methods and limitations](https://arxiv.org/html/2607.14009v1).
- **Bearing-UAV:** official code is available, so the old paper-only premise
  is obsolete. The documented navigation starts at a known position within
  four satellite tiles. Keep it as a map-conditioned teacher/baseline; do not
  interpret its regression output as calibrated, map-wide recovery.
  [Official repository](https://github.com/liukejia121/bearinguav).
- **Video2BEV/UniV:** retain as an offline teacher/retrieval experiment, not a
  claimed reproduction of our compact online student. The released benchmark
  separates training and test universities; similarly isolate our flight
  geography rather than randomly splitting neighboring frames.
  [Official repository](https://github.com/HaoDot/Video2BEV-Open).
- **UCVL/XIAN-Visloc, August 2026:** a useful additional retrieval evaluation
  candidate, with 21 trajectories totaling 81.13 km and a held-out city. Test
  continuous coverage, tile-boundary behavior and broad-area candidate search,
  rather than only isolated positive image pairs. This is coarse localization
  evidence, not a synchronized VIO or aircraft-control benchmark. Review code,
  dataset and underlying satellite-imagery permissions before use.
  [Author repository](https://github.com/VERYBC/UCVL),
  [Paper](https://doi.org/10.1016/j.isprsjprs.2026.04.054).

These are engineering inferences from the cited sources, not reproduced results.
NaviLoc is also relevant to trajectory-level reasoning, but its published data
availability statement restricts access to non-commercial academic research
under an NDA. Do not make it a commercial training dependency without separate
permission. [Publication](https://doi.org/10.3390/drones10020097).

### Practical Delivery Order

1. Stabilize the camera mechanically while preserving a fixed or measured
   camera/IMU transform. A moving gimbal needs synchronized orientation; digital
   stabilization needs its image transform recorded.
2. Produce one checksummed, replayable X500 session with calibration, imagery,
   raw inertial timing and an independent reference. Fix capture defects before
   collecting large volumes. See [OpenVINS calibration](https://docs.openvins.com/gs-calibration.html).
3. Establish real local-VIO drift and learned retrieval/geometry baselines on
   those recordings; freeze geographic and flight-level evaluation splits.
4. Train or adapt one candidate-conditioned student on RunPod, comparing each
   teacher/normalization change against the same baseline. No cloud dependency
   belongs in the in-flight localization path.
5. Close long-gap alignment and physical uncertainty gaps; qualify the combined
   shadow runtime on Pi with camera, USB telemetry and microSD logging active.
6. Review PX4 integration separately. Current guidance calls for a 30-50 Hz
   external-vision stream; repeated, re-timestamped map fixes are not a valid
   substitute for fresh propagated state. Pin the actual aircraft firmware.
   [PX4 guide](https://docs.px4.io/main/en/ros/external_position_estimation).

## Implementation Evidence

[Native passive anchors](native-passive-anchors.md) now provide an opt-in offline
retention path beyond the ordinary VIO window while keeping native correlations.
The replay test retained an original pose for 4.3 s against a 0.5 s normal window
and aligned a later pose using one unchanged synthetic map observation. The
subsequent [live-control API](live-native-anchors.md) binds retention to issued
frame tickets and ordered native acknowledgements, with recorded replay.
The subsequent [query owner](learned-query-anchors.md) connects pre-inference
retention and accepted-evidence lifetimes. Physical uncertainty, current-state
propagation and cadence qualification remain incomplete; this is not
aircraft-ready long-gap navigation.

The estimator's [bounded serialization path](alignment-serialization.md) now
removes redundant round trips while preserving supported JSON bytes and owned
containers. A paired local benchmark measured lower copying/encoding cost;
this is not a change to the solver or a claim of qualified end-to-end latency.

Subsequent implementation: [offline ground-support masks](ground-surface-masks.md)
now connect reviewed polygons to learned map compilation and live/replay PnP.
Existing v1 sources remain DSM-only. An explicit v2 source may use bare-earth
DTM only inside a checksum-bound support mask; unknown/excluded pixels are
removed before 3D lifting. This implements conservative map conditioning, not
automatic semantic classification, AeroMap3D reproduction or field qualification.

Large learned-match sets now have an explicit [bounded PnP refit](bounded-learned-pnp.md)
before conditional estimation. Full-set acceptance is required; a spatially
distributed final fit retains at most 24 pairs and must explain the original
consensus. Original evidence and selected identities remain in the trace. This
addresses the dense estimator's input bound, not physical uncertainty or Pi
latency qualification.

Latest bounded-fit verification: 245 focused estimator/VIO/PX4 tests, 132
broader model/native/map tests, a 13-test candidate-path follow-up and all 104
main checks passed. Three optional direct-checkpoint tests were skipped;
actual ONNX tests executed. Counts overlap where suites were rerun. See the
bounded-fit guide for environments, artifacts and limits.

The next implementation adds [independent snapshot refresh](continuous-research-alignment.md)
between learned fixes. It re-solves retained original map evidence against
new native posteriors in the isolated estimator, with its own trace and a
one-slot pending draft queue. This removes dependence on another learned
result arriving before an update. It is not propagation beyond clone retention
or a qualified global state; anchor expiry still withholds output.
The offline native test observed roughly 329-582 ms processing per refresh
with a deliberately permissive 10 s replay receipt-age limit. This verifies
execution, not the normal 500 ms freshness policy at live camera cadence.
Profiling copying, serialization and numerical work while preserving covariance
is the next estimator performance task. The continuous guide records the 266 focused,
133 broader and 104 main passing checks and the remaining qualification gaps.

## Latest Working-Tree Reevaluation

This follow-up checks the implemented live sensor session as well as recent
primary research. It supersedes older statements below that describe dynamic
native VIO input as not yet connected. No runtime or vehicle configuration was
changed for this review; desktop work remains deferred.

**Decision: retain the architecture, narrow the implementation program.**
Continuous local VIO plus asynchronous learned map correction is the appropriate
target. The project has working research components, but not an end-to-end,
field-qualified GNSS-denied navigation product. More teacher models are not the
critical dependency for collecting useful X500 data.

### What The Code Actually Provides

| Area | Implemented evidence | Remaining boundary |
| --- | --- | --- |
| Live acquisition | `vio/sensor_session.py` owns camera and MAVLink readers, buffers synchronized inputs and feeds native VIO | Real X500 synchronization, vibration, sustained Pi load and shutdown behavior |
| Local estimation | Native OpenVINS stream, bounded inputs, correlated state windows and explicit faults | Hardware calibration and flight-route accuracy; research licensing review |
| Learned localization | Model exports, retrieval/correspondence, map-conditioned replay and live image-ticket inference binding | Qualify live execution on Pi and fuse asynchronous corrections |
| Delayed estimation | Joint-snapshot refresh, live learned binding, independent between-fix refresh, replay inputs and optional deadline-controlled estimator process | Propagation beyond retained map anchors, Pi deadline/retention qualification and calibrated correlations |
| Recording | Single-owner live archive, exact native replay export and calibration/map-bound training-capture export | Strict timing/reference qualification, dataset splits and real microSD performance |
| PX4 | Offline conversion and publication gates | `publish_mavsdk` intentionally raises `NotImplementedError`; no qualified aircraft transport |

The live session now has an optional bounded archive of the same accepted
images/events and a verified offline native replay export. Acquisition retains
a single camera/serial owner; do not start a competing training recorder.
Partial captures remain visibly partial. A separate exporter now connects the
recording to the existing training-capture schema, preserving exact native replay,
calibration values, map digest and exposure timing. Missing pre/post clock
snapshots or GNSS remain explicit strict-training-gate failures; this is not a
qualified supervised DatasetRelease. Storage throughput, capacity and power-loss
behavior remain to be measured on microSD.

The opt-in [live learned session](live-learned-sensor-session.md) now executes
retrieval, the temporal student and optional learned geometry against native
image tickets with auxiliary exposure-aligned PX4 attitude. It keeps the single
camera/link owners and records native progress and retained-anchor diagnostics.
There is still no qualified global state or navigation covariance from this path,
and no PX4 publication. The CLI now uses an owned model process with bounded
startup/inference waits and termination escalation. Sustained Pi qualification
remains outstanding; it is a finite prop-off experiment, not the default service.

A separate current-snapshot refresh API now retries delayed alignment using
original PnP evidence and newly supplied snapshot-specific joint uncertainty.
It can update a latest retained pose with no direct map fix while preserving
cross-correlations. Synthetic Gaussian and actual native-process tests exercise
this behavior. An opt-in online observer now invokes it when both explicit
research noise providers are supplied; there is no qualified noise provider.
It does not extrapolate beyond a retained snapshot,
establish sensor-age freshness or enable aircraft output.

The [learned PnP binding](learned-pnp-estimator-binding.md) now translates a
successful live learned row into a conditional estimator request using its
original native frame ticket, pipeline digest, complete candidate set and exact
feature identities. An explicit uncertainty object is still mandatory. This
closes a data-format gap, not the measured-noise gap. The new
[live observer](live-research-alignment.md) supplies the optional runtime
callback and sole native-snapshot consumer. CLI use is audit-only by default.
Explicit providers can now run in an [owned child process](research-alignment-process.md)
for conditional research estimates, with startup/operation deadlines, source
digests and complete replay inputs. The direct-callback reference API still has
no hard timeout. No aircraft publication is activated.

Latest isolated-estimator integration verification: 228 focused estimator/binding/VIO/PX4
tests, 64 actual-model/native integration and correspondence tests, and 104
main checks passed. Three optional direct-checkpoint tests were skipped; see
the estimator process guide for exact scope. This advances execution evidence, not
physical timing, learned geographic accuracy or vehicle qualification.

### Research Update And Limits

- **NGPS (July 2026):** asynchronous satellite corrections, VIO and inertial
  fusion reinforce the multi-rate design. Its reported platform is Jetson Orin
  NX, not Pi 5. This is author-reported system evidence, not our reproduction
  or a transferable performance budget. [Paper](https://arxiv.org/abs/2607.18936).
- **AeroMap3D (July 2026):** evaluate scale/yaw normalization before matching
  and building masks before bare-earth DEM lifting. Crucially, its evaluation
  is offline with operator initialization; the dataset lacks raw IMU, and the
  main relative-motion prior is generated from GNSS reference data with noise.
  It does not measure wall-clock delayed fusion. Use it as geometry/registration
  research, not proof of live VIO or cold-start recovery. Our proposed experiment
  would use XFeat/LighterGlue, not move RoMa v2 onto Pi.
  [Methods and evaluation](https://arxiv.org/html/2607.14009v1).
- **Bearing-UAV:** the official implementation is available. Its documented
  navigation begins from a known position in a four-tile urban block. Evaluate
  it as a conditioned model/teacher; measure unrestricted recovery separately.
  [Official repository](https://github.com/liukejia121/bearinguav).
- **MegaLoc and XFeat:** retain as explicit retrieval and correspondence
  baselines, not as evidence of aerial pose accuracy. Promote replacements only
  after identical geographic-holdout and Pi tests.
  [MegaLoc](https://github.com/gmberton/MegaLoc),
  [XFeat](https://github.com/XinningC/XFeat).

These are engineering recommendations from source comparison. No paper's
reported accuracy or availability is a guarantee for our camera, altitude,
map resolution, season, compute budget or commercial data permissions.

### Architectural Decisions To Make Explicit

1. **Two clocks of computation, one sensor identity.** Ordered VIO inputs and
   latest-complete learned windows share immutable frames. Archive through that
   same ownership boundary; never let inference or storage silently stall local
   tracking. Log overload and end/reset the affected epoch explicitly.
2. **Local tracking and global lock are different states.** Report local health,
   last global correction age, ambiguity, drift uncertainty and publication
   eligibility separately. An old global fix plus ongoing VIO is not a new fix.
3. **Map geometry is a measurement input.** Bind surface type, vertical datum,
   georeferencing uncertainty and visibility to each map release. Use DSM/3D
   where available; test conservative invalid-surface masks for bare-earth DEM.
   A mask's absence is not proof that a feature is on the ground.
4. **Recovery must escape the current prior.** Bound routine neighborhood search,
   but reserve a wider recovery path. Score known-start, cold-start, wrong-map
   and lost-track recovery separately; nearby-frame train/test splits are not
   independent evidence.
5. **Preserve uncertainty and information dependence.** Neural scores are not
   pose covariance. Overlapping imagery, map errors and shared Pixhawk IMU data
   introduce correlations within the companion and at PX4. Keep independent
   factor replay and correlated-posterior experiments separate until reviewed.
6. **Keep the deployment model small by measurement.** Benchmark concurrent
   acquisition, VIO, retrieval, local matching, logging and thermal behavior.
   Compress the measured bottleneck; do not require every teacher in the live
   path or assume optional accelerator compatibility.

### Recommended Delivery Gates

| Order | Work | Evidence required before advancing |
| --- | --- | --- |
| 1 | Camera mount, focus/exposure and calibration | Fixed or measured camera/IMU transform; exposure/IMU clock audit; useful imagery under measured vibration |
| 2 | Complete single-owner recording | Retained frames, raw inertial data, timing, calibration, PX4 logs and independent reference; replayable checksummed session |
| 3 | Real local VIO baseline | Repeated X500 recordings with timestamp/dropout tests and trajectory error against qualified reference |
| 4 | Learned baseline and initial RunPod training | Frozen geography/flight splits, wrong-map negatives, source/checkpoint/dataset provenance; baseline before each teacher ablation |
| 5 | Connected live shadow system | Image-bound asynchronous results, ambiguity rejection, calibrated error coverage and no flight output |
| 6 | Sustained Pi qualification | P50/P95/P99 end-to-end delay, RSS, thermals, dropped frames, storage load; retained-state horizon covers measured delays |
| 7 | Separately approved PX4 integration | Firmware-specific frame/time/covariance/reset/origin tests, prop-off acceptance and defined loss/recovery behavior |

Current PX4 documentation describes external-vision `ODOMETRY` using
`MAV_FRAME_LOCAL_FRD` and a 30-50 Hz message stream. A slow map matcher cannot
meet that need by restamping old fixes. Any future stream needs fresh propagated
state and qualified uncertainty, plus an explicit decision about local-only
operation after global lock loss. No parameters or output were enabled here.
[PX4 integration reference](https://raw.githubusercontent.com/PX4/PX4-Autopilot/main/docs/en/ros/external_position_estimation.md).

Review verification: 91 focused tests passed in `test_live_sensor_buffer.py`,
`test_px4_boundary.py` and `test_delayed_alignment.py` using the existing local
estimator environment. This does not rerun the entire suite or constitute
physical sensor, native-session, learned accuracy or Pi qualification.

## Verdict

Keep the learned UAV-to-map pipeline as the product's central capability. The
right architecture is continuous local visual-inertial odometry (VIO) plus
asynchronous learned global corrections, not a sequence of independent image
matches presented as continuous navigation.

The repository now contains substantive model export, map geometry, VIO replay,
and correlated-estimation experiments. It does not yet demonstrate the complete
system on a Raspberry Pi or on held-out X500 flights. Additional teacher models
are secondary to connecting, measuring, and qualifying these components.

The [released Bearing-UAV audit and reference adapter](bearinguav-reference-evaluation.md)
now distinguish the real neighbor-conditioned regression model from the earlier
paper-only assumptions. Its CPU checkpoint compatibility is tested, but it does
not supply calibrated uncertainty, a no-match head or independent map-wide
recovery. Dataset evaluation and weights/data permission checks remain pending.

The [position-only fine-tuning path](bearinguav-training.md) now connects our
converted captures to the actual checkpoint, with training-membership auditing,
wrong-map validation and checksum-bound save/reload. Repeated CPU optimizer
tests use synthetic queries only. This is domain-adaptation infrastructure, not
official training reproduction, heading qualification or measured flight accuracy.

An isolated [newer-framework comparison](bearinguav-environment-parity.md) now
checks the same model/input against Torch 2.1.2 and 2.13.0. Synthetic CPU output
parity passes; this does not qualify Linux/CUDA, remove legacy augmentation
dependencies, or establish field accuracy. Training environment changes remain
separate experiments rather than in-place upgrades of resumable runs.

The [teacher-to-student connection](bearinguav-student-distillation.md) now runs
the released Bearing-UAV model to generate positive-context training points,
adds an optional loss to the compact map-conditioned student and preserves
teacher lineage through ONNX export. Synthetic execution passes but is not
evidence that distillation improves accuracy. Temporal/distribution teacher
signals and real geographic holdout comparison remain open.

The immediate hardware priority remains flight testing, rigid camera mounting,
focus/exposure and timing qualification, then synchronized footage collection.
Keep learned navigation in shadow mode during this work. Desktop expansion can
wait; no ROS or Gazebo dependency is needed for the recommended runtime.

The [portable Video2BEV workspace](video2bev-portable-workspace.md) now runs both
upstream data loaders and a separate random-initialization model forward/backward
check. The latter now preserves the upstream ViT/fusion/head and actual matching
loss/negative-selection functions, verifying frozen evaluation-mode encoding and
matching updates. It does not validate pretrained weights, the default Swin
configuration, a complete real-data trainer, CUDA or geographic accuracy.

## Findings In Priority Order

### 1. The PX4 boundary is not ready for publication

The initial review found a position-only frame conversion, unchanged orientation
and covariance, and future timestamps clamped to age zero. Subsequent offline
hardening corrects all three: explicit body FRD and world-left tangent contracts,
consistent pose/cross-covariance rotation, symmetric/PSD checks and rejection of
future timestamps. Unspecified conventions and release IDs fail closed. Tests
independently verify rotations and correlated covariance. The typed output is
an offline record, not a MAVLink packet; transport remains unimplemented.

Current PX4 main documentation describes external-vision ODOMETRY with
`MAV_FRAME_LOCAL_FRD` and recommends 30-50 Hz. The former blanket LOCAL_NED
wire contract is replaced by a pending firmware-pinned decision. Document parent/child frames, velocity,
covariance ordering/packing, clock conversion, reset counter and global origin.
Do not relabel delayed map fixes as fresh samples to achieve the required rate.
[PX4 external-position documentation](https://raw.githubusercontent.com/PX4/PX4-Autopilot/main/docs/en/ros/external_position_estimation.md).

Recommendation: leave publication disabled. Complete and independently test this
boundary before any future external-vision flight experiment.

### 2. Learned geometry still lacks qualified measurement uncertainty

Subsequent implementation: the [conditional PnP uncertainty tool](learned-pose-uncertainty.md)
now propagates explicit joint image/map/intrinsic errors and preserves the exact
PnP inlier set. It advances the research measurement model, not field covariance
calibration; timing, extrinsics, selection and VIO correlations remain open.

`neural/replay_local_geometry.py` intentionally returns `covariance: null` and
no position authority. Good correspondences and a plausible PnP solution do not
by themselves supply calibrated pose uncertainty or prove the correct location.

The new `estimation/correlated_window.py` is a research-only batch solver for one
VIO posterior snapshot. It requires joint VIO covariance, joint map covariance
and explicit cross-covariance. It rebuilds rather than accumulates snapshots and
withholds inconsistent batches. Its tests pass, but it does not create those
uncertainty inputs from imagery. It is not a live, multi-hypothesis fusion engine.

Recommendation: estimate geometry uncertainty with image, map-surface,
calibration and timing errors represented; validate coverage on held-out data.
Shared pixels and shared map errors must not become independent evidence merely
because two modules emitted separate outputs. Do not manufacture covariance
from neural confidence or substitute zero cross-covariance without justification.

### 3. Delayed corrections need a live retention design

The active shadow loop now preserves Picamera2 capture metadata and the converted
first-row exposure time instead of stamping frames at inference start. It records
capture-to-result age, withholds stale temporal/candidate evidence and clears
invalid temporal windows. This closes a timing-substitution bug, not the
asynchronous retention design. Continuous live capture now runs in an independent,
single-owner worker with one latest-only pending frame, preserving timing and
reporting superseded frames. Picamera2 now hands off an owned in-memory frame;
only frames selected for inference are JPEG-encoded and saved, outside the
producer lock. File-only camera backends retain their spool behavior. This reduces
discarded-frame storage writes by construction, but has not been measured on Pi.
After initialization, a separate compact status heartbeat now reports inference
activity/overdue state and live camera/attitude health. It never refreshes old
position evidence; full completed decisions remain in the trace. Model processing
is still serial, ATTITUDE context still uses unqualified receive time, and there is no connected
live VIO state history. See [shadow-run timing](first-learned-shadow-run.md).

The [native stream worker](native-vio-stream-worker.md) now establishes a
separate process and bounded sensor-batch interface for local VIO. Recorded-input
tests produce byte-identical state history to native file replay and verify
progress independent of the calling thread, backpressure and timeout cleanup.
The subsequent finite sensor session now wires a live acquisition interface to
this worker, as described below. Binding the learned shadow consumer remains
open, as do retention/correlation and physical sensor qualification.

The separate [arriving-sensor adapter](native-vio-live-input.md) now feeds new
in-memory camera/IMU batches to the actual native process without fabricating a
sealed recording. It binds calibration and exposure clocks, returns immutable
image tickets, reclaims temporary PNGs after acknowledgement and ends the epoch
on overflow or sensor discontinuity. Synthetic native output matches sealed
replay byte-for-byte. This closes the native dynamic-image input boundary, not
learned model binding or aircraft/Pi qualification. The new opt-in
[finite sensor session](live-vio-sensor-session.md) now connects dedicated camera
and MAVLink owners, clock-bracket buffering, ordered native dispatch and a
latest-complete-window handoff. Synthetic interfaces plus actual native VIO
verify concurrent execution; they do not establish hardware timing or global
localization. This is not yet the default Pi shadow service. PNG writes remain a measured
performance task rather than an assumed zero-copy path.

The [delayed PnP coordinator](joint-pnp-vio-alignment.md#delayed-results) now binds
asynchronous completions to exact native camera clones and worker epochs,
rejecting expired or mismatched jobs. It connects to the existing joint solver
with caller-supplied uncertainty, and lets native state updates continue while
that solve runs. An output overtaken by newer VIO remains a timestamped historical
research result, never a fresh navigation estimate. This implements bounded
result ownership/rejection; it does not extend the native clone window, derive
missing correlations or finish live global-pose propagation.

The independent attitude reader now binds to a configured sender, reports loss,
staleness and bounded-buffer overflow, and retains original PX4 boot timestamps
for ordering checks. Discontinuities invalidate cached attitude/temporal support;
the shadow loop checks again after inference to withhold results affected by a
reader failure or overflow. This is tested with controlled telemetry, not verified
camera/IMU synchronization, continuous raw-IMU ingestion or aircraft evidence.

The native OpenVINS adapter exports correlated clone windows. The measured TUM VI
configuration retains approximately 0.5 seconds of history. The batch solver
requires a map observation at an exact retained clone timestamp. A slower Pi
inference result can therefore arrive after its state has disappeared.

Paired replay now measures exact clone membership when learned processing
finishes, including native worker state and the original posterior receipt time.
It distinguishes initialization/missing clones from retained or out-of-window
anchors, and separates post-EOF observations from those made while native work
is nonterminal. New sealed inputs can explicitly select 2..32 native clones;
existing inputs and the default 11-clone configuration remain unchanged. This
is a research estimator setting, not an automatic Pi memory policy or a repair
for missing measurement correlations. See [retention diagnostics and history
experiments](map-conditioned-replay.md#delayed-result-retention).

Recommendation: measure capture-to-result P99 first, then size a bounded history
and define delayed-result ownership. Choose a reviewed snapshot-replacement or
correlation-preserving fusion approach. Do not stack overlapping VIO posterior
windows as independent factors or interpolate them into invented measurements.
The existing independent-factor smoother and correlated-window solver have
different assumptions and must not be connected interchangeably.

### 4. Aircraft sensor qualification remains the critical dependency

The ROS-free OpenVINS adapter is real image/IMU estimation, with recorded TUM VI
room1 evidence. That is useful local handheld-dataset validation, not proof of
accuracy on a vibrating quadcopter, satellite-map localization or sustained Pi
performance. See [OpenVINS replay](openvins-research-replay.md).

Recommendation: qualify raw accelerometer/gyroscope timestamps, rate, noise,
axes, camera exposure time and camera-to-IMU extrinsics on the actual assembly.
PX4 attitude is context, not a substitute for synchronized raw inertial data.
A [Pi capture-to-VIO converter](pi-capture-vio-replay.md) now connects sealed
recordings to the native replay using explicit sensor profiles and bounded clock
conversion. It has exercised the actual estimator with controlled Pi-schema
fixtures; no measured X500 profile or aircraft trajectory is qualified by that
test. Rolling-shutter and loosely synchronized captures remain unsupported.
A soft or moving camera mount can invalidate a fixed extrinsic calibration;
stabilization must not introduce unmeasured camera motion. Record mount and
manual-focus changes as calibration changes.

### 5. Temporal evidence is not yet a calibrated navigation hypothesis bank

The learned paths conservatively group overlapping, pose-consistent map tiles
without summing their evidence. This is a good duplicate-evidence safeguard.
It does not resolve multiple plausible geographic locations over a trajectory.
Existing tile-keyed persistence is not equivalent to motion-conditioned spatial
hypotheses and can change identity at tile boundaries.

Recommendation: track competing map-frame poses, their support and motion
consistency across time. Separate local tracking health from global map lock.
Expose last accepted global-fix age, local drift and recovery status. A locally
tracked trajectory after map loss is not a fresh global fix.

### 6. Pi feasibility and commercial provenance remain unproven

MegaLoc's local checkpoint is approximately 915 MB before runtime allocations.
Its 8,448-dimensional float32 descriptors cost 33,792 bytes per tile: 10,000
tiles are about 338 MB before index overhead, imagery and sparse features.
The current implementation is bounded exact cosine search, not deployed FAISS.
Model parity on the Mac is not a concurrent Pi timing or thermal benchmark.

An opt-in [live load recorder](live-runtime-load-recording.md) now collects
process CPU/RSS, available thermal/power indicators, kernel I/O counters and
camera/attitude activity on a separate thread during the actual shadow loop.
Logs are bounded and linked to heartbeat/decision run IDs. Controlled runtime
tests exercise this path, but no sustained aircraft/Pi measurements or automatic
performance acceptance are implied.

The [paired learned replay](map-conditioned-replay.md#concurrent-native-vio-replay)
now runs native VIO and the existing learned map pipeline concurrently on the
same sealed camera/IMU sequence, with latest-complete-window selection, exact
image/calibration binding and separate native/Python resource recording. Actual
MegaLoc, XFeat/LighterGlue, a synthetic-trained ONNX student and native OpenVINS
have executed together on synthetic sensor data. This is integration evidence,
not live ingress, delayed global fusion, aerial accuracy or Pi qualification.

The recorder can now explicitly attach an identity-bound native VIO process
probe. Recorded TUM VI execution verified separate child CPU/current-RSS samples
while preserving byte-identical state output. Default learned-shadow recording
is still parent-only until live VIO is connected; no combined Pi workload or
unique aggregate-memory measurement is established by this Mac check.

The same workflow now includes an offline load-report command that joins exact
run IDs, checks capture/result clocks and reports latency quantiles alongside
sampled resource and attitude health. It separates whole-run decisions from
complete intervals inside the sampled resource window, preserves stale results
in latency statistics and exposes evidence gaps. This is measurement tooling,
not a Pi benchmark result or an estimator-retention budget established by data.

Recommendation: retain the measured reference, benchmark the full workload, then
compress/distill the stage that actually exceeds budget. Audit source, weights,
datasets and map licenses separately before product training or distribution.
OpenVINS distribution also needs review of its GPL obligations.

## Research Decisions

These recommendations are inferred from task fit, not independently reproduced
performance. Publication recency alone is not a promotion criterion.

| Research | What the source establishes | Decision for this project |
| --- | --- | --- |
| [Bearing-UAV](https://github.com/liukejia121/bearinguav) | Official code, training/test scripts, dataset and weight links are available. Bearing-Naver describes known-start urban navigation within a four-tile satellite block. | Evaluate the released baseline on RunPod before reproducing architecture from prose. Score known-start navigation separately from map-wide cold start and lost-position recovery. Our small map-conditioned network is not its reproduction. |
| [Video2BEV](https://github.com/HaoDot/Video2BEV-Open) | 3DGS-rendered BEVs feed cross-view retrieval/matching; it is not a released small online temporal BEV network. | Keep as an offline teacher experiment. The [pinned source audit](video2bev-reproduction.md) found missing second-stage imports and a hardcoded negative-data path. Resolve reproduction inputs and portability before measuring incremental benefit. |
| [OrthoLoC](https://deepscenario.github.io/OrthoLoC/) | Orthophoto/DSM-based camera localization and calibration with a dedicated benchmark. Dataset license is CC BY-NC-SA 4.0. | Strong geometry reference. Check permissions before business-related use; do not assume its dataset can train a commercially deployed model. |
| [AnyVisLoc](https://github.com/UAV-AVL/Benchmark) | Released low-altitude/multi-view benchmark and retrieval -> matching -> PnP evaluation. Default evaluations can use altitude/attitude priors. | Compare the complete pipeline and geometry-only path separately. Report prior assumptions explicitly, including a no-prior recovery test; do not compare oracle-crop results to unrestricted search. |
| [Wang et al., July 2026](https://isprs-archives.copernicus.org/articles/XLIX-B1-2026/55/2026/) | SuperPoint/LightGlue with true orthophotos, DSM and spatial resection, evaluated at 100-250 m. Results depend on terrain and altitude. | Supports learned correspondence plus metric surface geometry. Benchmark as an alternative; it does not establish accuracy for our low-altitude X500 or justify replacing XFeat without measurements. |
| [SASGeo, July 2026](https://emilab.org/research/sasgeo) | Semantic structure, unknown-evidence handling and ambiguous-fix rejection, explicitly evaluated as a synthetic proof of concept. | Useful optional stale-map/appearance-change research. Do not treat its retrieval results as flight evidence or make semantic segmentation another mandatory runtime stage yet. |
| [NGPS, July 2026](https://arxiv.org/abs/2607.18936) | Reports asynchronous learned satellite corrections, VIO and inertial fusion, evaluated on five flights at 60-150 m using Jetson Orin NX. | Supports the multi-rate architecture, not Pi performance or calibrated uncertainty. Study scheduling and velocity-informed search. Do not adopt score-derived covariance as a qualified noise model. |
| [U2S-CVGL](https://github.com/UAV-AVL/U2S-CVGL) | Released scale-estimation/cropping code uses camera geometry and small-vehicle size priors; detector execution is not included. | Add scale/FOV mismatch to evaluation and bounded multi-scale search. Semantic size cues are optional research, not a required altitude source in fields, forests or scenes without vehicles. |

NGPS is a useful system-level reference, but its [public repository](https://github.com/snktshrma/ngps_flight)
explicitly expects a separate full VIO package. It also contains ROS 2,
ArduPilot and classical-feature paths that are outside our active architecture.
Compare the implementation and paper separately; do not import its deployment
scaffold or interpret the repository as a complete, independently reproduced
flight system. Its reported accuracy is author evidence, not our benchmark.

**Important dataset restriction:** AnyVisLoc explicitly restricts commercial
products, model deployment and contract research without written permission,
and restricts redistribution. Treat it as a permissions gate, including before
placing data on shared cloud storage. Its public download is not commercial
clearance. [Official dataset terms](https://github.com/UAV-AVL/Benchmark/blob/main/docs/LICENSE_DATASET.md).

MegaLoc remains a retrieval reference, not a pose estimator.
[Official repository](https://github.com/gmberton/MegaLoc).
RoMa/VGGT/MASt3R remain optional offline tools, not Pi requirements. Evaluate
individual teacher contributions before funding a combined distillation run.

## Recommended Runtime Structure

```text
Timestamped calibrated camera + qualified raw IMU
  +-- continuous local VIO ------------------------+
  |   local pose, velocity, biases, joint uncertainty|
  +-- bounded keyframe queue                       |
      learned retrieval -> candidate neighborhoods |
      map-conditioned temporal/location evidence   |
      XFeat/LighterGlue -> DSM/3D-backed geometry   |
      competing global hypotheses + rejection ----+
                                                  |
      correlation-aware map/local alignment <------+
        -> local health + global integrity + age
        -> shadow logs and ground telemetry
        -> separately qualified PX4 bridge, later
```

Keep local motion independent of expensive retrieval scheduling. Use bounded
queues, cancellation of obsolete work, bounded tile caches and explicit clock
domains. Never let a GPU/CPU map job block camera/IMU ingestion.

Optional barometer measurements may constrain relative height with an explicit
bias/drift model; they do not establish absolute terrain height. If absent, only
publish vertical results supported by qualified visual-inertial/map geometry.
GPS remains independent telemetry and a timestamp/lever-arm-qualified evaluation
reference. Healthy GPS flags are not proof against spoofing, and a GNSS-aided
PX4 local position must not be relabeled GNSS-free dead reckoning.

## Work Sequence And Exit Evidence

### Decisions To Resolve Before Connecting The Live Paths

These are engineering recommendations from the source/code comparison, not
newly demonstrated capabilities:

1. **One sensor owner, two delivery policies.** The finite sensor session now
   supplies ordered native inputs, shared immutable image tickets and bounded
   learned windows with fail-stop overrun handling. Next bind the live learned
   consumer and complete recording; qualify the policies on physical sensors.
2. **Measure, then retain.** Budget capture-to-result P99 plus clock uncertainty
   and scheduling margin. Retain the matching VIO state and its joint covariance
   for that interval, within a measured memory limit. Reject expired results;
   keeping old poses in a dictionary does not preserve their correlation with
   the current filter. Attach the new native-child resource probe for Pi
   measurements; default Python-only recording still excludes that process.
3. **Separate local health from global integrity.** Represent local tracking
   availability, global lock, last correction age and ambiguity separately.
   A functioning VIO frontend must not imply a current global map fix. During
   recovery, widen search beyond the predicted neighborhood so a wrong prior
   cannot permanently exclude the true location.
4. **Make scale and terrain explicit.** Search over bounded crop scales when
   altitude/scale is uncertain. Record DSM versus bare-earth DEM, vertical
   datum, surface uncertainty and camera footprint. A building feature must
   not silently use ground elevation. A homography is only a constrained
   planar-mode measurement, not a general terrain pose.
5. **Resolve inertial information reuse at the PX4 boundary.** Companion VIO
   consuming Pixhawk IMU and PX4's estimator share inertial information.
   Independent-noise assumptions need review; adding a covariance field alone
   does not resolve that dependence. Pin firmware, measurement selection,
   timing and innovation tests before any transport work is promoted.
6. **Train against one reproducible baseline first.** Start with actual
   MegaLoc/XFeat/LighterGlue outputs and held-out routes. Compare Bearing-UAV
   supervision as an ablation; add Video2BEV and dense teachers only after an
   independently measured benefit. Keep the qualified local KLT/VIO frontend
   distinct from the retired ORB/AKAZE global map-matching path. Learned global
   localization does not require every local tracking operation to be neural.

For a contract-oriented product, frozen geographic splits, map/model provenance,
negative examples and bounded false-fix behavior are release requirements, not
optional reporting. Public model availability is not evidence of commercial
rights or suitability for the X500 operating envelope.

| Order | Work | Required exit evidence |
| --- | --- | --- |
| 1 | Finish aircraft/camera preparation and capture qualification | Stable mount, frozen focus/intrinsics/extrinsics, measured time alignment, raw imagery + IMU + ULog and independent reference. |
| 2 | Replay the exact aircraft sensors locally | VIO initialization, drift, gaps, motion/blur failures and repeatability on recorded hardware data. |
| 3 | Establish RunPod public baselines under permitted licenses | Pinned official models, dataset permissions, geographic splits, complete versus geometry-only metrics and cost per experiment. No large teacher run first. |
| 4 | Evaluate the connected learned map path | Retrieval misses, wrong-map/no-match behavior, metric position/heading errors, covariance coverage and delayed-fix handling on unseen routes. |
| 5 | Improve and distill the student | An ablation showing each teacher's benefit; calibrated rejection; PyTorch/ONNX parity. Keep neighboring frames and overlapping geography out of test leakage. |
| 6 | Sustained concurrent Pi shadow run | Capture-to-result P50/P95/P99, RSS, dropped frames, CPU load, temperature/throttling, microSD writes and recovery behavior. |
| 7 | Separate PX4 integration milestone | Tested frame/time/covariance conversion, pinned firmware, prop-off packet/estimator evidence and reviewed loss/recovery behavior before constrained flight. |

Prioritize false global-fix rate, false-accept confidence bounds, time to first
fix, recovery time, worst drift between fixes, position/heading error and
uncertainty coverage. Separate known-start, cold-start and recovery. Stratify by
altitude, terrain, lighting, season, blur and map age. No numerical flight
acceptance threshold should be inferred from a paper or a synthetic unit test.

## Verification And Scope

Latest review reran the native streaming, joint PnP alignment, correlated-window
and PX4-boundary tests in the estimator environment: **77 passed**. The native
stream tests used the built OpenVINS executable. `git diff --check` also passed.
These are local regression checks, not new aircraft or Pi measurements.

Earlier focused native replay, joint covariance, evaluation, metric smoother,
canonical navigation and correlated-window suites passed **115 tests** in the
estimation environment. This includes the new batch solver's contract/Jacobian
tests, not real learned-map covariance calibration or end-to-end flight accuracy.
Earlier recorded TUM VI and model-export evidence was inspected, not recreated.

The original reassessment was documentation-only. Subsequent implementation
progress is identified in the sections above and linked workflow reports.
Retention testing uses local synthetic fixtures and actual research models;
it does not connect an aircraft, change PX4 parameters, provision cloud resources
or enable publication. Existing unrelated working-tree changes remain intact.
