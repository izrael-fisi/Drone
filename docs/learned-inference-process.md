# Isolated Learned Inference

The finite `vision-nav-live-learned-bench` command now loads and runs ONNX
models in an owned spawned process. Camera acquisition, MAVLink reception and
the native VIO process stay with the single-owner sensor session. The child
gets model/calibration paths and copies of selected immutable image windows;
it never receives a camera object, serial endpoint, GPS stream or flight-control
transport.

This is still a prop-off research command, not the default Pi systemd service
or a field-qualified navigation controller. Global fusion and PX4 output remain
disabled. See [live learned collection](live-learned-sensor-session.md).

## Behavior

1. Verify release/source identities and start a fresh model process before any
   sensor opens. Wait for a provenance-bound ready response.
2. Start the normal single-owner camera/IMU session. Select the newest complete
   learned window while VIO continues receiving every ordered sensor batch.
3. Transfer one window at a time over a private socket using bounded JSON
   metadata and raw uint8 pixels. Data messages are not Python pickles. Spawn
   configuration uses Python's normal trusted process-start machinery.
4. The model-only consumer validates pixels, exposure context, calibration and
   sequence, then runs the same retrieval/student/local-geometry stages as the
   in-process reference.
5. Match each response to the worker UUID, request ID, request digest and anchor
   frame identity. Reject unexpected position authority or publication state.
   The parent, not the model, attaches current VIO state and clone retention.
6. On timeout, disconnect, malformed response or cancellation, terminate and
   reap the inference process. Escalate to forced termination when necessary;
   unresolved cleanup is reported as `shutdown_incomplete`, never success.

There is no automatic restart or replay of the timed-out request. Later selected
windows are marked rejected because the worker is unavailable. A model failure
does not stop otherwise healthy camera/IMU recording or VIO: they finish the
planned finite session. The overall learned report is incomplete even if some
earlier windows produced model evidence. Operator interruption still stops the
whole bench workflow.

## Limits

| Boundary | Default |
| --- | --- |
| Model startup | 120 seconds |
| One selected window, including IPC | 5 seconds |
| Requests in flight | 1 |
| Image bytes per selected window | 64 MiB |
| Metadata per request | 256 KiB |
| Response size | 4 MiB |
| Normal child shutdown wait | 0.2 seconds |
| Terminate wait, then kill wait | 1 second each |

Set `--model-startup-timeout-s` or `--inference-timeout-s` explicitly when
measuring another supported machine. Startup is bounded to 0.05-300 seconds and
inference to 0.05-60 seconds. These are experiment watchdogs, not accepted flight
latency limits. The library can set `max_window_bytes` from 1 KiB through 256 MiB;
it must accommodate the selected camera dimensions and model window length.

Socket send and receive both honor the deadline, including a child that stops
reading or writes only part of a response. Pixel packing is size-bounded and
counts against inference elapsed time. This is not a hard real-time guarantee
for OS process spawning, filesystem access, scheduling or memory allocation.
The pixel limit is not an RSS cap on model weights or runtime libraries. Actual
Pi CPU, memory, thermal and storage measurements remain required.

## Evidence

`learned/summary.json` includes `inference_worker` with process PID, worker UUID,
native source UUID, exit code, completed/submitted window counts and failure
reason. Trace records include process identity, provider execution time and
parent-observed inference-plus-transfer elapsed time. Returned model evidence
cannot override native-owner health or turn an old anchor into a fresh pose.
The shutdown action and exit code are retained; a nonzero child exit leaves a
failed worker even when its final response arrived before the crash.

`ProcessWindowInference.resource_snapshot()` uses the existing owned-process
resource reader to expose PID/birth-bound CPU/RSS observations. It does not
enumerate unrelated processes or treat missing resource data as zero usage.

The in-process `LiveWindowInference` class remains available for parity and
fault tests. Its report explicitly says it has no hard provider timeout. The
CLI does not silently fall back to it if process startup fails.

## ONNX Runtime Vendor Telemetry

The neural package sets `ORT_DISABLE_TELEMETRY=1` before its lazy model imports.
For embedding into another Python application that might initialize ORT first,
set the same environment variable before launching that application. This
affects vendor analytics, not the project's MAVLink or position telemetry.

The official ORT privacy guidance says the early environment opt-out prevents
the non-Windows uploader and initialization telemetry from being created. The
later API only suppresses non-essential events and may run after an initial
event. Builds/platforms have different telemetry mechanisms; this is not an
OS-level firewall or proof that arbitrary dependencies cannot use the network.
[ONNX Runtime privacy documentation](https://github.com/microsoft/onnxruntime/blob/main/docs/Privacy.md).

This policy was added after a local macOS test process passed its assertions
but aborted during Python shutdown. The crash report identified
`onnxruntime_pybind11_state.so` frames in the vendor HTTP/telemetry worker and
`DebugEventSource::DispatchEvent`, not the camera or native VIO owners. Record
normal test-process exit as well as assertions when verifying this workflow.

## Verified Scope

On September 5, 2026 the final combined run passed 85 tests across process
isolation, live learned inference, recorded learned replay, single-owner sensors
and training export, exiting with code 0. An earlier 42-test run after the
telemetry opt-out also exited normally. All 104 main regression checks passed,
along with CLI help and `git diff --check`.

The full-model synthetic run used actual MegaLoc and XFeat/LighterGlue exports,
the synthetic-trained temporal student and the actual native VIO process. All
160 ordered native batches completed; 22 selected windows produced model
evidence and the model process exited with code 0. This is execution evidence,
not 22 correct global fixes or a flight-accuracy result.

The uncooperative-provider test ignored SIGTERM, was killed with exit code -9,
never restarted, and left its report incomplete. Native VIO still completed all
40 ordered sensor batches. Other tests cover startup crash versus timeout,
socket backpressure, partial/oversized/wrong-worker/wrong-request replies,
cancellation, altered pixels, and a crash after the last response.

Generated evidence is under
`research/outputs/inference-process-tests-20260905-r3/`; it is not committed.
These tests ran on macOS CPU, not Pi, and do not qualify physical camera timing,
IMU calibration, global fusion, learned accuracy or aircraft publication.
