# Independent Native VIO Stream Worker

The native OpenVINS research runner now supports bounded sensor delivery through
stdin, with local-state records flushed as each camera update completes. A Python
worker owns that process independently of the caller. This is a step toward VIO
running alongside slow learned map inference, not a completed live navigation
service.

The current entrypoint consumes a **sealed sensor recording**, including the
[Pi capture conversion output](pi-capture-vio-replay.md). It does not open a
camera, Pixhawk or radio. A separate [arriving-sensor adapter](native-vio-live-input.md)
now accepts new in-memory frames with qualified raw IMU/clock evidence and a
fail-stop spool. The opt-in [finite sensor session](live-vio-sensor-session.md)
connects device ownership, clock buffering and ordered native dispatch. Actual
learned model binding, delayed map fusion and aircraft publication still need integration and
hardware qualification. No new ROS or simulator dependency is introduced.

## Run

Rebuild the native adapter first; the upstream OpenVINS revision is unchanged:

```bash
cmake --build research/outputs/openvins-native-build -j 2

python -m vision_nav.vio.stream_replay \
  --binary research/outputs/openvins-native-build/vision-nav-openvins \
  --capture research/outputs/x500-vio-input-001 \
  --output research/outputs/x500-vio-stream-001 \
  --capacity 2 \
  --batch-timeout-s 5
```

Use an existing sealed synthetic/public-dataset input when no qualified aircraft
capture is available. That choice does not become aircraft evidence. After an
editable install, `vision-nav-stream-vio-replay` is equivalent. The output
directory must be new and outside the capture. The Python worker currently uses
POSIX nonblocking pipes on Linux/macOS; Windows-native process streaming has not
been implemented. Use the Linux research environment for that workflow.

The CLI drives the recording as fast as the worker processes it. It does not
pace sensor arrival, emulate real-time deadlines or measure Pi capture-to-pose
latency. Its five-second per-batch timeout is an experimental stall limit, not a
flight-control deadline. A separate 1,800-second whole-run limit bounds this
replay command.

Default live-load recording still measures the Python runtime process only.
The stream replay can now attach a separate, identity-bound native-child probe:

```bash
python -m pip install -e '.[runtime-metrics]'
python -m vision_nav.vio.stream_replay \
  --binary research/outputs/openvins-native-build/vision-nav-openvins \
  --capture research/outputs/x500-vio-input-001 \
  --output research/outputs/x500-vio-measured-001 \
  --record-runtime-metrics --metrics-interval-s 1 --metrics-max-samples 3600
```

Use an existing sealed capture and a new output directory. The report records
the exact metrics path and worker run ID. Parent and child counters remain
separate; their RSS is not summed as unique physical RAM. See
[native process accounting](live-runtime-load-recording.md#native-vio-accounting).
This recorded-input path does not start learned inference. A concurrent Pi
benchmark still needs both workloads connected and running together.

When recording is requested, fewer than two available native samples, recorder failure, shutdown
failure or exhausting the sample cap makes the replay report incomplete and
the CLI exit nonzero, even if native estimation finished. An available recording
can still contain unavailable child samples or other measurement gaps; inspect
the load report, not just the replay exit status.

## Ownership And Backpressure

`NativeVioStreamWorker` provides `start`, `submit`, `finish`, `join`, `stop`,
`status` and `take_latest`. One worker thread owns native stdin and the state-file
reader; native stdout/stderr go to `native.log` so library diagnostics cannot
masquerade as JSON pose records. The child estimator is a separate process.

- Each submitted batch contains IMU look-ahead followed by exactly one camera
  event. The caller retains sensor order and native bracketing requirements.
- Input capacity is 1..8 pending batches, default 2, plus one in-flight batch.
  Each batch has at most 1,024 events and 256 KiB; each event is at most 64 KiB.
  Camera images stay in the sealed capture, not inside the pipe payload.
- The worker serializes the accepted batch into owned bytes. A full queue returns
  false without consuming it or advancing the camera timestamp watermark.
  The caller must retry that same batch or abort, never silently discard IMU
  samples and continue. This backpressure is suitable for recorded input; an
  actual sensor producer cannot pause physical time and needs its own bounded
  overrun/fault policy before integration.
- One matching camera-state acknowledgement is required before sending the next
  batch. Startup and each in-flight write/read have a timeout. Waiting in the
  pending queue is separate from that timeout; it is not a freshness guarantee.
- Native state records are capped at 2 MiB. Wrong schema, revision, timestamp,
  position authority or navigation mode aborts the worker.
- Full state history is written to disk. Only the latest acknowledged snapshot
  is kept in worker memory. `take_latest` returns an owned copy once per new
  completion and may skip intervening snapshots; use the full log for audits.

The native filter preserves its own bounded clone window. A returned posterior
is still correlated with previous posteriors and is not an independent odometry
factor. The default stream boundary does not extend retention for delayed learned
fixes. Opt-in [owned anchor controls](live-native-anchors.md) retain exact native
poses and cross-correlations; they do not qualify covariance or implement the
learned coordinator's complete retention policy.

## State And Shutdown

Compact status distinguishes starting, waiting for sensors, processing, complete,
no local estimate, failed, stopped and incomplete shutdown. It includes submitted,
completed and pending batch counts, backpressure, local-state counts, native PID
and exit code. It does not put full covariance arrays in every status read.
The backpressure count is the number of unsuccessful submit attempts, including
retries for the same batch. It is not a hardware-drop or unique-frame count.

Both `status` and `take_latest` now include a per-instance `worker_run_id` UUID.
The [delayed PnP coordinator](joint-pnp-vio-alignment.md#delayed-results) uses it
to reject snapshots from a different native run. The ID binds process ownership;
it does not certify clock synchronization or detect every internal sensor reset.
The owning service must invalidate alignment on a reset or native failure.

`last_sensor_timestamp_ns` stays in the original dataset/qualified input clock.
`last_result_received_age_ms` measures host time since receiving a result. The
latter is not sensor age, and the worker explicitly says the two clocks are not
interchangeable. An old local result remains old even while status is read.
Consumers must inspect worker health; `take_latest` is not an authority gate.

### Snapshot Handoff And Timing

Snapshot selection captures the private record reference, original receipt time,
completion count and control generation under the native condition lock. The
owned deep copy is made outside that lock, so a slow consumer does not block
sensor submission or native publication. A newer posterior remains available to
the next call; an intervening applied anchor control or terminal failure
invalidates the captured copy. Downstream current-posterior and freshness checks
are unchanged. Returning a coherent older copy does not make it current.

Receipt time is recorded immediately after native response decoding and
identity validation, before adapter evidence checks, disk work or publication
lock acquisition. Those delays therefore increase reported receipt age. This
is still a host receipt timestamp, not an exposure timestamp or a native
compute-completion measurement.

`timing_snapshot()` returns `vision_nav_native_batch_timings_v1`. It retains
only the latest 256 completed sensor-batch records and reports how many earlier
records were omitted. Each record includes original sensor time, host queue,
receipt and publication times, plus queue wait, preparation, write, native
wait/decode, adapter acknowledgement, publication lock wait and total service
durations in milliseconds. An active-stage record remains available if a
sensor batch fails. Control-command timings are not included.

The arriving-sensor adapter includes this bounded report in `live_report.json`.
Native wait/decode includes host scheduling, file polling and JSON work; it is
not pure native estimator latency. These records neither establish sensor-age
freshness nor qualify sustained Pi throughput. Queue capacity, overflow policy
and freshness thresholds are unchanged.

`finish` sends EOF only after draining queued batches. Native exit 3 is reported
as no local estimate, not a successful trajectory. `stop`, timeout or invalid
native output ends this worker's own process, with terminate then kill as needed.
It does not search for or kill other processes. A failed reaping attempt is
reported as incomplete shutdown; a short `join` timeout alone is not completion.
Queued/unacknowledged batches remain visible through the counts after failure.

`local_states.jsonl`, `native.log` and `report.json` remain in the output directory.
The replay report retains the existing `vision_nav_openvins_replay_v1` contract
with `execution_mode="bounded_sensor_stream_replay"`, source/binary/worker hashes
and compact worker state. Failed runs retain partial evidence. The queue is
bounded, but disk logging is not a storage-retention policy or power-loss promise.

## Verified Scope

Native tests exercise file-versus-stream byte-identical state output, progress
while the calling thread waits, full-queue retry without consuming a batch,
timeout cleanup, and interruption while native VIO waits for stdin. The same
tests inspect explicit no-global-fix and disabled-publication fields.

```bash
VISION_NAV_OPENVINS_BINARY=research/outputs/openvins-native-build/vision-nav-openvins \
  python -m pytest -q tests/test_vio_stream_worker.py tests/test_openvins_replay.py
```

Use the research estimator Python environment (including GTSAM for the existing
joint-covariance audit). Actual X500 sensor timing, concurrent Pi VIO/learned
inference load, live overflow handling and global fusion remain unverified.
Every estimate stays `NO_GLOBAL_FIX`; no GPS, map correction or flight-control
transport enters this worker.

### Handoff Regression: September 5, 2026

After moving snapshot copies outside the native condition lock, three finite
native/isolated-alignment trials completed without overflowing the unchanged
two-batch queue. Each offered 20 further camera frames at 20 Hz after synthetic
map-fix binding; no per-solve wait was added. Each still counted **zero advancing
current global updates** during that interval. A result checked during the
interval but describing the initial posterior does not count as progress.

The table summarizes the complete 120-batch fixture, including initialization
and pre-stream acquisition. It is not a streaming-only or Pi benchmark.

| Trial | Service median / max, ms | Publish-lock wait max, ms | Advancing global updates |
| --- | --- | --- | --- |
| Initial handoff | 8.680 / 36.038 | 0.005 | 0 |
| Expanded regression | 8.791 / 48.648 | 0.007 | 0 |
| Independent repeat | 8.756 / 98.548 | 0.009 | 0 |

Local ignored roots, each containing
`test_query_owned_before_slow_r0/live/live_report.json` and
`test_query_owned_before_slow_r0/streaming-observation.json`:

- `research/outputs/native-handoff-20260905-r1`
- `research/outputs/native-handoff-regression-20260905-r1`
- `research/outputs/native-handoff-repeat-20260905-r1`

The expanded regression passed **218 tests in 127.52 s**, including actual
native replay equivalence, input faults/overflow, anchor lifecycle, isolated
alignment and the PX4 withholding boundary. An injected 50 ms acknowledgement
delay remains visible in snapshot age. Unit tests block the consumer copy and
prove native publication can proceed, while control/failure changes invalidate
the captured copy. The independent repeat passed in 6.28 s.

These trials use synthetic camera/IMU and map/noise fixtures on the Mac. They do
not establish sustained load tolerance, physical noise, aircraft accuracy,
current-global availability or Pi qualification. The earlier queue-overflow
failure remains historical evidence; three successful short trials do not
establish that all overload causes are fixed. The native binary is unchanged.

### Real Recorded-Sensor Check: September 5, 2026

The previously verified TUM VI room1 input was streamed through the rebuilt native
adapter on this Mac. All 2,821 camera batches were submitted and acknowledged,
with 132 initializing/no-update records and 2,689 local estimates. The complete
state file, including joint clone covariance, is byte-identical to the earlier
`tumvi-room1-joint-20260905-r1/local_states.jsonl` file replay. Native exit code
was zero and the owning thread/process exited normally.

The run took 39.90 seconds including stream execution and post-run input
verification. There were 5,770 full-queue retry attempts and zero remaining
batches. Those observations describe this as-fast-as-processed Mac replay, not
Pi throughput, real sensor drop rate, live latency or flight accuracy.

Local ignored evidence:
`research/outputs/tumvi-room1-stream-replay-20260905-r1/report.json`.

- State SHA-256: `b762a6f1b090053c87c16cf4d2d916d36ccff90e1afd397f0645b147568a929c`.
- Native binary SHA-256: `fca6a0463c3846b7e878996478193d7ce8c1a44b3b85ce15c1138499dcf4a0fe`.
- Input manifest SHA-256: `f82bf72d1dc382cdae12c8008d1ef7599b248dc0eac09b91ed485701e8908fe1`.

The combined stream, capture conversion, native replay, timing, pose-window and
PX4-boundary suite passed 133 tests with the real native binary. This does not
extend the earlier handheld-dataset accuracy result to aircraft or qualify a
live learned-map correction path.
