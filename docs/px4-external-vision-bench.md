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

- `ODOMETRY`: default PX4 bench path that can carry pose, covariance, velocity,
  reset counter, estimator type, and quality.
- `VISION_POSITION_ESTIMATE`: pose-only compatibility path for older or simpler
  setups.

Use this environment variable in the Pi wrappers:

```bash
VISION_NAV_MAVLINK_ENDPOINT=serial:/dev/ttyAMA0:921600
VISION_NAV_MAVLINK_MESSAGE=odometry
VISION_NAV_EXTERNAL_POSITION_MIN_RATE_HZ=1.0
VISION_NAV_EXTERNAL_POSITION_MAX_LATENCY_MS=500.0
```

To deliberately check the compatibility path instead:

```bash
VISION_NAV_MAVLINK_ENDPOINT=serial:/dev/ttyAMA0:921600
VISION_NAV_MAVLINK_MESSAGE=vision_position_estimate
```

The final bench-readiness and autonomy-readiness gates require PX4 receiver
evidence from the `ODOMETRY` path. Compatibility-path captures remain useful
for debugging, but they do not prove the preferred product interface.
The desktop app's Module Setup page exposes the same requirement as the
local-only `PX4 SITL Receiver Capture` action. It runs the dev wrapper below
and stores the generated report under
`~/DroneTransfer/from-pi/px4-sitl-evidence/` for local readiness re-audits.

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
- velocity-covariance warnings when `ODOMETRY` includes velocity fields

For `ODOMETRY`, the MAVLink send result also includes
`mavlink.details.reset_counter`, `mavlink.details.has_velocity`, and
`mavlink.details.has_velocity_covariance`. The counter increments when the
runtime sees an explicit estimator reset epoch change, a map change, or a
backward timestamp. This lets bench logs show discontinuities and whether the
velocity-bearing `ODOMETRY` path carried covariance before PX4 fusion tests.

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

After exporting PX4 parameters from QGroundControl or the PX4 shell, run the
repo checker:

```bash
vision-nav-check-px4-params \
  --params /path/to/px4.params \
  --gnss-denied
```

For a Pi bench workflow, use the wrapper:

```bash
VISION_NAV_PX4_PARAMS=/path/to/px4.params \
VISION_NAV_GNSS_DENIED_CHECK=1 \
./scripts/pi/check_px4_params.sh
```

The checker is report-only. It flags missing external-vision horizontal fusion,
GPS height/GNSS fusion during controlled GNSS-denied validation, vision height
fusion before vertical validation, velocity/yaw fusion before those fields are
validated, covariance-source mode, unusual EV delay values, and missing or
default-looking camera-to-body offsets.

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

4. Enable MAVLink with the default `ODOMETRY` path:

   ```bash
   VISION_NAV_MAVLINK_ENDPOINT=serial:/dev/ttyAMA0:921600 \
   VISION_NAV_MAVLINK_MESSAGE=odometry \
   VISION_NAV_COUNT=30 \
   ./scripts/pi/run_terrain_nav_loop.sh
   ```

5. If needed, repeat with the pose-only compatibility path for debugging:

   ```bash
   VISION_NAV_MAVLINK_ENDPOINT=serial:/dev/ttyAMA0:921600 \
   VISION_NAV_MAVLINK_MESSAGE=vision_position_estimate \
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

## PX4 SITL Smoke Sender

For a quick sender-path check before using camera captures, run PX4 SITL in one
terminal:

```bash
cd ~/PX4-Autopilot
make px4_sitl gz_x500
```

Then send a synthetic external-vision stream from this repo:

```bash
./scripts/dev/px4_sitl_external_vision_smoke.sh
```

For a live ROS 2 terrain-runtime bench check instead of the synthetic sender,
run the ROS 2 launch profile with the PX4 SITL UDP endpoint:

```bash
source /opt/ros/humble/setup.bash
ros2 launch ros2/launch/terrain_nav_live.launch.py \
  repo_root:=$(pwd) \
  pythonpath:=$(pwd)/src \
  bundle:=mission_bundle \
  output_dir:=terrain-run \
  mavlink_endpoint:=udp:14550 \
  mavlink_message:=odometry
```

Defaults:

```text
VISION_NAV_SITL_MAVLINK_ENDPOINT=udp:14550
VISION_NAV_SITL_MAVLINK_MESSAGE=odometry
VISION_NAV_SITL_RATE_HZ=5.0
VISION_NAV_SITL_REPEAT=6
```

Use `VISION_NAV_SITL_MAVLINK_MESSAGE=vision_position_estimate` to check the
pose-only compatibility path. The script creates a temporary synthetic
`synthetic_external_vision.jsonl`, sends accepted records through
`vision-nav-send-mavlink-log`, and includes one rejected record that should be
skipped.

The smoke script also writes a durable evidence-session folder. Set
`VISION_NAV_SITL_SMOKE_DIR` when you want stable paths:

```bash
VISION_NAV_SITL_SMOKE_DIR="$PWD/px4-sitl-evidence" \
./scripts/dev/px4_sitl_external_vision_smoke.sh
```

The folder contains:

- `synthetic_external_vision.jsonl`: sender-side synthetic match log
- `px4_sitl_evidence_session.json`: endpoint/message/rate/session metadata
- `receiver_capture/README.md`: exact capture and evaluation commands
- `receiver_capture/vehicle_visual_odometry.txt`: where to save listener output
- `receiver_capture/mavlink_status.txt`: where to save MAVLink status output
- `receiver_evidence.json`: recommended evaluator output path

The smoke script prints machine-readable artifact markers:

```text
__VISION_NAV_PX4_SITL_SESSION__=/path/to/px4-sitl-evidence
__VISION_NAV_PX4_SITL_MANIFEST__=/path/to/px4_sitl_evidence_session.json
__VISION_NAV_PX4_SITL_REPORT__=/path/to/receiver_evidence.json
```

For a no-MAVLink scaffolding check, use `VISION_NAV_SITL_DRY_RUN=1`.

This script proves the project can emit the selected MAVLink message path to
the SITL endpoint. It does not, by itself, prove EKF2 fusion. Confirm reception
from the PX4 shell or QGroundControl MAVLink console:

```bash
listener vehicle_visual_odometry 5
mavlink status
```

Save the output from those two commands into the session folder, then evaluate
the receiver-side evidence from this repo:

```bash
./scripts/dev/evaluate_px4_sitl_session.sh "$PWD/px4-sitl-evidence"
```

The session evaluator reads `rate_hz` from
`px4_sitl_evidence_session.json`, computes observed receive rate from the
captured `vehicle_visual_odometry` timestamps, and fails the receiver evidence
if the topic is far below the requested stream rate. Use
`VISION_NAV_PX4_SITL_MIN_RATE_RATIO` to adjust the conservative default only
when documenting a noisy SITL machine.

The evaluator checks that PX4 published `vehicle_visual_odometry`, that multiple
fresh samples are present, that local position and position variance arrived,
and that the optional MAVLink status capture looks like a MAVLink 2 UDP link.
It emits a JSON-compatible report through the
`vision-nav-evaluate-px4-sitl-evidence` CLI and prints
`__VISION_NAV_PX4_SITL_SESSION__=...` plus
`__VISION_NAV_PX4_SITL_REPORT__=...` markers. A `failed` or `degraded` result
means the SITL receiver requirement is not proven yet.
The final autonomy-readiness audit accepts either the full session via
`--px4-sitl-session` or the already generated receiver report via
`--px4-sitl-report`. That report must show `expected_message: odometry` before
it can satisfy final readiness.
From the desktop app, run Module Setup `PX4 SITL Receiver Capture` after PX4
SITL and tmux are installed; the generated report is listed in PX4 Receiver
Evidence and picked up by `Local Readiness Re-Audit`.

For loose capture files outside a session folder, use:

```bash
./scripts/dev/evaluate_px4_sitl_receiver_evidence.sh \
  /tmp/vehicle_visual_odometry.txt \
  /tmp/mavlink_status.txt
```

The same capture files can be included in support bundles with
`VISION_NAV_PX4_LISTENER_CAPTURE` and `VISION_NAV_PX4_MAVLINK_STATUS_CAPTURE`;
the generated report is stored under `summaries/px4_sitl_evidence/`.
If you used the smoke script's evidence-session folder, prefer:

```bash
vision-nav-support-bundle \
  --bundle mission_bundle \
  --log terrain_matches.jsonl \
  --px4-sitl-session "$PWD/px4-sitl-evidence"
```

The session folder is copied under `extras/px4_sitl_session/`, and the parsed
receiver report is still written under `summaries/px4_sitl_evidence/`.
When `px4_sitl_capture_prereqs.json` exists, support bundles also copy it under
`extras/px4_sitl_prereqs/` and summarize it under `px4_sitl_prereqs` for
offline setup review. That prerequisite summary is diagnostic only; final PX4
proof still requires a passing `receiver_evidence.json`.
Final autonomy readiness audits also preserve that prereq JSON as a diagnostic
input in the Markdown handoff and evidence ZIP without changing the
`px4_receiver_proof` gate.

## Automated SITL Capture Harness

When PX4 SITL and `tmux` are installed locally, this repo can start PX4 SITL,
send the synthetic external-vision stream, capture the PX4 shell outputs, and
evaluate the evidence session in one command:

```bash
VISION_NAV_SITL_SMOKE_DIR="$PWD/px4-sitl-evidence" \
./scripts/dev/run_px4_sitl_external_vision_capture.sh
```

To review or prepare local prerequisites first, run the setup helper. It is a
dry run unless `--apply` is provided, and it only clones PX4 when
`--clone-px4` is also provided:

```bash
./scripts/dev/setup_px4_sitl_prereqs.sh
./scripts/dev/setup_px4_sitl_prereqs.sh --apply
./scripts/dev/setup_px4_sitl_prereqs.sh --apply --clone-px4
```

If the workstation is missing PX4 or `tmux`, the harness exits nonzero but
still prepares the evidence-session scaffold, including the synthetic sender
log, `px4_sitl_evidence_session.json`, and
`receiver_capture/README.md`. It also writes
`px4_sitl_capture_prereqs.json` and prints
`__VISION_NAV_PX4_SITL_PREREQS__=...` so the missing checks are visible in app
logs and setup notes. That JSON includes copyable `fix_commands` for common
cases such as running the setup helper, installing `tmux`, cloning PX4,
pointing the harness at an existing PX4 checkout, or rerunning the same
evidence session. The same
commands are preserved in autonomy-readiness diagnostics, support bundles,
evidence-package manifests, Markdown handoffs, and `autonomy_goal_status.sh`
output. Autonomy-readiness command bundles keep them in
`prerequisite_fix_commands`, separate from commands that produce proof
artifacts. Fix the prerequisite and rerun the same command; the scaffold is not
receiver proof until `receiver_evidence.json` is generated from real
`vehicle_visual_odometry` and `mavlink status` captures.

Useful overrides:

```text
VISION_NAV_PX4_AUTOPILOT_DIR=$HOME/PX4-Autopilot
VISION_NAV_PX4_SITL_TARGET="px4_sitl gz_x500"
VISION_NAV_PX4_TMUX_SESSION=vision-nav-px4-sitl
VISION_NAV_PX4_BOOT_WAIT_S=45
VISION_NAV_PX4_LISTENER_ARM_WAIT_S=1
VISION_NAV_PX4_CAPTURE_WAIT_S=4
VISION_NAV_PX4_KEEP_TMUX=1
VISION_NAV_SITL_MAVLINK_MESSAGE=odometry
```

The harness is intentionally a bench helper, not proof by itself. The proof
artifact is still the generated `receiver_evidence.json` plus raw captures in
the evidence-session folder. The harness prints
`__VISION_NAV_PX4_SITL_SESSION__=...` and
`__VISION_NAV_PX4_SITL_REPORT__=...` so those paths can be copied into support
bundle or final readiness commands. Use `VISION_NAV_SITL_CAPTURE_DRY_RUN=1` to
verify the folder scaffold without starting PX4 or sending MAVLink.

Support bundles include the combined bench-readiness report automatically under
`summaries/bench_readiness.json`. Re-run the same gate against an existing ZIP
with:

```bash
vision-nav-bench-readiness \
  --support-bundle ~/DroneTransfer/outgoing/support-bundles/latest-support.zip
```

The readiness gate checks bundle health, runtime logs, replay gates, PX4
receiver evidence, and PX4 parameter readiness together. It should pass or
degrade for a bench artifact that is ready to review; it should fail when PX4
receiver evidence, replay gates, or parameter evidence are missing.

Only count the SITL requirement as passed after PX4 shows the selected external
vision stream arriving at a stable rate and rejected records are absent from the
receiver-side stream.

## Do Not Fly Yet Unless These Are True

- Camera calibration and camera-to-body extrinsics are measured.
- Time delay is measured or bounded.
- Wrong-map replay rejects matches.
- Low-texture and blurred replay cases degrade or reject cleanly.
- PX4 receives the selected external-position message in SITL or bench logs.
- A pilot can immediately switch back to a known safe mode.

This repo treats external vision as a navigation aid with explicit uncertainty,
not as ordinary satellite positioning.
