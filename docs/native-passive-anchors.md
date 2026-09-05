# Native Passive Pose Anchors

Status: opt-in research replay and [owned live controls](live-native-anchors.md).
This adds a bounded native mechanism for
retaining selected camera-time poses after the normal OpenVINS sliding window
advances. The live frame-ticket API is implemented; automatic learned-query
scheduling is not. It is not connected to a qualified
noise provider or aircraft output.

## Why Retain A Pose Inside The Filter?

The normal tested camera window spans about 0.5 s. A learned map result can
arrive later than that, and accepted map evidence may need to support motion
through a longer gap. Keeping an old exported JSON pose would not preserve its
changing relationship with the current state.

`PassivePoseAnchors` instead calls the pinned native `StateHelper::clone` on an
exact existing camera clone. The extra pose remains in the native state vector
and full covariance, outside the ordinary camera-clone collection. IMU
propagation and visual updates carry its cross-covariance and update its mean.
The feature tracker does not use it as another observation. No map measurement
is fed back into OpenVINS. The unchanged upstream checkout is built directly;
there is no patch to its estimator implementation.

This uses the documented native clone, update and marginalization interfaces.
Its correctness still depends on the native linearized filter model and the
separate map/VIO uncertainty assumptions. [OpenVINS StateHelper](https://docs.openvins.com/classov__msckf_1_1StateHelper.html).

## Bounded Ownership

- At most four extra six-dimensional poses, with capacity selected explicitly.
- Retain only an exact camera pose still available in the normal window.
- Duplicate identities/times, unavailable poses and capacity overflow reject.
- Release is explicit. There is no silent FIFO eviction or timestamp rewriting.
- Export the union of normal and passive timestamps, with one pose per timestamp;
  never export both identical stochastic copies as independent information.
- The original normal-clone count remains unchanged, bounded at 32. The union
  is bounded at 36. Passive states do not extend feature-track history.
- Release marginalizes the passive variable through the native helper. Its
  cross terms disappear with it; no cached covariance is kept as a new prior.

This preserves selected anchors beyond **ordinary clone marginalization**. It
does not transfer map information after the passive anchor itself is released,
nor provide an indefinitely valid global fix. Learned ambiguity, physical map
error, source age and uncertainty still require independent checks.

## Offline Schedule And CLI

`vision-nav-replay-openvins run` accepts `--anchor-schedule` together with
`--pose-window`. The original command, stream protocol and default Pi service
do not enable passive anchors. The native runner rejects combining this offline
schedule with `--stream-input`.

```bash
python -m vision_nav.vio.openvins_replay run \
  --binary research/outputs/openvins-native-build/vision-nav-openvins \
  --capture /absolute/path/to/sealed-capture \
  --output /absolute/path/to/new-replay \
  --pose-window --anchor-schedule /absolute/path/to/schedule.json
```

The schedule has schema `vision_nav_openvins_passive_anchor_schedule_v1`, an
integer `capacity` in 1..4 and 1..1024 `actions`. Each action has exactly:

| Field | Meaning |
| --- | --- |
| `at_timestamp_ns` | Exact initialized camera update after which to execute |
| `anchor_timestamp_ns` | Original camera timestamp to retain or release |
| `operation` | `retain` or `release` |

Times use the sealed capture's native clock, not host receipt time. Actions are
ordered by execution time; release-then-retain at one update is allowed. The
schedule is at most 128 KiB. Python validates its bounds/lifecycle, saves the
exact bytes in the replay output and binds their checksum into `report.json`.
The native runner additionally checks actual clone availability and camera
initialization. Missing execution frames or unexecuted actions at EOF fail the
replay rather than silently skipping the request.

## Pose Window v2

Scheduled replay and opt-in live controls export `vision_nav_openvins_pose_window_v2`, preserving the
existing pose/covariance conventions and adding:

- `normal_clone_timestamps_ns` and `passive_anchor_timestamps_ns`.
- `passive_anchor_capacity`.
- `retention_policy="native_passive_stochastic_clones_explicit_release"`.

Each enclosing native record also logs `passive_anchor_actions`. Python verifies
the sorted, duplicate-free union and the full joint covariance. The replay
auditor checks the schedule digest, applied actions and active membership at
each camera record. Live-control replay instead requires its ordered input and
native ACK provenance; unbound v2 output cannot qualify replay.
The existing v1 output remains the default and is byte-for-byte compatible.

The correlated PnP adapter already accepts a validated joint window. A retained
anchor can therefore bind the **unchanged original** map observation to a later
pose, with newly supplied snapshot-specific joint noise. The usual research
flags remain `NO_GLOBAL_FIX`, `position_authority=false`, uncalibrated covariance
and disabled PX4 output. Native retention does not invent map/VIO independence.

## Evidence And Remaining Work

Native CTest verifies actual clone, propagation, visual update and release:
the base 15-state mean/covariance stays unchanged within 1e-12, while the passive
mean, covariance and cross terms continue to update. It also checks state size
after release and invalid ownership operations.

The synthetic-image/IMU native replay retained a camera pose for 4.3 s, versus
the ordinary 0.5 s window. Its final joint export contained 11 ordinary poses
plus one passive pose. One unchanged synthetic PnP observation aligned the last
pose; the same observation failed against the ordinary window where its anchor
was absent. Default output matched the saved previous native binary byte for
byte. This is execution and conditional algebra evidence, not X500 field data,
geographic accuracy, physical uncertainty calibration or a Pi load benchmark.

Next integration requirements remain:

1. Connect the implemented live frame-ticket retain/release API to the learned
   coordinator's complete query/accepted-evidence lifecycle; no separate sensor owner.
2. Separate the deadline for a delayed result to arrive from the lifetime of
   already accepted evidence. The current coordinator's age limits are unchanged.
3. Select/release anchors without silently discarding a pending measurement or
   pretending old map evidence is a new fix; define transitions and loss behavior.
4. Measure concurrent runtime cost and measurement age, including continuous
   camera input. Offline retention does not fix the current exact-snapshot
   refresh/deadline bottleneck.
5. Qualify physical calibration, correlations, global ambiguity and the future
   PX4 boundary separately. GPL/native distribution review remains outstanding.

## Verification Record

On September 5, 2026, both native CTests passed and 282 focused Python tests
passed in the Python 3.11 estimator environment. These include passive replay,
normal-window compatibility, joint PnP alignment, continuous observer lifecycle,
isolated estimation and PX4 rejection boundaries. The integration tests used
the rebuilt native binary and a saved pre-change binary for byte comparison.

```bash
cmake --build research/outputs/openvins-native-build -j 2
ctest --test-dir research/outputs/openvins-native-build --output-on-failure
VISION_NAV_OPENVINS_BINARY=research/outputs/openvins-native-build/vision-nav-openvins \
python -m pytest -q tests/test_passive_anchor_replay.py tests/test_vio_pose_window.py
```

The byte-comparison test additionally requires `VISION_NAV_OPENVINS_BASELINE_BINARY`
pointing to a retained pre-change build. Do not rebuild that reference in place.
Generated test evidence is in `research/outputs/passive-anchor-final-20260905-r2/`.
Its `passive-anchor0/retained-alignment.json` preserves the exact synthetic PnP
request and resulting conditional alignment for numerical replay; native inputs,
schedule, local states and checksums are alongside it. Outputs are Git-ignored.

The broader actual-ONNX/native/map regression passed 133 tests in the Python 3.13
environment; three optional direct-PyTorch checkpoint tests were skipped because
`VISION_NAV_LOCAL_REFERENCE_CONFIG` was unset. Existing ONNX/geospatial warnings
remain. Evidence is under `research/outputs/passive-anchor-model-regression-20260905-r1/`.
All 104 main project checks and whitespace checks passed. The pinned OpenVINS
checkout remains clean. No desktop code changed, so desktop/Rust checks were
not rerun; no aircraft, live sensors or cloud resources were accessed.
