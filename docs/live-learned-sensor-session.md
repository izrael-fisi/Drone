# Live Learned Sensor Session

This finite, opt-in prop-off experiment connects the single-owner camera/IMU
session to learned image retrieval, the map-conditioned temporal student, and
optional XFeat/LighterGlue geometric verification. It runs inference on the
actual immutable buffers submitted to native VIO. It does not replay a file
as though it were a live sensor, open a second camera, or open a second serial
connection.

The command now uses an [isolated model process](learned-inference-process.md)
with bounded startup/inference waits. The original in-process implementation
remains a reference for tests, not a CLI fallback.

**Not flight navigation:** native local VIO and learned map evidence are not
fused by default. An optional [research observer](live-research-alignment.md)
can audit bindings or compute conditional joint alignment with explicitly
supplied uncertainty. Results remain `NO_GLOBAL_FIX`, `position_authority=false`, and
`px4_external_vision=disabled`. A model or geometric candidate is not a published
global pose. The existing systemd service is unchanged.

## Requirements

- All measured sensor-profile, exclusive device ownership and prop-off
  requirements from [live VIO collection](live-vio-sensor-session.md).
- Matching measured camera intrinsics and camera-to-body extrinsics. The native
  profile and learned calibration must agree numerically, not just by ID. This
  learned binding currently supports radtan cameras; an equidistant profile
  can remain valid for native VIO but is rejected here.
- PX4 `ATTITUDE` from the same selected sender as raw IMU and TIMESYNC. Missing,
  invalid, reordered or stale attitude blocks learned windows, not raw-IMU VIO.
- A parity-passed map-conditioned ONNX student trained with
  `exposure_native_attitude_v1`, its exact calibration file, a reference map,
  and corresponding tile layout.
- A [learned retrieval export and index](learned-retrieval-replay.md).
  Live candidate selection is image-based; there is no operator-tile or GPS
  fallback in this command.
- Optionally, a verified [local ONNX export](learned-local-onnx.md) and
  `local-verification-onnx` configuration, including elevation provenance when
  using surface-backed PnP. Without elevation, local geometry only tests planar
  consistency and does not recover a metric body pose.

The shared preprocessing implementation now accepts in-memory pixels. Its output
is regression-tested against lossless disk decoding, but the source checksum
changed. Re-export the map-conditioned student under the updated implementation;
do not edit an older export's recorded checksum to bypass compatibility checks.
This does not require retraining unchanged weights merely to exercise export
parity, and does not qualify those weights for flight.

## Run

Use actual measured paths and a new output directory:

```bash
vision-nav-live-learned-bench \
  --binary /opt/vision-nav/bin/vision-nav-openvins \
  --checkout /opt/research/open_vins \
  --sensor-profile /data/calibration/x500-vio-profile \
  --endpoint serial:/dev/ttyACM0:115200 \
  --calibration /data/calibration/down_camera.yaml \
  --camera-to-body /data/calibration/camera_to_body.yaml \
  --export-dir /data/models/map-conditioned-student \
  --reference-map /data/maps/test-area/reference_map_release.json \
  --tile-layout /data/maps/test-area/tiles/tile_layout.json \
  --retrieval-export /data/models/megaloc-export \
  --retrieval-index /data/maps/test-area/retrieval \
  --local-verification-config /data/models/local-verification.json \
  --frames 120 --interval-s 0.05 \
  --record-images --max-image-bytes 1073741824 \
  --output /data/bench/live-learned-001 \
  --propellers-removed
```

Module entrypoint: `python -m vision_nav.neural.live_sensor_inference`.
Omit `--local-verification-config` to test only retrieval and the temporal
student. Omit `--record-images` only when durable image replay is not needed.
The model sets the required window length; the session rejects incompatible
lengths. The finite session has a 60-second native duration limit. No service is
stopped for you, and no vehicle stream-rate or parameter writes are performed.

Optional `--alignment-config /data/research/alignment.json` enables research
binding audits by default. It does not install a noise model or enable a global pose.
An explicit `--alignment-noise-provider` loads trusted research noise in a
deadline-controlled [estimator child process](research-alignment-process.md).
See the observer guide for the configuration contract and provider API.

With continuous isolated alignment, `--retain-query-anchors` enables
[automatic pose ownership](learned-query-anchors.md) before model dispatch.
It requires image recording and local verification, uses four native slots,
and preserves accepted evidence under a separate original sensor-time horizon.
Admission failures skip learned work with an explicit reason, never VIO input.

## Ownership And Timing

The camera and MAVLink owners continue feeding the ordered VIO queue while a
separate process runs learned inference. A slow consumer gets the newest complete window;
superseded windows are counted. This policy does not drop VIO frames or turn
correlated local posterior poses into independent motion factors.

For each dispatched frame the buffer saves exposure-clock conversion and a
nearest native-timestamp `ATTITUDE` sample. Its time difference plus clock
uncertainty and millisecond message quantization must fit the existing 25 ms
training-context envelope. The buffer retains at most 256 auxiliary attitudes.
Selected-sender malformed or reordered attitude latches a learned-context fault
for the session. Other senders and GPS cannot supply model attitude or location.

This is the existing exposure/native-attitude training convention. The native
camera/IMU delay is logged separately and remains applied once by native VIO;
it is not silently applied again to model inputs. The nearest-sample envelope
does not establish physical camera/IMU calibration or compensate for airframe
vibration, rolling shutter, or unmeasured driver timing.

Every selected window checks worker UUID, sequence, image hash, calibration,
context ownership and consecutive exposure times. The consumer measures image
quality, runs shared preprocessing, retrieves at most four map candidates, and
runs the same ONNX student and local verification used by recorded replay.
Returned model failures clear the current learned window. A process timeout or
protocol failure disables inference for the rest of the session without
restarting it or stopping otherwise healthy VIO. Session failure during
inference vetoes its result even if the model returned successfully.

## Evidence

```text
live-learned-001/
  sensors/                  # normal single-owner session and optional images
  learned/trace.jsonl        # selected windows, timings, stages and rejections
  learned/summary.json       # source/release hashes, counts and latency quantiles
```

Trace records carry the original frame identities, exposure contexts, retrieval
candidates, learned outputs, optional geometric evidence, and native progress
before/after inference. Clone-retention diagnostics show whether the anchor
still appears in a native posterior window; they do not authorize fusion.
The evidence is bounded to 4 MiB per decision and 256 MiB per trace. Training
export can use the completed `sensors/` recording through the existing exporter;
it does not substitute learned predictions for missing reference labels.

Exit 0 requires a completed session and at least one model-evidence window.
It does not require or establish local VIO initialization, geometric acceptance,
global lock, field-qualified timing, useful learned accuracy, or Pi performance.
Read `source_session`, `model_windows`, `rejected_windows` and each reason.

## Remaining Work

- Fuse qualified asynchronous corrections into a current global estimate with
  ambiguity, uncertainty and correlation handling.
- Qualify process isolation, deadlines and termination behavior on the actual
  Pi before adopting this finite experiment as a long-running service.
- Measure concurrent CPU threads, end-to-end delay, retained clones, memory,
  temperature, dropped frames and microSD write load on the actual Pi.
- Validate on held-out X500 captures with measured timing, mount calibration,
  image quality and independent reference. Synthetic tests are not that evidence.
- Complete the separately reviewed PX4 integration; no aircraft output is enabled
  by this workflow.

## Verification Evidence

September 5, 2026, local macOS CPU research environment: 112 tests passed across
live learned inference, map-conditioned replay, live sensor buffering/ownership,
recorded training export and recorded native replay export. All 104 checks in
`tests/run_unit_tests.py` passed. CLI help and `git diff --check` passed.

The full-model test used the existing MegaLoc and XFeat/LighterGlue ONNX exports,
a synthetic-trained temporal student, synthetic device interfaces and the actual
pinned native OpenVINS process. The recorded run processed all 160 ordered
camera/native batches, completed local initialization, evaluated 23 learned
windows and explicitly superseded 136 pending learned windows. None of the
23 selected windows was rejected; this means successful model execution, not
23 valid global position fixes. The local verifier used planar-consistency mode.
Measured selected-window P50/P95/P99 execution times were approximately
407/421/450 ms on this host, not Pi or camera-to-flight-output latency.

Evidence directory (generated, not committed):
`research/outputs/live-learned-tests-20260905-r2/test_actual_retrieval_local_an0/`.
Fault checks cover missing/stale/foreign/reordered attitude, wrong frame identity,
changed pixels/context, duplicate windows, returned provider failures and a
sensor failure while inference is in progress. An additional final-release
verification gate rejects changed configuration even during session shutdown.
After that final gate was added, all 22 live-inference tests passed again with
the actual retrieval/local exports enabled (`live-learned-tests-20260905-r3`).
