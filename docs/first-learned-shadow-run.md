# First Learned Shadow Run

This procedure proves the Pi can consume one approved camera frame through the
learned global-retrieval and local-correspondence stages. It is a diagnostics
run only. It must never arm the vehicle or publish PX4 external vision.

## Required Inputs

Prepare these real, non-placeholder inputs before running the procedure:

```text
model_release/
  model_release.json
  models/megaloc.onnx
  models/xfeat.onnx
  models/lighterglue.onnx
  models/pi5_crossview_nav_v1.onnx

map_release/
  map_release.json
  imagery/map.tif
  index/megaloc.npz                     # default portable exact cosine index
  # or index/megaloc.faiss after Pi FAISS benchmark approval
  index/megaloc_tiles.json
  index/tiles.sqlite
  index/xfeat_tiles.json
  index/xfeat_tiles.sqlite
  features/xfeat/<tile-id>.npz
  ... other MapRelease assets

calibration/
  down_camera.yaml
```

The calibration file must have `calibration_status: measured`, its
`calibration_id` must appear in both releases, and the test frame must use its
native calibrated resolution.

`megaloc_tiles.json` follows
`vision_nav_retrieval_index_v1`. `xfeat_tiles.json` follows
`vision_nav_local_feature_cache_v1`. Both are release-bound and checksum
protected; templates are under [`research/`](../research/).

## Validate First

On the Pi, from the repository root:

```bash
VISION_NAV_MAP_RELEASE=$HOME/drone-data/map_release/map_release.json \
./scripts/pi/validate_learned_map_assets.sh

VISION_NAV_MODEL_RELEASE=$HOME/drone-data/model_release/model_release.json \
VISION_NAV_MODEL_ARTIFACT_ROOT=$HOME/drone-data/model_release \
./scripts/pi/validate_neural_model.sh

VISION_NAV_MAP_RELEASE=$HOME/drone-data/map_release/map_release.json \
VISION_NAV_MODEL_RELEASE=$HOME/drone-data/model_release/model_release.json \
VISION_NAV_CALIBRATION_ID=down-camera-v1 \
./scripts/pi/validate_neural_runtime.sh
```

The Pi requires ONNX Runtime for the learned model stages. `faiss-cpu` is
required only for a MapRelease that explicitly declares a FAISS retrieval
index; the default `numpy_flat` index does not need it. A failed checksum,
release mismatch, missing logical tensor contract, or unavailable provider is
a stop condition.

`validate_learned_map_assets.sh` is intentionally a deeper preflight than
normal startup: it reads the selected retrieval index, both SQLite catalogs,
every XFeat feature archive, and every referenced tile image. It must pass
after a map release is transferred to the Pi and before a shadow test. The
portable `numpy_flat` index does not require FAISS; a FAISS release does.

## Run One Saved Frame

```bash
VISION_NAV_MAP_RELEASE=$HOME/drone-data/map_release/map_release.json \
VISION_NAV_MODEL_RELEASE=$HOME/drone-data/model_release/model_release.json \
VISION_NAV_MODEL_ARTIFACT_ROOT=$HOME/drone-data/model_release \
VISION_NAV_CALIBRATION_ID=down-camera-v1 \
VISION_NAV_CALIBRATION_FILE=$HOME/drone-data/calibration/down_camera.yaml \
VISION_NAV_SHADOW_FRAME=$HOME/drone-data/test-frame.jpg \
./scripts/pi/run_vision_nav_shadow.sh --once
```

Read `runtime_status.json` under `VISION_NAV_OUTPUT_DIR`.

## Expected Evidence

For a complete learned shadow probe, verify all of the following:

- `compatibility.compatible` is `true`.
- `shadow_mode` is `true` and `px4_publication.enabled` is `false`.
- `inference.frame_probe.stages` shows executed `global_retriever` and
  `local_features` stages.
- `position.top_k_candidates` has one or more release-bound tile IDs.
- `local_matcher` executed against the first retrieved tile.
- `correspondence_evidence.status` is `available`, with its declared threshold,
  valid-pair count, and accepted-score count recorded.
- `position.navigation_mode` remains `NO_GLOBAL_FIX`.

When the map cache includes index-aligned 3D landmarks and the matcher declares
`pnp_3d`, `geometry_evidence.camera_position_enu_m` may appear in the trace.
This is a camera-pose candidate for offline review only. Do not interpret it as
a flight estimate until camera-to-body extrinsics, temporal fusion, and
held-out field validation have passed.

The runtime may additionally report `shadow_tracking_candidate` after three
consecutive, time-contiguous PnP candidates for the same tile with no excessive
position jump. The candidate bank additionally requires an aligned, executed
temporal-student window with finite learned location/heading evidence. Neither
the candidate nor `temporal_student_evidence` is a source of position for the
aircraft or PX4; both remain uncalibrated shadow evidence.

It is normal for `temporal_student` to be skipped in this procedure. It needs a
release-defined frame/attitude window and is a separate next-stage integration.

## Live Temporal Variant

When the temporal model export declares `frame_window_rgb` and
`attitude_window_rpy` inputs with the same `window_size`, run the attached
camera and read-only Pixhawk ATTITUDE stream together:

```bash
VISION_NAV_CAMERA_BACKEND=auto \
VISION_NAV_MAVLINK_ENDPOINT=serial:/dev/ttyACM0:921600 \
VISION_NAV_CALIBRATION_FILE=$HOME/drone-data/calibration/down_camera.yaml \
./scripts/pi/run_vision_nav_shadow.sh
```

Camera timing now comes from Picamera2's sensor metadata, converted from Linux
BOOTTIME to host MONOTONIC using the recorded clock brackets. Inference start,
file creation and processing completion are never substituted for capture time.
This is a first-row exposure-start API observation, not calibrated full-frame
timing or rolling-shutter compensation. See [capture timing](capture-timing.md).

ATTITUDE still uses host receipt time and is explicitly marked unqualified.
The nearest sample must be within `VISION_NAV_MAX_ATTITUDE_FRAME_DELTA_MS`; this
is a shadow-context check, not synchronized raw-IMU input for VIO. Full temporal
windows remain unqualified for position authority.

The live loop records `capture_metadata`, `capture_timing` and `result_timing`
under `inference.frame_probe`. `VISION_NAV_MAX_FRAME_AGE_MS` (default 1000)
limits capture-to-inference and capture-to-result age. Unavailable, future or
over-age timing withholds temporal/candidate evidence and breaks consecutive
support. Single-frame image-stage diagnostics may still run without timing,
but cannot establish a temporal tracking candidate. Saved images and
`rpicam_still` captures without sensor metadata never receive an invented time.

Missing, duplicated, reversed or unaligned frames clear the temporal history;
it cannot reuse a previously full window for a rejected frame. A gap exceeding
`VISION_NAV_MAX_TEMPORAL_FRAME_GAP_MS` (default 1500) starts a new warm-up window.
These limits are configurable research bounds, not flight acceptance thresholds.
They require adjustment only from measured cadence/model assumptions, not merely
to make a slow run report ready.

### Independent Live Capture

Continuous live runs now create, use and close the camera in a dedicated worker.
The inference thread takes the newest waiting frame; it never asks the producer
to wait for inference. `VISION_NAV_SHADOW_CAPTURE_INTERVAL_S` (default 0.5)
sets the requested capture interval independently of the status-loop interval.
It is a minimum cadence interval, not a guaranteed frame rate: capture itself
may take longer. Saved-frame and `--once` diagnostics retain their serial path.

The pending capacity is one. While inference works, a newer completed capture
replaces the waiting one. `camera_health.capture_worker` reports captured,
superseded and delivered counts, pending count, in-progress capture and errors.
It also reports `memory_capture_count`, `file_capture_count` and
`pending_memory_bytes` so the actual handoff path is observable.
Source timestamps and camera metadata survive the handoff unchanged; sequence
numbers in the status loop count processed/status iterations, not camera frames.

Picamera2 copies its BGR array before releasing the native camera request and
hands off an owned, read-only buffer. Superseded frames are released without JPEG
encoding or microSD writes. Only the selected frame is encoded and saved, outside
the capture handoff lock. Quality metrics for this path describe the original
pixels, not the lossy saved JPEG. Metadata serialization excludes image buffers.

Each worker has a private spool under the frame evidence folder. File-only camera
backends still save every capture and remove superseded pending files there.
Frames selected for inference are retained under unique names without overwriting
prior evidence. Encoding or persistence failures are raised, not counted as
delivered frames. Shutdown removes only
that worker's spool and preserves consumed frames. It waits up to two seconds;
if a camera call stays blocked, it reports shutdown failure rather than claiming
the thread stopped or closing the camera from another thread.

This is still a still-capture shadow path, with JPEG files delivered to inference.
Each pending buffer is limited to 64 MiB; one selected frame and an in-progress
capture can coexist with it. This is not a total-process memory limit: encoding,
camera, quality metrics and model work also allocate memory. Consumed evidence
and logs accumulate and need operator retention management. Measure memory and
microSD writes on the Pi before increasing cadence. A shared image/raw-IMU frontend,
delayed correlated-state retention and
real Pi throughput/thermal measurements remain separate work. The worker is not
a live OpenVINS integration or evidence of flight-ready navigation.

### Independent Attitude Context

When a MAVLink endpoint is configured for a live run, a dedicated reader owns
connection creation, reads and closure. It remains active during inference and
also reports attitude health when release validation prevents frame inference.
It does not transmit MAVLink messages, request stream rates or change parameters.
The Pixhawk must already be streaming `ATTITUDE` on that link. A `--once` snapshot
may still report `starting` or `waiting` before the first sample arrives.

The expected **incoming** sender defaults to system 1, component 1. Set
`VISION_NAV_MAVLINK_TARGET_SYSTEM` and `VISION_NAV_MAVLINK_TARGET_COMPONENT` to
the IDs verified on your hardware. These are not the Pi's outgoing source IDs.
Anonymous samples and other senders are excluded. Expected-sender samples require
finite roll/pitch/yaw and monotonic PX4 boot timestamps. Duplicates, reordered
timestamps or invalid data break temporal support. After a vehicle reboot, restart
the runtime to establish a new clock history; it does not silently rebase time.

Status packets contain `attitude_health`, including:

- `status`: starting, waiting, receiving, stale, invalid, failed or stopped.
- Accepted, rejected, filtered-sender and buffer-overflow counts.
- The latest valid sender, attitude and original vehicle timestamp.
- Age of the last accepted host-received sample and the configured freshness limit.
- A discontinuity generation and reason, connection errors and worker liveness.

The receive buffer holds at most 512 samples. Overflow discards the oldest sample
and increments the discontinuity generation. `VISION_NAV_ATTITUDE_STALE_AFTER_MS`
defaults to 500 ms; the separate nearest-frame pairing limit still defaults to
50 ms. These are provisional shadow diagnostics, not qualified flight thresholds.
Reader failure, invalid context, reception gaps and overflow clear cached attitude
and temporal frame support. The runtime checks health and generation again after
inference; a disconnect or overflow during inference withholds both the temporal
student evidence and shadow tracking candidate. Old frame timestamps cannot be
reused after clearing context.

If a read/connect operation blocks beyond the one-second shutdown wait, the
runtime reports incomplete shutdown instead of closing the link from another
thread. There is no automatic reconnect or sender discovery. Continuous runs now
report this health on an independent status heartbeat, described below.
`receiving` means usable provisional ATTITUDE context, not healthy GPS, a qualified
raw-IMU stream, clock synchronization, or permission to publish a position.

The deterministic tests in `tests/test_attitude_collector.py` and
`tests/test_shadow_attitude.py` cover sender filtering, overflow, timing faults,
staleness, thread ownership and disconnects before/during a shadow inference call.
They use controlled telemetry and model probes, not an aircraft or an accuracy
benchmark. Actual Pixhawk stream cadence, serial behavior and Pi load remain
hardware validation work.

### Independent Status Heartbeat

After release/model initialization, continuous runs publish a compact
`vision_nav_runtime_status_v3` heartbeat independently of inference. This worker
alone writes `runtime_status.json` and sends status UDP packets. The main thread
still appends full, completed decisions to `decision_trace.jsonl`; detailed
correspondences and probe output are not repeated on every heartbeat. `--once`
keeps the full single-decision snapshot behavior.

Set `VISION_NAV_HEARTBEAT_INTERVAL_S` (default 0.5 seconds) and
`VISION_NAV_INFERENCE_WARNING_MS` (default 2000 milliseconds) for the shadow run.
These are requested scheduling/warning settings, not guaranteed real-time rates
or flight acceptance limits. Each heartbeat includes:

- `emission_kind="heartbeat"`, a per-process `heartbeat.run_id` and its own
  increasing `sequence`. Decision-trace sequence numbers remain inference iterations.
- Current phase: waiting for a frame, preprocessing, inference, evidence writing,
  idle or shutdown, plus phase age and an `inference_overdue` flag.
- Live camera-worker and read-only attitude health, even while inference waits.
- A compact `inference.last_completed_result` with its original result sequence,
  completion time, capture time when available, both ages and the trace location.
- A diagnostic capture-age flag and any previous UDP transport error.

Heartbeat position remains `NO_GLOBAL_FIX` with publication disabled. No candidate,
position or heading from the previous decision is replayed as current evidence.
An updated heartbeat time is **not** a new camera exposure or a new localization
result. The last completed result stays explicitly aged. Its full evidence is
in `decision_trace.jsonl`, not `inference.frame_probe` on heartbeat packets.

Snapshots use same-directory atomic replacement so readers do not see half a JSON
document. Heartbeats must fit within 60,000 serialized bytes; oversized packets
fail the publisher rather than silently dropping provenance. Local snapshot
updates continue after a UDP error, which is recorded on the following heartbeat.
A local write/publisher failure is raised when the main loop next regains control.
The Pi status-read script now prints phase, file age, overdue state, attitude
health and last-result age separately.

This is a Python thread, **not** an independent hardware/process watchdog or an
inference cancellation mechanism. A native operation holding the Python GIL,
blocked filesystem/name resolution, process crash, power loss or severe CPU
contention can still stop/delay updates. Startup release hashing/model loading is
not yet covered. Consumers must detect missing/stale heartbeats; UDP is best
effort and large datagrams may fragment. The sender socket has a bounded send
timeout, but hostname resolution has no separate hard deadline. Prefer a known
local IP during bench tests. Shutdown waits up to two seconds for the publisher
and reports incomplete shutdown if it remains blocked. Atomic replacement is not
a claim of power-loss durability.

Tests verify advancing heartbeats and continued camera capture while a model
probe is blocked, separate full decision traces, no stale-position replay,
atomic replacement/failure handling and real loopback UDP delivery. These are
software tests, not sustained Pi timing/thermal or flight evidence.

For optional process CPU/RSS/temperature/I/O and capture-counter recording during
the same live run, see [live load recording](live-runtime-load-recording.md).

## Pi Camera Variant

After the saved-frame run is clean, substitute the attached camera:

```bash
VISION_NAV_CAMERA_BACKEND=auto \
VISION_NAV_CALIBRATION_FILE=$HOME/drone-data/calibration/down_camera.yaml \
./scripts/pi/run_vision_nav_shadow.sh --once
```

The camera frame is written below `VISION_NAV_OUTPUT_DIR/frames`. Keep the
vehicle prop-off throughout camera, model, and MAVLink bench testing.

## Benchmark The Target Pi

After the saved-frame shadow probe succeeds, collect a real performance report
on the Pi. Put at least three sharp, native-resolution frames from the measured
downward camera in one directory. They must use the active MapRelease's camera
configuration and calibration.

```bash
VISION_NAV_MAP_RELEASE=$HOME/drone-data/map_release/map_release.json \
VISION_NAV_MODEL_RELEASE=$HOME/drone-data/model_release/model_release.json \
VISION_NAV_MODEL_ARTIFACT_ROOT=$HOME/drone-data/model_release \
VISION_NAV_CALIBRATION_ID=down-camera-v1 \
VISION_NAV_CALIBRATION_FILE=$HOME/drone-data/calibration/down_camera.yaml \
VISION_NAV_BENCHMARK_FRAMES_DIR=$HOME/drone-data/benchmark-frames \
./scripts/pi/benchmark_learned_runtime.sh
```

The result is written to
`$HOME/DroneTransfer/outgoing/learned-runtime-bench/learned_runtime_benchmark.json`.
It deep-validates immutable map assets first, warms ONNX sessions, then records
P50/P95/P99 end-to-end and per-stage latency, process RSS, CPU utilization,
available Pi thermal readings, failed frames, and missing learned stages. It
never opens a MAVLink link and reports `px4_external_vision: disabled`.

This saved-frame bench intentionally isolates inference and map-access cost. Its
`capture_drop_measurement` is marked `not_measured_saved_frame_benchmark`; use
the camera capture bench and a later live-camera runtime run to establish real
capture cadence and dropped-frame evidence.

The default evidence limits are three completed frames, 1,000 ms P95, 2 GiB
RSS, and 80 C. They are starting gates, not flight-clearance limits. A missing
thermal sensor is a warning; an observed temperature over the limit, a failed
stage, a missing required role, or too few frames fails the report. Attach the
JSON report to a support bundle and review it with ONNX parity and deterministic
replay before promoting a model.
