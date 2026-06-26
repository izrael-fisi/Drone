@echo off
setlocal
cd /d "%~dp0"

where node >nul 2>nul
if errorlevel 1 (
  echo.
  echo [Drone Vision Nav] Node.js was not found.
  echo Install Node.js LTS from https://nodejs.org/ and run this file again.
  echo.
  pause
  exit /b 1
)

if "%PORT%"=="" set PORT=1420
echo.
echo [Drone Vision Nav] Starting UI preview on http://127.0.0.1:%PORT%/
echo.
start "" "http://127.0.0.1:%PORT%/"
node server.mjs
