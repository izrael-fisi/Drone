# Native Correlated Map Frame

Status: September 5, 2026. **Native estimator component, not wired into the live
sensor runner, Python observer, installed Pi service or PX4.** It is implemented
in `research/native/openvins/correlated_map_frame.{h,cpp}` and tested against
the pinned OpenVINS state helper. It does not replace the current conditional
PnP/window alignment or establish sustained live navigation.

## Intent

The current Python observer repeatedly solves a complete native snapshot and
then checks whether that snapshot is still latest. It can lose availability
while sensors advance. This component addresses the forward-propagation part:
keep a map-frame random variable inside the native covariance so local EKF
updates can carry its correlations, then project the native current pose
without a new full-window Python solve on every frame.

The upstream [delayed-initialization derivation](https://docs.openvins.com/update-delay.html)
explicitly includes ENU-to-VIO transforms as possible augmented states.
[StateHelper](https://docs.openvins.com/classov__msckf_1_1StateHelper.html)
provides initialization, propagation, update and marginalization. We use these
interfaces in a separate adapter; the pinned upstream source is unchanged.
The design below is our implementation, not an upstream navigation guarantee.

## State And Uncertainty Contract

`A = local_T_map` and `X = local_T_imu`. A native `PoseJPL` stores the inverse
orientation and a position expressed in the local world. Its error is
`[theta_map, delta_p_local]`, not the right-local SE(3) convention of our Python
alignment results. The output is `Y = A^-1 X = map_T_imu`.

Initialization requires an explicit conditional model:

```text
delta_A = B delta_native + epsilon
Cov(delta_native) = P
Cov(epsilon) = Q
Cov(delta_native, epsilon) = 0

P_native,A = P B^T
P_A,A = B P B^T + Q
```

`B` has six rows and one column for **every** native error-state dimension,
including velocity, biases, calibration, landmarks and passive poses when
present. `Q` must be finite, symmetric, positive definite and numerically
invertible. A pose-window marginal is not a complete native uncertainty model.
There is no default zero cross-covariance or automatic independent-noise mode.
Zero entries are a provider assertion that must eventually be justified by
measured evidence, not inferred from missing data.

The native owner captures a private snapshot with the complete covariance,
ordered variable handles, nominal means and camera-clock timestamp. Preparing
an initialization must not change that state. Initialization rejects a foreign
owner, changed timestamp, changed covariance/means, incomplete order, invalid
transform, invalid conditional covariance, overflow or expired observation.
Whitening uses `W = chol(Q)^-1`, then initializes with `H_L=W`, `H_R=-WB`,
unit noise and zero residual. This augments the map variable without updating
the existing local mean or marginal covariance.

This is an in-process API for the sole native owner, not an untrusted input
protocol. Callers must supply actual owned variable handles, serialize all
native mutations, and explicitly clear on epoch/reset/release changes. It is
not thread-safe. Initialization is capped at 4096 native error dimensions;
this allocation ceiling is not a Pi memory or timing qualification.

## Continuous Projection

Subsequent local measurements do not include `A` in their observation Jacobian.
Nevertheless, the native Kalman gain updates its mean and covariance through
the existing cross-covariance. Propagation carries cross terms to the current
IMU state. Camera-clone marginalization preserves the remaining joint marginal.
This is not a frozen alignment transform, independent odometry differencing,
or repeated reuse of a cached map fix as a new observation.

For right-local SE(3) output error ordered rotation then translation, let
`R = map_R_imu`, `p = map_p_imu`, and `R_X = imu_R_local`. The Jacobian with
respect to native errors `[theta_A, dp_A, theta_X, dp_X]` is:

```text
H_rotation    = [-R^T,           0, I,   0]
H_translation = [R^T [p]_cross, -R_X, 0, R_X]
P_Y = H P_[A,X] H^T
```

The complete 12x12 joint marginal is used, including the off-diagonal terms.
Native quaternion perturbation finite differences test this conversion.
Projection preserves current native sensor time, original map-observation time
and map age separately. An explicit caller-provided evidence expiry is mandatory
and may not exceed 300 seconds after the original observation. That hard bound
is not an approved operating horizon. No host receipt-age threshold is changed.

Projection retires the map variable on observed backward/nonfinite native time,
expiry, malformed pose or invalid covariance. A new explicit initialization is
required; advancing the clock afterward cannot resurrect it. Explicit clear and
destruction remove the six-dimensional state without changing the local marginal.
It supports one map state, not multi-fix fusion or multi-location hypotheses.

Every returned result has `global_pose_authority=false`,
`covariance_calibrated=false`, and `px4_publication_enabled=false`.
There is no transport, wire packet, live status schema or map activation action.

## What Must Be Done Before Live Binding

1. Derive `A`, `B` and `Q` from accepted learned geometry, including camera/body
   extrinsics and shared map/calibration/pixel/timing errors. Existing right-SE(3)
   PnP covariance cannot be relabelled as native map-frame covariance.
2. Establish the complete conditional dependence model. Independence from the
   present native state alone does not prove independence from later VIO
   innovations, especially when map matching and VIO reuse image features.
   Retain required shared nuisance states or account for those cross terms.
3. Bind the original query, map/model/calibration releases, complete native
   snapshot and epoch to a bounded owner transaction. A slow initialization
   can still be stale on arrival; this component only removes repeated solves
   **after** a valid initialization. Do not bypass that first-update problem.
4. Define successive fixes, ambiguity, map changes, covariance calibration,
   reset and long-gap policies before accepting multiple corrections.
5. Add sensor-runner/export/status integration, receipt-time/liveness gates,
   deterministic full-session replay and concurrent real-inference tests.
6. Measure combined Pi sensor/model/native/logging cost and physical uncertainty.
   No aircraft output should be enabled by this integration.

## Native Verification

After the pinned native environment is configured as described in
[OpenVINS replay](openvins-research-replay.md):

```bash
cmake -S research/native/openvins -B research/outputs/openvins-native-build
cmake --build research/outputs/openvins-native-build -j 2
ctest --test-dir research/outputs/openvins-native-build --output-on-failure
```

`native_correlated_map_frame` runs the real upstream augmentation, covariance
propagation, Kalman update and marginalization methods. It compares them to an
independent full-joint Joseph-form reference with nonzero velocity/bias/map
cross-covariance. Sixty synthetic update steps include eviction of the query
clone. Each step must expose the correct current projection and preserve the
unaugmented local estimator's mean/covariance. Removing map/current cross terms
must measurably disagree, so an independence shortcut cannot pass the fixture.

Other checks cover the native quaternion Jacobian, correlated non-diagonal
conditional noise, stale/foreign snapshots, malformed inputs, original times,
expiry, reset, projection failure, repeated initialization and state cleanup.
An additional single-fix fixture constructs `A = X_anchor body_T_camera Z^-1`
with a rotated, offset camera and compares the projected covariance to a direct
joint perturbation of map-camera, anchor and current poses. Only that synthetic
fixture declares its map-camera noise independent; this is not a runtime default.
The fixture is not a rendered-image VIO flight, concurrent model execution,
wall-clock 20 Hz benchmark, learned-fix integration or physical accuracy test.
