# Live Runtime Load Recording

This opt-in recorder measures the **actual shadow runtime process** while its
camera, read-only attitude collector, model inference, heartbeat and evidence
writes operate together. It complements saved-image benchmarks; it does not
replace model parity, navigation accuracy evaluation or a sustained Pi acceptance
test. It does not publish to PX4 or change any vehicle settings.

## First Pi Run

Use the measured camera calibration and compatible learned releases from
[the shadow-run guide](first-learned-shadow-run.md). Perform the first check with
propellers removed. Keep the normal frame-age and compatibility gates intact.

```bash
VISION_NAV_CAMERA_BACKEND=picamera2 \
VISION_NAV_CALIBRATION_FILE="$HOME/drone-data/calibration/down_camera.yaml" \
VISION_NAV_MAVLINK_ENDPOINT=serial:/dev/ttyACM0:921600 \
VISION_NAV_OUTPUT_DIR="$HOME/drone-data/shadow" \
VISION_NAV_RECORD_RUNTIME_METRICS=1 \
VISION_NAV_METRICS_INTERVAL_S=1 \
VISION_NAV_METRICS_MAX_SAMPLES=3600 \
./scripts/pi/run_vision_nav_shadow.sh
```

The map/model paths and calibration ID still come from the existing environment
configuration. This command does not create missing models, calibration or maps.
Do not combine recording with `--once`. Stop an interactive run with Ctrl+C;
normal cleanup closes the recorder and appends a footer. A power loss or forced
kill may leave an incomplete log, which must not be treated as a completed test.

From another terminal:

```bash
./scripts/pi/read_runtime_status.sh
```

This prints recorder state, latest sample age, process CPU load, memory,
temperature when available and the evidence path. An unchanged sample with
increasing age is not a fresh measurement, even if the heartbeat still advances.

## Evidence

Each run writes an exclusive `runtime_metrics-<run-id>.jsonl` in the configured
shadow output directory. Existing files are never overwritten. The same run ID
appears in `heartbeat.run_id` and `runtime_run_id` on new decision-trace records.

- Header: hardware/OS identity, process ID, measurement scope, interval and limit.
- Samples: monotonic observation time, probe duration, actual sampling interval,
  process CPU time/percentage, RSS, temperature, throttle flags, process I/O,
  inference phase, camera capture/supersede/delivery counters and attitude health.
- Footer: run ID, sample count and stop/limit state.

The recorder holds one latest sample in memory and permits at most 36,000
configured samples, each bounded to 64 KiB. The default 3,600 samples at one-second
intervals are approximately one hour, not a guaranteed duration. Reaching the cap
sets `sample_limit_reached` and stops measurement, **not** the drone runtime.
Logs from separate runs accumulate and need normal retention/storage management.
There is no automatic deletion or unbounded in-memory sample history.

Detailed capture-to-result ages and per-stage model latency remain in
`decision_trace.jsonl`, once per processed frame. Resource sampling can miss
short spikes and must not substitute for per-frame latency statistics. Link the
files by run ID; do not count each heartbeat as an additional inference result.
The resource log does not itself claim P50/P95/P99 or a pass/fail benchmark verdict.

## Generate A Load Report

Stop the recording first. Use the exact metrics filename printed by the status
command, not a wildcard that could accidentally join different runs. Substitute
your run ID and output directory in this example:

```bash
./scripts/pi/report_runtime_load.sh \
  --metrics "$HOME/drone-data/shadow/runtime_metrics-<run-id>.jsonl" \
  --decisions "$HOME/drone-data/shadow/decision_trace.jsonl" \
  --output "$HOME/drone-data/shadow/load-report-<run-id>.json"
```

The wrapper uses `VISION_NAV_PYTHON`, defaulting to
`$HOME/drone_vision_nav_venv/bin/python`. It also works on a development computer
with the runtime environment; it opens no camera, vehicle or network connection.
The installed equivalent is `vision-nav-report-runtime-load` with the same
arguments. Omitting `--decisions` produces a resource-only, explicitly incomplete
report, useful for diagnosing a run that did not reach inference.

Output uses `vision_nav_live_load_report_v1` and records:

- Exact input file paths, byte counts, SHA-256 digests and runtime run ID.
- Capture-to-result P50/P95/P99/max, recomputed from recorded monotonic clocks.
  Stale/rejected results with valid clocks remain in these latency statistics.
- Per-stage reported latency and executed-stage counts. A mocked or skipped
  stage is not proof of running the full learned pipeline.
- Sampled CPU, RSS, temperature, sampling intervals and probe-duration
  distributions. CPU is recomputed from cumulative counters.
- Current throttle sample counts, the first observed since-boot flags and new
  latched flags after that baseline. The baseline is not necessarily before the
  workload began; these fields do not establish exactly when a fault occurred.
- Camera counter changes, with intentional latest-frame supersedes separated
  from unknown hardware drops; available kernel process-I/O interval totals.
- Sampled attitude-reader states, receive ages, sender IDs, counter changes and
  observed discontinuity reasons. Repeated reason samples are not separate
  discontinuity events; the generation counter tracks context breaks.

`capture_to_result_ms` covers all matching decisions in the supplied trace.
`within_resource_window_capture_to_result_ms` and its stage-latency counterpart
cover only frames whose **entire capture-to-result interval** falls between the
first and last resource observations. The report counts other/unverified
intervals separately. This matters when measurement reaches its cap but the
runtime keeps running. Even in-window statistics show co-observation, not proof
that a particular resource caused a slow result.

The reporter accepts only completed decision records from the metrics run ID,
never heartbeat repeats or unbound older traces. It rejects reordered/duplicate
sequences, contradictory clocks, mixed release identities, active publication,
counter resets and malformed JSON. Missing observations and decision sequence
gaps are listed in `evidence_gaps`. A stable partial metrics file without a footer
can be summarized as incomplete; malformed/truncated JSON must not be silently
discarded to manufacture a complete result.

Inputs must remain unchanged while reporting and be at most 512 MiB each, with
up to 36,002 metrics records (64 KiB per record) and 100,000 decision records
(16 MiB per record). Larger traces need a separately preserved, run-specific
export; the tool does not truncate them. It hashes the supplied files, not an
assumed source dataset. Existing report output may be replaced atomically, but
an input evidence path cannot be used as output.

Exit code 0 means the report was written, **not that performance passed**.
`evidence_status="summarized_measurements"` means the report's measurement fields
were present, not that duration, tail latency, temperature or navigation accuracy
met an acceptance threshold. Short sample sets still produce descriptive
interpolated quantiles; they are not statistically qualified tail estimates.
Unavailable readings remain null. No raw-IMU rate, camera/IMU synchronization,
physical flash wear or aircraft position authority is inferred from this report.

## Measurement Semantics

CPU percentage is process CPU-time delta divided by monotonic elapsed time,
with one fully occupied CPU core equal to 100%. Multithreaded execution can
exceed 100%. It includes the recorder and other runtime threads, but excludes
child processes, other services, camera ISP work and accelerator utilization.
The first sample has no interval CPU percentage. Counter reversals produce an
explicit reason instead of negative utilization or invented I/O deltas.

Linux RSS comes from `/proc/self/status`. On macOS, the available fallback is
**process-lifetime peak RSS**, labelled in the hardware metadata; it is not
current RSS. Unavailable memory readings stay null. Desktop tests do not qualify
Pi memory usage.

Linux thermal-zone readings use millidegrees Celsius, including values near or
below zero. The shared probe now follows that fixed unit rather than guessing
from magnitude. [Linux thermal sysfs interface](https://www.kernel.org/doc/Documentation/thermal/sysfs-api.txt)
Missing/unreadable/nonfinite readings remain unavailable. On a detected Pi,
`vcgencmd get_throttled` supplies separate current and since-boot indicators when
the command is available. A latched historical flag is not automatically a
current fault. No flag is reset by this recorder.

Linux `/proc/self/io` supplies process `read_bytes`, `write_bytes` and
`cancelled_write_bytes`, with separate interval deltas. These are kernel-accounted
storage demand, **not physical flash writes, microSD wear, filesystem-wide I/O or
an SSD-vs-microSD identification**. Do not simply subtract cancelled bytes to claim
physical writes. Missing counters stay unavailable, never zero-filled.

## Native VIO Accounting

The recorder now accepts `native_vio_probe=worker.resource_snapshot` when the
owner explicitly attaches a `NativeVioStreamWorker`. The stream replay CLI wires
this up with `--record-runtime-metrics`; the current learned-only shadow service
does not yet launch or attach a native VIO worker. Install the optional
`runtime-metrics` dependency group for `psutil>=7,<8`. Missing dependency or
permissions produce unavailable measurements, not zero usage or a parent RSS
fallback. No system-wide process discovery is performed.

The probe binds the child PID, creation time, parent PID and native worker UUID
at launch. It checks identity/liveness before and after reads, rejects zombies
and does not follow a reused PID. It reads CPU user/system time, current child
RSS and available Linux `/proc/<pid>/io` counters. No command lines, environment,
process signals or descendant traversal are needed. Birth time is an identity
guard, not a navigation timestamp; OS clock changes can make that guard reject
measurements conservatively. [psutil process API](https://psutil.io/7.2/#psutil.Process)

The existing v1 metrics schema gains a header flag
`native_vio_measurement_requested` and a `native_vio_process` sample object.
It retains the parent's original CPU/RSS semantics. The reporter accepts older
parent-only logs and marks the native probe as not requested rather than
claiming a complete VIO workload was measured. For requested native samples it
checks fixed process identity, measurement scope and host-time brackets, and
recomputes CPU and I/O intervals from raw counters. An unavailable sample breaks
the interval chain; it never bridges a gap or fills it with zero. Process identity
changes within one recording are rejected, not merged into a single trajectory.

The report includes separate `native_vio_process.resource_distributions`, raw
identity, sampled state counts and I/O totals. Parent and child CPU percentages
have their own observation intervals; `parent_sample_skew_ms` exposes the timing
difference. Their RSS can overlap in shared pages and the Mac parent fallback
is a lifetime peak, so aggregate CPU/unique-RAM fields deliberately remain null.
These samples exclude other services, camera ISP, accelerators and grandchildren.
They do not measure whole-system load, missed short peaks or the child's complete
lifetime CPU after it exits. Live process loss remains an evidence gap.

## Failure And Validation

Hardware probes and log writes run on a separate recorder thread. A slow probe
does not hold the latest-sample lock; its age continues increasing in heartbeats.
The throttle command has a one-second timeout. Recorder failures are visible in
status and do not enable or disable flight control. Shutdown reports failure if
the recorder remains blocked after two seconds. File writes are flushed but are
not a promise of power-loss durability; a missing footer indicates incomplete
recording. Probe intervals and scheduling overhead must be measured on the Pi.

Software tests exercise counter math, missing sensors, low/negative thermal
values, file collisions, blocked probes, bounded logs and recording concurrently
with a controlled camera/model runtime. The report integration test consumes the
actual runtime writer's logs, with controlled camera/model execution, and checks
run binding and timing. Report tests also cover mixed runs, evidence mutation,
invalid clocks, telemetry loss, sample caps and unavailable measurements.
Actual Holybro/Pi capture cadence,
thermal behavior, raw-IMU timing, held-out route accuracy and promotion gates
remain separate, unverified hardware work.

### Local Counter Smoke Evidence

A 2026-09-05 Darwin/arm64 Python 3.13.9 check recorded 20 samples during a short
synthetic CPU loop, with normal shutdown and no recorder error. The final interval
reported approximately 99.57% of one CPU core and 33,177,600 bytes of explicitly
labelled lifetime-peak RSS. Temperature, Pi throttle flags and Linux process I/O
remained unavailable, as expected on this host. No learned model or drone was
running in this check; it is counter/recorder evidence only.

Local ignored artifact:
`research/outputs/live-runtime-metrics-host-smoke-20260905-r1/runtime_metrics-cbab844c-64e6-46c4-acdd-34910aeadf22.jsonl`

SHA-256: `d21bce4fa591b0bfdc36749746ac789c3b0476048c8b06eeaced4d9d4a4f1b47`.

The report command was also run against this unchanged recording, producing
`load-report.json` alongside it. It reports a 1.173-second observed sample span
and approximately 99.69% median process CPU on the one-core scale. Its evidence
status is `incomplete`: no run-bound inference, camera or attitude data, no Pi
thermal/I/O measurements, and no current-RSS measurement. The short recording's
quantiles are descriptive only and must not be used to size the VIO history.

### Recorded Native VIO Resource Evidence

On September 5, 2026 the existing TUM VI room1 recording was replayed on this
Darwin/arm64 host with both parent and native-child measurements enabled at a
requested 0.2-second interval. Native estimation processed all 2,821 camera
batches, including 2,689 local estimates. Its entire state log remained
byte-identical to the previous joint-pose replay. Both process and recorder
shut down normally; measured replay plus input verification took 40.11 seconds.

The resource log contains 195 samples: 191 available child samples and four
unavailable samples around startup/exit. Among valid child intervals, median
CPU usage was 61.48% on the one-core scale; peak sampled current RSS was
87,408,640 bytes. The parent median interval CPU was separately 10.93%; its
59,998,208-byte maximum is the Mac lifetime-peak measurement, not current RSS.
Do not sum those memory quantities or interpret the CPU samples as a Pi result.

The load report remains `incomplete`: it has no learned model, run-bound map
decisions, physical camera/attitude collector, Pi thermals or Linux I/O evidence.
Missing child samples break interval accounting rather than becoming zero CPU.
This is actual recorded-sensor execution evidence, not a concurrent learned
navigation benchmark, aircraft accuracy result or retention-budget measurement.

Ignored local artifact directory:
`research/outputs/tumvi-room1-process-metrics-20260905-r1/`.

- Worker/run ID: `d62dd23d-cc2a-4239-98a0-18cccd6d482c`.
- Metrics SHA-256: `441cb1c16a65cac6a80b880754ded6786d09c34b5577b8ff8d3a828332db06f0`.
- Native states SHA-256: `b762a6f1b090053c87c16cf4d2d916d36ccff90e1afd397f0645b147568a929c`.
- psutil version recorded in the available samples: `7.2.2`.

The final combined resource, native-stream, delayed-alignment, PnP/correlation,
PX4-boundary and capture-conversion suite passed **208 tests**. The main unit
runner passed all **104 checks** and `git diff --check` passed. Live Pi
concurrency, thermal limits and physical capture timing remain unverified.
