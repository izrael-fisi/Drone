# Claude Agent Context: Drone Vision Nav Windows UI Preview

You are testing the **Drone Vision Nav** desktop app UI in a Windows 10 browser preview package.

## What This Project Is

Drone Vision Nav is a ground-control and mission-planning app for a PX4 / Pixhawk / Raspberry Pi drone stack.

The product goal is GNSS-denied navigation using:
- Downward computer vision.
- Onboard IMU / PX4 state.
- Preloaded georeferenced terrain maps.
- GPS as primary position when healthy, with vision/dead-reckoning fallback when GPS is weak, jammed, or unavailable.

The app is being shaped toward a map-first operations console similar to field GCS tools.

## What This Export Is

This package is a **browser UI preview**, not the full native Tauri desktop app.

Use it to:
- Navigate the app.
- Review layout and operator flow.
- Test responsiveness and obvious UI issues.
- Verify panes are understandable to an operator.
- Report broken navigation, text overlap, confusing states, and missing affordances.

Do not expect it to:
- Connect to the Raspberry Pi.
- Run SSH commands.
- Build real mission bundles.
- Receive real MAVLink/UDP telemetry.
- Open native file dialogs.
- Access the filesystem through Tauri.

The browser preview uses fallback localStorage for profile, map, and device data.

## How To Run On Windows 10

Preferred:

```bat
start-windows.bat
```

PowerShell alternative:

```powershell
powershell -ExecutionPolicy Bypass -File .\start-powershell.ps1
```

Manual:

```powershell
node server.mjs
```

Then open:

```text
http://127.0.0.1:1420/
```

If Node.js is missing, install Node.js LTS from:

```text
https://nodejs.org/
```

## Optional Demo Data

Open this page first:

```text
http://127.0.0.1:1420/seed-demo-state.html
```

Click **Load demo state**.

This adds:
- One demo Raspberry Pi 5 device.
- One local planning profile.
- One active San Francisco demo map.
- Camera calibration state set to captured.

After seeding, go to **Drone** and select the demo device if needed.

## Main Routes To Inspect

Use these direct URLs if navigation gets confusing:

```text
/dashboard
/maps
/mission-planner
/mission-bundle-builder
/terrain
/devices
/pi-setup
/camera-vision
/system-status
/flight-review
/settings
```

## Current Operator Path

The intended simple path is:

1. **Start / Dashboard**: map-first operations overview.
2. **Map**: create, download, or import a map.
3. **Mission**: plan takeoff, waypoints, landing, fence, rally, and vision checkpoints.
4. **Drone**: select/configure the runtime module.
5. **Fly**: monitor position source, GPS health, vision cadence, MAVLink status, and diagnostics.
6. **Review**: inspect support bundles and GPS-vs-vision replay evidence.

## What To Evaluate

Focus on these issues:

- Is the map visually central to the operator workflow?
- Can a new operator understand what to do next?
- Are disabled or blocked states explained clearly?
- Are there dead ends?
- Does the sidebar route hierarchy make sense?
- Does text overlap or get clipped at Windows desktop sizes?
- Do buttons look clickable and consistent?
- Are technical terms too dense for a mission operator?
- Does Mission Planner remain usable without a connected device?
- Does Camera/Vision clearly separate calibration readiness from algorithm tuning?
- Does Flight Review communicate GPS-vs-vision track replay even with no evidence loaded?

## Known Preview Limitations

These are expected in the browser preview:

- Import/download/build/upload actions may show fallback/runtime errors.
- Tauri filesystem calls do not run.
- SSH/Pi actions do not run.
- Real telemetry is unavailable unless the native app/runtime sends compatible packets.
- Map tile imagery depends on internet access to the ESRI tile server.
- React Router future-flag warnings in the browser console are known and low priority.

## Useful Browser Checks

At minimum, inspect:

```text
http://127.0.0.1:1420/dashboard
http://127.0.0.1:1420/mission-planner
http://127.0.0.1:1420/camera-vision
http://127.0.0.1:1420/system-status
http://127.0.0.1:1420/flight-review
```

Report:
- Exact route.
- Screenshot if possible.
- Browser size.
- What action caused the issue.
- What the operator expected.
- What happened instead.
