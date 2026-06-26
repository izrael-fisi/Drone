# Agent Auto Deploy Instructions

Use this file when a coding/browser agent needs to deploy the Drone Vision Nav UI preview on Windows 10.

## Goal

Unzip the package, start the local preview server, optionally seed demo data, and navigate the UI.

## Fast Path

From PowerShell in the unzipped folder:

```powershell
powershell -ExecutionPolicy Bypass -File .\deploy-and-run.ps1
```

Then open:

```text
http://127.0.0.1:1420/
```

Optional demo state:

```text
http://127.0.0.1:1420/seed-demo-state.html
```

Click **Load demo state**.

## If Starting From The Zip File

Use PowerShell:

```powershell
$zip = "DroneVisionNav-Windows-UI-Preview.zip"
$out = "$env:USERPROFILE\Desktop\DroneVisionNav-Windows-UI-Preview"
Expand-Archive -LiteralPath $zip -DestinationPath $out -Force
Set-Location $out
powershell -ExecutionPolicy Bypass -File .\deploy-and-run.ps1
```

## What The Deploy Script Does

1. Checks for Node.js.
2. If Node.js is missing and `winget` is available, tries to install Node.js LTS.
3. Starts the local static preview server.
4. Opens the browser to `http://127.0.0.1:1420/`.

## Expected Limitations

This is a browser UI preview only.

Do not report these as bugs:

- SSH/Pi commands do not run.
- Native Tauri file dialogs do not run.
- MAVLink/UDP hardware telemetry is not available.
- Bundle building/upload is unavailable.
- Some actions may report that the Tauri desktop runtime is required.

Do report:

- Broken navigation.
- Missing pages.
- Text overlap.
- Buttons that are impossible to understand.
- Panes that do not fit a Windows 10 desktop viewport.
- Any route that fails to render.
