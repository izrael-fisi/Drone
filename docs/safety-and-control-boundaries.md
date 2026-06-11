# Safety And Control Boundaries

## Core Principle

The LLM/MCP layer must never directly control the aircraft at a low level.

It may request high-level actions. A deterministic safety layer decides whether those actions are allowed.

## Allowed LLM-Level Intents

Examples of acceptable high-level MCP tools:

- `get_vehicle_state`
- `run_preflight_check`
- `start_simulated_mission`
- `arm_if_safe`
- `takeoff_to_altitude_if_safe`
- `go_to_waypoint_if_safe`
- `hold_position`
- `return_to_launch`
- `land_now`
- `abort_mission`
- `get_localization_status`
- `get_nmea_output_status`

## Disallowed Direct LLM Actions

The LLM should not be able to:

- Send raw motor commands
- Send raw actuator commands
- Bypass arming checks
- Disable failsafes
- Publish arbitrary offboard setpoints
- Change critical PX4 parameters without review
- Override geofence or altitude constraints
- Treat low-confidence vision localization as a guaranteed fix

## Safety Gate Checks

Before allowing motion commands, check:

- Vehicle connected
- Vehicle mode known
- Armable state
- Battery state
- Home/local position state
- Geofence constraints
- Max altitude
- Max speed
- Mission radius
- Localization quality
- Operator confirmation where required

## GNSS-Denied Specific Checks

When using map/vision-derived position:

- Track confidence score
- Track time since last reliable match
- Track drift estimate if available
- Compare against inertial/dead-reckoning estimate
- Degrade gracefully when map matching fails
- Prefer hold/land/return behaviors over blind continuation

## Simulation Test Expectations

Every high-level tool should eventually have tests for:

- Normal success case
- Vehicle disconnected
- Not armable
- Low battery
- Bad localization
- Geofence violation
- LLM attempts invalid command
- Timeout during mission
- Abort path

## Practical Rule

The system should be able to say no to the LLM.
