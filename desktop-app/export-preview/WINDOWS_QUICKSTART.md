# Windows 10 Quickstart

This package lets you preview and navigate the Drone Vision Nav desktop app UI in a browser.

## 1. Install Node.js

Install Node.js LTS:

```text
https://nodejs.org/
```

Restart PowerShell or Command Prompt after installing.

## 2. Start The Preview

Double-click:

```text
start-windows.bat
```

Or run:

```powershell
powershell -ExecutionPolicy Bypass -File .\start-powershell.ps1
```

The app opens at:

```text
http://127.0.0.1:1420/
```

## 3. Optional: Load Demo Data

Open:

```text
http://127.0.0.1:1420/seed-demo-state.html
```

Click:

```text
Load demo state
```

Then navigate the app normally.

## 4. Stop The Preview

Return to the terminal window and press:

```text
Ctrl+C
```

## Notes

This is a UI preview only. It does not connect to real drone hardware, SSH, MAVLink, PX4, or Raspberry Pi services.
