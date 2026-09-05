# Handoff Verification And Reproduction

Date: September 5, 2026. Read alongside the
[technical handoff](technical-handoff-2026-09-05.md).
These are local development checks, not aircraft or Pi acceptance evidence.

## Verification Ledger

| Check | Observed outcome | Scope |
| --- | --- | --- |
| Main `tests/run_unit_tests.py` | 104 checks passed | Explicit project check list, not all pytest files |
| `scripts/dev/local_preflight.sh` after GitHub integration | Passed all seven stages | Syntax/imports, main checks, synthetic replay and field-template structure; no new field data |
| Estimator/native targeted suite, default parent thread environment | **292 passed, 1 failed**, 53.51 s | Cross-process versus parent exact numerical equality failed in `test_combined_real_child_matches_separate_operations_with_one_round_trip` |
| Same 293-test suite, parent BLAS/OpenMP startup limits set to one | **293 passed**, 44.68 s | Real local native binary enabled; numerical environment is part of reproduction |
| Companion API tests after GitHub integration | **7 passed**, 0.21 s | Mocked/local contracts; no Pi/service/GCS connection |
| Frontend build before GitHub integration | Passed | Existing Tauri import/chunk-size warnings |
| Rust check before and after GitHub integration | Passed | macOS arm64, not Windows/Pi execution |
| Rust tests after GitHub integration | **40 passed**, no failures/ignored tests | Before merge, 33 passed; added upstream tests preserved |
| Frontend merge validation | Initial TypeScript errors found in old per-device classical defaults; corrected during integration | Final build outcome recorded below |
| `npm ci` / `npm audit` after GitHub integration | Install passed; audit reports **5 vulnerable packages: 3 high, 2 moderate** | Not automatically upgraded during handoff |
| Learned/live/native five-file regression | **80 passed, 4 failed**, 39 warnings, 173.06 s; no skips | All failures were native resource-sample assertions in concurrent replay; `psutil` was absent from this separate model environment |
| Four concurrent replay cases rerun with `psutil` 7.2.2 installed | **4 passed**, 13 warnings, 42.88 s; no skips | Same native/model artifacts and assertions, no source/test relaxation; the other 80 cases were not rerun in this invocation |

Completed frontend and learned/native rerun results are recorded in the final
verification section below. Do not infer all-platform outcomes from these tests.

### Numerical Failure Must Not Be Hidden

The estimator child now limits detected startup BLAS/OpenMP pools to one thread
by default. The failing assertion compares complete Python result dictionaries
between combined child, separate child and parent solves, including floating
covariance values. With the parent unconstrained, that exact-equality assertion
failed. Constraining parent numerical pools before Python startup passed the
same full suite. This establishes environment sensitivity, not a universal
bitwise reproducibility guarantee or a quantified cross-platform error bound.

No assertion was removed or tolerance widened for this handoff. Investigate
the magnitude and acceptable numerical parity across platforms before deciding
whether the test should use a justified tolerance. Hash/identity and safety
decision checks must remain exact. Thread limits apply to detected native pools,
not all process threads or libraries loaded later.

### Important Negative Evidence

- Short synthetic native streams offered 20 further frames at 20 Hz, queue
  capacity two and a 500 ms snapshot receipt-age limit. Recent trials completed
  without overflow but still counted **zero advancing current global updates**.
- Faster JSON/private copying and one-thread numerical pools reduce some costs;
  they do not establish guaranteed progress under the exact-latest snapshot gate.
- Some older trials overflowed the bounded native queue. Subsequent short
  passes do not prove sustained overload cannot occur.
- Template coverage showing eight field conditions is schema/placeholder
  validation, not eight measured flight datasets.
- Historical model/native regression: 130 passed, no skips, after the orderly
  anchor-close fix. Historical timing and synthetic accuracy must not be presented
  as final-state Pi or X500 measurements.

## Portable Development Setup

Clone and record the exact revision:

```bash
git clone https://github.com/izrael-fisi/Drone.git
cd Drone
git status --short
git log -5 --oneline
```

Use Python **3.11** for the estimator/research starting environment. The package
metadata permits older versions, but the GTSAM/NumPy/native stack has narrower
tested combinations. Do not install every research extra into the Pi runtime.

On macOS/Linux, a starting estimator environment is:

```bash
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[estimation,geo,mavlink,map-production,runtime-metrics]' pytest
python tests/run_unit_tests.py
```

These commands resolve packages from `pyproject.toml`; they are **not a complete
reproducible lock**. Save installed versions and run compatibility checks. GTSAM
4.2.2 requires the selected NumPy 1.x environment here; the estimation extra
caps NumPy below 2 and OpenCV below 4.12. Do not silently override those caps.
Native C++ OpenCV is separate from the Python wheel.

Main checks can conditionally omit functionality when optional packages are
absent. For the full learned/model tests, provide Torch, ONNX, ONNX Runtime,
Pillow and their compatible export dependencies in a **separate** environment
and follow [local ONNX](learned-local-onnx.md),
[retrieval](learned-retrieval-replay.md), and
[map-conditioned student](map-conditioned-student.md). A generic environment
is not the pinned Bearing-UAV reference environment.

On Windows, use Python 3.11's `py -3.11 -m venv .venv` and
`.\.venv\Scripts\python.exe` equivalents. Tauri needs Rust/MSVC, Visual Studio
C++ tools and the Windows webview prerequisites. Follow
[Windows handoff](windows-11-codex-handoff.md), but do not assume its older
prototype Python 3.12 example qualifies the current estimator. For native VIO,
use the documented Linux/macOS build path until Windows portability is verified.
There is no requirement to add ROS/Gazebo for native compilation.

### Core Commands

```bash
python tests/run_unit_tests.py
VISION_NAV_PYTHON="$VIRTUAL_ENV/bin/python" ./scripts/dev/local_preflight.sh
npm ci --prefix desktop-app
npm run build --prefix desktop-app
cargo check --manifest-path desktop-app/src-tauri/Cargo.toml
cargo test --manifest-path desktop-app/src-tauri/Cargo.toml
python -m pytest tests/test_companion_api.py -q
```

The local preflight uses subordinate shell scripts; ensure the chosen environment
is activated/on PATH as well as setting `VISION_NAV_PYTHON`.
Do not treat a browser preview as a Tauri SSH/camera/hardware integration test.

### Native Estimator Suite

Build the pinned native adapter as described in
[ROS-free OpenVINS](openvins-research-replay.md). Requires CMake, C++ compiler,
OpenCV 4, Eigen, Boost, Ceres and nlohmann-json. Do not vendor generated build
directories or binaries into Git.

With the estimator environment activated:

```bash
export OPENBLAS_NUM_THREADS=1
export OMP_NUM_THREADS=1
export VECLIB_MAXIMUM_THREADS=1
export PYTHONPATH=src
export VISION_NAV_OPENVINS_BINARY=research/outputs/openvins-native-build/vision-nav-openvins
python -m pytest -q \
  tests/test_bounded_json.py tests/test_alignment_json_benchmark.py \
  tests/test_alignment_worker.py tests/test_current_alignment_refresh.py \
  tests/test_delayed_alignment.py tests/test_continuous_alignment.py \
  tests/test_joint_alignment_transaction.py tests/test_joint_transaction_benchmark.py \
  tests/test_single_fix_propagation.py tests/test_window_marginalization.py \
  tests/test_native_snapshot_handoff.py tests/test_live_query_anchors.py \
  tests/test_px4_boundary.py
```

Set these environment variables before interpreter startup, including on
Windows via `$env:VARIABLE = "value"`. A missing native artifact can cause skips;
inspect the summary. Do not edit hash-bound source while workers are running.

### Actual Learned Model Suite

Use a verified export/model environment, set the same native binary/thread
variables above, and point to actual parity-checked exports:

```bash
export VISION_NAV_RETRIEVAL_EXPORT=/absolute/path/to/megaloc-export
export VISION_NAV_LOCAL_EXPORT=/absolute/path/to/xfeat-lighterglue-export
python -m pytest -q \
  tests/test_live_learned_session.py tests/test_live_alignment_observer.py \
  tests/test_concurrent_learned_replay.py tests/test_inference_worker.py \
  tests/test_query_anchors.py
```

These tests use actual engines with synthetic fixtures. They do not train a
field model or establish aerial-to-map navigation accuracy. Missing dependencies,
upstream sources or exports must be reported, not counted as equivalent passes.
Install `.[runtime-metrics]` (the declared `psutil>=7,<8` dependency) in this
environment too. Merely having it in the separate estimator environment does
not enable native-process measurements in the model suite.

### RunPod

Use [RunPod Bearing-UAV training](runpod-bearinguav-training.md). It contains
authentication checks, an isolated Python 3.11 reference setup, hash-pinned
Linux lock, CUDA probes, one-epoch training and resume/cleanup guidance.
Credentials are machine-local and not in Git. Confirm cost, duration, storage
and cleanup before provisioning. No RunPod job or resource is required merely
to read or build this repository.

## Artifacts Not In Git

Transfer or reproduce these separately before expecting full research replay:

| Artifact | Required transfer metadata |
| --- | --- |
| Captures and independent reference | Session manifest, frame/raw-IMU/attitude logs, clock audit, checksums, site/split membership, permissions |
| Calibration | Measured intrinsics/extrinsics, image geometry, clock/noise evidence, installed hardware identity |
| Maps/surfaces | Reference and deployment manifests, imagery/COG/STAC, CRS/origin/vertical datum, surface masks, tile/index/cache files and licenses |
| Model releases | Original checkpoint, source revision/license, preprocessing, ONNX export, parity cases, model card and hashes |
| Native binary | Platform/toolchain/build manifest and clean pinned OpenVINS source; rebuild for a different architecture |
| Experiments | Raw trace, reports, environment/package versions, training optimizer/RNG state, dataset membership and artifact lineage |
| Credentials | Reauthenticate locally; never copy secrets into repository documents, fixtures or support bundles |

The previous machine's ignored local evidence includes:

- `research/outputs/openvins-native-build/vision-nav-openvins`
  with observed SHA-256
  `e89ddd42fbe01f5993cc32396ad8f6d54363bc910a199f52f01827450df2afcb`;
  source revision `69488123ed9362dd44b6f28e7f4680abbff1442b`.
- `research/outputs/megaloc-onnx-cpu-20260905-r2/` and
  `research/outputs/local-onnx-cpu-20260905-r2/`.
- `research/outputs/handoff-estimator-20260905-r1/` and
  `research/outputs/current-refresh-regression-20260905-r1/` for the passing
  constrained-parent run and preceding failing default-parent run.
- `research/outputs/current-refresh-bounded-20260905-r1/` and `-r2/` for
  short streaming observations, not qualification results.
- `research/outputs/handoff-learned-20260905-r1/` for the 80-pass/four-failure
  model run. Native metric records explicitly report `psutil_not_installed`;
  all four failing cases reached 160 completed native batches without a worker
  error before failing the sample-count assertion.
- `research/outputs/handoff-learned-metrics-20260905-r2/` for the four passing
  concurrent replay reruns with `psutil` 7.2.2 installed in the model environment.
- `research/outputs/alignment-numeric-chunks-20260905-r1.json` for serialization
  measurements against a frozen former incremental serializer, not Pi speedups.
- `/tmp/drone-estimator-tests`, `/tmp/drone-bearing-modern` and
  `/tmp/drone-nav-geo-tests` were local test environments only. They are not
  portable or durable installations and must not appear in production settings.

Before moving artifacts, generate/verify a file checksum manifest, verify
licensing and remove secrets. Do not weaken export source-hash checks to reuse
an incompatible artifact; re-export and verify parity under the new source.

## Security And Verification Limitations

The handoff dependency audit reports high findings in `browserslist`, `nanoid`
and `postcss`, and moderate findings in `react-router`/`react-router-dom`.
These are package audit results, not a demonstrated exploit of this app.
Review and update deliberately; no automatic major dependency upgrade was made.

No UI walkthrough, Windows native build, installed Pi run, physical X500 test,
remote Proxigo authentication/API test, RunPod GPU job or PX4 wire publication
was performed during this handoff. Build/test success does not cover those gaps.
The Git candidate scan found no private-key/token-pattern matches or generated
model/archive payloads in the new research files; this is not a complete
historical secret audit or security certification.

## Final Verification

- Final merged frontend: **build passed** after resolving per-device classical
  defaults and required status props in the new map shell. No algorithms or
  navigation gates were changed by that merge fix. Vite still warns about mixed
  static/dynamic Tauri imports and large application/MapLibre chunks.
- Final merged Rust: `cargo check` passed and `cargo test` passed **40 tests**.
- Learned concurrent replay rerun after installing the existing declared
  `psutil` dependency: **4 passed in 42.88 s**, 13 existing warnings, no skips.
  The larger preceding run had 80 other passes; it was not repeated in full.
- Local checks used Node 24.3.0 and Rust 1.96.0 on macOS arm64. Estimator/model
  suites used Python 3.11/NumPy 1.26.4/GTSAM 4.2.2; the main/preflight environment
  used Python 3.13. These are environment observations, not a portable lock.

No default-environment exact-equality test was disabled, and dependency audit
findings remain open. This handoff preserves known limitations instead of
claiming all-platform/flight qualification.

## Final Transfer Supplement

The transfer also includes the subsequent exact-request PnP cache, process
timing diagnostics and observer trace fields, plus regression tests. They were
local changes after the original `ce6b3088` handoff commit and are included in
the follow-up transfer commit, rather than left only on the previous machine.
Use Git history to identify that commit; no training/release promotion occurred.

Checks repeated against these changes on September 5:

| Check | Result |
| --- | --- |
| Cache, alignment, worker, native ownership/handoff and PX4 boundary suite below | **270 passed in 44.84 s**, no skips |
| Main project check list | **104 passed** |
| Local preflight | **All seven stages passed** |
| Desktop frontend build | **Passed**, existing Tauri import and chunk-size warnings remain |
| Rust `cargo check` | **Passed** |
| Rust `cargo test` | **40 passed**, no ignored tests |

Estimator command, with the same Python 3.11/NumPy 1.26.4/GTSAM 4.2.2
environment and locally built native binary described above:

```bash
OPENBLAS_NUM_THREADS=1 OMP_NUM_THREADS=1 VECLIB_MAXIMUM_THREADS=1 \
PYTHONPATH=src \
VISION_NAV_OPENVINS_BINARY=research/outputs/openvins-native-build/vision-nav-openvins \
python -m pytest -q \
  tests/test_pnp_assessment_cache.py tests/test_pnp_window_alignment.py \
  tests/test_alignment_worker.py tests/test_current_alignment_refresh.py \
  tests/test_joint_alignment_transaction.py tests/test_live_query_anchors.py \
  tests/test_single_fix_propagation.py tests/test_window_marginalization.py \
  tests/test_continuous_alignment.py tests/test_delayed_alignment.py \
  tests/test_native_snapshot_handoff.py tests/test_px4_boundary.py
```

The real native, synthetic-map observation in this run still recorded **zero
advancing current global updates** during 20 additional frames at 20 Hz. The
500 ms receipt-age gate was unchanged. This is a known availability failure,
not a passing flight-navigation result. The tests check rejection/ownership
contracts and must not be described as proving usable current navigation.

The ignored output is
`research/outputs/handoff-final-estimator-20260905-r1/`; its
`test_query_owned_before_slow_r0/streaming-observation.json` records that result.
Per-operation timing is retained in the adjacent alignment trace. Timing
diagnostics have tested structure/partitioning, but no representative Pi-load
bottleneck study was completed. The earlier full learned-model suite, dependency
audit caveats and all hardware/cloud limitations above still apply.

### Subsequent Owned-Transaction Check

After `70d3c820`, the same 12-file estimator/native suite listed above passed
**274 tests in 43.04 s**, with no skips, after adding four transaction reuse
tests. The real native binary, Python 3.11 environment and one-thread numerical
pool settings were unchanged. Full solver equivalence, per-snapshot validation,
cross-covariance and delayed identity/failure gates remain covered.

Artifact directory: `research/outputs/owned-alignment-transaction-20260905-r1/`.
Its 20 Hz observation still reports **zero advancing current global updates**.
See [the measured scope](research-alignment-process.md#owned-transaction-reuse).
No installed-Pi or aircraft test and no cloud training was performed.
The local preflight (including all 104 main checks), desktop frontend build
and Rust `cargo check` also passed again. Rust tests were not rerun for this
Python-only follow-up; the preceding 40-test result is recorded above.

### Compact Estimator Transport

After `2d84be05`, the private worker link defaults to `msgpack_v1` and retains
explicit `json_v1` comparison support. Install the updated `.[estimation]`
extra; local estimator/model checks used msgpack **1.2.2**. The JSON replay
contracts and canonical identity hashes remain unchanged; wire-envelope hashes
cover the selected encoded bytes. See [codec bounds](alignment-serialization.md#compact-worker-transport).

| Check | Observed result |
| --- | --- |
| Initial broad codec/estimator run | **400 passed, 1 failed**; a live-status test read two different snapshots in one predicate and raised `TypeError` |
| Same predicate changed to read once, subsequent broad run | **403 passed in 49.90 s**, no skips; no navigation/freshness assertion removed |
| Five-file learned/native/model suite before final allocation guard | **84 passed in 175.51 s**, 39 existing warnings, no skips |
| Final codec/estimator/native suite, including allocation guard | **419 passed in 49.86 s**, no skips |
| Final finite-session integration plus actual retrieval/local/student/native test | **5 passed in 23.15 s**, 39 deselected, 13 existing warnings; this is a selected rerun, not another full 84-test run |
| Local preflight | **All seven stages passed**, including all 104 main checks |
| Desktop frontend build / Rust check | **Passed**; existing frontend import/chunk warnings remain; Rust tests not repeated for this Python-only change |

The final estimator command is the 12-file command in the transfer supplement
plus `tests/test_bounded_msgpack.py` and `tests/test_bounded_json.py`. The added
coverage checks exact float64 bits and canonical hashes, format selection,
missing-dependency startup, duplicate/invalid keys, unsupported types, malformed
frames, and declared depth/node/length limits **before** allocating decoded
containers. The existing covariance, freshness, source and teardown tests remain.

Ignored outputs:

- `research/outputs/compact-alignment-20260905-r2/`: the initial 400/1 run.
- `research/outputs/compact-alignment-20260905-r3/`: the 403-test run.
- `research/outputs/compact-learned-integration-20260905-r1/`: the 84-test run.
- `research/outputs/compact-alignment-final-20260905-r1/`: final 419-test run.
- `research/outputs/compact-learned-final-20260905-r1/`: final five selected cases.

One initial compact-link trial produced one advancing current estimate, but
later paired runs and three explicit repeat trials returned to zero. The final
419-test run also recorded **zero for both codecs** in its 20 Hz observations.
The current-state requirement therefore remains unmet; neither green tests nor
an isolated positive update qualify sustained navigation. No installed-Pi run,
aircraft output, cloud allocation or training promotion occurred.

The final command-line transport smoke also completed with exact result/noise
parity for both codecs. It used a saved native request and a **synthetic** noise
provider, three measured pairs and two warmup pairs on this Mac. Separate versus
combined median transaction times were 46.35 / 39.58 ms for JSON and
31.00 / 32.82 ms for MessagePack. These small offline samples neither establish
a consistent combined-call speedup nor qualify current-state availability or Pi
performance. Results are local-only under
`research/outputs/compact-codec-cli-20260905/{json,msgpack}.json`; workers exited
cleanly. They are not included in the Git transfer.

### Canonical Identity And Live Status Ownership

After `8b592482`, the coordinator retains immutable canonical bytes for its
latest snapshot and reuses them for exact request digests. It does not reuse an
old noise model or bypass any response hash check. Native snapshot arrival also
wakes the refresh worker with the configured start-to-start interval, preserving
one solver caller. The compact conditional status record is now independent
of the full replay write and identifies pending/written evidence. See
[the status contract](continuous-research-alignment.md#evidence-and-status).

The focused final run passed **434 tests in 52.15 s**, no skips, using the same
Python 3.11 estimator environment and actual native binary. This is the preceding
14-file codec/estimator command plus `tests/test_canonical_identity.py`.
New tests preserve canonical hash equivalence under noise insertion, callback
mutation and concurrent snapshot replacement. Controlled blocked-write tests
verify that current status remains accessible before the write, becomes null
when native time advances, and is invalidated on write failure. No numerical,
freshness, retry or covariance requirement was relaxed.

Local-only artifacts:

- `research/outputs/fragment-live-20260905-r1/`: first canonical-byte trial;
  three advancing solve-completion checks, no operator-status sampling yet.
- `research/outputs/fragment-wakeup-20260905-r1/`: 21 checks passed; sampled
  actual status as well as solve completion. JSON had zero and MessagePack one
  advancing solve check, but both had zero advancing operator-visible poses.
- `research/outputs/current-status-20260905-r1/`: 36 checks passed after compact
  status ownership; both codecs had zero advancing solve/status observations.
- `research/outputs/current-status-final-20260905-r1/`: final 434-test run;
  both codecs again had zero advancing solve/status observations. JSON and
  MessagePack made 88 and 94 status samples respectively during their streams.

The stream has 20 frames at nominal 20 Hz with the original 500 ms native
receipt gate and original sensor timestamps. The new polling adds real status
reader work, so these are not isolated before/after latency benchmarks.
`current_pose_timestamps_ns` records earlier solve checks, whereas
`operator_status_advancing_pose_timestamps_ns` records what the status accessor
actually returned. The distinction exposes a gap that earlier check-only
reports could miss. Sustained current navigation remains unmet. No installed
Pi run, aircraft connection, PX4 publication, cloud allocation or training
promotion occurred.

The full five-file learned/native suite also passed **84 tests in 175.44 s**,
with 39 existing ONNX/Rasterio warnings and no skips, using the real MegaLoc,
XFeat/LighterGlue and project-student exports in the Python 3.11 model
environment. Its artifacts are under
`research/outputs/current-status-learned-20260905-r1/`. All seven local
preflight stages (including 104 main checks), the desktop frontend build and
Rust `cargo check` passed. Existing frontend import/chunk warnings remain.
Rust tests were not rerun for this Python-only update; the prior Rust test
result is historical, not a new execution claim.

### Child Transaction And Extended Stream Checks

After `870e37a4`, request-field encoding reuse also runs inside the isolated
estimator. Parent-side identity checks and full native/joint validation remain;
no old covariance, receipt timestamp or native pose is substituted. See
[the implementation contract](alignment-serialization.md#child-transaction-reuse).

The first focused run passed 82 tests, including one-second native observations.
The default compact link produced five distinct advancing poses returned by the
actual status reader; the JSON reference produced zero. The stream test was
then extended to 60 frames (three seconds) as well as the original 20 frames.
Every successful streaming estimate must equal a direct numerical replay, and
every visible status sample must resolve to its eventual journal event.

Three separate 60-frame compact-link observations:

| Local artifact suffix | Distinct advancing visible poses | Positive / total status samples | Longest gap between observed advancing samples, including stream edges |
| --- | --- | --- | --- |
| `child-canonical-long-20260905-r1` | 11 | 21 / 294 | 0.984 s |
| `child-canonical-long-20260905-r2` | 6 | 11 / 279 | 1.657 s |
| `child-canonical-long-20260905-r3` | 4 | 9 / 283 | 1.438 s |

These gaps use host observation times, including leading/trailing intervals;
they are not continuous-time outage measurements. The largest gap between
successive *pose sensor timestamps* in the three runs was 1.45 s. The sample
fractions are not a time-weighted availability estimate. Most positive samples
were observed while the full evidence write was still pending. Finish drained
successfully and each sample was linked back to written evidence.

The final 15-file codec/estimator/native suite passed **442 tests in 67.32 s**,
no skips. Its four native cases reported:

| Codec | Frames at nominal 20 Hz | Distinct advancing operator-visible poses |
| --- | --- | --- |
| JSON | 20 | 0 |
| MessagePack | 20 | 3 |
| JSON | 60 | 0 |
| MessagePack | 60 | 6 |

The 60-frame compact case had 12 positive samples out of 287 and a 1.653 s
maximum observed gap including edges. The original 500 ms receipt gate, exact
native timestamps, covariance checks, single-inflight process and shutdown
requirements were unchanged. New tests also cover fresh encoding per public
call, shared/cross-correlated noise, Unicode and signed-zero identities, refusal
of public cached-field inputs, and an actual child whose provider tries to
mutate the original snapshot.

All outputs are Git-ignored under `research/outputs/`, including
`child-canonical-20260905-r1/`, the three long-run directories above, and
`child-canonical-final-20260905-r1/`. Numeric timing is from a development Mac
with actual native VIO and synthetic map/noise observations, not a Pi or
physical drone. The improvement establishes intermittent operator-visible
updates in these runs, **not reliable continuous navigation**. Global
ambiguity, measured sensor/map noise, long-gap propagation, simultaneous Pi
load, thermal limits, trained field accuracy and PX4 transport remain open.

The full learned/native integration suite passed **84 tests in 174.82 s** with
39 existing ONNX/Rasterio warnings and no skips. It used actual MegaLoc,
XFeat/LighterGlue and project-student model execution; artifacts are under
`research/outputs/child-canonical-learned-20260905-r1/`. All seven local
preflight stages (including 104 main checks), the desktop build and Rust
`cargo check` passed. The existing frontend import/chunk warnings remain.
Rust tests were not rerun for this Python-only update. No aircraft connection,
PX4 output, installed-Pi execution, cloud allocation or training promotion was
performed.

### Documentation Transfer Refresh

The documentation-only refresh after implementation checkpoint
`fe13385f19f550af38941421280c077a9e59917c` adds a concise receiving-LLM checkpoint
and corrects older zero-update summaries to distinguish them from the latest
intermittent observations. It records correlated forward propagation as an open
design investigation, not an implemented native estimator feature.

For this refresh, `npm run build --prefix desktop-app`, `cargo check` in
`desktop-app/src-tauri`, and `git diff --check` passed. The frontend retains its
existing dynamic/static import and large-chunk warnings. No Python/native/model
tests or Rust tests were rerun for these documentation-only edits; the results
above describe the preceding implementation checkpoint. No runtime source,
hardware configuration, model weights or aircraft behavior changed.
