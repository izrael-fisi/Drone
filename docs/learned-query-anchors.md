# Learned Query Pose Ownership

The finite prop-off learned session now has opt-in automatic native pose
retention. This connects the [live native control API](live-native-anchors.md)
to the asynchronous learned query lifecycle. It does not enable aircraft
publication, qualify uncertainty, or solve continuous-current refresh starvation.

## Enable

Add these options to the existing, fully configured
[live learned bench command](live-learned-sensor-session.md):

```text
--record-images
--local-verification-config /absolute/path/to/local-verification.json
--alignment-config /absolute/path/to/alignment.json
--alignment-noise-provider /absolute/path/to/research_noise.py
--alignment-refresh-interval-s 0.2
--retain-query-anchors
--accepted-evidence-max-age-s 30
```

The last option defaults to 30 seconds and accepts (0,300]. It is an explicit
research evidence-retention policy, not measured flight accuracy or a change
to the default 500 ms native snapshot receipt-age gate. All normal model/map,
calibration, endpoint and `--propellers-removed` arguments remain required.
There is no supplied flight-qualified noise provider. Never use the synthetic
test provider as aircraft uncertainty. No paid compute or flight output is
started by enabling this local experiment.

Python callers construct `LiveSensorSession` with `anchor_capacity=4` and
`record_images=True`, then `LiveAlignmentObserver` with an owned estimator
process, continuous refresh, `retain_query_anchors=True`, and optionally
`accepted_evidence_max_age_ns`. Use `run_live_learned_session` to own admission,
inference, submission and shutdown. Direct callers must call
`begin_inference(item)` and require `status == "dispatched"` before inference;
late `observe(...)` calls cannot retroactively create an owned query.

## Lifecycle

1. Select an immutable frame ticket from the existing sensor owner.
2. Start the query's host deadline, wait for its exact native pose, and request
   retention. Require the native acknowledgement and a subsequent real camera
   snapshot that includes the passive anchor. A control ACK is not a new pose.
3. Register the coordinator query with the original dispatch time and frame
   checksum. Only then run the learned model. No second camera or serial reader
   is introduced; ingestion continues during this bounded admission wait.
4. Bind the returned geometry to that same query, not a new post-inference
   timestamp. Rejected bindings and superseded queued drafts release ownership.
5. A successful conditional alignment marks evidence accepted. Before acceptance,
   the ten-second query deadline includes admission, inference and binding/solve.
   After acceptance, evidence expires against the newest posterior's original
   sensor time and the configured horizon. Neither policy resets its capture
   timestamp, uncertainty or statistical independence.
6. Retire coordinator evidence before releasing its native pose. Cache/status
   checks withhold an estimate whose exact conditioning set or native snapshot
   changed. A normal worker exit destroys remaining passive states, but cleanup
   requires a confirmed return code and finished owner thread, not a wait timeout.

## Bounds And Failures

- Four native anchor slots; at most two accepted anchors survive maintenance.
  Oldest accepted evidence is explicitly retired when a third is accepted.
  Pending/in-flight queries are never silently evicted to admit another query.
- The native 1,024-command budget reserves capacity for outstanding releases.
  Admission refusal skips that learned window and records why; VIO sensor
  batches are not dropped by this policy.
- Command backpressure means nothing was submitted. A returned receipt is held
  until settled; a wait timeout never triggers a duplicate retain/release.
  Indeterminate outcomes invalidate the observer and require native-epoch cleanup.
- Orderly input closure is checked atomically with command submission. An
  `AnchorInputClosed` exception guarantees that no command or receipt was
  created; a racing new retain is explicitly withheld. A racing release still
  owns its anchor until native epoch exit is confirmed. A join timeout is not
  release evidence. Unexpected transport failures and pending/indeterminate
  receipts keep their existing error paths; they are not treated as clean EOF.
- Unaccepted bound evidence may remain available for retry until its original
  deadline. A binding rejection retires it immediately. Expired accepted fixes
  cannot be revived by another solve or a new timestamp.
- The owner holds references to at most four existing immutable frame buffers,
  not extra pixel copies. Native control and query ownership remain bounded;
  the broader recorder, inference and covariance budgets still apply.

## Evidence

`learned/query-anchor-trace.jsonl` records admission, requests, acknowledgements,
conditional acceptance, retirement and release. Each event is at most 16 KiB;
the complete file is at most 32 MiB. Exceeding the limit fails the observer.
The final `research_alignment.query_anchors` status contains its checksum,
remaining ownership and cleanup state. Model rows include the original
`query_anchor_admission`; `query_anchor_skipped_windows` counts withheld admission.

The native ordered stream, acknowledgements, covariance windows and images retain
their existing [exact replay export](live-native-anchors.md#evidence-and-replay).
All navigation authority remains false and PX4 external vision remains disabled.

## Remaining Work

Native retention preserves correlations; it does not establish physical noise
models, unbounded global memory, global recovery, or Pi timing. The independent
alignment solver can still lose the race with newer camera snapshots. Qualifying
delayed correction/forward propagation, microSD throughput, camera/IMU timing,
and X500 accuracy remains necessary before any aircraft integration.

## Verification: September 5, 2026

Later shutdown-race regression: the first native-handoff model suite had
125 passes and one failure. The sensor owner closed input between admission's
status check and retain submission; the finite session reported incomplete
with `RuntimeError:Native anchor input is not accepting commands`. Evidence is
under `research/outputs/native-handoff-learned-20260905-r1/`, in
`test_real_models_are_dispatche0/learned/summary.json`.

Anchor submission now distinguishes atomic, orderly, unsent refusal from
transport failure. Deterministic tests cover closure racing retain/release,
required exit confirmation, no created request, and genuine errors remaining
errors. Those tests passed 25 checks; the subsequent actual-native lifecycle
suite passed 87 checks in 76.35 s. Neither change retries sent commands, ignores
indeterminate receipts, drops sensor input or enables aircraft output.

The full learned/native/observer regression then passed **130 tests in 101.87 s**
with no skips and 13 existing Torch export/tracing and geospatial warnings.
Evidence: `research/outputs/native-handoff-learned-20260905-r2/`. This includes
the previously failing actual-model retention test; it is execution/lifecycle
evidence, not aircraft accuracy or continuous-current navigation qualification.

The combined lifecycle/live-observer/model suite passed **114 tests** using
Python 3.11, NumPy 1.26.4, GTSAM 4.2.2, Torch 2.13 and ONNX Runtime in the local
`/tmp/drone-bearing-modern` test environment. Existing Torch export/tracing and
geospatial deprecation warnings remain. No test in this run was skipped.

- The actual native estimator retained an issued pose beyond its normal clone
  window while another 25 camera updates completed. A delayed, explicitly
  synthetic map observation was accepted by the isolated conditional estimator
  under its original query identity, then explicitly released.
- Actual MegaLoc and XFeat/LighterGlue ONNX exports plus the fixture-trained
  temporal student ran with both native VIO and the isolated estimator present.
  The finite synthetic run completed 160 native camera batches, admitted two
  learned windows only after retention, and recorded eight withheld admissions
  (mostly initialization). It tested inference ownership and rejection, not
  successful real-image metric localization or sustained throughput.
- That run ended with no owned query poses and no live model/estimator children.
  Rejected evidence was released; a pose retained at normal native EOF was
  retired using confirmed epoch exit. Recorded query evidence was checksummed.
- Tests cover accepted sensor-age expiry, original dispatch deadlines, explicit
  retirement during solving, capacity, backpressure, indeterminate commands,
  wait-timeout receipt reuse and nonblocking status.

Artifacts are Git-ignored under
`research/outputs/query-anchor-combined-20260905-r2/`. The actual-model evidence
is in `test_real_models_are_dispatche0/learned/summary.json`; the native/synthetic
alignment evidence is in `test_query_owned_before_slow_r0/alignment.jsonl`.
These are local research tests, not X500 flight or Pi hardware qualification.

The subsequent native/estimator/PX4 regression run passed **220 tests** in
159.12 seconds; artifacts are under
`research/outputs/query-anchor-regression-20260905-r1/`. The main
`python tests/run_unit_tests.py` suite also passed all **104 checks** in the
existing Python 3.13 environment. Counts describe separate test runs, not a
claim of unique field scenarios. CLI help includes both new options, whitespace
checks passed, and the pinned upstream OpenVINS checkout remained unchanged.
