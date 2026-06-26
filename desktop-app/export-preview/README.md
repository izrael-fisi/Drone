# Drone Vision Nav UI Preview Export

This folder is the source template for a Windows-friendly browser preview package.

Use this when someone needs to test and navigate the desktop app UI without installing Rust, Tauri, PX4, Python, or Raspberry Pi tooling.

## Build The Export Package

From the repository root:

```bash
./scripts/dev/export_desktop_ui_preview.sh
```

The script builds the Vite app and writes:

```text
desktop-app/export/windows-ui-preview/
```

That output folder is safe to copy to a Windows 10 machine.

## Preview Limitations

This is not a native Tauri build. It is a browser preview of the desktop UI.

Works:
- Navigate all panes.
- Test layout, operator path, map-first dashboard, mission planner, camera/vision settings, system status, and flight review.
- Use browser fallback storage for profile, saved devices, and saved maps.
- Seed demo maps/devices with `seed-demo-state.html`.

Limited:
- Native file dialogs do not work outside Tauri.
- SSH, Pi commands, bundle building, and MAVLink/UDP telemetry require the native app/runtime.
- Download/import map actions that call Tauri backend commands will show the app's browser fallback limitations.
