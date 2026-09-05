# Live VIO Sensor Collection Session

This opt-in, finite prop-off workflow connects Picamera2, one MAVLink reader,
bounded clock/IMU buffering and the native OpenVINS research adapter. It keeps
local VIO progressing independently of a caller consuming learned image windows.
This collector alone does not run learned localization. The optional
[live learned consumer](live-learned-sensor-session.md) now binds its image
windows to ONNX retrieval/student/local-geometry stages. Neither workflow fuses
a map, enables flight navigation, or changes the existing shadow service.

## Before Running On Pi

- Physically remove propellers and keep the aircraft disarmed. The CLI requires
  an explicit acknowledgement; this is not a remotely verified safety condition.
- Build/install the pinned ROS-free native OpenVINS research executable. Its
  licensing and Pi qualification requirements still apply.
- Supply the measured, checksum-valid sensor profile from
  [camera/IMU preparation](pi-capture-vio-replay.md). Template or synthetic test
  declarations are not qualified aircraft calibration.
- Ensure the selected PX4 sender already streams complete HIGHRES_IMU samples
  at a sufficient rate. This workflow does not change rates, parameters, arming
  state or receiver configuration. ATTITUDE is not a replacement for raw IMU.
- Give this session exclusive ownership of the camera and its MAVLink endpoint.
  Do not run it alongside the status bridge, another capture script or another
  serial reader on that same USB device. It does not stop other services for you.
- Have Picamera2/libcamera, OpenCV, NumPy, PyYAML and pymavlink available in the
  Python environment. The exact measured global-shutter calibration dimensions
  are used; there is no camera backend fallback or implicit resize.

No aircraft profile or hardware synchronization is established by the local
synthetic verification described below. Do not run this on an active flight.

## Command

Use actual measured profile and device paths, and a new output directory:

```bash
vision-nav-live-vio-bench \
  --binary /opt/vision-nav/bin/vision-nav-openvins \
  --checkout /opt/research/open_vins \
  --sensor-profile /data/calibration/x500-vio-profile \
  --endpoint serial:/dev/ttyACM0:115200 \
  --frames 120 \
  --interval-s 0.05 \
  --output /data/bench/live-vio-001 \
  --propellers-removed
```

`python -m vision_nav.vio.sensor_session` is the equivalent module entrypoint.
`--max-clones` is an optional explicit research override; it does not change Pi
defaults or old captures. See [history experiments](map-conditioned-replay.md#delayed-result-retention).

The requested interval is a capture-call schedule, not a measured camera rate.
The default is 120 frames, a ten-second warmup limit and a sixty-second native
session limit. Capturing many frames without also changing the library's
duration policy can end at that limit. Longer runs require deliberate library
configuration and resource qualification, not removing bounds.

The only MAVLink transmissions are diagnostic TIMESYNC requests, nominally every
0.2 seconds with a 0.1-second timeout. There are no vehicle commands, heartbeats
from this workflow, parameter writes, GPS substitutions or external-vision messages.

Exit 0 means the input session completed and native local estimates existed.
Exit 2 includes a completed static capture without native initialization, an
input fault or interruption. Neither exit code certifies global navigation.
All reported navigation modes remain `NO_GLOBAL_FIX` with no position authority.

## Data Flow

1. One dedicated thread creates, reads and closes the MAVLink link. It journals
   telemetry and incremental TIMESYNC exchanges. Other message types, including
   GPS, remain audit data and cannot enter the native IMU stream.
2. Initial arming waits for fresh raw IMU and at least six valid clock samples
   spanning one second. The camera is not opened if this warmup fails.
3. A separate camera-owner thread captures immutable, ordered image buffers.
   It does not wait for learned inference and cannot silently supersede a frame
   needed by the native frontend.
4. The rendezvous buffer waits for a subsequent clock exchange to bracket the
   exposure, and for raw IMU to cover the fixed delay/uncertainty/look-ahead.
   It never extrapolates a clock fit or stamps exposure with receipt time.
5. A sole coordinator feeds ready batches to the native live adapter. A clock
   exchange can release several frames together; they remain in the bounded
   sensor buffer until a native queue slot is available. No accepted batch is
   resubmitted and no raw IMU is dropped after the native epoch starts.
6. The caller may take one latest complete window of immutable image tickets.
   Only this learned-consumer window can be superseded. Its native worker UUID,
   original camera timestamps, hashes, exposure-aligned auxiliary attitude and
   session/native state accompany it.

`LiveSensorSession.start/status/take_latest_window/join/stop` provides this
library interface. A complete window is not a learned model result or pose.
The optional `LiveWindowInference` consumer provides learned model and geometry
execution; global fusion remains a separate, incomplete step.
Consumers must check session health after inference and reject invalidated
epochs. A window returned after completion is recorded historical input, not a
live position update.

## Bounds And Failures

Default buffering holds at most 4,096 unconsumed IMU samples, 16 frames, 64 MiB
of queued image pixels and 32 clock exchanges. The oldest waiting camera frame
has a one-second rendezvous limit. Camera/IMU ordering and gap checks are shared
with the qualified native input contract. The per-file telemetry/camera/dispatch
journals are limited to 64 MiB and each record to 64 KiB; native evidence and
temporary images have [their own bounds](native-vio-live-input.md#bounds).
These are engineering rejection limits, not accepted flight timing budgets.

Before camera arming, old IMU warmup samples may be evicted with a counter. The
first batch also trims only samples well before its first exposure while keeping
a preceding sample. Neither operation discards information from an initialized
native filter. Once armed, IMU overflow is fatal; ordered camera overflow,
clock steps/reboots, missing brackets, source faults and excessive waits also
end the epoch. Reinitialization is explicit, never automatic continuation over
missing data.

At planned EOF, all final-frame IMU coverage has already been submitted. The
reader can close while native VIO drains without creating a false dropout.
On failure or operator stop, pending learned windows are invalidated and the
owned process/threads are stopped. A driver that does not return cannot safely
be closed by an unrelated thread: `shutdown_incomplete` and live-thread flags
must be treated as an unresolved resource shutdown, not success.

Clock waiting adds latency, and Python PNG staging still writes to storage.
Measure the complete sustained Pi workload, temperatures, microSD writes,
queue peaks and capture-to-result distribution before adopting these policies.
The synthetic tests do not demonstrate that the default limits fit the X500.

## Evidence And Training Scope

The session writes `telemetry.jsonl`, `camera.jsonl`, `dispatch.jsonl`,
`timesync.json`, `session_report.json` and the native adapter's `vio/` directory.
The terminal report binds adapter source hashes and journal hashes, raw input
counts, clock/buffer health, native state and resource-owner shutdown status.
It is written immediately before the coordinator returns; use `join()` and a
fresh `status()` to confirm all threads actually ended.

By default image pixels are not retained: the native adapter removes temporary
PNGs after acknowledgement. Add `--record-images` to the bench command above to
retain the exact PNGs consumed by native VIO and the exact ordered input events.
`--max-image-bytes 1073741824` sets the default 1 GiB image budget explicitly.
The library accepts 1 KiB..32 GiB; the independent frame/session/evidence limits
still apply. Recording is never silently disabled or sampled to fit the budget.

### Optional Recording And Replay

The archive resides under `vio/recording/`:

```text
vio/recording/
  images/000000.png ...
  events.jsonl
```

Images are hard-linked from the same-session temporary spool before native
dispatch. This preserves exactly one encoded PNG without a second camera reader,
image encoding or pixel-file copy during capture. It requires hard-link support
on the output filesystem; failure stops the session. Temporary spool names are
still removed after acknowledgement, while retained names survive.

The native terminal report records image hashes, counts and bytes, and the
events hash. Finalization detects missing/extra files, symlinks and changed bytes.
Storage exhaustion, interrupted input and incomplete acknowledgement leave
partial evidence, not a complete capture. These checks are not tamper-proof
signatures or power-loss durability guarantees. No filesystem quota or disk
space reservation is installed. Measure this on the actual microSD before use.

After the session has ended, export to a new directory outside it:

```bash
vision-nav-export-sensor-replay \
  --session /data/bench/live-vio-001 \
  --output /data/replay/live-vio-001

vision-nav-replay-openvins run \
  --binary /opt/vision-nav/bin/vision-nav-openvins \
  --capture /data/replay/live-vio-001 \
  --output /data/replay-results/live-vio-001 \
  --pose-window
```

`python -m vision_nav.vio.export_sensor_replay` is the exporter module form.
It checks stopped camera/link owners, joined native worker, successful input
completion, exact frame counts, original source/recording/configuration hashes,
frame acknowledgements, original camera exposure metadata, dispatch identity
and selected raw-IMU equality. It copies the original configuration and inputs,
then seals a native replay manifest. Changes detected during copying leave an
unsealed partial output; choose a new destination after investigating the failure.

The full source telemetry and camera/clock journals are retained in
`source_evidence/`. Replay uses only the exact dispatched sensor subset, with
the original per-frame converted timestamps. Warmup/tail IMU remains in source
evidence, not secretly inserted into native replay. GPS remains audit data and
never becomes a native motion prior.

A complete static recording without native initialization can be exported,
but still reports no local estimate and no global authority. This replay command
produces a sealed native input, not a model-training release. The separate
training-format export below adds explicit map/calibration binding. Physical
qualification, independent reference quality and training splits remain required.
Do not invent pre/post timing audits or GPS labels to qualify a recording.

### Export Into Training Tooling

Use the completed original recorded session, a measured camera calibration and
camera-to-body file, and the complete reference map release:

```bash
vision-nav-export-recorded-training-capture \
  --session /data/bench/live-vio-001 \
  --output /data/captures/live-vio-001 \
  --calibration /data/calibration/down_camera.yaml \
  --camera-to-body /data/calibration/camera_to_body.yaml \
  --reference-map /data/maps/test-area/reference_map_release.json \
  --site-name test-area \
  --condition prop_off_bench
```

`python -m vision_nav.vio.export_training_capture` is the module entrypoint.
Alternatively select `--map-release` for a fully validated deployment MapRelease
v2, never both map arguments. Reference maps do not require a trained model,
which avoids the first-training bootstrap dependency.

The exporter checks IDs, actual intrinsics/distortion/dimensions and the rigid
camera transform against the recorded native sensor profile. The supported
profile declares body-FRD corrected IMU and identity IMU/body transform; no new
lever arm or frame conversion is guessed. Equivalent four-coefficient radtan
and five-coefficient plumb-bob with zero k3 are accepted. Nonzero k3, different
calibration values or a map lacking that calibration ID are rejected.

The output contains:

```text
manifest.json                   # vision_nav_training_capture_v1
checksums.sha256
training_eligibility.json        # unchanged strict gate, with missing evidence
calibration/down_camera.yaml
calibration/camera_to_body.yaml
map_reference.json              # hash-bound manifest snapshot, not map assets
telemetry/px4.jsonl              # original samples/receipt times, indexed
native_replay/                  # original images, events, config and evidence
```

The images are referenced in place inside `native_replay/`; there is no second
pixel copy within the capture. The complete map assets remain a separate release
to avoid duplicating imagery per flight. For label generation and map-pair
building, supply the original complete map release, not the capture's manifest
snapshot. Map binding is recorded as an offline export decision, not proof that
the map was selected during flight or covers the captured route.

Session IDs derive from the original native worker UUID. Re-exporting the same
recording cannot create a new session identity to bypass split separation.
Original camera metadata, raw telemetry and per-frame native clock fits remain
available. Missing capture UTC, capture-call durations and GPS stay missing.
Image quality measured offline is separately recorded and identified when used
by the training index; it does not masquerade as camera-driver metadata.

The standard training index consumes exposure/native-attitude timing. These
captures explicitly reject the legacy midpoint-timing path. Training labels
also reject a different map manifest under the same release ID, and map-bound
samples cannot silently replace their origin with an arbitrary coordinate.

**Export success is not supervised training approval.** The live session has
periodic clock exchanges, not the separate pre/post snapshots required by the
current strict DatasetRelease gate. Its manifest preserves those exchanges,
marks snapshots `not_recorded` and stores the resulting eligibility errors.
Missing GNSS likewise remains a rejection, not a pseudo-label. Existing explicit
bench-only DatasetRelease options may exercise indexing with incomplete evidence;
they do not make the package qualified field-training data. Do not use those
options to conceal missing synchronization or supervision.

After a qualified DatasetRelease exists, use the existing
`vision-nav-prepare-training-dataset` and
`vision-nav-materialize-training-labels` workflow. The capture export does not
start a training job, provision RunPod, alter desktop state or enable PX4 output.

## Verification

Tests use controlled sensor interfaces and known synthetic timestamps/imagery,
but the actual pinned native estimator. They check single-thread ownership of
each resource, ordered 160-frame native ingestion, independent progress while
the learned consumer waits, bounded clock waiting, EOF handling and failure
cleanup. These are execution/contract tests, not physical timestamp validation,
flight accuracy, global model execution or sustained Pi load evidence.

Training-export verification on September 5, 2026: 147 tests passed across
`test_recorded_training_capture.py`, `test_recorded_sensor_replay.py`,
`test_exposure_training.py`, `test_capture_timing.py`,
`test_camera_extrinsics_release.py` and `test_inertial_capture.py` with the pinned
native binary. All 104 checks in `tests/run_unit_tests.py` also passed, including
same-ID/different-digest map rejection during training-pair generation. Exporter
CLI help and `git diff --check` passed. These results validate packaging and
rejection behavior, not hardware-qualified supervision or model accuracy.

```bash
PYTHONPATH=src:tests \
VISION_NAV_OPENVINS_BINARY=research/outputs/openvins-native-build/vision-nav-openvins \
python -m pytest -q tests/test_live_sensor_session.py \
  tests/test_live_sensor_buffer.py tests/test_capture_timing.py \
  tests/test_recorded_sensor_replay.py
```

Missing native artifacts cause explicit integration skips. They are not success
evidence. No actual camera, Pixhawk, cloud resource or flight transport is used
by these local tests.

September 5 verification: **128 sensor-session/buffer/timing/native/capture
tests passed**, including actual native execution, and the main runner passed
**104 checks**. CLI help and `git diff --check` also passed. The later, paced
synthetic sensor fixture uses scheduled IMU delivery rather than adding a fixed
sleep to processing overhead; none of these runs is a sustained hardware-rate
benchmark.

Evidence for the 160-frame case is in the Git-ignored directory
`research/outputs/live-sensor-session-tests-20260905-r3/test_owned_sensor_threads_feed0/live-session/`.
Native worker ID: `04bdac2b-ca9a-4f0a-85ba-36193e65d704`. All 160 frames were
submitted and acknowledged, with 73 initialization/no-update records and 87
local estimates. Peak queued camera occupancy was four grayscale frames
(307,200 pixel bytes), clock history stayed at 32 samples, and native queue retry
count was zero. The paused learned consumer allowed 157 pending windows to be
superseded without dropping ordered native input. These counts describe this
fixture, not model accuracy, body motion accuracy or Pi capacity.

Recording/export follow-up: **156 regression tests passed** across sensor
session/buffer, capture timing, native live/streamed input, capture conversion
and recording export. After adding the late-sealing mutation check, all **29
recording/export tests passed** again. The main project runner passed **104
checks**; both CLI help paths and `git diff --check` passed.

Latest recording evidence:
`research/outputs/recorded-sensor-replay-tests-20260905-r3/test_complete_live_session_exp0/`.
The actual native estimator consumed 160 synthetic frames and produced 73
initialization/no-update records and 87 local estimates. All 160 PNGs were
retained, totaling 10,432,343 bytes; temporary spool occupancy peaked at three
and ended at zero. Worker ID: `dbdd4a9d-0bf6-4729-bd89-5050c2e43f7a`.
The exported replay produced byte-identical native state output, SHA-256
`876dfad7c8d80f042aaec9ee60c247709ea639c03049ddd6bf8c7869615c81fb`.
This verifies exact input preservation on this fixture, not aircraft accuracy,
microSD load, dataset suitability or trained global navigation.
