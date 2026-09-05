# Continuous Research Alignment

The finite learned sensor session can now update conditional map-aligned poses
when native VIO advances, **without requiring another learned frame**. This is
an opt-in extension of [live research alignment](live-research-alignment.md).
It does not enable aircraft control or replace the default Pi service.

## What Advances

The background worker rebuilds the existing joint estimator from retained,
original map-fix evidence and the latest complete native VIO posterior. Each
solve obtains a new explicitly supplied cross-observation/VIO uncertainty model.
It never treats the previous alignment result as an independent prior, adds
the same map fix twice, or assigns a newer timestamp to an old pose.

This advances estimates **within the retained VIO clone window**. When a map
anchor is marginalized or its query deadline expires, that evidence is retired.
If no usable map evidence remains, output is withheld. There is no propagation
past that boundary, IMU-rate extrapolation, long-gap global-state carry-over,
qualified covariance model or flight `NavEstimate` in this implementation.
An additional [query-owner option](learned-query-anchors.md) now retains exact
poses beyond ordinary clone marginalization. It separates the inference deadline
from accepted-evidence sensor age while retaining the same correlation contract.
That bounded retention is not indefinite global-state propagation.

Successful output remains `NO_GLOBAL_FIX`, `position_authority=false`,
`covariance_calibrated=false`, and `px4_external_vision=disabled`. A research
pose is current only at its final snapshot check; it can be superseded immediately
by another native posterior. Do not interpret the scope as a flight freshness
guarantee or a sensor synchronization qualification.

## Ownership

- Camera and MAVLink acquisition retain their existing single owners.
- The original snapshot monitor remains the only consumer of native
  `take_latest()`. It is not blocked by noise or alignment computation.
- Learned inference submits verified binding drafts to a one-slot pending
  queue. A newer draft replaces an older waiting draft, with both identities
  recorded in the learned trace. An in-flight draft is not replaced.
- One background thread owns all noise/solver requests to the existing
  isolated estimator process. There is no concurrent use of that process and
  no unbounded job queue. Direct in-process noise callbacks are not permitted
  in continuous mode.
- The estimator preloads GTSAM and the SciPy consistency-gate dependency during
  its startup deadline, before opening sensors. This reduces cold-import work
  on the first fix; it is not a measured Pi latency guarantee.

New map evidence wakes the worker. Without new map evidence, it checks for
changed VIO snapshots, stale receipt times or retired evidence at the configured
interval. An unchanged, still-valid snapshot is not repeatedly solved or counted
as new information. A newly ingested posterior now wakes the worker without an
extra full idle interval after a completed solve. Snapshot-triggered starts
remain limited by the configured start-to-start interval; there is still only
one solver caller, one latest native snapshot and one pending map draft. New map
evidence and final drain retain their existing immediate wake behavior. This is
not a guaranteed output frequency. Slow solves, new posteriors during solving,
and retry/deadline limits can all produce explicit withheld results.

## Enable For A Finite Bench Session

Use the existing fully configured `vision-nav-live-learned-bench` command and
add these options:

```text
--alignment-config /absolute/path/to/alignment.json
--alignment-noise-provider /absolute/path/to/research_noise.py
--alignment-noise-config /absolute/path/to/research_noise_config.json
--alignment-refresh-interval-s 0.2
```

The noise-config option is optional. The other three are required for this mode.
Keep `--propellers-removed` and the existing camera, map, calibration, native
binary and model-release arguments. There is no supplied field-qualified noise
provider. **Do not use the synthetic test provider on an aircraft.** Without
the refresh option, existing synchronous/audit behavior remains available.

Python callers pass `continuous_refresh_interval_s=0.2` and an owned
`ProcessAlignmentEngine` to `LiveAlignmentObserver`. The session runner creates
the dedicated evidence file and drains the final pending work before closing.
Direct callers must supply a new `evidence_path` to `start`, call `finish` after
submitting the last draft, and always call `stop`. Finish is bounded; interrupted,
timed-out or failed drains are reported incomplete. Stop cancels pending work
and stops the owned process and both observer threads.

## Evidence And Status

The normal learned `trace.jsonl` contains submission results, including
`draft_sha256`, superseded draft ID and original map-evidence sensor timestamp.
Its alignment latency measures submission only in continuous mode, as declared
by `research_alignment_latency_scope` in the summary.

`alignment-trace.jsonl` independently records bindings, conditional refreshes,
withheld decisions, complete numerical replay inputs and processing latency.
`summary.json` binds that file with `alignment_trace_sha256`. Events and the
whole alignment trace are limited to 4 MiB and 256 MiB respectively, separately
from the learned trace. Exceeding a budget fails the observer; data is not
silently truncated. Recording throughput still needs microSD qualification.

Useful event fields:

- `trigger`: new map evidence or a VIO snapshot refresh.
- `map_evidence_timestamp_ns`: latest original fix used, not its arrival time.
- `map_evidence_age_at_pose_ns`: pose sensor time minus that original fix time.
- `position_source`: `research_map_correction`, `research_vio_between_fixes`,
  or `no_research_position`; these do not override GPS telemetry selection.
- `current_estimate`: the checked conditional snapshot pose, or null.
- `replay_inputs`: exact requests for `pnp_window_alignment.align`, including
  each attempted refresh's full joint uncertainty.

Observer status retains a clearly historical, written `last_event`. Its separate
`latest_snapshot_estimate` is a compact, isolated snapshot made available before
copying/serializing/writing the complete numerical replay event. This prevents
the diagnostic write itself from blocking access to an otherwise-current
conditional pose. The compact record is limited to 64 KiB; full event/trace
limits remain 4 MiB / 256 MiB. There is no new unbounded journal queue or writer
thread, and the solver still cannot start its next cycle until that write ends.

`latest_snapshot_evidence` identifies the event sequence and reports `pending`
or `written`. Pending means the numerical result has passed its existing checks
but the full replay write has not completed; it is **not durable evidence or
navigation authority**. Written means write/flush returned, not a power-loss
durability or `fsync` guarantee. Every journal row has the matching monotonic
`event_sequence`. A byte-budget, encoding or write failure terminates the
observer and withholds the compact result. Finish must still drain successfully.

Both latest fields are null when the native pose, retained fix set, source
health or receipt-age checks no longer agree, and after stop. All exact-current
checks run again when status is read. Old results are not re-timestamped or
promoted merely because their evidence write completes. Caller mutation cannot
change the private estimate. A historical successful event is not evidence that
the current system is healthy.

The advancing-stream observation now samples this actual status accessor as
well as completed solve checks. `operator_status_advancing_pose_timestamps_ns`
must not be confused with `current_pose_timestamps_ns`: the latter records
checks earlier in processing and can be nonempty even when no current pose was
visible to the status reader. See the latest handoff verification for measured
results; positive offline or stopped-stream solves are insufficient.

## Remaining Qualification

This feature does not solve measured camera/IMU timing, physical VIO/map
cross-correlations, temporal place ambiguity, learned geographic accuracy,
global propagation after anchor marginalization, or sustained Pi resource and
thermal limits. Native integration tests use actual OpenVINS and GTSAM
processes but synthetic map observations and noise, not real-flight fixes.
PX4 transport remains a separate, disabled promotion boundary.

## Initial Execution Evidence

The September 5 native integration used actual OpenVINS and isolated GTSAM
processes with synthetic PnP observations and explicitly synthetic independent
noise. One original map fix at sensor time 8.800 s corrected a native pose at
8.850 s; two additional native frames advanced that pose to 8.950 s without a
new map fix. Its map-evidence age correctly changed from 50 to 150 ms. All 160
native input batches completed and the original fix was not re-timestamped.

This is **not a real-time benchmark**. The sealed-replay test permits a 10 s
snapshot receipt age; it does not exercise the production-default 500 ms limit
at a continuously advancing camera cadence. Observed background processing
times on this Mac were approximately 329 ms for the first correction and
582 ms for the subsequent refresh (170 ms for the final drain re-solve).
These are individual test observations, not Pi P50/P95/P99 measurements.
They expose remaining performance work: full dense re-solving cannot yet be
treated as flight-rate propagation. Raising the freshness limit is not a fix.

The next estimator step must reduce repeated work while retaining the proper
joint VIO/map covariance and exact sensor times, then test against a continuously
advancing native stream with the intended freshness policy. Carry-over beyond
anchor marginalization remains a separate mathematical requirement.

## Verification Record

A subsequent [correlated-window reduction](joint-pnp-vio-alignment.md#smaller-correlated-solve)
eliminates unused pose residuals from each solve and corrects an observed
high-correlation covariance numerical issue. It retains complete input
validation, all relevant cross blocks and the original observation times.
Paired Mac benchmarks show reduced work, but the coordinator still withholds
when its snapshot changes during solving. This is not a completed solution
to current-state starvation or long-gap global propagation.

A subsequent [bounded serialization improvement](alignment-serialization.md)
reduces measured copy/message overhead without changing numerical estimation.
Its local serialization-only benchmark is separate from the native refresh
timings above and does not qualify live cadence or Pi performance.

September 5, 2026, development Mac:

- 266 focused estimator, PnP binding, continuous/synchronous lifecycle,
  native-VIO and PX4-boundary tests passed in the Python 3.11 estimator
  environment, including the actual native-process experiment above.
- 133 broader learned-model/native/map regressions passed in the Python 3.13
  ONNX environment with the actual MegaLoc and XFeat/LighterGlue exports.
  Three optional direct-PyTorch checkpoint tests were skipped because no
  `VISION_NAV_LOCAL_REFERENCE_CONFIG` was supplied.
- All 104 main project checks passed. CLI help includes the new refresh
  option, and `git diff --check` passed. Desktop/Rust checks were not rerun;
  no desktop code changed in this pass.

Generated evidence is under
`research/outputs/continuous-alignment-final-20260905-r1/` and
`research/outputs/continuous-alignment-model-regression-20260905-r1/`, excluded
from Git. Tests cover asynchronous queue replacement, retained-window updates,
expired anchors, stale receipts, exact replay, idle estimator exit, journal
failure, timeout, final drain and shutdown. Map/ONNX dependency deprecation
warnings remain. These are execution and contract checks, not hardware
qualification or field accuracy.
