# Joint PnP And VIO Alignment Replay

This research adapter connects the [conditional PnP uncertainty calculation](learned-pose-uncertainty.md)
to a single [correlated OpenVINS pose snapshot](vio-joint-pose-contract.md).
It does not enable live navigation, change VIO states or publish to PX4.

## Connected Path

```text
Accepted PnP attempts + explicit per-attempt input covariance
  -> recomputed PnP sensitivities and conditional pose covariance
  + full cross-frame input covariance
  + explicit VIO/input cross-covariance
  -> complete joint map-pose and VIO/map covariance
  -> single-snapshot GTSAM map/local alignment
  -> immutable research evidence, NO_GLOBAL_FIX
```

The caller supplies the uncertainty model. There is no default independence,
score-derived noise, automatic covariance calibration or hidden map-error floor.
Every PnP request is recalculated, not trusted from a cached result. Each fix
must use an exact retained clone timestamp; late fixes outside the snapshot are
rejected rather than moved to the current time.

## Input Contract

Request schema: `vision_nav_joint_pnp_alignment_request_v1`.

| Field | Content |
| --- | --- |
| `native_record` | Complete pinned OpenVINS record with a valid joint pose window |
| `config` | `WindowAlignmentConfig`: map/model/calibration/clock/uncertainty IDs, fixed `imu_T_camera`, and zero `map_common_left_covariance` |
| `fixes` | Ordered list of distinct `fix_id` and complete PnP uncertainty `request` objects |
| `joint_uncertainty.snapshot_sha256` | Canonical hash of the native record |
| `joint_uncertainty.pnp_request_sha256s` | Canonical hashes of all PnP requests in fix order |
| `joint_uncertainty.joint_measurement_covariance` | Joint covariance of the concatenated PnP input vectors |
| `joint_uncertainty.vio_input_cross_covariance` | Cross covariance of all VIO right-pose errors with those input vectors |
| `joint_uncertainty.cross_covariance_model` | Nonempty description of the supplied correlation model |

Exact additional fields inside `joint_uncertainty`:

```json
{
  "error_convention": "estimated_vio_right_pose_and_additive_pnp_inputs",
  "measurement_order": "concatenated_pnp_request_input_vectors",
  "map_error_ownership": "joint_measurement_covariance_only_no_extra_floor",
  "snapshot_policy": "replace_do_not_accumulate"
}
```

Each PnP input vector has `5N+4` elements, ordered as specified by the PnP tool.
Diagonal blocks of the joint covariance must match the bound per-attempt input
covariances. All map, model, calibration, clock and uncertainty IDs must match
the alignment config. Synthetic and unvalidated-real-model scopes cannot be
mixed within a request. The adapter accepts 1-16 fixes, at most the number of
retained clones, and at most 1,024 combined PnP input dimensions. The CLI bounds
the complete request to 32 MiB. No observations are silently discarded to fit.

## Correlation And Error Ownership

For each PnP result, `S_i` maps additive input errors into right-camera pose
errors. Assemble block-diagonal `S` in the exact request order. Then:

```text
Q_map = S C_inputs S^T
C_vio_map = C_vio_inputs S^T
```

The VIO covariance comes from the entire native snapshot, converted to its
right-pose tangent while retaining cross blocks. The adapter validates that
the full VIO/input covariance is positive semidefinite. The resulting pose
residual covariance must satisfy the alignment solver's positive-definite
requirement. Invalid models are rejected, not repaired with diagonal jitter.

Errors describe perturbations of the estimated VIO poses and input measurements.
At the true state, both VIO and map pose residuals are the negative of the
corresponding estimated-pose error to first order. The two negative signs cancel
in their cross-covariance. This does not authorize mixing another residual sign
convention, tangent frame or input ordering without an explicit transformation.

Shared map error is already part of `C_inputs`, including across-frame blocks.
The adapter therefore requires the separate alignment map floor to be exactly
zero. `WindowAlignmentConfig` now allows a positive-semidefinite additional
floor, including zero; this is not permission to set unknown measurement axes
to zero. The complete pose-residual model and output covariance remain subject
to their existing positive-definite checks.

Repeated calls replace the solve. They do not accumulate old priors or fixes,
feed map corrections into OpenVINS, or treat overlapping snapshots as independent.
If one PnP uncertainty calculation is withheld, the entire batch is withheld.
Inconsistent map/VIO batches also remain withheld by the downstream solver.
One fix alone cannot test relative map/VIO consistency; its alignment explicitly
reports `untestable_single_map_fix`, with no confidence or position authority.

### Smaller Correlated Solve

`CorrelatedWindowAligner.solve` now optimizes the map-anchor poses and newest
pose, eliminating unused pose residuals from this single snapshot. It first
validates the complete original native record and joint covariance, including
the blocks that will not appear in the optimizer. No native filter state,
measurement, cross-covariance or original timestamp is silently discarded.

Partition the residual into retained `r` and unused `u`, with joint covariance
`C`. Unused poses have no map factor and their only residual is
`Log(mean_i^-1 X_i)`. Minimizing the Gaussian objective over `u` gives:

```text
u* = C_ur C_rr^-1 r
min_u [r,u]^T C^-1 [r,u] = r^T C_rr^-1 r
```

The retained covariance is therefore the **principal submatrix `C_rr`**, not
the Schur-complement covariance that would incorrectly condition unused poses
to their nominal values. All retained pose/map cross blocks remain present.
The eliminated rotation residual must remain inside the principal SE(3) log
chart; an optimum at or beyond pi is withheld. This is Gaussian residual-chart
elimination in an existing research model, not a field-qualified posterior on
the full nonlinear manifold or persistent marginalization across snapshots.

The numerical path also changed. In the pinned GTSAM 4.2.2 environment, the
former covariance-to-information/marginal path produced an incorrect covariance
on an actual native window with joint condition number about 6.5e9. The pose
and zero-residual checks alone did not detect it. A separate finite-difference
single-fix propagation exposed the discrepancy. The solver now constructs a
checked square-root information factor from covariance Cholesky plus QR, then
propagates output covariance using the whitened Jacobian QR. It does not form
normal-equation inverses, clip eigenvalues or add diagonal jitter. Recompute
older affected research covariance reports; they are not validated references.

`nuisance_elimination` reports original/optimized dimensions, original retained
timestamps, eliminated count and log-chart diagnostics. `covariance_solver` is
`whitened_jacobian_qr` for graph solves. The full graph remains available to offline Python
comparisons with `marginalize_unused_poses=False`; both paths use the corrected
square-root numerics. The ordinary joint-PnP and isolated live worker use the
reduced path without a new operator setting. All freshness and uncertainty
requirements above remain unchanged, and PnP is still recalculated each time.

#### Direct Single-Fix Propagation

The ordinary reduced path now uses `solution_method=single_fix_closed_form`
when there is exactly one map fix. With fixed camera extrinsic `E`, retained
VIO anchor `X_a`, current VIO pose `X_t` and map camera measurement `Z`:

```text
map_T_imu(t) = Z E^-1 X_a^-1 X_t
local_T_nominal_map = X_a E Z^-1
```

There is no redundant constraint: a free map/local transform can satisfy the
one map observation while all native poses stay at their means. The joint
Gaussian optimum therefore has zero residual, regardless of correlations.
The implementation propagates the complete validated joint covariance using
analytic right-SE(3) composition Jacobians. It computes `(H chol(C)) (H chol(C))^T`
rather than cancelling large covariance terms directly. If anchor and current
are the same stochastic pose, their contribution cancels exactly; they are not
treated as independent draws. Map/VIO cross terms and the declared independent
common-map floor remain accounted for.

This is an exact mean shortcut for this model with first-order covariance, not
a new uncertainty model or tracking qualification. Metadata records
`covariance_solver=analytic_joint_se3_pushforward`, zero optimizer iterations,
and `consistency_test=untestable_single_map_fix`. A wrong single anchor cannot
be rejected through inter-fix consistency. Confidence remains unset and
publication remains disabled. `nuisance_elimination` retains its legacy
dimension labels for the equivalent reduced problem; they do not mean an
optimizer ran. All native poses/covariance blocks are validated, including
unused ones. Multiple fixes still use the joint nonlinear graph. Setting
`marginalize_unused_poses=False` retains the full graph as an offline reference.

The delayed coordinator hashes each privately owned, fully validated snapshot
once on ingestion. Prepared drafts carry that exact identity; the final
freshness check still rejects a different snapshot. The private record is never
mutated or exposed by public methods. This avoids repeated serialization under
the ingestion lock without substituting timestamp equality for content binding.

September 5 follow-up evidence, arm64 Mac:

- 230 estimator/native/lifecycle/PX4 tests passed. Single-fix tests compare the
  full graph and independent finite-difference propagation, including same-pose
  cancellation, nonidentity extrinsics, cross-correlations and invalid unused
  native blocks. A single wrong global anchor still has no consistency test.
- Thirty paired native-window benchmarks: corrected full graph P50 **17.88 ms**,
  direct single-fix P50 **11.19 ms** (1.60x). This includes input validation and
  hashes, not PnP uncertainty, IPC or evidence recording.
- Thirty paired worker transactions on the same original replay request:
  separate noise/align P50 **101.55 ms**, combined P50 **79.26 ms**, with exact
  returned noise/result equality. Report:
  `research/outputs/single-fix-joint-benchmark-20260905-r1.json`.
- A finite native stream still produced **zero new current-pose updates during
  20 frames offered at 20 Hz**. The strengthened observation counts only results
  checked during streaming whose pose time advances beyond the initial
  posterior; a pre-stream estimate cannot count. Neither a positive unit test
  nor a faster isolated solve demonstrates continuous availability.
- Native benchmark:
  `research/outputs/single-fix-native-benchmark-20260905-r1.json`.
  Streaming observation:
  `research/outputs/single-fix-native-20260905-r2/test_query_owned_before_slow_r0/streaming-observation.json`.

All map/noise evidence in these tests is synthetic and no Pi or aircraft is
exercised. The complete live path, including repeated ownership copies and
serialization, remains a latency/availability blocker. Do not weaken the
snapshot, deadline, covariance or publication gates to hide it.

Final expanded regression: **250 passed, one failed** in 54.71 s. The failure
was the 20 Hz native cadence portion of `test_live_query_anchors.py`: bounded
input overflow stopped the epoch after 104 submitted and 102 completed batches.
It was not a pose/covariance parity failure. Keep this as a failing load test;
do not increase queue capacity, drop raw IMU batches, slow the offered cadence
or turn the failure into a passing qualification check. The resulting report is
`research/outputs/single-fix-native-20260905-r4/test_query_owned_before_slow_r0/live/live_report.json`.
Its thread/anchor fields are a failure-time snapshot, not live process state;
post-test inspection found no remaining native/estimator/test processes.

The actual-model regression separately passed 126 tests (13 existing exporter/
geospatial warnings), and all 104 main checks passed. Neither establishes
sustained stream throughput. The final callback-mutation guard also passes:
in-process reference solvers cannot change their native request and then reuse
the cached original snapshot identity. Next investigate acquisition/monitor/
serialization contention and native batch service time as one coupled workload,
rather than optimizing only the already faster numerical solve.

#### Reproducible Benchmark

Supply a `vision_nav_correlated_alignment_request_v1` JSON containing `config`,
the complete `native_record`, and its `map_batch`:

```bash
PYTHONPATH=src python scripts/research/benchmark_window_alignment.py \
  --request /data/evidence/correlated-request.json \
  --output /data/evidence/window-benchmark.json --iterations 30
```

This compares corrected full and optimized solves in alternating order, excluding
two paired warmups. Every pair must agree on decisions, provenance, pose,
covariance and consistency residual. The report binds input/source hashes and
environment, records all samples and refuses to overwrite existing output.
`solution_methods` identifies graph versus direct single-fix evaluation.
It measures snapshot validation, provenance hashing and solving, not PnP,
IPC, complete message encoding or sensor acquisition. Short-run P95/P99 statistics
are descriptive, not qualified latency bounds.

September 5 local arm64 Mac measurements, 30 pairs:

| Input | Optimized poses | Full P50 | Reduced P50 | Speedup |
| --- | --- | --- | --- | --- |
| Actual native window, synthetic map/noise | 12 -> 2 | 16.25 ms | 14.50 ms | 1.12x |
| Synthetic correlated window, two map fixes | 32 -> 3 | 112.84 ms | 61.86 ms | 1.82x |

Full/reduced covariance maximum absolute differences were below 1.1e-14 in
these paired cases. The native case also matched independent single-fix
finite-difference covariance propagation. Reports are under Git-ignored
`research/outputs/window-reduction-native-benchmark-20260905-r1.json` and
`research/outputs/window-reduction-32-benchmark-20260905-r1.json`.

189 tests passed in the estimator/native/PX4 regression run. New cases cover
nonzero nonlinear residuals, common/map/VIO correlations, independent dense
Gaussian reference results, high-condition-number noise, omitted invalid blocks,
wrong maps, 32/36-pose bounds and the rotation-chart guard. Actual native VIO
and an isolated estimator were exercised with synthetic map data. This does
not prove real-flight accuracy, continuous-current availability or Pi timing.

The subsequent combined learned/live regression passed 126 tests in 102.94 s
with actual MegaLoc and XFeat/LighterGlue ONNX exports, native OpenVINS, and
the isolated estimator available. Thirteen existing Torch export/tracing and
geospatial deprecation warnings remain; no tests were skipped. Generated
artifacts are in `research/outputs/window-reduction-model-regression-20260905-r1/`.
That run checks integration with synthetic captures, not X500 flight accuracy.
All 104 main project checks also passed. No desktop code or native C++ source
changed; desktop builds and the native binary build were not rerun in this pass.

## Delayed Results

`vision_nav.estimation.delayed_alignment.DelayedPnPAlignment` now connects native
worker snapshots and asynchronous PnP results to the solver above. It is an
importable research coordinator used by the opt-in finite live learned session,
not the default Pi flight service. The caller must supply the sensor clock, release config and
actual joint uncertainty model; this component does not manufacture them.

The native worker attaches a unique `worker_run_id` to `status()` and
`take_latest()`. Construct one coordinator with that ID and a
`WindowAlignmentConfig` dictionary. A new worker run, calibration/map change,
sensor reset or coordinate-origin change requires a new coordinator epoch.

| Operation | Result and ownership |
| --- | --- |
| `update_snapshot(worker.take_latest())` | Validates/copies one complete joint posterior and replaces the previous snapshot. Retires queries whose exact camera clone disappeared. |
| `begin_query(timestamp_ns, frame_sha256)` | Issues an owned ticket only for an exact retained camera state. Dispatch times must increase; capacity rejection does not advance the watermark. |
| `complete_query(ticket, pnp_request)` | Binds a complete PnP request to the ticket's original timestamp, frame digest and configured release IDs. Arrival order may differ from capture order. |
| `prepare()` | Freezes the current snapshot plus all completed, retained queries in timestamp order. Returns a draft ID and owned request so the caller can construct explicit joint uncertainty. |
| `solve(draft_id, joint_uncertainty)` | Consumes that draft once and calls the existing solver outside the coordinator lock. No persistent graph prior or VIO feedback is added. |
| `invalidate(reason)` | Permanently retires the epoch and clears outstanding inputs after a worker/sensor fault. |
| `status()` | Compact counters, pending/ready counts and fault state; no cached global pose. |

PnP `context` additionally needs `frame_sha256`, matching the dispatched image
bytes or documented preprocessing input. A digest is an identity binding, not
proof that the caller extracted correspondences from that image. The producer
remains responsible for input ownership and calibrated transforms. Completion
reports `ready`, not geometric acceptance; `solve` recomputes PnP and joint-noise
checks before returning research alignment.

Bounds are 1..16 outstanding queries (default 8), one prepared draft, one
in-flight solve, 2 MiB per input snapshot/PnP request, and 32 MiB for the supplied
joint-noise object. The existing 1,024-dimensional combined PnP limit still
applies. Exceeding it with completed queries withholds the batch; evidence is
not silently truncated. These are research limits, not measured Pi budgets.

The default query lifetime is ten host-monotonic seconds, configurable up to
sixty. It includes waiting and solving, not just model execution. This is a
stall bound, **not** a flight freshness requirement, sensor-clock conversion,
or an extension of the native clone window. Native clone eviction can retire
a query much sooner. There is no second pose-history cache that pretends to
retain missing cross-covariance.

Late/duplicate results, frame/time/release mismatches, expired drafts and absent
cross-covariance are withheld with reasons. Invalid native records or a regressed
host clock invalidate the epoch. Start the coordinator after VIO initialization;
the owning service must call `invalidate` on worker loss or a sensor reset.
Merely having a prior snapshot is not a health check on the child process.

VIO can advance during a solve. If it does, a successful result is explicitly
`estimate_scope="historical_snapshot_only"`, with `snapshot_is_latest=false`
and the original nested `pose_timestamp_ns`. It is not propagated to the new
state or retimestamped. Epoch failure or query deadline expiry during a solve
withholds the outer result even if nested numerical diagnostics succeeded.
Every outcome remains `NO_GLOBAL_FIX`, uncalibrated, no position authority and
PX4 disabled. Consumers must honor the outer decision, not extract a pose from
withheld diagnostics.

### Refresh Against The Latest Snapshot

`DelayedPnPAlignment.refresh_current(uncertainty_provider,
max_snapshot_age_ns=..., max_attempts=2)` now re-solves retained original PnP
observations against the latest correlated VIO posterior. The provider receives
an owned alignment request and returns the exact `joint_uncertainty` described
above, including cross-covariance for every pose in that snapshot. There is no
default provider, assumed independence or reuse of an old covariance with a new
hash. This is a synchronous research API, not the default live navigation loop.

If VIO advances while the provider or solver runs, the coordinator prepares a
new request and calls the provider again. It does not propagate by multiplying
an old pose by an independent increment or by inserting the last solution as a
prior. The latest pose, including one without a map observation, is estimated
jointly with alignment and the map-anchor poses, while unused pose residuals
are eliminated with their uncertainty accounted for as described above. At most 1..4 attempts are allowed;
exhaustion withholds the current result instead of returning the old pose.

Success has `status="research_current_alignment"` and `current_estimate`, with
`current_scope="latest_snapshot_at_check_only"`. It requires that the final
snapshot hash still matches and that the estimated clone timestamp equals the
posterior timestamp. The original sensor timestamp, clock ID, IMU body frame
and right-SE(3) rotation-then-translation covariance convention remain explicit.
Receipt age is checked before and after work against the caller's explicit
limit (at most ten seconds). This is a host receipt-age check, NOT a sensor
capture-age conversion, real-time guarantee, child-liveness check or aircraft
publication qualification. A snapshot can become obsolete after return.

The uncertainty provider and numerical solve run outside the sensor lock; a
separate lock prevents overlapping refresh calls. Attempts are bounded, not
wall-clock execution: this method cannot terminate a hung provider/GTSAM call.
An owning service must isolate that work if a hard cancellation bound is needed
and invalidate the epoch on native/sensor failure. All outcomes retain
`position_authority=false`, `NO_GLOBAL_FIX` and disabled PX4 output. Withheld
outcomes have `current_estimate=null`, even if historical diagnostic solves
succeeded. No earlier successful result is used as a fallback.

Tests cover changed snapshots during work, stale receipt times, mismatched
noise models, absent current clones, epoch failure, duplicate refresh calls,
provider failure and retry exhaustion. An independent dense linear-Gaussian
reference checks both the conditional mean and covariance of a third pose with
no direct map fix, using nonzero cross-correlations. A native integration test
processes all 160 synthetic camera frames through OpenVINS and advances two
native updates during the refresh. Its PnP observations/noise are synthetic,
not extracted from those images or field-calibrated. The recorded artifact is:
`research/outputs/current-refresh-native-tests-20260905-r1/test_current_alignment_refresh0/current-refresh-result.json`.

Current-refresh verification on September 5, 2026: **143 tests passed** across
refresh, delayed/PnP/correlated alignment, VIO joint windows, actual native
streaming and the PX4 boundary. The main unit runner passed **104 checks**.
No Pi, aircraft, real cross-view accuracy or field covariance qualification was
performed in this pass.

Tests connect an actual native image/IMU snapshot to synthetic PnP map
observations, and exercise out-of-order completion, clone eviction, duplicate
jobs, bounded queues, owned copies, missing cross terms, native epoch failures
and concurrent sensor updates. Synthetic map points/noise are not real
cross-view matches or field covariance evidence. Live sensor ingress and a
learned process now exist separately. Still required are their qualified online
fusion binding, measured correlation/uncertainty, a Pi-sized retention policy
and continuous propagation beyond the retained snapshot without losing those
correlations. Current-snapshot refresh does not implement that propagation.

Verification on September 5, 2026: **140 tests passed** across delayed alignment,
native streaming, PnP alignment, correlated windows, PX4 boundaries, VIO joint
poses and Pi capture conversion. The real native executable was enabled for
those tests. The main unit runner also passed all **104 checks**, and
`git diff --check` passed. No new aircraft data or Pi performance was measured.

## Execute

Use the estimation environment with GTSAM and the installed project:

```bash
vision-nav-align-pnp-window --request request.json --output new-result.json
# Equivalent module invocation:
python -m vision_nav.estimation.pnp_window_alignment \
  --request request.json --output new-result.json
```

Exit 0 means a research alignment, not flight acceptance. Exit 2 means withheld.
Invalid contracts fail without a result. Existing outputs are never overwritten.
The result contains complete PnP diagnostics, the generated correlated map batch,
alignment diagnostics and provenance hashes. All outputs remain `NO_GLOBAL_FIX`,
`position_authority=false`, `covariance_calibrated=false` and PX4 disabled.

## Evidence And Limits

Synthetic tests check the connected numerical path, explicit nonzero VIO/input
correlation, full map cross blocks, map error counted exactly once, repeated
solve determinism, hash/order/identity/clock checks, invalid covariance rejection
and all-or-nothing withholding.

Recorded integration artifact:
`research/outputs/pnp-vio-alignment-20260905-r1/` contains the complete request and
result. The native snapshot is from the existing TUM VI room1 replay; the source
state-file checksum was verified. Two synthetic PnP observations use the first
and last of 11 retained clones. The solve reached research alignment, with
residual chi-square approximately `1.62e-7` and no position authority. This is
expected for deliberately consistent synthetic observations, not an accuracy
measurement. The map observations are not real TUM cross-view matches, and their
explicit zero VIO/input covariance is a synthetic conditional assumption.

Still required: measured noise/correlation models, timing and mount uncertainty,
cross-frame correspondence identity, map ambiguity, empirical covariance
coverage, longer delayed-fix retention and continuous aircraft/Pi replay. This
adapter does not replace those requirements with a Gaussian assumption.
