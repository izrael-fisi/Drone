# Correlated VIO Pose Windows

Status: September 5, 2026. Opt-in native research output, not a Pi service,
map-fusion backend or aircraft observation. The learned map pipeline is unchanged.

## Purpose

Successive VIO poses share tracks, inertial measurements and filter history.
Their posterior covariances cannot be treated as independent relative-motion
noise. OpenVINS' native [StateHelper](https://docs.openvins.com/classov__msckf_1_1StateHelper.html)
extracts all cross terms for selected pose clones. We now export these blocks
together. This preserves within-snapshot correlations, not independence across
snapshots or correlation handling between learned map features and VIO.

## Native Contract

`vision-nav-replay-openvins run --pose-window` adds an optional `pose_window`
field, null before a usable window exists. Normal replay retains its previous
output and storage cost without this flag.

The object uses `vision_nav_openvins_pose_window_v1`:

| Field | Meaning |
| --- | --- |
| `posterior_timestamp_ns` | Filter posterior time, matching the enclosing record |
| `pose_timestamps_ns` | Ordered original camera timestamps for the clones |
| `local_T_imu` | Current posterior mean of each clone in the arbitrary local frame |
| `joint_covariance_native` | Full 6N x 6N marginal, in pose-list order |
| `covariance_convention` | `per_pose_theta_imu_delta_p_local` |
| `linearization` | Current means with native FEJ filter covariance |
| `contains_cross_covariance` | True |
| `independent_across_snapshots` | False |
| `map_updates_fused` | False |
| `covariance_calibrated` | False |
| `position_authority` | False |

Only cloned IMU poses are extracted; the current IMU pose is not appended as a
duplicate of its identical newest clone. The native 15-state output remains
separate. Velocity and bias are marginalized in this pose covariance.

Exact lookup avoids reconstructing epoch timestamps from floating point. It
retains initialization history and active clones, removing obsolete entries.
Export requires `max_clones` in [2,32]. The tested TUM profile retains 11 clones,
roughly half a second at 20 Hz. Arbitrarily delayed map inference needs a
separate retention/scheduling design.

An opt-in [native passive-anchor replay](native-passive-anchors.md) now adds up
to four extra correlated poses outside the ordinary camera window. Its v2
export validates the explicit normal/passive union (at most 36 unique poses).
The v1/default path is unchanged. Live learned-ticket ownership and scheduling
remain unconnected; this is not a larger default clone window.

## Tangent Conversion

OpenVINS' [JPL update](https://docs.openvins.com/classov__type_1_1JPLQuat.html)
uses a negative left rotation error for `R_imu_local`. Transposing it to
`T_local_imu` gives positive right rotation error. Additive local-frame position
error rotates into the IMU frame. The first-order Jacobian is:

```text
J_i = diag(I_3, R_imu_local_i)
J = block_diag(J_0, ..., J_N-1)
P_right = J P_native J^T
```

Ordering is rotation in radians, then translation in metres. All cross blocks
are transformed. `JointPoseWindow.from_record` validates version, flags, times,
proper poses, dimensions, symmetry and positive-semidefinite covariance. Only
round-off symmetry is restored; negative eigenvalues are not clipped.

Native CTest perturbs the pinned `PoseJPL.update` at 12 orientations. Maximum
Jacobian discrepancy was `5.87e-10`. This checks algebra, not calibration.

## Relative Diagnostics

`JointPoseWindow.relative_diagnostic(t_i, t_j)` uses GTSAM `Pose3.between`
Jacobians and full joint covariance. Missing times are rejected, not
interpolated. Results are explicitly
`correlated_posterior_diagnostic_not_process_noise`, never independent factors.

A regression fixture with 4 m2 shared translation variance per pose gives
relative x variance 0.02 m2 with cross terms, versus 8.02 m2 if deleted. Other
correlations can have different effects. Monte Carlo testing with nontrivial
rotations also checks the propagated full covariance.

Neither marginal poses nor these diagnostics may enter `MetricPoseSmoother` as
independent odometry. Overlapping snapshots must not be stacked as independent
priors. The [joint PnP replay adapter](joint-pnp-vio-alignment.md) now performs a
fresh single-snapshot solve with explicitly supplied cross-frame/VIO uncertainty.
Live correlated map fusion still needs snapshot replacement/history, delayed
factor ownership, measured map uncertainty and body/IMU calibration.

## Run And Audit

Use the estimation Python environment and existing pinned native build:

```bash
cmake --build research/outputs/openvins-native-build -j 2
ctest --test-dir research/outputs/openvins-native-build --output-on-failure
python -m vision_nav.vio.openvins_replay run \
  --binary research/outputs/openvins-native-build/vision-nav-openvins \
  --capture research/outputs/tumvi-room1-input-20260905-r1 \
  --output research/outputs/tumvi-room1-joint-new --pose-window
python -m vision_nav.vio.pose_window \
  --replay research/outputs/tumvi-room1-joint-new \
  --output research/outputs/tumvi-room1-joint-audit-new.json
```

The auditor checks hashes, ordering, all windows and relative uncertainty. It
separately counts initialized estimates missing windows. The CLI fails for
incomplete replay, zero windows, or local estimates lacking windows. Eigenvalues,
cross-block norms and mixed-coordinate traces are numerical diagnostics, not
calibrated error scores or publication thresholds.

## Recorded Evidence

Run: `research/outputs/tumvi-room1-joint-20260905-r1`.
Audit: `research/outputs/tumvi-room1-joint-audit-20260905-r2.json`.

- 2,689 valid windows; 132 initialization/no-update records; no missing windows
  among initialized local estimates.
- At most 11 clones; maximum span 0.501774 seconds.
- Every multi-pose window retained nonzero cross-covariance blocks.
- Minimum joint eigenvalue approximately `2.19e-11`, positive.
- All 2,821 original local-state records exactly matched the earlier replay
  after removing the new field: export did not alter estimation.
- Uncompressed evidence is 292,866,327 bytes. Do not enable this research stream
  as routine microSD flight logging. Compact recording and Pi load tests remain.
- Native binary SHA-256:
  `b402af8d8bf763c716ca5dcd5ead76577c25a0f44b3474d0cfb41ef18907c78d`.
- State SHA-256:
  `b762a6f1b090053c87c16cf4d2d916d36ccff90e1afd397f0645b147568a929c`.

Native CTest passed; the native/evaluation/smoother/runtime suite passed 100
tests; the existing unit runner passed 104 checks. Outputs stay `NO_GLOBAL_FIX`,
without map updates, position authority or PX4 output. OpenVINS remains GPL
research evaluation; distribution and hardware qualification are pending.
