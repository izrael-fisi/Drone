# Live Research Alignment

`vision_nav.estimation.live_alignment.LiveAlignmentObserver` connects the finite
learned sensor session to the delayed joint-window research estimator. It does
not produce an authorized `NavEstimate`, send MAVLink or alter the default Pi
service. Every event remains `NO_GLOBAL_FIX`, `position_authority=false`,
`covariance_calibrated=false` and `px4_external_vision=disabled`.

## Ownership And Configuration

Construct the observer before starting the sensor session. It must reference
the same session as inference. Its background thread is the sole consumer of
`session.native.take_latest()`; do not attach another consuming monitor. It
opens no camera, serial connection or native process. It ignores initialization
records until local tracking begins, then invalidates the estimator epoch on
tracking loss, source failure or invalid native snapshots.

The configuration is the existing `WindowAlignmentConfig` dictionary: map,
model, calibration, clock and uncertainty-model IDs, `imu_T_camera`, and
`map_common_left_covariance`, with optional consistency/iteration settings.
Calibration and extrinsics must match the native profile; loaded learned map
and calibration provenance must agree at startup. The PnP joint-input contract
requires the additional common-map covariance to be zero because its map errors
already belong in the explicitly supplied joint noise. This is not permission
to assume map errors or cross-correlations are zero.

Default native snapshot receipt-age limit is 500 ms; default refresh attempts
is two. These limit accepted results, not computation time. A new posterior
during a solve requires new snapshot-specific noise and another solve; old
results are never restamped. A successful estimate is current only at its final
snapshot check, not continuously propagated beyond that snapshot. Receipt age
does not establish physical sensor freshness or clock calibration.

An opt-in [continuous refresh mode](continuous-research-alignment.md) now runs
these solves independently of learned-frame arrivals, while original map
anchors remain retained. It has a one-slot pending draft queue and separate
alignment evidence trace; it does not propagate beyond the retained window.

## Audit Mode

Add `--alignment-config PATH` to `vision-nav-live-learned-bench`. The file is JSON,
at most 32 KiB, containing the configuration described above. All existing
prop-off, measured sensor and model/map requirements still apply.

Without `--alignment-noise-provider`, the CLI supplies no uncertainty providers. Valid metric bindings return
`awaiting_uncertainty`; missing or incompatible geometry is withheld with its
reason. Planar-only geometry cannot enter this metric PnP path. No default
covariance is derived from a confidence score, feature count or configuration ID.

## Conditional Research API

Prefer the [owned estimator process](research-alignment-process.md) for explicit
research noise and solves. It is also available from the CLI; no default noise
provider is installed. The direct-callback API below remains a lab reference.

```python
from vision_nav.estimation.live_alignment import LiveAlignmentObserver
from vision_nav.neural.live_sensor_inference import run_live_learned_session

observer = LiveAlignmentObserver(
    session,
    alignment_config,
    attempt_noise_provider=explicit_attempt_noise_provider,
    joint_noise_provider=explicit_snapshot_joint_noise_provider,
)
report = run_live_learned_session(
    session, inference, output_directory, alignment_observer=observer,
)
```

The caller must implement both providers or neither. The first receives the
exact learned PnP request without uncertainty and returns the
[conditional PnP noise object](learned-pose-uncertainty.md). The second receives
an owned complete [joint alignment request](joint-pnp-vio-alignment.md) and
returns its snapshot-bound `joint_uncertainty`, including cross-fix and VIO
cross-covariances. Per-frame covariance does not supply those cross terms.
Synthetic independent-noise fixtures are tests, not suitable field providers.

Direct callbacks run synchronously in the research consumer, outside sensor ingestion.
They are trusted callbacks with **no hard timeout**. A blocked provider can stall
the caller; bounded retries are not a watchdog. `stop()` invalidates estimates,
joins the snapshot monitor and reports incomplete shutdown if observation is
still running. Results returned after stop or sensor failure are withheld. Use
only bounded lab providers through this reference API. The isolated engine
enforces child startup and operation deadlines and terminates failed workers.

## Evidence

The existing learned trace gains `research_alignment`, a separate
`research_alignment_latency_ms`, and
`research_alignment_completed_host_monotonic_ns`. Model latency remains separate.
The original learned-row digest is computed before these three parent annotations;
remove them when reconstructing the row for digest verification.

Each conditional solve records exact original requests and joint uncertainty in
`replay_inputs`, including attempted refreshes. These can be passed to
`vision_nav.estimation.pnp_window_alignment.align` for numerical replay.
In continuous mode, these background solve records live in the separate
`alignment-trace.jsonl`; the learned trace records queued/superseded draft IDs
and submission latency, not the background solver duration.
The observer event and complete enclosing decision are each capped at 4 MiB;
the enclosing trace remains capped at 256 MiB. Large covariance batches can
exceed this budget and fail the session; they are not silently truncated.

The summary records observer configuration, state, failure, thread shutdown,
counts, uncertainty mode, receipt-age/retry policy and latency quantiles.
`research_alignment_connected=true` means this observer was attached.
`global_fusion_connected=false` remains deliberate: the canonical navigation
authority and aircraft publication paths are not connected.

## Qualification Still Required

- Field-derived uncertainty, timing/extrinsic errors and VIO correlations.
- Temporal ambiguity rejection beyond uniqueness within retrieved candidates.
- Pi qualification of the isolated deadlines and propagation past retained map anchors.
- Sustained real Pi acquisition, native VIO, learned inference and storage tests.
- Geographic holdouts and separately approved PX4 transport validation.

Tests use explicit synthetic uncertainty for successful PnP fusion. The separate
actual-ONNX/native integration test exercises audit mode without fabricated noise;
it is not evidence of learned metric accuracy or aircraft readiness.

## Verification

September 5, 2026, local macOS CPU environments:

- 203 focused estimator, binding, observer, VIO-contract and PX4-boundary tests
  passed, including full numerical replay of synthetic conditional alignment.
- 64 model-process, live-session and learned-correspondence tests passed with
  the actual MegaLoc/XFeat/LighterGlue ONNX exports and native OpenVINS enabled.
  Three optional direct-PyTorch checkpoint tests were skipped because
  `VISION_NAV_LOCAL_REFERENCE_CONFIG` was not supplied.
- All 104 main unit-runner checks passed. CLI help and `git diff --check` passed.
- Model/native processes exited normally; no matching owned child remained.

Generated integration evidence is under
`research/outputs/live-alignment-integration-tests-20260905-r2/` (not committed).
The real-model observer test uses synthetic sensor interfaces and planar
geometry in audit mode; the successful metric fusion test uses synthetic PnP
and explicit synthetic noise. Neither is a physical Pi/X500 qualification.
