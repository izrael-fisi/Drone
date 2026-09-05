# Recorded Camera To Learned Map Replay

This workflow feeds original captured camera images through the exported
map-conditioned student. It does not require position labels, precomputed
model-input tensors, GNSS fixes, a connected Pixhawk, or a desktop app.

It is a model integration test, not a navigation release. Candidates can come
from a checksum-bound learned retrieval export/index or explicit operator
selection. Optional research-only XFeat/LighterGlue verification adds planar
consistency or DSM-backed camera-pose evidence. Optional paired replay runs native
local VIO concurrently on the same sealed camera/IMU recording. Navigation
fusion, navigation covariance and global position authority remain absent. All outputs retain
`NO_GLOBAL_FIX` and `position_authority: false`.
No MAVLink connection is created and no flight messages are emitted.

## Inputs

- A checksum-valid recorded capture with Picamera2 exposure-clock brackets,
  native same-source ATTITUDE, TIMESYNC observations, and measured camera-body
  extrinsics. See [capture timing](capture-timing.md).
- A current parity-passed map-conditioned ONNX export. See
  [student training and export](map-conditioned-student.md).
- The exact measured calibration YAML used for that model's calibration ID.
  An unchanged ID with changed YAML bytes is rejected.
- The capture's `ReferenceMapRelease`, including its original image/STAC assets.
- A tile layout generated from that reference imagery, with tile image hashes
  and exact pixel geometry. The map origin and imagery digest must agree.
- A compatible learned retrieval export/index, or one to four distinct candidate
  tile IDs from the layout chosen explicitly for diagnostic comparison.

The replay reads the capture in recorded order. Timing failures, poor image
quality and inference errors clear temporal context. Missing sequence numbers
or excessive frame-time gaps restart window accumulation. There is no padding
with repeated images or reuse of old context after a rejected frame.

## Run

Use Python 3.11 with the project's base, ONNX Runtime and PyProj dependencies.
PyTorch is needed to train/export the model, not for default ONNX replay.
Local verification can use either the PyTorch/Kornia reference or a
parity-checked XFeat/LighterGlue ONNX CPU export. Neither is a flight release;
the ONNX option runs without PyTorch, Kornia or the upstream checkout.

```bash
python -m pip install -e '.[neural-runtime,geo]'

python -m vision_nav.neural.map_conditioned_replay \
  --session /data/captures/prop-off-session \
  --export-dir /data/models/crossview-001 \
  --reference-map /data/reference/reference_map.json \
  --tile-layout /data/map-tiles/tile_layout.json \
  --calibration /data/calibration/down-camera.yaml \
  --candidate-tile tile_000000 \
  --candidate-tile tile_000001 \
  --output-dir /data/replays/prop-off-crossview-001
```

The equivalent installed command is `vision-nav-replay-map-conditioned-capture`.
For automatic selection, replace the two `--candidate-tile` arguments with
`--retrieval-export /data/models/retrieval-001 --retrieval-index /data/retrieval/area-001`
and optionally `--retrieval-top-k 4`. See
[learned retrieval setup](learned-retrieval-replay.md) for export/index commands.
The two selection modes are mutually exclusive; automatic retrieval never
falls back to manual candidates when it fails.

Add `--local-verification-config /data/local-verification.json` to verify
candidate tiles with the pinned learned local matcher. See
[local correspondence and geometry](learned-local-verification.md). This does
not enable position authority or any flight output.

Output must be empty and outside the capture folder. It contains:

| File | Content |
| --- | --- |
| `timing-audit.json` | Recomputed clock fit and exposure/native-attitude pairing |
| `trace.jsonl` | One decision per recorded frame, including rejection/warmup/model evidence |
| `summary.json` | Input/model/calibration hashes, candidate list, counters and processing latency |

`WINDOW_WARMUP` means the configured number of consecutive frames is not yet
available. `MODEL_EVIDENCE` contains per-candidate location/heading distributions
and no-match probability. These are uncalibrated model outputs, not accepted
fixes. `REJECTED` retains the reason and clears the frame window.

Exit code 0 requires at least one evaluated model window. Exit code 2 reports
startup rejection or a replay with no evaluable windows; inspect the trace
instead of assuming the model ran successfully. A completed replay can still
contain rejected frames. Never interpret its exit code as navigation accuracy.

## Input Parity And Identity

Training and replay use the same rectification, linear resize, BGR-to-RGB,
channel ordering and zero-to-one normalization implementation. The trainer pins
its source digest and each calibration-file digest. Export refuses checkpoints
without those bindings, and replay refuses an incompatible preprocessing
implementation. Older checkpoints need retraining with this input contract;
renaming a checkpoint or manually adding digest fields is not a valid migration.

The rolling window supplies roll/pitch, relative sensor times, measured camera
context and candidate-map scale/orientation. It does not read position labels,
GNSS coordinates, absolute ENU supervision, or absolute yaw as model inputs.
The selected map/capture IDs must agree. Use a separately designed wrong-map
benchmark for broader geographic false-fix evaluation; this command does not
bypass release identity checks.

Image quality is recomputed from decoded files rather than trusting old
metadata. Initial defaults reject Laplacian variance below 20 and saturated
fraction above 0.65. These are engineering smoke-test limits, not calibrated
flight-quality thresholds. The default maximum inter-frame gap is 1000 ms;
`--max-frame-gap-ms` may tighten it for a particular capture-rate experiment.

`decision_sha256_excluding_latency` allows repeated decisions to be compared
on the same software/provider environment. The trace-file digest includes
measured latency and will generally differ between runs. This is not a claim
of bitwise floating-point portability across hardware or provider versions.
Reported processing percentiles are replay timings, not end-to-end live Pi
deadlines, thermals or power measurements.

## Concurrent Native VIO Replay

This mode runs the existing learned pipeline while native VIO consumes the same
recording independently. First convert a capture using
[the camera/IMU converter](pi-capture-vio-replay.md). Both inputs must originate
from the same capture manifest and telemetry hashes. Native intrinsics,
distortion and camera-to-body transform must numerically match the learned
calibration; an identical calibration ID is not sufficient.

Add these options to the command above, retaining automatic retrieval and local
verification when measuring the full learned workload:

```bash
  --native-vio-binary /opt/vision-nav/bin/vision-nav-openvins \
  --native-vio-capture /data/vio-input/prop-off-session \
  --playback-speed 1 \
  --metrics-interval-s 0.2
```

Install `python -m pip install -e '.[runtime-metrics]'` for native CPU/RSS
measurement. Models and startup parity load before paced replay. This is a
steady-workload experiment, not cold startup, live ingress or flight acceptance.
The native binary remains a research build with separate licensing requirements.

- Every ordered IMU/camera batch enters native VIO. Recorded input waits on
  backpressure, without discarding IMU. Schedule lateness and submission wait are
  measured. Physical sensors cannot use this recorded-input backpressure policy.
- Learned inference selects one latest pending **complete consecutive window**.
  Superseded windows are counted; prefix images retain the same quality/timing
  checks. No repeated-frame padding or invented sensor context is introduced.
- Each trace row binds the original native anchor time and image digest,
  scheduled/offered/processing host times, worker identity and native progress
  before/after inference. Replay host clocks never replace original exposures.
- Native failure clears pending windows and raises an error. Cleanup stops only
  the owned process/threads. `native-vio/paired_report.json` remains available
  on early learned-replay exit. Runs cannot resume a broken sensor stream.

Additional output includes native states/logs and
`runtime_metrics-<run-id>.jsonl` with separate Python and child CPU/RSS evidence.
The summary adds per-stage P50/P95/P99, source frame count, selected/superseded
windows and measurement status. Failed stage attempts remain in latency samples.
Local correspondence and geometry are currently timed as one grouped stage.
Do not sum RSS readings as unique physical memory.

Here `counts.frames` means selected window anchors, not all source frames. The
decision hash includes nondeterministic scheduling/native observations. Exit
code 2 also reports incomplete native tracking or insufficient requested resource
evidence. A completed run can contain rejected learned observations.

Tests exercise native OpenVINS with synthetic sensor images/IMU and an actual
ONNX student trained on synthetic fixtures. Optional integration coverage runs
the real exported MegaLoc and XFeat/LighterGlue models concurrently as well.
This is not evidence of geographic localization, field-trained student accuracy,
Pi thermals or hardware synchronization. No VIO/map fusion or aircraft output is
enabled; every result remains `NO_GLOBAL_FIX` with no position authority.

### Delayed Result Retention

Every paired trace includes `concurrent_replay.clone_retention`: exact anchor
membership in the latest native clone window, posterior time, clone span,
worker identity, completed batch count and original native receipt time. The
batch count binds the observation to the corresponding line of
`native-vio/local_states.jsonl`; the separate `native_after` status may have been
sampled slightly earlier. Reading this diagnostic does not consume an estimator
snapshot or copy its covariance. A repeated read after EOF does not refresh the
receipt time.

Membership outcomes are `retained`, `before_retained_window`,
`processed_without_clone`, `not_yet_processed`, or `unavailable` with a reason.
`before_retained_window` does not prove the anchor ever became a clone: an
initialization frame may never have entered the state. Retained membership is
not a covariance check, accepted correction, freshness guarantee or permission
to fuse. Delayed alignment still requires the full joint posterior and its
measurement/epoch checks.

`summary.json` includes `clone_retention_counts` for all observations and the
`native_not_terminal` subset. The latter excludes results observed after the
native worker finished, failed or stopped. Inspect native receipt age and replay
schedule lateness too; a nonterminal process can still be stalled.

For a research history comparison, create a **new** native input with an explicit
override before running paired replay:

```bash
python -m vision_nav.vio.prepare_capture \
  --checkout research/vendor/open_vins \
  --session /data/captures/prop-off-session \
  --sensor-profile /data/calibration/native-vio-profile \
  --max-clones 24 \
  --output /data/vio-input/prop-off-session-clones24
```

The override accepts integers 2 through 32. Omission preserves the upstream
configuration (11 clones for the pinned setup). The selected value and override
are recorded in the converted input's source metadata, and its configuration
is checksum-bound. Existing captures and Pi defaults are not rewritten. The
synthetic, EuRoC and TUM VI preparation commands accept the same option.

At a steady 20 Hz with every frame cloned, 11/24/32 clones span approximately
0.50/1.15/1.55 seconds. Actual timestamps and initialization determine the real
span. This changes OpenVINS feature processing/marginalization, not just a log
cache. A 6N-by-6N joint covariance has quadratic storage growth; longer history
must be measured for tracking quality, latency, RSS and microSD writes before
being adopted on Pi. Do not silently increase it to conceal delayed results.

September 5 retention experiment (second run, other regression suites finished):

| Maximum clones | Learned windows | Retained before native termination | Retained after native termination | Native state log, decimal MB |
| --- | --- | --- | --- | --- |
| 11 | 9 | 0 | 1 | 2.79 |
| 24 | 10 | 0 | 2 | 9.97 |
| 32 | 10 | 1 | 2 | 14.99 |

All runs processed the same 160 synthetic camera batches at 2x playback with
actual MegaLoc/XFeat/LighterGlue exports and a tiny synthetic-trained student.
The first retained window can begin after an earlier anchor's exposure because
native initialization has not completed. In the 24-clone run, a later anchor
that was initialized also missed retention: its posterior advanced 1.30 seconds
against a 1.15-second retained span. Post-EOF retention is not sustained-loop
success. The 32-clone observation demonstrates membership, not accepted geometry
or successful delayed fusion. It does not establish a recommended setting.

Evidence is under the Git-ignored directory
`research/outputs/clone-retention-workload-20260905-r2/`, test directories ending
`test_actual_megaloc_xfeat_ligh0`, `1`, and `2` respectively. Native run IDs are
`9ab54352-2e21-4ff2-9f39-524c3025aec6`,
`197eab75-a998-425a-a68d-6e3a52b40262`, and
`54d257c1-3feb-4f36-bfbb-eb2567c39dd1`. The first repeat, `r1`, overlapped other
regression work and is retained as integration evidence, not a controlled timing
comparison. Neither run is a Pi, long-duration, flight or localization benchmark.

Focused retention/preparation tests passed **43 tests** (including actual native
11/24/32-clone histories). The estimator/native regression selection passed
**166 tests**. All **4 paired ONNX/native integration tests** passed; repeating
the full-model selection passed **3 tests**. Serial learned/retrieval regression
checks passed **35 tests**. Main unit checks passed **104** in
the geo-enabled environment; an earlier estimator-environment main run skipped
the two Rasterio-dependent checks. Upstream ONNX tracing/deprecation warnings
remain. No native binary, live input path, flight output or Pi default changed.

## Remaining Integration

- Official MegaLoc checkpoint accuracy and retrieval preprocessing evaluation;
  recovery/keyframe scheduling on the live Pi instead of retrieval at each
  complete replay window.
- Sustained Pi qualification of the exported learned models; real UAV/map
  accuracy and pose uncertainty calibration.
- Live camera/clock service binding and physical sensor-delay calibration.
- Continuous local visual-inertial odometry and temporal hypothesis fusion.
- Real-data accuracy, false-fix, covariance and Pi thermal/latency benchmarks.
- Official teacher evaluation and student distillation.

## Tests

```bash
python -m pytest -q tests/test_map_conditioned_replay.py tests/test_map_conditioned_student.py
python tests/run_unit_tests.py
```

For paired execution tests, set `VISION_NAV_OPENVINS_BINARY` to the built native
executable and run `tests/test_paired_replay.py` and
`tests/test_concurrent_learned_replay.py`. Also set
`VISION_NAV_RETRIEVAL_EXPORT` and `VISION_NAV_LOCAL_EXPORT` to parity-passed
MegaLoc and XFeat/LighterGlue export directories to exercise those actual models.
Missing optional artifacts cause the corresponding integration tests to skip;
a skipped test is not model/native execution evidence.

September 5 local verification: **59 model/paired-replay tests passed**, including
the actual MegaLoc/XFeat/LighterGlue/native test. **154 estimator/native tests**
passed separately in the GTSAM environment, and the main runner passed **104
checks**. An initial mixed-environment run failed one existing GTSAM-dependent
test because that dependency was absent; it passed in the estimator environment.
The exporter still emits upstream deprecation/tracing warnings.

The full-workload synthetic run is retained under
`research/outputs/concurrent-learned-replay-tests-20260905-r2/test_actual_megaloc_xfeat_ligh0/concurrent/`
(ignored by Git), run ID `0058b753-6ed0-4799-b7c5-f7c7d342a19b`.
At 2x recorded playback on the Mac it processed all 160 native camera batches,
produced 48 local-estimate records after initialization, and selected 11 learned
windows while superseding 148. There were 83 resource samples, 69 with available
native-process measurements. Learned processing P50/P95/P99 was approximately
413/452/466 ms for these 11 windows. These short-run timings include a synthetic
64-pixel camera, two synthetic map candidates and an unqualified tiny student;
they must not be used as Pi budgets or localization performance claims.

Tests train a tiny synthetic student, export it to ONNX, then replay original
image files against generated georeferenced map tiles. They verify input parity,
repeatability, GPS independence, calibration mismatch rejection, bounded windows,
bad-image/gap handling, and provider failure traces. They do not demonstrate
real-world localization accuracy or hardware synchronization. The additional
`tests/test_replay_retrieval.py` suite exports a small synthetic embedding model,
builds its map index, and tests automatic selection plus student replay without
claiming MegaLoc accuracy. Replay and indexing require no PyTorch imports.
