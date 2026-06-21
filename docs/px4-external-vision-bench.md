# PX4 External Vision Bench Guide

This guide covers bench testing the terrain-navigation runtime as a PX4
external-position source. It is intentionally conservative: the app and runtime
must not change PX4 parameters automatically.

Official references:

- [PX4 External Position Estimation](https://docs.px4.io/main/en/ros/external_position_estimation)
- [PX4 EKF2 Navigation Filter](https://docs.px4.io/main/en/advanced_config/tuning_the_ecl_ekf)
- [PX4 Visual Inertial Odometry](https://docs.px4.io/main/en/computer_vision/visual_inertial_odometry)

## Output Modes

The runtime supports two MAVLink external-position modes:

- `VISION_POSITION_ESTIMATE`: default pose-only compatibility path.
- `ODOMETRY`: richer PX4 path that can carry pose, covariance, velocity, reset
  counter, estimator type, and quality.

Use this environment variable in the Pi wrappers:

```bash
VISION_NAV_MAVLINK_ENDPOINT=serial:/dev/ttyAMA0:921600
VISION_NAV_MAVLINK_MESSAGE=vision_position_estimate
VISION_NAV_EXTERNAL_POSITION_MIN_RATE_HZ=1.0
VISION_NAV_EXTERNAL_POSITION_MAX_LATENCY_MS=500.0
```

For PX4 `ODOMETRY` bench tests:

```bash
VISION_NAV_MAVLINK_ENDPOINT=serial:/dev/ttyAMA0:921600
VISION_NAV_MAVLINK_MESSAGE=odometry
```

## Frame And Covariance Rules

The terrain matcher emits local ENU measurements:

- `x_m`: east
- `y_m`: north
- `z_m`: up, optional

PX4 external-vision messages are sent in local NED/FRD-compatible axes:

- `x`: north
- `y`: east
- `z`: down

The shared conversion code lives in `src/vision_nav/external_position.py`.
Rejected matches, missing positions, and non-ENU measurements are not sent.

Covariance is mapped into MAVLink upper-right-triangle order:

- index `0`: north variance
- index `6`: east variance
- index `11`: down variance
- index `20`: yaw variance

When vertical position is unavailable, the runtime sends zero down position for
compatibility but keeps vertical uncertainty conservative. Do not configure PX4
to fuse vision height unless the runtime is emitting a valid `z_m` and `z_m2`.

## Stream Health

Every MAVLink-enabled runtime log record includes an `external_position_health`
snapshot. It reports:

- output status: `inactive`, `warming_up`, `healthy`, or `degraded`
- MAVLink message type
- send attempts, sent count, skipped count, and skip reasons
- measured send rate over the recent window
- measurement latency from `timestamp_us`
- covariance and stale-timestamp warnings

For `ODOMETRY`, the MAVLink send result also includes
`mavlink.details.reset_counter`. The counter increments when the runtime sees an
explicit estimator reset epoch change, a map change, or a backward timestamp.
This lets bench logs show discontinuities before PX4 fusion tests.

The defaults are intentionally bench-friendly:

- `VISION_NAV_EXTERNAL_POSITION_MIN_RATE_HZ=1.0`
- `VISION_NAV_EXTERNAL_POSITION_MAX_LATENCY_MS=500.0`

For PX4 fusion testing, tighten these values to match the expected estimator
input rate and measured capture/processing delay.

## PX4 Parameters To Review Manually

Review these in QGroundControl under Vehicle Setup > Parameters. Reboot the
flight controller after changing EKF parameters.

| Parameter | Purpose | Conservative project guidance |
| --- | --- | --- |
| `EKF2_EV_CTRL` | Selects which external-vision measurements EKF2 fuses. Bits include horizontal position, vertical position, velocity, and yaw. | Start with horizontal position only. Add vertical, velocity, or yaw only after those fields are measured, timestamped, and tested. |
| `EKF2_HGT_REF` | Selects the long-term height reference. PX4 requires a height source. | Do not set height reference to vision unless vision height is valid. Keep barometer or another PX4-supported height source for early tests. |
| `EKF2_EV_DELAY` | Vision estimate delay relative to IMU time. | Measure camera capture plus processing delay. Start with the runtime `--mavlink-ev-delay-ms` value and tune in SITL/log review. |
| `EKF2_EV_POS_X/Y/Z` | Vision sensor position relative to vehicle body frame. | Set from measured camera-to-body extrinsics before flight. |
| `EKF2_EV_NOISE_MD` | Chooses whether EKF2 uses message covariance or parameter noise values. | Prefer message covariance after covariance behavior is tested; use parameters for controlled experiments. |
| `EKF2_EVP_NOISE`, `EKF2_EVV_NOISE`, `EKF2_EVA_NOISE` | Lower bounds or direct noise values for external position, velocity, and angle. | Keep conservative until replay logs prove accuracy. |
| `EKF2_GPS_CTRL` | Controls GNSS aiding. | Leave GNSS available for ground truth during early outdoor testing. Disable GNSS fusion only in controlled GNSS-denied validation. |

## Bench Sequence

1. Confirm the terrain bundle validates:

   ```bash
   ./scripts/pi/validate_terrain_bundle.sh
   ```

2. Run a logging-only terrain loop without MAVLink output:

   ```bash
   VISION_NAV_COUNT=30 ./scripts/pi/run_terrain_nav_loop.sh
   ```

3. Review match logs and confirm accepted/rejected behavior:

   ```bash
   ./scripts/pi/summarize_vision_nav_logs.sh
   ```

   The summary reports external-position health when MAVLink output was enabled.

4. Enable MAVLink with the default pose-only path:

   ```bash
   VISION_NAV_MAVLINK_ENDPOINT=serial:/dev/ttyAMA0:921600 \
   VISION_NAV_MAVLINK_MESSAGE=vision_position_estimate \
   VISION_NAV_COUNT=30 \
   ./scripts/pi/run_terrain_nav_loop.sh
   ```

5. Repeat with `ODOMETRY` only after pose-only output looks sane:

   ```bash
   VISION_NAV_MAVLINK_ENDPOINT=serial:/dev/ttyAMA0:921600 \
   VISION_NAV_MAVLINK_MESSAGE=odometry \
   VISION_NAV_COUNT=30 \
   ./scripts/pi/run_terrain_nav_loop.sh
   ```

6. In PX4 SITL or bench logs, confirm:

   - message rate is stable,
   - timestamps are plausible,
   - rejected matches are not fused,
   - covariance changes with match quality,
   - PX4 local position does not jump on weak matches,
   - vertical fusion is disabled unless valid vertical measurements exist.

## Do Not Fly Yet Unless These Are True

- Camera calibration and camera-to-body extrinsics are measured.
- Time delay is measured or bounded.
- Wrong-map replay rejects matches.
- Low-texture and blurred replay cases degrade or reject cleanly.
- PX4 receives the selected external-position message in SITL or bench logs.
- A pilot can immediately switch back to a known safe mode.

This repo treats external vision as a navigation aid with explicit uncertainty,
not as ordinary satellite positioning.
