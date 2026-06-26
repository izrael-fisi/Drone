# Windows 11 Codex Handoff

Use this guide when moving active development to a Windows 11 desktop.

## Clone

Open PowerShell:

```powershell
cd $env:USERPROFILE\Documents
git clone https://github.com/izrael-fisi/Drone.git
cd Drone
```

Codex should read `AGENTS.md` first after cloning.

## Required Tools

Install:

- Git for Windows
- Node.js LTS
- Python 3.11 or 3.12
- Rust via `rustup`
- Microsoft Visual Studio Build Tools with the C++ desktop workload

Optional:

- GitHub CLI
- QGroundControl
- Windows Terminal

Useful `winget` commands:

```powershell
winget install --id Git.Git -e
winget install --id OpenJS.NodeJS.LTS -e
winget install --id Python.Python.3.12 -e
winget install --id Rustlang.Rustup -e
winget install --id Microsoft.VisualStudio.2022.BuildTools -e
winget install --id GitHub.cli -e
```

For Visual Studio Build Tools, ensure **Desktop development with C++** is
installed. Tauri native builds need MSVC tooling.

## Python Runtime Setup

From repo root:

```powershell
py -3.12 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e .[geo,mavlink]
python tests/run_unit_tests.py
```

If PowerShell blocks activation:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

## Desktop App Setup

Browser/dev build:

```powershell
cd desktop-app
npm ci
npm run build
npm run dev -- --host 127.0.0.1
```

Open:

```text
http://127.0.0.1:1420/
```

Native Tauri backend checks:

```powershell
cd desktop-app\src-tauri
cargo check
cargo test
```

Native app dev mode, after Rust/MSVC dependencies are ready:

```powershell
cd desktop-app
npm run tauri dev
```

## UI Preview Zip

To regenerate the browser-only Windows preview package from a Unix-like shell:

```bash
./scripts/dev/export_desktop_ui_preview.sh
```

If using PowerShell only, run:

```powershell
cd desktop-app
npm ci
npm run build
```

Then serve `desktop-app\dist` with a simple static server, or use the checked-in
templates under `desktop-app\export-preview` as reference.

The generated export folder and zip live under `desktop-app\export\` and are
ignored by Git.

## Active Development Priorities

1. Keep the desktop app map-first and operator-focused.
2. Keep Raspberry Pi/PX4 hardware workflows separate from browser-only preview
   flows.
3. Continue hardening live position telemetry: GPS primary, vision fallback,
   dead reckoning, degraded/no-position states.
4. Preserve support-bundle evidence and flight review outputs.
5. Avoid adding simulator/ROS scaffolding unless explicitly requested.

## Windows Path Notes

The app and Python tools should tolerate Windows paths. Prefer configuration
fields and environment variables over hard-coded Unix paths in new code.

Common Windows paths:

```text
C:\Users\<name>\Documents\Drone
C:\Users\<name>\DroneVisionNav\maps
C:\Users\<name>\DroneTransfer
```

Common Pi paths remain Linux paths:

```text
/home/user/Drone
/home/user/drone-data/map_bundles/mission_bundle
/home/user/DroneTransfer/outgoing
```

## Do Not Commit

- `desktop-app\node_modules\`
- `desktop-app\dist\`
- `desktop-app\export\`
- `desktop-app\src-tauri\target\`
- downloaded map tiles or mission bundles
- support bundles and flight logs
- secrets, API keys, SSH private keys, Wi-Fi passwords
