# Native VIO Arriving-Sensor Adapter

`vision_nav.vio.live_worker.NativeVioLiveWorker` accepts newly arriving,
in-memory camera frames and new raw IMU batches without requiring a sealed
recording. It drives the same real OpenVINS process used by replay. It is a
library boundary for a finite research/bench session, not an installed live
flight-navigation service. Aircraft transport and global position authority
remain disabled.

The adapter does not open Picamera2, a serial port or a second telemetry reader.
One upstream sensor owner must supply the camera, selected raw IMU and bracketed
clock fit. The opt-in [live sensor session](live-vio-sensor-session.md) now connects
those owners and a bounded rendezvous buffer for finite bench collection. Do not
start a second reader on the Pixhawk USB endpoint to fill this interface.

## Inputs

- The pinned native executable and clean OpenVINS checkout.
- A checksum-valid `vision_nav_openvins_sensor_profile_v1` directory as described
  in [Pi capture conversion](pi-capture-vio-replay.md). Global-shutter intrinsics,
  camera-to-IMU transform, fixed delay, IMU noise, body FRD axes and firmware
  clock identity must be supplied. Validation checks evidence structure; it does
  not independently establish physical calibration accuracy.
- A `CapturedFrame` from Picamera2 with uint8 gray/BGR pixels, original
  SensorTimestamp and the BOOTTIME/MONOTONIC brackets. The calibration ID must
  match the fixed profile. No implicit resize or alternate camera fallback is
  used.
- A `Px4ClockFit` from the selected IMU sender which brackets the entire
  exposure uncertainty interval. The adapter does not extrapolate an old fit
  to the present or use host receive time as exposure time. The future sensor
  owner must wait for a later clock observation within its own bounded buffer.
- Exactly the new HIGHRES_IMU samples for this camera batch. Sensor ID, boot
  timestamp, body FRD axes, SI vectors and all six updated-axis flags are checked.
  Attitude and GNSS cannot substitute for raw acceleration/angular velocity.

IMU time must increase with gaps no greater than 50 ms; camera gaps cannot
exceed 250 ms. These are research input rejection limits, not validated flight
sampling targets. IMU must cover the camera's fixed delay and clock-uncertainty
envelope with at least 5 ms look-ahead and no more than 150 ms ahead. The fixed
camera/IMU delay is applied by native OpenVINS once, not added to the camera event.

## Ownership And Overrun

`submit_frame(frame, imu_samples, calibration_id=..., clock_fit=...)` returns a
`LiveFrameTicket` containing the worker UUID, sequence, native camera timestamp,
pixel/image hashes and a bytes-backed immutable image. This exact image can
later be shared with learned inference. The caller must bound its own retained
ticket/window count; the adapter does not manage the learned consumer's memory.

The native process currently loads images from disk. The adapter copies and
encodes a temporary PNG outside the camera driver, then registers its digest
and sends the normal native camera event. The source capture buffer can change
or be released without altering either the native PNG or the ticket pixels.
The PNG hash is verified before and after native processing. Only after a
matching acknowledgement is it deleted. A failed/stopped session reaps its own
native process before removing unacknowledged temporary images.

This is not a zero-copy or Pi write-rate optimization. Native PNG encoding and
microSD writes still need measurement and may motivate a binary in-memory
native image transport later. Temporary files alone are not a full recording.
The optional `record_images=True` path now retains same-filesystem hard links
under `recording/images/` and exact native events under `recording/events.jsonl`.
It uses the same sensor owner and encoding, with a separate byte limit and final
hash/inventory checks. The [sensor-session recording/export workflow](live-vio-sensor-session.md#optional-recording-and-replay)
combines this archive with camera/telemetry/clock journals for sealed native
replay. Supervised training release qualification remains separate.

Unlike recorded replay, a full queue **ends this worker epoch**. There is no
retry-forever policy and no silent raw-IMU drop followed by continued tracking.
Clock, ordering, calibration or image validation failures also stop the epoch.
The owning runtime must invalidate local/global alignment and explicitly
reinitialize after any such failure. A previously returned local snapshot is
not current merely because it remains readable.

## Bounds

| Resource | Default | Allowed envelope |
| --- | --- | --- |
| Pending native batches | 2 | 1..8, plus one in flight |
| Temporary images | pending capacity + 2 | includes a currently offered image |
| BGR frame size | 16 MiB | explicit limit 1 KiB..64 MiB; calibrated dimensions checked before setup |
| Frame count | 600 | 2..20,000 |
| Session duration | 60 seconds | 1..1,800 seconds, including idle time |
| Evidence size | 128 MiB | 1 MiB..1 GiB |
| Optional retained PNG bytes | 1 GiB | 1 KiB..32 GiB; recording off by default |
| Native result size | 2 MiB | inherited hard per-record bound |

Evidence size is monitored for the frame journal, native states, native log and
optional exact-event recording.
It is a fail-stop monitoring threshold, not an atomic filesystem quota: a write
can cross it before the next check. Temporary image storage is separately
bounded by image count/calibration size. Config and terminal report overhead
are outside that threshold. There is no crash/power-loss durability guarantee.

Use `finish()` after the intended final frame to drain and end normally.
With recording enabled, finalization also reads and verifies retained image
bytes. This adds storage work after native EOF; wait for `join()` before export.
An input failure preserves partial archive evidence but cannot claim a complete
recording. A complete static recording can have no initialized local estimate;
recording completeness is not navigation success.
Offering beyond the selected frame limit, exceeding the duration, or exceeding
the evidence threshold ends the session as a failure. `join()` timing out does
not mean the worker has stopped. `stop()` affects only its own process; wait for
successful shutdown before reusing device ownership. Workers cannot restart.

## Evidence

Each new output directory contains:

```text
input/
  live_input.json            # static manifest, distinct from sealed replay
  estimator_config.yaml
  kalibr_imucam_chain.yaml
  kalibr_imu_chain.yaml
  sensor_profile/            # copied, checksum-bound qualification evidence
  live_frames.jsonl          # offer/ack, pixel hashes, clock and batch identity
  images/                    # bounded temporary PNG spool
native/
  local_states.jsonl
  native.log
live_report.json
```

The terminal report records unacknowledged image paths, state/journal hashes and
the worker's terminal status. It is written on the worker thread immediately
before that thread returns; its thread-alive flag is therefore not a later
shutdown observation. Confirm `join()` and current `status()` as well.

Fixed configuration is checked before each native batch; native binary identity
and the five input/process/timing adapter source hashes are checked at startup
and finalization. Original source calibration changes do
not mutate a running session's copied inputs. Changing those copied inputs ends
the epoch rather than silently recalibrating.

## Verification And Remaining Work

The integration test feeds all 160 synthetic camera/IMU batches through this
adapter and compares native output byte-for-byte with the equivalent sealed
replay. It also tests source-buffer mutation, immutable learned tickets,
temporary-file release, wrong sensor/clock/calibration, input gaps, overrun,
native cleanup and finite session limits. This proves adapter execution on the
local Mac, not physical synchronization, aerial accuracy or Pi performance.

The finite sensor session now supplies single-owner camera/MAVLink collection
and bounded waiting for clock/IMU brackets. Remaining integration is actual
learned model/attitude/geometry processing of its image windows, native-health
invalidation of delayed alignment, and full on-Pi resource/thermal tests. The
existing shadow CLI and systemd service have not switched to this adapter.
No hardware settings, PX4 parameters or flight output were changed.

```bash
PYTHONPATH=src:tests \
VISION_NAV_OPENVINS_BINARY=research/outputs/openvins-native-build/vision-nav-openvins \
python -m pytest -q tests/test_native_live_worker.py tests/test_vio_stream_worker.py
```

Tests require the native research build; its GPL licensing review remains
separate from technical qualification. Missing artifacts cause explicit skips,
not a passed native execution claim.

September 5 verification: **85 native/live-input/capture/retention tests passed**
with the actual pinned executable, **61 paired/learned-replay tests passed**
including actual MegaLoc/XFeat/LighterGlue execution, and the main runner passed
**104 checks** in the geo-enabled environment. Existing upstream ONNX export
tracing/deprecation warnings remain; `git diff --check` passed.

The synthetic arriving-frame run is retained under the Git-ignored path
`research/outputs/native-live-input-tests-20260905-r2/test_actual_arriving_frames_ma0/live/`.
Run ID: `a799e0fd-85a1-4783-a9ce-388499980bfd`. All 160 frames were acknowledged,
peak temporary spool occupancy was one in the acknowledgement-paced test, and
zero images remained. Native output SHA-256 was
`8b0a31d21a2874457f845ce948039edb93bb1bea2b5b57f92a27c19a59114277`, identical
to the sealed reference run. Separate controlled-overflow tests fill the pending
queue and verify fail-stop cleanup. Neither test is physical live-sensor pacing
or a sustainable performance measurement.
