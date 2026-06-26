# PX4 Hardware Configuration Snapshots

This folder stores parameter snapshots exported from the real Holybro X500 V2 /
Pixhawk 6C bench setup.

## Files

- `holybro_x500v2_pixhawk6c.params` - PX4 parameter snapshot exported on
  2026-06-26 over Raspberry Pi USB MAVLink.

## Current Bench Snapshot Notes

The 2026-06-26 snapshot captures the QGroundControl motor assignment after
`Identify & Assign Motors`:

```text
PWM_MAIN_FUNC1 = 101  # Motor 1
PWM_MAIN_FUNC2 = 102  # Motor 2
PWM_MAIN_FUNC3 = 103  # Motor 3
PWM_MAIN_FUNC4 = 104  # Motor 4
PWM_AUX_FUNC1-8 = 0   # Disabled
```

Other relevant bench settings captured in the same snapshot:

```text
SYS_AUTOSTART = 4019
BAT1_N_CELLS = 4
BAT1_CAPACITY = 3300
COM_RC_IN_MODE = 1
CBRK_BUZZER = 782097
```

Treat this as evidence of the configured hardware state, not as a blind restore
recipe. Review changes in QGroundControl before applying a full parameter file
back to the aircraft.
