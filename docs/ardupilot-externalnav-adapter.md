# ArduPilot ExternalNav Adapter Design

## Purpose

PX4 remains the primary flight-controller target for this project. ArduPilot is
a later adapter path so the same low-cost terrain vision module can support
more autopilot ecosystems after PX4 bench validation is proven.

The ArduPilot adapter must consume the same estimator output as PX4:

- local pose in a known frame
- covariance and confidence
- timestamp and reset-counter behavior
- health state and rejection reasons

It must not create a separate estimator, relax wrong-map rejection, or present
map-derived vision as ordinary GNSS unless a compatibility mode is explicitly
selected.

## Interface Choice

Use MAVLink `ODOMETRY` as the preferred ArduPilot ExternalNav input. ArduPilot's
Non-GPS Position Estimation docs list `ODOMETRY` as the preferred message and
state that External Navigation messages should be sent at 4 Hz or higher.

Compatibility options stay secondary:

- `VISION_POSITION_ESTIMATE` plus optional `VISION_SPEED_ESTIMATE` for pose-only
  or older setups.
- `GPS_INPUT` only for an intentional GPS-like compatibility mode; ArduPilot
  lists it but marks it not recommended for this purpose.

The existing `vision_nav.external_position` conversion layer already emits
local NED/FRD-safe `ODOMETRY` payloads with pose covariance, quality, and reset
counter. The ArduPilot adapter should reuse that path rather than creating a
new frame conversion.

## Parameter Readiness

Use the parameter checker before any ArduPilot bench test:

```bash
vision-nav-check-ardupilot-params \
  --params ardupilot.params \
  --source-set 1 \
  --gnss-denied \
  --extrinsics-measured
```

On the Pi wrapper:

```bash
VISION_NAV_ARDUPILOT_PARAMS="$HOME/ardupilot.params" \
VISION_NAV_GNSS_DENIED_CHECK=1 \
VISION_NAV_EXTRINSICS_MEASURED=1 \
./scripts/pi/check_ardupilot_params.sh
```

The checker looks for the conservative bench shape:

- EKF3 active when exported parameters show EKF state.
- `VISO_TYPE=3` for VOXL-style visual odometry input.
- `VISO_POS_X/Y/Z` present and measured.
- selected `EK3_SRCn_POSXY=6` for ExternalNav horizontal position.
- vertical position stays barometer by default unless visual height is
  explicitly validated.
- ExternalNav velocity and yaw are warnings unless the runtime output has been
  validated for those fields.
- `EK3_SRC_OPTIONS` FuseAllVelocities is not enabled without a deliberate frame
  review.
- optional `RCx_OPTION=90` source switch is present when manual GPS/non-GPS
  source-set switching is part of the bench plan.

This is an audit tool only. It does not modify flight-controller parameters.
Support bundles can include the same report with
`VISION_NAV_ARDUPILOT_PARAMS=/path/to/ardupilot.params ./scripts/pi/create_support_bundle.sh`.

## Bench Sequence

1. Finish PX4 SITL and bench receiver evidence first.
2. Export ArduPilot parameters from Mission Planner or MAVProxy.
3. Run `vision-nav-check-ardupilot-params` and save the JSON report.
4. Run the terrain runtime in logging-only mode and verify accepted/rejected
   match quality with replay gates.
5. Send `ODOMETRY` to ArduPilot SITL at 4 Hz or higher.
6. Confirm EKF health, source-set state, and local position behavior in the GCS.
7. Package the runtime log, parameter check, and autopilot metadata in a support
   bundle before any flight test.

## Implementation Boundary

Do now:

- Keep common estimator output and covariance handling autopilot-agnostic.
- Keep ArduPilot parameter readiness checks in the repo.
- Document the adapter contract and bench evidence needed.

Do after PX4 bench evidence:

- Add an explicit ArduPilot message profile to `mavlink_bridge.py` if SITL shows
  any frame, quality, or covariance difference from the PX4 `ODOMETRY` profile.
- Add ArduPilot SITL receiver evidence parsing.
- Add support-bundle fields for ArduPilot parameter and receiver evidence.
- Add desktop display of ArduPilot readiness only for devices configured as
  ArduPilot.

Do not:

- Auto-change ArduPilot parameters from the desktop app.
- Enable `GPS_INPUT` as the default output.
- Treat ArduPilot support as field-ready until the same wrong-map, low-texture,
  blur, and source-switch tests pass.

## References

- ArduPilot Non-GPS Position Estimation:
  https://ardupilot.org/dev/docs/mavlink-nongps-position-estimation.html
- ArduPilot EKF Source Selection:
  https://ardupilot.org/copter/docs/common-ekf-sources.html
- ArduPilot GPS / Non-GPS Transitions:
  https://ardupilot.org/copter/docs/common-non-gps-to-gps.html
- ArduPilot Home and EKF Origin:
  https://ardupilot.org/dev/docs/mavlink-get-set-home-and-origin.html
- MAVLink `ODOMETRY` message:
  https://mavlink.io/en/messages/common.html#ODOMETRY
