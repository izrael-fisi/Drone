# Native Map-Frame Stream Controls

September 5, 2026. **Opt-in recorded image/IMU stream research.** The
[native correlated map frame](native-map-frame.md) is now connected to the
native sensor process and `NativeVioStreamWorker`. It is not connected to the
physical `NativeVioLiveWorker` camera adapter, learned PnP completion, installed
Pi service, ground-station navigation telemetry or PX4 publication.

## Purpose And Scope

After a valid conditional initialization, map-relative pose and covariance are
computed in the native owner on each initialized camera update. A second Python
full-window alignment solve is not required for every new current pose. The
native local trajectory remains unchanged, and global map/current correlations
survive removal of the original query clone.

This implements the owner transaction and post-initialization sensor path. It
does **not** derive qualified map uncertainty or establish a useful learned
global fix. All outer native records remain `NO_GLOBAL_FIX`,
`position_authority=false`, and `px4_external_vision="disabled"`.

## Enabling The Research Interface

The Python owner accepts `map_frame_control=True` when constructing
`NativeVioStreamWorker(binary, sealed_capture, new_output, ...)`. It is false
by default. Map controls share four bounded control queue slots with optional
passive-anchor controls; they cannot consume the separately reserved sensor
batch slots. Sensor batches are never dropped to admit a map command.

The corresponding native option is:

```text
--map-frame-control WORKER_EPOCH NEW_ACKNOWLEDGEMENTS_FILE
```

It requires `--pose-window`. It can accompany `--stream-input` for the owned
stdin stream or operate on a saved ordered event file for replay. If passive
anchor controls are also enabled, the worker epoch must match. Evidence files
must be new and must not alias each other, including through normalized paths.
The native binary advertises `map_frame_control_schema` in `--version`; the
Python owner rejects a binary that lacks this capability.

`request_map_frame(operation, payload, worker_run_id=...)` returns a receipt or
`None` for backpressure without submission. Wait on the same receipt after a
caller wait timeout: that timeout does not cancel a queued/native operation.
An orderly closed input raises `MapFrameInputClosed` before queue mutation.
No method arms the aircraft, changes PX4 parameters or writes flight commands.

## Transactions

All commands use `vision_nav_openvins_map_frame_control_v1`, the worker epoch,
a strictly increasing request ID and a `type` of `map_frame_control`.

| Operation | Payload | Effect |
| --- | --- | --- |
| `capture` | `query_timestamp_ns` | Capture complete native state at the current posterior, only if the exact original query pose remains a normal or passive clone |
| `initialize` | `snapshot_id`, `snapshot_sha256`, `expires_at_ns`, `local_T_map`, `regression_native`, `conditional_covariance_native`, `bindings` | Attempt one conditional map-frame initialization against the exact owned snapshot |
| `clear` | Empty object | Retire map state and pending initialization without changing the remaining local marginal |

There is one pending snapshot and one active map state. A successful new capture
replaces an older pending snapshot. Capturing while a map state is active is
rejected; successive-map fusion remains future work. An initialization matching
the pending ID consumes that snapshot even on rejection. An unknown ID cannot
consume a different pending snapshot. Repeated initialization is not repeated
evidence. An explicit clear is required before another map state can be prepared.

The snapshot contains every native error-state variable in covariance order:
IMU pose/velocity/biases, ordinary camera clones, optional passive poses,
estimated calibration and landmarks when present. Layout entries include name,
offset, error dimension and native nominal value. The original query has an
explicit variable offset. Native IMU and pose means use JPL quaternion storage;
their error dimensions differ from their nominal-value dimensions. Follow the
pinned native types, not a guessed six-dimensional pose-only layout.

The native process encodes the snapshot once and hashes those exact JSON bytes
with SHA-256 using OpenSSL. The acknowledgement contains that string and digest;
Python verifies the bytes before decoding, then returns the parsed snapshot plus
digest. Initialization echoes both snapshot ID and digest. This avoids assuming
identical floating-point JSON formatting across C++ and Python. It is integrity
and transaction binding, **not authentication**.

The retained C++ snapshot separately checks native timestamp, full covariance,
nominal values and variable ordering before augmentation. A hash match cannot
make a stale native snapshot current. Sensor updates and anchor changes may
invalidate a pending initialization; the caller must capture again. The native
input owner is not paused awaiting a Python model result.

## Required Uncertainty Declaration

`regression_native` is the complete six-by-N matrix `B` and
`conditional_covariance_native` is the six-by-six `Q` from the component contract.
There is no inferred independent-noise default. `bindings` requires:

- `fix_id`, `map_release_id`, `model_release_id`, `calibration_id`;
- `uncertainty_sha256`;
- `conditional_model="complete_native_state_and_future_innovations_v1"`;
- `uncertainty_status="research_unqualified"`.

These values are declarations recorded with the evidence. This interface does
not load the release artifacts, verify the referenced uncertainty file, derive
`B/Q` from PnP, or prove independence from later visual innovations. It cannot
promote an uncertainty/model/map release. A qualified upstream adapter must
perform those checks before this could become an operational integration.
The current tests deliberately use synthetic declarations and matrices.

## Output And Failure Semantics

With opt-in enabled, each native camera record includes
`map_frame_projection`, schema `vision_nav_native_map_projection_v1`:

- `conditional_projection`: native current sensor timestamp, original map
  observation timestamp, expiry, map age, initialization ID, `map_T_imu`, full
  right-local SE(3) covariance in rotation/translation order, and bindings;
- `unavailable`: reason and null initialization ID, with no pose/covariance.

Both outcomes are unqualified and carry false authority/calibration flags.
The Python reader checks epoch, current/original times, active initialization,
bindings, transform, covariance and expiry. Applied initialization/clear ACKs
invalidate the prior cached camera record; they do not fabricate a new sensor
receipt. Capture ACKs do not change the local estimator or receipt timestamp.
Status retains only compact ACK metadata, not the full snapshot matrix.

Expiry, loss of an initialized camera posterior, invalid native pose/covariance
or observed time reversal retires the map state. Native exit, response timeout,
source change or malformed response terminates the controlled epoch and settles
pending receipts as not applied or indeterminate. Terminal status clears active
map ownership. A previously recorded pose remains historical evidence, not a
fresh position merely because it can still be read after EOF.

Bounds: 256 native error dimensions for this control interface; 64 KiB request
lines; 2 MiB ACK lines; 64 MiB total map-ACK journal; 1024 map commands per epoch;
four shared pending control slots; one pending snapshot and one active map state.
These are resource ceilings, not Pi throughput guarantees or flight horizons.

## Evidence And Replay

The owner records `owned_stream.jsonl`, `map_frame_acknowledgements.jsonl`,
`local_states.jsonl`, and `map_frame_control_report.json`, plus existing anchor
evidence when enabled. Reports include binary/adapter identities, checksums,
command outcomes and indeterminate requests. Preserve the sealed sensor capture
and these files together. The existing sensor-only replay must not strip map
commands; replay the complete ordered stream with the same epoch/options.

Tests use the real native rendered-image/IMU fixture, explicit synthetic map
conditionals, control faults, clone eviction, expiry and exact replay. The
20 Hz observation test initializes the map **before** starting its timed
producer, then samples `take_latest()` during 60 arriving camera batches. Its
success does not establish delayed learned initialization under uninterrupted
input, concurrent learned-model load, desktop display, field accuracy or Pi
performance. The earlier learned observer's intermittent availability result
is not superseded by this separate experiment.

Next: implement release-validated learned-to-native conditional initialization,
prove shared/future-innovation uncertainty, integrate physical capture and
replay export, handle successive fixes/ambiguity, then qualify combined Pi load.
