# Live Native Pose Retention

Status: opt-in research transport and frame-ticket API. This connects the
[passive native pose mechanism](native-passive-anchors.md) to the single owned
VIO input stream. The separate [learned-query owner](learned-query-anchors.md)
now supplies opt-in automatic retention and retirement. Neither path establishes
qualified long-gap global navigation or aircraft output.

## Contract

Construct `NativeVioStreamWorker`, `NativeVioLiveWorker` or `LiveSensorSession`
with `anchor_capacity=1..4`. Zero is the unchanged default. Rebuild the native
adapter; its version response must advertise
`anchor_control_schema=vision_nav_openvins_anchor_control_v1`.

The stream worker accepts:

```python
command = worker.request_anchor(
    "retain", original_camera_timestamp_ns, worker_run_id=worker.worker_run_id
)
if command is not None:
    acknowledgement = command.result(timeout_s=5.0)
```

For arriving frames, use the actual issued `LiveFrameTicket`:

```python
command = session.native.retain_frame(ticket)
# Require command.result(...)['status'] == 'applied' before relying on retention.
# Later, explicitly release the same frame:
release = session.native.release_frame(ticket)
```

These snippets describe the existing-owner API, not a standalone camera loop.
The live adapter rejects raw timestamp requests, foreign epochs and altered
ticket sequence/pixel/PNG identities. It keeps only frame identity metadata,
bounded by the finite session's `max_frames`; it does not retain another image
copy. Native code verifies the exact camera-clone timestamp. The Python sensor
owner binds that timestamp to captured pixels; the native ACK is not a separate
pixel-content attestation.

## Ordering, Bounds And Failure

- One FIFO and one worker own stdin. Commands run between complete IMU/camera
  batches, never inside a sensor batch or through a second pipe writer.
- Controls have four pending slots plus at most one in flight. Sensor batches
  retain their separately reserved capacity. Neither queue silently evicts.
- At most 1,024 accepted control requests per worker epoch. Request IDs increase
  within that epoch; gaps are allowed because unsent expired commands may
  have consumed an ID. The native process rejects duplicate/reordered IDs.
- `None` means queue backpressure: no command or ID was accepted. A returned
  receipt means queued, **not retained**.
- The command deadline includes queue time. Expiration before sending reports
  `not_applied`; there is no native mutation. Native capacity, unavailable
  camera, duplicate retention and unknown release return explicit rejections
  without terminating ordinary VIO.
- A caller's `result()` wait timeout does not cancel the request. Wait on that
  same receipt; do not retry a possibly applied command under a new identity.
- A sent command with no trustworthy ACK terminates the native epoch and is
  reported `indeterminate`. Pending unsent commands are settled as `not_applied`.
  Only the owned native process is reaped; there is no automatic restart.
- `finish()` drains accepted commands and sensor batches before EOF. New commands
  are rejected after closing. Native exit destroys any remaining passive states;
  a historical successful ACK is not retention in a future worker epoch.

ACKs include operation, original camera timestamp, request ID, worker UUID,
current native camera timestamp, exact input-line index, capacity, active anchor
membership and a reason. They are bounded at 4 KiB. No control advances camera
completion counters, IMU timestamps, exposure times or receipt freshness.

After a successful mutation, the worker withholds its cached pose snapshot until
the next actual camera update exports the changed covariance-variable set. It
does not present a control acknowledgement as a new sensor posterior. The next
snapshot uses the v2 joint-window contract, including passive/live cross terms.

## Evidence And Replay

Opt-in runs produce, under the native output directory:

- `owned_stream.jsonl`: exact dispatched sensor/control bytes in order.
- `anchor_acknowledgements.jsonl`: native control responses only.
- `local_states.jsonl`: camera records only, with v2 membership.
- `anchor_control_report.json`: hashes, epoch, capacity and unsent/indeterminate
  requests. Its scope is native transport before final input-adapter checks;
  require the complete live/session reports as well.

The live evidence-byte budget includes both additional streams. With image
recording enabled, sensor-only archived events remain unchanged. The export
validator compares every non-control dispatched event against that archive,
checks ACK ordering and checks each camera window against active membership.

`export_sensor_replay` preserves the control evidence and marks it in the sealed
input's `source.anchor_control`. The existing replay command automatically
replays that ordered stream when `--pose-window` is supplied; it refuses a
separate offline anchor schedule or omission of the joint-window flag. The
pose-window auditor verifies control provenance and numerical joint covariance.
Default captures continue using the original sensor-only path.
The sensor-only stream replay rejects these controlled captures rather than
silently omitting their anchor commands; use `openvins_replay run --pose-window`
for the exact recorded-control replay.

## Remaining Integration

[Automatic query ownership](learned-query-anchors.md) acquires before inference
and manages queued, rejected, superseded and accepted evidence with explicit
capacity. It is enabled separately from this low-level API and separates the
inference deadline from an accepted fix's original sensor-time horizon.

The exact-latest-snapshot timing limitation, physically calibrated
joint noise, Pi thermals/cadence and X500 flight accuracy are also unresolved.
Every result remains research-only, `NO_GLOBAL_FIX`, uncalibrated, and without
PX4 publication. No sensor, aircraft parameter or cloud resource is accessed
by the automated synthetic tests.

## Verification: September 5, 2026

The rebuilt native adapter passed both CTests. The estimator/native regression
passed 305 tests; a further 52 control/retention checks passed after adding the
sensor-only replay rejection and explicit queued-command shutdown test. These
counts overlap and are not a total of unique project tests.

Evidence includes:

- A native pipe request retained the original camera pose for more than four
  seconds, beyond the ordinary half-second camera window. The local state,
  velocity, biases and covariance matched the no-anchor baseline within the
  stated numerical tolerances.
- One unchanged synthetic PnP observation could align a later retained snapshot
  with explicitly synthetic joint noise. No physical uncertainty was inferred.
- The live sensor-session harness kept exactly one camera owner and one link
  owner while 25 additional camera batches completed during retention. Its
  export replayed native states and ACKs byte-for-byte.
- Malformed epochs, duplicate IDs, capacity overflow, expired poses, queued
  deadlines, sent-command ACK failure, stopped pending commands, source changes
  and corrupted recording evidence were rejected or terminated as specified.

Artifacts are Git-ignored under:

```text
research/outputs/live-anchor-final-20260905-r1/
research/outputs/live-anchor-followup-20260905-r1/
```

The `live-anchor0/live-anchor-alignment.json` artifact retains the complete
synthetic observation/noise request and conditional alignment result. The
native build SHA-256 is
`e89ddd42fbe01f5993cc32396ad8f6d54363bc910a199f52f01827450df2afcb`.
The pinned upstream OpenVINS checkout was not modified.

The actual ONNX/native/map/recorded-training regression then passed 152 tests
in the Python 3.13 environment. Three optional direct-checkpoint tests were
skipped because `VISION_NAV_LOCAL_REFERENCE_CONFIG` was unset; real ONNX and
native-process cases ran. Existing Torch export and geospatial deprecation
warnings remain. Evidence is under
`research/outputs/live-anchor-model-regression-20260905-r1/`.

A final controlled-replay provenance guard rejects changes to the input
manifest, native executable, replay adapter or dispatched stream during a run.
Its control/replay follow-up passed 45 tests in
`research/outputs/live-anchor-provenance-20260905-r1/`. All 104 main project
checks also passed. No desktop code changed in this pass, so desktop and Rust
tests were not rerun. These checks do not establish physical timing, field
accuracy, calibrated covariance or flight readiness.
