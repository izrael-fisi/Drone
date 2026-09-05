# Technical Project Handoff

**Snapshot date:** September 5, 2026.
**Audience:** the next LLM, navigation engineer, training engineer or integrator.
**Repository:** `https://github.com/izrael-fisi/Drone`.
**Release status:** research/development; no flight-navigation release approved.

This is the canonical transfer summary of implemented work and remaining work.
It is not a replacement for the detailed contracts or evidence. Exact commands
and verification outcomes are in [handoff verification](handoff-verification-2026-09-05.md).
Paths in this document are relative to the repository, not the previous Mac.

## 1. Purpose And Current Priority

Build low-cost GNSS-denied drone localization from downward camera imagery,
synchronized onboard inertial measurements and preloaded georeferenced maps.
Healthy GPS can remain a separate primary telemetry/reference source. The visual
system must work without GPS, online maps or cloud inference during operation.
Its intended output is metric pose, heading, uncertainty, health and explicit
no-fix decisions, eventually usable by a separately qualified PX4 interface.

There are two product sections: the drone runtime and the ground-control desktop
app. Training, offline map production and test tools support those sections;
they are not additional in-flight services. The latest user priority is:

1. Continue the user's airframe flight testing independently of learned control.
2. Stabilize the installed camera and verify focus/exposure/calibration.
3. Collect synchronized, auditable footage and sensor evidence.
4. Develop, replay, train and benchmark the learned navigation system.
5. Use RunPod for GPU training after inputs, credentials and budget are ready.

Desktop/browser expansion is deferred. Existing UI work is preserved, including
newer work merged from GitHub during handoff. No new LLM/MCP control layer,
ROS 2/Gazebo scaffold or mandatory cloud link should be introduced.

## 2. Hardware And Deployment Assumptions

| Component | Current target / requirement | Still needs confirmation |
| --- | --- | --- |
| Aircraft | Holybro X500 V2 with Pixhawk 6C-class PX4 controller | Installed model, firmware hash, sensor IDs and parameter snapshot; older conversation references to 6X are not evidence of installed hardware |
| Companion | Raspberry Pi 5, 16 GB target | Actual OS, architecture, power/cooling, CPU performance and device access |
| Storage | 256 GB microSD for current operation | Free space, write latency, endurance and capture retention; USB SSD remains optional |
| Camera | Downward RGB/global-shutter target, manual focus/aperture | Actual sensor, lens, resolution, exposure mode, mount rigidity and measured calibration |
| Inertial data | Ordered raw IMU with measured clock relationship to exposure | Noise, bias, sample rate/gaps, sender identity and extrinsics; ATTITUDE alone is insufficient |
| GPS | Independent reference/healthy telemetry | Receiver accuracy and timestamps; GPS quality flags do not prove absence of spoofing |
| Barometer | Optional legacy telemetry/altitude aid remains in the repo | Not a qualified factor in the current native/learned estimator; no absolute terrain-height claim |
| Development | Mac M1, Windows RTX workstation, Pi Linux | Cross-platform native/compiler/package parity, not implied by Mac tests |
| Training | RunPod GPU host selected as intended platform | Authentication, paid allocation, CUDA execution and full training remain unverified in this work |

Camera motion caused by a gimbal or software stabilization changes geometry.
Use a rigid, calibrated transform or explicitly measured time-varying transforms
and image warps. A sharper-looking stabilized video is not automatically valid
VIO input. Retain original exposure times, crop/resize transforms and raw data.

## 3. Requirements And Acceptance

| ID | Requirement | Acceptance evidence |
| --- | --- | --- |
| NAV-01 | Flight localization is offline and learned; no classical global fallback | Disconnect network/GPS inference input; replay complete release with explicit no-match behavior |
| NAV-02 | Continuous local motion does not wait for learned inference | Ordered sensor processing under slow model, queue/resource bounds and loss tests |
| NAV-03 | Global fixes are geometrically justified, not retrieval scores | Calibrated 2D-3D or explicitly constrained planar geometry, spatial support, residuals and ambiguity tests |
| NAV-04 | Delayed fixes retain observation time and update a genuinely current state | Positive advancing-current output under continuous input, with delay/outage/correlation tests |
| NAV-05 | Covariance has measured physical meaning | Held-out position/heading error and coverage, joint error model, no double-counted VIO/map/image information |
| NAV-06 | Wrong map, stale map, blur and aliasing fail closed | False global-fix rate, time-to-first-fix, recovery time, no-fix correctness across geographic holdouts |
| DATA-01 | Every observation/release is attributable | Image/sensor identities, clocks, calibration, CRS/origin, model/source/weight hashes and dataset membership |
| DATA-02 | Training/evaluation cannot leak route or GPS labels into denied inference | Geographic and session-disjoint splits; GPS restricted to reference/label paths |
| PERF-01 | Runtime fits Pi CPU, memory, thermal and storage budgets | Combined camera/IMU/VIO/model/solver/logging P50/P95/P99 and sustained duration; budgets agreed before promotion |
| SAFE-01 | Learned research never commands aircraft | Shadow flag, publication veto and no ODOMETRY/actuator/parameter writes from learned sessions |
| SAFE-02 | Aircraft integration is an independent qualification | Pinned firmware, wire frames/covariance/clocks/reset/loss, prop-off evidence and explicit flight approval |
| OPS-01 | Status distinguishes configured, observed, stale and unavailable | Ground app displays live evidence and provenance, not merely saved profile values |
| OPS-02 | Map/model/calibration activation is immutable and compatible | Partial/mismatched release rejection, checksums, atomic activation and rollback evidence |
| SEC-01 | Credentials and regulated data are not shipped in Git or public services | Secret review, transport/auth audit, approved storage/data classification and license review |

Numeric mission accuracy, allowed false-fix probability, time-to-fix, operating
altitude/speed, map GSD/age and sustained runtime budgets are **not yet agreed
product requirements**. Existing test thresholds are research defaults, not a
contractual guarantee. Define an operating envelope and measurable acceptance
criteria before optimizing for contract claims.

## 4. Actual Architecture Versus Target

```text
Offline:
  calibrated captures + independent labels + georeferenced surface/imagery
    -> reference map / dataset releases / splits
    -> research training and evaluation -> ONNX export/parity
    -> model-bound tile geometry, retrieval index, learned feature cache

Current opt-in live research:
  one camera owner + one MAVLink/raw-IMU owner
    -> ordered native OpenVINS stream -> correlated local pose windows
    -> immutable camera windows -> isolated learned model process
       -> MegaLoc candidates -> map-conditioned student
       -> optional XFeat/LighterGlue + surface-backed PnP
    -> optional retained-query / conditional alignment research observer
    -> bounded JSON evidence + health, no aircraft publication

Still to qualify:
  delayed map corrections + continuous correlated forward propagation
    -> persistent ambiguity/recovery policy -> current global estimate
    -> calibrated release gates -> separately verified PX4 transport
```

The simpler `neural/shadow_runtime.py` service path and the finite
`neural/live_sensor_inference.py` native-VIO experiment coexist. The native
experiment has **not** replaced the installed systemd service. Do not assume
running `run_vision_nav_shadow.sh` enables the native estimator and query owner.

### Implementation Matrix

| Area | Implemented | Not established |
| --- | --- | --- |
| Sensor capture | Calibration helpers, camera quality/metadata, TIMESYNC audit, raw-IMU recording, bounded ownership, durable replay export | Installed X500 physical timing/noise and long-duration capture under vibration |
| Map/data releases | Georeferenced reference-map import, COG/STAC-related metadata, tiling, surface masks, dataset/split/label tooling and checksum validation | Approved complete field map/model/calibration release; arbitrary maps are not automatically metrically usable |
| Retrieval | Real MegaLoc export/execution, model-bound bounded NumPy cosine search, up to four research candidates | Pi large-area throughput, FAISS production path and separate tracking/recovery cadence |
| Local learned matching | XFeat and LighterGlue export/parity and actual ONNX execution, correspondence filtering and robust geometry | Held-out aerial cross-view precision, Pi runtime envelope and calibrated matcher confidence |
| Student | Project-owned CNN encoders, spatial cross-attention, weighted temporal pooling, location/no-match and heading heads | Faithful Video2BEV/Bearing-UAV reproduction or useful field-trained/distilled accuracy |
| VIO | Pinned ROS-free OpenVINS native frontend, joint pose windows, bounded anchors, asynchronous stream wrapper | Pi/hardware qualification and commercial distribution approval |
| Estimation | Conditional PnP uncertainty, correlated window alignment, delayed binding, worker isolation, current-snapshot refresh, one-fix analytic propagation | Reliable advancing-current global output, measured joint noise and qualified long-gap behavior |
| Ambiguity | Per-frame candidate association and rejection; hypothesis scaffolding | Persistent multi-location hypothesis management and global recovery qualification |
| Training | Dataset conversion, reference-model evaluation, Bearing-UAV optimizer/save/reload/resume, teacher point targets, student export, Video2BEV workspace/model smoke | Reproduction metrics, full distributions distilled, proprietary holdout benefit and GPU campaign |
| PX4 | Read telemetry, offline frame/covariance/publication checks | Learned aircraft transport; `publish_mavsdk` intentionally raises `NotImplementedError` |
| Desktop | Map-first shell, map/mission/device panes, companion API, cloud account/maps client, older detailed pages, v1/v2 and partial v3 status adaptation | Complete modern learned-release workflow on routed panes, hardware end-to-end verification and full evidence review |

Model names identify adapters or research direction, not trained capabilities.
Video2BEV uses offline reconstructed/rendered BEV in its upstream workflow; it
is not the deployed temporal network. Bearing-UAV now has official code and
checkpoint adapters: older notes saying its code is unavailable are historical.
RoMa, VGGT, MASt3R and semantic teachers remain optional research/map tools.
Do not make every paper a prerequisite for the first measured operational baseline.

## 5. Contracts That Must Survive Further Work

### Frames, Time And Geometry

- World map coordinates are ENU metres with explicit CRS and local origin.
  Preserve imagery pixel geometry and surface vertical datum/provenance.
- Intrinsics and camera-to-body extrinsics must be measured at the actual image
  configuration. Example YAML is not a measured calibration.
- Record exposure sensor time, conversion to native/host clock, uncertainty,
  original query time, posterior time and result availability separately.
- Native camera/IMU delay is applied once. Do not also apply it to learned
  context without a defined transformation.
- Local monotonic, PX4 boot and UTC clocks are not interchangeable. A field
  named `clock_id` does not establish synchronization.
- A homography or map-pixel lookup is not unrestricted metric six-DoF pose.
  PnP needs supported map 3D points, calibration and geometry validation.
- DSM height and DTM ground height are different. DTM use requires reviewed,
  checksum-bound ground support; missing building labels do not prove ground.
- The bounded PnP path selects at most 24 spatially distributed correspondences
  after full acceptance checks. This is a compute policy, not an accuracy claim.

### Correlation And Delayed Ownership

- Native posterior poses are correlated. Do not difference them into independent
  motion factors or reuse repeated map/image evidence as independent fixes.
- Native v1 pose windows are bounded; v2 adds normal/passive membership. The
  automatic query-owner path has four slots and at most two accepted anchors.
  Original query and accepted-retention horizons remain explicit.
- Retain the camera query pose before inference; require native acknowledgement
  and subsequent membership before using it for delayed alignment.
- Conditional uncertainty requires an explicit joint pixel/map/intrinsic/VIO
  error provider. Test fixtures do not supply a flight-qualified noise model.
- The one-fix shortcut propagates `Y = Z E^-1 X_anchor^-1 X_current` with joint
  SE(3) covariance. It is not a multi-fix consistency test. Multiple fixes retain
  the graph path and square-root/QR covariance computation.
- `refresh_current` checks that the solved snapshot is still latest. Tests show
  this may continually withhold under advancing input. Do not relabel historical
  results, drop cross-covariance or increase freshness limits to hide the issue.

### Publication And Failure

- `Px4VisionBridge` is the future sole qualified learned-publication boundary.
  Passing its offline eligibility checks still does not send a packet.
- Its explicit FRD body, ENU world, `xyzw` rotation and world-left perturbation
  covariance contract differs from native right-SE(3) research covariance.
  Derive conversions; never relabel matrices.
- The internal NED record is not a MAVLink payload. A future pinned-firmware
  wire adapter must separately address LOCAL_FRD expectations, covariance packing,
  timestamps, velocity, resets and loss handling. See [PX4 contract](px4-integration.md).
- Sensor failure, incompatible release, stale observation, ambiguity, solver
  timeout and invalid covariance produce explicit withholding, not low-noise fixes.
- Capture can send bounded TIMESYNC requests for clock evidence. This is not
  permission to arm, change parameters, change stream rates or publish vision.
- Historical MAVLink/log helpers and companion-launched third-party GCS tools
  are separate capabilities. Their presence must not bypass learned shadow gates.

## 6. Work Completed In The Research Program

The source checkpoint `e4c2165c` preserves accumulated work from this task:
399 changed files, including previously untracked source/tests/docs. It is a
development checkpoint, not a release tag. Work includes:

1. Learned profile migration and versioned map/model/dataset interfaces; legacy
   classical tools retained for reproducible migration/replay only.
2. Camera and MAVLink bench/capture checks, intrinsic/extrinsic identities,
   clock auditing, reference-map bootstrapping and field-data preparation.
3. Actual MegaLoc, XFeat/LighterGlue and map-conditioned student export/runtime
   paths; negative cases, geometry, surface-mask and source-binding checks.
4. Bearing-UAV reference evaluation/training/resume and point-target workflows;
   Video2BEV portable workspace/data/model smoke; RunPod deployment instructions
   and a hash-pinned Linux reference dependency lock.
5. ROS-free native local VIO, bounded live sensor buffering, joint pose windows,
   passive/query anchors and isolated learned inference without duplicate owners.
6. Conditional correlated alignment, geometry/noise provenance, asynchronous
   research observation, bounded worker protocols and delayed current refresh.
7. Numerical fixes replacing unstable covariance calculations with whitening/QR;
   exact single-fix propagation preserving the joint correlations.
8. Native handoff optimization: copy outside the acquisition lock, retain actual
   receipt time, bound diagnostic timing samples, reject invalidated snapshots.
9. Orderly-close race handling: refuse unsent anchors atomically, distinguish
   real transport faults and wait for confirmed epoch exit before owned cleanup.
10. Latest CPU work: bounded float-chunk JSON validation with unchanged hashes,
    default one-thread detected BLAS/OpenMP pools in the estimator child, and
    removal of one redundant internal draft copy. Public ownership remains intact.
11. Subsequent worker-local PnP reuse: at most 16 successful assessments / 2 MiB
    of serialized results, keyed by the complete original request. Every new
    snapshot still requires joint uncertainty and alignment validation. Cache
    reuse does not create a new measurement or qualify a current pose.
12. Per-operation diagnostics: parent encode/transfer/decode/verification timing,
    child decode/verification/operation timing, response sizes, cache counters
    and per-attempt observer records. Child timings overlap parent waiting and
    must not be added to parent totals or called sensor-to-pose latency.
13. Owned transaction reuse: one native record validation/hash per PnP alignment
    call, reused per-fix digests and private coordinator request identities.
    External inputs still require full validation, and each new native snapshot
    still requires its own correlated uncertainty model. This reduces measured
    overhead but the advancing-stream trial still produced zero current updates.
14. Private compact estimator transport: MessagePack with bounded JSON-shaped
    values and exact float64 round trips, explicit `json_v1` comparison mode,
    codec handshake and malformed-packet rejection. Canonical JSON identities
    and stored evidence do not change. The `estimation` extra now includes
    msgpack; standalone capture without opt-in alignment does not require it.
    One initial compact trial produced a single advancing current update, but
    repeated trials did not sustain it. This does not qualify live navigation.
15. Current-path scheduling and evidence ownership: exact canonical snapshot
    bytes are reused for request identities without changing hashes; fresh
    native snapshots wake the single solver caller without an extra idle wait.
    Compact status is available independently of the full replay write and
    explicitly labels its evidence `pending` or `written`. Any source/time/fix
    mismatch or journal failure still withholds it. Actual status sampling at
    20 Hz input remains zero in the final check, despite passing lifecycle and
    numerical regression tests; this is not sustained-current qualification.
16. Child transaction identity reuse: the worker encodes each owned request
    field once and reuses it for unbound/native/noise-bound hashes. Public
    callers cannot supply cached fields. Three extended 60-frame native streams
    produced 11, 6 and 4 distinct current poses visible to the status reader;
    every streaming success retained exact numerical replay. Large gaps and
    low sample availability remain, so the current-state requirement is still
    unqualified. Full measured VIO/map uncertainty, Pi load and flight validation
    are separate missing evidence, not established by these synthetic runs.

Items 11-12 and their regression tests are included in the final transfer
supplement after the original `ce6b3088` documentation/merge checkpoint.
No new training, Pi measurement, aircraft connection or PX4 publication was
performed for that supplement. The modest cached-assessment benchmark does not
resolve the zero-advancing-current-update finding. The next estimator task should
use the diagnostics to investigate that limitation, not infer it is solved.
Item 13 follows `70d3c820`; its evidence and unchanged open requirement are in
[owned transaction reuse](research-alignment-process.md#owned-transaction-reuse).
Item 14 follows `2d84be05`; see [codec contract and observations](alignment-serialization.md#compact-worker-transport).

The handoff also integrates GitHub main through `ee867d56`, preserving 22 newer
commits covering map-first navigation, companion/GCS tools, map providers and
Proxigo account/map/usage integration. Merge decisions retain the learned profile
instead of resurrecting an obsolete classical checkbox. Companion API entrypoints
are retained; removed classical flight-loop entrypoints are not reinstated.

## 7. Desktop And Companion Integration Reality

The **current routed operator UI** is primarily `desktop-app/src/App.tsx`,
`components/Layout.tsx` and `pages/Dashboard.tsx`. Many former routes redirect
to `/home` and open a dock. `/maps`, `/mission-planner`, `/vision-pipeline`,
`/system-status` and `/flight-review` no longer necessarily render their older
same-named page files. Inspect route and import usage before editing them.

- `lib/shellStore.ts` owns dock state; `lib/operatorRuntimeAdapter.ts` derives
  map/device views from saved state. Saved lifecycle fields are not Pi attestations.
- `lib/pipelineConfig.ts` is the learned configuration storage helper. Device
  editors must not write independent ORB/classical settings. The old editable
  VisionPipeline page exists but the routed camera dock still lacks the full
  release editor. Integrating it is backlog, not completed by a successful build.
- Tauri `commands/drone.rs` packages a learned-map **candidate**. Its declared
  future index paths and `ortho/map.png` do not mean FAISS, XFeat indexes or a COG
  were generated. `awaiting_learned_map_compiler` must block readiness/activation.
- `commands/telemetry.rs` accepts existing v1/v2 position packets and adapts
  portions of `runtime_status_v3`. This is not complete current-estimator output.
- `src/vision_nav/companion_api.py` provides device/status/probe/GCS controls;
  `scripts/pi/run_companion_api.sh` and user units install/run it separately.
  Default service control is disabled. Do not expose this prototype HTTP service
  publicly or assume it has production authentication/TLS/authorization.
- Companion probes may independently open the MAVLink endpoint. The research
  sensor runner requires exclusive ownership: coordinate probes/status/GCS
  processes or use a qualified router before simultaneous access.
- Proxigo cloud client and account/org-map/usage flows are documented in
  `desktop-app/PROXIGO.md`. This handoff does not verify the external service.
  Profile token/SSH-secret storage needs review and must not enter support exports.
- `docs/cloud/` is infrastructure/compliance planning. Its older blanket claim
  that no cloud implementation exists is superseded for the desktop client,
  not proof that the planned backend/GovCloud controls were implemented.

Desktop integration remains secondary to capture and estimator qualification.
Cloud map management must not become a runtime flight dependency.

## 8. Known Gaps And Prioritized Work

### P0: Capture And Current-State Correctness

**Installed hardware dataset.** Produce one checksummed X500 session containing
raw imagery, raw IMU, attitude, clock evidence, measured calibration, map/surface
identity, PX4 logs and independently qualified reference. Record focus/aperture,
exposure, vibration, mounting, altitude/speed and lighting. Confirm missing/stale
GPS reference prevents training-label claims without stopping independent denied
inference. Acceptance: a second machine reproduces capture validation and replay.

**Advancing-current estimator.** Design delayed correction plus correlated
forward propagation, or an estimator-owned current-state update. Acceptance:
positive current global updates while new VIO frames continue, bounded delays,
honest covariance growth and safe reset/outage behavior. The most recent short
20 Hz trials still had zero advancing current updates. Faster JSON does not
solve this architectural availability condition.

**Measured uncertainty.** Replace synthetic provider assumptions with calibrated
pixel, surface, timing, intrinsic/extrinsic and inertial uncertainty, including
shared dependencies. Acceptance: held-out coverage and sensitivity/innovation
tests; no unreviewed independent-noise simplification.

### P1: Model Quality, Recovery And Pi Budget

**Training baseline before model expansion.** Fix geographically separated
splits; evaluate the current learned matcher/retriever, one normalized variant,
and one supervised/teacher-assisted student. Include wrong-map/no-match examples.
The synthetic distillation smoke's zero positive-cell accuracy is execution
evidence only. Measure real location/heading error, false fixes and recovery.

**Tracking/recovery policy.** Add bounded local tracking with explicit escape to
wider retrieval, multi-location persistence and tests for wrong prior, repetitive
terrain, map edge, stale map and long gap. Per-frame grouping is not sufficient.

**Pi system qualification.** Run sensors, native VIO, model/solver workers and
microSD recording together; measure latency tails, drops, RSS, load, temperature,
throttling and storage throughput over representative duration. AI HAT support
is optional and must pass model parity and thermal/system benchmarks first.

**RunPod execution.** Reauthenticate on the new host, agree cost/time/storage
limits, provision explicitly, run environment and one-epoch CUDA checks, then
scale. Keep source/dataset/checkpoint identity, optimizer/RNG recovery, evaluation
and backups. No paid resources or successful GPU training were created by this
research pass. See [RunPod handoff](runpod-bearinguav-training.md).

### P2: Promotion, Operations And Security

**PX4 qualification.** Implement the wire adapter only after estimator acceptance;
verify frames, covariance, clock/reset behavior and PX4 receiver evidence prop-off.
Aircraft arming, actuator control and safety actions remain PX4/operator-owned.

**Live operator integration.** Connect the routed map shell to actual model/map/
calibration release health, trustworthy active-map status, estimator withholding
reasons and replay metrics. Do not mark a candidate bundle active because a file
copy or metadata edit succeeded. Complete modern workflow routing before deleting
old pages, then add operator journey tests.

**Security and reproducibility.** Resolve the dependency audit, review credential
storage, companion network/command boundaries and model/data licensing. Add
qualified Linux/Pi/Windows CI and immutable environment records. A hash pin is
not a security approval, and passing builds do not establish compliance.

## 9. Source Ownership And Duplicate Paths

| Path | Responsibility / caution |
| --- | --- |
| `src/vision_nav/neural/` | Learned adapters, releases, map compilation, student/training, replay, shadow/live model paths |
| `src/vision_nav/vio/`, `research/native/openvins/` | Native sensor/frontend boundary, replay/live ownership, joint windows and anchors |
| `src/vision_nav/estimation/` | Conditional geometry uncertainty, metric replay, correlated/delayed alignment, query ownership and worker |
| `src/vision_nav/{types.py,contracts.py,runtime/,mapping/,px4/}` | Shared/future runtime contracts and qualification boundary; not all adapters are bound |
| `camera*`, `capture_timing.py`, `inertial_capture.py`, `training_capture.py` | Physical calibration and evidence acquisition |
| Root `terrain_*`, `match_*`, older feature modules | Frozen prototype/replay/migration; do not route back into active learned operation |
| `mavlink_bridge.py`, `run_status_bridge.py`, `companion_api.py` | Telemetry/legacy helpers/operator services; inspect ownership and write behavior individually |
| `scripts/pi/`, `config/`, `systemd/` | Host setup/capture/service examples, not auto-approved hardware values |
| `configs/default.yaml` | Canonical architecture/demo configuration, distinct from hardware `config/` |
| `research/`, `scripts/research/`, `docker/research/` | Research inputs/locks/entrypoints; ignored vendor and outputs are external artifacts |
| `desktop-app/` | React/Tauri shell; current route ownership described above |
| `tests/run_unit_tests.py` | Main explicit check list; does not discover every pytest test |
| `tests/test_*.py`, `tests/fixtures/` | Focused unit/native/model/replay tests; some require optional dependencies/artifacts |

Intentional coexistence includes old versus modern bundle paths, generic shadow
service versus finite native research, pose-only smoother versus native VIO,
and older GRU experiments versus the current cross-attention student. These are
not interchangeable implementations. Consolidate only after preserving replay
compatibility and proving callers. Retain historical assessments as history;
do not quote their older maturity claims as current status.

## 10. Transfer, Security And Licensing

Git transfers code, fixtures, documentation, dependency locks and examples.
It does not transfer `research/vendor/`, `research/outputs/`, learned weights,
map imagery, datasets, flight/camera logs, native binaries, virtual environments,
cloud resources, desktop profiles or SSH/API credentials. See the separate
[artifact inventory](handoff-verification-2026-09-05.md#artifacts-not-in-git).

OpenVINS and its linked adapter have GPL obligations; OrthoTrack research code
has non-commercial restrictions noted in the assessment. Review each upstream,
checkpoint, teacher-derived label and map/dataset right before redistribution or
contract delivery. There is no blanket commercial-license clearance.

Never paste credentials from historical chat into code or handoff files. Rotate
shared/default passwords before networked use. Keep regulated mission data out
of unapproved commercial services; planning documents are not certifications.

## 11. Next LLM First-Session Checklist

1. Read this file, verification, AGENTS and the four core contracts; inspect Git
   status and history. Do not reset user changes or assume a historical branch.
2. Inventory local artifacts and measured hardware evidence. Request only what
   is missing; do not substitute synthetic tests for actual footage/calibration.
3. Reproduce main tests and the relevant native/model suites. Report skips,
   numerical environment differences and withheld outputs explicitly.
4. Freeze source while hash-bound worker tests run. Source changes during a
   running test can intentionally invalidate the release/worker handshake.
5. Choose one P0 task with acceptance criteria. Keep docs, source and evidence
   aligned; do not repeatedly add models or UI while current-state output is absent.
6. Preserve shadow-only operation and original timestamps. No aircraft control,
   automatic parameter changes or paid deployment without explicit authorization.
7. Before committing, review generated files/secrets and run the required desktop
   build and Rust check. Push without rewriting unrelated GitHub history.
