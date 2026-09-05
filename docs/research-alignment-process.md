# Isolated Research Estimator

`ProcessAlignmentEngine` moves explicit uncertainty callbacks, conditional PnP
linearization and GTSAM alignment into one spawned child. The existing parent
observer retains native snapshot ownership, epoch invalidation, frame binding
and final freshness checks. The child receives only bounded JSON requests, not
a camera, serial link, sensor session, raw image or aircraft transport handle.

This closes the unbounded callback/solver execution path for opt-in research
sessions. The [continuous observer](continuous-research-alignment.md) can now
schedule independent refreshes while map anchors remain retained. The process
does not supply measured uncertainty, propagation past those anchors or
navigation authority. All results remain shadow-only; PX4 output is disabled.

## Provider Contract

Supply a trusted local `.py` file, at most 1 MiB, with these functions:

```python
def attempt_noise(request, config):
    # Return the exact-attempt conditional PnP uncertainty object.
    ...

def joint_noise(request, config):
    # Return the full snapshot-bound joint uncertainty object.
    ...
```

The first function receives `request_without_uncertainty` from the learned
binding. The second receives the complete joint-alignment request before
`joint_uncertainty` is added. Follow the existing
[PnP uncertainty](learned-pose-uncertainty.md) and
[joint alignment](joint-pnp-vio-alignment.md) contracts; no implicit diagonal,
independence, confidence-to-covariance mapping or noise floor is added.

`config` is an owned JSON object, at most 32 KiB, copied for every call and
included in evidence. Do not place secrets in it. Providers must explicitly
validate and record any additional data/model asset digests they use. Source
hashes alone do not qualify their measurement model.

The provider is compiled from exactly the checked source bytes in the child,
without relying on cached bytecode. This is process isolation, **not a security
sandbox**. Trusted providers must not open sensors, publish commands, mutate
external state or spawn descendants. The engine reaps its own child only.

## Use

Add to the existing finite prop-off command:

```text
--alignment-config /data/research/alignment.json
--alignment-noise-provider /data/research/qualified_noise_provider.py
--alignment-noise-config /data/research/noise_model.json
--alignment-startup-timeout-s 20
--alignment-operation-timeout-s 2
```

These paths are placeholders for an explicitly reviewed provider and its data,
not supplied field-qualified models. The optional noise configuration defaults
to `{}`. Without a provider the existing audit-only behavior is unchanged.
The child uses the parent Python environment, which must include GTSAM, NumPy,
SciPy, threadpoolctl and the provider's dependencies. Install the `estimation`
extra; there is no automatic dependency install when starting a worker.

Python integration:

```python
from vision_nav.estimation.alignment_worker import ProcessAlignmentEngine
from vision_nav.estimation.live_alignment import LiveAlignmentObserver

engine = ProcessAlignmentEngine(provider_path, provider_config=noise_config)
observer = LiveAlignmentObserver(session, alignment_config, process_engine=engine)
# Pass observer to run_live_learned_session(..., alignment_observer=observer).
```

Do not also pass in-process callbacks. The runner starts the estimator before
opening sensors and closes it during session cleanup. As with other spawned
Python workers, application startup must be protected by `if __name__ == "__main__"`.

### Numerical CPU Budget

The owned estimator defaults to `numerical_threads=1`; Python callers may choose
an integer from 1 to 4. After importing its startup numerical libraries, the child
uses threadpoolctl to bound the detected BLAS/OpenMP pools. This happens only in
the child main thread and does not change parent application settings.

Status retains the requested count, detected startup pool details and the explicit
scope `detected_startup_blas_openmp_in_estimator_child_only`. These are startup
observations, not a total process-thread limit. A provider must not change pool
settings; libraries loaded later are not covered by that startup observation.
Unknown/undetected runtimes are not proof of enforcement. Reference:
[threadpoolctl supported scope](https://github.com/joblib/threadpoolctl).

The sensor/coordinator parent has a separate budget. For a controlled Linux/Mac
research run, set `OPENBLAS_NUM_THREADS=1`, `OMP_NUM_THREADS=1` and
`VECLIB_MAXIMUM_THREADS=1` before starting Python, then record those settings
with the workload. This does not establish ONNX/native-estimator thread budgets
or Pi thermal qualification. Do not dynamically change process-wide math pools
from the concurrently running sensor/observer threads.

## Deadlines And Failures

- Startup defaults to 20 seconds, including provider and solver import.
- Each bind, joint-noise and alignment request defaults to two seconds.
- The live observer uses `align_with_noise` to evaluate the explicit joint-noise
  provider and alignment in one child transaction. One operation deadline covers
  both; the exact returned noise and full result/request digests are retained
  for numerical replay. Separate `joint_noise` and `align` calls remain available.
- One request may be in flight; there is no queue, implicit retry or restart.
- Sensor failure cancels the parent wait. Timeout, child exit, changed source
  or mismatched response invalidates that worker for the rest of the session.
- Shutdown closes the private socket, waits briefly, then escalates to terminate
  and kill with bounded joins. Failure to reap is reported, not hidden.

Idle workers receive up to one second to exit after socket EOF, allowing normal
Python/numerical-library cleanup. Failed or in-flight workers retain a 200 ms
initial grace period. Terminate and kill each retain a one-second join bound.
This shutdown allowance does not extend operation or snapshot freshness limits.

The parent validates worker/request IDs, operation and exact request digest.
Messages use length-bounded JSON, not pickle payloads, and are capped at 40 MiB.
The smaller existing learned-trace budget still applies; a large replayable
covariance result may exceed it and make the session incomplete.

Operation deadlines include parent serialization/transfer/check time when
deciding whether to accept a result. They are not hard real-time scheduling or
memory guarantees for the host: bounded JSON encoding, source reads and process
cleanup have overhead. Provider and GTSAM code can no longer hold the caller
indefinitely because they execute in the killable child. Native acquisition
continues independently, subject to its own queue and resource limits.

The 500 ms default snapshot receipt-age gate is unchanged. A two-second operation
budget is not permission to accept a two-second-old snapshot. Refresh still
requires an exact newer posterior/noise pair, otherwise it withholds. Slow
research providers may execute successfully yet fail the freshness gate.

## Evidence And Limits

Observer status includes child identity/exit status, deadline settings, request
counts, provider configuration, source digests and GTSAM/NumPy/SciPy versions.
`last_operation` reports parent encode/IPC/child/decode/verification elapsed
time and request bytes. Combined replies also report child joint-noise and
alignment stage times; none is an end-to-end sensor-age metric.
The engine can report owned-process CPU/RSS measurements without enumerating
other processes. Full replay inputs remain in the existing learned trace.

Source pinning covers the provider file, estimator Python files and joint VIO
contract. It is not a signed artifact, full dependency lock or provider asset
validator. Retain environment locks and measured-noise provenance separately.

Tests use real spawned GTSAM and native OpenVINS processes with synthetic input
and uncertainty. They exercise numerical replay parity, execution independence,
snapshot refresh, timeout/cancel/exit handling and session cleanup. They do not
establish real camera timing, calibrated covariance, Pi performance or aircraft
navigation accuracy. The test-only noise fixture must never be deployed.

## Verification Record

September 5, 2026, local macOS CPU environments:

- 228 focused estimator/observer/binding/VIO/PX4 checks passed, including the
  actual native-plus-isolated-GTSAM refresh test and runner failure cleanup.
- 64 learned-model/process/correspondence regression tests passed using the
  existing MegaLoc and XFeat/LighterGlue ONNX exports. Three optional direct
  PyTorch checkpoint tests were skipped because their reference configuration
  was not supplied; the ONNX/native integration did execute.
- All 104 main unit checks, CLI help and `git diff --check` passed.
- All commands exited normally, with no matching owned child processes left.

Generated evidence (not committed):
`research/outputs/isolated-alignment-verification-20260905-r2/` and
`research/outputs/isolated-alignment-model-regression-20260905-r1/`.
The native concurrency case completed 160 ordered synthetic camera/IMU batches,
advanced VIO while the noise process was busy, and re-solved against the newer
posterior. Separate process faults cover hung/import-failed providers, abrupt
exit, malformed or oversized replies, changed source, cancellation and timeout.
