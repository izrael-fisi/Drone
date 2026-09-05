# PX4 integration boundary

**Current status: no aircraft transport.** The learned runtime remains shadow-only.
`Px4VisionBridge.publish_mavsdk` deliberately raises `NotImplementedError` even
when an estimate passes its eligibility checks. No parameters are changed.

## Offline Conversion Contract

The bridge accepts only explicitly declared `NavEstimate` conventions:

- `body_frame="FRD"`: quaternion is `ENU_R_bodyFRD`, Hamilton `xyzw`.
- `covariance_convention="position_then_left_rotation_world"`: full 6x6
  covariance for `[delta_p_enu, delta_theta_enu]`, metres then radians.
  Position is additive; orientation error is `Exp(delta_theta) @ R`.
  This is NOT right-SE(3) covariance or Euler-angle covariance. Native VIO and
  PnP research outputs must not be relabelled to satisfy this contract.
- `clock_domain="host_monotonic"`: capture time already converted to the same
  host clock used for the eligibility check. Merely naming a clock is not a
  synchronization procedure.

Unset conventions fail closed. Conversion uses the proper rotation
`C = [[0,1,0],[1,0,0],[0,0,-1]]`: position becomes `[N,E,-U]`, orientation
becomes `C @ ENU_R_bodyFRD`, and covariance becomes `J @ P @ J.T`, where
`J=diag(C,C)`. Cross-covariance terms are preserved. Body FRD is unchanged;
there is no implicit FLU-to-FRD or camera-to-body conversion.

The historical `Px4VisionOdometry` name now denotes an **internal offline
record**, with `frame_id="NED"`, a full 6x6 tangent covariance and a
host-monotonic timestamp. It is not a serializable MAVLink payload.
`to_message` checks structure/conventions but does not establish freshness or
release compatibility. `publication_decision` also rejects future/stale times,
nonfinite current time, indefinite/asymmetric covariance, invalid quaternion,
upstream publication vetoes, missing/mismatched map/model/calibration release
IDs, unhealthy transport and excessive position covariance. A passing decision
is eligibility only, not flight qualification or transport authorization.

## Future Wire Adapter

Current PX4 documentation specifies external-vision `ODOMETRY` with
`MAV_FRAME_LOCAL_FRD`; the older blanket `LOCAL_NED` wire prescription in this
repo was incorrect. Pin the actual firmware receiver before choosing wire
frames. MAVLink describes a 21-element upper-triangular pose covariance in
position/roll/pitch/yaw order, not this internal tangent matrix. Derive and test
the appropriate covariance mapping and packing; do not flatten or truncate it.
[PX4 external pose](https://raw.githubusercontent.com/PX4/PX4-Autopilot/main/docs/en/ros/external_position_estimation.md),
[MAVLink ODOMETRY](https://mavlink.io/en/messages/common.html#ODOMETRY).

Vehicle integration must:

1. Record and validate PX4 external-vision estimator parameters before flight
   testing. Use replay and prop-off/HIL evidence in the active scaffold; SITL
   is optional future validation infrastructure, not a runtime dependency.
2. Qualify parent/child frames, world origin/heading alignment, velocity and its
   covariance, measurement clock conversion, reset counters and packet layout
   for the pinned PX4/MAVLink release. Inspect packets and estimator acceptance
   on a prop-off bench before binding a transport. Never relabel delayed map
   fixes as fresh samples to manufacture a continuous output rate.
3. Keep the PX4 estimator/commander responsible for estimator innovation gates, arming, offboard loss, geofence, return/land action, and all actuator outputs.
4. Fail closed: if global vision is stale, ambiguous, or high covariance, send no new global observation and expose health to the companion watchdog. Do not forge a low covariance.

Before air tests, use recorded replay and deterministic fault injection (blur,
repetitive terrain, map staleness, low light, drops), HIL/prop-off checks,
tethered tests where appropriate, and an independent kill/safety plan suitable
for the airframe and local regulation. A future SITL stage may supplement this
evidence, but it is not part of the active project scaffold.

Offline regression checks:

```bash
python -m pytest -q tests/test_px4_boundary.py tests/test_vision_first_navigation.py
```

These do not connect to a vehicle and are not a replacement for hardware evidence.
