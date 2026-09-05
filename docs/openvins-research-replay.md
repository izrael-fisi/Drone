# ROS-Free Local VIO Research Replay

Status: September 5, 2026. An opt-in native OpenVINS adapter now runs monocular
images and synchronized inertial measurements through a real visual-inertial
frontend. It is separate from the learned UAV-to-map pipeline, the desktop app,
and the Pi service. By default no aircraft commands, GPS measurements or map
fixes enter this replay. An explicit [map-frame control option](native-map-frame-controls.md)
now accepts conditional research initialization, not learned release promotion.
No flight-control messages leave either path.

## Why This Component

Continuous motion between learned map fixes needs a local estimator. The
[OpenVINS project](https://github.com/rpng/open_vins) provides an MSCKF-based VIO
implementation. Its [ROS-free interface](https://docs.openvins.com/gs-installing-free.html)
accepts camera and inertial data directly, although upstream warns that this
interface has limited support. We pin revision
`69488123ed9362dd44b6f28e7f4680abbff1442b` and build only a native research adapter.

OpenVINS and the linked native adapter are GPL-3.0-or-later. Keep the upstream
license and source with research artifacts; review distribution obligations
before including this in a product. This is an evaluation choice, not approval
to redistribute it with a proprietary product. No default Pi installation or
commercial release configuration has been changed.

Local tracking is KLT optical flow. Descriptor mode and tag tracking are rejected
by the runner. This does not reintroduce ORB/AKAZE map descriptors: MegaLoc,
XFeat/LighterGlue and the learned map-conditioned student remain the global
localization path. Upstream includes other tracker implementations, but this
adapter does not select them.

## Build On A Development Machine

Use the repository's Python environment with its standard NumPy, OpenCV and
PyYAML dependencies. The native compiler dependencies are separate from the
Python OpenCV wheel.

On macOS with Homebrew:

```bash
brew install cmake eigen opencv@4 boost ceres-solver nlohmann-json openssl@3
git clone https://github.com/rpng/open_vins.git research/vendor/open_vins
git -C research/vendor/open_vins checkout 69488123ed9362dd44b6f28e7f4680abbff1442b
cmake -S research/native/openvins -B research/outputs/openvins-native-build \
  -DOPENVINS_SOURCE="$PWD/research/vendor/open_vins" \
  -DOpenCV_DIR="$(brew --prefix opencv@4)/lib/cmake/opencv4" \
  -DCMAKE_BUILD_TYPE=Release
cmake --build research/outputs/openvins-native-build -j 2
```

Skip cloning if that pinned, clean checkout is already present. Builds are kept
outside the checkout and ignored by Git. The CMake project refuses a dirty or
different upstream revision. OpenCV 5 is not compatible with this build contract;
select OpenCV 4 explicitly rather than changing global library links.

On Ubuntu, the corresponding native packages are `build-essential`, `cmake`,
`libeigen3-dev`, `libopencv-dev`, `libboost-filesystem-dev`, `libboost-thread-dev`,
`libboost-date-time-dev`, `libceres-dev`, `libssl-dev`, and `nlohmann-json3-dev`. Configure with
the same CMake project and omit the Homebrew-specific OpenCV path. This Linux
build and Pi ARM64 execution have **not** been verified in this pass.

The verified macOS build uses AppleClang 17, OpenCV 4.14, Eigen 5.0.1,
Boost 1.92 and Ceres 2.2. An explicit `cassert` precompiled header supplies a
transitive include removed in Eigen 5; the pinned upstream checkout is unmodified.
No ROS, Gazebo, MAVSDK or simulator executable is built or launched.
The map-frame snapshot byte identity uses OpenSSL Crypto; the current Mac
control build found OpenSSL 3.6.4. This addition does not enable map controls
unless explicitly requested.

## Run The Sensor Smoke Test

```bash
python -m vision_nav.vio.openvins_replay synthetic \
  --checkout research/vendor/open_vins \
  --output research/outputs/openvins-input-001

python -m vision_nav.vio.openvins_replay run \
  --binary research/outputs/openvins-native-build/vision-nav-openvins \
  --capture research/outputs/openvins-input-001 \
  --output research/outputs/openvins-replay-001

VISION_NAV_OPENVINS_BINARY=research/outputs/openvins-native-build/vision-nav-openvins \
  python -m pytest -q tests/test_openvins_replay.py
```

New input/output directories are required. After an editable package install,
`vision-nav-replay-openvins` is an equivalent Python command entrypoint.

The fixture renders 160 monocular frames at 20 Hz and emits matching 200 Hz IMU
samples over eight seconds. It exercises static initialization followed by
translation without feeding a ground-truth state into OpenVINS. It is an ideal
textured-plane sensor fixture, **not a flight simulator, training dataset,
real-flight accuracy benchmark or proof of GNSS-denied navigation**.

Native tests also check missing/reordered IMU, invalid image resolution,
descriptor-mode rejection and no-excitation initialization. Fixture-only tests
do not replace the native tests. Missing checkout/binary causes explicit skips;
set the environment variable above for complete native verification.

The saved September 5 run at
`research/outputs/openvins-synthetic-replay-20260905-r2/report.json` completed with
73 initializing/no-update frames and 87 local-estimate frames. All 160 frames
were recorded with global position authority disabled. The combined native,
metric-smoother and canonical runtime suite passed 68 tests with the actual
native binary enabled. These results establish execution and rejection behavior,
not an operating envelope or Pi throughput.

## Public Dataset Replay

For our own Pi-format sessions, use the separate
[Pi capture converter](pi-capture-vio-replay.md). It requires explicit calibrated
sensor/clock evidence and rejects insufficient raw IMU or camera coverage. Public
dataset profiles below must not be reused as aircraft calibration.

The [EuRoC MAV dataset](https://projects.asl.ethz.ch/datasets/euroc-mav/) supplies
real images, synchronized inertial measurements and calibration. Its current
official download is hosted in the ETH Research Collection. The old robotics
download timed out and the new collection returned HTTP 403 during this pass,
so **no real EuRoC accuracy result is claimed**.

Once an authorized dataset copy is available, extract its `mav0` folder and run:

```bash
python -m vision_nav.vio.openvins_replay prepare-euroc \
  --checkout research/vendor/open_vins \
  --mav0 /data/MH_01_easy/mav0 --max-frames 400 \
  --output research/outputs/euroc-input-001

python -m vision_nav.vio.openvins_replay run \
  --binary research/outputs/openvins-native-build/vision-nav-openvins \
  --capture research/outputs/euroc-input-001 \
  --output research/outputs/euroc-replay-001
```

The preparer uses cam0 only, copies the selected images, reads the dataset's
camera/IMU transforms and noise values, and orders camera delivery after a short
IMU look-ahead. It does not read ground truth for initialization or inference.
EuRoC-specific accuracy evaluation remains outstanding.
This is not a converter for loosely synchronized Pixhawk/Pi logs: it assumes
EuRoC's calibrated sensor clock and calibration conventions.

### TUM VI Calibrated Replay And Evaluation

An additional preparer supports the official
[TUM VI calibrated 512x512, 16-bit EuRoC-format exports](https://cvg.cit.tum.de/data/datasets/visual-inertial-dataset).
These are handheld camera/IMU sequences, not Holybro flight validation or a
cross-view map benchmark. The dataset is CC BY 4.0. Retain attribution and
the dataset identity with evidence. The pinned OpenVINS `tum_vi` calibration,
IMU noise, gravity and image mask are used; only cam0 is enabled. Online
calibration remains disabled. This is a declared mono configuration, not an
exact reproduction of upstream stereo benchmark settings.

Use the calibrated export only: timestamps and IMU measurements are already
corrected. Do not apply raw-dataset offsets/scales again. The importer converts
uint16 intensity to uint8 with a fixed eight-bit right shift, without resizing
or per-frame intensity normalization. Original image and CSV hashes accompany
the converted input. Ground truth is never copied into frontend input.

```bash
python -m pip install -e '.[estimation,vio-evaluation]'
python -m vision_nav.vio.openvins_replay prepare-tumvi \
  --checkout research/vendor/open_vins \
  --mav0 /data/dataset-room1_512_16/mav0 --max-frames 20000 \
  --output research/outputs/tumvi-input-001
python -m vision_nav.vio.openvins_replay run \
  --binary research/outputs/openvins-native-build/vision-nav-openvins \
  --capture research/outputs/tumvi-input-001 \
  --output research/outputs/tumvi-replay-001
python -m vision_nav.vio.evaluate_replay \
  --replay research/outputs/tumvi-replay-001 \
  --ground-truth /data/dataset-room1_512_16/mav0/mocap0/data.csv \
  --output research/outputs/tumvi-evaluation-001.json
```

The independent evaluator verifies replay hashes and shadow-only output, reads
the calibrated export's **IMU-frame** reference poses, and associates integer
nanosecond timestamps without fitted time offsets. Position interpolation is
linear and orientation interpolation uses SciPy SLERP; gaps above 20 ms and
extrapolation are excluded and counted. It reports initialization/update coverage
and missing associations alongside errors; no local updates means no metrics.

Using research-only `evo==1.37.1`, report both initial-pose alignment and full
trajectory SE(3) alignment. Neither permits scale correction. Full-trajectory
alignment removes a rigid gauge using all associated positions and can conceal
some accumulated rigid error; the initial-pose result is retained for comparison.
Relative position error uses reference-path one-metre pairs with 10% tolerance,
all-pairs selection, and no alignment. Metrics concern the associated portion
only; pairs cannot cross associated-pose gaps above 250 ms. They do not certify
covariance, global-map lock or real-time execution.
Degenerate rigid alignment and unavailable relative-error pairs are explicit
null results. `evo` is GPL evaluation tooling, not a new Pi production dependency.

The native adapter now applies the configured image mask. A fully masked replay
is tested to remain uninitialized, and a missing mask checksum is rejected before
native execution. Changing the native adapter requires rebuilding it; replay
reports record the new binary digest independently of the upstream source pin.

### Measured Room1 Result: September 5, 2026

The official `dataset-room1_512_16.tar` download passed its published MD5
`51d2b8d93ee4ada47ec0b2d10115dcf8`. Its measured SHA-256 is
`20354392eab5cc82c9770ed29cf0130c5acce0db54a257571499008fb34c398f`.
Only regular files/directories under `mav0` were extracted; unused DSO symlinks
were excluded. Dataset and generated replay outputs remain ignored by Git.

| Measurement | Result |
| --- | --- |
| Input duration / camera frames | 141.004 s / 2,821 |
| Initializing or no-update frames | 132 |
| Local-estimate frames | 2,689 (95.32% of input frames) |
| First local update | 6.600 s after first camera frame |
| Maximum local-update gap | 51.46 ms |
| Ground-truth-associated estimates | 2,624 |
| Local estimates excluded from metrics | 65; insufficient bounded reference coverage |
| Maximum associated-pose gap | 1.100 s; not interpolated across |
| Initial-pose-aligned position RMSE | 0.1565 m |
| Initial-pose-aligned orientation RMSE | 2.155 degrees |
| Full-trajectory SE(3)-aligned position RMSE | 0.0806 m |
| Full-trajectory SE(3)-aligned orientation RMSE | 0.834 degrees |
| Reference 1 m relative position RMSE | 0.01228 m across 2,494 eligible pairs |
| Scale correction | Disabled in all metrics |
| Associated reference / estimated path length | 145.882 m / 145.561 m |
| Replay wall times on this Mac | 21.54 s and 22.13 s; not Pi latency measurements |
| Repeatability | Two runs produced byte-identical `local_states.jsonl` |

Evidence paths:

- Input: `research/outputs/tumvi-room1-input-20260905-r1`.
- Runs: `research/outputs/tumvi-room1-replay-20260905-r1` and `-r2`.
- Final evaluator report: `research/outputs/tumvi-room1-evaluation-20260905-r3.json`.
  Earlier evaluation versions are retained; r3 excludes relative pairs across
  long association gaps and reports the maximum associated-pose gap.
- Native binary SHA-256:
  `e2c504ffa440273c97c469ff915c9c71f52551506505e56b6934bbc298b9ed02`.
- State output SHA-256, identical in both runs:
  `2594039819d58e9cdd3a01a7880fd9d9bc9566b2846bb536357a92571962408b`.

The native/evaluation/smoother/runtime suite passed **82 tests**, with the native
binary enabled; the existing unit runner passed **104 checks**. Evaluation tests
include scale errors, gauge transforms, degenerate alignment, timestamp gaps,
altered evidence and no initialization. This is one real local-VIO sequence,
not independent validation of the learned map pipeline, aerial operating
conditions, runtime covariance, or publication eligibility. Every record
remained `NO_GLOBAL_FIX` with PX4 output disabled.

## Contracts And Evidence

An [independent native stream worker](native-vio-stream-worker.md) now accepts
bounded batches from sealed recordings and emits the same local-state contract.
It is verified against file replay, with process timeouts and explicit
backpressure. This does not yet connect a live Pi camera/IMU producer.

Input manifests bind configuration, calibration, images and JSONL events by
SHA-256. Image paths and external configuration must be included in that inventory.
The runner verifies them before execution and records native build information,
binary/input/adapter/output hashes, return code, elapsed time and state counts.
Outputs are `local_states.jsonl`, `native.log`, and `report.json`.

Native input events contain integer `timestamp_ns` plus:

- IMU: `angular_velocity_rad_s` and `specific_force_m_s2`, three finite axes
  in the calibrated IMU frame. Specific force includes the gravity response;
  it is not gravity-subtracted linear acceleration.
- Camera: `image_path` for an unresized grayscale-compatible image matching
  the calibrated dimensions. Delivery requires IMU samples bracketing the
  exposure, including at least 5 ms after it, and no more than 150 ms ahead.

The runner fails on nonmonotonic samples, IMU gaps above 50 ms, camera gaps above
250 ms, or missing coverage. Those are research input bounds, not demonstrated
operating limits. Fixed camera/IMU delay must be within 100 ms; online delay
calibration is disabled so buffering cannot silently change its timing contract.

Local results contain `local_T_imu`, velocity, estimated gyro/accelerometer biases
and the full native 15x15 state covariance. The pose matrix explicitly transposes
OpenVINS' global-to-IMU rotation; no JPL quaternion is relabeled as Hamilton.
The local frame is gravity-aligned with arbitrary yaw/origin, **not georeferenced
ENU**, and the tracked sensor frame is the IMU, **not airframe FRD**.

The covariance remains labeled `openvins_theta_I_p_G_v_G_bg_ba`, with
`covariance_validated_for_fusion: false`. It is a correlated posterior state
covariance, not independent relative-factor noise. Do not difference these poses
and feed them into `MetricPoseSmoother` with guessed covariances. A joint/correlated
fusion contract and body/IMU extrinsics are required before that connection.

The optional [correlated pose-window export](vio-joint-pose-contract.md) now
preserves native clone cross-covariance and verifies its tangent conversion.
Use `run --pose-window` for research audits only. This supplies within-snapshot
correlation evidence, not independent process factors or a map/VIO fusion path.

`LOCAL_ESTIMATE` only means the initialized filter updated to that camera time.
It does not establish integrity, map lock or sensor qualification. Every record
still declares `NO_GLOBAL_FIX`, no position authority and disabled PX4 output.
No automatic reinitialization, flight mode or promotion option is provided.

## Work Before Hardware Use

1. Extend the initial TUM VI room1 evidence to additional TUM/EuRoC sequences, including
   initialization failures, aggressive motion and visual dropouts.
2. Measure actual camera/IMU delay, readout, axes, noise/bias, cadence and
   vibration on the camera/Pixhawk combination. Raw message availability alone
   is insufficient; the current Pi capture is not automatically VIO-ready.
3. Bind the exposure/PX4 clock audit and implement live bounded buffering with
   explicit camera-to-IMU and IMU-to-body calibration.
4. Establish local-state correlation handling and connect learned map corrections
   without feeding past corrections back as new odometry evidence.
5. Evaluate CPU contention, memory, thermals, initialization and recovery on the
   Pi alongside learned inference, then review licensing and release eligibility.
6. Keep PX4 publication disabled until the separate vehicle-integration gates pass.
