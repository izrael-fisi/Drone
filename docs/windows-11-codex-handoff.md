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

## RTX Research Environment

Use a separate virtual environment for CUDA teacher training, map production,
and ONNX export. This avoids mixing the desktop-app Python tools with large GPU
packages. Confirm an NVIDIA driver is installed first with `nvidia-smi`, then
run from the repository root:

```powershell
.\scripts\research\setup_windows_cuda_research.ps1
```

The script creates `.venv-research`, installs the project research extras, and
runs the read-only CUDA/map/ONNX readiness gate. It uses the CUDA 12.4 PyTorch
wheel index by default to match the project research container. If the official
PyTorch selector supplies a newer compatible index for the RTX workstation,
pass it explicitly:

```powershell
.\scripts\research\setup_windows_cuda_research.ps1 `
  -TorchIndexUrl https://download.pytorch.org/whl/cu124
```

Do not start a teacher job until `research/environment_report.json` reports
`"status": "ready"`. That check is local/read-only; it never reaches the Pi
or PX4. The initial learned baseline uses only verified temporal windows and
produces research-only checkpoints.

### First End-to-End CUDA Smoke Run

After a verified temporal-window package exists, run the bounded workflow below.
It trains only a small project-owned teacher/student smoke run, evaluates it,
exports a standalone temporal ONNX artifact, and checks ONNX parity. It uses a
fresh output folder to preserve one immutable lineage per run:

```powershell
.\scripts\research\run_first_temporal_smoke.ps1 `
  -TemporalWindowsDir D:\DroneData\temporal-windows\first-site-v1 `
  -OutputRoot D:\DroneData\research-runs\first-site-smoke-v1
```

The teacher and student optimization passes are intentionally bounded to ten
train and validation batches. Teacher-target materialization still covers every
declared temporal window, because a partial target package would fail the
project's immutable coverage check. This establishes that CUDA, dataset
contracts, checkpointing, teacher targets, student distillation, ONNX export,
and numerical parity work together. It is not a field model, a complete
`ModelRelease`, a Pi deployment artifact, or an authorization to publish to PX4.

### Prepare Temporal Windows From Pi Captures

Copy at least two validated Pi capture-session folders with the import tool
before building the training package. It copies only the explicit session paths
you name, then reruns checksum, PX4-attitude, MAVLink `TIMESYNC`, frame-timing,
camera sensor timestamp, and offline-GNSS-label validation on Windows. It is
transfer-only and never contacts PX4 through MAVLink, starts training, or
changes the Pi:

```powershell
.\scripts\research\import_pi_training_captures.ps1 `
  -PiHost 192.168.1.158 `
  -PiUser user `
  -RemoteCaptureSessionDir /home/user/drone-data/captures/route-a,/home/user/drone-data/captures/route-b `
  -DestinationRoot D:\DroneData\captures\first-site-v1
```

After the import manifest reports `validated`, use the same research
environment to build the complete training package. A measured, georeferenced
`ReferenceMapRelease` must already exist; this is the model-independent map
used for first-model labels and map crops.

```powershell
.\scripts\research\prepare_first_temporal_windows.ps1 `
  -CaptureSessionDir D:\DroneData\captures\route-a,D:\DroneData\captures\route-b `
  -ReferenceMap D:\DroneData\reference-maps\first-site\reference_map.json `
  -OutputRoot D:\DroneData\prepared\first-site-v1 `
  -DatasetReleaseId holybro-first-site `
  -DatasetReleaseVersion 0.1.0
```

It fails closed for missing checksums, attitude, camera timestamps, measured
extrinsics, poor image quality, inadequate healthy-GNSS labels, or sessions
that cannot be split cleanly. Its resulting `temporal-windows` folder is the
only input needed by `run_first_temporal_smoke.ps1`.

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
